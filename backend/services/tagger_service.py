"""
MusicBrainz Auto-Tagging Service  # MUSICBRAINZ
=================================
Looks up tracks on MusicBrainz, writes ID3 tags via mutagen,  # MUSICBRAINZ
and falls back to Spotify metadata when no MusicBrainz match is found.  # MUSICBRAINZ

All functions are thread-safe and respect MusicBrainz rate limits.  # MUSICBRAINZ
Storage: MongoDB via database.py  # MUSICBRAINZ
"""
# MUSICBRAINZ — entire file rewritten for MongoDB

import os  # MUSICBRAINZ
import hashlib  # MUSICBRAINZ
import time  # MUSICBRAINZ
import threading  # MUSICBRAINZ
from difflib import SequenceMatcher  # MUSICBRAINZ

# MUSICBRAINZ — MusicBrainz client (optional — app degrades gracefully if absent)
try:
    import musicbrainzngs  # MUSICBRAINZ
    _MB_AVAILABLE = True
except ImportError:
    musicbrainzngs = None  # type: ignore[assignment]
    _MB_AVAILABLE = False

# MUSICBRAINZ — Mutagen for ID3 tagging
from mutagen.mp3 import MP3  # MUSICBRAINZ
from mutagen.id3 import (  # MUSICBRAINZ
    ID3,  # MUSICBRAINZ
    ID3NoHeaderError,  # MUSICBRAINZ
    TIT2,  # MUSICBRAINZ
    TPE1,  # MUSICBRAINZ
    TALB,  # MUSICBRAINZ
    TPE2,  # MUSICBRAINZ
    TRCK,  # MUSICBRAINZ
    TDRC,  # MUSICBRAINZ
    TCON,  # MUSICBRAINZ
    TBPM,  # MUSICBRAINZ
    TKEY,  # MUSICBRAINZ
    TSRC,  # MUSICBRAINZ
    TXXX,  # MUSICBRAINZ
    COMM,  # MUSICBRAINZ
    APIC,  # MUSICBRAINZ
)  # MUSICBRAINZ

# MUSICBRAINZ — MongoDB storage
from database import (  # MUSICBRAINZ
    get_cached_mb,  # MUSICBRAINZ
    set_cached_mb,  # MUSICBRAINZ
    log_tagging_failure,  # MUSICBRAINZ
    update_tagging_report,  # MUSICBRAINZ
)  # MUSICBRAINZ

# MUSICBRAINZ — Optional requests for album art
try:  # MUSICBRAINZ
    import requests as _requests  # MUSICBRAINZ
except ImportError:  # MUSICBRAINZ
    _requests = None  # MUSICBRAINZ

# MUSICBRAINZ — Loguru / stdlib fallback
try:  # MUSICBRAINZ
    from loguru import logger  # MUSICBRAINZ
except ImportError:  # MUSICBRAINZ
    import logging  # MUSICBRAINZ
    logger = logging.getLogger(__name__)  # MUSICBRAINZ

# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Configuration
# ═══════════════════════════════════════════════════════════════════
if _MB_AVAILABLE:  # MUSICBRAINZ — only configure if import succeeded
    musicbrainzngs.set_useragent(  # MUSICBRAINZ
        "SpotifyDownloader",  # MUSICBRAINZ
        "1.0",  # MUSICBRAINZ
        "aswin.abhinab22@gmail.com",  # MUSICBRAINZ
    )  # MUSICBRAINZ
else:
    logger.warning(
        "[tagger] musicbrainzngs not installed — MusicBrainz enrichment disabled. "
        "Run: pip install musicbrainzngs>=0.7.1  "
        "Spotify tagging (BPM, key, camelot, artwork, TXXX) remains fully operational."
    )

# MUSICBRAINZ — Rate limiter: max 1 request per second (strict)
_mb_lock = threading.Lock()  # MUSICBRAINZ
_mb_last_call = 0.0  # MUSICBRAINZ

# MUSICBRAINZ — Spotify key map (pitch class → musical key)
_PITCH_CLASS_MAP = {  # MUSICBRAINZ
    0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E", 5: "F",  # MUSICBRAINZ
    6: "F#", 7: "G", 8: "G#", 9: "A", 10: "A#", 11: "B",  # MUSICBRAINZ
}  # MUSICBRAINZ
_MODE_MAP = {0: "m", 1: ""}  # MUSICBRAINZ — 0=minor, 1=major

# Camelot Wheel — (key_num, mode_num) → Camelot notation for harmonic mixing.
# mode_num: 1 = major (B suffix), 0 = minor (A suffix).
# Source: standard Camelot Wheel / Open Key mapping.
_CAMELOT_MAP = {  # CAMELOT
    (0,  1): "8B",  (0,  0): "5A",   # C  major / C  minor
    (1,  1): "3B",  (1,  0): "12A",  # C# major / C# minor
    (2,  1): "10B", (2,  0): "7A",   # D  major / D  minor
    (3,  1): "5B",  (3,  0): "2A",   # D# major / D# minor
    (4,  1): "12B", (4,  0): "9A",   # E  major / E  minor
    (5,  1): "7B",  (5,  0): "4A",   # F  major / F  minor
    (6,  1): "2B",  (6,  0): "11A",  # F# major / F# minor
    (7,  1): "9B",  (7,  0): "6A",   # G  major / G  minor
    (8,  1): "4B",  (8,  0): "1A",   # G# major / G# minor
    (9,  1): "11B", (9,  0): "8A",   # A  major / A  minor
    (10, 1): "6B",  (10, 0): "3A",   # A# major / A# minor
    (11, 1): "1B",  (11, 0): "10A",  # B  major / B  minor
}  # CAMELOT


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Initialization (no-op for MongoDB, kept for compat)
# ═══════════════════════════════════════════════════════════════════

def _ensure_tables():  # MUSICBRAINZ
    """No-op — MongoDB collections are created automatically."""  # MUSICBRAINZ
    pass  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Cache helpers
# ═══════════════════════════════════════════════════════════════════

def _cache_key(title: str, artist: str) -> str:  # MUSICBRAINZ
    """Generate a deterministic cache key from title + artist."""  # MUSICBRAINZ
    raw = f"{title.lower().strip()}|{artist.lower().strip()}"  # MUSICBRAINZ
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]  # MUSICBRAINZ


def _get_cached_mb(track_id: str) -> dict | None:  # MUSICBRAINZ
    """Retrieve cached MusicBrainz data from MongoDB."""  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        data = get_cached_mb(track_id)  # MUSICBRAINZ
        if data is None:  # MUSICBRAINZ
            return None  # MUSICBRAINZ
        return data  # MUSICBRAINZ
    except Exception:  # MUSICBRAINZ
        return None  # MUSICBRAINZ


def _set_cached_mb(track_id: str, mb_data: dict):  # MUSICBRAINZ
    """Store MusicBrainz data in MongoDB cache."""  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        set_cached_mb(track_id, mb_data)  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"Failed to cache MusicBrainz data: {e}")  # MUSICBRAINZ


def _log_tagging_failure(track_id: str, title: str, artist: str, error: str):  # MUSICBRAINZ
    """Record a tagging failure in MongoDB."""  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        log_tagging_failure(track_id, title, artist, error)  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"Failed to log tagging failure: {e}")  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Lookup
# ═══════════════════════════════════════════════════════════════════

def _rate_limit_mb():  # MUSICBRAINZ
    """Enforce strict 1 request/second rate limit for MusicBrainz API."""  # MUSICBRAINZ
    global _mb_last_call  # MUSICBRAINZ
    with _mb_lock:  # MUSICBRAINZ
        elapsed = time.time() - _mb_last_call  # MUSICBRAINZ
        if elapsed < 1.0:  # MUSICBRAINZ
            time.sleep(1.0 - elapsed)  # MUSICBRAINZ
        _mb_last_call = time.time()  # MUSICBRAINZ


def _string_similarity(a: str, b: str) -> float:  # MUSICBRAINZ
    """Return 0.0–1.0 similarity ratio between two strings."""  # MUSICBRAINZ
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()  # MUSICBRAINZ


def lookup_musicbrainz(title: str, artist: str, duration_ms: int = None) -> dict | None:  # MUSICBRAINZ
    """
    Search MusicBrainz for a recording matching title + artist.  # MUSICBRAINZ

    Scoring:  # MUSICBRAINZ
      - Title similarity > 85%  → +40 points  # MUSICBRAINZ
      - Artist name match       → +30 points  # MUSICBRAINZ
      - Duration within ±3s     → +20 points  # MUSICBRAINZ
      - Has ISRC code           → +10 points  # MUSICBRAINZ

    Returns best match dict if score > 60, else None.  # MUSICBRAINZ
    Results are cached in MongoDB with 30-day TTL.  # MUSICBRAINZ
    """  # MUSICBRAINZ
    if not _MB_AVAILABLE:  # MUSICBRAINZ — graceful degraded mode
        logger.debug("[tagger] musicbrainzngs unavailable — skipping MusicBrainz lookup")
        return None

    cache_id = _cache_key(title, artist)  # MUSICBRAINZ

    # MUSICBRAINZ — Check cache first
    cached = _get_cached_mb(cache_id)  # MUSICBRAINZ
    if cached is not None:  # MUSICBRAINZ
        logger.info(f"[tagger] MusicBrainz cache HIT: {title} - {artist}")  # MUSICBRAINZ
        return cached if cached.get("mb_id") else None  # MUSICBRAINZ

    # MUSICBRAINZ — Rate limit then query
    _rate_limit_mb()  # MUSICBRAINZ

    try:  # MUSICBRAINZ
        query = f'recording:"{title}" AND artist:"{artist}"'  # MUSICBRAINZ
        result = musicbrainzngs.search_recordings(  # MUSICBRAINZ
            query=query,  # MUSICBRAINZ
            limit=10,  # MUSICBRAINZ
        )  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"[tagger] MusicBrainz search failed: {e}")  # MUSICBRAINZ
        _set_cached_mb(cache_id, {"_miss": True})  # MUSICBRAINZ
        return None  # MUSICBRAINZ

    recordings = result.get("recording-list", [])  # MUSICBRAINZ
    if not recordings:  # MUSICBRAINZ
        logger.info(f"[tagger] MusicBrainz: no results for {title} - {artist}")  # MUSICBRAINZ
        _set_cached_mb(cache_id, {"_miss": True})  # MUSICBRAINZ
        return None  # MUSICBRAINZ

    best_match = None  # MUSICBRAINZ
    best_score = 0  # MUSICBRAINZ

    for rec in recordings:  # MUSICBRAINZ
        score = 0  # MUSICBRAINZ

        # MUSICBRAINZ — Title similarity (40 points)
        rec_title = rec.get("title", "")  # MUSICBRAINZ
        title_sim = _string_similarity(title, rec_title)  # MUSICBRAINZ
        if title_sim > 0.85:  # MUSICBRAINZ
            score += 40  # MUSICBRAINZ

        # MUSICBRAINZ — Artist match (30 points)
        rec_artists = rec.get("artist-credit", [])  # MUSICBRAINZ
        rec_artist_name = ""  # MUSICBRAINZ
        if rec_artists:  # MUSICBRAINZ
            rec_artist_name = rec_artists[0].get("artist", {}).get("name", "")  # MUSICBRAINZ
        artist_sim = _string_similarity(artist, rec_artist_name)  # MUSICBRAINZ
        if artist_sim > 0.80:  # MUSICBRAINZ
            score += 30  # MUSICBRAINZ

        # MUSICBRAINZ — Duration match ±3s (20 points)
        rec_length = rec.get("length")  # MUSICBRAINZ
        if rec_length and duration_ms:  # MUSICBRAINZ
            diff_ms = abs(int(rec_length) - duration_ms)  # MUSICBRAINZ
            if diff_ms <= 3000:  # MUSICBRAINZ
                score += 20  # MUSICBRAINZ

        # MUSICBRAINZ — ISRC presence (10 points)
        isrc_list = rec.get("isrc-list", [])  # MUSICBRAINZ
        has_isrc = len(isrc_list) > 0  # MUSICBRAINZ
        if has_isrc:  # MUSICBRAINZ
            score += 10  # MUSICBRAINZ

        if score > best_score:  # MUSICBRAINZ
            best_score = score  # MUSICBRAINZ

            # MUSICBRAINZ — Extract genre from tags
            tag_list = rec.get("tag-list", [])  # MUSICBRAINZ
            genre = ""  # MUSICBRAINZ
            if tag_list:  # MUSICBRAINZ
                sorted_tags = sorted(tag_list, key=lambda t: int(t.get("count", 0)), reverse=True)  # MUSICBRAINZ
                genre = sorted_tags[0].get("name", "") if sorted_tags else ""  # MUSICBRAINZ

            # MUSICBRAINZ — Extract release info
            release_list = rec.get("release-list", [])  # MUSICBRAINZ
            album_name = ""  # MUSICBRAINZ
            release_year = ""  # MUSICBRAINZ
            track_number = ""  # MUSICBRAINZ
            album_artist = ""  # MUSICBRAINZ
            if release_list:  # MUSICBRAINZ
                rel = release_list[0]  # MUSICBRAINZ
                album_name = rel.get("title", "")  # MUSICBRAINZ
                release_year = (rel.get("date", "") or "")[:4]  # MUSICBRAINZ
                medium_list = rel.get("medium-list", [])  # MUSICBRAINZ
                if medium_list:  # MUSICBRAINZ
                    track_list = medium_list[0].get("track-list", [])  # MUSICBRAINZ
                    if track_list:  # MUSICBRAINZ
                        track_number = track_list[0].get("number", "")  # MUSICBRAINZ
                rel_artist_credit = rel.get("artist-credit", [])  # MUSICBRAINZ
                if rel_artist_credit:  # MUSICBRAINZ
                    album_artist = rel_artist_credit[0].get("artist", {}).get("name", "")  # MUSICBRAINZ

            best_match = {  # MUSICBRAINZ
                "mb_id": rec.get("id", ""),  # MUSICBRAINZ
                "title": rec_title,  # MUSICBRAINZ
                "artist": rec_artist_name,  # MUSICBRAINZ
                "album": album_name,  # MUSICBRAINZ
                "album_artist": album_artist,  # MUSICBRAINZ
                "year": release_year,  # MUSICBRAINZ
                "track_number": track_number,  # MUSICBRAINZ
                "genre": genre,  # MUSICBRAINZ
                "isrc": isrc_list[0] if isrc_list else "",  # MUSICBRAINZ
                "score": best_score,  # MUSICBRAINZ
                "title_similarity": round(title_sim, 3),  # MUSICBRAINZ
                "artist_similarity": round(artist_sim, 3),  # MUSICBRAINZ
            }  # MUSICBRAINZ

    # MUSICBRAINZ — Threshold check
    if best_score <= 60:  # MUSICBRAINZ
        logger.info(f"[tagger] MusicBrainz: best score {best_score} <= 60 for {title} - {artist}")  # MUSICBRAINZ
        _set_cached_mb(cache_id, {"_miss": True})  # MUSICBRAINZ
        return None  # MUSICBRAINZ

    logger.info(f"[tagger] MusicBrainz match: {best_match['title']} (score={best_score})")  # MUSICBRAINZ
    _set_cached_mb(cache_id, best_match)  # MUSICBRAINZ
    return best_match  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Spotify audio features (BPM + Key)
# ═══════════════════════════════════════════════════════════════════

_PITCH_CLASS_REVERSE = {v: k for k, v in _PITCH_CLASS_MAP.items()}  # MUSICBRAINZ


def _get_audio_features(spotify_service, track_id: str) -> dict:  # MUSICBRAINZ
    """
    Fetch BPM, standard key, Camelot key, energy, and danceability from Spotify.

    Returns dict with a '_source' key: 'spotify' | 'unavailable'.
    Sets '_403' = True when the endpoint is deprecated for this auth flow.
    """  # MUSICBRAINZ
    result = {  # MUSICBRAINZ
        "bpm": None, "key": None, "camelot": None,
        "energy": None, "danceability": None,
        "_source": "unavailable", "_403": False,
    }
    if not spotify_service or not track_id:  # MUSICBRAINZ
        return result  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        import spotipy.exceptions  # MUSICBRAINZ
        sp = spotify_service.sp  # MUSICBRAINZ
        features = sp.audio_features([track_id])  # MUSICBRAINZ
        if features and features[0]:  # MUSICBRAINZ
            f = features[0]  # MUSICBRAINZ
            tempo = f.get("tempo")  # MUSICBRAINZ
            if tempo and tempo > 0:  # MUSICBRAINZ
                result["bpm"] = round(tempo)  # MUSICBRAINZ
            key_num  = f.get("key",  -1)  # MUSICBRAINZ
            mode_num = f.get("mode", -1)  # MUSICBRAINZ
            if key_num >= 0 and mode_num >= 0:  # MUSICBRAINZ
                result["key"]     = _PITCH_CLASS_MAP.get(key_num, "") + _MODE_MAP.get(mode_num, "")  # MUSICBRAINZ
                result["camelot"] = _CAMELOT_MAP.get((key_num, mode_num))  # CAMELOT
            energy = f.get("energy")  # MUSICBRAINZ
            if energy is not None:  # MUSICBRAINZ
                result["energy"] = round(float(energy), 3)  # MUSICBRAINZ
            danceability = f.get("danceability")  # MUSICBRAINZ
            if danceability is not None:  # MUSICBRAINZ
                result["danceability"] = round(float(danceability), 3)  # MUSICBRAINZ
            result["_source"] = "spotify"  # MUSICBRAINZ
    except spotipy.exceptions.SpotifyException as e:  # MUSICBRAINZ
        if e.http_status == 403:
            # Spotify deprecated GET /v1/audio-features for Client Credentials
            # tokens on November 27, 2024. Caller should use librosa fallback.
            result["_403"] = True
            logger.warning(
                "[tagger] Spotify audio-features returned 403 — endpoint deprecated "
                "for app-only (client_credentials) tokens since Nov 2024. "
                "BPM/key will be derived from local librosa analysis."
            )
        else:
            logger.warning(f"[tagger] Spotify audio features failed (HTTP {e.http_status}): {e}")  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"[tagger] Spotify audio features failed: {e}")  # MUSICBRAINZ
    return result  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Album art helper
# ═══════════════════════════════════════════════════════════════════

def _fetch_album_art(url: str) -> bytes | None:  # MUSICBRAINZ
    """Download album art image bytes from URL."""  # MUSICBRAINZ
    if not url or not _requests:  # MUSICBRAINZ
        return None  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        resp = _requests.get(url, timeout=15)  # MUSICBRAINZ
        resp.raise_for_status()  # MUSICBRAINZ
        if len(resp.content) > 100:  # MUSICBRAINZ
            return resp.content  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"[tagger] Album art download failed: {e}")  # MUSICBRAINZ
    return None  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Tag writing
# ═══════════════════════════════════════════════════════════════════

def tag_file(  # MUSICBRAINZ
    file_path: str,  # MUSICBRAINZ
    spotify_metadata: dict,  # MUSICBRAINZ
    musicbrainz_data: dict = None,  # MUSICBRAINZ
    spotify_service_instance=None,  # MUSICBRAINZ
) -> dict:  # MUSICBRAINZ
    """
    Write ID3 tags to an MP3 file using MusicBrainz + Spotify metadata.  # MUSICBRAINZ

    Args:  # MUSICBRAINZ
        file_path: Path to the .mp3 file  # MUSICBRAINZ
        spotify_metadata: Dict with title, artist, album, id, album_art_url, duration_ms, etc.  # MUSICBRAINZ
        musicbrainz_data: Optional MB lookup result from lookup_musicbrainz()  # MUSICBRAINZ
        spotify_service_instance: Optional SpotifyService for audio features  # MUSICBRAINZ

    Returns:  # MUSICBRAINZ
        Tagging report dict  # MUSICBRAINZ
    """  # MUSICBRAINZ

    tags_written = []  # MUSICBRAINZ
    source = "spotify_fallback"  # MUSICBRAINZ
    confidence_score = 0.0  # MUSICBRAINZ
    isrc_matched = False  # MUSICBRAINZ
    needs_review = False  # MUSICBRAINZ
    bpm_val = None  # MUSICBRAINZ
    key_val = None  # MUSICBRAINZ
    camelot_val = None  # CAMELOT
    energy_val = None  # MUSICBRAINZ
    danceability_val = None  # MUSICBRAINZ
    genre_val = ""  # MUSICBRAINZ

    # MUSICBRAINZ — Attempt MusicBrainz lookup if not provided
    if not _MB_AVAILABLE and musicbrainz_data is None:
        logger.warning(
            "[tagger] Degraded mode — musicbrainzngs unavailable. "
            "Tagging with Spotify metadata only (BPM/key/camelot/artwork still written)."
        )
    if musicbrainz_data is None:  # MUSICBRAINZ
        try:  # MUSICBRAINZ
            musicbrainz_data = lookup_musicbrainz(  # MUSICBRAINZ
                spotify_metadata.get("title", ""),  # MUSICBRAINZ
                spotify_metadata.get("artist", ""),  # MUSICBRAINZ
                spotify_metadata.get("duration_ms"),  # MUSICBRAINZ
            )  # MUSICBRAINZ
        except Exception as e:  # MUSICBRAINZ
            logger.warning(f"[tagger] MusicBrainz lookup exception: {e}")  # MUSICBRAINZ
            musicbrainz_data = None  # MUSICBRAINZ

    # MUSICBRAINZ — Determine source and confidence
    if musicbrainz_data and musicbrainz_data.get("mb_id"):  # MUSICBRAINZ
        source = "musicbrainz"  # MUSICBRAINZ
        confidence_score = musicbrainz_data.get("score", 0) / 100.0  # MUSICBRAINZ
    else:  # MUSICBRAINZ
        needs_review = True  # MUSICBRAINZ
        confidence_score = 0.5  # MUSICBRAINZ
        cache_id = _cache_key(  # MUSICBRAINZ
            spotify_metadata.get("title", ""),  # MUSICBRAINZ
            spotify_metadata.get("artist", ""),  # MUSICBRAINZ
        )  # MUSICBRAINZ
        _log_tagging_failure(  # MUSICBRAINZ
            cache_id,  # MUSICBRAINZ
            spotify_metadata.get("title", ""),  # MUSICBRAINZ
            spotify_metadata.get("artist", ""),  # MUSICBRAINZ
            "No MusicBrainz match found (score <= 60)",  # MUSICBRAINZ
        )  # MUSICBRAINZ

    # MUSICBRAINZ — Fetch Spotify audio features (BPM, Key)
    track_id = spotify_metadata.get("id", "")  # MUSICBRAINZ
    audio_features = _get_audio_features(spotify_service_instance, track_id)  # MUSICBRAINZ
    bpm_val      = audio_features.get("bpm")  # MUSICBRAINZ
    key_val      = audio_features.get("key")  # MUSICBRAINZ
    camelot_val  = audio_features.get("camelot")  # CAMELOT
    energy_val   = audio_features.get("energy")  # MUSICBRAINZ
    danceability_val = audio_features.get("danceability")  # MUSICBRAINZ

    # LIBROSA FALLBACK — Spotify audio-features deprecated for client_credentials
    # tokens (Nov 2024 → HTTP 403). Use local Krumhansl-Schmuckler analysis instead.
    if bpm_val is None and file_path:
        try:
            from bpm_key_service import detect_bpm_and_key as _local_bpm, persist_audio_features as _persist_af
            _path_lower = file_path.lower().replace("\\", "/")
            _genre_hint = "dnb" if any(k in _path_lower for k in ("/dnb/", "/drum and bass/", "/d&b/")) else ""
            local = _local_bpm(file_path, genre_hint=_genre_hint)
            if local.get("analyzed"):
                bpm_val = local.get("bpm")
                key_root = local.get("key_root", "")
                key_mode_str = local.get("key_mode", "")
                camelot_val = local.get("camelot")
                if key_root and key_mode_str:
                    mode_num = 1 if key_mode_str == "maj" else 0
                    key_num  = _PITCH_CLASS_REVERSE.get(key_root, -1)
                    key_val  = key_root + ("m" if mode_num == 0 else "")
                    if key_num >= 0 and not camelot_val:
                        camelot_val = _CAMELOT_MAP.get((key_num, mode_num))
                logger.info(
                    f"[tagger] librosa fallback: BPM={bpm_val} key={key_val} "
                    f"camelot={camelot_val}"
                )
                _sp_id = (spotify_metadata or {}).get("id", "")
                if _sp_id:
                    _persist_af(f"sp:{_sp_id}", local)
        except Exception as _lb_exc:
            logger.debug(f"[tagger] librosa fallback failed: {_lb_exc}")

    # MUSICBRAINZ — Merge metadata: MusicBrainz takes priority, Spotify fills gaps
    mb = musicbrainz_data or {}  # MUSICBRAINZ
    sp = spotify_metadata or {}  # MUSICBRAINZ

    tag_title = mb.get("title") or sp.get("title", "")  # MUSICBRAINZ
    tag_artist = sp.get("artist", "") or mb.get("artist", "")  # MUSICBRAINZ
    tag_album = mb.get("album") or sp.get("album", "")  # MUSICBRAINZ
    tag_album_artist = mb.get("album_artist") or sp.get("artist", "")  # MUSICBRAINZ
    tag_track_number = mb.get("track_number", "")  # MUSICBRAINZ
    tag_year = mb.get("year", "") or (sp.get("release_date", "") or "")[:4]  # MUSICBRAINZ
    genre_val = mb.get("genre", "")  # MUSICBRAINZ
    tag_isrc = mb.get("isrc", "")  # MUSICBRAINZ
    tag_mb_id = mb.get("mb_id", "")  # MUSICBRAINZ
    isrc_matched = bool(tag_isrc)  # MUSICBRAINZ
    album_art_url = sp.get("album_art_url", "")  # MUSICBRAINZ

    # MUSICBRAINZ — Write tags to file
    try:  # MUSICBRAINZ
        try:  # MUSICBRAINZ
            audio = ID3(file_path)  # MUSICBRAINZ
        except ID3NoHeaderError:  # MUSICBRAINZ
            audio = ID3()  # MUSICBRAINZ

        if tag_title:  # MUSICBRAINZ
            audio.delall("TIT2")  # MUSICBRAINZ
            audio.add(TIT2(encoding=3, text=[tag_title]))  # MUSICBRAINZ
            tags_written.append("TIT2")  # MUSICBRAINZ

        if tag_artist:  # MUSICBRAINZ
            audio.delall("TPE1")  # MUSICBRAINZ
            audio.add(TPE1(encoding=3, text=[tag_artist]))  # MUSICBRAINZ
            tags_written.append("TPE1")  # MUSICBRAINZ

        if tag_album:  # MUSICBRAINZ
            audio.delall("TALB")  # MUSICBRAINZ
            audio.add(TALB(encoding=3, text=[tag_album]))  # MUSICBRAINZ
            tags_written.append("TALB")  # MUSICBRAINZ

        if tag_album_artist:  # MUSICBRAINZ
            audio.delall("TPE2")  # MUSICBRAINZ
            audio.add(TPE2(encoding=3, text=[tag_album_artist]))  # MUSICBRAINZ
            tags_written.append("TPE2")  # MUSICBRAINZ

        if tag_track_number:  # MUSICBRAINZ
            audio.delall("TRCK")  # MUSICBRAINZ
            audio.add(TRCK(encoding=3, text=[str(tag_track_number)]))  # MUSICBRAINZ
            tags_written.append("TRCK")  # MUSICBRAINZ

        if tag_year:  # MUSICBRAINZ
            audio.delall("TDRC")  # MUSICBRAINZ
            audio.add(TDRC(encoding=3, text=[tag_year]))  # MUSICBRAINZ
            tags_written.append("TDRC")  # MUSICBRAINZ

        if genre_val:  # MUSICBRAINZ
            audio.delall("TCON")  # MUSICBRAINZ
            audio.add(TCON(encoding=3, text=[genre_val]))  # MUSICBRAINZ
            tags_written.append("TCON")  # MUSICBRAINZ

        if bpm_val:  # MUSICBRAINZ
            _bpm_int = str(round(float(bpm_val)))  # DJ HARDENING — integer BPM (Rekordbox rejects decimals)
            audio.delall("TBPM")  # MUSICBRAINZ
            audio.add(TBPM(encoding=3, text=[_bpm_int]))  # MUSICBRAINZ
            tags_written.append("TBPM")  # MUSICBRAINZ
            audio.delall("TXXX:BPM")  # DJ HARDENING — Rekordbox TXXX alias
            audio.add(TXXX(encoding=3, desc="BPM", text=[_bpm_int]))  # DJ HARDENING
            tags_written.append("TXXX:BPM")  # DJ HARDENING

        if key_val:  # MUSICBRAINZ
            audio.delall("TKEY")  # MUSICBRAINZ
            audio.add(TKEY(encoding=3, text=[key_val]))  # MUSICBRAINZ
            tags_written.append("TKEY")  # MUSICBRAINZ
            audio.delall("TXXX:KEY")  # DJ HARDENING — Rekordbox 6+ reads TXXX:KEY
            audio.add(TXXX(encoding=3, desc="KEY", text=[key_val]))  # DJ HARDENING
            tags_written.append("TXXX:KEY")  # DJ HARDENING

        if camelot_val:  # CAMELOT — Rekordbox/Serato read TXXX:INITIALKEY for harmonic key
            audio.delall("TXXX:INITIALKEY")  # CAMELOT
            audio.add(TXXX(encoding=3, desc="INITIALKEY", text=[camelot_val]))  # CAMELOT
            tags_written.append("TXXX:INITIALKEY")  # CAMELOT
            audio.delall("TXXX:CAMELOT")  # DJ HARDENING — explicit Camelot alias for Mixed In Key / VDJ
            audio.add(TXXX(encoding=3, desc="CAMELOT", text=[camelot_val]))  # DJ HARDENING
            tags_written.append("TXXX:CAMELOT")  # DJ HARDENING

        if track_id:  # CAMELOT — persist Spotify ID for future re-routing/dedup
            audio.delall("TXXX:SPOTIFY_ID")  # CAMELOT
            audio.add(TXXX(encoding=3, desc="SPOTIFY_ID", text=[track_id]))  # CAMELOT
            tags_written.append("TXXX:SPOTIFY_ID")  # CAMELOT

        if energy_val is not None:  # CAMELOT
            audio.delall("TXXX:ENERGY")  # CAMELOT
            audio.add(TXXX(encoding=3, desc="ENERGY", text=[str(energy_val)]))  # CAMELOT
            tags_written.append("TXXX:ENERGY")  # CAMELOT

        if danceability_val is not None:  # CAMELOT
            audio.delall("TXXX:DANCEABILITY")  # CAMELOT
            audio.add(TXXX(encoding=3, desc="DANCEABILITY", text=[str(danceability_val)]))  # CAMELOT
            tags_written.append("TXXX:DANCEABILITY")  # CAMELOT

        if tag_isrc:  # MUSICBRAINZ
            audio.delall("TSRC")  # MUSICBRAINZ
            audio.add(TSRC(encoding=3, text=[tag_isrc]))  # MUSICBRAINZ
            tags_written.append("TSRC")  # MUSICBRAINZ

        if tag_mb_id:  # MUSICBRAINZ
            audio.delall("TXXX:MusicBrainz Recording Id")  # MUSICBRAINZ
            audio.add(TXXX(encoding=3, desc="MusicBrainz Recording Id", text=[tag_mb_id]))  # MUSICBRAINZ
            tags_written.append("TXXX:MusicBrainz Recording Id")  # MUSICBRAINZ

        audio.delall("COMM::eng")  # MUSICBRAINZ
        audio.add(COMM(encoding=3, lang="eng", desc="", text=["Downloaded via SpotifyDownloader"]))  # MUSICBRAINZ
        tags_written.append("COMM")  # MUSICBRAINZ

        if album_art_url:  # MUSICBRAINZ
            art_data = _fetch_album_art(album_art_url)  # MUSICBRAINZ
            if art_data:  # MUSICBRAINZ
                audio.delall("APIC")  # MUSICBRAINZ
                audio.add(APIC(  # MUSICBRAINZ
                    encoding=3,  # MUSICBRAINZ
                    mime="image/jpeg",  # MUSICBRAINZ
                    type=3,  # MUSICBRAINZ
                    desc="Cover",  # MUSICBRAINZ
                    data=art_data,  # MUSICBRAINZ
                ))  # MUSICBRAINZ
                tags_written.append("APIC")  # MUSICBRAINZ

        audio.save(file_path)  # MUSICBRAINZ
        logger.info(f"[tagger] Tagged: {file_path} ({len(tags_written)} tags, source={source})")  # MUSICBRAINZ

    except Exception as e:  # MUSICBRAINZ
        logger.error(f"[tagger] Failed to write tags to {file_path}: {e}")  # MUSICBRAINZ
        cache_id = _cache_key(sp.get("title", ""), sp.get("artist", ""))  # MUSICBRAINZ
        _log_tagging_failure(cache_id, sp.get("title", ""), sp.get("artist", ""), str(e))  # MUSICBRAINZ
        needs_review = True  # MUSICBRAINZ

    # MUSICBRAINZ — Build tagging report
    report = {  # MUSICBRAINZ
        "source": source,  # MUSICBRAINZ
        "confidence_score": round(confidence_score, 3),  # MUSICBRAINZ
        "tags_written": tags_written,  # MUSICBRAINZ
        "isrc_matched": isrc_matched,  # MUSICBRAINZ
        "needs_review": needs_review,  # MUSICBRAINZ
        "bpm": bpm_val,  # MUSICBRAINZ
        "key": key_val,  # MUSICBRAINZ
        "camelot": camelot_val,  # CAMELOT
        "energy": energy_val,  # MUSICBRAINZ
        "danceability": danceability_val,  # MUSICBRAINZ
        "genre": genre_val,  # MUSICBRAINZ
    }  # MUSICBRAINZ

    return report  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Store tagging report in download_history (MongoDB)
# ═══════════════════════════════════════════════════════════════════

def save_tagging_report(filename: str, report: dict, spotify_id: str = None):  # MUSICBRAINZ
    """Persist tagging report to the download_history collection in MongoDB."""  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        update_tagging_report(filename, report, spotify_id=spotify_id)  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"[tagger] Failed to save tagging report to history: {e}")  # MUSICBRAINZ
