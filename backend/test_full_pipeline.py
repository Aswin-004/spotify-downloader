"""
SpotifyDL — Full Pipeline Integration Test
=============================================
Tests every layer: Redis, MongoDB, Flask, Socket.IO, Spotify API,
download pipeline, MusicBrainz tagging, analytics, and file storage.

Route names, event names, collection names, and field names are all
taken DIRECTLY from the source code — nothing assumed.

Usage:
    cd backend
    python test_full_pipeline.py
"""

import requests
import socketio
import time
import os
import sys
import json
from datetime import datetime

# ── Ensure backend/ is on sys.path so local imports work ──────────────────
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ── Configuration ────────────────────────────────────────────────────────
BASE_URL = "http://localhost:5000"
# "Feel Good Inc." — well-known, short, high-confidence match
TEST_TRACK_URL = "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"
# Today's Top Hits — large public playlist
TEST_PLAYLIST_URL = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
# MongoDB from database.py defaults
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "spotify_downloader")

# ── Real collection names from database.py ────────────────────────────────
COLLECTION_DOWNLOAD_HISTORY = "download_history"
COLLECTION_MUSICBRAINZ_CACHE = "musicbrainz_cache"
COLLECTION_TAGGING_FAILURES = "tagging_failures"

RESULTS = []


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def log_result(test_name, passed, message=""):
    status = "\u2705 PASS" if passed else "\u274c FAIL"
    RESULTS.append({"test": test_name, "passed": passed, "message": message})
    line = f"  {status} | {test_name}"
    if message:
        line += f"  \u2014  {message}"
    print(line)


def http_get(path, timeout=10):
    """GET helper — returns (status_code, json_or_None)."""
    try:
        r = requests.get(f"{BASE_URL}{path}", timeout=timeout)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    except Exception as e:
        return None, str(e)


def http_post(path, body=None, timeout=15):
    """POST helper — returns (status_code, json_or_None)."""
    try:
        r = requests.post(f"{BASE_URL}{path}", json=body, timeout=timeout)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    except Exception as e:
        return None, str(e)


def get_mongo_db():
    """Return a raw pymongo db handle (for verification queries)."""
    from pymongo import MongoClient
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
    return client[MONGODB_DB]


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — Infrastructure
# ═══════════════════════════════════════════════════════════════════

def test_redis_connection():
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=3)
        r.ping()
        log_result("Redis Connection", True, "localhost:6379 reachable")
    except Exception as e:
        log_result("Redis Connection", False, str(e))


def test_mongodb_connection():
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        client.server_info()
        log_result("MongoDB Connection", True, f"{MONGODB_URI}")
    except Exception as e:
        log_result("MongoDB Connection", False, str(e))


def test_mongodb_collections():
    """Verify the 3 real collections from database.py exist."""
    try:
        db = get_mongo_db()
        existing = db.list_collection_names()
        # These are the EXACT names created by database._ensure_indexes()
        required = [
            COLLECTION_DOWNLOAD_HISTORY,
            COLLECTION_MUSICBRAINZ_CACHE,
            COLLECTION_TAGGING_FAILURES,
        ]
        for col_name in required:
            found = col_name in existing
            count = db[col_name].count_documents({}) if found else 0
            log_result(
                f"MongoDB Collection: {col_name}",
                found,
                f"{count} documents" if found else "NOT FOUND (will be auto-created on first write)",
            )
    except Exception as e:
        log_result("MongoDB Collections", False, str(e))


def test_mongodb_indexes():
    """Verify indexes created by database._ensure_indexes()."""
    try:
        db = get_mongo_db()
        # download_history should have idx_downloaded_at, idx_filename
        dh_indexes = [idx["name"] for idx in db[COLLECTION_DOWNLOAD_HISTORY].list_indexes()]
        has_dl_idx = "idx_downloaded_at" in dh_indexes and "idx_filename" in dh_indexes
        log_result("MongoDB Indexes: download_history", has_dl_idx, str(dh_indexes))

        # musicbrainz_cache should have idx_track_id (unique), idx_cache_ttl
        mc_indexes = [idx["name"] for idx in db[COLLECTION_MUSICBRAINZ_CACHE].list_indexes()]
        has_mc_idx = "idx_track_id" in mc_indexes
        log_result("MongoDB Indexes: musicbrainz_cache", has_mc_idx, str(mc_indexes))

        # tagging_failures should have idx_failure_time
        tf_indexes = [idx["name"] for idx in db[COLLECTION_TAGGING_FAILURES].list_indexes()]
        has_tf_idx = "idx_failure_time" in tf_indexes
        log_result("MongoDB Indexes: tagging_failures", has_tf_idx, str(tf_indexes))
    except Exception as e:
        log_result("MongoDB Indexes", False, str(e))


def test_celery_workers():
    try:
        from celery_app import celery_app, is_redis_available
        if not is_redis_available():
            log_result("Celery Workers", False, "Redis not reachable — Celery disabled")
            return
        inspect = celery_app.control.inspect(timeout=3)
        ping = inspect.ping()
        if ping:
            workers = list(ping.keys())
            log_result("Celery Workers", True, f"{len(workers)} worker(s): {', '.join(workers)}")
        else:
            log_result("Celery Workers", False,
                       "No workers responded. Run: celery -A celery_app worker --pool=solo")
    except Exception as e:
        log_result("Celery Workers", False, str(e))


def test_flask_running():
    code, data = http_get("/api/health")
    if code == 200 and data:
        log_result("Flask Server", True,
                   f"status={data.get('status')}, celery={data.get('celery_available')}")
    elif code is not None:
        log_result("Flask Server", False, f"Status {code}")
    else:
        log_result("Flask Server", False, f"Not reachable: {data}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — Every Flask API Route
# ═══════════════════════════════════════════════════════════════════

def test_api_routes():
    """
    Hit every GET endpoint from app.py.
    POST endpoints that trigger actions are tested separately.
    """
    get_routes = [
        # Core routes
        ("/api/health", 200),
        ("/api/status", 200),
        ("/api/downloads", 200),
        ("/api/files", 200),
        ("/api/auto-status", 200),
        ("/api/queue-status", 200),
        ("/api/api-usage", 200),
        ("/api/ingest-config", 200),
        ("/api/history", 200),
        # MusicBrainz routes
        ("/api/library/retag/status", 200),
        # Analytics routes (from analytics_service.py)
        ("/api/analytics/overview", 200),
        ("/api/analytics/downloads-per-day", 200),
        ("/api/analytics/downloads-per-day?days=7", 200),
        ("/api/analytics/top-artists", 200),
        ("/api/analytics/top-artists?limit=5", 200),
        ("/api/analytics/source-breakdown", 200),
        ("/api/analytics/tagging-breakdown", 200),
        ("/api/analytics/recent", 200),
        ("/api/analytics/failed", 200),
    ]

    for path, expected_code in get_routes:
        code, data = http_get(path)
        passed = code == expected_code
        log_result(f"GET {path}", passed, f"Status: {code}")


def test_api_validation():
    """Test that endpoints reject bad input correctly."""
    # POST /api/track with no body → 400
    code, data = http_post("/api/track", {})
    log_result("POST /api/track (no url)", code == 400, f"Status: {code}")

    # POST /api/track with invalid URL → 400
    code, data = http_post("/api/track", {"url": "https://example.com/nope"})
    log_result("POST /api/track (bad url)", code == 400, f"Status: {code}")

    # POST /api/download with no url → 400
    code, data = http_post("/api/download", {})
    log_result("POST /api/download (no url)", code == 400, f"Status: {code}")

    # POST /api/download_playlist with empty tracks → 400
    code, data = http_post("/api/download_playlist", {"tracks": []})
    log_result("POST /api/download_playlist (empty)", code == 400, f"Status: {code}")

    # GET non-existent endpoint → 404
    code, _ = http_get("/api/does-not-exist")
    log_result("GET /api/does-not-exist", code == 404, f"Status: {code}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — Spotify Metadata Fetch
# ═══════════════════════════════════════════════════════════════════

def test_spotify_track_fetch():
    """POST /api/track — single track metadata (exact route from app.py)."""
    code, data = http_post("/api/track", {"url": TEST_TRACK_URL})
    if code == 200 and data:
        has_fields = all(k in data for k in ("type", "title", "artist"))
        log_result("Spotify Track Fetch", has_fields,
                   f"type={data.get('type')} title={data.get('title')!r} artist={data.get('artist')!r}")
    elif code == 429:
        log_result("Spotify Track Fetch", False, "Rate limited — try again later")
    else:
        msg = data.get("error", "") if isinstance(data, dict) else str(data)
        log_result("Spotify Track Fetch", False, f"Status: {code} | {msg[:120]}")


def test_spotify_playlist_fetch():
    """POST /api/track — playlist URL returns type='album' with tracks array."""
    code, data = http_post("/api/track", {"url": TEST_PLAYLIST_URL})
    if code == 200 and data:
        is_album_type = data.get("type") == "album"
        track_count = data.get("total_tracks", 0)
        log_result("Spotify Playlist Fetch", is_album_type and track_count > 0,
                   f"name={data.get('name')!r} total_tracks={track_count}")
    elif code == 429:
        log_result("Spotify Playlist Fetch", False, "Rate limited")
    else:
        msg = data.get("error", "") if isinstance(data, dict) else str(data)
        log_result("Spotify Playlist Fetch", False, f"Status: {code} | {msg[:120]}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 4 — Socket.IO Connection
# ═══════════════════════════════════════════════════════════════════

def test_socketio_connection():
    """
    Connect, verify we receive the initial events that app.py emits
    on 'connect': status_update, files_list, queue_status.
    """
    sio = socketio.SimpleClient()
    received_events = set()

    try:
        sio.connect(BASE_URL, wait_timeout=10,
                     transports=["websocket", "polling"])
        log_result("Socket.IO Connect", True, f"SID: {sio.sid}")

        # Collect events for up to 5 seconds
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                event = sio.receive(timeout=2)
                if event:
                    received_events.add(event[0])
            except Exception:
                break

        # Expected events emitted on connect by app.py:
        # handle_connect() emits: status_update, files_list, queue_status
        # _auto_status_emitter() may also emit status_update, queue_status
        expected = {"status_update", "files_list", "queue_status"}
        got = expected & received_events
        log_result("Socket.IO Initial Events", len(got) >= 2,
                   f"Expected {expected}, got {received_events}")

        sio.disconnect()
        log_result("Socket.IO Disconnect", True, "Clean")
    except Exception as e:
        log_result("Socket.IO Connect", False, str(e))


def test_socketio_keepalive():
    """Test the ping_keepalive / pong_keepalive handshake from app.py."""
    sio = socketio.SimpleClient()
    try:
        sio.connect(BASE_URL, wait_timeout=10,
                     transports=["websocket", "polling"])
        # Drain initial events
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                sio.receive(timeout=1)
            except Exception:
                break

        sio.emit("ping_keepalive")
        got_pong = False
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                event = sio.receive(timeout=2)
                if event and event[0] == "pong_keepalive":
                    got_pong = True
                    break
            except Exception:
                break

        log_result("Socket.IO Keepalive", got_pong,
                   "pong_keepalive received" if got_pong else "No pong")
        sio.disconnect()
    except Exception as e:
        log_result("Socket.IO Keepalive", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# SECTION 5 — Full Download Pipeline (single track)
# ═══════════════════════════════════════════════════════════════════

def test_full_download():
    """
    POST /api/download → expects 202 Accepted.
    Then polls GET /api/status + listens on Socket.IO for status_update
    events until download_status.status becomes 'completed' or 'failed'.
    Also listens for quality_report and tagging_complete events emitted
    by downloader_service.py.
    """
    sio = socketio.SimpleClient()
    quality_report_received = False
    tagging_complete_received = False
    final_status = None
    all_events = []

    try:
        sio.connect(BASE_URL, wait_timeout=10,
                     transports=["websocket", "polling"])
        # Drain initial burst
        drain_deadline = time.time() + 2
        while time.time() < drain_deadline:
            try:
                sio.receive(timeout=1)
            except Exception:
                break

        # Trigger download — POST /api/download (exact route from app.py)
        code, data = http_post("/api/download", {"url": TEST_TRACK_URL})
        if code == 429:
            log_result("Download Trigger", False,
                       "Server busy — another download running. Wait and retry.")
            sio.disconnect()
            return
        passed = code == 202 and data and data.get("status") == "started"
        log_result("Download Trigger", passed, f"Status: {code} | {data}")
        if not passed:
            sio.disconnect()
            return

        # Listen for events (max 180s for slow connections)
        start = time.time()
        while time.time() - start < 180:
            try:
                event = sio.receive(timeout=5)
                if not event:
                    continue
                event_name = event[0]
                event_data = event[1] if len(event) > 1 else {}
                all_events.append(event_name)

                # quality_report emitted by downloader_service._emit_quality_report()
                if event_name == "quality_report":
                    quality_report_received = True
                    source = event_data.get("source_platform", "?")
                    print(f"    \U0001f4e1 quality_report  source={source}")

                # tagging_complete emitted by downloader_service after _tag_file()
                if event_name == "tagging_complete":
                    tagging_complete_received = True
                    src = event_data.get("report", {}).get("source", "?")
                    print(f"    \U0001f4e1 tagging_complete  source={src}")

                # status_update carries download.status
                if event_name == "status_update" and isinstance(event_data, dict):
                    dl = event_data.get("download", {})
                    st = dl.get("status", "")
                    pct = dl.get("progress", 0)
                    cur = dl.get("current", "")
                    if st in ("completed", "failed", "fallback"):
                        final_status = st
                        print(f"    \U0001f3af status_update  status={st}  progress={pct}")
                    elif st == "downloading" and pct > 0:
                        print(f"    \u23f3 downloading {pct}%  {cur[:60]}")

                if final_status:
                    # Give 3 more seconds for quality_report / tagging_complete
                    extra_deadline = time.time() + 4
                    while time.time() < extra_deadline:
                        try:
                            ev = sio.receive(timeout=1)
                            if ev:
                                all_events.append(ev[0])
                                if ev[0] == "quality_report":
                                    quality_report_received = True
                                if ev[0] == "tagging_complete":
                                    tagging_complete_received = True
                        except Exception:
                            break
                    break

            except Exception:
                continue

        sio.disconnect()

        # Summarize
        unique_events = sorted(set(all_events))
        log_result("Download Completed", final_status in ("completed", "fallback"),
                   f"final_status={final_status}")
        log_result("Quality Report Event", quality_report_received,
                   "quality_report received" if quality_report_received
                   else "Not received (may be OK if emission was throttled)")
        log_result("Tagging Complete Event", tagging_complete_received,
                   "tagging_complete received" if tagging_complete_received
                   else "Not received (tagger may not be installed)")
        log_result("Socket.IO Events Observed", True,
                   ", ".join(unique_events))

    except Exception as e:
        log_result("Full Download Pipeline", False, str(e))
        try:
            sio.disconnect()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# SECTION 6 — MongoDB Storage Verification
# ═══════════════════════════════════════════════════════════════════

def test_mongodb_download_storage():
    """
    Verify the most recent document in download_history has the
    expected fields from database.save_download_report().
    """
    try:
        db = get_mongo_db()
        col = db[COLLECTION_DOWNLOAD_HISTORY]
        latest = col.find_one(sort=[("downloaded_at", -1)])
        if not latest:
            log_result("Download Stored in MongoDB", False,
                       "No documents in download_history — download something first")
            return

        title = latest.get("track_title", "?")
        artist = latest.get("artist", "?")
        log_result("Download Stored in MongoDB", True,
                   f"Latest: '{title}' by '{artist}'")

        # Fields from database.save_download_report()
        expected_fields = [
            "track_title", "artist", "album", "filename",
            "downloaded_at", "source_platform",
        ]
        missing = [f for f in expected_fields if f not in latest]
        log_result("Document Schema Valid", len(missing) == 0,
                   f"Missing fields: {missing}" if missing else "All core fields present")

        # Check tagging_report sub-document
        has_tagging = "tagging_report" in latest and latest["tagging_report"] is not None
        source = latest.get("tagging_report", {}).get("source", "none") if has_tagging else "none"
        log_result("Tagging Report Stored", has_tagging,
                   f"source={source}")

    except Exception as e:
        log_result("MongoDB Storage Check", False, str(e))


def test_mongodb_failures():
    """Check tagging_failures collection structure."""
    try:
        db = get_mongo_db()
        col = db[COLLECTION_TAGGING_FAILURES]
        count = col.count_documents({})
        log_result("Tagging Failures Collection", True, f"{count} entries")

        if count > 0:
            latest = col.find_one(sort=[("timestamp", -1)])
            expected = ["track_id", "title", "artist", "error", "timestamp"]
            missing = [f for f in expected if f not in latest]
            log_result("Failure Document Schema", len(missing) == 0,
                       f"Missing: {missing}" if missing else "All fields present")
    except Exception as e:
        log_result("Tagging Failures", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# SECTION 7 — MP3 File Tags (Mutagen)
# ═══════════════════════════════════════════════════════════════════

def test_mp3_tags():
    """
    Find the most recently downloaded MP3 via download_history.filename,
    locate it on disk, and verify ID3 tags written by tagger_service.
    """
    try:
        db = get_mongo_db()
        latest = db[COLLECTION_DOWNLOAD_HISTORY].find_one(sort=[("downloaded_at", -1)])
        if not latest:
            log_result("MP3 Tags Check", False, "No download_history entries")
            return

        filename = latest.get("filename", "")
        if not filename:
            log_result("MP3 Tags Check", False, "No filename in latest document")
            return

        # Search for the file in the download tree
        from config import config as _cfg
        base_dir = _cfg.BASE_DOWNLOAD_DIR
        found_path = None
        if os.path.isdir(base_dir):
            for root, _dirs, files in os.walk(base_dir):
                if filename in files:
                    found_path = os.path.join(root, filename)
                    break

        if not found_path:
            log_result("MP3 File on Disk", False,
                       f"'{filename}' not found under {base_dir}")
            return

        log_result("MP3 File on Disk", True, found_path)

        from mutagen.id3 import ID3
        tags = ID3(found_path)

        # Core tags written by tagger_service.tag_file()
        required_tags = {
            "TIT2": "Title",
            "TPE1": "Artist",
            "TALB": "Album",
        }
        # Tags that may be present depending on MusicBrainz match
        optional_tags = {
            "TDRC": "Year",
            "TBPM": "BPM",
            "TKEY": "Key",
            "TSRC": "ISRC",
            "TCON": "Genre",
        }

        for tag_key, tag_name in required_tags.items():
            present = any(k.startswith(tag_key) for k in tags.keys())
            value = str(tags.get(tag_key, ""))[:60] if present else ""
            log_result(f"ID3 Tag: {tag_name}", present, value)

        # Album art (APIC frame)
        has_apic = any(k.startswith("APIC") for k in tags.keys())
        log_result("ID3 Tag: Album Art", has_apic,
                   "APIC frame present" if has_apic else "No album art embedded")

        for tag_key, tag_name in optional_tags.items():
            present = tag_key in tags
            value = str(tags.get(tag_key, ""))[:60] if present else "not set"
            # Optional tags are informational, always pass
            log_result(f"ID3 Tag: {tag_name} (optional)", present, value)

    except ImportError:
        log_result("MP3 Tags Check", False, "mutagen not installed: pip install mutagen")
    except Exception as e:
        log_result("MP3 Tags Check", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# SECTION 8 — MusicBrainz Cache
# ═══════════════════════════════════════════════════════════════════

def test_musicbrainz_cache():
    try:
        db = get_mongo_db()
        col = db[COLLECTION_MUSICBRAINZ_CACHE]
        count = col.count_documents({})
        log_result("MusicBrainz Cache", True, f"{count} cached entries")

        if count > 0:
            latest = col.find_one(sort=[("cached_at", -1)])
            # Fields from database.set_cached_mb(): track_id, mb_data, cached_at
            has_structure = (
                "track_id" in latest
                and "mb_data" in latest
                and "cached_at" in latest
            )
            log_result("Cache Document Schema", has_structure,
                       f"track_id={latest.get('track_id', '?')[:20]}")
    except Exception as e:
        log_result("MusicBrainz Cache", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# SECTION 9 — Analytics Endpoints (deep validation)
# ═══════════════════════════════════════════════════════════════════

def test_analytics_overview():
    code, data = http_get("/api/analytics/overview")
    if code == 200 and isinstance(data, dict):
        expected_keys = [
            "total_downloads", "success_rate", "total_storage_mb",
            "total_artists", "failed_downloads", "musicbrainz_cached",
        ]
        missing = [k for k in expected_keys if k not in data]
        log_result("Analytics Overview Schema", len(missing) == 0,
                   f"Missing: {missing}" if missing else
                   f"downloads={data['total_downloads']} artists={data['total_artists']} "
                   f"rate={data['success_rate']}% mb_cached={data['musicbrainz_cached']}")
    else:
        log_result("Analytics Overview", False, f"Status: {code}")


def test_analytics_downloads_per_day():
    code, data = http_get("/api/analytics/downloads-per-day?days=30")
    if code == 200 and isinstance(data, list):
        valid = all("date" in r and "count" in r for r in data) if data else True
        log_result("Analytics Downloads/Day", valid,
                   f"{len(data)} day(s) returned, schema OK" if valid
                   else "Invalid entry structure")
    else:
        log_result("Analytics Downloads/Day", False, f"Status: {code}")


def test_analytics_top_artists():
    code, data = http_get("/api/analytics/top-artists?limit=5")
    if code == 200 and isinstance(data, list):
        valid = all("artist" in r and "count" in r for r in data) if data else True
        top = data[0]["artist"] if data else "(none)"
        log_result("Analytics Top Artists", valid,
                   f"#1: {top} ({data[0]['count']} dl)" if data else "No data")
    else:
        log_result("Analytics Top Artists", False, f"Status: {code}")


def test_analytics_source_breakdown():
    code, data = http_get("/api/analytics/source-breakdown")
    if code == 200 and isinstance(data, list):
        valid = all("platform" in r and "count" in r and "percentage" in r for r in data) if data else True
        summary = ", ".join(f"{r['platform']}={r['count']}" for r in data) if data else "(empty)"
        log_result("Analytics Source Breakdown", valid, summary)
    else:
        log_result("Analytics Source Breakdown", False, f"Status: {code}")


def test_analytics_tagging_breakdown():
    code, data = http_get("/api/analytics/tagging-breakdown")
    if code == 200 and isinstance(data, list):
        # Expected 3 entries: MusicBrainz, Spotify Fallback, Untagged
        sources = [r["source"] for r in data]
        has_all = "MusicBrainz" in sources and "Spotify Fallback" in sources and "Untagged" in sources
        summary = ", ".join(f"{r['source']}={r['count']}" for r in data)
        log_result("Analytics Tagging Breakdown", has_all, summary)
    else:
        log_result("Analytics Tagging Breakdown", False, f"Status: {code}")


def test_analytics_recent():
    code, data = http_get("/api/analytics/recent")
    if code == 200 and isinstance(data, list):
        count = len(data)
        log_result("Analytics Recent Downloads", True,
                   f"{count} entries returned")
        if data:
            # Verify first entry has expected fields from analytics_service.get_recent_downloads()
            entry = data[0]
            expected = ["_id", "track_title", "artist", "downloaded_at"]
            missing = [k for k in expected if k not in entry]
            log_result("Recent Entry Schema", len(missing) == 0,
                       f"Missing: {missing}" if missing else
                       f"title={entry.get('track_title', '?')!r}")
    else:
        log_result("Analytics Recent", False, f"Status: {code}")


def test_analytics_failed():
    code, data = http_get("/api/analytics/failed")
    if code == 200 and isinstance(data, list):
        log_result("Analytics Failed Downloads", True, f"{len(data)} entries")
        if data:
            entry = data[0]
            expected = ["_id", "title", "artist", "error", "timestamp"]
            missing = [k for k in expected if k not in entry]
            log_result("Failed Entry Schema", len(missing) == 0,
                       f"Missing: {missing}" if missing else
                       f"title={entry.get('title', '?')!r}")
    else:
        log_result("Analytics Failed", False, f"Status: {code}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 10 — Cross-layer Consistency
# ═══════════════════════════════════════════════════════════════════

def test_cross_layer_consistency():
    """
    Verify that analytics overview counts match direct MongoDB queries.
    """
    try:
        code, overview = http_get("/api/analytics/overview")
        if code != 200 or not isinstance(overview, dict):
            log_result("Cross-layer Consistency", False, f"Overview API returned {code}")
            return

        db = get_mongo_db()
        actual_dl = db[COLLECTION_DOWNLOAD_HISTORY].count_documents({})
        actual_fail = db[COLLECTION_TAGGING_FAILURES].count_documents({})
        actual_mb = db[COLLECTION_MUSICBRAINZ_CACHE].count_documents({})

        dl_match = overview["total_downloads"] == actual_dl
        fail_match = overview["failed_downloads"] == actual_fail
        mb_match = overview["musicbrainz_cached"] == actual_mb

        all_match = dl_match and fail_match and mb_match
        detail = (
            f"downloads: API={overview['total_downloads']} DB={actual_dl} {'OK' if dl_match else 'MISMATCH'} | "
            f"failures: API={overview['failed_downloads']} DB={actual_fail} {'OK' if fail_match else 'MISMATCH'} | "
            f"mb_cache: API={overview['musicbrainz_cached']} DB={actual_mb} {'OK' if mb_match else 'MISMATCH'}"
        )
        log_result("Analytics \u2194 MongoDB Consistency", all_match, detail)
    except Exception as e:
        log_result("Cross-layer Consistency", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════════════════

def print_summary():
    passed = [r for r in RESULTS if r["passed"]]
    failed = [r for r in RESULTS if not r["passed"]]
    total = len(RESULTS)
    pct = round(len(passed) / total * 100) if total > 0 else 0

    print()
    print("=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)
    print(f"  Total:  {total}")
    print(f"  Passed: {len(passed)} \u2705")
    print(f"  Failed: {len(failed)} \u274c")
    print(f"  Score:  {pct}%")

    if failed:
        print()
        print("  FAILED TESTS:")
        for r in failed:
            print(f"    \u274c {r['test']}")
            if r["message"]:
                print(f"       {r['message']}")

    print()
    print("=" * 60)
    if len(failed) == 0:
        print("  \U0001f389 ALL TESTS PASSED \u2014 Pipeline is fully working!")
    elif len(failed) <= 3:
        print("  \u26a0\ufe0f  Almost there \u2014 fix the failures above")
    else:
        print("  \U0001f527 Several issues found \u2014 see failures above")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  SpotifyDL \u2014 Full Pipeline Integration Test")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Target: {BASE_URL}")
    print("=" * 60)

    # ── Section 1: Infrastructure ─────────────────────────────────
    print("\n\u250c\u2500\u2500 1. INFRASTRUCTURE \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_flask_running()
    test_redis_connection()
    test_mongodb_connection()
    test_mongodb_collections()
    test_mongodb_indexes()
    test_celery_workers()

    # Quick-fail: if Flask isn't running, skip everything else
    flask_ok = any(r["test"] == "Flask Server" and r["passed"] for r in RESULTS)
    if not flask_ok:
        print("\n  \u26d4 Flask server not running. Start it first: cd backend && python app.py")
        print_summary()
        return

    # ── Section 2: API Routes ─────────────────────────────────────
    print("\n\u250c\u2500\u2500 2. API ROUTES \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_api_routes()
    test_api_validation()

    # ── Section 3: Spotify Fetch ──────────────────────────────────
    print("\n\u250c\u2500\u2500 3. SPOTIFY METADATA \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_spotify_track_fetch()
    test_spotify_playlist_fetch()

    # ── Section 4: Socket.IO ──────────────────────────────────────
    print("\n\u250c\u2500\u2500 4. SOCKET.IO \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_socketio_connection()
    test_socketio_keepalive()

    # ── Section 5: Full Download ──────────────────────────────────
    print("\n\u250c\u2500\u2500 5. FULL DOWNLOAD PIPELINE \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    print("  \u26a0\ufe0f  This will trigger a REAL download (30\u2013120 seconds)")
    skip_download = False
    try:
        confirm = input("  Run full download test? (y/n): ").strip().lower()
        if confirm != "y":
            print("  Skipped full download test")
            skip_download = True
    except EOFError:
        # Non-interactive mode — skip download
        print("  Non-interactive mode — skipping download test")
        skip_download = True

    if not skip_download:
        test_full_download()

    # ── Section 6: MongoDB Storage ────────────────────────────────
    print("\n\u250c\u2500\u2500 6. MONGODB STORAGE \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_mongodb_download_storage()
    test_mongodb_failures()

    # ── Section 7: MP3 Tags ───────────────────────────────────────
    print("\n\u250c\u2500\u2500 7. MP3 ID3 TAGS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_mp3_tags()

    # ── Section 8: MusicBrainz Cache ──────────────────────────────
    print("\n\u250c\u2500\u2500 8. MUSICBRAINZ CACHE \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_musicbrainz_cache()

    # ── Section 9: Analytics ──────────────────────────────────────
    print("\n\u250c\u2500\u2500 9. ANALYTICS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_analytics_overview()
    test_analytics_downloads_per_day()
    test_analytics_top_artists()
    test_analytics_source_breakdown()
    test_analytics_tagging_breakdown()
    test_analytics_recent()
    test_analytics_failed()

    # ── Section 10: Cross-layer ───────────────────────────────────
    print("\n\u250c\u2500\u2500 10. CROSS-LAYER CONSISTENCY \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
    test_cross_layer_consistency()

    # ── Summary ───────────────────────────────────────────────────
    print_summary()


if __name__ == "__main__":
    main()
