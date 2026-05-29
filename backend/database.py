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

from pymongo import MongoClient, ASCENDING, DESCENDING  # MUSICBRAINZ
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
            serverSelectionTimeoutMS=10000,  # Atlas M0 SRV cold-connect needs >5s
            connectTimeoutMS=10000,  # MUSICBRAINZ
        )  # MUSICBRAINZ
        _db = _client[MONGODB_DB]  # MUSICBRAINZ
        _ensure_indexes()  # MUSICBRAINZ
        _initialized = True  # MUSICBRAINZ
        logger.info(f"[database] Connected to MongoDB: {MONGODB_URI}/{MONGODB_DB}")  # MUSICBRAINZ
        return _db  # MUSICBRAINZ


get_db = _get_db  # public alias used by tagger_service and organizer_service


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
    db.tagging_failures.create_index(  # MUSICBRAINZ
        [("error_type", ASCENDING), ("timestamp", DESCENDING)],  # MUSICBRAINZ
        name="idx_failure_error_type_time",  # MUSICBRAINZ
    )  # MUSICBRAINZ

    # MUSICBRAINZ — download_history compound index (user_id optional field)
    db.download_history.create_index(  # MUSICBRAINZ
        [("user_id", ASCENDING), ("downloaded_at", DESCENDING)],  # MUSICBRAINZ
        name="idx_user_downloaded_at",  # MUSICBRAINZ
        sparse=True,  # MUSICBRAINZ — sparse because user_id is optional
    )  # MUSICBRAINZ

    # LIBRARY INDEX — O(1) dedup, replaces os.walk scan
    db.library_index.create_index(
        [("identity_key", 1)],
        unique=True,
        name="idx_identity_key",
    )
    db.library_index.create_index(
        [("spotify_id", 1)],
        sparse=True,
        name="idx_lib_spotify_id",
    )
    db.library_index.create_index(
        [("content_hash", 1)],
        sparse=True,
        name="idx_lib_content_hash",
    )
    # FINGERPRINT INDEX — Phase 13 audio fingerprinting
    db.library_index.create_index(
        [("audio_fingerprint", 1), ("fingerprint_source", 1)],
        sparse=True,
        name="idx_lib_fingerprint",
    )

    # ARTIST MEMORY — Phase 2 genre learning
    db.artist_memory.create_index(
        [("artist_key", 1)],
        unique=True,
        name="idx_am_artist_key",
    )
    db.artist_memory.create_index(
        [("genre", 1), ("family", 1)],
        name="idx_am_genre_family",
    )
    db.artist_memory.create_index(
        [("last_seen", DESCENDING)],
        name="idx_am_last_seen",
    )

    # CUSTOM FOLDER MAPPINGS — unique folder name
    db.custom_folder_mappings.create_index(
        [("folder_name", 1)],
        unique=True,
        name="idx_cfm_folder_name",
    )

    logger.info("[database] MongoDB indexes ensured")  # MUSICBRAINZ


def is_mongo_available() -> bool:  # MUSICBRAINZ
    """Check if MongoDB is reachable.

    Fast path: ping the existing singleton client if already initialized.
    Slow path: create a throwaway client for the very first check.
    Always logs the exception so Render logs show the real error.
    """
    global _client, _initialized

    # Fast path — reuse the already-connected singleton (avoids new TLS handshake)
    if _initialized and _client is not None:
        try:
            _client.admin.command("ping")
            return True
        except Exception as e:
            logger.warning(f"[database] MongoDB ping failed on existing client: {e}")
            return False

    # Slow path — singleton not yet initialized, create a throwaway probe client
    try:
        probe = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,  # Atlas M0 SRV cold-connect needs >5s
        )
        probe.admin.command("ping")
        probe.close()
        return True
    except Exception as e:
        logger.warning(f"[database] MongoDB availability check failed: {e}")
        return False


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


def get_artist_memory_collection():
    """Return the artist_memory collection (Phase 2 genre learning)."""
    return _get_db().artist_memory


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


def update_tagging_report(filename: str, tagging_report: dict, spotify_id: str = None):  # MUSICBRAINZ
    """Attach a tagging report to an existing download_history document.

    Uses spotify_id as the primary lookup when provided — this survives
    collision-suffix renames (_1, _2) applied during PASS 3 of the ingest
    pipeline.  Falls back to filename so pre-patch records still update.
    Never upserts — avoids creating phantom history entries.
    """  # MUSICBRAINZ
    col = get_download_history_collection()  # MUSICBRAINZ
    if spotify_id:  # MUSICBRAINZ
        query = {"$or": [{"spotify_id": spotify_id}, {"filename": filename}]}  # MUSICBRAINZ
    else:  # MUSICBRAINZ
        query = {"filename": filename}  # MUSICBRAINZ
    col.update_one(  # MUSICBRAINZ
        query,  # MUSICBRAINZ
        {"$set": {"tagging_report": tagging_report}},  # MUSICBRAINZ
        upsert=False,  # MUSICBRAINZ — never create phantom history entries
    )  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — musicbrainz_cache helpers
# ═══════════════════════════════════════════════════════════════════

def get_cached_mb(track_id: str) -> dict | None:  # MUSICBRAINZ
    """Retrieve cached MusicBrainz data, updating hit counter and last_accessed."""  # MUSICBRAINZ
    col = get_musicbrainz_cache_collection()  # MUSICBRAINZ
    doc = col.find_one_and_update(  # MUSICBRAINZ
        {"track_id": track_id},  # MUSICBRAINZ
        {  # MUSICBRAINZ
            "$inc": {"cache_hit_count": 1},  # MUSICBRAINZ
            "$set": {"last_accessed": datetime.now(timezone.utc)},  # MUSICBRAINZ
        },  # MUSICBRAINZ
        return_document=True,  # MUSICBRAINZ
    )  # MUSICBRAINZ
    if doc is None:  # MUSICBRAINZ
        return None  # MUSICBRAINZ
    return doc.get("mb_data")  # MUSICBRAINZ


def set_cached_mb(track_id: str, mb_data: dict):  # MUSICBRAINZ
    """Store MusicBrainz data in cache (upsert). Initialises hit counter on insert."""  # MUSICBRAINZ
    col = get_musicbrainz_cache_collection()  # MUSICBRAINZ
    now = datetime.now(timezone.utc)  # MUSICBRAINZ
    col.update_one(  # MUSICBRAINZ
        {"track_id": track_id},  # MUSICBRAINZ
        {  # MUSICBRAINZ
            "$set": {  # MUSICBRAINZ
                "mb_data": mb_data,  # MUSICBRAINZ
                "cached_at": now,  # MUSICBRAINZ
                "last_accessed": now,  # MUSICBRAINZ
            },  # MUSICBRAINZ
            "$setOnInsert": {"cache_hit_count": 0},  # MUSICBRAINZ — only set on new docs
        },  # MUSICBRAINZ
        upsert=True,  # MUSICBRAINZ
    )  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — tagging_failures helpers
# ═══════════════════════════════════════════════════════════════════

def _classify_error_type(error: str | BaseException) -> str:  # MUSICBRAINZ
    """Classify a tagging error into a category using stable types/codes."""  # MUSICBRAINZ
    if isinstance(error, ConnectionFailure):  # MUSICBRAINZ
        return "network"  # MUSICBRAINZ
    if isinstance(error, (TimeoutError, OSError)):  # MUSICBRAINZ
        return "network"  # MUSICBRAINZ
    if not isinstance(error, str):  # MUSICBRAINZ
        return "unknown"  # MUSICBRAINZ

    error_code = error.strip().lower().replace(" ", "_").replace("-", "_")  # MUSICBRAINZ
    code_map = {  # MUSICBRAINZ
        "network": "network",  # MUSICBRAINZ
        "connection_failure": "network",  # MUSICBRAINZ
        "timeout": "network",  # MUSICBRAINZ
        "metadata_missing": "metadata_missing",  # MUSICBRAINZ
        "no_musicbrainz_match": "metadata_missing",  # MUSICBRAINZ
        "mb_miss": "metadata_missing",  # MUSICBRAINZ
        "_miss": "metadata_missing",  # MUSICBRAINZ
        "format_invalid": "format_invalid",  # MUSICBRAINZ
        "invalid_format": "format_invalid",  # MUSICBRAINZ
        "rate_limit": "rate_limit",  # MUSICBRAINZ
        "429": "rate_limit",  # MUSICBRAINZ
    }  # MUSICBRAINZ
    return code_map.get(error_code, "unknown")  # MUSICBRAINZ


def log_tagging_failure(  # MUSICBRAINZ
    track_id: str,  # MUSICBRAINZ
    title: str,  # MUSICBRAINZ
    artist: str,  # MUSICBRAINZ
    error: str | BaseException,  # MUSICBRAINZ
):  # MUSICBRAINZ
    """Record a tagging failure with error classification and retry tracking."""  # MUSICBRAINZ
    col = get_tagging_failures_collection()  # MUSICBRAINZ
    now = datetime.now(timezone.utc)  # MUSICBRAINZ
    # MUSICBRAINZ — upsert by track_id so re-runs increment retry_count
    col.update_one(  # MUSICBRAINZ
        {"track_id": track_id},  # MUSICBRAINZ
        {  # MUSICBRAINZ
            "$set": {  # MUSICBRAINZ
                "title": title,  # MUSICBRAINZ
                "artist": artist,  # MUSICBRAINZ
                "error": str(error)[:500],  # MUSICBRAINZ
                "error_type": _classify_error_type(error),  # MUSICBRAINZ
                "last_retry_timestamp": now,  # MUSICBRAINZ
            },  # MUSICBRAINZ
            "$inc": {"retry_count": 1},  # MUSICBRAINZ
            "$setOnInsert": {"timestamp": now},  # MUSICBRAINZ — first seen
        },  # MUSICBRAINZ
        upsert=True,  # MUSICBRAINZ
    )  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════
# LIBRARY INDEX — persistent O(1) dedup (Phase 5)
# ═══════════════════════════════════════════════════════════════════

def get_library_index_collection():
    """Return the library_index collection."""
    return _get_db().library_index


def get_custom_folder_mappings_collection():
    """Return the custom_folder_mappings collection (user-defined folder→genre)."""
    return _get_db().custom_folder_mappings


def index_track(
    identity_key: str,
    spotify_id: str,
    content_hash: str,
    title: str,
    artist: str,
    filename: str,
    final_path: str,
    genre_folder: str = "",
    genre_confidence: float = 0.0,
    # Phase 13 — optional fingerprint fields
    audio_fingerprint: str = "",
    fingerprint_source: str = "",
    fingerprint_confidence: float = 0.0,
) -> bool:
    """
    Add or update a track in the library index.

    Uses identity_key as the unique key (upsert). Returns True on insert
    (new track), False on update (already indexed).
    """
    col = get_library_index_collection()
    now = datetime.now(timezone.utc)
    fields: dict = {
        "spotify_id": spotify_id,
        "content_hash": content_hash,
        "title": title,
        "artist": artist,
        "filename": filename,
        "final_path": final_path,
        "genre_folder": genre_folder,
        "genre_confidence": genre_confidence,
        "last_seen": now,
    }
    # Only write fingerprint fields when provided — avoids overwriting good
    # fingerprints with empty strings on routine metadata-only updates.
    if audio_fingerprint:
        fields["audio_fingerprint"] = audio_fingerprint
        fields["fingerprint_source"] = fingerprint_source
        fields["fingerprint_confidence"] = fingerprint_confidence
    result = col.update_one(
        {"identity_key": identity_key},
        {"$set": fields, "$setOnInsert": {"indexed_at": now}},
        upsert=True,
    )
    return result.upserted_id is not None


def is_indexed(identity_key: str) -> bool:
    """Return True if identity_key exists in the library index."""
    col = get_library_index_collection()
    return col.count_documents({"identity_key": identity_key}, limit=1) > 0


def lookup_by_spotify_id(spotify_id: str) -> dict | None:
    """Return the library_index document for a Spotify ID, or None."""
    if not spotify_id:
        return None
    col = get_library_index_collection()
    return col.find_one({"spotify_id": spotify_id}, {"_id": 0})


def lookup_by_content_hash(ch: str) -> dict | None:
    """Return the library_index document matching the content hash, or None."""
    if not ch:
        return None
    col = get_library_index_collection()
    return col.find_one({"content_hash": ch}, {"_id": 0})


def remove_from_index(identity_key: str) -> bool:
    """Remove a track from the library index. Returns True if a doc was deleted."""
    col = get_library_index_collection()
    result = col.delete_one({"identity_key": identity_key})
    return result.deleted_count > 0


def get_library_stats() -> dict:
    """Return summary statistics for the library index."""
    col = get_library_index_collection()
    total = col.count_documents({})
    genres = col.distinct("genre_folder")
    return {"total_indexed": total, "genre_folders": sorted(genres)}
