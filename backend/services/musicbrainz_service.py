"""
MusicBrainz genre service — two lookup modes, both free with no quota.

Mode 1 — Title+artist search:
  MusicBrainz recording search → top result → release-group tags → genre

Mode 2 — AcoustID fingerprint:
  fpcalc fingerprint → AcoustID API → MusicBrainz recording ID → tags → genre
  Requires ACOUSTID_API_KEY env var (free at acoustid.org/login).

Rate limit: 1 req/s (enforced internally via User-Agent + sleep).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_MB_BASE   = "https://musicbrainz.org/ws/2"
_ACOUSTID_BASE = "https://api.acoustid.org/v2"
_ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY", "")
_UA = "spotify-meta-downloader/1.0 (aswin.abhinab22@gmail.com)"
_LAST_REQ  = 0.0   # rate-limit tracker


# Tag → genre folder mapping (same pattern as lastfm_service)
_TAG_MAP: list[tuple[str, str]] = [
    ("bollywood",          "Bollywood"),
    ("hindi",              "Bollywood"),
    ("filmi",              "Bollywood"),
    ("indian",             "Bollywood"),
    ("punjabi",            "Punjabi"),
    ("bhangra",            "Punjabi"),
    ("tamil",              "Tamil"),
    ("psytrance",          "Trance"),
    ("progressive trance", "Trance"),
    ("trance",             "Trance"),
    ("drum and bass",      "Drum & Bass"),
    ("dnb",                "Drum & Bass"),
    ("jungle",             "Drum & Bass"),
    ("uk garage",          "UK Garage"),
    ("speed garage",       "UK Garage"),
    ("2step",              "UK Garage"),
    ("grime",              "Grime"),
    ("uk drill",           "Grime"),
    ("dubstep",            "Dubstep"),
    ("techno",             "Techno"),
    ("deep house",         "House"),
    ("afro house",         "House"),
    ("tech house",         "House"),
    ("house",              "House"),
    ("electronic",         "Electronic"),
    ("hip-hop",            "Hip Hop"),
    ("hip hop",            "Hip Hop"),
    ("rap",                "Hip Hop"),
    ("r&b",                "R&B"),
    ("soul",               "R&B"),
    ("pop",                "Pop"),
    ("latin",              "Latin"),
    ("reggaeton",          "Latin"),
]


def _get(url: str) -> dict:
    global _LAST_REQ
    elapsed = time.time() - _LAST_REQ
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    _LAST_REQ = time.time()
    return data


def _tags_to_genre(tags: list[dict]) -> str:
    """Map a list of MB tag dicts (with 'name' and 'count') to a genre folder."""
    names = [t["name"].lower() for t in sorted(tags, key=lambda x: -x.get("count", 0))]
    for name in names:
        for pattern, genre in _TAG_MAP:
            if pattern in name:
                return genre
    return ""


def lookup_by_search(title: str, artist: str = "") -> str:
    """
    Search MusicBrainz for a recording by title (+ optional artist) and return
    a genre folder name derived from release-group tags.
    Returns "" on no match or network failure.
    """
    if not title:
        return ""
    try:
        query = f'recording:"{urllib.parse.quote(title)}"'
        if artist and artist.lower() not in ("unknown", "electronic", ""):
            query += f' AND artist:"{urllib.parse.quote(artist)}"'
        url = f"{_MB_BASE}/recording?query={query}&limit=3&fmt=json"
        data = _get(url)
        recordings = data.get("recordings", [])
        if not recordings:
            return ""

        # Collect tags from the top recording and its releases
        all_tags: list[dict] = []
        rec = recordings[0]
        all_tags.extend(rec.get("tags", []))

        # Fetch release-group tags for richer genre data
        rg_id = ""
        for rel in rec.get("releases", [])[:1]:
            rg = rel.get("release-group", {})
            rg_id = rg.get("id", "")
        if rg_id:
            try:
                rg_data = _get(f"{_MB_BASE}/release-group/{rg_id}?inc=tags&fmt=json")
                all_tags.extend(rg_data.get("tags", []))
            except Exception:
                pass

        genre = _tags_to_genre(all_tags)
        if genre:
            logger.debug(f"[musicbrainz] '{title}' → {genre}")
        return genre
    except Exception as exc:
        logger.debug(f"[musicbrainz] search failed for {title!r}: {exc}")
        return ""


def lookup_by_fingerprint(filepath: str) -> str:
    """
    Fingerprint the audio with fpcalc, submit to AcoustID, get MusicBrainz
    recording tags, and return a genre folder name.
    Requires ACOUSTID_API_KEY env var.
    Returns "" if key missing, fpcalc unavailable, or no match.
    """
    if not _ACOUSTID_API_KEY:
        return ""
    try:
        from services.fingerprint_service import _run_fpcalc
        fp_str, duration = _run_fpcalc(filepath)
        if not fp_str:
            return ""

        params = urllib.parse.urlencode({
            "client":      _ACOUSTID_API_KEY,
            "duration":    int(duration),
            "fingerprint": fp_str,
            "meta":        "recordings+recordingids+releases+releasegroups+tracks+compress",
        })
        data = _get(f"{_ACOUSTID_BASE}/lookup?{params}")

        results = data.get("results", [])
        if not results:
            return ""

        # Pick highest-score result
        best = max(results, key=lambda r: r.get("score", 0))
        if best.get("score", 0) < 0.85:
            return ""

        # Collect MusicBrainz recording IDs
        mb_ids = [r.get("id") for r in best.get("recordings", []) if r.get("id")]
        if not mb_ids:
            return ""

        # Fetch tags for the first recording
        rec_data = _get(f"{_MB_BASE}/recording/{mb_ids[0]}?inc=tags+releases+release-groups&fmt=json")
        all_tags = rec_data.get("tags", [])

        # Also get release-group tags
        for rel in rec_data.get("releases", [])[:1]:
            rg_id = rel.get("release-group", {}).get("id", "")
            if rg_id:
                try:
                    rg_data = _get(f"{_MB_BASE}/release-group/{rg_id}?inc=tags&fmt=json")
                    all_tags.extend(rg_data.get("tags", []))
                except Exception:
                    pass

        genre = _tags_to_genre(all_tags)
        if genre:
            logger.debug(f"[musicbrainz] fingerprint match → {genre} (score={best['score']:.2f})")
        return genre
    except Exception as exc:
        logger.debug(f"[musicbrainz] fingerprint lookup failed: {exc}")
        return ""


def is_available() -> bool:
    """True — MusicBrainz search works without any API key."""
    return True
