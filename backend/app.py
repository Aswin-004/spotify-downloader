"""
Spotify Meta Downloader - Flask Backend Application
Main application entry point
"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging
import os
import threading
from pathlib import Path
from config import config
from spotify_service import get_spotify_service
from downloader_service import get_downloader_service, sanitize_filename
from utils import setup_logging, extract_spotify_id

# Setup logging
logger = setup_logging(__name__, level=logging.INFO)

# Download status tracking
download_status = {
    "status": "idle",
    "progress": 0,
    "current": ""
}
active_download = False
status_lock = threading.Lock()  # Prevent race conditions

# Create Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Enable CORS for all API routes
CORS(app, resources={
    r"/api/*": {
        "origins": ["*"],
        "methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        "supports_credentials": False
    }
})

# Get services
spotify_service = get_spotify_service()
downloader_service = get_downloader_service()

@app.route('/', methods=['GET'])
def index():
    """Serve the frontend"""
    return send_from_directory('../frontend', 'index.html')


@app.route('/<path:filename>', methods=['GET'])
def serve_frontend(filename):
    """Serve frontend static files"""
    return send_from_directory('../frontend', filename)


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
        
        if url_info["type"] == "album":
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
            }), 200
        
        else:
            # Single track metadata
            metadata = spotify_service.get_track_metadata(url)
            return jsonify({
                "type": "track",
                "title": metadata["title"],
                "artist": metadata["artist"],
                "album": metadata["album"],
                "duration": metadata.get("duration_ms", 0) // 1000 if metadata.get("duration_ms") else 0
            }), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
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
        
        # Spawn background thread for download
        thread = threading.Thread(
            target=_download_background,
            args=(url,),
            daemon=True
        )
        thread.start()
        
        # Return immediately with 202 Accepted
        return jsonify({"status": "started"}), 202
    
    except Exception as e:
        logger.error(f"Error in download_track: {str(e)}")
        with status_lock:
            active_download = False
        return jsonify({"error": str(e)}), 500


def _download_background(url):
    """
    Background worker for downloading a single track or album.
    Uses yt-dlp progress hooks for real-time progress updates.
    """
    global active_download, download_status
    
    def _progress_cb(percent, status_text):
        """Callback invoked by yt-dlp progress hook"""
        with status_lock:
            download_status["progress"] = percent
            download_status["current"] = status_text
    
    try:
        # Detect URL type
        url_info = extract_spotify_id(url)
        
        if url_info["type"] == "album":
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
            
            with status_lock:
                download_status["status"] = "completed"
                download_status["progress"] = 100
                download_status["current"] = f"Album done: {downloaded}/{total} tracks"
        
        else:
            # ─── Single track download ───
            with status_lock:
                download_status["status"] = "starting"
                download_status["current"] = "Fetching metadata..."
                download_status["progress"] = 5
            
            metadata = spotify_service.get_track_metadata(url)
            title = metadata["title"]
            artist = metadata["artist"]
            
            def track_progress_cb(pct, status_text):
                with status_lock:
                    download_status["progress"] = max(10, pct)
                    download_status["current"] = f"{title} - {status_text}"
            
            with status_lock:
                download_status["status"] = "downloading"
                download_status["current"] = f"{title} - {artist}"
                download_status["progress"] = 10
            
            result = downloader_service.download_track(title, artist, progress_callback=track_progress_cb)
            
            with status_lock:
                if result['status'] == 'success':
                    download_status["status"] = "completed"
                    download_status["progress"] = 100
                    download_status["current"] = result['filename']
                elif result['status'] == 'fallback':
                    download_status["status"] = "fallback"
                    download_status["progress"] = 100
                    download_status["current"] = f"Manual download: {title} - {artist}"
                else:
                    download_status["status"] = "failed"
                    download_status["current"] = "Download failed"
    
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        with status_lock:
            download_status["status"] = "failed"
            download_status["current"] = str(e)[:100]
    
    finally:
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
            "current": download_status["current"]
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


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "message": "Spotify Meta Downloader API is running"
    }), 200


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
        logger.info("=" * 50)
        
        # Run Flask app
        app.run(
            host=config.HOST,
            port=config.PORT,
            debug=config.DEBUG
        )
    
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        raise
