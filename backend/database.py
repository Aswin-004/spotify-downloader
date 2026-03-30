"""
MongoDB Database Layer  # MUSICBRAINZ
========================
Centralized MongoDB connection and collection management.  # MUSICBRAINZ
All storage (download history, MusicBrainz cache, tagging failures)  # MUSICBRAINZ
goes through this module.  # MUSICBRAINZ

Collections:  # MUSICBRAINZ
  - download_history   : quality reports per download  # MUSICBRAINZ
  - musicbrainz_cache  : cached MusicBrainz lookup results (30-day TTL)  # MUSICBRAINZ
  - tagging_failures   : tracks that failed MusicBrainz matching  # MUSICBRAINZ
"""
# MUSICBRAINZ — entire file is new

import os  # MUSICBRAINZ
import threading  # MUSICBRAINZ
from datetime import datetime, timedelta, timezone  # MUSICBRAINZ

from pymongo import MongoClient, DESCENDING  # MUSICBRAINZ
from pymongo.errors import ConnectionFailure  # MUSICBRAINZ

# MUSICBRAINZ — Loguru / stdlib fallback
try:  # MUSICBRAINZ
    from loguru import logger  # MUSICBRAINZ
except ImportError:  # MUSICBRAINZ
    import logging  # MUSICBRAINZ
    logger = logging.getLogger(__name__)  # MUSICBRAINZ

# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Configuration
# ═══════════════════════════════════════════════════════════════════
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")  # MUSICBRAINZ
MONGODB_DB = os.getenv("MONGODB_DB", "spotify_downloader")  # MUSICBRAINZ

# MUSICBRAINZ — Module-level state
_client = None  # MUSICBRAINZ
_db = None  # MUSICBRAINZ
_lock = threading.Lock()  # MUSICBRAINZ
_initialized = False  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Connection
# ═══════════════════════════════════════════════════════════════════

def _get_db():  # MUSICBRAINZ
    """Return the MongoDB database instance (lazy singleton)."""  # MUSICBRAINZ
    global _client, _db, _initialized  # MUSICBRAINZ
    if _initialized and _db is not None:  # MUSICBRAINZ
        return _db  # MUSICBRAINZ
    with _lock:  # MUSICBRAINZ
        if _initialized and _db is not None:  # MUSICBRAINZ
            return _db  # MUSICBRAINZ
        _client = MongoClient(  # MUSICBRAINZ
            MONGODB_URI,  # MUSICBRAINZ
            serverSelectionTimeoutMS=5000,  # MUSICBRAINZ
            connectTimeoutMS=5000,  # MUSICBRAINZ
        )  # MUSICBRAINZ
        _db = _client[MONGODB_DB]  # MUSICBRAINZ
        _ensure_indexes()  # MUSICBRAINZ
        _initialized = True  # MUSICBRAINZ
        logger.info(f"[database] Connected to MongoDB: {MONGODB_URI}/{MONGODB_DB}")  # MUSICBRAINZ
        return _db  # MUSICBRAINZ


def _ensure_indexes():  # MUSICBRAINZ
    """Create indexes on all collections (idempotent)."""  # MUSICBRAINZ
    db = _client[MONGODB_DB]  # MUSICBRAINZ

    # MUSICBRAINZ — download_history indexes
    db.download_history.create_index(  # MUSICBRAINZ
        [("downloaded_at", DESCENDING)],  # MUSICBRAINZ
        name="idx_downloaded_at",  # MUSICBRAINZ
    )  # MUSICBRAINZ
    db.download_history.create_index(  # MUSICBRAINZ
        [("filename", 1)],  # MUSICBRAINZ
        name="idx_filename",  # MUSICBRAINZ
    )  # MUSICBRAINZ

    # MUSICBRAINZ — musicbrainz_cache indexes
    db.musicbrainz_cache.create_index(  # MUSICBRAINZ
        [("track_id", 1)],  # MUSICBRAINZ
        unique=True,  # MUSICBRAINZ
        name="idx_track_id",  # MUSICBRAINZ
    )  # MUSICBRAINZ
    db.musicbrainz_cache.create_index(  # MUSICBRAINZ
        [("cached_at", 1)],  # MUSICBRAINZ
        expireAfterSeconds=30 * 24 * 3600,  # MUSICBRAINZ — 30-day TTL
        name="idx_cache_ttl",  # MUSICBRAINZ
    )  # MUSICBRAINZ

    # MUSICBRAINZ — tagging_failures indexes
    db.tagging_failures.create_index(  # MUSICBRAINZ
        [("timestamp", DESCENDING)],  # MUSICBRAINZ
        name="idx_failure_time",  # MUSICBRAINZ
    )  # MUSICBRAINZ

    logger.info("[database] MongoDB indexes ensured")  # MUSICBRAINZ


def is_mongo_available() -> bool:  # MUSICBRAINZ
    """Check if MongoDB is reachable."""  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        client = MongoClient(  # MUSICBRAINZ
            MONGODB_URI,  # MUSICBRAINZ
            serverSelectionTimeoutMS=2000,  # MUSICBRAINZ
        )  # MUSICBRAINZ
        client.admin.command("ping")  # MUSICBRAINZ
        client.close()  # MUSICBRAINZ
        return True  # MUSICBRAINZ
    except (ConnectionFailure, Exception):  # MUSICBRAINZ
        return False  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Collection accessors
# ═══════════════════════════════════════════════════════════════════

def get_download_history_collection():  # MUSICBRAINZ
    """Return the download_history collection."""  # MUSICBRAINZ
    return _get_db().download_history  # MUSICBRAINZ


def get_musicbrainz_cache_collection():  # MUSICBRAINZ
    """Return the musicbrainz_cache collection."""  # MUSICBRAINZ
    return _get_db().musicbrainz_cache  # MUSICBRAINZ


def get_tagging_failures_collection():  # MUSICBRAINZ
    """Return the tagging_failures collection."""  # MUSICBRAINZ
    return _get_db().tagging_failures  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — download_history helpers
# ═══════════════════════════════════════════════════════════════════

def save_download_report(  # MUSICBRAINZ
    track_title: str,  # MUSICBRAINZ
    artist: str,  # MUSICBRAINZ
    album: str,  # MUSICBRAINZ
    filename: str,  # MUSICBRAINZ
    report: dict,  # MUSICBRAINZ
) -> str:  # MUSICBRAINZ
    """
    Persist a quality_report dict to download_history.  # MUSICBRAINZ
    Returns the inserted document's _id as a string.  # MUSICBRAINZ
    """  # MUSICBRAINZ
    col = get_download_history_collection()  # MUSICBRAINZ
    doc = {  # MUSICBRAINZ
        "track_title": track_title,  # MUSICBRAINZ
        "artist": artist,  # MUSICBRAINZ
        "album": album or "",  # MUSICBRAINZ
        "filename": filename or "",  # MUSICBRAINZ
        "downloaded_at": datetime.now(timezone.utc),  # MUSICBRAINZ
        "bitrate_achieved": report.get("bitrate_achieved", ""),  # MUSICBRAINZ
        "source_platform": report.get("source_platform", ""),  # MUSICBRAINZ
        "duration_match_diff": report.get("duration_match_diff"),  # MUSICBRAINZ
        "title_similarity_score": report.get("title_similarity_score"),  # MUSICBRAINZ
        "art_embedded": bool(report.get("art_embedded")),  # MUSICBRAINZ
        "normalization_applied": bool(report.get("normalization_applied")),  # MUSICBRAINZ
        "query_stage_used": report.get("query_stage_used"),  # MUSICBRAINZ
        "extra": {k: v for k, v in report.items()  # MUSICBRAINZ
                  if k not in {  # MUSICBRAINZ
                      "bitrate_achieved", "source_platform",  # MUSICBRAINZ
                      "duration_match_diff", "title_similarity_score",  # MUSICBRAINZ
                      "art_embedded", "normalization_applied",  # MUSICBRAINZ
                      "query_stage_used",  # MUSICBRAINZ
                  }},  # MUSICBRAINZ
    }  # MUSICBRAINZ
    result = col.insert_one(doc)  # MUSICBRAINZ
    return str(result.inserted_id)  # MUSICBRAINZ


def get_recent_reports(limit: int = 50) -> list:  # MUSICBRAINZ
    """Return the most recent *limit* download reports as dicts."""  # MUSICBRAINZ
    col = get_download_history_collection()  # MUSICBRAINZ
    docs = col.find(  # MUSICBRAINZ
        {},  # MUSICBRAINZ
        {"_id": 0},  # MUSICBRAINZ — exclude ObjectId for JSON serialization
    ).sort("downloaded_at", DESCENDING).limit(limit)  # MUSICBRAINZ
    results = []  # MUSICBRAINZ
    for doc in docs:  # MUSICBRAINZ
        # MUSICBRAINZ — Convert datetime to ISO string for JSON
        if "downloaded_at" in doc and isinstance(doc["downloaded_at"], datetime):  # MUSICBRAINZ
            doc["downloaded_at"] = doc["downloaded_at"].isoformat()  # MUSICBRAINZ
        results.append(doc)  # MUSICBRAINZ
    return results  # MUSICBRAINZ


def update_tagging_report(filename: str, tagging_report: dict):  # MUSICBRAINZ
    """Attach a tagging report to an existing download_history document."""  # MUSICBRAINZ
    col = get_download_history_collection()  # MUSICBRAINZ
    col.update_one(  # MUSICBRAINZ
        {"filename": filename},  # MUSICBRAINZ
        {"$set": {"tagging_report": tagging_report}},  # MUSICBRAINZ
    )  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — musicbrainz_cache helpers
# ═══════════════════════════════════════════════════════════════════

def get_cached_mb(track_id: str) -> dict | None:  # MUSICBRAINZ
    """Retrieve cached MusicBrainz data (TTL handled by MongoDB TTL index)."""  # MUSICBRAINZ
    col = get_musicbrainz_cache_collection()  # MUSICBRAINZ
    doc = col.find_one({"track_id": track_id})  # MUSICBRAINZ
    if doc is None:  # MUSICBRAINZ
        return None  # MUSICBRAINZ
    return doc.get("mb_data")  # MUSICBRAINZ


def set_cached_mb(track_id: str, mb_data: dict):  # MUSICBRAINZ
    """Store MusicBrainz data in cache (upsert)."""  # MUSICBRAINZ
    col = get_musicbrainz_cache_collection()  # MUSICBRAINZ
    col.update_one(  # MUSICBRAINZ
        {"track_id": track_id},  # MUSICBRAINZ
        {"$set": {  # MUSICBRAINZ
            "mb_data": mb_data,  # MUSICBRAINZ
            "cached_at": datetime.now(timezone.utc),  # MUSICBRAINZ
        }},  # MUSICBRAINZ
        upsert=True,  # MUSICBRAINZ
    )  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — tagging_failures helpers
# ═══════════════════════════════════════════════════════════════════

def log_tagging_failure(  # MUSICBRAINZ
    track_id: str,  # MUSICBRAINZ
    title: str,  # MUSICBRAINZ
    artist: str,  # MUSICBRAINZ
    error: str,  # MUSICBRAINZ
):  # MUSICBRAINZ
    """Record a tagging failure for review."""  # MUSICBRAINZ
    col = get_tagging_failures_collection()  # MUSICBRAINZ
    col.insert_one({  # MUSICBRAINZ
        "track_id": track_id,  # MUSICBRAINZ
        "title": title,  # MUSICBRAINZ
        "artist": artist,  # MUSICBRAINZ
        "error": error[:500],  # MUSICBRAINZ
        "timestamp": datetime.now(timezone.utc),  # MUSICBRAINZ
    })  # MUSICBRAINZ
