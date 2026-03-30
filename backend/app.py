"""
Spotify Meta Downloader - Flask Backend Application
Main application entry point
"""
# DISCONNECT FIX: removed eventlet entirely — it's deprecated and causes
# RLock/green-thread conflicts with Celery, Redis, yt-dlp, and ThreadPoolExecutor.
# Using threading async_mode instead (requires simple-websocket for WebSocket support).

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import logging
import os
import threading
import time
from pathlib import Path
from config import config
from spotify_service import get_spotify_service
from downloader_service import get_downloader_service, sanitize_filename
from downloader_service import download_queue_status, update_queue, set_manual_active
from downloader_service import set_socketio as set_downloader_socketio
from auto_downloader import AUTO_STATUS, BASE_DOWNLOAD_DIR, INGEST_PLAYLIST_ID, set_socketio
from auto_downloader import manual_refresh as _manual_refresh
from spotify_service import get_api_usage
from utils import setup_logging, extract_spotify_id

# MUSICBRAINZ — import tagger service
try:  # MUSICBRAINZ
    from tagger_service import tag_file as tagger_tag_file, lookup_musicbrainz, _ensure_tables as tagger_ensure_tables  # MUSICBRAINZ
    _tagger_available = True  # MUSICBRAINZ
except ImportError as _tag_err:  # MUSICBRAINZ
    _tagger_available = False  # MUSICBRAINZ
    logging.getLogger(__name__).warning(f"Tagger service not available: {_tag_err}")  # MUSICBRAINZ

# CELERY UPGRADE — conditional Celery imports (graceful if Redis unavailable)
_celery_available = False
_celery_app = None
try:
    from celery_app import is_redis_available
    if is_redis_available():
        from tasks import download_track_task, sync_playlist_task, retry_failed_task
        from celery_app import celery_app as _celery_app
        _celery_available = True
        logging.getLogger(__name__).info("Celery + Redis detected — task queue enabled")
    else:
        logging.getLogger(__name__).info("Redis not reachable — falling back to threading")
except ImportError:
    logging.getLogger(__name__).info("Celery not installed — falling back to threading")
except Exception as _celery_err:
    logging.getLogger(__name__).warning(f"Celery init error: {_celery_err} — falling back to threading")

# ── Loguru: structured file logging ──────────────────────────────────────────
try:
    from loguru import logger

    _log_dir = Path(__file__).parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    logger.add(
        str(_log_dir / "app.log"),
        rotation="5 MB",
        retention="7 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name} | {message}",
    )
except ImportError:
    logger = setup_logging(__name__, level=logging.INFO)  # type: ignore[assignment]

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

def add_history_entry(title, artist, status, filename=""):
    """Add an entry to download history and emit via WebSocket"""
    entry = {
        "title": title,
        "artist": artist,
        "status": status,
        "filename": filename,
        "timestamp": time.strftime("%H:%M:%S")
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

# SocketIO with CORS — threading async mode (no eventlet)
socketio = SocketIO(  # DISCONNECT FIX: switched from eventlet to threading
    app,
    async_mode="threading",  # DISCONNECT FIX: native threads — no green thread corruption
    cors_allowed_origins=["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173"],
    ping_timeout=300,
    ping_interval=10,
    max_http_buffer_size=1e8,
    logger=False,
    engineio_logger=False,
)
set_socketio(socketio)
set_downloader_socketio(socketio)  # quality_report events

# Enable CORS for all API routes
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173"],  # DISCONNECT FIX: added 127.0.0.1
        "methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        "supports_credentials": False
    }
})

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

# Background task to periodically emit auto-downloader status and queue status
def _auto_status_emitter():
    """Emit auto-downloader status and queue status every 5 seconds"""
    while True:
        time.sleep(5)  # DISCONNECT FIX: use time.sleep instead of socketio.sleep (no eventlet)
        try:
            emit_status()
            socketio.emit("queue_status", download_queue_status)
        except Exception:
            pass

socketio.start_background_task(target=_auto_status_emitter)


@app.route('/', methods=['GET'])
def index():
    """Serve the frontend"""
    return send_from_directory('../frontend-react/dist', 'index.html')


@app.route('/<path:filename>', methods=['GET'])
def serve_frontend(filename):
    """Serve frontend static files (React SPA with client-side routing)"""
    dist_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend-react', 'dist')
    full_path = os.path.join(dist_dir, filename)
    if os.path.isfile(full_path):
        return send_from_directory(dist_dir, filename)
    return send_from_directory(dist_dir, 'index.html')


@app.route('/api/track', methods=['POST'])
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
        
        if "spotify.com" not in url and not url.startswith("spotify:"):
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
            return jsonify({
                "type": "track",
                "title": metadata["title"],
                "artist": metadata["artist"],
                "album": metadata["album"],
                "duration": metadata.get("duration_ms", 0) // 1000 if metadata.get("duration_ms") else 0,
                "source": source,
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
def download_track():
    """
    Download track from Spotify URL
    Runs download in background and returns immediately with 202 Accepted
    """
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
                add_history_entry(title, artist, result["status"], result.get("filename", ""))
            
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
                add_history_entry(title, artist, result["status"], result.get("filename", ""))
            
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
            
            # Route to Artists/{artist}/ folder for manual downloads
            artists_root = os.path.join(os.path.dirname(downloader_service.download_dir), "Artists")
            artist_folder = os.path.join(artists_root, sanitize_filename(artist))
            os.makedirs(artist_folder, exist_ok=True)
            
            # Update global queue for manual download
            update_queue(total=1, completed=0, current=f"{title} - {artist}")
            
            def track_progress_cb(pct, status_text):
                with status_lock:
                    download_status["progress"] = max(10, pct)
                    download_status["current"] = f"{title} - {status_text}"
            
            with status_lock:
                download_status["status"] = "downloading"
                download_status["current"] = f"{title} - {artist}"
                download_status["progress"] = 10
            
            result = downloader_service.download_track(title, artist, progress_callback=track_progress_cb, duration_ms=duration_ms, output_dir=artist_folder, album_art_url=album_art_url)
            
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
            add_history_entry(title, artist, result['status'], result.get('filename', ''))
    
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



@app.route('/api/downloads', methods=['GET'])
def list_downloads():
    """
    Get list of downloaded files
    
    Response:
    {
        "success": true,
        "downloads": ["file1.mp3", "file2.mp3", ...]
    }
    """
    try:
        downloads = downloader_service.get_downloads_list()
        
        return jsonify({
            "success": True,
            "downloads": downloads
        }), 200
    
    except Exception as e:
        logger.error(f"Error listing downloads: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/status', methods=['GET'])
def get_download_status():
    """
    Get current download status and progress
    
    Response:
    {
        "status": "idle|starting|downloading|completed|fallback|failed|busy",
        "progress": 0-100,
        "current": "description of current task"
    }
    """
    with status_lock:
        return jsonify({
            "status": download_status["status"],
            "progress": download_status["progress"],
            "current": download_status["current"],
            "match_quality": download_status.get("match_quality", ""),
        }), 200


@app.route('/api/delete/<filename>', methods=['DELETE'])
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
        result = downloader_service.delete_download(filename)
        
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


@app.route('/api/download_playlist', methods=['POST'])
def download_playlist():
    """
    Download all tracks from a playlist
    
    Request body:
    {
        "tracks": [
            {"title": "Track 1", "artist": "Artist 1", "album": "Album 1"},
            {"title": "Track 2", "artist": "Artist 2", "album": "Album 2"}
        ]
    }
    
    Response:
    {
        "success": true/false,
        "total": 10,
        "successful": 9,
        "failed": 1,
        "downloads": ["file1.mp3", "file2.mp3", ...],
        "errors": ["Track: error message", ...]
    }
    """
    try:
        # Validate request
        if not request.json:
            return jsonify({
                "success": False,
                "error": "Request body must be JSON"
            }), 400
        
        tracks = request.json.get('tracks', [])
        
        if not tracks or not isinstance(tracks, list):
            return jsonify({
                "success": False,
                "error": "tracks must be a non-empty list"
            }), 400
        
        logger.info(f"Received playlist download request for {len(tracks)} tracks")
        
        # Download all tracks
        result = downloader_service.download_playlist(tracks)
        
        status_code = 200 if result['status'] in ('success', 'mixed') else 400
        return jsonify(result), status_code
    
    except Exception as e:
        logger.error(f"Error in download_playlist: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Internal server error: {str(e)}"
        }), 500


@app.route('/api/files', methods=['GET'])
def get_all_files():
    """Get all MP3 files from the download directory tree."""
    return jsonify({"success": True, "files": load_existing_files()}), 200


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


@app.route('/api/ingest-config', methods=['GET'])
def ingest_config():
    """Get ingest playlist configuration"""
    return jsonify({
        "enabled": bool(INGEST_PLAYLIST_ID),
        "playlist_id": INGEST_PLAYLIST_ID or None,
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


@app.route('/api/refresh-playlist', methods=['POST'])
def refresh_playlist():
    """Trigger a manual playlist refresh (force-fetches from Spotify, bypasses cache).
    Accepts optional JSON body: { "download_dir": "/path/to/folder" }
    """
    download_dir = None
    data = request.get_json(silent=True)
    if data and data.get("download_dir"):
        requested = data["download_dir"].strip()
        # Validate: must be an absolute path under a real directory
        if os.path.isabs(requested):
            download_dir = requested
        else:
            return jsonify({"status": "error", "message": "download_dir must be an absolute path"}), 400
    result = _manual_refresh(download_dir=download_dir)
    status_code = 200 if result["status"] == "ok" else 429 if result["status"] == "rate_limited" else 500
    return jsonify(result), status_code


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "message": "Spotify Meta Downloader API is running",
        "celery_available": _celery_available,
    }), 200


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
        logger.info("=" * 50)
        
        # Seed download history from existing files on disk
        seed_history_from_disk()

        # Start playlist auto-sync monitor (guarded against duplicate tasks)
        if not getattr(app, '_auto_thread_started', False):
            from auto_downloader import playlist_monitor
            socketio.start_background_task(target=playlist_monitor)
            app._auto_thread_started = True
            logger.info("Auto-downloader background task started")

        # CELERY UPGRADE — Start Redis pub/sub bridge if Celery is available
        if _celery_available:
            import threading
            bridge_thread = threading.Thread(target=_redis_pubsub_bridge, daemon=True)
            bridge_thread.start()
            logger.info("Redis pub/sub bridge thread started")
        
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
