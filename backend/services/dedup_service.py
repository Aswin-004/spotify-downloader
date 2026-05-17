"""
Dedup Service — strong duplicate identity for the ingest pipeline.

Problem with the old approach
------------------------------
auto_downloader used normalize(sanitize_filename(title)) as the dedup key.
This breaks in three ways:
  1. Two different songs with similar titles collide (false positive).
  2. The same song with a slightly different title passes through twice.
  3. Collision-suffix renames (_1, _2) produce distinct keys for the same file.

Two-tier identity
-----------------
Primary   — Spotify track ID (globally unique, survives any rename).
Secondary — sha256(normalized_title + "||" + normalized_artist + "||"
                   + duration_bucket) where duration_bucket = round(ms/5000).
            The 5-second bucket tolerates minor duration discrepancies between
            sources without generating false matches.

Callers
-------
- auto_downloader._download_single: replaces track_key = normalize(sanitize_filename(title))
- database.library_index: stored alongside the file record for O(1) lookups
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path


# Characters that sanitize_filename strips, kept in sync with downloader_service.
_UNSAFE = r'[<>:"/\\|?*\x00-\x1f]'


def _normalize_text(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, drop unsafe chars."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(_UNSAFE, "", text)
    return " ".join(text.lower().split()).strip()


def _duration_bucket(duration_ms: int | None) -> int:
    """Floor-divide duration into 5-second buckets (tolerates up to 4.999s mismatch).

    Floor division gives clean boundaries at multiples of 5 000 ms:
      180 000 → bucket 36, 183 000 → bucket 36 (same), 185 000 → bucket 37 (different).
    """
    if not duration_ms or duration_ms <= 0:
        return 0
    return duration_ms // 5000


def content_hash(title: str, artist: str, duration_ms: int | None = None) -> str:
    """
    Stable content-based hash for a track.
    sha256(normalized_title || normalized_artist || duration_bucket)[:16]
    """
    raw = (
        _normalize_text(title)
        + "||"
        + _normalize_text(artist)
        + "||"
        + str(_duration_bucket(duration_ms))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def duplicate_identity_key(
    spotify_id: str,
    title: str,
    artist: str,
    duration_ms: int | None = None,
) -> str:
    """
    Return the canonical dedup key for a track.

    If spotify_id is present and non-empty, use it directly — it is globally
    unique and survives any rename.  Otherwise fall back to the content hash
    so tracks without a Spotify ID are still deduplicated correctly.

    This key is stored in:
      - _downloaded_registry (in-memory, process lifetime)
      - _in_progress_registry (in-memory, per-cycle)
      - library_index MongoDB collection (persistent)
    """
    sid = (spotify_id or "").strip()
    if sid:
        return f"sp:{sid}"
    return f"ch:{content_hash(title, artist, duration_ms)}"


# ── Phase 7: Canonical scoring + duplicate policy ─────────────────────────────

def canonical_score(
    filepath: str,
    spotify_id: str = "",
    has_bpm: bool = False,
    has_key: bool = False,
    has_artwork: bool = False,
    has_camelot: bool = False,
    bitrate_kbps: int = 0,
) -> float:
    """
    Score a file's suitability as the canonical (keeper) copy when duplicates
    are detected.  Higher is better.

    Scoring weights:
      +10  spotify_id present (globally unique identity)
      +8   BPM + key both present
      +4   BPM or key alone
      +6   embedded artwork (APIC frame)
      +4   Camelot key tag
      +3   bitrate (scaled: 320 kbps → +3, 192 → +1.8)
      -5   temp file (*.trimmed.mp3, *_1.mp3, etc.)
      -3   in Ingest/ folder
      -1   in NeedsReview/ folder

    Args:
        filepath:    Absolute or relative path to the MP3.
        spotify_id:  Known Spotify track ID (empty → 0 pts).
        has_bpm:     True if TBPM tag is present.
        has_key:     True if TKEY tag is present.
        has_artwork: True if APIC tag is embedded.
        has_camelot: True if TXXX:INITIALKEY is present.
        bitrate_kbps: Audio bitrate in kbps (0 if unknown).

    Returns:
        Float score — compare two copies: higher score wins.
    """
    import re as _re
    score = 0.0

    if spotify_id and spotify_id.strip():
        score += 10.0

    if has_bpm and has_key:
        score += 8.0
    elif has_bpm or has_key:
        score += 4.0

    if has_artwork:
        score += 6.0

    if has_camelot:
        score += 4.0

    if bitrate_kbps > 0:
        score += 3.0 * min(1.0, bitrate_kbps / 320.0)

    # Location penalties
    p = str(filepath).replace("\\", "/")
    _TEMP_RE = _re.compile(r"(\.trimmed\.mp3|_\d{1,2}\.mp3|_temp[^.]*\.mp3|_part\d*\.mp3)$",
                           _re.IGNORECASE)
    if _TEMP_RE.search(p):
        score -= 5.0
    if "/Ingest/" in p or "\\Ingest\\" in p:
        score -= 3.0
    if "/NeedsReview/" in p or "\\NeedsReview\\" in p:
        score -= 1.0

    return score


def score_from_tags(filepath: str) -> float:
    """
    Convenience wrapper: read ID3 tags from *filepath* and return
    ``canonical_score`` populated from those tags.

    Falls back to 0.0 if the file is unreadable.
    """
    try:
        from mutagen.mp3 import MP3
        audio = MP3(filepath)
        tags  = audio.tags or {}

        spotify_id   = ""
        txxx_sid     = tags.get("TXXX:SPOTIFY_ID")
        if txxx_sid and txxx_sid.text:
            spotify_id = str(txxx_sid.text[0]).strip()

        camelot = tags.get("TXXX:INITIALKEY") or tags.get("TXXX:CAMELOT")

        return canonical_score(
            filepath     = filepath,
            spotify_id   = spotify_id,
            has_bpm      = bool(tags.get("TBPM")),
            has_key      = bool(tags.get("TKEY")),
            has_artwork  = bool(tags.get("APIC:")),
            has_camelot  = bool(camelot),
            bitrate_kbps = int(audio.info.bitrate / 1000),
        )
    except Exception:
        return 0.0


def is_exact_duplicate(
    filepath: str,
    identity_key: str,
    library_index_col,
) -> bool:
    """
    Return True if *filepath* is an exact duplicate of an already-indexed track.

    An exact duplicate shares:
      • The same identity_key (sp:{spotify_id} or ch:{content_hash})
      • A different final_path (i.e. it is not the same file in the index)

    Exact duplicates should be quarantined, not kept.
    Mix variants (same title, different audio content) are NOT exact duplicates
    — they have different fingerprints and different identity_keys.
    """
    if library_index_col is None:
        return False
    try:
        existing = library_index_col.find_one(
            {"identity_key": identity_key},
            {"final_path": 1, "_id": 0},
        )
        if existing is None:
            return False
        existing_path = existing.get("final_path", "")
        return (
            bool(existing_path)
            and str(Path(existing_path).resolve()) != str(Path(filepath).resolve())
        )
    except Exception:
        return False


def legacy_title_key(title: str) -> str:
    """
    Reproduce the old normalize(sanitize_filename(title)) key for
    backward-compatibility when reading the existing _downloaded_registry
    that was populated before this patch.
    """
    text = re.sub(_UNSAFE, "", title)
    text = " ".join(text.lower().split()).strip()
    return text
