"""
Spotify Ingest Playlist Monitor & Downloader
Monitors a single ingest playlist and downloads new tracks with parallel workers.

Uses SpotifyOAuth for playlist access (required since 2025 API changes).

One-time setup:
  1. Add http://127.0.0.1:8888/callback as a Redirect URI in your Spotify
     Developer Dashboard (https://developer.spotify.com/dashboard).
  2. Run: python auto_downloader.py
  3. Authorize in the browser that opens.
  4. After that, the server will auto-sync your ingest playlist.
"""
import json
import os
import sys
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import re
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from config import config
from downloader_service import get_downloader_service, sanitize_filename
from downloader_service import download_queue_status, update_queue, wait_if_manual_active
from spotify_service import is_rate_limited, set_global_rate_limit
from metadata_cache import get_cache

# Use loguru when available, fall back to stdlib logger
try:
    from loguru import logger
except ImportError:
    logger = logging.getLogger(__name__)  # type: ignore[assignment]

INGEST_PLAYLIST_ID = config.INGEST_PLAYLIST_ID
BASE_DOWNLOAD_DIR = config.BASE_DOWNLOAD_DIR
INGEST_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Ingest")


def resolve_folder(artist, base_dir=None, title=None):
    """Resolve download subfolder based on artist, FOLDER_RULES, and language detection.
    Returns the full folder path (e.g. Ingest/Sammy Virji/).
    Falls back to base_dir if no rule matches.
    If title contains Devanagari (Hindi) script -> routes to Bollywood/ subfolder."""
    base = base_dir or INGEST_FOLDER
    rules = config.FOLDER_RULES if hasattr(config, 'FOLDER_RULES') else {}
    artist_lower = artist.lower() if artist else ""
    for pattern, subfolder in rules.items():
        if pattern in artist_lower:
            folder = os.path.join(base, subfolder)
            os.makedirs(folder, exist_ok=True)
            return folder
    # Language detection: Devanagari script -> Bollywood
    text_to_check = f"{title or ''} {artist or ''}"
    if any('\u0900' <= ch <= '\u097F' for ch in text_to_check):
        folder = os.path.join(base, "Bollywood")
        os.makedirs(folder, exist_ok=True)
        return folder
    return base


CHECK_INTERVAL = config.CHECK_INTERVAL
INGEST_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "ingest_tracks.json")
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".spotify_cache")

REDIRECT_URI = config.REDIRECT_URI
# Dynamic workers: min(5, cpu_count), with a floor of 3
MAX_WORKERS = min(5, max(3, os.cpu_count() or 3))

AUTO_STATUS = {
    "status": "idle",
    "current": "",
    "last": "",
    "progress": 0,
    "total": 0,
    "completed": 0,
    "last_checked": "",
    "playlist_total": 0,
    "synced_total": 0,
}

# Thread-safe registry of downloaded file keys
_registry_lock = threading.Lock()
_downloaded_registry: set = set()


# ── SocketIO bridge for real-time events ─────────────────────────────────────
_socketio = None


def set_socketio(sio):
    """Store a reference to the Flask-SocketIO instance for real-time events."""
    global _socketio
    _socketio = sio


def _emit(event, data):
    """Emit a SocketIO event to all connected clients."""
    if _socketio is not None:
        try:
            _socketio.emit(event, data)
            _socketio.sleep(0)
        except Exception:
            pass


def _emit_auto_status():
    """Push current AUTO_STATUS to all clients immediately."""
    _emit("auto_status_update", dict(AUTO_STATUS))


def normalize(text):
    """Normalize a string for consistent duplicate comparison."""
    return " ".join(text.lower().split()).strip()


def _build_file_registry(folder):
    """Scan folder recursively for existing .mp3 files and return a set of normalized names."""
    registry = set()
    if not os.path.isdir(folder):
        return registry
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".mp3"):
                name = f[:-4].strip()  # remove .mp3
                registry.add(normalize(name))
    return registry


def _get_user_sp(interactive=False):
    """Get a Spotify client with user OAuth.
    interactive=True opens the browser for first-time auth.
    interactive=False only works if a cached token exists.
    """
    auth = SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope="playlist-read-private playlist-read-collaborative",
        cache_path=CACHE_PATH,
        open_browser=interactive,
    )
    if not interactive:
        # In daemon mode, only use cached/refreshed token
        token_info = auth.get_cached_token()
        if not token_info:
            return None
    return spotipy.Spotify(auth_manager=auth, retries=0, requests_timeout=10)


def is_authenticated():
    """Check if we have a valid cached OAuth token."""
    return os.path.exists(CACHE_PATH)


def _load_ingest_history():
    try:
        with open(INGEST_HISTORY_FILE, "r") as f:
            return set(json.load(f).get("track_ids", []))
    except Exception:
        return set()


def _save_ingest_history(ids):
    with open(INGEST_HISTORY_FILE, "w") as f:
        json.dump({"track_ids": list(ids), "last_checked": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)


def _extract_retry_seconds(*sources):
    """Extract retry duration from error message or captured stderr."""
    for src in sources:
        msg = str(src)
        m = re.search(r'Retry will occur after:\s*(\d+)', msg)
        if m:
            return int(m.group(1))
        m = re.search(r'Retry-After[:\s]+(\d+)', msg, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def ingest_download(download_dir=None):
    """Download new tracks from the ingest playlist with parallel workers.

    Args:
        download_dir: Optional custom download directory. Falls back to INGEST_FOLDER.
    """
    if not INGEST_PLAYLIST_ID:
        logger.warning("[ingest] No INGEST_PLAYLIST_ID configured. Skipping.")
        return

    # Skip if globally rate-limited
    if is_rate_limited():
        logger.info("[ingest] Skipping — Spotify API rate-limited (cooldown active)")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = "Rate limited — waiting for cooldown"
        return

    target_base = download_dir or INGEST_FOLDER

    from spotify_service import get_spotify_service
    sp_service = get_spotify_service()

    try:
        tracks = sp_service.get_playlist_tracks_by_id(INGEST_PLAYLIST_ID, force_refresh=True)
    except Exception as e:
        logger.error(f"[ingest] Failed to fetch ingest playlist: {e}")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = f"Fetch error: {str(e)[:80]}"
        return

    saved_ids = _load_ingest_history()
    new_tracks = [t for t in tracks if t["id"] not in saved_ids]

    # Update status with playlist totals
    current_ids = {t["id"] for t in tracks}
    AUTO_STATUS["playlist_total"] = len(tracks)
    AUTO_STATUS["synced_total"] = len(saved_ids & current_ids)
    AUTO_STATUS["last_checked"] = time.strftime("%H:%M:%S")

    if not new_tracks:
        logger.info("[ingest] No new tracks in ingest playlist.")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = ""
        _emit_auto_status()
        return

    logger.info(f"[ingest] {len(new_tracks)} new track(s) from ingest playlist")

    downloader = get_downloader_service()
    os.makedirs(target_base, exist_ok=True)

    # Build file registry from existing downloads for instant duplicate skip
    global _downloaded_registry
    with _registry_lock:
        _downloaded_registry = _build_file_registry(target_base)
    logger.info(f"[ingest] Existing files in {os.path.basename(target_base)}: {len(_downloaded_registry)}")

    total = len(new_tracks)
    completed_count = [0]  # mutable counter for threads
    success_count = [0]
    skip_count = [0]
    fail_count = [0]

    AUTO_STATUS["status"] = "downloading"
    AUTO_STATUS["total"] = total
    AUTO_STATUS["completed"] = 0
    AUTO_STATUS["progress"] = 0
    _emit_auto_status()

    def _download_single(track_info):
        """Download a single track with duplicate guard. Thread-safe."""
        # Yield to manual downloads (priority)
        if wait_if_manual_active():
            logger.info("[ingest] Yielded to manual download, resuming")

        tid = track_info["id"]
        title = track_info["title"]
        artist = track_info["artist"]
        track_key = normalize(sanitize_filename(title))

        # Resolve target folder based on artist rules
        target_folder = resolve_folder(artist, base_dir=target_base, title=title)
        os.makedirs(target_folder, exist_ok=True)

        # --- Duplicate check 1: file registry ---
        with _registry_lock:
            if track_key in _downloaded_registry:
                skip_count[0] += 1
                logger.debug(f"[ingest] Skipping (exists): {title} - {artist}")
                _emit("download_skipped", {"title": title, "artist": artist, "reason": "Already downloaded", "source": "ingest"})
                saved_ids.add(tid)
                return

        # --- Duplicate check 2: file on disk ---
        filename = sanitize_filename(title)
        file_path = os.path.join(target_folder, f"{filename}.mp3")
        if os.path.isfile(file_path) and os.path.getsize(file_path) > 1000:
            with _registry_lock:
                _downloaded_registry.add(track_key)
            skip_count[0] += 1
            logger.debug(f"[ingest] Skipping (on disk): {title} - {artist}")
            _emit("download_skipped", {"title": title, "artist": artist, "reason": "File exists on disk", "source": "ingest"})
            saved_ids.add(tid)
            return

        try:
            AUTO_STATUS["current"] = f"{title} - {artist}"
            _emit("download_start", {"title": title, "artist": artist, "source": "ingest"})
            result = downloader.download_track(
                title,
                artist,
                output_dir=target_folder,
                output_filename=filename,
                duration_ms=track_info.get("duration_ms"),
            )

            if result["status"] == "success":
                with _registry_lock:
                    _downloaded_registry.add(track_key)
                success_count[0] += 1
                saved_ids.add(tid)
                logger.info(f"[ingest] Downloaded: {result['filename']}")
                _emit("download_complete", {"title": title, "artist": artist, "status": "completed", "filename": result.get("filename", ""), "source": "ingest"})
            else:
                # Strict match policy — don't save to history so it retries next cycle
                fail_count[0] += 1
                logger.warning(f"[ingest] SKIPPED (no strict match): {title} - {artist} | {result.get('message', '')}")
                _emit("download_error", {"title": title, "artist": artist, "error": result.get("message", "No strict match"), "source": "ingest"})

        except Exception as e:
            fail_count[0] += 1
            logger.error(f"[ingest] Error downloading {title} - {artist}: {str(e)[:150]}")
            _emit("download_error", {"title": title, "artist": artist, "error": str(e)[:100], "source": "ingest"})

        finally:
            completed_count[0] += 1
            pct = int((completed_count[0] / total) * 100)
            AUTO_STATUS["completed"] = completed_count[0]
            AUTO_STATUS["progress"] = pct
            update_queue(completed=completed_count[0], current=f"{title} - {artist}")
            _emit("download_progress", {"title": title, "artist": artist, "current": completed_count[0], "total": total, "percent": pct, "source": "ingest"})
            _emit_auto_status()
            logger.info(f"[ingest] Progress: {completed_count[0]}/{total} ({pct}%)")

    # --- Parallel download ---
    logger.info(f"[ingest] Starting parallel download ({MAX_WORKERS} workers, {total} tracks)")
    pending_names = [f"{t['title']} - {t['artist']}" for t in new_tracks]
    update_queue(total=total, completed=0, pending=pending_names)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_download_single, t): t for t in new_tracks}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                t = futures[future]
                logger.error(f"[ingest] Unhandled error for {t['title']}: {e}")

    # Persist history
    _save_ingest_history(saved_ids)
    AUTO_STATUS["status"] = "completed"
    AUTO_STATUS["current"] = ""
    AUTO_STATUS["progress"] = 100
    AUTO_STATUS["completed"] = total
    AUTO_STATUS["last"] = (f"{success_count[0]} downloaded, {skip_count[0]} skipped, "
                           f"{fail_count[0]} failed (of {total})")
    logger.info(f"[ingest] Sync complete: {success_count[0]} downloaded, "
                f"{skip_count[0]} skipped, {fail_count[0]} failed (total {total})")
    _emit_auto_status()


def playlist_monitor():
    """Main monitor loop — checks the ingest playlist every CHECK_INTERVAL seconds."""
    time.sleep(10)
    if not is_authenticated():
        logger.warning("[ingest] Playlist monitor SKIPPED - no OAuth token. "
                       "Run 'python auto_downloader.py' to authorize.")
        return
    logger.info("[ingest] Playlist monitor started.")
    while True:
        if is_rate_limited():
            logger.info("[ingest] Skipping cycle — Spotify API cooldown active.")
        else:
            try:
                logger.info("[ingest] Checking ingest playlist for new songs...")
                ingest_download()
            except spotipy.exceptions.SpotifyException as e:
                if e.http_status == 429:
                    retry_secs = _extract_retry_seconds(e) or 600
                    set_global_rate_limit(retry_secs)
                    logger.warning(f"[ingest] Spotify 429. Blocked for {retry_secs}s.")
                else:
                    logger.error(f"[ingest] Monitor error: {e}")
            except Exception as e:
                logger.error(f"[ingest] Monitor error: {e}")

        time.sleep(CHECK_INTERVAL)


def manual_refresh(download_dir=None):
    """Trigger a manual ingest refresh (force-fetches from Spotify, bypasses cache).

    Args:
        download_dir: Optional custom download directory.
    """
    if is_rate_limited():
        return {"status": "rate_limited", "message": "Spotify API is rate-limited."}
    try:
        ingest_download(download_dir=download_dir)
        return {"status": "ok", "message": "Ingest refresh triggered."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    # One-time interactive OAuth setup
    print("=" * 50)
    print("Spotify Ingest Playlist - OAuth Setup")
    print("=" * 50)
    print(f"\nRedirect URI: {REDIRECT_URI}")
    print("Make sure this URI is added in your Spotify Developer Dashboard.")
    print("\nOpening browser for authorization...\n")

    try:
        sp = _get_user_sp(interactive=True)
        if sp is None:
            print("ERROR: Authorization failed.")
            sys.exit(1)
        user = sp.current_user()
        print(f"Logged in as: {user['display_name']}")

        if INGEST_PLAYLIST_ID:
            from spotify_service import get_spotify_service
            svc = get_spotify_service()
            tracks = svc.get_playlist_tracks_by_id(INGEST_PLAYLIST_ID, force_refresh=True)
            print(f"Ingest playlist has {len(tracks)} track(s).")
            if tracks:
                print(f"  Latest: {tracks[0]['title']} - {tracks[0]['artist']}")
        else:
            print("WARNING: No INGEST_PLAYLIST_ID configured in .env")

        print("\nOAuth setup complete! The server will now auto-sync your ingest playlist.")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nTroubleshooting:")
        print(f"  1. Add {REDIRECT_URI} as a Redirect URI in your Spotify Dashboard")
        print("  2. Make sure your Spotify app is not in 'development mode' restriction")
        print("  3. Try again: python auto_downloader.py")
        sys.exit(1)
