# CELERY UPGRADE
"""
Celery Task Definitions
========================
Wraps existing download logic from downloader_service.py.
Does NOT rewrite any download code — calls existing functions directly.

Each task emits the same Socket.IO events as the original threading
pipeline by obtaining a socketio reference via flask_socketio.

If Redis / Celery is unavailable these tasks are never called;
the app falls back to its original eventlet-based background tasks.
"""
import os
import sys
import logging
import time

# Ensure the backend directory is on sys.path so local imports work
# when the Celery worker is started from the project root.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery_app import celery_app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

# ── Lazy imports (avoid circular / heavy imports at module level) ──────────
_downloader_service = None
_spotify_service = None


def _get_downloader():
    """Lazy-init the downloader service singleton."""
    global _downloader_service
    if _downloader_service is None:
        from downloader_service import get_downloader_service
        _downloader_service = get_downloader_service()
    return _downloader_service


def _get_spotify():
    """Lazy-init the Spotify service singleton."""
    global _spotify_service
    if _spotify_service is None:
        from spotify_service import get_spotify_service
        _spotify_service = get_spotify_service()
    return _spotify_service


def _emit_socketio_event(event: str, data: dict):
    """
    Best-effort Socket.IO emit from within a Celery worker.

    Strategy: push the event payload into a Redis pub/sub channel that
    the Flask process subscribes to and re-emits.  If that channel isn't
    available we try the downloader_service._socketio handle directly
    (works when worker runs in the same process during tests).
    """
    # Try the module-level socketio reference (set by app.py)
    try:
        from downloader_service import _socketio
        if _socketio is not None:
            _socketio.emit(event, data)
            return
    except Exception:
        pass

    # Fallback: publish to Redis so Flask picks it up
    try:
        import json as _json
        import redis as _redis_lib
        from celery_app import REDIS_URL
        r = _redis_lib.Redis.from_url(REDIS_URL, socket_connect_timeout=2)
        payload = _json.dumps({"event": event, "data": data})
        r.publish("socketio_bridge", payload)
    except Exception:
        pass  # Silently degrade — the download still works


# ═══════════════════════════════════════════════════════════════════
# TASK 1: download_track_task
# ═══════════════════════════════════════════════════════════════════

@celery_app.task(
    bind=True,
    name="tasks.download_track_task",
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=60,
    retry_backoff_max=300,
    acks_late=True,
)
def download_track_task(self, track_metadata: dict, output_path: str = None):
    """
    Celery task that wraps DownloaderService.download_track().

    Args:
        track_metadata: dict with keys: title, artist, album (optional),
                        duration_ms (optional), album_art_url (optional)
        output_path: optional output directory override

    Returns:
        quality_report dict (same structure emitted via Socket.IO)
    """
    title = track_metadata.get("title", "")
    artist = track_metadata.get("artist", "")
    album = track_metadata.get("album")
    duration_ms = track_metadata.get("duration_ms")
    album_art_url = track_metadata.get("album_art_url")
    task_id = self.request.id

    # CELERY UPGRADE — emit task_started event
    _emit_socketio_event("task_started", {
        "task_id": task_id,
        "title": title,
        "artist": artist,
    })

    logger.info(f"[task {task_id}] Starting download: {title} – {artist}")

    try:
        ds = _get_downloader()

        # Build progress callback that emits via Socket.IO
        def _progress_cb(pct, status_text):
            _emit_socketio_event("status_update", {
                "download": {
                    "status": "downloading",
                    "progress": pct,
                    "current": f"{title} – {status_text}",
                }
            })

        # Call the EXISTING download_track — no logic rewrite
        result = ds.download_track(
            title,
            artist,
            album=album,
            progress_callback=_progress_cb,
            output_dir=output_path,
            duration_ms=duration_ms,
            album_art_url=album_art_url,
        )

        quality_report = result.get("quality_report", {})

        # Emit the same quality_report event the original pipeline emits
        _emit_socketio_event("quality_report", quality_report)

        logger.info(f"[task {task_id}] Completed: {result.get('status')} — {result.get('filename', '')}")
        return quality_report

    except Exception as exc:
        retry_num = self.request.retries
        # CELERY UPGRADE — emit task_retrying event
        if retry_num < self.max_retries:
            _emit_socketio_event("task_retrying", {
                "task_id": task_id,
                "title": title,
                "artist": artist,
                "attempt": retry_num + 1,
                "max_retries": self.max_retries,
                "error": str(exc)[:200],
            })
            logger.warning(f"[task {task_id}] Retrying ({retry_num + 1}/{self.max_retries}): {exc}")
            raise  # Celery's autoretry will handle it
        else:
            # CELERY UPGRADE — emit task_failed event
            _emit_socketio_event("task_failed", {
                "task_id": task_id,
                "title": title,
                "artist": artist,
                "error": str(exc)[:200],
            })
            logger.error(f"[task {task_id}] All retries exhausted: {exc}")
            raise


# ═══════════════════════════════════════════════════════════════════
# TASK 2: sync_playlist_task
# ═══════════════════════════════════════════════════════════════════

@celery_app.task(
    bind=True,
    name="tasks.sync_playlist_task",
    acks_late=True,
)
def sync_playlist_task(self, playlist_id: str):
    """
    Celery task that wraps playlist sync logic.

    Fetches playlist tracks via SpotifyService and spawns one
    download_track_task.delay() per track.

    Emits "playlist_sync_progress" Socket.IO events.
    """
    task_id = self.request.id
    logger.info(f"[playlist {task_id}] Syncing playlist: {playlist_id}")

    try:
        sp = _get_spotify()
        from downloader_service import sanitize_filename

        # Fetch tracks via existing spotify_service
        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        tracks = sp.get_playlist_tracks(playlist_url)
        total = len(tracks)

        # Try to get playlist name
        playlist_name = "Playlist"
        try:
            user_sp = sp._get_user_sp()
            if user_sp:
                info = user_sp.playlist(playlist_id, fields="name")
                playlist_name = info.get("name", "Playlist")
        except Exception:
            pass

        # Build output folder
        from config import config as _cfg
        base = _cfg.BASE_DOWNLOAD_DIR
        playlists_root = os.path.join(base, "Playlists")
        playlist_folder = os.path.join(playlists_root, sanitize_filename(playlist_name))
        os.makedirs(playlist_folder, exist_ok=True)

        _emit_socketio_event("playlist_sync_progress", {
            "task_id": task_id,
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "total": total,
            "dispatched": 0,
            "status": "starting",
        })

        # Spawn a Celery sub-task per track
        child_task_ids = []
        for i, track in enumerate(tracks):
            meta = {
                "title": track.get("title", ""),
                "artist": track.get("artist", ""),
                "album": track.get("album", ""),
                "duration_ms": track.get("duration_ms"),
                "album_art_url": track.get("album_art_url"),
            }

            child = download_track_task.delay(meta, playlist_folder)
            child_task_ids.append(child.id)

            _emit_socketio_event("playlist_sync_progress", {
                "task_id": task_id,
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "total": total,
                "dispatched": i + 1,
                "status": "dispatching",
                "latest_track": f"{meta['title']} – {meta['artist']}",
            })

        _emit_socketio_event("playlist_sync_progress", {
            "task_id": task_id,
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "total": total,
            "dispatched": total,
            "status": "all_dispatched",
            "child_task_ids": child_task_ids,
        })

        logger.info(f"[playlist {task_id}] Dispatched {total} download tasks")
        return {
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "total": total,
            "child_task_ids": child_task_ids,
        }

    except Exception as exc:
        _emit_socketio_event("task_failed", {
            "task_id": task_id,
            "error": str(exc)[:200],
            "type": "playlist_sync",
        })
        logger.error(f"[playlist {task_id}] Failed: {exc}")
        raise


# ═══════════════════════════════════════════════════════════════════
# TASK 3: retry_failed_task
# ═══════════════════════════════════════════════════════════════════

@celery_app.task(
    bind=True,
    name="tasks.retry_failed_task",
    max_retries=3,
    retry_backoff=60,
)
def retry_failed_task(self, track_id: int):
    """
    Fetch a failed download from the SQLite history table by row id,
    then retry via download_track_task.

    Args:
        track_id: Row ID in download_history table.

    Returns:
        quality_report dict from the retry attempt.
    """
    task_id = self.request.id
    logger.info(f"[retry {task_id}] Retrying failed track id={track_id}")

    try:
        from download_history import get_recent

        # Locate the failed entry
        rows = get_recent(limit=500)
        target = None
        for row in rows:
            if row.get("id") == track_id:
                target = row
                break

        if not target:
            raise ValueError(f"Track id={track_id} not found in download_history")

        meta = {
            "title": target["track_title"],
            "artist": target["artist"],
            "album": target.get("album", ""),
        }

        # Dispatch as a fresh download task
        child = download_track_task.delay(meta)

        logger.info(f"[retry {task_id}] Dispatched child task {child.id} for '{meta['title']}'")
        return {"child_task_id": child.id, "track_id": track_id}

    except Exception as exc:
        _emit_socketio_event("task_failed", {
            "task_id": task_id,
            "error": str(exc)[:200],
            "type": "retry_failed",
        })
        logger.error(f"[retry {task_id}] Failed: {exc}")
        raise
