"""
MusicBrainz Auto-Tagging Service
=================================
Looks up tracks on MusicBrainz, writes ID3 tags via mutagen,
and falls back to Spotify metadata when no MusicBrainz match is found.

All functions are thread-safe and respect MusicBrainz rate limits.
"""
# MUSICBRAINZ — entire file is new

import os  # MUSICBRAINZ
import io  # MUSICBRAINZ
import json  # MUSICBRAINZ
import time  # MUSICBRAINZ
import sqlite3  # MUSICBRAINZ
import hashlib  # MUSICBRAINZ
import threading  # MUSICBRAINZ
from difflib import SequenceMatcher  # MUSICBRAINZ
from pathlib import Path  # MUSICBRAINZ

# MUSICBRAINZ — MusicBrainz client
import musicbrainzngs  # MUSICBRAINZ

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
musicbrainzngs.set_useragent(  # MUSICBRAINZ
    "SpotifyDownloader",  # MUSICBRAINZ
    "1.0",  # MUSICBRAINZ
    "aswin.abhinab22@gmail.com",  # MUSICBRAINZ
)  # MUSICBRAINZ

# MUSICBRAINZ — Rate limiter: max 1 request per second (strict)
_mb_lock = threading.Lock()  # MUSICBRAINZ
_mb_last_call = 0.0  # MUSICBRAINZ

# MUSICBRAINZ — SQLite cache + failure logging
_DB_PATH = os.path.join(os.path.dirname(__file__), "cache", "tagger.db")  # MUSICBRAINZ
_db_lock = threading.Lock()  # MUSICBRAINZ
_db_initialized = False  # MUSICBRAINZ

# MUSICBRAINZ — Spotify key map (pitch class → musical key)
_PITCH_CLASS_MAP = {  # MUSICBRAINZ
    0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E", 5: "F",  # MUSICBRAINZ
    6: "F#", 7: "G", 8: "G#", 9: "A", 10: "A#", 11: "B",  # MUSICBRAINZ
}  # MUSICBRAINZ
_MODE_MAP = {0: "m", 1: ""}  # MUSICBRAINZ — 0=minor, 1=major


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — SQLite setup (Task 4)
# ═══════════════════════════════════════════════════════════════════

def _get_db() -> sqlite3.Connection:  # MUSICBRAINZ
    """Return a new SQLite connection (one per call for thread safety)."""  # MUSICBRAINZ
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)  # MUSICBRAINZ
    conn = sqlite3.connect(_DB_PATH, timeout=10)  # MUSICBRAINZ
    conn.row_factory = sqlite3.Row  # MUSICBRAINZ
    return conn  # MUSICBRAINZ


def _ensure_tables():  # MUSICBRAINZ
    """Create tagging tables if they don't exist (idempotent)."""  # MUSICBRAINZ
    global _db_initialized  # MUSICBRAINZ
    if _db_initialized:  # MUSICBRAINZ
        return  # MUSICBRAINZ
    with _db_lock:  # MUSICBRAINZ
        if _db_initialized:  # MUSICBRAINZ
            return  # MUSICBRAINZ
        conn = _get_db()  # MUSICBRAINZ
        try:  # MUSICBRAINZ
            # MUSICBRAINZ — Cache table for MusicBrainz lookups
            conn.execute("""  
                CREATE TABLE IF NOT EXISTS musicbrainz_cache (
                    track_id        TEXT PRIMARY KEY,
                    mb_data         TEXT NOT NULL,
                    cached_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)  # MUSICBRAINZ
            # MUSICBRAINZ — Failures table for review
            conn.execute("""  
                CREATE TABLE IF NOT EXISTS tagging_failures (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id        TEXT,
                    title           TEXT,
                    artist          TEXT,
                    error           TEXT,
                    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)  # MUSICBRAINZ
            conn.commit()  # MUSICBRAINZ
            _db_initialized = True  # MUSICBRAINZ
        finally:  # MUSICBRAINZ
            conn.close()  # MUSICBRAINZ

    # MUSICBRAINZ — Add tagging_report column to download_history if it exists
    _migrate_download_history()  # MUSICBRAINZ


def _migrate_download_history():  # MUSICBRAINZ
    """Add tagging_report column to existing download_history table if missing."""  # MUSICBRAINZ
    history_db = os.path.join(os.path.dirname(__file__), "cache", "download_history.db")  # MUSICBRAINZ
    if not os.path.isfile(history_db):  # MUSICBRAINZ
        return  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        conn = sqlite3.connect(history_db, timeout=10)  # MUSICBRAINZ
        cursor = conn.execute("PRAGMA table_info(download_history)")  # MUSICBRAINZ
        columns = [row[1] for row in cursor.fetchall()]  # MUSICBRAINZ
        if "tagging_report" not in columns:  # MUSICBRAINZ
            conn.execute("ALTER TABLE download_history ADD COLUMN tagging_report TEXT")  # MUSICBRAINZ
            conn.commit()  # MUSICBRAINZ
            logger.info("Added tagging_report column to download_history table")  # MUSICBRAINZ
        conn.close()  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"Could not migrate download_history: {e}")  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Cache helpers
# ═══════════════════════════════════════════════════════════════════

def _cache_key(title: str, artist: str) -> str:  # MUSICBRAINZ
    """Generate a deterministic cache key from title + artist."""  # MUSICBRAINZ
    raw = f"{title.lower().strip()}|{artist.lower().strip()}"  # MUSICBRAINZ
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]  # MUSICBRAINZ


def _get_cached_mb(track_id: str) -> dict | None:  # MUSICBRAINZ
    """Retrieve cached MusicBrainz data if less than 30 days old."""  # MUSICBRAINZ
    _ensure_tables()  # MUSICBRAINZ
    conn = _get_db()  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        row = conn.execute(  # MUSICBRAINZ
            "SELECT mb_data, cached_at FROM musicbrainz_cache WHERE track_id = ?",  # MUSICBRAINZ
            (track_id,),  # MUSICBRAINZ
        ).fetchone()  # MUSICBRAINZ
        if not row:  # MUSICBRAINZ
            return None  # MUSICBRAINZ
        # MUSICBRAINZ — Check 30-day expiry
        cached_at = row["cached_at"]  # MUSICBRAINZ
        if cached_at:  # MUSICBRAINZ
            from datetime import datetime, timedelta  # MUSICBRAINZ
            try:  # MUSICBRAINZ
                cached_time = datetime.fromisoformat(cached_at)  # MUSICBRAINZ
                if datetime.now() - cached_time > timedelta(days=30):  # MUSICBRAINZ
                    return None  # MUSICBRAINZ
            except (ValueError, TypeError):  # MUSICBRAINZ
                pass  # MUSICBRAINZ
        return json.loads(row["mb_data"])  # MUSICBRAINZ
    except Exception:  # MUSICBRAINZ
        return None  # MUSICBRAINZ
    finally:  # MUSICBRAINZ
        conn.close()  # MUSICBRAINZ


def _set_cached_mb(track_id: str, mb_data: dict):  # MUSICBRAINZ
    """Store MusicBrainz data in cache."""  # MUSICBRAINZ
    _ensure_tables()  # MUSICBRAINZ
    conn = _get_db()  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        conn.execute(  # MUSICBRAINZ
            "INSERT OR REPLACE INTO musicbrainz_cache (track_id, mb_data, cached_at) VALUES (?, ?, CURRENT_TIMESTAMP)",  # MUSICBRAINZ
            (track_id, json.dumps(mb_data)),  # MUSICBRAINZ
        )  # MUSICBRAINZ
        conn.commit()  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"Failed to cache MusicBrainz data: {e}")  # MUSICBRAINZ
    finally:  # MUSICBRAINZ
        conn.close()  # MUSICBRAINZ


def _log_tagging_failure(track_id: str, title: str, artist: str, error: str):  # MUSICBRAINZ
    """Record a tagging failure for review."""  # MUSICBRAINZ
    _ensure_tables()  # MUSICBRAINZ
    conn = _get_db()  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        conn.execute(  # MUSICBRAINZ
            "INSERT INTO tagging_failures (track_id, title, artist, error) VALUES (?, ?, ?, ?)",  # MUSICBRAINZ
            (track_id, title, artist, error[:500]),  # MUSICBRAINZ
        )  # MUSICBRAINZ
        conn.commit()  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"Failed to log tagging failure: {e}")  # MUSICBRAINZ
    finally:  # MUSICBRAINZ
        conn.close()  # MUSICBRAINZ


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
    Results are cached in SQLite for 30 days.  # MUSICBRAINZ
    """  # MUSICBRAINZ
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
        # MUSICBRAINZ — Cache the miss to avoid re-fetching
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
                # MUSICBRAINZ — Pick highest-count tag as genre
                sorted_tags = sorted(tag_list, key=lambda t: int(t.get("count", 0)), reverse=True)  # MUSICBRAINZ
                genre = sorted_tags[0].get("name", "") if sorted_tags else ""  # MUSICBRAINZ

            # MUSICBRAINZ — Extract release info (album, year, track number)
            release_list = rec.get("release-list", [])  # MUSICBRAINZ
            album_name = ""  # MUSICBRAINZ
            release_year = ""  # MUSICBRAINZ
            track_number = ""  # MUSICBRAINZ
            album_artist = ""  # MUSICBRAINZ
            if release_list:  # MUSICBRAINZ
                rel = release_list[0]  # MUSICBRAINZ
                album_name = rel.get("title", "")  # MUSICBRAINZ
                release_year = (rel.get("date", "") or "")[:4]  # MUSICBRAINZ
                # MUSICBRAINZ — Track number from medium list
                medium_list = rel.get("medium-list", [])  # MUSICBRAINZ
                if medium_list:  # MUSICBRAINZ
                    track_list = medium_list[0].get("track-list", [])  # MUSICBRAINZ
                    if track_list:  # MUSICBRAINZ
                        track_number = track_list[0].get("number", "")  # MUSICBRAINZ
                # MUSICBRAINZ — Album artist
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

def _get_audio_features(spotify_service, track_id: str) -> dict:  # MUSICBRAINZ
    """Fetch BPM and musical key from Spotify audio features API."""  # MUSICBRAINZ
    result = {"bpm": None, "key": None}  # MUSICBRAINZ
    if not spotify_service or not track_id:  # MUSICBRAINZ
        return result  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        sp = spotify_service.sp  # MUSICBRAINZ
        features = sp.audio_features([track_id])  # MUSICBRAINZ
        if features and features[0]:  # MUSICBRAINZ
            f = features[0]  # MUSICBRAINZ
            tempo = f.get("tempo")  # MUSICBRAINZ
            if tempo and tempo > 0:  # MUSICBRAINZ
                result["bpm"] = round(tempo)  # MUSICBRAINZ
            key_num = f.get("key", -1)  # MUSICBRAINZ
            mode_num = f.get("mode", -1)  # MUSICBRAINZ
            if key_num >= 0 and mode_num >= 0:  # MUSICBRAINZ
                result["key"] = _PITCH_CLASS_MAP.get(key_num, "") + _MODE_MAP.get(mode_num, "")  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"[tagger] Spotify audio features failed: {e}")  # MUSICBRAINZ
    return result  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Tag writing
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
    _ensure_tables()  # MUSICBRAINZ

    tags_written = []  # MUSICBRAINZ
    source = "spotify_fallback"  # MUSICBRAINZ
    confidence_score = 0.0  # MUSICBRAINZ
    isrc_matched = False  # MUSICBRAINZ
    needs_review = False  # MUSICBRAINZ
    bpm_val = None  # MUSICBRAINZ
    key_val = None  # MUSICBRAINZ
    genre_val = ""  # MUSICBRAINZ

    # MUSICBRAINZ — Attempt MusicBrainz lookup if not provided
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
    bpm_val = audio_features.get("bpm")  # MUSICBRAINZ
    key_val = audio_features.get("key")  # MUSICBRAINZ

    # MUSICBRAINZ — Merge metadata: MusicBrainz takes priority, Spotify fills gaps
    mb = musicbrainz_data or {}  # MUSICBRAINZ
    sp = spotify_metadata or {}  # MUSICBRAINZ

    tag_title = mb.get("title") or sp.get("title", "")  # MUSICBRAINZ
    tag_artist = sp.get("artist", "") or mb.get("artist", "")  # MUSICBRAINZ — Spotify artist preferred (exact match)
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

        # MUSICBRAINZ — TIT2: Title
        if tag_title:  # MUSICBRAINZ
            audio.delall("TIT2")  # MUSICBRAINZ
            audio.add(TIT2(encoding=3, text=[tag_title]))  # MUSICBRAINZ
            tags_written.append("TIT2")  # MUSICBRAINZ

        # MUSICBRAINZ — TPE1: Artist
        if tag_artist:  # MUSICBRAINZ
            audio.delall("TPE1")  # MUSICBRAINZ
            audio.add(TPE1(encoding=3, text=[tag_artist]))  # MUSICBRAINZ
            tags_written.append("TPE1")  # MUSICBRAINZ

        # MUSICBRAINZ — TALB: Album
        if tag_album:  # MUSICBRAINZ
            audio.delall("TALB")  # MUSICBRAINZ
            audio.add(TALB(encoding=3, text=[tag_album]))  # MUSICBRAINZ
            tags_written.append("TALB")  # MUSICBRAINZ

        # MUSICBRAINZ — TPE2: Album Artist
        if tag_album_artist:  # MUSICBRAINZ
            audio.delall("TPE2")  # MUSICBRAINZ
            audio.add(TPE2(encoding=3, text=[tag_album_artist]))  # MUSICBRAINZ
            tags_written.append("TPE2")  # MUSICBRAINZ

        # MUSICBRAINZ — TRCK: Track Number
        if tag_track_number:  # MUSICBRAINZ
            audio.delall("TRCK")  # MUSICBRAINZ
            audio.add(TRCK(encoding=3, text=[str(tag_track_number)]))  # MUSICBRAINZ
            tags_written.append("TRCK")  # MUSICBRAINZ

        # MUSICBRAINZ — TDRC: Year
        if tag_year:  # MUSICBRAINZ
            audio.delall("TDRC")  # MUSICBRAINZ
            audio.add(TDRC(encoding=3, text=[tag_year]))  # MUSICBRAINZ
            tags_written.append("TDRC")  # MUSICBRAINZ

        # MUSICBRAINZ — TCON: Genre
        if genre_val:  # MUSICBRAINZ
            audio.delall("TCON")  # MUSICBRAINZ
            audio.add(TCON(encoding=3, text=[genre_val]))  # MUSICBRAINZ
            tags_written.append("TCON")  # MUSICBRAINZ

        # MUSICBRAINZ — TBPM: BPM
        if bpm_val:  # MUSICBRAINZ
            audio.delall("TBPM")  # MUSICBRAINZ
            audio.add(TBPM(encoding=3, text=[str(bpm_val)]))  # MUSICBRAINZ
            tags_written.append("TBPM")  # MUSICBRAINZ

        # MUSICBRAINZ — TKEY: Musical Key
        if key_val:  # MUSICBRAINZ
            audio.delall("TKEY")  # MUSICBRAINZ
            audio.add(TKEY(encoding=3, text=[key_val]))  # MUSICBRAINZ
            tags_written.append("TKEY")  # MUSICBRAINZ

        # MUSICBRAINZ — TSRC: ISRC
        if tag_isrc:  # MUSICBRAINZ
            audio.delall("TSRC")  # MUSICBRAINZ
            audio.add(TSRC(encoding=3, text=[tag_isrc]))  # MUSICBRAINZ
            tags_written.append("TSRC")  # MUSICBRAINZ

        # MUSICBRAINZ — TXXX: MusicBrainz Recording Id
        if tag_mb_id:  # MUSICBRAINZ
            audio.delall("TXXX:MusicBrainz Recording Id")  # MUSICBRAINZ
            audio.add(TXXX(encoding=3, desc="MusicBrainz Recording Id", text=[tag_mb_id]))  # MUSICBRAINZ
            tags_written.append("TXXX:MusicBrainz Recording Id")  # MUSICBRAINZ

        # MUSICBRAINZ — COMM: Comment
        audio.delall("COMM::eng")  # MUSICBRAINZ
        audio.add(COMM(encoding=3, lang="eng", desc="", text=["Downloaded via SpotifyDownloader"]))  # MUSICBRAINZ
        tags_written.append("COMM")  # MUSICBRAINZ

        # MUSICBRAINZ — APIC: Album Art (640x640 from Spotify)
        if album_art_url:  # MUSICBRAINZ
            art_data = _fetch_album_art(album_art_url)  # MUSICBRAINZ
            if art_data:  # MUSICBRAINZ
                audio.delall("APIC")  # MUSICBRAINZ
                audio.add(APIC(  # MUSICBRAINZ
                    encoding=3,  # MUSICBRAINZ
                    mime="image/jpeg",  # MUSICBRAINZ
                    type=3,  # MUSICBRAINZ — Cover (front)
                    desc="Cover",  # MUSICBRAINZ
                    data=art_data,  # MUSICBRAINZ
                ))  # MUSICBRAINZ
                tags_written.append("APIC")  # MUSICBRAINZ

        # MUSICBRAINZ — Save tags to file
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
        "genre": genre_val,  # MUSICBRAINZ
    }  # MUSICBRAINZ

    return report  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Store tagging report in download_history
# ═══════════════════════════════════════════════════════════════════

def save_tagging_report(filename: str, report: dict):  # MUSICBRAINZ
    """Persist tagging report to the download_history table."""  # MUSICBRAINZ
    history_db = os.path.join(os.path.dirname(__file__), "cache", "download_history.db")  # MUSICBRAINZ
    if not os.path.isfile(history_db):  # MUSICBRAINZ
        return  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        conn = sqlite3.connect(history_db, timeout=10)  # MUSICBRAINZ
        conn.execute(  # MUSICBRAINZ
            "UPDATE download_history SET tagging_report = ? WHERE filename = ?",  # MUSICBRAINZ
            (json.dumps(report), filename),  # MUSICBRAINZ
        )  # MUSICBRAINZ
        conn.commit()  # MUSICBRAINZ
        conn.close()  # MUSICBRAINZ
    except Exception as e:  # MUSICBRAINZ
        logger.warning(f"[tagger] Failed to save tagging report to history: {e}")  # MUSICBRAINZ
