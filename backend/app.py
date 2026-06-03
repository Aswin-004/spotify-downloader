"""
Spotify Meta Downloader - Flask Backend Application
Main application entry point
"""
# GEVENT PATCH — must be the very first executable statement before any other
# imports.  When SOCKETIO_ASYNC_MODE=gevent (production), Gunicorn's gevent
# worker calls monkey.patch_all() AFTER the master process has already
# imported ssl/pymongo, which causes RecursionError on TLS connections.
# Patching here ensures ssl is monkey-patched before pymongo ever imports it.
import os as _os
if _os.getenv("SOCKETIO_ASYNC_MODE", "threading") == "gevent":
    try:
        from gevent import monkey as _monkey
        _monkey.patch_all(thread=False)  # thread=False avoids breaking threading.Lock
    except ImportError:
        pass

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import json
import logging
import os
import re as _re
import subprocess
import sys as _sys
import threading
import time
from pathlib import Path
from config import config
import settings_store as _settings_store
_settings_store.apply_to_config()   # apply user_config.json overrides before any service imports
from services.spotify_service import get_spotify_service
from services.downloader_service import get_downloader_service, sanitize_filename
from services.downloader_service import download_queue_status, update_queue, set_manual_active
from services.downloader_service import set_socketio as set_downloader_socketio
from services.auto_downloader import AUTO_STATUS, BASE_DOWNLOAD_DIR, INGEST_PLAYLIST_ID, set_socketio
from services.auto_downloader import manual_refresh as _manual_refresh
from services.spotify_service import get_api_usage
from services.analytics_service import (  # ANALYTICS
    get_overview_stats, get_downloads_per_day,  # ANALYTICS
    get_top_artists, get_source_breakdown,  # ANALYTICS
    get_tagging_breakdown, get_recent_downloads,  # ANALYTICS
    get_failed_downloads, get_cache_analytics,  # ANALYTICS
    get_tagging_failure_summary, get_weekly_download_stats,  # ANALYTICS
)  # ANALYTICS
from utils import setup_logging, extract_spotify_id

# FILE ORGANIZER — Import library blueprint
from routes import library_bp

# MUSICBRAINZ — import tagger service
try:  # MUSICBRAINZ
    from services.tagger_service import tag_file as tagger_tag_file  # MUSICBRAINZ
    _tagger_available = True  # MUSICBRAINZ
except ImportError as _tag_err:  # MUSICBRAINZ
    _tagger_available = False  # MUSICBRAINZ
    logging.getLogger(__name__).warning(f"Tagger service not available: {_tag_err}")  # MUSICBRAINZ

# RATE LIMITING — Flask-Limiter with Redis storage
from rate_limiter import (
    limiter,
    rate_limit_exceeded_handler,
    check_queue_overload,
    LIMIT_DOWNLOAD,
    LIMIT_PLAYLIST,
    LIMIT_MAINTENANCE,
    LIMIT_METADATA,
    LIMIT_READS,
)

# QUEUE MANAGER — dedup, stuck-task cleanup, SocketIO bridge
from queue_manager import (
    is_duplicate_task,
    claim_task_slot,
    register_task,
    deregister_task,
    get_queue_depth,
    start_stuck_task_cleanup_thread,
    start_socketio_bridge,
)

# INVIDIOUS — streaming download service (YouTube proxy for cloud users)
from services.invidious_service import get_invidious_url

# CELERY UPGRADE — conditional Celery imports (graceful if Redis unavailable)
_celery_available = False
_celery_app = None
try:
    from celery_app import is_redis_available
    if is_redis_available():
        from tasks import download_track_task
        from celery_app import celery_app as _celery_app
        _celery_available = True
        logging.getLogger(__name__).info("Celery + Redis detected — task queue enabled")
    else:
        logging.getLogger(__name__).info("Redis not reachable — falling back to threading")
except ImportError:
    logging.getLogger(__name__).info("Celery not installed — falling back to threading")
except Exception as _celery_err:
    logging.getLogger(__name__).warning(f"Celery init error: {_celery_err} — falling back to threading")

# ── Loguru: structured logging ────────────────────────────────────────────────
# In production (Render/cloud) log to stdout so the platform captures it.
# In development keep the rotating file log as well.
try:
    import sys as _sys_log
    from loguru import logger

    _IS_PRODUCTION = os.getenv("FLASK_ENV", "production") == "production"

    if not _IS_PRODUCTION:
        # Dev: also write to a rotating file
        _log_dir = Path(__file__).parent / "logs"
        _log_dir.mkdir(exist_ok=True)
        logger.add(
            str(_log_dir / "app.log"),
            rotation="5 MB",
            retention="7 days",
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name} | {message}",
        )
    else:
        # Production: stdout only (Loguru's default sink is stderr — add stdout)
        logger.remove()  # remove default stderr sink
        logger.add(
            _sys_log.stdout,
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name} | {message}",
            colorize=False,
        )
except ImportError:
    logger = setup_logging(__name__, level=logging.INFO)  # type: ignore[assignment]

# Process start time — used by /api/health to report uptime
_START_TIME = time.time()

# Download status tracking
download_status = {
    "status": "idle",
    "progress": 0,
    "current": ""
}
active_download = False
status_lock = threading.Lock()  # Prevent race conditions

# DISCONNECT FIX: per-track dedup guard to prevent duplicate simultaneous downloads
_active_downloads = {}
_active_downloads_lock = threading.Lock()

# Download history (last 100 entries)
download_history = []
history_lock = threading.Lock()
MAX_HISTORY = 100

# Lock protecting concurrent writes to user_config.json
_user_config_lock = threading.Lock()


def load_existing_files():
    """Scan BASE_DOWNLOAD_DIR recursively for all .mp3 files."""
    files = []
    if not os.path.isdir(BASE_DOWNLOAD_DIR):
        return files
    for root, _dirs, filenames in os.walk(BASE_DOWNLOAD_DIR):
        for fname in filenames:
            if fname.lower().endswith(".mp3"):
                full = os.path.join(root, fname)
                rel_folder = os.path.relpath(root, BASE_DOWNLOAD_DIR)
                if rel_folder == ".":
                    rel_folder = ""
                files.append({
                    "name": fname,
                    "folder": rel_folder,
                    "path": full,
                    "mtime": os.path.getmtime(full)
                })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def seed_history_from_disk():
    """Populate download_history from existing files on startup."""
    existing = load_existing_files()
    with history_lock:
        for f in existing[:MAX_HISTORY]:
            name = f["name"]
            title = name[:-4] if name.lower().endswith(".mp3") else name
            download_history.append({
                "title": title,
                "artist": f["folder"] or "Library",
                "status": "success",
                "filename": name,
                "timestamp": time.strftime("%Y-%m-%d", time.localtime(f["mtime"]))
            })
    logger.info(f"Seeded history with {len(download_history)} existing files")

def add_history_entry(title, artist, status, filename="", error=""):
    """Add an entry to download history and emit via WebSocket"""
    entry = {
        "title": title,
        "artist": artist,
        "status": status,
        "filename": filename,
        "timestamp": time.strftime("%H:%M:%S"),
        "error": error or "",
    }
    with history_lock:
        download_history.insert(0, entry)
        if len(download_history) > MAX_HISTORY:
            download_history.pop()
    emit_status()

def emit_status():
    """Emit current status to all connected WebSocket clients"""
    with status_lock:
        status_data = dict(download_status)
    with history_lock:
        history_data = list(download_history[:50])
    try:
        socketio.emit("status_update", {
            "download": status_data,
            "auto": dict(AUTO_STATUS),
            "history": history_data
        })
    except Exception:
        pass  # ignore emit errors during startup

# Create Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['SECRET_KEY'] = config.SECRET_KEY

# RATE LIMITING — attach limiter to app + register 429 handler
limiter.init_app(app)
app.register_error_handler(429, rate_limit_exceeded_handler)

# CORS origins — read from config (env-driven in production)
_ALLOWED_ORIGINS = config.ALLOWED_ORIGINS

# WebSocket upgrade guard: MUST be explicitly enabled in production.
# Defaults to False (safe for Werkzeug dev server).
# In production (Gunicorn + gevent worker): set SOCKETIO_ALLOW_UPGRADES=true
_ALLOW_WS_UPGRADES = os.getenv("SOCKETIO_ALLOW_UPGRADES", "false").lower() in ("1", "true", "yes")

# SocketIO with CORS — async_mode set by env (gevent in production, threading locally)
_SOCKETIO_ASYNC_MODE = os.getenv("SOCKETIO_ASYNC_MODE", "threading")

socketio = SocketIO(
    app,
    async_mode=_SOCKETIO_ASYNC_MODE,
    cors_allowed_origins=_ALLOWED_ORIGINS,
    ping_timeout=300,
    ping_interval=10,
    max_http_buffer_size=1e8,
    logger=False,
    engineio_logger=False,
    allow_upgrades=_ALLOW_WS_UPGRADES,
)
set_socketio(socketio)
set_downloader_socketio(socketio)  # quality_report events

# QUEUE MANAGER — start background threads after socketio is ready
start_socketio_bridge(socketio)   # forward Celery→Redis→SocketIO events
start_stuck_task_cleanup_thread() # revoke tasks stuck > MAX_TASK_AGE

# Enable CORS for all API routes
CORS(app, resources={
    r"/api/*": {
        "origins": _ALLOWED_ORIGINS,
        "methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        "supports_credentials": False
    }
})

# FILE ORGANIZER — Register library blueprint
app.register_blueprint(library_bp)

# Get services
spotify_service = get_spotify_service()
downloader_service = get_downloader_service()

@socketio.on('connect')
def handle_connect():
    """Send current state to newly connected client"""
    emit_status()
    emit("files_list", load_existing_files())
    emit("queue_status", download_queue_status)

@socketio.on('request_status')
def handle_request_status():
    """Client explicitly requests current status"""
    emit_status()

@socketio.on('ping_keepalive')  # DISCONNECT FIX: keepalive handler
def handle_keepalive():
    """Respond to frontend keepalive ping to prevent timeout"""
    emit('pong_keepalive', {'status': 'alive'})  # DISCONNECT FIX

# Background task: lightweight 1s heartbeat so the UI stays responsive.
# The full emit_status() bundle still fires on discrete events (download
# complete, history update, etc.) — this tick just keeps the auto-status
# and queue panels current without waiting for the next real event.
def _auto_status_emitter():
    """Emit auto and queue status every 1 second (was 5s)."""
    while True:
        time.sleep(1)
        try:
            socketio.emit("auto_status", dict(AUTO_STATUS))
            socketio.emit("queue_status", download_queue_status)
        except Exception:
            pass

socketio.start_background_task(target=_auto_status_emitter)


@app.route('/', methods=['GET'])
def index():
    """Serve the frontend"""
    return send_from_directory('../frontend-react/dist', 'index.html')


@app.route('/api/maintenance/status', methods=['GET'])
def maintenance_status():
    return jsonify({"running": _maintenance_running, "task": _maintenance_active_task})


@app.route('/api/maintenance/run', methods=['POST'])
@limiter.limit(LIMIT_MAINTENANCE)
def maintenance_run():
    global _maintenance_running
    data   = request.get_json() or {}
    task   = (data.get("task") or "").strip()
    dry    = bool(data.get("dry_run", False))
    passes = data.get("passes", [1, 2, 3, 4])
    limit  = int(data.get("limit") or 0)

    if task not in _MAINTENANCE_SCRIPTS:
        return jsonify({"error": "unknown task"}), 400

    with _maintenance_lock:
        if _maintenance_running:
            return jsonify({"error": "A maintenance task is already running"}), 409
        _maintenance_running = True
        global _maintenance_active_task
        _maintenance_active_task = task

    script = _MAINTENANCE_SCRIPTS[task]
    backend_dir = os.path.dirname(os.path.abspath(__file__))

    def _emit(line, done=False, exit_code=None):
        payload = {"task": task, "line": line, "done": done}
        if exit_code is not None:
            payload["exit_code"] = exit_code
        socketio.emit("maintenance_log", payload)

    def run_task():
        global _maintenance_running, _maintenance_active_task
        args = [_sys.executable, script]
        if dry:
            args.append("--dry-run")
        if task == "backfill_gemini":
            for p in passes:
                args.append(f"--pass{p}")
            if limit:
                args += ["--limit", str(limit)]
        if task == "backfill_lastfm":
            if limit:
                args += ["--limit", str(limit)]

        try:
            proc = subprocess.Popen(
                args,
                cwd=backend_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            _emit(f"▶ Started: {' '.join(args[1:])}")
            for raw in proc.stdout:
                line = _strip_line(raw)
                if line:
                    _emit(line)
            try:
                proc.wait(timeout=3600)  # 1-hour hard limit
            except subprocess.TimeoutExpired:
                proc.kill()
                _emit("✗ Task killed — exceeded 1-hour time limit", done=True, exit_code=1)
                return
            _emit(f"{'✓ Done' if proc.returncode == 0 else '✗ Failed'} (exit {proc.returncode})",
                  done=True, exit_code=proc.returncode)
        except Exception as exc:
            _emit(f"Error: {exc}", done=True, exit_code=1)
        finally:
            with _maintenance_lock:
                _maintenance_running = False
                _maintenance_active_task = None

    threading.Thread(target=run_task, daemon=True).start()
    return jsonify({"started": True, "task": task}), 202


# ─── Settings: App Configuration (all user-configurable settings) ────────────

@app.route('/api/settings/app-config', methods=['GET'])
def settings_get_app_config():
    """Return current app configuration (secrets masked)."""
    try:
        return jsonify(_settings_store.for_frontend()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/app-config', methods=['POST'])
def settings_save_app_config():
    """Save app configuration and apply immediately (no restart needed)."""
    data = request.get_json(silent=True) or {}
    try:
        updated = _settings_store.save(data)
        _settings_store.apply_to_config()

        # Hot-reload ingest playlist in auto_downloader if it changed
        if "ingest_playlist_id" in data:
            try:
                import services.auto_downloader as _ad
                _ad.INGEST_PLAYLIST_ID = updated.get("ingest_playlist_id", "")
            except Exception:
                pass

        return jsonify({"success": True, "config": _settings_store.for_frontend()}), 200
    except Exception as e:
        logger.error(f"[settings] Failed to save config: {e}")
        return jsonify({"error": str(e)}), 500


# ─── Settings: Custom Folder Mappings ────────────────────────────────────────

@app.route('/api/settings/scan-folders', methods=['GET'])
def settings_scan_folders():
    """Return subdirectory names inside BASE_DOWNLOAD_DIR."""
    try:
        if not BASE_DOWNLOAD_DIR or not os.path.isdir(BASE_DOWNLOAD_DIR):
            return jsonify({"folders": [], "warning": "Music folder not configured or does not exist"}), 200
        entries = [
            e for e in os.listdir(BASE_DOWNLOAD_DIR)
            if os.path.isdir(os.path.join(BASE_DOWNLOAD_DIR, e))
               and not e.startswith('.')
        ]
        return jsonify({"folders": sorted(entries)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/custom-folders', methods=['GET'])
def settings_get_custom_folders():
    """Return all custom folder→genre mappings."""
    try:
        from database import get_custom_folder_mappings_collection
        col = get_custom_folder_mappings_collection()
        docs = list(col.find({}, {"_id": 0}))
        return jsonify({"mappings": docs}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/custom-folders', methods=['POST'])
def settings_save_custom_folder():
    """Upsert a folder→genre mapping."""
    data = request.get_json(silent=True) or {}
    folder_name = (data.get("folder_name") or "").strip()
    genre_label = (data.get("genre_label") or "").strip()
    if not folder_name or not genre_label:
        return jsonify({"error": "folder_name and genre_label are required"}), 400
    try:
        from datetime import datetime
        from database import get_custom_folder_mappings_collection
        col = get_custom_folder_mappings_collection()
        col.update_one(
            {"folder_name": folder_name},
            {"$set": {"folder_name": folder_name, "genre_label": genre_label, "updated_at": datetime.utcnow().isoformat()}},
            upsert=True,
        )
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/custom-folders/<path:folder_name>', methods=['DELETE'])
def settings_delete_custom_folder(folder_name):
    """Delete a custom folder mapping by folder_name."""
    try:
        from database import get_custom_folder_mappings_collection
        col = get_custom_folder_mappings_collection()
        col.delete_one({"folder_name": folder_name})
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/<path:filename>', methods=['GET'])
def serve_frontend(filename):
    """Serve frontend static files (React SPA with client-side routing)"""
    dist_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend-react', 'dist')
    full_path = os.path.join(dist_dir, filename)
    if os.path.isfile(full_path):
        return send_from_directory(dist_dir, filename)
    return send_from_directory(dist_dir, 'index.html')


@app.route('/api/track', methods=['POST'])
@limiter.limit(LIMIT_METADATA)
def get_track_metadata():
    """
    Extract metadata from Spotify URL (track or album)
    
    Request body: { "url": "https://open.spotify.com/track/..." }
    Response varies by type:
      Track: { "type": "track", "title": ..., "artist": ..., "album": ..., "duration": ... }
      Album: { "type": "album", "name": ..., "artist": ..., "total_tracks": ..., "tracks": [...] }
    """
    try:
        data = request.get_json()
        
        if not data or "url" not in data:
            return jsonify({"error": "URL missing"}), 400
        
        url = data["url"].strip()

        import re as _re
        _SPOTIFY_RE = _re.compile(
            r'^(https://open\.spotify\.com/(track|album|playlist|artist)/[A-Za-z0-9]+|spotify:(track|album|playlist|artist):[A-Za-z0-9]+)'
        )
        if not _SPOTIFY_RE.match(url):
            return jsonify({"error": "Invalid Spotify URL"}), 400
        
        logger.info(f"Metadata request for: {url[:60]}...")
        
        # Detect URL type
        try:
            url_info = extract_spotify_id(url)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        
        if url_info["type"] == "playlist":
            # Playlist metadata (requires user OAuth)
            playlist_tracks = spotify_service.get_playlist_tracks(url)
            user_sp = spotify_service._get_user_sp()
            if user_sp:
                playlist_info = user_sp.playlist(url_info["id"], fields="name")
                playlist_name = playlist_info.get("name", "Playlist")
            else:
                playlist_name = "Playlist"
            tracks_out = []
            for i, t in enumerate(playlist_tracks):
                dur = t.get("duration_ms", 0) or 0
                tracks_out.append({
                    "title": t["title"],
                    "artist": t["artist"],
                    "duration": dur // 1000,
                    "track_number": i + 1,
                })
            return jsonify({
                "type": "album",
                "name": playlist_name,
                "artist": "Various Artists",
                "total_tracks": len(tracks_out),
                "tracks": tracks_out,
                "source": "spotify",
            }), 200
        
        elif url_info["type"] == "album":
            # Album metadata
            album_data = spotify_service.get_album_tracks(url)
            tracks_out = []
            for i, t in enumerate(album_data["tracks"]):
                dur = t.get("duration_ms", 0) or 0
                tracks_out.append({
                    "title": t["title"],
                    "artist": t["artist"],
                    "duration": dur // 1000,
                    "track_number": t.get("track_number", i + 1),
                })
            return jsonify({
                "type": "album",
                "name": album_data["name"],
                "artist": album_data["artist"],
                "total_tracks": album_data["total_tracks"],
                "tracks": tracks_out,
                "source": "spotify",
            }), 200
        
        else:
            # Single track metadata
            metadata = spotify_service.get_track_metadata(url)
            source = metadata.get("source", "spotify")
            # Duplicate detection — check library index by Spotify track ID
            already_in_library = False
            existing_folder = None
            try:
                from database import find_track_by_spotify_id as _find_by_sid
                _sid = url_info.get("id", "")
                if _sid:
                    _existing = _find_by_sid(_sid)
                    if _existing:
                        already_in_library = True
                        existing_folder = _existing.get("genre_folder", "")
            except Exception:
                pass
            return jsonify({
                "type": "track",
                "title": metadata["title"],
                "artist": metadata["artist"],
                "album": metadata["album"],
                "duration": metadata.get("duration_ms", 0) // 1000 if metadata.get("duration_ms") else 0,
                "source": source,
                "already_in_library": already_in_library,
                "existing_folder": existing_folder,
            }), 200
    
    except ValueError as e:
        error_msg = str(e)
        if "rate limit" in error_msg.lower() or "cooling down" in error_msg.lower():
            return jsonify({"error": error_msg, "error_type": "RATE_LIMIT"}), 429
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        logger.error(f"Error in get_track_metadata: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/download', methods=['POST'])
@limiter.limit(LIMIT_DOWNLOAD)
def download_track():
    """
    Download track from Spotify URL
    Runs download in background and returns immediately with 202 Accepted
    """
    # RATE LIMITING — reject if Celery queue is full
    overloaded, err = check_queue_overload()
    if overloaded:
        return jsonify(err), 503
    try:
        global active_download, download_status
        
        # Get request data
        data = request.get_json()
        url = data.get("url")
        
        if not url:
            return jsonify({"error": "No URL"}), 400
        
        # Check if download is already running
        with status_lock:
            if active_download:
                return jsonify({"status": "busy", "message": "Download already running"}), 429
            
            # Mark as active
            active_download = True
            download_status["status"] = "starting"
            download_status["progress"] = 10
        
        # DISCONNECT FIX: per-track dedup — skip if same URL already downloading
        with _active_downloads_lock:
            if url in _active_downloads:
                logger.warning(f"Duplicate download blocked: {url[:60]}")
                with status_lock:
                    active_download = False
                return jsonify({"status": "busy", "message": "This track is already downloading"}), 429
            _active_downloads[url] = True

        # Spawn background task for download (eventlet-safe)
        socketio.start_background_task(target=_download_background, url=url)
        
        # Return immediately with 202 Accepted
        return jsonify({"status": "started"}), 202
    
    except Exception as e:
        logger.error(f"Error in download_track: {str(e)}")
        with status_lock:
            active_download = False
        return jsonify({"error": str(e)}), 500


@app.route('/api/download-stream', methods=['POST'])
@limiter.limit(LIMIT_DOWNLOAD)
def download_stream_to_browser():
    """
    Synchronous streaming download — file goes directly to the user's browser.
    Uses Invidious (YouTube proxy) to bypass datacenter IP bot-blocking.
    Completely separate from the existing background pipeline — nothing shared.
    """
    import tempfile, shutil
    import yt_dlp as _yt

    data = request.get_json() or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({"error": "URL required"}), 400

    url_info = extract_spotify_id(url)
    if url_info.get('type') != 'track':
        return jsonify({"error": "Only single tracks supported for browser download"}), 400

    # 1. Spotify metadata
    try:
        metadata = spotify_service.get_track_metadata(url)
        title = metadata.get('title', '')
        artist = metadata.get('artist', '')
        duration_ms = metadata.get('duration_ms')
        if not title:
            return jsonify({"error": "Could not fetch Spotify metadata"}), 502
    except Exception as e:
        return jsonify({"error": f"Spotify error: {str(e)[:80]}"}), 502

    # 2. YouTube search (extract_flat=True — works from any IP, no bot check)
    search_query = f"ytsearch1:{artist} - {title} Official Audio"
    search_opts = {
        'quiet': True, 'no_warnings': True,
        'extract_flat': True, 'socket_timeout': 10,
    }
    _cookies = '/tmp/youtube_cookies.txt'
    if os.path.isfile(_cookies):
        search_opts['cookiefile'] = _cookies

    try:
        with _yt.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            entries = (info or {}).get('entries', [])
            if not entries:
                return jsonify({"error": "No YouTube match found for this track"}), 404
            video_id = entries[0].get('id', '')
    except Exception as e:
        return jsonify({"error": f"YouTube search failed: {str(e)[:80]}"}), 502

    if not video_id:
        return jsonify({"error": "Could not extract video ID"}), 502

    # 3. Download using YouTube tv_embedded client — far less bot-checked than web client
    #    Works from datacenter IPs without cookies or proxies.
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    tmp_base = tempfile.mktemp()
    tmp_mp3 = tmp_base + '.mp3'
    try:
        ffmpeg = shutil.which('ffmpeg')
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True, 'no_warnings': True,
            'outtmpl': tmp_base + '.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'socket_timeout': 60,
            # tv_embedded player: YouTube's TV/embed API — not subject to same IP blocks as web
            'extractor_args': {
                'youtube': {
                    'player_client': ['tv_embedded'],
                }
            },
        }
        if ffmpeg:
            ydl_opts['ffmpeg_location'] = str(Path(ffmpeg).parent)
        if os.path.isfile(_cookies):
            ydl_opts['cookiefile'] = _cookies

        with _yt.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])

        if not os.path.isfile(tmp_mp3):
            # yt-dlp may have named it differently — find it
            import glob
            candidates = glob.glob(tmp_base + '*.mp3')
            if candidates:
                tmp_mp3 = candidates[0]
            else:
                return jsonify({"error": "Download completed but MP3 file not found"}), 502

        safe_artist = sanitize_filename(artist)[:40]
        safe_title  = sanitize_filename(title)[:60]
        download_name = f"{safe_artist} - {safe_title}.mp3"

        logger.info(f"[download-stream] Streaming '{title}' by {artist} → {download_name}")

        return send_file(
            tmp_mp3,
            as_attachment=True,
            download_name=download_name,
            mimetype='audio/mpeg',
        )

    except Exception as e:
        logger.error(f"[download-stream] Failed for '{title}': {e}")
        return jsonify({"error": f"Download failed: {str(e)[:120]}"}), 502
    finally:
        # Clean up temp files regardless of outcome
        for pat in [tmp_mp3, tmp_base + '.*']:
            import glob
            for f in glob.glob(pat):
                try:
                    os.unlink(f)
                except Exception:
                    pass


def _download_background(url):
    """
    Background worker for downloading a single track, album, or playlist.
    Uses yt-dlp progress hooks for real-time progress updates.
    """
    global active_download, download_status
    
    # DISCONNECT FIX: rate-limit progress emissions to max 2/sec to avoid flooding Socket.IO
    _last_progress_emit = [0.0]

    def _progress_cb(percent, status_text):
        """Callback invoked by yt-dlp progress hook"""
        now = time.time()
        if now - _last_progress_emit[0] < 0.5 and percent < 100:  # DISCONNECT FIX: throttle
            return
        _last_progress_emit[0] = now
        with status_lock:
            download_status["progress"] = percent
            download_status["current"] = status_text
        emit_status()
    
    try:
        # Signal auto-downloader to yield
        set_manual_active(True)
        # Detect URL type
        url_info = extract_spotify_id(url)
        
        if url_info["type"] == "playlist":
            # ─── Playlist download ───
            with status_lock:
                download_status["status"] = "starting"
                download_status["current"] = "Fetching playlist metadata..."
                download_status["progress"] = 5
            
            playlist_tracks = spotify_service.get_playlist_tracks(url)
            total = len(playlist_tracks)
            
            # Get playlist name for folder
            user_sp = spotify_service._get_user_sp()
            if user_sp:
                playlist_info = user_sp.playlist(url_info["id"], fields="name")
                playlist_name = sanitize_filename(playlist_info.get("name", "Playlist"))
            else:
                playlist_name = "Playlist"
            
            # Create playlist folder: Playlists/{playlist_name}/
            playlists_root = os.path.join(os.path.dirname(downloader_service.download_dir), "Playlists")
            playlist_folder = os.path.join(playlists_root, playlist_name)
            os.makedirs(playlist_folder, exist_ok=True)
            
            with status_lock:
                download_status["status"] = "downloading"
                download_status["current"] = f"Playlist: {playlist_name} ({total} tracks)"
                download_status["progress"] = 10
            
            downloaded = 0
            for i, track in enumerate(playlist_tracks):
                title = track["title"]
                artist = track["artist"]
                track_number = i + 1
                base_pct = int(10 + (i / total) * 85)
                
                clean_title = sanitize_filename(title)
                output_fname = f"{str(track_number).zfill(2)} - {clean_title}"
                
                def playlist_progress_cb(pct, status_text, _i=i, _total=total, _title=title):
                    slice_start = 10 + (_i / _total) * 85
                    slice_end = 10 + ((_i + 1) / _total) * 85
                    mapped = int(slice_start + (pct / 100) * (slice_end - slice_start))
                    with status_lock:
                        download_status["progress"] = mapped
                        download_status["current"] = f"[{_i+1}/{_total}] {_title} - {status_text}"
                
                with status_lock:
                    download_status["current"] = f"[{i+1}/{total}] {title} - {artist}"
                    download_status["progress"] = base_pct
                
                result = downloader_service.download_track(
                    title, artist,
                    progress_callback=playlist_progress_cb,
                    output_dir=playlist_folder,
                    output_filename=output_fname
                )
                if result["status"] == "success":
                    downloaded += 1
                add_history_entry(title, artist, result["status"], result.get("filename", ""), result.get("error", ""))
            
            with status_lock:
                download_status["status"] = "completed"
                download_status["progress"] = 100
                download_status["current"] = f"Playlist done: {downloaded}/{total} tracks"
            emit_status()
        
        elif url_info["type"] == "album":
            # ─── Album download ───
            with status_lock:
                download_status["status"] = "starting"
                download_status["current"] = "Fetching album metadata..."
                download_status["progress"] = 5
            
            album_data = spotify_service.get_album_tracks(url)
            tracks = album_data["tracks"]
            total = len(tracks)
            
            with status_lock:
                download_status["status"] = "downloading"
                download_status["current"] = f"Album: {album_data['name']} ({total} tracks)"
                download_status["progress"] = 10
            
            # Create album folder with clean name
            album_folder_name = sanitize_filename(album_data['name'])
            album_folder = os.path.join(downloader_service.download_dir, album_folder_name)
            os.makedirs(album_folder, exist_ok=True)
            
            downloaded = 0
            for i, track in enumerate(tracks):
                title = track["title"]
                artist = track["artist"]
                track_number = track.get("track_number", i + 1)
                base_pct = int(10 + (i / total) * 85)  # 10-95% range
                
                # Build clean numbered filename: "01 - Track Title"
                clean_title = sanitize_filename(title)
                output_fname = f"{str(track_number).zfill(2)} - {clean_title}"
                
                def album_progress_cb(pct, status_text, _i=i, _total=total, _title=title):
                    slice_start = 10 + (_i / _total) * 85
                    slice_end = 10 + ((_i + 1) / _total) * 85
                    mapped = int(slice_start + (pct / 100) * (slice_end - slice_start))
                    with status_lock:
                        download_status["progress"] = mapped
                        download_status["current"] = f"[{_i+1}/{_total}] {_title} - {status_text}"
                
                with status_lock:
                    download_status["current"] = f"[{i+1}/{total}] {title} - {artist}"
                    download_status["progress"] = base_pct
                
                result = downloader_service.download_track(
                    title, artist,
                    progress_callback=album_progress_cb,
                    output_dir=album_folder,
                    output_filename=output_fname
                )
                if result["status"] == "success":
                    downloaded += 1
                add_history_entry(title, artist, result["status"], result.get("filename", ""), result.get("error", ""))
            
            with status_lock:
                download_status["status"] = "completed"
                download_status["progress"] = 100
                download_status["current"] = f"Album done: {downloaded}/{total} tracks"
            emit_status()
        
        else:
            # ─── Single track download ───
            with status_lock:
                download_status["status"] = "starting"
                download_status["current"] = "Fetching metadata..."
                download_status["progress"] = 5

            metadata = spotify_service.get_track_metadata(url)
            title = metadata["title"]
            artist = metadata["artist"]
            duration_ms = metadata.get("duration_ms")
            album_art_url = metadata.get("album_art_url")  # highest-res Spotify image

            # Route to Manual/ folder for manual downloads
            manual_folder = os.path.join(os.path.dirname(downloader_service.download_dir), "Manual")
            os.makedirs(manual_folder, exist_ok=True)

            # Update global queue for manual download
            update_queue(total=1, completed=0, current=f"{title} - {artist}")

            # Use Celery task queue when available AND workers are running.
            # If Redis is reachable but no workers exist, dispatching creates
            # a stale dedup key (never cleared) that blocks re-downloads.
            _live_workers = get_queue_depth().get("workers", 0) if _celery_available else 0
            if _celery_available and _live_workers > 0:
                # QUEUE MANAGER — Atomic check-and-claim to prevent duplicate downloads.
                # claim_task_slot uses Redis SET NX — no race condition.
                if not claim_task_slot(title, artist):
                    logger.info(f"[celery] Dedup skip (already queued): {title} - {artist}")
                    with status_lock:
                        active_download = False
                    socketio.emit("download_duplicate", {
                        "title": title,
                        "artist": artist,
                        "message": f"'{title}' by {artist} is already downloading.",
                    })
                    return  # exits background task cleanly; finally block still runs

                logger.info(f"[celery] Dispatching track to Celery: {title} - {artist}")
                task_meta = {
                    "title": title,
                    "artist": artist,
                    "album": metadata.get("album"),
                    "duration_ms": duration_ms,
                    "album_art_url": album_art_url,
                }
                task = download_track_task.delay(task_meta, manual_folder)
                # Slot already claimed by claim_task_slot above; update with real task_id
                register_task(title, artist, task.id)
                with status_lock:
                    download_status["status"] = "queued"
                    download_status["current"] = f"{title} - {artist} (queued)"
                    download_status["progress"] = 15
                    download_status["task_id"] = task.id
                emit_status()
                add_history_entry(title, artist, "queued", "")
            else:
                if _celery_available and _live_workers == 0:
                    logger.info(f"[celery] No live workers — using threading for: {title} - {artist}")
                def track_progress_cb(pct, status_text):
                    with status_lock:
                        download_status["progress"] = max(10, pct)
                        download_status["current"] = f"{title} - {status_text}"

                with status_lock:
                    download_status["status"] = "downloading"
                    download_status["current"] = f"{title} - {artist}"
                    download_status["progress"] = 10

                result = downloader_service.download_track(title, artist, progress_callback=track_progress_cb, duration_ms=duration_ms, output_dir=manual_folder, album_art_url=album_art_url)

                # Update queue as completed
                update_queue(completed=1)

                with status_lock:
                    if result['status'] == 'success':
                        download_status["status"] = "completed"
                        download_status["progress"] = 100
                        download_status["current"] = result['filename']
                        download_status["match_quality"] = result.get("match_quality", "exact")
                    elif result['status'] == 'fallback':
                        download_status["status"] = "fallback"
                        download_status["progress"] = 100
                        download_status["current"] = f"Manual download: {title} - {artist}"
                        download_status["match_quality"] = "fallback"
                    else:
                        download_status["status"] = "failed"
                        download_status["current"] = "Download failed"
                        download_status["match_quality"] = ""
                emit_status()
                add_history_entry(title, artist, result['status'], result.get('filename', ''), result.get('error', ''))
    
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        with status_lock:
            download_status["status"] = "failed"
            download_status["current"] = str(e)[:100]
        emit_status()
    
    finally:
        set_manual_active(False)
        with _active_downloads_lock:  # DISCONNECT FIX: release per-track lock
            _active_downloads.pop(url, None)
        with status_lock:
            active_download = False



@app.route('/api/delete/<path:filename>', methods=['DELETE'])
def delete_download(filename):
    """
    Delete a downloaded file

    Response:
    {
        "success": true/false,
        "message": "message"
    }
    """
    try:
        safe = os.path.basename(filename)
        full = os.path.realpath(os.path.join(BASE_DOWNLOAD_DIR, safe))
        if not full.startswith(os.path.realpath(BASE_DOWNLOAD_DIR) + os.sep):
            return jsonify({"success": False, "message": "Invalid filename"}), 400
        result = downloader_service.delete_download(safe)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    
    except Exception as e:
        logger.error(f"Error deleting download: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route('/api/artwork', methods=['GET'])
def get_artwork():
    """Serve embedded album art (APIC frame) from an MP3 file."""
    filepath = request.args.get('path', '').strip()
    if not filepath:
        return '', 400
    full = os.path.realpath(
        os.path.join(BASE_DOWNLOAD_DIR, filepath)
        if not os.path.isabs(filepath) else filepath
    )
    if not full.startswith(os.path.realpath(BASE_DOWNLOAD_DIR) + os.sep):
        return '', 403
    if not os.path.isfile(full):
        return '', 404
    try:
        from mutagen.id3 import ID3
        tags = ID3(full)
        apic = tags.get('APIC:') or tags.get('APIC:Cover')
        if not apic:
            return '', 404
        from flask import Response
        return Response(
            apic.data,
            mimetype=apic.mime or 'image/jpeg',
            headers={'Cache-Control': 'public, max-age=86400'},
        )
    except Exception:
        return '', 404


@app.route('/api/files', methods=['GET'])
def get_all_files():
    """Get all MP3 files from the download directory tree."""
    return jsonify({"success": True, "files": load_existing_files()}), 200


@app.route('/api/files/folder-tags', methods=['GET'])
def folder_tags():
    """Return BPM, Camelot key, artist, title for every MP3 in one folder.

    Query param: folder — relative path, e.g. 'Library/House'
    Returns:     { "Song.mp3": { bpm, camelot_key, artist, title }, ... }
    """
    from mutagen.id3 import ID3, ID3NoHeaderError
    from bpm_key_service import tkey_to_camelot as _t2c

    folder = request.args.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder param required"}), 400

    target = Path(BASE_DOWNLOAD_DIR) / folder
    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(Path(BASE_DOWNLOAD_DIR).resolve())):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_dir():
        return jsonify({}), 200

    result = {}
    for mp3 in sorted(resolved.glob("*.mp3")):
        try:
            tags    = ID3(str(mp3))
            bpm_f   = tags.get("TBPM")
            key_f   = tags.get("TKEY")
            tpe_f   = tags.get("TPE1")
            tit_f   = tags.get("TIT2")
            bpm_raw = str(bpm_f.text[0]).strip() if bpm_f and bpm_f.text else ""
            tkey    = str(key_f.text[0]).strip() if key_f and key_f.text else ""
            try:
                bpm_val = int(float(bpm_raw)) if bpm_raw else None
            except ValueError:
                bpm_val = None
            result[mp3.name] = {
                "bpm":         bpm_val,
                "camelot_key": _t2c(tkey),
                "artist":      str(tpe_f.text[0]).strip() if tpe_f and tpe_f.text else "",
                "title":       str(tit_f.text[0]).strip() if tit_f and tit_f.text else mp3.stem,
            }
        except (ID3NoHeaderError, Exception):
            result[mp3.name] = {"bpm": None, "camelot_key": "", "artist": "", "title": mp3.stem}

    return jsonify(result), 200


@app.route('/api/auto-status', methods=['GET'])
def auto_status():
    """Get auto-downloader status"""
    return jsonify(AUTO_STATUS), 200


@app.route('/api/queue-status', methods=['GET'])
def queue_status():
    """Get global download queue status"""
    return jsonify(download_queue_status), 200


@app.route('/api/api-usage', methods=['GET'])
def api_usage():
    """Get Spotify API usage stats"""
    return jsonify(get_api_usage()), 200


def _extract_playlist_id(raw: str) -> str:
    import re
    m = re.search(r'playlist/([A-Za-z0-9]+)', raw)
    return m.group(1) if m else raw

def _save_user_config(data: dict) -> None:
    p = Path(__file__).parent / "user_config.json"
    with _user_config_lock:
        existing = {}
        try:
            existing = json.loads(p.read_text())
        except Exception:
            pass
        existing.update(data)
        p.write_text(json.dumps(existing, indent=2))

@app.route('/api/ingest-config', methods=['GET', 'POST'])
def ingest_config():
    """Get or update ingest playlist configuration"""
    if request.method == 'POST':
        data = request.get_json() or {}
        raw = (data.get("playlist_id") or "").strip()
        playlist_id = _extract_playlist_id(raw)
        _save_user_config({"ingest_playlist_id": playlist_id})
        import services.auto_downloader as _ad
        _ad.INGEST_PLAYLIST_ID = playlist_id
        return jsonify({"ok": True, "playlist_id": playlist_id}), 200
    from services.auto_downloader import INGEST_PLAYLIST_ID as _pid
    return jsonify({
        "enabled": bool(_pid),
        "playlist_id": _pid or None,
    }), 200


@app.route('/api/history', methods=['GET'])
def get_history():
    """Get download history"""
    with history_lock:
        return jsonify({"success": True, "history": list(download_history[:50])}), 200


@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Clear download history"""
    with history_lock:
        download_history.clear()
    emit_status()
    return jsonify({"success": True}), 200


def _sanitize_force_folder(raw):
    """Validate and sanitize an optional force_folder value supplied by the client.

    Returns a sanitized folder name, or None if the input is empty/blank.
    Raises ValueError with a user-facing message if the input is unsafe or
    reduces to nothing after cleaning.
    """
    from services.organizer_service import clean_folder_name
    if not isinstance(raw, str):
        raise ValueError("force_folder must be a string")
    stripped = raw.strip()
    if not stripped:
        raise ValueError("force_folder cannot be empty")
    if ".." in stripped or "/" in stripped or "\\" in stripped or stripped in (".", "..") :
        raise ValueError("force_folder contains invalid path characters")
    cleaned = clean_folder_name(stripped)
    if not cleaned or cleaned == "Unknown" or len(cleaned) > 120:
        raise ValueError("force_folder is not a valid folder name")
    return cleaned


@app.route('/api/refresh-playlist', methods=['POST'])
@limiter.limit(LIMIT_PLAYLIST)
def refresh_playlist():
    """Trigger a manual playlist refresh (force-fetches from Spotify, bypasses cache).
    Accepts optional JSON body:
        {
          "download_dir": "/path/to/folder",     # optional absolute path
          "force_folder": "Sammy Virji",         # optional per-trigger folder override
          "force_redownload": true               # optional dedup bypass (history + registry)
        }
    """
    download_dir = None
    force_folder = None
    force_redownload = False
    data = request.get_json(silent=True)

    if data and data.get("download_dir"):
        requested = data["download_dir"].strip()
        # Validate: must be an absolute path under a real directory
        if os.path.isabs(requested):
            download_dir = requested
        else:
            return jsonify({"status": "error", "message": "download_dir must be an absolute path"}), 400

    # FORCE FOLDER — optional per-trigger override that pins every track in the
    # sync to a single Ingest subfolder regardless of artist-based routing.
    if data and data.get("force_folder"):
        try:
            force_folder = _sanitize_force_folder(data["force_folder"])
        except ValueError as ve:
            logger.warning(f"[refresh-playlist] Rejected force_folder: {ve}")
            return jsonify({"status": "error", "message": str(ve)}), 400
        if force_folder:
            logger.info(f"[refresh-playlist] force_folder override requested: '{force_folder}'")

    # FORCE REDOWNLOAD — optional dedup bypass. Reject non-boolean-coercible
    # input (e.g. arbitrary strings) rather than silently treating them as
    # truthy, matching the defensive style used for force_folder.
    if data and "force_redownload" in data:
        raw_fr = data["force_redownload"]
        if not isinstance(raw_fr, bool):
            return jsonify({"status": "error", "message": "force_redownload must be a boolean"}), 400
        force_redownload = raw_fr
        if force_redownload:
            logger.info("[refresh-playlist] force_redownload requested — history + registry dedup will be bypassed")

    result = _manual_refresh(
        download_dir=download_dir,
        force_folder=force_folder,
        force_redownload=force_redownload,
    )
    status_code = 200 if result["status"] == "ok" else 429 if result["status"] == "rate_limited" else 500
    return jsonify(result), status_code


@app.route('/api/clear-history-for-playlist', methods=['POST'])
def clear_history_for_playlist():
    """Remove ingest playlist track IDs from history so they re-download on next sync."""
    from services.auto_downloader import remove_tracks_from_history
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id") or INGEST_PLAYLIST_ID
    if not playlist_id:
        return jsonify({"error": "No playlist_id configured"}), 400
    try:
        tracks = spotify_service.get_playlist_tracks_by_id(
            playlist_id, force_refresh=True
        )
        track_ids = [t["id"] for t in tracks]
        result = remove_tracks_from_history(track_ids)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[app] clear-history-for-playlist error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/clear-genre-cache', methods=['POST'])
def clear_genre_cache_endpoint():
    """Clear the in-memory genre cache after updating SPOTIFY_GENRE_MAP."""
    try:
        from services.genre_router import clear_genre_cache as _clear
        _clear()
        return jsonify({"status": "ok", "message": "Genre cache cleared"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """
    Production health check — used by Render health probes and the frontend
    status panel.  Returns 200 when healthy, 503 when degraded/critical.

    Includes: Redis, MongoDB, Celery queue depth, worker count, uptime,
    rate-limit storage, and pipeline metrics summary.
    """
    from database import is_mongo_available
    from celery_app import is_redis_available
    from queue_manager import get_queue_depth

    mongo_ok  = is_mongo_available()
    redis_ok  = is_redis_available()
    queue     = get_queue_depth()
    uptime    = int(time.time() - _START_TIME)

    # Fetch pipeline metrics (non-blocking; returns empty dicts on failure)
    pipeline_metrics: dict = {}
    try:
        from services.metrics_service import health_report as _metrics_health
        pipeline_metrics = _metrics_health(window_minutes=60)
    except Exception:
        pass

    # Determine overall status.
    # 503 only for hard infrastructure failures so Render health probes don't
    # restart the dyno due to stale/historical pipeline metrics.
    # Pipeline metric status is informational only.
    if not mongo_ok:
        overall = "critical"    # 503 — MongoDB is required for all operations
    elif _celery_available and not redis_ok:
        overall = "degraded"    # 200 — Redis was expected but unreachable
    else:
        # Incorporate pipeline health only as an advisory — never escalate to
        # 503 from pipeline metrics alone so health probes remain stable.
        pipeline_status = pipeline_metrics.get("status", "unknown")
        if pipeline_status in ("degraded", "critical"):
            overall = "degraded"
        else:
            overall = "healthy"

    redis_url_masked = ""
    raw_redis = os.getenv("REDIS_URL", "")
    if raw_redis:
        # Mask credentials: redis://user:pass@host:port/db → host:port/db
        redis_url_masked = raw_redis.split("@")[-1]

    body = {
        "status": overall,
        "version": os.getenv("APP_VERSION", "alpha-1"),
        "uptime_seconds": uptime,
        "services": {
            "mongodb": "ok" if mongo_ok else "unavailable",
            "redis":   "ok" if redis_ok   else "unavailable",
            "celery":  "ok" if _celery_available else "unavailable (threading fallback)",
        },
        "queue": queue,
        "rate_limiter": {
            "storage": redis_url_masked or "memory (dev)",
        },
        "pipeline": {
            "status":   pipeline_metrics.get("status", "unknown"),
            "alerts":   pipeline_metrics.get("alerts", []),
            "counters": pipeline_metrics.get("counters", {}),
        },
        "environment": os.getenv("FLASK_ENV", "production"),
    }

    # Only return 503 for hard failures (MongoDB down) so Render doesn't
    # restart the dyno on degraded-but-functional state.
    http_status = 503 if overall == "critical" else 200
    return jsonify(body), http_status


# ═══════════════════════════════════════════════════════════════════
# CELERY UPGRADE — New API routes for task management
# ═══════════════════════════════════════════════════════════════════

# CELERY UPGRADE — GET /api/task/<task_id>/status
@app.route('/api/task/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """
    Return the current state of a Celery task.
    Falls back to a 503 if Celery is not available.
    """
    if not _celery_available:
        return jsonify({"error": "Task queue not available (Redis offline)"}), 503

    try:
        result = _celery_app.AsyncResult(task_id)
        response = {
            "task_id": task_id,
            "state": result.state,       # PENDING, STARTED, RETRY, SUCCESS, FAILURE
            "ready": result.ready(),
            "successful": result.successful() if result.ready() else None,
        }
        if result.ready() and result.successful():
            response["result"] = result.result
        elif result.failed():
            response["error"] = str(result.result)[:300]
        # Include info dict when task is in STARTED/RETRY
        if result.info and isinstance(result.info, dict):
            response["info"] = result.info
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"Error checking task {task_id}: {e}")
        return jsonify({"error": str(e)}), 500


# CELERY UPGRADE — DELETE /api/task/<task_id>
@app.route('/api/task/<task_id>', methods=['DELETE'])
def revoke_task(task_id):
    """
    Revoke (cancel) a pending or running Celery task.
    """
    if not _celery_available:
        return jsonify({"error": "Task queue not available (Redis offline)"}), 503

    try:
        _celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        logger.info(f"Revoked task {task_id}")
        return jsonify({"task_id": task_id, "status": "revoked"}), 200
    except Exception as e:
        logger.error(f"Error revoking task {task_id}: {e}")
        return jsonify({"error": str(e)}), 500


# CELERY UPGRADE — GET /api/queue
@app.route('/api/queue', methods=['GET'])
def get_celery_queue():
    """
    Return a snapshot of active, reserved, and scheduled Celery tasks.
    """
    if not _celery_available:
        return jsonify({"error": "Task queue not available (Redis offline)"}), 503

    try:
        inspect = _celery_app.control.inspect(timeout=2)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}

        # Flatten into lists
        def _flatten(d):
            out = []
            for worker_tasks in d.values():
                out.extend(worker_tasks)
            return out

        return jsonify({
            "active": _flatten(active),
            "reserved": _flatten(reserved),
            "scheduled": _flatten(scheduled),
        }), 200
    except Exception as e:
        logger.error(f"Error inspecting queue: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════
# CELERY UPGRADE — Redis pub/sub bridge for Socket.IO events
# ═══════════════════════════════════════════════════════════════════
def _redis_pubsub_bridge():
    """
    Background task: subscribe to Redis 'socketio_bridge' channel and
    re-emit events to all connected Socket.IO clients.

    This bridges events published from Celery workers into the Flask
    Socket.IO server.
    """
    if not _celery_available:
        return

    try:
        import json as _json
        import redis as _redis_lib
        from celery_app import REDIS_URL

        r = _redis_lib.Redis.from_url(REDIS_URL, socket_connect_timeout=2)
        pubsub = r.pubsub()
        pubsub.subscribe("socketio_bridge")
        logger.info("Redis pub/sub bridge started")

        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                payload = _json.loads(message["data"])
                event = payload.get("event")
                data = payload.get("data")
                if event and data:
                    socketio.emit(event, data)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Redis pub/sub bridge failed: {e} — Celery events won't reach frontend")


# ═══════════════════════════════════════════════════════════════════
# MUSICBRAINZ — Library retag routes (Task 3)
# ═══════════════════════════════════════════════════════════════════

# MUSICBRAINZ — Retag state (shared across requests)
_retag_state = {  # MUSICBRAINZ
    "running": False,  # MUSICBRAINZ
    "current": 0,  # MUSICBRAINZ
    "total": 0,  # MUSICBRAINZ
    "percentage": 0.0,  # MUSICBRAINZ
    "current_file": "",  # MUSICBRAINZ
    "status": "idle",  # MUSICBRAINZ
    "tagged": 0,  # MUSICBRAINZ
    "failed": 0,  # MUSICBRAINZ
}  # MUSICBRAINZ
_retag_lock = threading.Lock()  # MUSICBRAINZ


def _retag_worker():  # MUSICBRAINZ
    """Background worker that retags all MP3 files in the library."""  # MUSICBRAINZ
    global _retag_state  # MUSICBRAINZ
    try:  # MUSICBRAINZ
        # MUSICBRAINZ — Scan for all .mp3 files
        all_files = []  # MUSICBRAINZ
        for root, _dirs, filenames in os.walk(BASE_DOWNLOAD_DIR):  # MUSICBRAINZ
            for fname in filenames:  # MUSICBRAINZ
                if fname.lower().endswith(".mp3"):  # MUSICBRAINZ
                    all_files.append(os.path.join(root, fname))  # MUSICBRAINZ

        total = len(all_files)  # MUSICBRAINZ
        with _retag_lock:  # MUSICBRAINZ
            _retag_state["total"] = total  # MUSICBRAINZ
            _retag_state["status"] = "processing"  # MUSICBRAINZ

        if total == 0:  # MUSICBRAINZ
            with _retag_lock:  # MUSICBRAINZ
                _retag_state["status"] = "complete"  # MUSICBRAINZ
                _retag_state["running"] = False  # MUSICBRAINZ
            socketio.emit("retag_progress", {  # MUSICBRAINZ
                "current": 0, "total": 0, "percentage": 100.0,  # MUSICBRAINZ
                "current_file": "", "status": "complete",  # MUSICBRAINZ
            })  # MUSICBRAINZ
            return  # MUSICBRAINZ

        tagged = 0  # MUSICBRAINZ
        failed = 0  # MUSICBRAINZ

        for i, filepath in enumerate(all_files):  # MUSICBRAINZ
            fname = os.path.basename(filepath)  # MUSICBRAINZ
            title = fname[:-4] if fname.lower().endswith(".mp3") else fname  # MUSICBRAINZ
            # MUSICBRAINZ — Extract artist from parent folder name
            parent_folder = os.path.basename(os.path.dirname(filepath))  # MUSICBRAINZ
            artist = parent_folder if parent_folder != os.path.basename(BASE_DOWNLOAD_DIR) else ""  # MUSICBRAINZ

            pct = round(((i + 1) / total) * 100, 1)  # MUSICBRAINZ
            with _retag_lock:  # MUSICBRAINZ
                _retag_state["current"] = i + 1  # MUSICBRAINZ
                _retag_state["percentage"] = pct  # MUSICBRAINZ
                _retag_state["current_file"] = fname  # MUSICBRAINZ

            # MUSICBRAINZ — Emit progress
            socketio.emit("retag_progress", {  # MUSICBRAINZ
                "current": i + 1,  # MUSICBRAINZ
                "total": total,  # MUSICBRAINZ
                "percentage": pct,  # MUSICBRAINZ
                "current_file": fname,  # MUSICBRAINZ
                "status": "processing",  # MUSICBRAINZ
            })  # MUSICBRAINZ

            try:  # MUSICBRAINZ
                spotify_meta = {  # MUSICBRAINZ
                    "title": title,  # MUSICBRAINZ
                    "artist": artist,  # MUSICBRAINZ
                    "album": "",  # MUSICBRAINZ
                    "album_art_url": "",  # MUSICBRAINZ
                    "duration_ms": None,  # MUSICBRAINZ
                    "id": "",  # MUSICBRAINZ
                }  # MUSICBRAINZ
                report = tagger_tag_file(filepath, spotify_meta)  # MUSICBRAINZ
                if report and report.get("tags_written"):  # MUSICBRAINZ
                    tagged += 1  # MUSICBRAINZ
                else:  # MUSICBRAINZ
                    failed += 1  # MUSICBRAINZ
            except Exception as e:  # MUSICBRAINZ
                logger.warning(f"[retag] Failed to tag {fname}: {e}")  # MUSICBRAINZ
                failed += 1  # MUSICBRAINZ

        # MUSICBRAINZ — Done
        with _retag_lock:  # MUSICBRAINZ
            _retag_state["status"] = "complete"  # MUSICBRAINZ
            _retag_state["running"] = False  # MUSICBRAINZ
            _retag_state["tagged"] = tagged  # MUSICBRAINZ
            _retag_state["failed"] = failed  # MUSICBRAINZ
            _retag_state["percentage"] = 100.0  # MUSICBRAINZ

        socketio.emit("retag_progress", {  # MUSICBRAINZ
            "current": total,  # MUSICBRAINZ
            "total": total,  # MUSICBRAINZ
            "percentage": 100.0,  # MUSICBRAINZ
            "current_file": "",  # MUSICBRAINZ
            "status": "complete",  # MUSICBRAINZ
            "tagged": tagged,  # MUSICBRAINZ
            "failed": failed,  # MUSICBRAINZ
        })  # MUSICBRAINZ
        logger.info(f"[retag] Complete: {tagged} tagged, {failed} failed, {total} total")  # MUSICBRAINZ

    except Exception as e:  # MUSICBRAINZ
        logger.error(f"[retag] Worker crashed: {e}")  # MUSICBRAINZ
        with _retag_lock:  # MUSICBRAINZ
            _retag_state["status"] = "error"  # MUSICBRAINZ
            _retag_state["running"] = False  # MUSICBRAINZ


@app.route('/api/library/retag', methods=['POST'])  # MUSICBRAINZ
def retag_library():  # MUSICBRAINZ
    """Retag all MP3 files in the downloads folder with MusicBrainz metadata."""  # MUSICBRAINZ
    if not _tagger_available:  # MUSICBRAINZ
        return jsonify({"error": "Tagger service not available"}), 503  # MUSICBRAINZ

    with _retag_lock:  # MUSICBRAINZ
        if _retag_state["running"]:  # MUSICBRAINZ
            return jsonify({"error": "Retag already in progress"}), 429  # MUSICBRAINZ
        _retag_state["running"] = True  # MUSICBRAINZ
        _retag_state["current"] = 0  # MUSICBRAINZ
        _retag_state["total"] = 0  # MUSICBRAINZ
        _retag_state["percentage"] = 0.0  # MUSICBRAINZ
        _retag_state["current_file"] = ""  # MUSICBRAINZ
        _retag_state["status"] = "starting"  # MUSICBRAINZ
        _retag_state["tagged"] = 0  # MUSICBRAINZ
        _retag_state["failed"] = 0  # MUSICBRAINZ

    # MUSICBRAINZ — Run in background thread so it doesn't block
    socketio.start_background_task(target=_retag_worker)  # MUSICBRAINZ
    return jsonify({"status": "started", "message": "Library retag started"}), 202  # MUSICBRAINZ


@app.route('/api/library/retag/status', methods=['GET'])  # MUSICBRAINZ
def retag_status():  # MUSICBRAINZ
    """Return current retag progress or last summary."""  # MUSICBRAINZ
    with _retag_lock:  # MUSICBRAINZ
        return jsonify(dict(_retag_state)), 200  # MUSICBRAINZ


# ═══════════════════════════════════════════════════════════════════  # ANALYTICS
# ANALYTICS — Dashboard aggregation routes  # ANALYTICS
# ═══════════════════════════════════════════════════════════════════  # ANALYTICS


@app.route('/api/analytics/overview')  # ANALYTICS
def analytics_overview():  # ANALYTICS
    """Return high-level analytics stats."""  # ANALYTICS
    return jsonify(get_overview_stats())  # ANALYTICS


@app.route('/api/analytics/downloads-per-day')  # ANALYTICS
def analytics_downloads_per_day():  # ANALYTICS
    """Return download counts grouped by day."""  # ANALYTICS
    days = request.args.get('days', 30, type=int)  # ANALYTICS
    return jsonify(get_downloads_per_day(days))  # ANALYTICS


@app.route('/api/analytics/top-artists')  # ANALYTICS
def analytics_top_artists():  # ANALYTICS
    """Return top artists by download count."""  # ANALYTICS
    limit = request.args.get('limit', 10, type=int)  # ANALYTICS
    return jsonify(get_top_artists(limit))  # ANALYTICS


@app.route('/api/analytics/source-breakdown')  # ANALYTICS
def analytics_source_breakdown():  # ANALYTICS
    """Return download counts by source platform."""  # ANALYTICS
    return jsonify(get_source_breakdown())  # ANALYTICS


@app.route('/api/analytics/tagging-breakdown')  # ANALYTICS
def analytics_tagging_breakdown():  # ANALYTICS
    """Return tagging source breakdown."""  # ANALYTICS
    return jsonify(get_tagging_breakdown())  # ANALYTICS


@app.route('/api/analytics/recent')  # ANALYTICS
def analytics_recent():  # ANALYTICS
    """Return recent downloads."""  # ANALYTICS
    return jsonify(get_recent_downloads())  # ANALYTICS


@app.route('/api/analytics/failed')  # ANALYTICS
def analytics_failed():  # ANALYTICS
    """Return recent tagging failures."""  # ANALYTICS
    return jsonify(get_failed_downloads())  # ANALYTICS


@app.route('/api/cache-analytics')  # ANALYTICS
def cache_analytics():  # ANALYTICS
    """Return MusicBrainz cache statistics."""  # ANALYTICS
    try:  # ANALYTICS
        return jsonify(get_cache_analytics())  # ANALYTICS
    except Exception as e:  # ANALYTICS
        return jsonify({"error": str(e)}), 500  # ANALYTICS


@app.route('/api/tagging-failures/summary')  # ANALYTICS
def tagging_failures_summary():  # ANALYTICS
    """Return tagging failure breakdown by error_type + retry trend."""  # ANALYTICS
    try:  # ANALYTICS
        return jsonify(get_tagging_failure_summary())  # ANALYTICS
    except Exception as e:  # ANALYTICS
        return jsonify({"error": str(e)}), 500  # ANALYTICS


@app.route('/api/download-history/stats')  # ANALYTICS
def download_history_stats():  # ANALYTICS
    """Return weekly download stats and top-3 artists."""  # ANALYTICS
    try:  # ANALYTICS
        return jsonify(get_weekly_download_stats())  # ANALYTICS
    except Exception as e:  # ANALYTICS
        return jsonify({"error": str(e)}), 500  # ANALYTICS


# ═══════════════════════════════════════════════════════════════════  # NOTIFICATION
# NOTIFICATION — Test route & storage monitor  # NOTIFICATION
# ═══════════════════════════════════════════════════════════════════  # NOTIFICATION

@app.route('/api/notifications/test', methods=['POST'])  # NOTIFICATION
def test_notifications_route():  # NOTIFICATION
    """Send a test notification to Telegram + Discord."""  # NOTIFICATION
    try:  # NOTIFICATION
        from services.notifications_service import test_notifications, is_telegram_enabled, is_discord_enabled  # NOTIFICATION
        test_notifications()  # NOTIFICATION
        return jsonify({  # NOTIFICATION
            "message": "Test notification sent",  # NOTIFICATION
            "telegram_enabled": is_telegram_enabled(),  # NOTIFICATION
            "discord_enabled": is_discord_enabled(),  # NOTIFICATION
        })  # NOTIFICATION
    except Exception as e:  # NOTIFICATION
        return jsonify({"error": str(e)}), 500  # NOTIFICATION


@app.route('/api/skipped-tracks', methods=['GET'])  # PERMANENT SKIP
def get_skipped_tracks():  # PERMANENT SKIP
    """Return all permanently skipped tracks and their failure counts."""  # PERMANENT SKIP
    try:  # PERMANENT SKIP
        from services.auto_downloader import _load_failure_counts, MAX_FAIL_ATTEMPTS  # PERMANENT SKIP
        counts = _load_failure_counts()  # PERMANENT SKIP
        skipped = {tid: c for tid, c in counts.items() if c >= MAX_FAIL_ATTEMPTS}  # PERMANENT SKIP
        return jsonify({"skipped": skipped, "threshold": MAX_FAIL_ATTEMPTS, "total": len(skipped)})  # PERMANENT SKIP
    except Exception as e:  # PERMANENT SKIP
        return jsonify({"error": str(e)}), 500  # PERMANENT SKIP


@app.route('/api/skipped-tracks/reset', methods=['POST'])  # PERMANENT SKIP
def reset_skipped_tracks():  # PERMANENT SKIP
    """Reset failure counts — optionally for a single track_id or all."""  # PERMANENT SKIP
    try:  # PERMANENT SKIP
        from services.auto_downloader import _load_failure_counts, _save_failure_counts  # PERMANENT SKIP
        from services.auto_downloader import _load_ingest_history, _save_ingest_history  # PERMANENT SKIP
        data = request.get_json(silent=True) or {}  # PERMANENT SKIP
        track_id = data.get("track_id")  # PERMANENT SKIP
        counts = _load_failure_counts()  # PERMANENT SKIP
        history = _load_ingest_history()  # PERMANENT SKIP
        if track_id:  # PERMANENT SKIP
            counts.pop(track_id, None)  # PERMANENT SKIP
            history.discard(track_id)  # PERMANENT SKIP — allow re-download
            msg = f"Reset failure count for {track_id}"  # PERMANENT SKIP
        else:  # PERMANENT SKIP
            reset_ids = [tid for tid, c in counts.items() if c >= 3]  # PERMANENT SKIP
            counts.clear()  # PERMANENT SKIP
            for tid in reset_ids:  # PERMANENT SKIP
                history.discard(tid)  # PERMANENT SKIP
            msg = f"Reset all failure counts ({len(reset_ids)} tracks unblocked)"  # PERMANENT SKIP
        _save_failure_counts(counts)  # PERMANENT SKIP
        _save_ingest_history(history)  # PERMANENT SKIP
        return jsonify({"message": msg})  # PERMANENT SKIP
    except Exception as e:  # PERMANENT SKIP
        return jsonify({"error": str(e)}), 500  # PERMANENT SKIP


@app.route('/api/notifications/status', methods=['GET'])  # NOTIFICATION
def notifications_status():  # NOTIFICATION
    """Return which notification channels are enabled."""  # NOTIFICATION
    try:  # NOTIFICATION
        from services.notifications_service import is_telegram_enabled, is_discord_enabled  # NOTIFICATION
        from services.notifications_service import NOTIFY_ON_SUCCESS, NOTIFY_ON_FAILURE, NOTIFY_ON_PLAYLIST  # NOTIFICATION
        from services.notifications_service import STORAGE_THRESHOLD_MB  # NOTIFICATION
        return jsonify({  # NOTIFICATION
            "telegram_enabled": is_telegram_enabled(),  # NOTIFICATION
            "discord_enabled": is_discord_enabled(),  # NOTIFICATION
            "notify_on_success": NOTIFY_ON_SUCCESS,  # NOTIFICATION
            "notify_on_failure": NOTIFY_ON_FAILURE,  # NOTIFICATION
            "notify_on_playlist": NOTIFY_ON_PLAYLIST,  # NOTIFICATION
            "storage_threshold_mb": STORAGE_THRESHOLD_MB,  # NOTIFICATION
        })  # NOTIFICATION
    except Exception as e:  # NOTIFICATION
        return jsonify({"error": str(e)}), 500  # NOTIFICATION


# NOTIFICATION — Storage monitor (runs every 30 minutes, warns once per hour max)
_last_storage_warning_time = 0  # NOTIFICATION


def _storage_monitor():  # NOTIFICATION
    """Background task: check storage every 30 minutes and send warning if over threshold."""  # NOTIFICATION
    global _last_storage_warning_time  # NOTIFICATION
    time.sleep(60)  # NOTIFICATION — wait for startup
    while True:  # NOTIFICATION
        try:  # NOTIFICATION
            from services.notifications_service import STORAGE_THRESHOLD_MB, notify_storage_warning  # NOTIFICATION
            from services.notifications_service import is_telegram_enabled, is_discord_enabled  # NOTIFICATION
            if not (is_telegram_enabled() or is_discord_enabled()):  # NOTIFICATION
                time.sleep(1800)  # NOTIFICATION — 30 min
                continue  # NOTIFICATION
            total_bytes = 0  # NOTIFICATION
            if os.path.isdir(BASE_DOWNLOAD_DIR):  # NOTIFICATION
                for root, _dirs, files in os.walk(BASE_DOWNLOAD_DIR):  # NOTIFICATION
                    for f in files:  # NOTIFICATION
                        try:  # NOTIFICATION
                            total_bytes += os.path.getsize(os.path.join(root, f))  # NOTIFICATION
                        except OSError:  # NOTIFICATION
                            pass  # NOTIFICATION
            used_mb = total_bytes / (1024 * 1024)  # NOTIFICATION
            now = time.time()  # NOTIFICATION
            # NOTIFICATION — Only warn if over threshold AND at least 1 hour since last warning
            if used_mb > STORAGE_THRESHOLD_MB and (now - _last_storage_warning_time) > 3600:  # NOTIFICATION
                notify_storage_warning(used_mb, STORAGE_THRESHOLD_MB)  # NOTIFICATION
                _last_storage_warning_time = now  # NOTIFICATION
                logger.info(f"[notifications] Storage warning sent: {round(used_mb)}MB / {round(STORAGE_THRESHOLD_MB)}MB")  # NOTIFICATION
        except Exception as e:  # NOTIFICATION
            logger.error(f"[notifications] Storage monitor error: {e}")  # NOTIFICATION
        time.sleep(1800)  # NOTIFICATION — check every 30 minutes


@app.route('/api/library/organize', methods=['POST'])
def library_organize():
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'artist')
    if mode not in ('artist', 'genre', 'artist_genre', 'dj'):
        return jsonify({"success": False, "error": f"Invalid mode: {mode}"}), 400
    try:
        from services.organizer_service import organize_library
        result = organize_library(mode=mode)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"[library/organize] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/library/organize-recent', methods=['POST'])
def library_organize_recent():
    data  = request.get_json(silent=True) or {}
    mode  = data.get('mode', 'artist')
    hours = int(data.get('hours', 24))
    if mode not in ('artist', 'genre', 'artist_genre'):
        return jsonify({"success": False, "error": f"Invalid mode: {mode}"}), 400
    try:
        from services.organizer_service import organize_recent
        result = organize_recent(mode=mode, hours=hours)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"[library/organize-recent] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _with_timeout(fn, seconds=8):
    """Run fn() in a daemon thread with a wall-clock timeout. Raises TimeoutError on expiry."""
    import threading
    result = [None]
    exc = [None]

    def _run():
        try:
            result[0] = fn()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"External API call timed out after {seconds}s")
    if exc[0]:
        raise exc[0]
    return result[0]


@app.route('/api/duplicates', methods=['GET'])
def list_duplicates():
    """Return all files sitting in NeedsReview/Duplicates/."""
    from mutagen.id3 import ID3 as _ID3
    dup_dir = Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates"
    items = []
    if dup_dir.is_dir():
        for fp in sorted(dup_dir.glob("*.mp3")):
            try:
                tags = _ID3(str(fp))
                title  = str(tags.get("TIT2", fp.stem)).strip() or fp.stem
                artist = str(tags.get("TPE1", "")).strip()
            except Exception:
                title, artist = fp.stem, ""
            items.append({"filename": fp.name, "title": title, "artist": artist})
    return jsonify({"duplicates": items}), 200


@app.route('/api/duplicates/delete', methods=['POST'])
def delete_duplicate():
    """Permanently delete a file from NeedsReview/Duplicates/."""
    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Invalid filename"}), 400
    target = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates" / filename).resolve()
    allowed = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates").resolve()
    if not str(target).startswith(str(allowed)):
        return jsonify({"error": "Access denied"}), 403
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
    logger.info(f"[duplicates] Deleted: {filename}")
    return jsonify({"deleted": True, "filename": filename}), 200


@app.route('/api/duplicates/keep', methods=['POST'])
def keep_duplicate():
    """Move a duplicate to the main library under a chosen genre."""
    import shutil as _shutil
    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    genre    = (data.get("genre") or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Invalid filename"}), 400
    if not genre:
        return jsonify({"error": "genre required"}), 400

    src = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates" / filename).resolve()
    allowed = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates").resolve()
    if not str(src).startswith(str(allowed)):
        return jsonify({"error": "Access denied"}), 403
    if not src.is_file():
        return jsonify({"error": "File not found"}), 404

    from services.genre_router import normalize_genre, _library_path
    canonical = normalize_genre(genre)
    lib_path  = _library_path(canonical) if canonical else f"Library/{genre}"
    dest_dir  = Path(BASE_DOWNLOAD_DIR) / lib_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    _shutil.move(str(src), str(dest))
    logger.info(f"[duplicates] Kept: {filename} → {lib_path}")
    return jsonify({"moved": True, "filename": filename, "destination": lib_path}), 200


@app.route('/api/retag-catchall-track', methods=['POST'])
def retag_catchall_track():
    """Retry genre classification for one catch-all track using the full fallback chain."""
    data = request.get_json() or {}
    filepath = (data.get("filepath") or "").strip()
    if not filepath:
        return jsonify({"error": "filepath required"}), 400
    full_path = os.path.join(BASE_DOWNLOAD_DIR, filepath) if not os.path.isabs(filepath) else filepath
    if not os.path.isfile(full_path):
        # Fallback: scan the parent folder for a file whose stem starts with the
        # requested stem — catches old title-only filenames when the frontend sends
        # "Title - Artist.mp3" but the file on disk is still "Title.mp3"
        _parent = os.path.dirname(full_path)
        _stem = os.path.splitext(os.path.basename(full_path))[0].lower()
        _fallback = next(
            (os.path.join(_parent, f) for f in os.listdir(_parent)
             if f.lower().endswith(".mp3") and f.lower().startswith(_stem.split(" - ")[0].strip())),
            None,
        ) if os.path.isdir(_parent) else None
        if _fallback and os.path.isfile(_fallback):
            full_path = _fallback
        else:
            return jsonify({"error": "File not found"}), 404
    try:
        from services.gemini_service import identify_audio, GeminiQuotaExceeded
        from services.genre_router import normalize_genre, _library_path, resolve_genre_folder_with_confidence
        import shutil as _shutil
        from mutagen.id3 import ID3

        # Read artist from ID3
        artist_name = ""
        try:
            _id3 = ID3(full_path)
            artist_name = str(_id3.get("TPE1", "")).strip()
        except Exception:
            pass

        genre_path = None
        route_source = "unknown"

        # ── 1. ARTIST_GENRE_OVERRIDE (instant, no API) ──────────────────────
        if artist_name:
            from services.genre_router import normalize_artist_key as _nak
            override = config.ARTIST_GENRE_OVERRIDE.get(_nak(artist_name))
            if override:
                canonical = normalize_genre(override)
                genre_path = _library_path(canonical) if canonical else None
                route_source = "artist_override"

        # Read title from ID3 once (used by multiple fallback steps)
        title_tag = ""
        try:
            title_tag = str(_id3.get("TIT2", "")).strip()
        except Exception:
            pass

        # ── 2. Spotify artist search ─────────────────────────────────────────
        if not genre_path and artist_name and artist_name.lower() not in ("unknown", "electronic", ""):
            try:
                def _sp2_search():
                    s = spotify_service.sp.search(q=artist_name, type="artist", limit=1)
                    its = s.get("artists", {}).get("items", [])
                    aid = its[0]["id"] if its else ""
                    return resolve_genre_folder_with_confidence(aid, artist_name, spotify_service.sp)
                folder, conf, src = _with_timeout(_sp2_search)
                if folder.startswith("Library/") and folder != "Library/Electronic" and conf >= 0.5:
                    genre_path = folder
                    route_source = src
                    logger.info(f"[retag-catchall-track] {Path(full_path).name} → {genre_path} via {src} ({conf:.0%})")
            except Exception as e:
                logger.debug(f"[retag-catchall-track] Spotify artist chain failed for '{artist_name}': {e}")

        # ── 3. Spotify title-only search (works when artist tag is wrong) ────
        if not genre_path and title_tag:
            try:
                def _sp3_search():
                    return spotify_service.sp.search(q=f"track:{title_tag}", type="track", limit=5)
                results = _with_timeout(_sp3_search)
                tracks = results.get("tracks", {}).get("items", [])
                for t in tracks:
                    sp_artist = t.get("artists", [{}])[0].get("name", "")
                    if not sp_artist:
                        continue
                    artist_id = t.get("artists", [{}])[0].get("id", "")
                    folder, conf, src = resolve_genre_folder_with_confidence(
                        artist_id, sp_artist, spotify_service.sp
                    )
                    if folder.startswith("Library/") and folder != "Library/Electronic" and conf >= 0.5:
                        genre_path = folder
                        route_source = f"spotify_title/{src}"
                        # Fix the corrupted artist tag while we're here
                        try:
                            from mutagen.id3 import TPE1
                            _id3["TPE1"] = TPE1(encoding=3, text=sp_artist)
                            _id3.save(full_path)
                        except Exception:
                            pass
                        logger.info(f"[retag-catchall-track] {Path(full_path).name} → {genre_path} via title search (artist corrected to '{sp_artist}')")
                        break
            except Exception as e:
                logger.debug(f"[retag-catchall-track] Spotify title search failed: {e}")

        # ── 4. Last.fm tag lookup (free, no quota) ──────────────────────────
        if not genre_path:
            try:
                from services.lastfm_service import lookup_genre as _lastfm_lookup
                if title_tag and artist_name:
                    raw = _with_timeout(lambda: _lastfm_lookup(title_tag, artist_name))
                    if raw:
                        canonical = normalize_genre(raw)
                        _lp = _library_path(canonical) if canonical else None
                        if _lp and _lp != "Library/Electronic":
                            genre_path = _lp
                            route_source = "lastfm"
                            logger.info(f"[retag-catchall-track] {Path(full_path).name} → {genre_path} via lastfm")
            except Exception as e:
                logger.debug(f"[retag-catchall-track] Last.fm failed: {e}")

        # ── 5. MusicBrainz search (free, no quota) ──────────────────────────
        if not genre_path and title_tag:
            try:
                from services.musicbrainz_service import lookup_by_search as _mb_search
                mb_raw = _with_timeout(lambda: _mb_search(title_tag, artist_name))
                if mb_raw:
                    canonical = normalize_genre(mb_raw)
                    _lp = _library_path(canonical) if canonical else None
                    if _lp and _lp != "Library/Electronic":
                        genre_path = _lp
                        route_source = "musicbrainz"
                        logger.info(f"[retag-catchall-track] {Path(full_path).name} → {genre_path} via musicbrainz")
            except Exception as e:
                logger.debug(f"[retag-catchall-track] MusicBrainz search failed: {e}")

        # ── 6. AcoustID fingerprint (free, no quota if key configured) ───────
        if not genre_path:
            try:
                from services.musicbrainz_service import lookup_by_fingerprint as _mb_fp
                fp_raw = _with_timeout(lambda: _mb_fp(full_path), seconds=15)
                if fp_raw:
                    canonical = normalize_genre(fp_raw)
                    _lp = _library_path(canonical) if canonical else None
                    if _lp and _lp != "Library/Electronic":
                        genre_path = _lp
                        route_source = "acoustid"
                        logger.info(f"[retag-catchall-track] {Path(full_path).name} → {genre_path} via acoustid")
            except Exception as e:
                logger.debug(f"[retag-catchall-track] AcoustID lookup failed: {e}")

        # ── 7. Gemini (last resort — costs daily quota) ──────────────────────
        if not genre_path:
            from services.gemini_service import remaining_quota as _remaining_quota
            if _remaining_quota() == 0:
                return jsonify({"moved": False, "reason": "Could not classify via steps 1-6 and Gemini quota is exhausted"}), 200
            gemini = identify_audio(full_path)
            raw = gemini.get("gemini_genre", "")
            if raw:
                canonical = normalize_genre(raw)
                genre_path = _library_path(canonical) if canonical else None
                route_source = "gemini"

        if not genre_path:
            return jsonify({"moved": False, "reason": f"Could not classify (source={route_source})"}), 200

        dest_dir = Path(BASE_DOWNLOAD_DIR) / genre_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        src_path = Path(full_path)
        dest = dest_dir / src_path.name

        # Already in the correct folder — mark as reviewed so it drops from the catchall
        if src_path.resolve() == dest.resolve():
            try:
                from mutagen.id3 import TXXX as _TXXX
                _tags = ID3(str(src_path))
                _tags.add(_TXXX(encoding=3, desc="catchall_reviewed", text=["1"]))
                _tags.save()
            except Exception:
                pass
            return jsonify({"moved": False, "confirmed": True, "reason": "already in correct folder", "new_folder": genre_path}), 200

        _shutil.move(str(src_path), str(dest))
        try:
            tags = ID3(str(dest))
            tags.delall("TXXX:routing_source")
            tags.save()
        except Exception:
            pass
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                col.update_one(
                    {"final_path": full_path},
                    {"$set": {"final_path": str(dest), "genre_folder": genre_path}},
                )
        except Exception:
            pass
        return jsonify({"moved": True, "new_folder": genre_path, "source": route_source}), 200
    except GeminiQuotaExceeded as e:
        logger.warning(f"[retag-catchall-track] quota exhausted: {e}")
        return jsonify({"moved": False, "reason": str(e), "quota_exhausted": True}), 200
    except Exception as e:
        logger.error(f"[retag-catchall-track] {e}")
        return jsonify({"moved": False, "reason": str(e), "quota_exhausted": False}), 200


@app.route('/api/gemini-quota', methods=['GET'])
def gemini_quota():
    """Return AI classifier status (Groq-backed, effectively unlimited)."""
    try:
        from services.gemini_service import remaining_quota, GEMINI_DAILY_BUDGET
        remaining = remaining_quota()
        return jsonify({"remaining": remaining, "total": GEMINI_DAILY_BUDGET, "exhausted": False})
    except Exception as e:
        return jsonify({"remaining": 0, "total": 0, "exhausted": True, "error": str(e)})


@app.route('/api/move-and-remember', methods=['POST'])
def move_and_remember():
    """Move a track to a chosen genre folder and record the artist→genre memory."""
    import shutil as _shutil_mar
    data = request.get_json() or {}
    filepath = (data.get('filepath') or '').strip()
    genre    = (data.get('genre') or '').strip()
    artist   = (data.get('artist') or '').strip()
    if not filepath or not genre:
        return jsonify({'error': 'filepath and genre required'}), 400
    try:
        from services.genre_router import normalize_genre, GENRE_TAXONOMY
        _display_map = {'Drum & Bass': 'Drum and Bass'}
        canonical = normalize_genre(_display_map.get(genre, genre)) or _display_map.get(genre, genre)
        entry = GENRE_TAXONOMY.get(canonical)
        if not entry:
            return jsonify({'error': f'Unknown genre: {genre}'}), 400
        _, subfolder = entry
        genre_path = f'Library/{subfolder}'
        full_path = filepath if os.path.isabs(filepath) else os.path.join(BASE_DOWNLOAD_DIR, filepath)
        try:
            Path(full_path).resolve().relative_to(Path(BASE_DOWNLOAD_DIR).resolve())
        except ValueError:
            return jsonify({'error': 'Invalid path'}), 400
        src = Path(full_path)
        if not src.exists():
            return jsonify({'error': f'File not found: {filepath}'}), 404
        dest_dir = Path(BASE_DOWNLOAD_DIR) / genre_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        _shutil_mar.move(str(src), str(dest))
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                col.update_one(
                    {'final_path': full_path},
                    {'$set': {'final_path': str(dest), 'genre_folder': genre_path}},
                )
        except Exception:
            pass
        if artist:
            from services.artist_memory_service import record_move as _record_move_mar
            _record_move_mar(artist, canonical, source='manual_move')
            logger.info(f'[move-and-remember] {artist!r} → {canonical} (manual)')
        logger.info(f'[move-and-remember] {src.name} → {genre_path}')
        return jsonify({'moved': True, 'new_folder': genre_path, 'genre': canonical}), 200
    except Exception as e:
        logger.error(f'[move-and-remember] {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/genre-overrides', methods=['GET'])
def get_genre_overrides():
    """List all learned artist→genre mappings from artist_memory."""
    try:
        from database import get_artist_memory_collection
        col = get_artist_memory_collection()
        if col is None:
            return jsonify({'overrides': []}), 200
        docs = list(col.find({}, {'_id': 0}).sort('last_seen', -1).limit(500))
        for d in docs:
            if 'last_seen' in d and hasattr(d['last_seen'], 'isoformat'):
                d['last_seen'] = d['last_seen'].isoformat()
        return jsonify({'overrides': docs}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/genre-override/<artist_key>', methods=['DELETE'])
def delete_genre_override(artist_key):
    """Remove a learned artist→genre mapping."""
    try:
        from services.artist_memory_service import forget_artist as _forget_artist_mar
        deleted = _forget_artist_mar(artist_key)
        return jsonify({'ok': True, 'deleted': deleted}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/catchall-tracks', methods=['GET'])
def get_catchall_tracks():
    """Return all tracks currently sitting in Library/Electronic/ (the catch-all bucket)."""
    from mutagen.id3 import ID3
    from bpm_key_service import tkey_to_camelot as _t2c  # imported once, not per file
    electronic_dir = Path(BASE_DOWNLOAD_DIR) / "Library" / "Electronic"
    items = []
    if electronic_dir.is_dir():
        for fp in sorted(electronic_dir.glob("*.mp3")):
            try:
                tags = ID3(str(fp))
                # Skip files already confirmed as correctly placed
                if str(tags.get("TXXX:catchall_reviewed", "")).strip() == "1":
                    continue
                title = str(tags.get("TIT2", fp.stem)).strip() or fp.stem
                artist = str(tags.get("TPE1", "")).strip()
                conf_tag = tags.get("TXXX:gemini_confidence")
                confidence = float(str(conf_tag).strip()) if conf_tag else 0.0
                bpm_tag = tags.get("TBPM")
                bpm_val = int(float(str(bpm_tag.text[0]).strip())) if bpm_tag and bpm_tag.text else None
                tkey_tag = tags.get("TKEY")
                tkey_str = str(tkey_tag.text[0]).strip() if tkey_tag and tkey_tag.text else ""
                camelot = _t2c(tkey_str)
            except Exception:
                title = fp.stem
                artist = ""
                confidence = 0.0
                bpm_val = None
                camelot = ""
            items.append({
                "title": title,
                "artist": artist,
                "confidence": confidence,
                "suggested_folder": "Library/Electronic",
                "filename": fp.name,
                "id": fp.stem,
                "bpm": bpm_val,
                "camelot_key": camelot,
            })
    return jsonify(items), 200


@app.route('/api/preview-track', methods=['GET'])
def preview_track():
    """Stream an audio file for in-browser preview.

    Query params:
      filename  — bare filename (e.g. "Song - Artist.mp3"), looked up in Library/Electronic/
      path      — relative path from BASE_DOWNLOAD_DIR (e.g. "Library/House/Song.mp3"),
                  used when the file is not in Electronic/
    """
    filename = request.args.get("filename", "").strip()
    rel_path  = request.args.get("path", "").strip()

    if not filename and not rel_path:
        return jsonify({"error": "filename or path required"}), 400

    if rel_path:
        target = Path(BASE_DOWNLOAD_DIR) / rel_path
    else:
        target = Path(BASE_DOWNLOAD_DIR) / "Library" / "Electronic" / filename

    # Security: resolve and ensure it stays inside BASE_DOWNLOAD_DIR
    try:
        resolved = target.resolve()
        base_resolved = Path(BASE_DOWNLOAD_DIR).resolve()
        if not str(resolved).startswith(str(base_resolved)):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_file():
        return jsonify({"error": "File not found"}), 404

    return send_from_directory(
        str(resolved.parent),
        resolved.name,
        mimetype="audio/mpeg",
        conditional=True,  # enables Range request support for audio seeking
    )


@app.route('/api/track/bpm', methods=['POST'])
def update_track_bpm():
    """Manually correct BPM (and optionally key) for a track.

    Body JSON:
      filename   — bare filename (e.g. "Song - Artist.mp3")
      path       — optional relative path from BASE_DOWNLOAD_DIR
      bpm        — corrected BPM value (integer or float)
      key        — optional corrected key string (e.g. "4A")
    """
    data = request.get_json(force=True) or {}
    filename  = str(data.get("filename", "")).strip()
    rel_path  = str(data.get("path", "")).strip()
    bpm_raw   = data.get("bpm")
    key_str   = str(data.get("key", "")).strip() or None

    if not filename and not rel_path:
        return jsonify({"error": "filename or path required"}), 400
    if bpm_raw is None:
        return jsonify({"error": "bpm required"}), 400

    try:
        bpm = float(bpm_raw)
        if bpm <= 0 or bpm > 300:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "bpm must be a positive number ≤ 300"}), 400

    # Locate the file
    if rel_path:
        target = Path(BASE_DOWNLOAD_DIR) / rel_path
    elif filename:
        # Search Library/Electronic first, then scan all library folders
        candidate = Path(BASE_DOWNLOAD_DIR) / "Library" / "Electronic" / filename
        if not candidate.is_file():
            found = list(Path(BASE_DOWNLOAD_DIR).rglob(filename))
            candidate = found[0] if found else candidate
        target = candidate

    # Security: must stay inside BASE_DOWNLOAD_DIR
    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(Path(BASE_DOWNLOAD_DIR).resolve())):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_file():
        return jsonify({"error": "File not found"}), 404

    # Write BPM + key to ID3 tags
    try:
        from bpm_key_service import write_bpm_key_to_tags, persist_audio_features
        write_bpm_key_to_tags(str(resolved), bpm, key_str)
        persist_audio_features(
            resolved.stem,
            {"bpm": bpm, "key": key_str, "analyzed": True, "manual": True},
        )
        logger.info(f"[bpm-update] {resolved.name} → {bpm} BPM · {key_str or 'key unchanged'}")
        return jsonify({"ok": True, "bpm": bpm, "key": key_str, "filename": resolved.name}), 200
    except Exception as e:
        logger.error(f"[bpm-update] Failed for {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/track/tags', methods=['POST'])
def update_track_tags():
    """Edit artist and/or title ID3 tags for any Library file.

    Body JSON:
      path    — relative path from BASE_DOWNLOAD_DIR (e.g. 'Library/Bollywood/Song.mp3')
      artist  — new artist string (optional)
      title   — new title string (optional)
    """
    data     = request.get_json(force=True) or {}
    rel_path = str(data.get("path", "")).strip()
    new_artist = str(data.get("artist", "")).strip() if data.get("artist") is not None else None
    new_title  = str(data.get("title",  "")).strip() if data.get("title")  is not None else None

    if not rel_path:
        return jsonify({"error": "path required"}), 400
    if new_artist is None and new_title is None:
        return jsonify({"error": "artist or title required"}), 400

    target = Path(BASE_DOWNLOAD_DIR) / rel_path
    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(Path(BASE_DOWNLOAD_DIR).resolve())):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        from mutagen.id3 import ID3, TPE1, TIT2, ID3NoHeaderError
        try:
            tags = ID3(str(resolved))
        except ID3NoHeaderError:
            from mutagen.id3 import ID3 as _ID3
            tags = _ID3()

        if new_artist is not None:
            tags["TPE1"] = TPE1(encoding=3, text=[new_artist])
        if new_title is not None:
            tags["TIT2"] = TIT2(encoding=3, text=[new_title])
        tags.save(str(resolved))

        # Sync to MongoDB library_index
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            update = {"last_seen": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)}
            if new_artist is not None:
                update["artist"] = new_artist
            if new_title is not None:
                update["title"] = new_title
            col.update_one({"filename": resolved.name}, {"$set": update})
        except Exception:
            pass

        logger.info(f"[tag-edit] {resolved.name} — artist={new_artist!r} title={new_title!r}")
        return jsonify({"ok": True, "artist": new_artist, "title": new_title, "filename": resolved.name}), 200
    except Exception as e:
        logger.error(f"[tag-edit] {rel_path}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/export/rekordbox', methods=['GET'])
def export_rekordbox():
    """
    Generate a Rekordbox-compatible XML playlist for a library folder.

    Query params:
        folder  — relative path inside BASE_DOWNLOAD_DIR (e.g. Library/Electronic)
                  or "all" to include every .mp3 in the library
        name    — playlist name shown inside Rekordbox (defaults to folder name)
    """
    import xml.etree.ElementTree as _ET
    from mutagen.id3 import ID3 as _ID3
    from mutagen.mp3 import MP3 as _MP3

    folder_param = request.args.get("folder", "all").strip()
    playlist_name = request.args.get("name", "").strip() or folder_param

    base = Path(BASE_DOWNLOAD_DIR).resolve()

    if folder_param.lower() == "all":
        scan_root = base
    else:
        scan_root = (base / folder_param).resolve()
        if not str(scan_root).startswith(str(base)):
            return jsonify({"error": "Access denied"}), 403

    if not scan_root.is_dir():
        return jsonify({"error": "Folder not found"}), 404

    mp3_files = sorted(scan_root.rglob("*.mp3"))

    # Build Rekordbox XML
    root_el = _ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    _ET.SubElement(root_el, "PRODUCT", Name="rekordbox", Version="6.8.5", Company="AlphaTheta")

    collection = _ET.SubElement(root_el, "COLLECTION", Entries=str(len(mp3_files)))

    for idx, mp3_path in enumerate(mp3_files, start=1):
        title, artist, bpm_val, key_val, duration_sec = "", "", "", "", 0
        try:
            tags = _ID3(str(mp3_path))
            title  = str(tags.get("TIT2", mp3_path.stem))
            artist = str(tags.get("TPE1", ""))
            bpm_raw = str(tags.get("TBPM", ""))
            bpm_val = bpm_raw.split(".")[0] if bpm_raw else ""
            key_val = str(tags.get("TKEY", ""))
        except Exception:
            title = mp3_path.stem

        try:
            audio = _MP3(str(mp3_path))
            duration_sec = int(audio.info.length)
        except Exception:
            pass

        # Rekordbox expects file:// URI with forward slashes
        file_uri = "file://localhost/" + str(mp3_path).replace("\\", "/").lstrip("/")

        track_el = _ET.SubElement(
            collection, "TRACK",
            TrackID=str(idx),
            Name=title,
            Artist=artist,
            TotalTime=str(duration_sec),
            Tonality=key_val,
            Location=file_uri,
        )
        if bpm_val:
            track_el.set("AverageBpm", bpm_val)

    # Build genre-grouped playlists (one per Library subfolder)
    from collections import defaultdict as _dd
    genre_map: dict = _dd(list)
    for idx, mp3_path in enumerate(mp3_files, start=1):
        try:
            rel = mp3_path.relative_to(base)
            genre_folder = rel.parts[1] if len(rel.parts) > 1 else "Other"
        except ValueError:
            genre_folder = "Other"
        genre_map[genre_folder].append(str(idx))

    playlists_el = _ET.SubElement(root_el, "PLAYLISTS")
    root_node    = _ET.SubElement(playlists_el, "NODE", Name="ROOT", Type="0",
                                  Count=str(len(genre_map)))
    for genre_name, keys in sorted(genre_map.items()):
        g_node = _ET.SubElement(
            root_node, "NODE",
            Name=genre_name,
            Type="1",
            Entries=str(len(keys)),
            KeyType="0",
        )
        for key in keys:
            _ET.SubElement(g_node, "TRACK", Key=key)

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + _ET.tostring(root_el, encoding="unicode").encode("utf-8")

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in playlist_name).strip()
    filename = f"rekordbox_{safe_name}.xml"

    from flask import Response as _Response
    return _Response(
        xml_bytes,
        mimetype="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route('/api/stop-sync', methods=['POST'])
def stop_sync():
    """Signal the ingest monitor to stop after the current track."""
    import services.auto_downloader as _ad
    _ad.request_stop()
    return jsonify({"ok": True}), 200


@app.route('/api/download/retry', methods=['POST'])
def retry_download():
    """Reset a track's failure count so the next ingest cycle re-attempts it."""
    from services.auto_downloader import (
        _load_failure_counts, _save_failure_counts,
        _load_ingest_history, _save_ingest_history,
    )
    data = request.get_json() or {}
    track_id = (data.get("track_id") or "").strip()
    if not track_id:
        return jsonify({"error": "track_id required"}), 400
    try:
        failures = _load_failure_counts()
        if track_id in failures:
            del failures[track_id]
            _save_failure_counts(failures)
        history = _load_ingest_history()
        history.discard(track_id)
        _save_ingest_history(history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"queued": True, "track_id": track_id}), 200


# ── Maintenance tasks ─────────────────────────────────────────────────────────

_maintenance_lock = threading.Lock()
_maintenance_running = False
_maintenance_active_task = None

_MAINTENANCE_SCRIPTS = {
    "organise":        "master_organise.py",
    "repair_index":    "repair_index.py",
    "backfill_gemini": "backfill_gemini.py",
    "backfill_lastfm": "backfill_lastfm.py",
}

_ANSI_RE = _re.compile(r'\x1b\[[0-9;]*m')
_LOGURU_RE = _re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ \| (DEBUG|INFO)\s+\|')

def _strip_line(raw: str) -> str:
    clean = _ANSI_RE.sub('', raw).rstrip()
    if _LOGURU_RE.match(clean):
        return ''
    return clean


@app.errorhandler(404)
def not_found(e):
    """Handle 404 errors"""
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


@app.errorhandler(500)
def internal_error(e):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {str(e)}")
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


if __name__ == '__main__':
    try:
        logger.info("=" * 50)
        logger.info("Starting Spotify Meta Downloader")
        logger.info(f"Environment: {config.FLASK_ENV}")
        logger.info(f"Debug: {config.DEBUG}")
        logger.info(f"Server: {config.HOST}:{config.PORT}")
        logger.info(f"Celery available: {_celery_available}")
        logger.info(f"Music folder: {BASE_DOWNLOAD_DIR}")

        # Spotify credentials check
        _sp_id = os.getenv("SPOTIPY_CLIENT_ID") or os.getenv("SPOTIFY_CLIENT_ID") or ""
        _sp_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or os.getenv("SPOTIFY_CLIENT_SECRET") or ""
        if _sp_id and _sp_secret:
            logger.info(f"Spotify: configured (client_id ...{_sp_id[-4:]})")
        else:
            logger.warning("Spotify: NOT configured — set SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET")

        # MongoDB reachability check
        try:
            from database import get_db
            _db = get_db()
            _db.command("ping")
            logger.info("MongoDB: reachable")
        except Exception as _db_err:
            logger.warning(f"MongoDB: unreachable — {_db_err}")

        # Redis check
        if _celery_available:
            try:
                import redis as _redis
                _r = _redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
                _r.ping()
                logger.info("Redis: reachable")
            except Exception as _redis_err:
                logger.warning(f"Redis: unreachable — {_redis_err}")
        logger.info("=" * 50)
        
        # Seed download history from existing files on disk
        seed_history_from_disk()

        # Start playlist auto-sync monitor (guarded against duplicate tasks)
        if not getattr(app, '_auto_thread_started', False):
            from services.auto_downloader import playlist_monitor
            socketio.start_background_task(target=playlist_monitor)
            app._auto_thread_started = True
            logger.info("Auto-downloader background task started")

        # CELERY UPGRADE — Start Redis pub/sub bridge if Celery is available
        if _celery_available:
            import threading
            bridge_thread = threading.Thread(target=_redis_pubsub_bridge, daemon=True)
            bridge_thread.start()
            logger.info("Redis pub/sub bridge thread started")

        # NOTIFICATION — Start storage monitor background task
        socketio.start_background_task(target=_storage_monitor)  # NOTIFICATION
        logger.info("Storage monitor background task started")  # NOTIFICATION

        # BACKGROUND SERVICES — metrics indexes + maintenance daemon
        try:
            from services.metrics_service import _ensure_once as _metrics_init
            _metrics_init()
            logger.info("[startup] Metrics service: TTL indexes ensured")
        except Exception as _svc_err:
            logger.warning(f"[startup] Metrics service skipped: {_svc_err}")

        try:
            from services.maintenance_worker import start_maintenance
            start_maintenance()
            # fingerprint_service and reclassification_service run as maintenance tasks
        except Exception as _svc_err:
            logger.warning(f"[startup] Maintenance worker skipped: {_svc_err}")

        # START TELEGRAM BOT
        _telegram_bot_started = False
        try:
            if os.getenv("TELEGRAM_BOT_TOKEN"):
                from telegram_bot import start_bot_thread, TELEGRAM_CHAT_ID

                if not TELEGRAM_CHAT_ID:
                    logger.error("[app] TELEGRAM_CHAT_ID not valid — bot disabled")
                else:
                    try:
                        start_bot_thread()
                        _telegram_bot_started = True
                        logger.success(f"[app] ✅ Telegram bot initialized (chat_id={TELEGRAM_CHAT_ID})")
                    except Exception as bot_err:
                        if "Conflict" in str(bot_err) or "getUpdates" in str(bot_err):
                            logger.warning(f"[app] ⚠️  Telegram bot conflict (another instance running): {bot_err}")
                            logger.info("[app] Continuing without Telegram bot for this session")
                        else:
                            raise
            else:
                logger.warning("[app] ⚠️  TELEGRAM_BOT_TOKEN not set — bot disabled")
        except ImportError as e:
            logger.warning(f"[app] ⚠️  python-telegram-bot not installed: {e}")
        except Exception as e:
            logger.error(f"[app] ❌ Telegram bot init failed: {e}")
            logger.exception(e)

        # Run Flask app with SocketIO
        socketio.run(
            app,
            host=config.HOST,
            port=config.PORT,
            debug=config.DEBUG,
            use_reloader=False,
            allow_unsafe_werkzeug=True
        )
    
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        raise
