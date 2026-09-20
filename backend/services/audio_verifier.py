"""
audio_verifier.py
=================
Verify that a downloaded file really IS the requested recording — by
fingerprinting the AUDIO (AcoustID / Chromaprint) instead of trusting the
YouTube title.

Why this exists
---------------
Title / artist / duration scoring (strict_matcher.py) cannot tell a studio
recording from a karaoke or instrumental RE-RECORDING of the same song: the
uploader copies the artist and title verbatim, and the re-recording has the same
arrangement and length. strict_matcher's own comments call this out as a known
residual risk "not preventable without per-candidate audio analysis
(fingerprinting)". This module is that analysis.

How it decides
--------------
The fingerprint is looked up on AcoustID, which returns the MusicBrainz
recording(s) that audio corresponds to (title + artists). Then:

  verified          a confident match names the requested song by the requested
                    artist, and the recording isn't a karaoke/remix/live variant.
  suspect_version   the audio IS a known recording of this song, but a karaoke /
                    instrumental / cover / remix / live variant of it.
  mismatch          the audio is confidently a DIFFERENT song by a different artist.
  inconclusive      anything else — no match, low score, lookup failure, a
                    non-Latin-script match we can't compare, missing key/fpcalc.

Safety design (why a wrong verdict is unlikely to destroy a good file)
----------------------------------------------------------------------
* Only POSITIVE evidence ever rejects a file. "No AcoustID match" is
  inconclusive, never bad — plenty of legitimate tracks aren't in AcoustID.
* Every rejecting verdict needs a match score >= STRONG_MATCH_SCORE (0.90).
* "verified" also needs >= 0.90, so a partial match against an official
  instrumental of the same master can't masquerade as the original.
* "mismatch" is never issued when the matched title/artist is written in a
  non-Latin script (Devanagari, Tamil, ...): a correct match against a
  Devanagari MusicBrainz entry would otherwise look like a totally different song.
* AUDIO_VERIFY_MODE = off | flag | enforce (default enforce). In `flag` mode the
  verdict is only recorded/logged; nothing is rejected.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_FLAG = "flag"
MODE_ENFORCE = "enforce"

# AcoustID scores below this are ignored entirely.
MIN_MATCH_SCORE = 0.80
# Required before a verdict that can REJECT a file, and before "verified".
STRONG_MATCH_SCORE = 0.90
# Similarity needed between the requested and the matched recording.
VERIFIED_TITLE_SIM = 0.75
VERIFIED_ARTIST_SIM = 0.60
SAME_SONG_TITLE_SIM = 0.60      # "this is the same song, just a different version"
DIFFERENT_SONG_SIM = 0.40       # both title AND artist below this => different song
# Fraction of letters that must be Latin for a string to be safely comparable.
LATIN_COMPARABLE_RATIO = 0.60

_ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
_USER_AGENT = "spotify-meta-downloader/1.0"
_MIN_REQUEST_GAP_SEC = 0.4      # AcoustID allows ~3 requests/second
_req_lock = threading.Lock()
_last_request = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Result type
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class VerifyResult:
    status: str                                   # verified | suspect_version | mismatch | inconclusive
    confidence: float = 0.0                       # AcoustID score of the deciding match
    matched_title: str = ""
    matched_artists: List[str] = field(default_factory=list)
    reason: str = ""

    @property
    def is_bad(self) -> bool:
        """True for the two verdicts that prove the audio is NOT the requested recording."""
        return self.status in ("suspect_version", "mismatch")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "confidence": round(float(self.confidence), 3),
            "matched_title": self.matched_title,
            "matched_artists": list(self.matched_artists),
            "reason": self.reason,
        }


def get_mode() -> str:
    """AUDIO_VERIFY_MODE from the environment; unknown values fall back to enforce."""
    mode = (os.getenv("AUDIO_VERIFY_MODE") or MODE_ENFORCE).strip().lower()
    return mode if mode in (MODE_OFF, MODE_FLAG, MODE_ENFORCE) else MODE_ENFORCE


def should_reject(result: VerifyResult, mode: Optional[str] = None) -> bool:
    """A file is rejected only in enforce mode and only on a proven-bad verdict."""
    return (mode or get_mode()) == MODE_ENFORCE and result.is_bad


# ═══════════════════════════════════════════════════════════════════════════
# Text helpers
# ═══════════════════════════════════════════════════════════════════════════

_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|;|/|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\band\b|\bx\b|\bwith\b)\s*", re.IGNORECASE)


def _split_artists(artist: str) -> List[str]:
    parts = [p.strip().lower() for p in _ARTIST_SPLIT_RE.split(artist or "") if p and p.strip()]
    return parts or ([artist.strip().lower()] if artist and artist.strip() else [])


def _is_comparable(text: str) -> bool:
    """True when `text` is mostly Latin letters, so a fuzzy comparison is meaningful."""
    normalized = unicodedata.normalize("NFKD", text or "")
    letters = [c for c in normalized if c.isalpha()]
    if not letters:
        return True                      # digits/punctuation only ("10xx", "35")
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) >= LATIN_COMPARABLE_RATIO


def _fuzzy(a: str, b: str) -> float:
    from services.strict_matcher import _fuzzy_ratio
    return _fuzzy_ratio(a, b)


def _title_sim(expected_title: str, recording_title: str) -> float:
    from services.strict_matcher import clean_title
    return _fuzzy(clean_title(expected_title), clean_title(recording_title))


def _artist_sim(expected_artists: List[str], recording_artists: List[str]) -> float:
    best = 0.0
    for exp in expected_artists:
        for rec in recording_artists:
            best = max(best, _fuzzy(exp, rec.lower()))
    return best


def _variant_flag(recording_title: str, recording_artists: Iterable[str], expected_title: str) -> Optional[str]:
    """Why a matched recording is a karaoke/instrumental/cover/remix/live VARIANT, or None."""
    from services.strict_matcher import (
        REJECT_CHANNEL_KEYWORDS, has_reject_keyword, version_mismatch,
    )
    kw = has_reject_keyword(recording_title, exempt_from=expected_title)
    if kw:
        return f"recording title contains '{kw}'"
    for name in recording_artists:
        kw = has_reject_keyword(name, exempt_from=expected_title, keywords=REJECT_CHANNEL_KEYWORDS)
        if kw:
            return f"recording artist '{name}' looks like a karaoke/cover act ('{kw}')"
    extra = version_mismatch(recording_title, expected_title)
    if extra:
        return f"recording is a '{', '.join(sorted(extra))}' version"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Decision logic (pure — no I/O, fully unit-testable)
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_lookup(results: list, expected_title: str, expected_artist: str) -> VerifyResult:
    """Turn an AcoustID `results` list into a verdict about the requested recording."""
    if not results:
        return VerifyResult("inconclusive", 0.0, reason="no AcoustID match")

    scored = sorted(results, key=lambda r: float(r.get("score", 0) or 0), reverse=True)
    best_score = float(scored[0].get("score", 0) or 0)
    confident = [r for r in scored if float(r.get("score", 0) or 0) >= MIN_MATCH_SCORE]
    if not confident:
        return VerifyResult("inconclusive", best_score,
                            reason=f"best AcoustID score {best_score:.2f} < {MIN_MATCH_SCORE}")

    expected_artists = _split_artists(expected_artist)

    # (score, recording title, recording artists, title_sim, artist_sim)
    rows = []
    for res in confident:
        score = float(res.get("score", 0) or 0)
        for rec in res.get("recordings") or []:
            r_title = rec.get("title") or ""
            r_artists = [a.get("name", "") for a in rec.get("artists") or [] if a.get("name")]
            if not r_title:
                continue
            rows.append((score, r_title, r_artists,
                         _title_sim(expected_title, r_title),
                         _artist_sim(expected_artists, r_artists)))

    if not rows:
        return VerifyResult("inconclusive", best_score, reason="AcoustID match has no linked recording")

    # 1) VERIFIED — any clean recording that is the requested song by the requested artist.
    for score, r_title, r_artists, t_sim, a_sim in rows:
        if (score >= STRONG_MATCH_SCORE and t_sim >= VERIFIED_TITLE_SIM and a_sim >= VERIFIED_ARTIST_SIM
                and _variant_flag(r_title, r_artists, expected_title) is None):
            return VerifyResult("verified", score, r_title, r_artists,
                                "AcoustID fingerprint matches the requested recording")

    # 2) SUSPECT VERSION — the audio is a known recording of THIS song, but a variant of it.
    for score, r_title, r_artists, t_sim, a_sim in rows:
        if score < STRONG_MATCH_SCORE or t_sim < SAME_SONG_TITLE_SIM:
            continue
        why = _variant_flag(r_title, r_artists, expected_title)
        if why:
            return VerifyResult("suspect_version", score, r_title, r_artists,
                                f"fingerprint identifies this audio as '{r_title}' — {why}")

    # 3) MISMATCH — confidently a different song by a different artist. Never when the
    #    matched text is in a script we can't compare (transliteration would look "different").
    strong = [row for row in rows if row[0] >= STRONG_MATCH_SCORE]
    if strong and all(
        t_sim < DIFFERENT_SONG_SIM and a_sim < DIFFERENT_SONG_SIM
        and _is_comparable(r_title) and all(_is_comparable(a) for a in r_artists)
        for _score, r_title, r_artists, t_sim, a_sim in strong
    ):
        score, r_title, r_artists, _t, _a = strong[0]
        return VerifyResult("mismatch", score, r_title, r_artists,
                            f"fingerprint identifies this audio as a different song: "
                            f"'{r_title}' by {', '.join(r_artists) or 'unknown'}")

    return VerifyResult("inconclusive", best_score, rows[0][1], rows[0][2],
                        "matched recording is only partially similar to the request")


# ═══════════════════════════════════════════════════════════════════════════
# I/O
# ═══════════════════════════════════════════════════════════════════════════

def _acoustid_lookup_recordings(fingerprint: str, duration: float, api_key: str) -> list:
    """POST a fingerprint to AcoustID and return its `results` list. Raises on failure."""
    global _last_request
    with _req_lock:
        gap = time.time() - _last_request
        if gap < _MIN_REQUEST_GAP_SEC:
            time.sleep(_MIN_REQUEST_GAP_SEC - gap)
        _last_request = time.time()
    body = urllib.parse.urlencode({
        "client": api_key,
        "duration": int(duration),
        "fingerprint": fingerprint,
        "meta": "recordings",
    }).encode()
    req = urllib.request.Request(_ACOUSTID_LOOKUP_URL, data=body,
                                 headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode())
    if data.get("status") != "ok":
        raise RuntimeError(f"AcoustID error: {data.get('error', data)}")
    return data.get("results") or []


def verify_recording(
    filepath: str,
    expected_title: str,
    expected_artist: str,
    expected_duration_sec: Optional[float] = None,
) -> VerifyResult:
    """
    Fingerprint `filepath` and decide whether it is the requested recording.
    Never raises; every failure path returns an `inconclusive` result.
    """
    if get_mode() == MODE_OFF:
        return VerifyResult("inconclusive", reason="verification disabled (AUDIO_VERIFY_MODE=off)")
    try:
        from services.musicbrainz_service import _acoustid_key
        api_key = _acoustid_key()
    except Exception:
        api_key = os.getenv("ACOUSTID_API_KEY", "")
    if not api_key:
        return VerifyResult("inconclusive", reason="ACOUSTID_API_KEY not set")
    if not filepath or not os.path.isfile(filepath):
        return VerifyResult("inconclusive", reason="file not found")

    try:
        from services.fingerprint_service import _run_fpcalc
        fingerprint, duration = _run_fpcalc(filepath)
    except Exception as exc:
        return VerifyResult("inconclusive", reason=f"fingerprinting unavailable: {exc}")
    if not fingerprint:
        return VerifyResult("inconclusive", reason="fpcalc could not fingerprint the file")

    try:
        results = _acoustid_lookup_recordings(fingerprint, duration, api_key)
    except Exception as exc:
        logger.debug(f"[audio_verifier] lookup failed for {os.path.basename(filepath)}: {exc}")
        return VerifyResult("inconclusive", reason=f"AcoustID lookup failed: {exc}")

    result = evaluate_lookup(results, expected_title, expected_artist)
    logger.info(
        f"[audio_verifier] {os.path.basename(filepath)} vs '{expected_title}' / '{expected_artist}' "
        f"-> {result.status} ({result.confidence:.2f}) {result.reason}"
    )
    return result
