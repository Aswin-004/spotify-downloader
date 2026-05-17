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
import shutil
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure backend/ is on sys.path so config/database/utils resolve
# regardless of whether this file is run directly or imported as a module.
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import re
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from config import config
from services.downloader_service import get_downloader_service, sanitize_filename
from services.downloader_service import download_queue_status, update_queue, wait_if_manual_active
from services.spotify_service import is_rate_limited, set_global_rate_limit
from services.metadata_cache import get_cache


# Use loguru when available, fall back to stdlib logger
try:
    from loguru import logger
except ImportError:
    logger = logging.getLogger(__name__)  # type: ignore[assignment]

INGEST_PLAYLIST_ID = config.INGEST_PLAYLIST_ID
BASE_DOWNLOAD_DIR = config.BASE_DOWNLOAD_DIR
INGEST_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Ingest")
STAGING_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Ingest", "Staging")


CHECK_INTERVAL = config.CHECK_INTERVAL
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
INGEST_HISTORY_FILE = str(_BACKEND_ROOT / "ingest_tracks.json")
INGEST_FAILURES_FILE = str(_BACKEND_ROOT / "ingest_failures.json")  # PERMANENT SKIP
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".spotify_cache")
MAX_FAIL_ATTEMPTS = 3  # PERMANENT SKIP — skip track permanently after this many failures

REDIRECT_URI = config.REDIRECT_URI
# DISCONNECT FIX: cap at 2 workers — 5 parallel yt-dlp+FFmpeg processes
# flood Socket.IO with events and exhaust the eventlet hub, causing disconnects
MAX_WORKERS = 2  # DISCONNECT FIX

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
_in_progress_registry: set = set()  # keys currently being downloaded — TOCTOU guard
_download_semaphore = threading.Semaphore(2)  # DISCONNECT FIX: limit concurrent yt-dlp processes


# ── SocketIO bridge for real-time events ─────────────────────────────────────
_socketio = None


def set_socketio(sio):
    """Store a reference to the Flask-SocketIO instance for real-time events."""
    global _socketio
    _socketio = sio


# DISCONNECT FIX: rate-limit _emit per event to avoid flooding Socket.IO.
# Per-event thresholds let auto_status_update fire ~10x more often than
# download_progress so the UI stays responsive without drowning the socket.
_last_emit_times = {}  # DISCONNECT FIX
_emit_lock = threading.Lock()  # DISCONNECT FIX
_EMIT_THROTTLE = {
    "download_progress":  0.3,
    "auto_status_update": 0.1,
}

def _emit(event, data):
    """Emit a SocketIO event to all connected clients."""
    if _socketio is not None:
        try:
            now = time.time()  # DISCONNECT FIX
            threshold = _EMIT_THROTTLE.get(event, 0.3)
            with _emit_lock:  # DISCONNECT FIX
                last = _last_emit_times.get(event, 0)  # DISCONNECT FIX
                if now - last < threshold:  # DISCONNECT FIX
                    return  # DISCONNECT FIX
                _last_emit_times[event] = now  # DISCONNECT FIX
            _socketio.emit(event, data)
            # DISCONNECT FIX: removed _socketio.sleep(0) — calling it from
            # ThreadPoolExecutor worker threads corrupts the eventlet hub
            # and directly causes WebSocket disconnections under parallel load.
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


def remove_tracks_from_history(track_ids: list) -> dict:
    """Remove specific track IDs from ingest history so they re-download."""
    saved_ids = _load_ingest_history()
    before = len(saved_ids)
    saved_ids -= set(track_ids)
    _save_ingest_history(saved_ids)
    removed = before - len(saved_ids)
    logger.info(f"[ingest] Removed {removed} track ID(s) from history")
    return {"removed": removed, "remaining": len(saved_ids)}


# PERMANENT SKIP — Persistent failure counter
def _load_failure_counts():  # PERMANENT SKIP
    """Load {track_id: failure_count} from disk."""
    try:  # PERMANENT SKIP
        with open(INGEST_FAILURES_FILE, "r") as f:  # PERMANENT SKIP
            return json.load(f)  # PERMANENT SKIP
    except Exception:  # PERMANENT SKIP
        return {}  # PERMANENT SKIP


def _save_failure_counts(counts):  # PERMANENT SKIP
    """Persist {track_id: failure_count} to disk."""
    with open(INGEST_FAILURES_FILE, "w") as f:  # PERMANENT SKIP
        json.dump(counts, f, indent=2)  # PERMANENT SKIP


def _record_failure(tid, title, artist, failure_counts):  # PERMANENT SKIP
    """Increment failure count for a track. Returns True if permanently skipped."""
    failure_counts[tid] = failure_counts.get(tid, 0) + 1  # PERMANENT SKIP
    count = failure_counts[tid]  # PERMANENT SKIP
    if count >= MAX_FAIL_ATTEMPTS:  # PERMANENT SKIP
        logger.warning(f"[ingest] PERMANENTLY SKIPPED ({count}/{MAX_FAIL_ATTEMPTS} failures): {title} - {artist}")  # PERMANENT SKIP
        # NOTIFICATION — Permanent skip
        try:  # NOTIFICATION
            from services.notifications_service import notify_download_failure  # NOTIFICATION
            notify_download_failure(  # NOTIFICATION
                track={'name': title, 'artists': [{'name': artist}]},  # NOTIFICATION
                attempt=count,  # NOTIFICATION
                error=f"Permanently skipped after {count} failed attempts",  # NOTIFICATION
            )  # NOTIFICATION
        except Exception:  # NOTIFICATION
            pass  # NOTIFICATION
        return True  # PERMANENT SKIP
    logger.info(f"[ingest] Failure {count}/{MAX_FAIL_ATTEMPTS} for: {title} - {artist}")  # PERMANENT SKIP
    return False  # PERMANENT SKIP


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


def _write_retry_manifest(staged_path: str, intended_dest: str, track_info: dict,
                          pipeline_stage_failed: str = "move"):
    """Write a JSON manifest so a failed atomic move can be replayed without data loss.

    The staged file is intentionally left in Staging/ — it is NEVER deleted on
    move failure.  Manifests live in  Ingest/.retry_queue/<ts>_<id>.json  and
    are auto-replayed by _process_retry_queue() at the start of each ingest cycle.

    Fields added for Phase 9 retry hardening:
      sha256               — hex digest of staged file (integrity verification)
      filesize_bytes       — byte size at failure time
      pipeline_stage_failed — "download" | "tagging" | "move"
      retry_count          — how many times this manifest has been attempted
    """
    retry_dir = Path(STAGING_FOLDER).parent / ".retry_queue"
    try:
        retry_dir.mkdir(exist_ok=True)
        # Compute sha256 of the staged file for integrity verification on replay
        sha256_hex = ""
        filesize = 0
        try:
            import hashlib as _hl
            h = _hl.sha256()
            with open(staged_path, "rb") as _f:
                for _chunk in iter(lambda: _f.read(65536), b""):
                    h.update(_chunk)
            sha256_hex = h.hexdigest()
            filesize = os.path.getsize(staged_path)
        except Exception:
            pass
        manifest = {
            "staged_path": staged_path,
            "intended_dest": intended_dest,
            "spotify_id": track_info.get("id", ""),
            "artist": track_info.get("artist", ""),
            "title": track_info.get("title", ""),
            "duration_ms": track_info.get("duration_ms", 0),
            "sha256": sha256_hex,
            "filesize_bytes": filesize,
            "pipeline_stage_failed": pipeline_stage_failed,
            "retry_count": 0,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        retry_file = retry_dir / f"{int(time.time())}_{track_info.get('id', 'unknown')}.json"
        retry_file.write_text(json.dumps(manifest, indent=2))
        logger.info(f"[ingest] Retry manifest written: {retry_file.name}")
    except Exception as _e:
        logger.warning(f"[ingest] Failed to write retry manifest: {_e}")


def _process_retry_queue():
    """
    Replay pending retry manifests from .retry_queue/.

    Called at the start of each ingest_download cycle.  Attempts to move
    any staged files whose manifests are present.  On success the manifest
    is deleted; on failure the retry_count is incremented.  Manifests that
    have failed >= MAX_RETRY_ATTEMPTS times are moved to .retry_queue/dead/
    so they don't block the queue indefinitely.
    """
    MAX_RETRY_ATTEMPTS = 5
    retry_dir = Path(STAGING_FOLDER).parent / ".retry_queue"
    if not retry_dir.is_dir():
        return
    manifests = list(retry_dir.glob("*.json"))
    if not manifests:
        return
    logger.info(f"[ingest] Retry queue: {len(manifests)} pending manifest(s)")
    for mf in manifests:
        try:
            data = json.loads(mf.read_text())
        except Exception as _e:
            logger.warning(f"[ingest] Bad retry manifest {mf.name}: {_e}")
            continue

        staged = data.get("staged_path", "")
        dest = data.get("intended_dest", "")
        if not staged or not dest:
            mf.unlink(missing_ok=True)
            continue

        if not os.path.isfile(staged):
            logger.warning(f"[ingest] Retry manifest {mf.name}: staged file missing — discarding")
            mf.unlink(missing_ok=True)
            continue

        # Integrity check: verify sha256 if available
        expected_sha = data.get("sha256", "")
        if expected_sha:
            try:
                import hashlib as _hl
                h = _hl.sha256()
                with open(staged, "rb") as _f:
                    for _chunk in iter(lambda: _f.read(65536), b""):
                        h.update(_chunk)
                if h.hexdigest() != expected_sha:
                    logger.error(f"[ingest] Retry {mf.name}: sha256 mismatch — file corrupted, discarding")
                    mf.unlink(missing_ok=True)
                    continue
            except Exception:
                pass  # skip integrity check on error

        retry_count = data.get("retry_count", 0)
        if retry_count >= MAX_RETRY_ATTEMPTS:
            dead_dir = retry_dir / "dead"
            dead_dir.mkdir(exist_ok=True)
            mf.rename(dead_dir / mf.name)
            logger.warning(f"[ingest] Retry {mf.name}: exceeded {MAX_RETRY_ATTEMPTS} attempts — moved to dead/")
            continue

        # Attempt atomic move
        try:
            dest_dir = os.path.dirname(dest)
            os.makedirs(dest_dir, exist_ok=True)
            _staged_st = os.stat(staged)
            _dest_st = os.stat(dest_dir)
            if _staged_st.st_dev == _dest_st.st_dev:
                os.replace(staged, dest)
            else:
                shutil.copy2(staged, dest)
                if os.path.getsize(dest) != os.path.getsize(staged):
                    os.remove(dest)
                    raise OSError("Cross-device copy size mismatch on retry")
                os.remove(staged)
            logger.info(f"[ingest] Retry succeeded: {os.path.basename(dest)}")
            mf.unlink(missing_ok=True)
        except Exception as _re:
            logger.warning(f"[ingest] Retry {mf.name} attempt {retry_count + 1} failed: {_re}")
            data["retry_count"] = retry_count + 1
            mf.write_text(json.dumps(data, indent=2))


def ingest_download(download_dir=None, force_folder=None, force_redownload=False):
    """Download new tracks from the ingest playlist with parallel workers.

    Args:
        download_dir: Optional custom download directory. Falls back to INGEST_FOLDER.
        force_folder: Optional per-batch folder override. When set, every track
            in this sync is routed to ``{download_dir}/{force_folder}/``,
            bypassing the genre router entirely. Ephemeral — not persisted
            across monitor cycles.
        force_redownload: When True, bypass both the ingest_tracks.json history
            filter and the in-memory file registry dedup — every playlist
            track is treated as new and sent through the downloader. The
            ``PERMANENT_SKIP`` filter (tracks with >= MAX_FAIL_ATTEMPTS prior
            failures) is still applied to avoid replaying known-broken tracks.
            Ephemeral — not persisted across monitor cycles.
    """
    if not INGEST_PLAYLIST_ID:
        logger.warning("[ingest] No INGEST_PLAYLIST_ID configured. Skipping.")
        return

    # RETRY HARDENING — replay any pending manifests from previous failed moves
    try:
        _process_retry_queue()
    except Exception as _rq_err:
        logger.warning(f"[ingest] Retry queue processing failed: {_rq_err}")

    # Skip if globally rate-limited
    if is_rate_limited():
        logger.info("[ingest] Skipping — Spotify API rate-limited (cooldown active)")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = "Rate limited — waiting for cooldown"
        return

    target_base = download_dir or INGEST_FOLDER

    # FORCE FOLDER — log override so ingest logs make routing obvious
    if force_folder:
        logger.info(f"[ingest] FORCE FOLDER active: all tracks -> {os.path.join(target_base, force_folder)}")
    # FORCE REDOWNLOAD — log override so ingest logs make dedup-bypass obvious
    if force_redownload:
        logger.info("[ingest] FORCE REDOWNLOAD active: bypassing history + registry dedup")

    from services.spotify_service import get_spotify_service
    sp_service = get_spotify_service()

    try:
        tracks = sp_service.get_playlist_tracks_by_id(INGEST_PLAYLIST_ID, force_refresh=True)
    except Exception as e:
        logger.error(f"[ingest] Failed to fetch ingest playlist: {e}")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = f"Fetch error: {str(e)[:80]}"
        return

    saved_ids = _load_ingest_history()
    if force_redownload:
        # FORCE REDOWNLOAD — bypass ingest_tracks.json history filter
        new_tracks = list(tracks)
    else:
        new_tracks = [t for t in tracks if t["id"] not in saved_ids]

    # PERMANENT SKIP — Load failure counts and filter out permanently failed tracks
    failure_counts = _load_failure_counts()  # PERMANENT SKIP
    pre_filter = len(new_tracks)  # PERMANENT SKIP
    new_tracks = [t for t in new_tracks if failure_counts.get(t["id"], 0) < MAX_FAIL_ATTEMPTS]  # PERMANENT SKIP
    skipped_permanent = pre_filter - len(new_tracks)  # PERMANENT SKIP
    if skipped_permanent > 0:  # PERMANENT SKIP
        logger.info(f"[ingest] {skipped_permanent} track(s) permanently skipped (>{MAX_FAIL_ATTEMPTS} failures)")  # PERMANENT SKIP

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
    os.makedirs(STAGING_FOLDER, exist_ok=True)

    # Build file registry from ALL download directories (including organized folders)
    # to prevent re-downloading tracks that were already organized into subfolders.
    global _downloaded_registry
    with _registry_lock:
        _downloaded_registry = _build_file_registry(BASE_DOWNLOAD_DIR)
    logger.info(f"[ingest] Existing files across all folders: {len(_downloaded_registry)}")

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

    def _download_single(track_info, force_folder=None, force_redownload=False):
        """Download a single track with two-pass staging system. Thread-safe.

        Pass 1: Download to Staging/ folder
        Pass 2: Determine final genre folder using MusicBrainz tags (best),
                Spotify genres (fallback), or Uncategorized (last resort)
        Pass 3: Move from Staging/ to final folder

        Args:
            track_info: Spotify track dict (id, title, artist, artist_id, duration_ms).
            force_folder: Optional per-batch folder override passed down from
                ``ingest_download``. When set, the genre router is bypassed
                and every track is pinned to ``{target_base}/{force_folder}/``.
            force_redownload: When True, skip the ``_downloaded_registry``
                dedup check so the track is processed even if an MP3 with the
                same normalized name already exists somewhere under
                ``BASE_DOWNLOAD_DIR``. Used together with ``force_folder`` to
                land a previously-downloaded track in a different subfolder.
        """
        from services.genre_router import map_genre_string, resolve_genre_folder
        from services.organizer_service import clean_folder_name

        # Yield to manual downloads (priority)
        if wait_if_manual_active():
            logger.info("[ingest] Yielded to manual download, resuming")

        tid = track_info["id"]
        title = track_info["title"]
        artist = track_info["artist"]
        # DEDUP — strong identity: spotify_id primary, content-hash secondary
        try:
            from services.dedup_service import duplicate_identity_key as _dik
            track_key = _dik(tid, title, artist, track_info.get("duration_ms"))
        except Exception:
            track_key = normalize(sanitize_filename(title))  # legacy fallback

        # --- Duplicate check 1: file registry (atomic check-and-reserve) ---
        # FORCE REDOWNLOAD — skip the registry dedup so redownloads aren't swallowed
        _reserved = False
        if not force_redownload:
            with _registry_lock:
                if track_key in _downloaded_registry or track_key in _in_progress_registry:
                    skip_count[0] += 1
                    logger.debug(f"[ingest] Skipping (exists/in-progress): {title} - {artist}")
                    _emit("download_skipped", {"title": title, "artist": artist, "reason": "Already downloaded or in progress", "source": "ingest"})
                    saved_ids.add(tid)
                    return
                _in_progress_registry.add(track_key)  # reserve the slot — no other worker can claim it
                _reserved = True

        filename = sanitize_filename(title)

        try:
            AUTO_STATUS["current"] = f"{title} - {artist}"
            _emit("download_start", {"title": title, "artist": artist, "source": "ingest"})

            # Throttled per-track progress callback for real-time UI updates
            _last_pct = [0]
            _last_track_emit = [0.0]  # DISCONNECT FIX: time-based throttle too

            def _track_progress_cb(pct, status_text):
                now = time.time()  # DISCONNECT FIX
                # Only emit when progress changes by >= 2% AND at most every 0.5s
                if (abs(pct - _last_pct[0]) >= 2 or pct >= 100) and (now - _last_track_emit[0] >= 0.5 or pct >= 100):  # DISCONNECT FIX
                    _last_pct[0] = pct
                    _last_track_emit[0] = now  # DISCONNECT FIX
                    _emit("download_track_progress", {
                        "title": title,
                        "artist": artist,
                        "percent": pct,
                        "status_text": status_text,
                        "source": "ingest",
                    })

            # PASS 1: Always download to staging first
            staging_dir = STAGING_FOLDER
            os.makedirs(staging_dir, exist_ok=True)

            with _download_semaphore:  # DISCONNECT FIX: limit concurrent yt-dlp processes
                result = downloader.download_track(
                    title,
                    artist,
                    progress_callback=_track_progress_cb,
                    output_dir=staging_dir,
                    output_filename=filename,
                    duration_ms=track_info.get("duration_ms"),
                )

            if result["status"] == "success":
                staged_filepath = result.get("filepath") or os.path.join(staging_dir, result.get("filename", ""))

                # Verify the file actually exists on disk before marking success
                if not os.path.isfile(staged_filepath) or os.path.getsize(staged_filepath) < 1000:
                    logger.error(f"[ingest] Download reported success but file missing/empty: {staged_filepath}")
                    fail_count[0] += 1
                    _record_failure(tid, title, artist, failure_counts)
                    _emit("download_error", {"title": title, "artist": artist, "error": "File missing after download", "source": "ingest"})
                    return

                # PASS 2: Determine final folder using best available genre
                # Tracks below NEEDS_REVIEW_THRESHOLD confidence land in
                # NeedsReview/<artist>/ for manual inspection instead of being
                # silently mis-filed.
                NEEDS_REVIEW_THRESHOLD = 0.5  # configurable per-run if needed

                if force_folder:
                    # Manual override always wins — flat, no artist subfolder
                    final_folder = os.path.join(target_base, force_folder)
                else:
                    from services.genre_router import resolve_genre_folder_with_confidence
                    # Try MusicBrainz genre first (conf=0.9 — most accurate)
                    mb_genre = (result.get("tagging_report") or {}).get("genre", "")
                    if mb_genre:
                        mapped = map_genre_string(mb_genre)
                        if mapped:
                            # CANONICAL-BASE: map_genre_string now returns Library/ paths;
                            # anchor to BASE_DOWNLOAD_DIR to prevent Ingest/Library/ nesting.
                            final_folder = os.path.join(BASE_DOWNLOAD_DIR, mapped, clean_folder_name(artist))
                            logger.info(f"[ingest] MB genre routing: {title} → {mapped}/{artist} (conf=0.9)")
                        else:
                            # No map match → NeedsReview (never Uncategorized)
                            final_folder = os.path.join(BASE_DOWNLOAD_DIR, "NeedsReview", clean_folder_name(artist))
                    else:
                        # Spotify artist genres with confidence scoring
                        folder_structure, genre_confidence, genre_source = resolve_genre_folder_with_confidence(
                            artist_id=track_info.get("artist_id", ""),
                            artist_name=artist,
                            sp=sp_service.sp,
                        )
                        if genre_confidence >= NEEDS_REVIEW_THRESHOLD:
                            # CANONICAL-BASE: folder_structure is a Library/ path;
                            # anchor to BASE_DOWNLOAD_DIR, not INGEST_FOLDER.
                            final_folder = os.path.join(BASE_DOWNLOAD_DIR, folder_structure)
                            logger.info(f"[ingest] Genre routing: {title} → {folder_structure} "
                                        f"(conf={genre_confidence:.2f}, src={genre_source})")
                        else:
                            # Low-confidence decision — route to NeedsReview for manual triage
                            _clean_artist = clean_folder_name(artist)
                            final_folder = os.path.join(BASE_DOWNLOAD_DIR, "NeedsReview", _clean_artist)
                            logger.warning(
                                f"[ingest] Low-confidence genre ({genre_confidence:.2f}, src={genre_source}) "
                                f"for {title} — routing to NeedsReview/{_clean_artist}"
                            )
                            _emit("download_needs_review", {
                                "title": title, "artist": artist,
                                "genre_source": genre_source,
                                "confidence": genre_confidence,
                                "suggested_folder": folder_structure,
                                "source": "ingest",
                            })

                os.makedirs(final_folder, exist_ok=True)

                # PASS 3: Move from staging to final folder
                final_filepath = os.path.join(final_folder, result.get("filename", f"{filename}.mp3"))

                # Collision handling
                if os.path.exists(final_filepath):
                    base = Path(final_filepath).stem
                    n = 1
                    while os.path.exists(os.path.join(final_folder, f"{base}_{n}.mp3")):
                        n += 1
                    final_filepath = os.path.join(final_folder, f"{base}_{n}.mp3")

                # Atomic move — os.replace (same device) or copy+verify+delete (cross-device).
                # On failure: staged file is preserved and a retry manifest is written.
                try:
                    _staged_st = os.stat(staged_filepath)
                    _dest_st = os.stat(final_folder)
                    if _staged_st.st_dev == _dest_st.st_dev:
                        os.replace(staged_filepath, final_filepath)
                    else:
                        shutil.copy2(staged_filepath, final_filepath)
                        if (not os.path.isfile(final_filepath) or
                                os.path.getsize(final_filepath) != os.path.getsize(staged_filepath)):
                            if os.path.exists(final_filepath):
                                os.remove(final_filepath)
                            raise OSError("Cross-device copy verification failed (size mismatch)")
                        os.remove(staged_filepath)
                    if not os.path.isfile(final_filepath) or os.path.getsize(final_filepath) < 1000:
                        raise OSError(f"Post-move integrity check failed: {final_filepath}")
                except OSError as _move_err:
                    logger.error(f"[ingest] Move failed for '{title}': {_move_err}")
                    _write_retry_manifest(staged_filepath, final_filepath, track_info)
                    fail_count[0] += 1
                    _record_failure(tid, title, artist, failure_counts)
                    _emit("download_error", {"title": title, "artist": artist, "error": f"Move failed: {str(_move_err)[:80]}", "source": "ingest"})
                    return

                logger.info(f"[ingest] Moved: {result.get('filename')} → {final_folder}")

                # Update result with final path
                result["filepath"] = final_filepath

                with _registry_lock:
                    _downloaded_registry.add(track_key)
                success_count[0] += 1
                saved_ids.add(tid)
                logger.info(f"[ingest] Downloaded: {result['filename']}")
                # LIBRARY INDEX — register in MongoDB for persistent O(1) dedup
                try:
                    from database import index_track as _idx
                    from services.dedup_service import content_hash as _ch
                    _genre_folder = os.path.relpath(final_folder, BASE_DOWNLOAD_DIR)
                    _idx(
                        identity_key=track_key,
                        spotify_id=tid,
                        content_hash=_ch(title, artist, track_info.get("duration_ms")),
                        title=title,
                        artist=artist,
                        filename=result.get("filename", ""),
                        final_path=final_filepath,
                        genre_folder=_genre_folder,
                    )
                except Exception as _idx_err:
                    logger.warning(f"[ingest] library_index write failed: {_idx_err}")
                _emit("download_complete", {"title": title, "artist": artist, "status": "completed", "filename": result.get("filename", ""), "source": "ingest"})

                # Post-processing warning (file downloaded but tagging/organizing failed)
                if result.get("warning"):
                    logger.warning(f"[ingest] Post-processing warning: {result.get('warning')}")
            else:
                fail_count[0] += 1
                permanently_skipped = _record_failure(tid, title, artist, failure_counts)  # PERMANENT SKIP
                if permanently_skipped:  # PERMANENT SKIP
                    saved_ids.add(tid)  # PERMANENT SKIP — mark as done so it never retries
                logger.warning(f"[ingest] FAILED (auto-download failed): {title} - {artist} | {result.get('message', '')}")
                _emit("download_error", {"title": title, "artist": artist, "error": result.get("message", "No strict match"), "source": "ingest"})

        except Exception as e:
            fail_count[0] += 1
            permanently_skipped = _record_failure(tid, title, artist, failure_counts)  # PERMANENT SKIP
            if permanently_skipped:  # PERMANENT SKIP
                saved_ids.add(tid)  # PERMANENT SKIP — mark as done so it never retries
            logger.error(f"[ingest] Error downloading {title} - {artist}: {str(e)[:150]}")
            _emit("download_error", {"title": title, "artist": artist, "error": str(e)[:100], "source": "ingest"})

        finally:
            if _reserved:  # release in-progress reservation so the slot is freed for future cycles
                with _registry_lock:
                    _in_progress_registry.discard(track_key)
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
    _ingest_start_time = time.time()  # NOTIFICATION — track elapsed time

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_download_single, t, force_folder, force_redownload): t for t in new_tracks}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                t = futures[future]
                logger.error(f"[ingest] Unhandled error for {t['title']}: {e}")

    # Persist history
    _save_ingest_history(saved_ids)
    _save_failure_counts(failure_counts)  # PERMANENT SKIP — persist failure counts to disk

    # Clean up Staging folder — move any leftover files to Uncategorized
    try:
        staging = Path(STAGING_FOLDER)
        if staging.exists():
            leftover = list(staging.glob("*.mp3"))
            if leftover:
                logger.warning(f"[ingest] {len(leftover)} file(s) left in Staging/ — moving to Uncategorized/")
                uncategorized = Path(target_base) / "Uncategorized"
                uncategorized.mkdir(exist_ok=True)
                for f in leftover:
                    shutil.move(str(f), str(uncategorized / f.name))
    except Exception as e:
        logger.warning(f"[ingest] Staging cleanup failed: {e}")
    AUTO_STATUS["status"] = "completed"
    AUTO_STATUS["current"] = ""
    AUTO_STATUS["progress"] = 100
    AUTO_STATUS["completed"] = total
    AUTO_STATUS["last"] = (f"{success_count[0]} downloaded, {skip_count[0]} skipped, "
                           f"{fail_count[0]} failed (of {total})")
    logger.info(f"[ingest] Sync complete: {success_count[0]} downloaded, "
                f"{skip_count[0]} skipped, {fail_count[0]} failed (total {total})")
    _emit_auto_status()

    # NOTIFICATION — Playlist sync complete
    try:  # NOTIFICATION
        from services.notifications_service import notify_playlist_complete  # NOTIFICATION
        _elapsed = time.time() - _ingest_start_time  # NOTIFICATION
        # NOTIFICATION — Calculate storage used by target folder
        _storage_bytes = 0  # NOTIFICATION
        if os.path.isdir(target_base):  # NOTIFICATION
            for _root, _dirs, _files in os.walk(target_base):  # NOTIFICATION
                for _f in _files:  # NOTIFICATION
                    _storage_bytes += os.path.getsize(os.path.join(_root, _f))  # NOTIFICATION
        notify_playlist_complete(  # NOTIFICATION
            playlist_name="Ingest Playlist",  # NOTIFICATION
            stats={  # NOTIFICATION
                'success': success_count[0],  # NOTIFICATION
                'failed': fail_count[0],  # NOTIFICATION
                'total': total,  # NOTIFICATION
                'duration_seconds': _elapsed,  # NOTIFICATION
                'storage_mb': _storage_bytes / (1024 * 1024),  # NOTIFICATION
            },  # NOTIFICATION
        )  # NOTIFICATION
    except Exception as _notif_err:  # NOTIFICATION
        logger.error(f"Notification error: {_notif_err}")  # NOTIFICATION


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


def manual_refresh(download_dir=None, force_folder=None, force_redownload=False):
    """Trigger a manual ingest refresh (force-fetches from Spotify, bypasses cache).

    Args:
        download_dir: Optional custom download directory.
        force_folder: Optional per-trigger folder override. When set, every
            track in this sync lands in ``{download_dir}/{force_folder}/``,
            bypassing the genre router entirely. Ephemeral — not persisted.
        force_redownload: When True, bypass the ingest history filter and the
            in-memory file registry dedup so every playlist track is processed
            as new. Typically set automatically when ``force_folder`` is used,
            so previously-downloaded tracks actually land in the pinned folder.
    """
    if is_rate_limited():
        return {"status": "rate_limited", "message": "Spotify API is rate-limited."}
    try:
        ingest_download(
            download_dir=download_dir,
            force_folder=force_folder,
            force_redownload=force_redownload,
        )
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
            from services.spotify_service import get_spotify_service
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
