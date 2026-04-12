"""
Genre Router
============
Resolves the destination folder for a downloaded track using
Spotify artist genre tags. Single source of truth for all
folder routing decisions.

Rule: Genre/ArtistName/
Fallback: Uncategorized/ArtistName/
"""

from typing import Any

from loguru import logger

from config import config
from services.organizer_service import clean_folder_name


# In-memory cache, keyed by Spotify artist_id. Module-level on purpose:
# resets on server restart, which is fine — Spotify genre tags are stable
# enough that a fresh fetch per process is harmless, and this keeps the
# cache free of the staleness problems a persistent store would bring.
_genre_cache: dict = {}


def _matches_devanagari(text: str) -> bool:
    """True if `text` contains any Devanagari character (U+0900..U+097F).

    Used as a last-resort fallback when the Spotify genres list is empty
    and the artist name itself is written in Hindi script — those tracks
    almost always belong in the Indian bucket.
    """
    if not text:
        return False
    return any("\u0900" <= ch <= "\u097F" for ch in text)


def _match_genre(genres: list) -> str:
    """
    Walk the artist's Spotify `genres` list and return the first parent
    folder from SPOTIFY_GENRE_MAP whose key appears (case-insensitively)
    as a substring of any genre tag. Returns an empty string when nothing
    matches — the caller is responsible for the Devanagari / Uncategorized
    fallbacks.

    Args:
        genres: List of genre strings from a Spotify artist object
                (e.g. ["uk garage", "bassline", "speed garage"]).

    Returns:
        The mapped parent folder name (e.g. "UK Garage") or "" on no match.
    """
    # Genres-first, keys sorted by length descending (specificity).
    # Spotify lists genres by weight so we honour the artist's primary
    # tag while still preferring "uk garage" over "house" when both match.
    # Keys are sorted here defensively — don't rely on dict insertion order.
    sorted_keys = sorted(config.SPOTIFY_GENRE_MAP.keys(), key=len, reverse=True)
    for genre in genres:
        genre_lower = genre.lower()
        for key in sorted_keys:
            if key in genre_lower:
                return config.SPOTIFY_GENRE_MAP[key]
    return ""


def resolve_genre_folder(artist_id: str, artist_name: str, sp: Any) -> str:
    """
    Resolve the subfolder path for a track based on its Spotify artist
    genres. Returns a relative path like ``"UK Garage/Sammy Virji"`` that
    the caller appends to the ingest base directory.

    Never raises — on any Spotify API error, falls back silently to
    ``"Uncategorized/<clean_artist_name>"`` and logs a warning. A missing
    or empty `artist_id` short-circuits straight to the fallback so we
    don't waste an API call on a guaranteed miss.

    Args:
        artist_id:   Spotify artist ID (from ``track["artists"][0]["id"]``).
        artist_name: Display name for the folder label.
        sp:          Authenticated ``spotipy.Spotify`` client.

    Returns:
        Relative folder path: ``"{genre_folder}/{clean_artist_name}"``.
    """
    clean_artist_name = clean_folder_name(artist_name)

    # Cache hit — return immediately, no API call.
    if artist_id and artist_id in _genre_cache:
        cached = _genre_cache[artist_id]
        logger.debug(f"[genre_router] cache hit: {artist_name} → {cached}")
        return cached

    # No artist_id means we can't look up genres — go straight to fallback.
    if not artist_id:
        fallback = f"Uncategorized/{clean_artist_name}"
        logger.info(f"[genre_router] {artist_name} → {fallback} (no artist_id)")
        return fallback

    # Fetch artist object, tolerate any failure.
    try:
        artist_obj = sp.artist(artist_id)
        genres = artist_obj.get("genres", []) or []
    except Exception as e:
        fallback = f"Uncategorized/{clean_artist_name}"
        logger.warning(
            f"[genre_router] sp.artist({artist_id}) failed for {artist_name}: {e} "
            f"→ {fallback}"
        )
        # Cache the fallback too, so we don't retry broken lookups on every track.
        _genre_cache[artist_id] = fallback
        return fallback

    # Primary: match against SPOTIFY_GENRE_MAP.
    genre_folder = _match_genre(genres)
    matched_tag = None
    if genre_folder:
        # Recover which tag fired, for the log line — purely cosmetic.
        for tag in genres:
            tag_lower = tag.lower()
            for key in config.SPOTIFY_GENRE_MAP:
                if key in tag_lower:
                    matched_tag = tag
                    break
            if matched_tag:
                break

    # Secondary: Devanagari artist names → Indian.
    if not genre_folder and _matches_devanagari(artist_name):
        genre_folder = "Indian"
        matched_tag = "devanagari-artist-name"

    # Tertiary: give up and bucket as Uncategorized.
    if not genre_folder:
        genre_folder = "Uncategorized"

    result = f"{genre_folder}/{clean_artist_name}"
    _genre_cache[artist_id] = result

    if matched_tag:
        logger.info(
            f"[genre_router] {artist_name} → {result} (matched: '{matched_tag}')"
        )
    else:
        logger.info(f"[genre_router] {artist_name} → {result} (no genre match)")

    return result
