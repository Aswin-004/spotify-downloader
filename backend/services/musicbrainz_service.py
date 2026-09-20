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
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_MB_BASE   = "https://musicbrainz.org/ws/2"
_ACOUSTID_BASE = "https://api.acoustid.org/v2"
_ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY", "")   # import-time snapshot; prefer _acoustid_key()


def _acoustid_key() -> str:
    """Resolve the AcoustID key at CALL time (see lastfm_service._api_key for why:
    an import-time os.getenv() is "" if .env has not been loaded yet, which makes
    the fingerprint step silently return no result)."""
    key = os.getenv("ACOUSTID_API_KEY", "")
    if key:
        return key
    try:
        from config import config
        return getattr(config, "ACOUSTID_API_KEY", "") or ""
    except Exception:
        return ""
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
    """GET + parse JSON, at most one request per 1.1 s. MusicBrainz answers 503 (and sometimes
    429) intermittently when busy or rate-limiting; a single backed-off retry succeeds in
    practice (seen live: first try 503, second try 200), so one is made before giving up."""
    global _LAST_REQ
    for attempt in (1, 2):
        elapsed = time.time() - _LAST_REQ
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            _LAST_REQ = time.time()
            return data
        except urllib.error.HTTPError as exc:
            _LAST_REQ = time.time()
            if exc.code in (429, 503) and attempt == 1:
                time.sleep(2.0)
                continue
            raise


def _tags_to_genre(tags: list[dict]) -> str:
    """Map a list of MB tag dicts (with 'name' and 'count') to a genre folder."""
    names = [t["name"].lower() for t in sorted(tags, key=lambda x: -x.get("count", 0))]
    for name in names:
        for pattern, genre in _TAG_MAP:
            if pattern in name:
                return genre
    return ""


_PLACEHOLDER_ARTISTS = {"", "unknown", "electronic", "various artists", "various", "va"}

# A search hit only counts as "this track" when MusicBrainz is confident AND both the title
# and the artist credit resemble what we asked about (see search_recording_tags).
MB_MIN_SEARCH_SCORE = 85
MB_MIN_TITLE_SIM = 0.85
MB_MIN_ARTIST_SIM = 0.60


def _lucene_escape(text: str) -> str:
    """Escape the two characters that can break out of a quoted Lucene phrase."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _credit_names(rec: dict) -> list[str]:
    """Every artist name MusicBrainz lists on a recording (credited name + canonical name)."""
    names: list[str] = []
    for credit in rec.get("artist-credit") or []:
        if not isinstance(credit, dict):
            continue
        for n in (credit.get("name"), (credit.get("artist") or {}).get("name")):
            if n and n not in names:
                names.append(n)
    joined = " ".join(names)
    return names + ([joined] if len(names) > 1 else [])


def _is_same_recording(rec: dict, title: str, artist: str) -> bool:
    from services import strict_matcher as sm

    if sm.title_similarity(title, rec.get("title", "")) < MB_MIN_TITLE_SIM:
        return False
    if not artist:
        return True
    names = _credit_names(rec)
    return bool(names) and max(sm._fuzzy_ratio(artist.lower(), n.lower()) for n in names) >= MB_MIN_ARTIST_SIM


def search_recording_tags(title: str, artist: str = "") -> dict | None:
    """
    Find the MusicBrainz recording that IS this track and return its community tags.

    Returns {"title", "artist", "score", "tags": [{"name", "count"}, ...]}. When the request
    worked but nothing confidently matches, the dict has empty title/artist and no tags (a
    definite answer, safe to cache); None means the request itself failed. The previous implementation built the URL
    with raw spaces (http.client rejects those — the blanket except turned every search that
    included an artist into a silent "no result") and trusted the first hit whatever it was,
    so a generic title could return the genre of an unrelated recording.
    """
    if not title:
        return None
    artist = "" if (artist or "").strip().lower() in _PLACEHOLDER_ARTISTS else artist.strip()
    try:
        query = f'recording:"{_lucene_escape(title)}"'
        if artist:
            query += f' AND artist:"{_lucene_escape(artist)}"'
        url = f"{_MB_BASE}/recording?" + urllib.parse.urlencode({"query": query, "limit": 5, "fmt": "json"})
        for rec in _get(url).get("recordings", []):
            if int(rec.get("score", 0)) < MB_MIN_SEARCH_SCORE or not _is_same_recording(rec, title, artist):
                continue
            tags: list[dict] = list(rec.get("tags") or [])
            # Recording tags are sparse; the release group usually carries the real genre votes.
            if len(tags) < 2:
                rg_id = next((r.get("release-group", {}).get("id", "") for r in rec.get("releases", [])[:1]), "")
                if rg_id:
                    try:
                        tags.extend(_get(f"{_MB_BASE}/release-group/{rg_id}?inc=tags&fmt=json").get("tags", []))
                    except Exception:
                        pass
            names = _credit_names(rec)
            return {"title": rec.get("title", ""), "artist": names[0] if names else "",
                    "score": int(rec.get("score", 0)), "tags": tags}
        return {"title": "", "artist": "", "score": 0, "tags": []}
    except Exception as exc:
        logger.debug(f"[musicbrainz] search failed for {title!r}: {exc}")
        return None


def lookup_by_search(title: str, artist: str = "") -> str:
    """
    Search MusicBrainz for a recording by title (+ optional artist) and return
    a genre folder name derived from its tags.
    Returns "" on no confident match or network failure.
    """
    hit = search_recording_tags(title, artist)
    genre = _tags_to_genre(hit["tags"]) if hit else ""
    if genre:
        logger.debug(f"[musicbrainz] '{title}' → {genre}")
    return genre


def lookup_by_fingerprint(filepath: str) -> str:
    """
    Fingerprint the audio with fpcalc, submit to AcoustID, get MusicBrainz
    recording tags, and return a genre folder name.
    Requires ACOUSTID_API_KEY env var.
    Returns "" if key missing, fpcalc unavailable, or no match.
    """
    if not _acoustid_key():
        return ""
    try:
        from services.fingerprint_service import _run_fpcalc
        fp_str, duration = _run_fpcalc(filepath)
        if not fp_str:
            return ""

        params = urllib.parse.urlencode({
            "client":      _acoustid_key(),
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
