"""
Automatic Spotify Playlist Sync & Download
Monitors a playlist every 2 minutes and downloads new tracks.
Uses SpotifyOAuth for playlist access (required since 2025 API changes).

One-time setup:
  1. Add http://127.0.0.1:8888/callback as a Redirect URI in your Spotify
     Developer Dashboard (https://developer.spotify.com/dashboard).
  2. Run: python auto_downloader.py
  3. Authorize in the browser that opens.
  4. After that, the server will auto-sync your playlist.
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

logger = logging.getLogger(__name__)

PLAYLIST_ID = config.PLAYLIST_ID
INGEST_PLAYLIST_ID = config.INGEST_PLAYLIST_ID
BASE_DOWNLOAD_DIR = config.BASE_DOWNLOAD_DIR
INGEST_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Ingest")
AUTO_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Auto Downloads")


def resolve_folder(artist, base_dir=None, title=None):
    """Resolve download subfolder based on artist, FOLDER_RULES, and language detection.
    Returns the full folder path (e.g. Auto Downloads/Sammy Virji/).
    Falls back to base_dir if no rule matches.
    If title contains Devanagari (Hindi) script → routes to Bollywood/ subfolder."""
    base = base_dir or AUTO_FOLDER
    rules = config.FOLDER_RULES if hasattr(config, 'FOLDER_RULES') else {}
    artist_lower = artist.lower() if artist else ""
    for pattern, subfolder in rules.items():
        if pattern in artist_lower:
            folder = os.path.join(base, subfolder)
            os.makedirs(folder, exist_ok=True)
            return folder
    # Language detection: Devanagari script → Bollywood
    text_to_check = f"{title or ''} {artist or ''}"
    if any('\u0900' <= ch <= '\u097F' for ch in text_to_check):
        folder = os.path.join(base, "Bollywood")
        os.makedirs(folder, exist_ok=True)
        return folder
    return base
CHECK_INTERVAL = config.CHECK_INTERVAL
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "downloaded_tracks.json")
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".spotify_cache")

REDIRECT_URI = config.REDIRECT_URI
# Dynamic workers: min(5, cpu_count), with a floor of 3
MAX_WORKERS = min(5, max(3, os.cpu_count() or 3))

# Cached playlist snapshot for delta detection
_last_playlist_ids = set()
_playlist_lock = threading.Lock()

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
_downloaded_registry = set()


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


def load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("track_ids", []))
    except Exception:
        return set()


def save_history(track_ids):
    with open(HISTORY_FILE, "w") as f:
        json.dump({
            "track_ids": list(track_ids),
            "last_checked": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, f, indent=2)


def get_playlist_tracks(sp):
    """Fetch ALL tracks from the playlist with pagination and added_at timestamps."""
    if sp is None:
        raise Exception("No authenticated Spotify client. Run 'python auto_downloader.py' to authorize.")

    # --- Debug: verify authenticated user ---
    user = None
    try:
        user = sp.current_user()
        logger.info(f"[auto] Logged in as: {user['display_name']} ({user['id']})")
    except Exception as e:
        logger.warning(f"[auto] Could not verify current user: {e}")

    # --- Verify playlist access ---
    try:
        playlist_meta = sp.playlist(PLAYLIST_ID, fields="name,owner(display_name,id),public")
        owner = playlist_meta["owner"]
        logger.info(f"[auto] Playlist: {playlist_meta.get('name')} (owner: {owner['display_name']}, public: {playlist_meta.get('public')})")
        if user and user["id"] != owner["id"] and not playlist_meta.get("public"):
            logger.warning(f"[auto] You ({user['id']}) are not the playlist owner ({owner['id']}). "
                  "Private playlists require owner login or a collaborative invite.")
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 403:
            raise Exception(
                f"403 Forbidden: Cannot access playlist {PLAYLIST_ID}. "
                "The playlist is private or your account lacks access. "
                "Make it public or login with the correct account."
            )
        raise
    except Exception:
        pass  # non-critical

    # --- Fetch tracks with 403 guard ---
    try:
        results = sp.playlist_items(
            PLAYLIST_ID,
            limit=100,
            additional_types=["track"],
        )
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 403:
            raise Exception(
                f"403 Forbidden: Cannot read tracks from playlist {PLAYLIST_ID}. "
                "Playlist is private or access denied. Make it public or login with the correct account."
            )
        raise

    tracks = []
    while results:
        for item in results["items"]:
            if not item:
                continue
            # Spotify API now uses "item" key; fall back to "track" for compat
            track = item.get("item") or item.get("track")
            if track and track.get("id") and track.get("type") == "track":
                tracks.append({
                    "id": track["id"],
                    "title": track.get("name", ""),
                    "artist": track["artists"][0]["name"] if track.get("artists") else "Unknown",
                    "added_at": item.get("added_at", ""),
                    "duration_ms": track.get("duration_ms"),
                })
        if results.get("next"):
            results = sp.next(results)
        else:
            break
    # Newest additions first
    tracks.sort(key=lambda x: x["added_at"], reverse=True)
    logger.info(f"[auto] Playlist fetched: {len(tracks)} track(s)")
    return tracks


def get_new_tracks(sp, force_refresh=False):
    """Compare playlist against history; return only delta (new) tracks.
    Uses metadata cache for playlist snapshots to reduce API calls.
    Only fetches from Spotify if cache is stale (>30 min) or force_refresh=True."""
    global _last_playlist_ids
    cache = get_cache()

    # Use cached snapshot if fresh and not forced
    if not force_refresh and cache.is_snapshot_fresh(PLAYLIST_ID, max_age=1800):
        cached_tracks = cache.get_playlist_snapshot(PLAYLIST_ID)
        if cached_tracks:
            tracks = cached_tracks
            logger.info(f"[auto] Using cached snapshot ({len(tracks)} tracks, age={cache.get_snapshot_age(PLAYLIST_ID):.0f}s)")
        else:
            tracks = get_playlist_tracks(sp)
            cache.set_playlist_snapshot(PLAYLIST_ID, tracks)
    else:
        tracks = get_playlist_tracks(sp)
        cache.set_playlist_snapshot(PLAYLIST_ID, tracks)

    saved_ids = load_history()

    current_ids = {t["id"] for t in tracks}

    # Update AUTO_STATUS with playlist totals
    AUTO_STATUS["playlist_total"] = len(tracks)
    AUTO_STATUS["synced_total"] = len(saved_ids & current_ids)
    AUTO_STATUS["last_checked"] = time.strftime("%H:%M:%S")

    # Delta detection: only consider tracks that weren't in the last snapshot
    with _playlist_lock:
        if _last_playlist_ids:
            delta_ids = current_ids - _last_playlist_ids
            if delta_ids:
                logger.info(f"[auto] Delta detection: {len(delta_ids)} new since last check")
        _last_playlist_ids = current_ids

    logger.info(f"[auto] Playlist: {len(tracks)} total, {len(saved_ids)} saved")

    new_tracks = [t for t in tracks if t["id"] not in saved_ids]

    logger.info(f"[auto] New tracks to download: {len(new_tracks)}")
    return new_tracks


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


def auto_download_new_tracks(force_refresh=False):
    # Skip entirely if globally rate-limited
    if is_rate_limited():
        logger.info("[auto] Skipping — Spotify API rate-limited (cooldown active)")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = "Rate limited — waiting for cooldown"
        return

    sp = _get_user_sp(interactive=False)
    if sp is None:
        logger.warning("[auto] No OAuth token. Run 'python auto_downloader.py' to authorize.")
        return

    # Validate token before proceeding
    try:
        sp.current_user()
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 429:
            # Retry seconds will be extracted at playlist_monitor level via stderr capture
            retry_secs = _extract_retry_seconds(e) or 600
            set_global_rate_limit(retry_secs)
            logger.warning(f"[auto] Spotify 429 during token validation. Blocked for {retry_secs}s.")
            AUTO_STATUS["status"] = "idle"
            AUTO_STATUS["current"] = f"Rate limited — blocked for {retry_secs}s"
            return
        logger.warning("[auto] Token expired, clearing cache for re-auth")
        try:
            os.remove(CACHE_PATH)
        except OSError:
            pass
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = "Auth expired - run auto_downloader.py"
        return
    except Exception:
        logger.warning("[auto] Token expired, clearing cache for re-auth")
        try:
            os.remove(CACHE_PATH)
        except OSError:
            pass
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = "Auth expired - run auto_downloader.py"
        return

    AUTO_STATUS["status"] = "checking"
    AUTO_STATUS["current"] = "Checking playlist..."
    new_tracks = get_new_tracks(sp, force_refresh=force_refresh)

    if not new_tracks:
        logger.info("[auto] No new songs in playlist.")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = ""
        return

    downloader = get_downloader_service()
    os.makedirs(AUTO_FOLDER, exist_ok=True)
    saved_ids = load_history()

    # Build file registry from existing downloads for instant duplicate skip
    global _downloaded_registry
    with _registry_lock:
        _downloaded_registry = _build_file_registry(AUTO_FOLDER)
    logger.info(f"[auto] Existing files in Auto Downloads: {len(_downloaded_registry)}")

    total = len(new_tracks)
    completed_count = [0]  # mutable counter for threads
    success_count = [0]
    skip_count = [0]
    fail_count = [0]

    AUTO_STATUS["status"] = "downloading"
    AUTO_STATUS["total"] = total
    AUTO_STATUS["completed"] = 0
    AUTO_STATUS["progress"] = 0

    # Metadata already included from playlist_items — no extra API calls needed
    tracks_meta = new_tracks
    logger.info(f"[auto] {len(tracks_meta)} tracks ready for download")

    def _download_single(track_info):
        """Download a single track with duplicate guard. Thread-safe."""
        # Yield to manual downloads (priority 0 > auto priority 1)
        if wait_if_manual_active():
            logger.info("[auto] Yielded to manual download, resuming")

        tid = track_info["id"]
        title = track_info["title"]
        artist = track_info["artist"]
        track_key = normalize(f"{sanitize_filename(title)}")

        # Resolve target folder based on artist rules
        target_folder = resolve_folder(artist, title=title)

        # --- Duplicate check 1: file registry ---
        with _registry_lock:
            if track_key in _downloaded_registry:
                skip_count[0] += 1
                logger.debug(f"[auto] Skipping (exists): {title} - {artist}")
                saved_ids.add(tid)
                return

        # --- Duplicate check 2: file on disk ---
        filename = sanitize_filename(title)
        file_path = os.path.join(target_folder, f"{filename}.mp3")
        if os.path.isfile(file_path) and os.path.getsize(file_path) > 1000:
            with _registry_lock:
                _downloaded_registry.add(track_key)
            skip_count[0] += 1
            logger.debug(f"[auto] Skipping (on disk): {title} - {artist}")
            saved_ids.add(tid)
            return

        try:
            AUTO_STATUS["current"] = f"{title} - {artist}"
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
                logger.info(f"[auto] Downloaded: {result['filename']}")
            else:
                fail_count[0] += 1
                logger.warning(f"[auto] Fallback for: {title}")

        except Exception as e:
            logger.error(f"[auto] Error downloading {title}: {e}")

        finally:
            completed_count[0] += 1
            pct = int((completed_count[0] / total) * 100)
            AUTO_STATUS["completed"] = completed_count[0]
            AUTO_STATUS["progress"] = pct
            # Update global queue status
            update_queue(completed=completed_count[0], current=f"{title} - {artist}")
            logger.info(f"[auto] Progress: {completed_count[0]}/{total} ({pct}%)")

    # --- Parallel download ---
    logger.info(f"[auto] Starting parallel download ({MAX_WORKERS} workers, {len(tracks_meta)} tracks)")
    # Update global queue status
    pending_names = [f"{t['title']} - {t['artist']}" for t in tracks_meta]
    update_queue(total=total, completed=0, pending=pending_names)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_download_single, tm): tm for tm in tracks_meta}
        for future in as_completed(futures):
            # Propagate any unhandled exception for logging
            try:
                future.result()
            except Exception as e:
                tm = futures[future]
                logger.error(f"[auto] Unhandled error for {tm['title']}: {e}")

    # Persist history — only tracks that were actually downloaded or skipped (on disk)
    save_history(saved_ids)
    AUTO_STATUS["status"] = "completed"
    AUTO_STATUS["current"] = ""
    AUTO_STATUS["progress"] = 100
    AUTO_STATUS["completed"] = total
    AUTO_STATUS["last"] = (f"{success_count[0]} downloaded, {skip_count[0]} skipped, "
                           f"{fail_count[0]} failed (of {total})")
    logger.info(f"[auto] Sync complete: {success_count[0]} downloaded, "
          f"{skip_count[0]} skipped, {fail_count[0]} failed (total {total})")


# ═══════════════════════ INGEST PLAYLIST ═══════════════════════

INGEST_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "ingest_tracks.json")


def _load_ingest_history():
    try:
        with open(INGEST_HISTORY_FILE, "r") as f:
            return set(json.load(f).get("track_ids", []))
    except Exception:
        return set()


def _save_ingest_history(ids):
    with open(INGEST_HISTORY_FILE, "w") as f:
        json.dump({"track_ids": list(ids), "last_checked": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)


def ingest_download():
    """Download new tracks from the ingest playlist (public, no OAuth needed)."""
    if not INGEST_PLAYLIST_ID:
        return

    from spotify_service import get_spotify_service
    sp_service = get_spotify_service()

    try:
        tracks = sp_service.get_playlist_tracks_by_id(INGEST_PLAYLIST_ID)
    except Exception as e:
        logger.error(f"[ingest] Failed to fetch ingest playlist: {e}")
        return

    saved_ids = _load_ingest_history()
    new_tracks = [t for t in tracks if t["id"] not in saved_ids]

    if not new_tracks:
        logger.info("[ingest] No new tracks in ingest playlist.")
        return

    logger.info(f"[ingest] {len(new_tracks)} new track(s) from ingest playlist")
    downloader = get_downloader_service()
    os.makedirs(INGEST_FOLDER, exist_ok=True)

    # Build file registry from existing ingest downloads for dedup
    ingest_registry = _build_file_registry(INGEST_FOLDER)
    logger.info(f"[ingest] Existing files in Ingest: {len(ingest_registry)}")

    for t in new_tracks:
        title = t["title"]
        artist = t["artist"]
        filename = sanitize_filename(title)
        track_key = normalize(filename)

        # Dedup: skip if already downloaded
        if track_key in ingest_registry:
            logger.debug(f"[ingest] Skipping (exists): {title} - {artist}")
            saved_ids.add(t["id"])
            continue

        target = resolve_folder(artist, base_dir=INGEST_FOLDER, title=title)
        try:
            result = downloader.download_track(
                title, artist,
                output_dir=target,
                output_filename=filename,
                duration_ms=t.get("duration_ms"),
            )
            if result["status"] == "success":
                logger.info(f"[ingest] Downloaded: {title} - {artist}")
                saved_ids.add(t["id"])
            else:
                logger.warning(f"[ingest] Failed (will retry next cycle): {title} - {artist}")
        except Exception as e:
            logger.error(f"[ingest] Error: {title} - {e}")

    _save_ingest_history(saved_ids)
    logger.info("[ingest] Ingest sync complete.")


def playlist_monitor():
    time.sleep(10)
    if not is_authenticated():
        logger.warning("[auto] Playlist monitor SKIPPED - no OAuth token. Run 'python auto_downloader.py' to authorize.")
        return
    logger.info("[auto] Playlist monitor started.")
    while True:
        # Skip cycle if globally rate-limited
        if is_rate_limited():
            logger.info("[auto] Skipping cycle — Spotify API cooldown active.")
        else:
            import io as _io, sys as _sys
            _captured = _io.StringIO()
            _old_stderr = _sys.stderr
            _sys.stderr = _captured
            try:
                logger.info("[auto] Checking playlist for new songs...")
                auto_download_new_tracks()
            except spotipy.exceptions.SpotifyException as e:
                _sys.stderr = _old_stderr
                stderr_out = _captured.getvalue()
                if stderr_out:
                    _sys.stderr.write(stderr_out)
                if e.http_status == 429:
                    retry_secs = _extract_retry_seconds(e, stderr_out) or 600
                    set_global_rate_limit(retry_secs)
                    logger.warning(f"[auto] Spotify 429. Blocked for {retry_secs}s.")
                else:
                    logger.error(f"[auto] Monitor error: {e}")
                _captured = None  # prevent finally from double-restoring
            except Exception as e:
                logger.error(f"[auto] Monitor error: {e}")
            finally:
                if _captured is not None:
                    _sys.stderr = _old_stderr
                    stderr_text = _captured.getvalue()
                    if stderr_text:
                        _sys.stderr.write(stderr_text)

            # Ingest playlist
            if not is_rate_limited():
                try:
                    ingest_download()
                except Exception as e:
                    logger.error(f"[ingest] Monitor error: {e}")

        time.sleep(CHECK_INTERVAL)


def manual_refresh():
    """Trigger a manual playlist refresh (force-fetches from Spotify, bypasses cache)."""
    if is_rate_limited():
        return {"status": "rate_limited", "message": "Spotify API is rate-limited."}
    try:
        sp = _get_user_sp(interactive=False)
        if sp is None:
            return {"status": "error", "message": "No OAuth token. Run auto_downloader.py to authorize."}
        auto_download_new_tracks(force_refresh=True)
        return {"status": "ok", "message": "Manual refresh triggered."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    # One-time interactive OAuth setup
    print("=" * 50)
    print("Spotify Playlist Auto-Sync - OAuth Setup")
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

        # Quick test: fetch playlist tracks
        tracks = get_playlist_tracks(sp)
        print(f"Playlist has {len(tracks)} track(s).")
        if tracks:
            t = sp.track(tracks[0]["id"])
            print(f"  Latest: {t['name']} - {t['artists'][0]['name']}")
        print("\nOAuth setup complete! The server will now auto-sync this playlist.")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nTroubleshooting:")
        print(f"  1. Add {REDIRECT_URI} as a Redirect URI in your Spotify Dashboard")
        print("  2. Make sure your Spotify app is not in 'development mode' restriction")
        print("  3. Try again: python auto_downloader.py")
        sys.exit(1)
