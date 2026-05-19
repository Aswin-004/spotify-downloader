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
import urllib.parse
import urllib.request

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
    return bool(_API_KEY)
