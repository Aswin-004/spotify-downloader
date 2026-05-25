"""
Last.fm metadata service — tag-based genre lookup.

Fallback chain position: after Spotify, before Gemini.
Requires LASTFM_API_KEY env var (free at last.fm/api).
Gracefully returns "" if key missing, track not found, or network fails.

No quota concerns — Last.fm allows 5 req/s on free tier.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import requests

try:
    from loguru import logger as _lg
    logger = _lg
except ImportError:
    logger = logging.getLogger(__name__)

_API_KEY = os.getenv("LASTFM_API_KEY", "")
_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# (tag substring, genre folder) — checked in order, first match wins.
# More specific patterns first to avoid "house" matching "tech house" wrongly.
_TAG_MAP: list[tuple[str, str]] = [
    # Indian
    ("bollywood",         "Bollywood"),
    ("hindi film",        "Bollywood"),
    ("filmi",             "Bollywood"),
    ("hindi",             "Bollywood"),
    ("desi",              "Bollywood"),
    ("indian",            "Bollywood"),
    ("punjabi",           "Punjabi"),
    ("bhangra",           "Punjabi"),
    ("tamil",             "Tamil"),
    # Electronic — specific before generic
    ("psytrance",         "Trance"),
    ("progressive trance","Trance"),
    ("trance",            "Trance"),
    ("drum and bass",     "Drum & Bass"),
    ("dnb",               "Drum & Bass"),
    ("jungle",            "Drum & Bass"),
    ("uk garage",         "UK Garage"),
    ("speed garage",      "UK Garage"),
    ("2step",             "UK Garage"),
    ("grime",             "Grime"),
    ("uk drill",          "Grime"),
    ("dubstep",           "Dubstep"),
    ("brostep",           "Dubstep"),
    ("techno",            "Techno"),
    ("deep house",        "House"),
    ("afro house",        "House"),
    ("tech house",        "House"),
    ("house",             "House"),
    ("electronic",        "Electronic"),
    # Global
    ("hip-hop",           "Hip Hop"),
    ("hip hop",           "Hip Hop"),
    ("rap",               "Hip Hop"),
    ("r&b",               "R&B"),
    ("rnb",               "R&B"),
    ("soul",              "R&B"),
    ("pop",               "Pop"),
    ("latin",             "Latin"),
    ("reggaeton",         "Latin"),
    ("cumbia",            "Latin"),
]

_MIN_TAG_COUNT = 30   # tags with fewer votes are noise


def lookup_genre(title: str, artist: str) -> str:
    """
    Query Last.fm for the top community tags on a track and map to a genre folder.
    Returns a genre folder name (e.g. "House", "Bollywood") or "" on failure.
    """
    if not _API_KEY:
        return ""
    if not title or not artist:
        return ""

    try:
        params = urllib.parse.urlencode({
            "method":      "track.getTopTags",
            "artist":      artist,
            "track":       title,
            "api_key":     _API_KEY,
            "format":      "json",
            "autocorrect": 1,
        })
        req = urllib.request.Request(
            f"{_BASE_URL}?{params}",
            headers={"User-Agent": "spotify-meta-downloader/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        tags = data.get("toptags", {}).get("tag", [])
        if not tags:
            return ""

        # Prefer tags with community consensus; fall back to top tag alone
        strong = [t["name"].lower() for t in tags if int(t.get("count", 0)) >= _MIN_TAG_COUNT]
        tag_names = strong or [tags[0]["name"].lower()]

        for tag in tag_names:
            for pattern, genre in _TAG_MAP:
                if pattern in tag:
                    logger.debug(f"[lastfm] {artist!r} – {title!r} → {genre} (tag: {tag!r})")
                    return genre
        return ""

    except Exception as exc:
        logger.debug(f"[lastfm] lookup failed for {artist!r} – {title!r}: {exc}")
        return ""


def is_available() -> bool:
    """True when an API key is configured."""
    try:
        from config import config
        return bool(config.LASTFM_API_KEY)
    except Exception:
        return bool(_API_KEY)


# ── Full enrichment (genre + mood + listeners + all tags) ──────────────────────
# Additive — does not modify lookup_genre() above.

_CACHE_LOCK = threading.Lock()
_enrich_cache: dict = {}          # key → (result_dict, datetime)
_CACHE_TTL = timedelta(days=30)
_CACHE_MAX = 10_000

_RATE_LOCK = threading.Lock()
_last_enrich_call: float = 0.0
_ENRICH_INTERVAL = 0.25           # 4 req/sec ceiling

_MOOD_KEYWORDS = {
    "energetic", "dark", "uplifting", "melancholic", "happy", "sad",
    "aggressive", "party", "relaxed", "romantic", "chill", "intense",
    "euphoric", "moody", "bittersweet", "feel-good", "dancefloor",
    "nostalgic", "epic", "emotional", "positive", "atmospheric",
}


def _enrich_cache_key(artist: str, title: str, mbid: str) -> str:
    if mbid:
        return f"mbid:{mbid}"
    from services.genre_router import normalize_artist_key
    return f"{normalize_artist_key(artist)}||{title.lower().strip()}"


def _enrich_cache_get(key: str) -> dict | None:
    with _CACHE_LOCK:
        entry = _enrich_cache.get(key)
        if entry and datetime.utcnow() - entry[1] < _CACHE_TTL:
            return entry[0]
    return None


def _enrich_cache_set(key: str, value: dict) -> None:
    with _CACHE_LOCK:
        if len(_enrich_cache) >= _CACHE_MAX:
            oldest = min(_enrich_cache, key=lambda k: _enrich_cache[k][1])
            del _enrich_cache[oldest]
        _enrich_cache[key] = (value, datetime.utcnow())


def _enrich_rate_limited_get(params: dict) -> dict:
    global _last_enrich_call
    with _RATE_LOCK:
        now = time.time()
        wait = _ENRICH_INTERVAL - (now - _last_enrich_call)
        if wait > 0:
            time.sleep(wait)
        _last_enrich_call = time.time()

    from config import config
    params["api_key"] = config.LASTFM_API_KEY
    params["format"] = "json"
    resp = requests.get(_BASE_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _parse_enrichment_tags(tags_raw: list) -> tuple[str, str, str]:
    """Return (genre_str, mood_str, all_tags_str) from Last.fm toptags list."""
    from services.genre_router import normalize_genre

    tag_names = [t.get("name", "").lower().strip() for t in tags_raw if t.get("name")]

    mood_tags = [t for t in tag_names if t in _MOOD_KEYWORDS]
    genre_tags = [t for t in tag_names if t not in _MOOD_KEYWORDS]

    genre_str = ""
    for tag in genre_tags:
        mapped = normalize_genre(tag)
        if mapped:
            genre_str = mapped
            break

    return genre_str, ", ".join(mood_tags[:4]), ", ".join(tag_names[:10])


def enrich_from_lastfm(artist: str, title: str, mbid: str = "") -> dict:
    """
    Fetch Last.fm tags and return enrichment dict.

    Keys: lastfm_genre, lastfm_mood, lastfm_tags, lastfm_listeners.
    Returns {} on any failure, missing API key, or no tag data.
    Results cached 30 days per (mbid or artist+title) key.
    """
    _PLACEHOLDER_ARTISTS = {
        "unknown", "various artists", "various", "n/a", "va",
        # Genre labels stored as artist (corrupted metadata pattern)
        "electronic", "indian", "bollywood", "hiphop", "hip hop",
        "dance", "punjabi", "house", "techno", "grime", "latin",
        "r&b", "pop", "rock", "metal", "classical", "jazz",
    }
    if not _API_KEY or not artist or artist.lower().strip() in _PLACEHOLDER_ARTISTS:
        return {}

    key = _enrich_cache_key(artist, title, mbid)
    cached = _enrich_cache_get(key)
    if cached is not None:
        logger.debug(f"[lastfm] cache hit for {key!r}")
        return cached

    try:
        params: dict = {"method": "track.getInfo", "autocorrect": "1"}
        if mbid:
            params["mbid"] = mbid
        else:
            params["artist"] = artist
            params["track"] = title

        data = _enrich_rate_limited_get(params)
        track_data = data.get("track") or {}

        tags_raw = (track_data.get("toptags") or {}).get("tag") or []
        if isinstance(tags_raw, dict):
            tags_raw = [tags_raw]

        _used_artist_fallback = False
        if not tags_raw:
            # Artist-level fallback — only reliable when the track itself is known on Last.fm
            # (listeners > 0 means track.getInfo found the correct track).
            _used_artist_fallback = True
            data2 = _enrich_rate_limited_get(
                {"method": "artist.getTopTags", "artist": artist, "autocorrect": "1"}
            )
            tags_raw = ((data2.get("toptags") or {}).get("tag")) or []
            if isinstance(tags_raw, dict):
                tags_raw = [tags_raw]

        if not tags_raw:
            _enrich_cache_set(key, {})
            return {}

        listeners = 0
        try:
            listeners = int(track_data.get("listeners", 0))
        except (ValueError, TypeError):
            pass

        # Guard: artist fallback + 0 track listeners = track not on Last.fm.
        # The artist.getTopTags matched some other artist with the same name.
        # Reject to avoid storing genre noise from unrelated artists.
        if _used_artist_fallback and listeners == 0:
            logger.debug(
                f"[lastfm] {artist!r} / {title!r} — artist-fallback with 0 listeners, skipping"
            )
            _enrich_cache_set(key, {})
            return {}

        # Guard: very low listener count = near-certain metadata mismatch.
        # Legitimate tracks in a DJ library have at least a handful of listeners.
        # Tracks with 1–29 listeners are almost always wrong-artist autocorrect hits.
        _MIN_LISTENERS = 30
        if listeners < _MIN_LISTENERS:
            logger.debug(
                f"[lastfm] {artist!r} / {title!r} — {listeners} listeners below "
                f"threshold ({_MIN_LISTENERS}), skipping"
            )
            _enrich_cache_set(key, {})
            return {}

        genre_str, mood_str, all_tags_str = _parse_enrichment_tags(tags_raw)

        result = {
            "lastfm_genre":     genre_str,
            "lastfm_mood":      mood_str,
            "lastfm_tags":      all_tags_str,
            "lastfm_listeners": listeners,
        }
        _enrich_cache_set(key, result)
        logger.info(
            f"[lastfm] {artist!r} / {title!r} → genre={genre_str!r} "
            f"mood={mood_str!r} listeners={listeners}"
        )
        return result

    except Exception as e:
        logger.debug(f"[lastfm] enrich_from_lastfm failed for {artist!r}/{title!r}: {e}")
        _enrich_cache_set(key, {})
        return {}


def enrich_cache_size() -> int:
    """Return number of entries in the enrichment cache."""
    with _CACHE_LOCK:
        return len(_enrich_cache)
