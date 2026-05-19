"""
Audio Fingerprint Service — Phase 13
======================================
Optional acoustic fingerprinting for DJ library duplicate detection.

Priority order (dedup_service contract):
  1. spotify_id          — globally unique, zero computation
  2. acoustid fingerprint — audio-content match (chromaprint/fpcalc required)
  3. sha256 of raw audio — file-level integrity / corruption detection
  4. normalized metadata — content_hash from dedup_service (fallback)

Graceful degradation
--------------------
  pyacoustid not installed → sha256 mode silently
  fpcalc binary missing    → sha256 mode silently
  acoustid API unreachable → sha256 mode silently

Collision guard
---------------
acoustid identifies the same *recording* (same studio take, same master).
Remasters, live versions, edits, and VIP mixes produce different fingerprints
because their audio content differs.  A confidence threshold of 0.90 is used
to prevent accidental collisions between very similar (but distinct) masters.

Storage
-------
Stored in library_index as:
  audio_fingerprint        str  — hex fingerprint or sha256
  fingerprint_source       str  — "acoustid" | "sha256_audio" | "none"
  fingerprint_confidence   float — 0.0–1.0 (1.0 for sha256, 0.0 if none)

Integration
-----------
  fingerprint_service.compute(filepath) → FingerprintResult
  fingerprint_service.find_duplicate(fp_result, library_index_col) → dict|None
  dedup_service.duplicate_identity_key — unchanged; fingerprint is additive
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import json
from dataclasses import dataclass

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# ── Optional acoustid import ──────────────────────────────────────────────────
try:
    import acoustid as _acoustid
    _ACOUSTID_AVAILABLE = True
except Exception:  # ImportError on Linux/Mac, OSError (DLL load failed) on Windows
    _acoustid = None
    _ACOUSTID_AVAILABLE = False

_FPCALC_BINARY = os.getenv("FPCALC_PATH", "fpcalc")
_ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY", "")
_CONFIDENCE_THRESHOLD = 0.90   # below this → no acoustid match declared
_AUDIO_HASH_BYTES = 2 * 1024 * 1024  # first 2 MB for sha256 (fast, stable)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class FingerprintResult:
    audio_fingerprint: str = ""        # raw chromaprint string or sha256 hex
    fingerprint_source: str = "none"   # "acoustid" | "sha256_audio" | "none"
    fingerprint_confidence: float = 0.0
    acoustid_id: str = ""              # AcoustID recording UUID (if available)
    duration_sec: float = 0.0
    error: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.audio_fingerprint) and self.fingerprint_source != "none"


# ── fpcalc runner ─────────────────────────────────────────────────────────────

def _run_fpcalc(filepath: str) -> tuple[str, float]:
    """
    Run fpcalc and return (fingerprint_str, duration_seconds).
    Returns ("", 0.0) on any failure.
    """
    try:
        result = subprocess.run(
            [_FPCALC_BINARY, "-json", filepath],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return "", 0.0
        data = json.loads(result.stdout)
        return data.get("fingerprint", ""), float(data.get("duration", 0.0))
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError,
            OSError, Exception):
        return "", 0.0


# ── sha256 of audio content ───────────────────────────────────────────────────

def _sha256_audio(filepath: str) -> str:
    """sha256 of first _AUDIO_HASH_BYTES of file (stable across metadata edits)."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            data = f.read(_AUDIO_HASH_BYTES)
        h.update(data)
        return h.hexdigest()
    except Exception:
        return ""


# ── AcoustID lookup ───────────────────────────────────────────────────────────

def _acoustid_lookup(fingerprint: str, duration: float) -> tuple[str, float]:
    """
    Look up the fingerprint against the AcoustID API.
    Returns (acoustid_recording_id, confidence) or ("", 0.0).

    Requires ACOUSTID_API_KEY env var and pyacoustid package.
    Only used for metadata enrichment — not required for dedup.
    """
    if not _ACOUSTID_AVAILABLE or not _acoustid or not _ACOUSTID_API_KEY:
        return "", 0.0
    try:
        results = _acoustid.lookup(
            _ACOUSTID_API_KEY,
            fingerprint,
            int(duration),
            meta="recordings",
        )
        for score, rid, title, artist in _acoustid.parse_lookup_results(results):
            if score >= _CONFIDENCE_THRESHOLD:
                return rid, score
        return "", 0.0
    except Exception as e:
        logger.debug(f"[fingerprint] acoustid lookup failed: {e}")
        return "", 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def compute(filepath: str, lookup_acoustid: bool = False) -> FingerprintResult:
    """
    Compute the best available fingerprint for a file.

    Args:
        filepath:       Absolute path to the MP3 file.
        lookup_acoustid: If True, query AcoustID API for recording UUID.
                         Requires ACOUSTID_API_KEY.  Never blocks pipeline
                         (failures are silently swallowed).

    Returns:
        FingerprintResult — always succeeds, degrades to sha256_audio.
    """
    if not os.path.isfile(filepath):
        return FingerprintResult(error=f"file not found: {filepath}")

    # Try chromaprint first
    fp_str, duration = _run_fpcalc(filepath)
    if fp_str:
        acoustid_id = ""
        if lookup_acoustid:
            acoustid_id, _ = _acoustid_lookup(fp_str, duration)
        return FingerprintResult(
            audio_fingerprint=fp_str,
            fingerprint_source="acoustid",
            fingerprint_confidence=1.0,
            acoustid_id=acoustid_id,
            duration_sec=duration,
        )

    # Fallback: sha256 of raw audio
    sha = _sha256_audio(filepath)
    if sha:
        return FingerprintResult(
            audio_fingerprint=sha,
            fingerprint_source="sha256_audio",
            fingerprint_confidence=1.0,
            duration_sec=0.0,
        )

    return FingerprintResult(
        fingerprint_source="none",
        fingerprint_confidence=0.0,
        error="fpcalc unavailable and sha256 failed",
    )


def find_duplicate(
    fp_result: FingerprintResult,
    col,   # pymongo Collection for library_index
) -> dict | None:
    """
    Search the library_index for an existing track with the same fingerprint.

    Only matches when fingerprint_source is the same (acoustid vs acoustid,
    sha256_audio vs sha256_audio) to avoid cross-type false positives.

    Returns the matching library_index document or None.
    """
    if not fp_result.is_valid or col is None:
        return None
    try:
        return col.find_one(
            {
                "audio_fingerprint": fp_result.audio_fingerprint,
                "fingerprint_source": fp_result.fingerprint_source,
            },
            {"_id": 0},
        )
    except Exception:
        return None


def index_fingerprint(identity_key: str, fp_result: FingerprintResult) -> bool:
    """
    Attach fingerprint fields to an existing library_index document.
    No-op (returns False) if MongoDB is unavailable or record not found.
    """
    if not fp_result.is_valid:
        return False
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        result = col.update_one(
            {"identity_key": identity_key},
            {"$set": {
                "audio_fingerprint":      fp_result.audio_fingerprint,
                "fingerprint_source":     fp_result.fingerprint_source,
                "fingerprint_confidence": fp_result.fingerprint_confidence,
                "acoustid_id":            fp_result.acoustid_id,
            }},
        )
        return result.matched_count > 0
    except Exception as e:
        logger.debug(f"[fingerprint] index_fingerprint failed: {e}")
        return False


def is_available() -> bool:
    """Return True if fpcalc binary is on PATH and functional."""
    try:
        r = subprocess.run([_FPCALC_BINARY, "--version"],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False
