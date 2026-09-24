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

_USER_CONFIG_PATH = Path(__file__).parent.parent / "user_config.json"

def _load_user_config() -> dict:
    try:
        return json.loads(_USER_CONFIG_PATH.read_text())
    except Exception:
        return {}

_uc = _load_user_config()
INGEST_PLAYLIST_ID = _uc.get("ingest_playlist_id") or config.INGEST_PLAYLIST_ID
BASE_DOWNLOAD_DIR = config.BASE_DOWNLOAD_DIR
INGEST_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Ingest")
STAGING_FOLDER = os.path.join(BASE_DOWNLOAD_DIR, "Ingest", "Staging")


CHECK_INTERVAL = config.CHECK_INTERVAL
# A check now costs ONE Spotify request (services/playlist_delta.py), but the app was locked out of Spotify for
# ~22 h at far lower traffic than a full re-read every minute, so however small CHECK_INTERVAL is set it is never
# honoured below this (seconds).
MIN_POLL_SECONDS = 600     # 10 minutes: at most ~144 requests a day; "Sync now" checks instantly when you want


def effective_poll_interval(configured=None) -> int:
    """CHECK_INTERVAL (seconds), raised to MIN_POLL_SECONDS if it is smaller or unreadable.
    Reads the live setting each time, so a change on the Settings page takes effect without a restart."""
    value = getattr(config, "CHECK_INTERVAL", CHECK_INTERVAL) if configured is None else configured
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 600
    return max(value, MIN_POLL_SECONDS)


_BACKEND_ROOT = Path(__file__).resolve().parent.parent
INGEST_HISTORY_FILE = str(_BACKEND_ROOT / "ingest_tracks.json")
INGEST_FAILURES_FILE = str(_BACKEND_ROOT / "ingest_failures.json")  # PERMANENT SKIP
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".spotify_oauth_cache")
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
_failure_count_lock = threading.Lock()
_downloaded_registry: set = set()
_in_progress_registry: set = set()  # keys currently being downloaded — TOCTOU guard
_download_semaphore = threading.Semaphore(2)  # DISCONNECT FIX: limit concurrent yt-dlp processes

# Stop-sync flag — set by /api/stop-sync to cancel mid-run
_stop_requested = threading.Event()

def request_stop() -> None:
    """Signal the ingest monitor to stop after the current track."""
    _stop_requested.set()
    logger.info("[ingest] Stop requested by user")

def clear_stop() -> None:
    """Clear the stop flag (called at start of each sync cycle)."""
    _stop_requested.clear()


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
    interactive=False only works if a cached token exists and is refreshable.

    NOTE: Reads the cache directly to bypass spotipy's strict scope comparison.
    Any valid cached token (regardless of which scopes it was issued for) is
    accepted — Spotify read operations work fine with write-scope tokens.
    """
    import time, json as _json

    if not interactive:
        if not os.path.exists(CACHE_PATH):
            return None
        try:
            with open(CACHE_PATH) as _f:
                token_info = _json.load(_f)
            if not token_info.get("access_token"):
                return None
            # Refresh if expired
            if token_info.get("expires_at", 0) <= time.time():
                logger.info("[ingest] OAuth token expired, attempting refresh...")
                auth = SpotifyOAuth(
                    client_id=config.SPOTIFY_CLIENT_ID,
                    client_secret=config.SPOTIFY_CLIENT_SECRET,
                    redirect_uri=REDIRECT_URI,
                    scope="playlist-read-private playlist-read-collaborative "
                          "playlist-modify-public playlist-modify-private",
                    cache_path=CACHE_PATH,
                    open_browser=False,
                )
                try:
                    token_info = auth.refresh_access_token(token_info["refresh_token"])
                    logger.info("[ingest] OAuth token refreshed successfully")
                except Exception as e:
                    logger.error(f"[ingest] Failed to refresh OAuth token: {e}")
                    return None
            return spotipy.Spotify(auth=token_info["access_token"],
                                   retries=0, requests_timeout=10)
        except Exception as e:
            logger.warning(f"[ingest] Could not load cached token: {e}")
            return None

    # Interactive: full browser-based OAuth flow
    auth = SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope="playlist-read-private playlist-read-collaborative "
              "playlist-modify-public playlist-modify-private",
        cache_path=CACHE_PATH,
        open_browser=True,
    )
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
    tmp = INGEST_HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"track_ids": list(ids), "last_checked": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)
    os.replace(tmp, INGEST_HISTORY_FILE)


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
    tmp = INGEST_FAILURES_FILE + ".tmp"  # PERMANENT SKIP
    with open(tmp, "w") as f:  # PERMANENT SKIP
        json.dump(counts, f, indent=2)  # PERMANENT SKIP
    os.replace(tmp, INGEST_FAILURES_FILE)  # PERMANENT SKIP


def _record_failure(tid, title, artist, failure_counts):  # PERMANENT SKIP
    """Increment failure count for a track. Returns True if permanently skipped."""
    with _failure_count_lock:  # PERMANENT SKIP
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


# ── INDEX RECOVERY (P0) — hardening for silent library_index write failures ──
# Distinct from the retry queue above: that queue is specifically for MOVE
# failures, where the staged file has NOT yet reached its final destination.
# An index-write failure is the opposite shape — the download succeeded and
# the file IS already sitting at final_path; only the library_index write
# failed. Reusing _write_retry_manifest/_process_retry_queue for this would be
# unsafe: _process_retry_queue() discards any manifest whose staged_path no
# longer exists, which is *always* true here (the staged file was already
# moved away by the time an index-write failure can even occur). A separate,
# minimal queue avoids that mismatch instead of overloading the move-retry one.

INDEX_QUEUE_DIR = Path(STAGING_FOLDER).parent / ".index_queue"
MAX_INDEX_RETRY_ATTEMPTS = 5


def _write_index_recovery_manifest(identity_key: str, spotify_id: str, title: str,
                                    artist: str, filename: str, final_path: str,
                                    genre_folder: str, genre_confidence: float,
                                    duration_ms=None):
    """Persist enough to safely retry a failed library_index write later.

    The downloaded file itself is never touched by this — it only records what
    index_track() needs in order to be called again. Safe to replay any number
    of times: index_track() is an upsert keyed on identity_key (the collection's
    only unique index), so a replay can never create a duplicate index document.
    """
    try:
        INDEX_QUEUE_DIR.mkdir(exist_ok=True)
        manifest = {
            "identity_key": identity_key,
            "spotify_id": spotify_id or "",
            "title": title or "",
            "artist": artist or "",
            "filename": filename or "",
            "final_path": final_path or "",
            "genre_folder": genre_folder or "",
            "genre_confidence": genre_confidence or 0.0,
            "duration_ms": duration_ms,
            "attempt_count": 0,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        mf_name = f"{int(time.time())}_{spotify_id or 'unknown'}.json"
        (INDEX_QUEUE_DIR / mf_name).write_text(json.dumps(manifest, indent=2))
        logger.info(f"[ingest] Index-recovery manifest written: {mf_name}")
    except Exception as _e:
        logger.warning(f"[ingest] Failed to write index-recovery manifest: {_e}")


def _process_index_queue():
    """
    Replay pending index-recovery manifests from .index_queue/.

    Called at the start of each ingest_download cycle, alongside
    _process_retry_queue(). For each manifest:
      - if final_path no longer exists on disk, discard it — there is nothing
        left to safely index (the file may have been manually moved/removed).
      - if identity_key is already indexed, discard it — already resolved,
        whether by a prior replay that crashed before cleanup, a manual
        repair, or any other writer.
      - otherwise, retry index_track() with the recorded fields.

    Manifests that still fail after MAX_INDEX_RETRY_ATTEMPTS are moved to
    .index_queue/dead/, mirroring the existing retry-queue convention — never
    deleted, so a persistently-failing case stays inspectable.
    """
    if not INDEX_QUEUE_DIR.is_dir():
        return
    manifests = list(INDEX_QUEUE_DIR.glob("*.json"))
    if not manifests:
        return
    logger.info(f"[ingest] Index-recovery queue: {len(manifests)} pending manifest(s)")

    from database import index_track as _idx, is_indexed as _is_indexed
    from services.dedup_service import content_hash as _ch

    for mf in manifests:
        try:
            data = json.loads(mf.read_text())
        except Exception as _e:
            logger.warning(f"[ingest] Bad index-recovery manifest {mf.name}: {_e}")
            continue

        identity_key = data.get("identity_key", "")
        final_path = data.get("final_path", "")
        if not identity_key or not final_path:
            mf.unlink(missing_ok=True)
            continue

        if not os.path.isfile(final_path):
            logger.warning(
                f"[ingest] Index-recovery {mf.name}: final file missing "
                f"({final_path}) — discarding, nothing left to index"
            )
            mf.unlink(missing_ok=True)
            continue

        try:
            if _is_indexed(identity_key):
                logger.info(f"[ingest] Index-recovery {mf.name}: already indexed — discarding")
                mf.unlink(missing_ok=True)
                continue
        except Exception as _chk_err:
            logger.warning(f"[ingest] Index-recovery {mf.name}: is_indexed() check failed: {_chk_err}")
            # Fall through and attempt the upsert anyway — it's safe even if
            # the track turns out to already be indexed (upsert, not insert).

        attempt_count = data.get("attempt_count", 0)
        if attempt_count >= MAX_INDEX_RETRY_ATTEMPTS:
            dead_dir = INDEX_QUEUE_DIR / "dead"
            dead_dir.mkdir(exist_ok=True)
            mf.rename(dead_dir / mf.name)
            logger.warning(
                f"[ingest] Index-recovery {mf.name}: exceeded {MAX_INDEX_RETRY_ATTEMPTS} "
                f"attempts — moved to dead/"
            )
            continue

        try:
            _idx(
                identity_key=identity_key,
                spotify_id=data.get("spotify_id", ""),
                content_hash=_ch(data.get("title", ""), data.get("artist", ""), data.get("duration_ms")),
                title=data.get("title", ""),
                artist=data.get("artist", ""),
                filename=data.get("filename", ""),
                final_path=final_path,
                genre_folder=data.get("genre_folder", ""),
                genre_confidence=data.get("genre_confidence", 0.0),
            )
            logger.info(f"[ingest] Index-recovery succeeded: {mf.name} ({identity_key})")
            mf.unlink(missing_ok=True)
        except Exception as _re:
            logger.warning(
                f"[ingest] Index-recovery {mf.name} attempt {attempt_count + 1} failed: "
                f"{type(_re).__name__}: {_re}"
            )
            data["attempt_count"] = attempt_count + 1
            mf.write_text(json.dumps(data, indent=2))


def _requeue_done(track_id, title, artist, why):
    """Drop a track from the requeue list once its re-download is finished (or moot)."""
    try:
        from services.requeue_service import remove_from_requeue, key_for_track
        if remove_from_requeue(key_for_track(track_id, title, artist)):
            logger.info(f"[requeue] done ({why}): {title} - {artist}")
    except Exception as exc:
        logger.debug(f"[requeue] could not update the queue for {title!r}: {exc}")


def _read_playlists(sp_service, *, full_scan: bool):
    """
    The ingest playlist plus every registered genre playlist (services/genre_playlists.py), read as cheaply as
    possible (services/playlist_delta.py: one request when nothing changed). Returns (tracks, reads):
    `tracks` is the merged list — songs from a genre playlist carry `forced_genre` — and `reads` is what
    `_commit_reads` needs once those songs have been handled. Raises if the ingest playlist itself cannot be
    read; an unreadable genre playlist is skipped with a warning.
    """
    from services import genre_playlists, playlist_delta

    state = playlist_delta.load_state()
    main = playlist_delta.read_playlist(sp_service, INGEST_PLAYLIST_ID, state.get(INGEST_PLAYLIST_ID),
                                        force_full=full_scan)
    reads = [(INGEST_PLAYLIST_ID, main)]
    genre_reads = []
    for entry in genre_playlists.load():
        try:
            r = playlist_delta.read_playlist(sp_service, entry["id"], state.get(entry["id"]), force_full=full_scan)
        except Exception as exc:
            logger.warning(f"[ingest] genre playlist {entry.get('name') or entry['id']} ({entry['crate']}) "
                           f"could not be read: {exc}")
            continue
        reads.append((entry["id"], r))
        genre_reads.append((entry, r.tracks))
    return genre_playlists.merge_tracks(main.tracks, genre_reads), reads


def _evidence_route(artist, title, filepath, bpm=None, duration_ms=None, classify=None):
    """
    Is there VERIFIED evidence for which crate this song belongs in? The router only knows artists that are on
    its own lists; for everyone else this asks the evidence engine — the one the hourly catch-all job uses:
    Last.fm / MusicBrainz / iTunes tags for THIS song, remixer and title hints, tempo — and accepts the answer
    only when it is backed by a trusted source and names a real crate (not the Electronic catch-all).

    Returns ('Library/<crate>', 'evidence:<sources>') or ('', ''). Never raises.
    INGEST_EVIDENCE_ROUTING=false turns it off. `classify` is injectable for tests.
    """
    if os.getenv("INGEST_EVIDENCE_ROUTING", "true").strip().lower() in ("0", "false", "no", "off"):
        return "", ""
    try:
        if classify is None:
            from services.genre_evidence import classify_track as classify
        secs = (duration_ms or 0) / 1000.0 or None
        dec = classify(artist, title, path=filepath, bpm=bpm, online=True, use_itunes=True, duration_s=secs)
        if (not dec.abstain) and dec.verified and dec.genre not in ("", "Electronic"):
            logger.info(f"[ingest] evidence routing: {title} → Library/{dec.genre} ({dec.explain()})")
            return f"Library/{dec.genre}", "evidence:" + "+".join(dec.sources)
    except Exception as exc:
        logger.debug(f"[ingest] evidence routing unavailable: {exc}")
    return "", ""


_NOT_SUNG_RE = re.compile(r"\b(?:instrumental|karaoke|bgm|theme|beat|dialogue)\b", re.IGNORECASE)


def _known_crate(artist) -> str:
    """The crate your own lists already give this artist (override list, what the app learned, knowledge base); ''."""
    try:
        from services.genre_router import _get_artist_override, normalize_genre, GENRE_TAXONOMY
        genre = _get_artist_override(artist)
        if not genre:
            from services.artist_memory_service import lookup_artist
            rec = lookup_artist(artist) or {}
            genre = rec.get("genre", "") if rec.get("confidence", 0) >= 0.5 else ""
        if not genre:
            from services.artist_knowledge_service import lookup_artist_knowledge
            genre = (lookup_artist_knowledge(artist) or {}).get("genre", "")
        canonical = normalize_genre(genre) if genre else ""
        return GENRE_TAXONOMY[canonical][1] if canonical in GENRE_TAXONOMY else ""
    except Exception as exc:
        logger.debug(f"[ingest] known-crate lookup unavailable for {artist!r}: {exc}")
        return ""


def _expects_indic_vocals(artist, title, forced_crate="") -> bool:
    """
    Must this download have South Asian singing? Yes when the crate is already known (a genre playlist, or
    the artist's crate on your lists) and is Bollywood/Punjabi/Tamil/Indian Hip Hop, unless the title itself
    says it is an instrumental. The downloader then rejects karaoke/instrumental uploads (services/ai_listener).
    """
    if _NOT_SUNG_RE.search(title or ""):
        return False
    from services.ai_listener import INDIC_VOCAL_CRATES
    crate = forced_crate or _known_crate(artist)
    return crate in INDIC_VOCAL_CRATES


def _ai_route(artist, title, filepath, bpm=None, classify=None):
    """
    Groq listens to the song (Whisper: language, lyrics, vocals) and picks one of your crates
    (services/ai_listener.py). Returns ('Library/<crate>', 'ai:groq') or ('', '') when Groq is unavailable,
    unsure (confidence below AI_MIN_CONFIDENCE, default 0.4) or says Electronic — the catch-all keeps its
    routing_source=catchall tag then, so the hourly job looks at it again. `classify` is injectable for tests.
    """
    try:
        if classify is None:
            from services.ai_listener import classify
        answer = classify(filepath, title, artist, bpm=bpm)
    except Exception as exc:
        logger.debug(f"[ingest] AI routing unavailable: {exc}")
        return "", ""
    crate = (answer or {}).get("crate", "")
    try:
        floor = float(os.getenv("AI_MIN_CONFIDENCE", "0.4"))
    except ValueError:
        floor = 0.4
    if not crate or crate == "Electronic" or answer.get("confidence", 0) < floor:
        return "", ""
    return f"Library/{crate}", "ai:groq"


def _hip_hop_folder(artist, title, is_indian=None) -> str:
    """
    'Library/Indian Hip Hop' when any Indian signal exists for the artist (curated list, Indian tags, Indian
    script), else 'Library/International Hip Hop'. Never raises. `is_indian` is injectable for tests.
    """
    try:
        if is_indian is None:
            from services.genre_evidence import is_indian_artist as is_indian
        return "Library/Indian Hip Hop" if is_indian(artist, title) else "Library/International Hip Hop"
    except Exception as exc:
        logger.debug(f"[ingest] hip hop origin check unavailable: {exc}")
        return "Library/International Hip Hop"


def _commit_reads(reads) -> None:
    """Everything `reads` returned has been handled: from now on an unchanged playlist is a one-request skip."""
    try:
        from services import playlist_delta
        for playlist_id, read in reads:
            playlist_delta.commit(playlist_id, read)
    except Exception as exc:
        logger.warning(f"[ingest] could not save the playlist watch state (next check re-reads): {exc}")


def ingest_download(download_dir=None, force_folder=None, force_redownload=False, full_scan=False):
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
        full_scan: When True, read every playlist completely instead of asking Spotify
            "did anything change?" first. Implied by force_folder / force_redownload;
            set by manual_refresh() (the "Sync now" button).
    """
    if not INGEST_PLAYLIST_ID:
        logger.warning("[ingest] No INGEST_PLAYLIST_ID configured. Skipping.")
        return

    # RETRY HARDENING — replay any pending manifests from previous failed moves
    try:
        _process_retry_queue()
    except Exception as _rq_err:
        logger.warning(f"[ingest] Retry queue processing failed: {_rq_err}")

    # INDEX RECOVERY (P0) — replay any pending manifests from previous failed
    # library_index writes (download succeeded, index write did not).
    try:
        _process_index_queue()
    except Exception as _iq_err:
        logger.warning(f"[ingest] Index-recovery queue processing failed: {_iq_err}")

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

    # VERIFY — a download recorded as done a few minutes ago whose file does not exist anywhere is queued
    # again (twice at most). Runs before the playlist is read so the re-queued songs are picked up this cycle.
    try:
        from services import download_verifier
        from services.requeue_service import requeue_tracks
        _vs = download_verifier.process_pending(BASE_DOWNLOAD_DIR, requeue_tracks)
        if _vs["requeued"] or _vs["gave_up"]:
            logger.warning(f"[ingest] verification: {len(_vs['requeued'])} song(s) had no file and were re-queued, "
                           f"{len(_vs['gave_up'])} still had none after every retry")
    except Exception as _v_err:
        logger.warning(f"[ingest] download verification skipped: {_v_err}")

    # One cheap request asks Spotify whether the playlist changed; songs are read only if it did.
    try:
        tracks, _reads = _read_playlists(sp_service, full_scan=bool(full_scan or force_folder or force_redownload))
    except Exception as e:
        logger.error(f"[ingest] Failed to fetch ingest playlist: {e}")
        AUTO_STATUS["status"] = "idle"
        AUTO_STATUS["current"] = f"Fetch error: {str(e)[:80]}"
        return
    _main_read = _reads[0][1]

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

    # REQUEUE — tracks queued for a fresh download (e.g. a wrong karaoke/instrumental
    # version was removed from the library). They bypass the ingest-history filter above
    # and are picked up even when they are not in the ingest playlist at all.
    # See services/requeue_service.py.
    try:
        from services.requeue_service import merge_requeued_tracks
        new_tracks = merge_requeued_tracks(new_tracks, sp_service, failure_counts, MAX_FAIL_ATTEMPTS)
    except Exception as _rq_merge_err:
        logger.warning(f"[ingest] Requeue merge failed (continuing without it): {_rq_merge_err}")

    # Update status with playlist totals. A full read sees every song; an incremental one sees only the ends of
    # the playlist, so its totals come from Spotify's own count instead.
    if _main_read.was_full:
        current_ids = {t["id"] for t in tracks}
        AUTO_STATUS["playlist_total"] = len(tracks)
        AUTO_STATUS["synced_total"] = len(saved_ids & current_ids)
    else:
        AUTO_STATUS["playlist_total"] = _main_read.total
        AUTO_STATUS["synced_total"] = min(len(saved_ids), _main_read.total)
    AUTO_STATUS["last_checked"] = time.strftime("%H:%M:%S")

    _already = sum(1 for t in tracks if t.get("forced_genre") and t["id"] in saved_ids)
    if _already:
        logger.info(f"[ingest] {_already} song(s) in your genre playlists are already in the library — "
                    "they stay where they are")

    if not new_tracks:
        logger.info(f"[ingest] No new tracks ({'playlist unchanged' if _main_read.mode == 'skip' else 'nothing to download'}).")
        _commit_reads(_reads)
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

        def _fallback_genre(filepath: str, bpm=None):
            """
            The router does not know this artist. First the evidence engine (see _evidence_route: only a
            VERIFIED answer for a real crate counts), then Groq listens to the song and picks a crate
            (_ai_route). Returns (Library/-prefixed path or '', method): 'evidence:<sources>', 'ai:groq' or ''.
            Never raises.
            """
            _epath, _emethod = _evidence_route(artist, title, filepath, bpm, track_info.get("duration_ms"))
            if _epath:
                return _epath, _emethod
            return _ai_route(artist, title, filepath, bpm)

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
                    _requeue_done(tid, title, artist, "a file with this identity already exists in the library")
                    return
                _in_progress_registry.add(track_key)  # reserve the slot — no other worker can claim it
                _reserved = True

        # Include artist in filename so same-title tracks by different artists
        # (e.g. "Peach" by Diljit vs "Peach" in Trance) get distinct filenames
        # and the cross-directory dedup never falsely blocks one of them.
        filename = sanitize_filename(f"{title} - {artist}")

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
                    album_art_url=track_info.get("album_art_url"),
                    spotify_track_id=track_info.get("id", ""),
                    spotify_release_date=track_info.get("release_date", ""),
                    expect_vocals=_expects_indic_vocals(artist, title, track_info.get("forced_genre", "")),
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

                _is_catchall = False  # set True when routed to Electronic as fallback
                _route_tag = ""       # how a song was filed when it was NOT the router: written to the file's routing_source tag
                _bpm_hint = (result.get("tagging_report") or {}).get("bpm")   # tempers unverified evidence
                genre_confidence = 0.0  # default; overwritten by routing branches below
                genre_source = "unclassified"  # default; overwritten by routing branches below.
                # Needed because the MB-genre-unmapped branch (mb_genre set, map_genre_string()
                # returns falsy, Gemini fallback also fails) sets _is_catchall=True without ever
                # assigning genre_source — leaving it unset raised UnboundLocalError in the
                # download_needs_review emit below, which the outer except Exception caught and
                # mislabeled a fully-downloaded/tagged/moved track as FAILED.
                _forced_folder = ""
                if not force_folder and track_info.get("forced_genre"):
                    from services.genre_playlists import crate_folder as _crate_folder
                    _forced_folder = _crate_folder(track_info["forced_genre"])
                    if not _forced_folder:
                        logger.warning(f"[ingest] genre playlist crate {track_info['forced_genre']!r} is not a crate "
                                       f"in your library — routing {title!r} normally")
                if force_folder:
                    # Manual override always wins — flat, no artist subfolder
                    final_folder = os.path.join(target_base, force_folder)
                    genre_confidence = 1.0
                elif _forced_folder:
                    # A genre playlist you own says which crate this is: the one source that is never a guess.
                    final_folder = os.path.join(BASE_DOWNLOAD_DIR, _forced_folder)
                    genre_confidence = 1.0
                    genre_source = "genre_playlist"
                    _route_tag = f"genre_playlist:{track_info['forced_genre']}"
                    logger.info(f"[ingest] Genre playlist: {title} → {_forced_folder}")
                else:
                    from services.genre_router import resolve_genre_folder_with_confidence
                    # ── artist_memory: user-confirmed associations override Spotify/Groq ──
                    _memory_routed = False
                    try:
                        from services.artist_memory_service import lookup_artist as _mem_lookup
                        from services.genre_router import normalize_genre as _norm_g, _library_path as _lib_p
                        _mem_rec = _mem_lookup(artist)
                        if _mem_rec and _mem_rec.get("confidence", 0) >= 0.5:
                            _mem_genre = _norm_g(_mem_rec.get("genre", "")) or _mem_rec.get("genre", "")
                            if _mem_genre:
                                _mem_lib_path = _lib_p(_mem_genre)
                                final_folder = os.path.join(BASE_DOWNLOAD_DIR, _mem_lib_path)
                                genre_confidence = _mem_rec.get("confidence", 0.5)
                                logger.info(
                                    f"[ingest] artist_memory: {title} → {_mem_lib_path} "
                                    f"(conf={genre_confidence:.2f}, src={_mem_rec.get('source', 'memory')})"
                                )
                                _memory_routed = True
                    except Exception as _me:
                        logger.debug(f"[ingest] artist_memory lookup skipped: {_me}")

                    if not _memory_routed:
                        # Try MusicBrainz genre first (conf=0.9 — most accurate)
                        mb_genre = (result.get("tagging_report") or {}).get("genre", "")
                        if mb_genre:
                            mapped = map_genre_string(mb_genre)
                            if mapped:
                                # map_genre_string returns a Library/-prefixed path — flat, no artist subfolder.
                                final_folder = os.path.join(BASE_DOWNLOAD_DIR, mapped)
                                genre_confidence = 0.9
                                logger.info(f"[ingest] MB genre routing: {title} → {mapped} (conf=0.9)")
                            else:
                                # MB genre not in map — verified evidence, then Groq listening, then Electronic catch-all
                                # (the variable keeps its old name; it now holds either fallback's answer)
                                gemini_path, _fb_method = _fallback_genre(staged_filepath, _bpm_hint)
                                if gemini_path:
                                    final_folder = os.path.join(BASE_DOWNLOAD_DIR, gemini_path)
                                    logger.info(f"[ingest] Fallback routing ({_fb_method}, MB miss): {title} → {gemini_path}")
                                    _route_tag = _fb_method
                                    _emit("download_auto_classified", {
                                        "title": title, "artist": artist,
                                        "folder": gemini_path, "method": _fb_method.split(":")[0],
                                        "source": "ingest",
                                    })
                                else:
                                    final_folder = os.path.join(BASE_DOWNLOAD_DIR, "Library", "Electronic")
                                    _is_catchall = True
                                    logger.info(f"[ingest] Electronic catch-all (MB miss, no evidence, Groq unsure): {title}")
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
                                # Low-confidence — try verified evidence, then Groq listening, before the catch-all.
                                # Never routes to NeedsReview for genre failures.
                                gemini_path, _fb_method = _fallback_genre(staged_filepath, _bpm_hint)
                                if gemini_path:
                                    final_folder = os.path.join(BASE_DOWNLOAD_DIR, gemini_path)
                                    logger.info(
                                        f"[ingest] Fallback routing ({_fb_method}): {title} → {gemini_path} "
                                        f"(Spotify conf={genre_confidence:.2f} was too low)"
                                    )
                                    _route_tag = _fb_method
                                    _emit("download_auto_classified", {
                                        "title": title, "artist": artist,
                                        "folder": gemini_path, "method": _fb_method.split(":")[0],
                                        "spotify_confidence": genre_confidence,
                                        "source": "ingest",
                                    })
                                else:
                                    # No evidence and Groq unsure/unavailable → Electronic catch-all.
                                    # Song is never lost — always lands somewhere in Library/.
                                    final_folder = os.path.join(BASE_DOWNLOAD_DIR, "Library", "Electronic")
                                    _is_catchall = True
                                    logger.warning(
                                        f"[ingest] Electronic catch-all: {title} "
                                        f"(Spotify conf={genre_confidence:.2f}, no evidence, Groq unsure)"
                                    )

                # Every routing branch above except the plain Electronic catch-all
                # returns a "Library/..." forward-slash path (genre_router.py's
                # _library_path()/map_genre_string()/Gemini-fallback convention —
                # confirmed: os.path.join(BASE_DOWNLOAD_DIR, "Library/Bollywood")
                # on Windows does NOT normalize the slash inside the second
                # argument, so final_folder ends up mixed-separator, e.g.
                # "C:\Users\...\DJ music\Library/Bollywood". The move/os.makedirs
                # calls below still work (Windows tolerates '/' in paths), but the
                # exact-string final_path written to library_index a few lines
                # down never matches the all-backslash paths produced by
                # pathlib/os.walk in reconcile_library_state.py or repair_index.py
                # — every such track is misreported as "orphaned" even though it
                # has a valid (if oddly-formatted) index entry. Confirmed live:
                # 9 tracks downloaded via this exact path tonight (2026-08-25)
                # showed up as orphans in repair_index.py --dry-run. normpath()
                # collapses this to a clean native path without touching which
                # file it points to.
                # Hip hop has two crates. Whatever said "hip hop" (MusicBrainz, the router, the AI guess) knew the
                # GENRE but not where the artist is from, so the Indian-vs-International call is made here. A crate
                # you chose yourself (a pinned folder or a genre playlist) is never second-guessed.
                if (not force_folder and not _forced_folder
                        and str(final_folder).replace("\\", "/").rstrip("/").endswith("/International Hip Hop")):
                    final_folder = os.path.join(BASE_DOWNLOAD_DIR, _hip_hop_folder(artist, title))
                    _route_tag = _route_tag or "evidence:hip_hop_origin"

                final_folder = os.path.normpath(final_folder)

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

                # Tag catch-all files so maintenance worker can look at them again later
                if _is_catchall:
                    try:
                        from mutagen.id3 import ID3, TXXX
                        _audio = ID3(final_filepath)
                        _audio.add(TXXX(encoding=3, desc="routing_source", text=["catchall"]))
                        _audio.save()
                    except Exception:
                        pass
                    # Emit now that final filename/path are known (filepath was unknown before the move)
                    _emit("download_needs_review", {
                        "title": title, "artist": artist,
                        "genre_source": genre_source,
                        "confidence": genre_confidence,
                        "suggested_folder": "Library/Electronic",
                        "filename": Path(final_filepath).name,
                        "source": "ingest",
                    })

                # The app filed this song here: a later move to another crate is then recognisably YOURS.
                try:
                    from services.hand_moves import stamp as _stamp_crate
                    _stamp_crate(final_filepath)
                except Exception:
                    pass

                # Say HOW a song was filed when it was not the router: a genre playlist, verified evidence or Groq.
                if _route_tag and not _is_catchall:
                    try:
                        from mutagen.id3 import ID3, TXXX
                        _audio = ID3(final_filepath)
                        _audio.add(TXXX(encoding=3, desc="routing_source", text=[_route_tag]))
                        _audio.save()
                    except Exception:
                        pass

                # Update result with final path
                result["filepath"] = final_filepath

                with _registry_lock:
                    _downloaded_registry.add(track_key)
                success_count[0] += 1
                saved_ids.add(tid)
                _requeue_done(tid, title, artist, "downloaded")
                # VERIFY — have the file's presence double-checked a few minutes from now (services/download_verifier.py)
                try:
                    from services import download_verifier as _dv
                    _dv.record_done(tid, title, artist, os.path.basename(final_filepath))
                except Exception as _dv_err:
                    logger.debug(f"[ingest] verification record skipped: {_dv_err}")
                logger.info(f"[ingest] Downloaded: {result['filename']}")
                # LIBRARY INDEX — register in MongoDB for persistent O(1) dedup
                _genre_folder = ""  # default; overwritten below on success. Must be set BEFORE
                # the try so _routing_label / the download_complete emit further down never hit
                # an UnboundLocalError if relative_to() raises (final_folder not under
                # BASE_DOWNLOAD_DIR) and the except branch below runs instead.
                try:
                    from database import index_track as _idx
                    from services.dedup_service import content_hash as _ch
                    _genre_folder = Path(final_folder).relative_to(BASE_DOWNLOAD_DIR).as_posix()
                    _idx(
                        identity_key=track_key,
                        spotify_id=tid,
                        content_hash=_ch(title, artist, track_info.get("duration_ms")),
                        title=title,
                        artist=artist,
                        filename=result.get("filename", ""),
                        final_path=final_filepath,
                        genre_folder=_genre_folder,
                        genre_confidence=genre_confidence,
                    )
                    # H9 fix: re-persist BPM/key now that the library_index document exists
                    _tr_report = result.get("tagging_report") or {}
                    _tr_bpm = _tr_report.get("bpm")
                    if _tr_bpm:
                        try:
                            from bpm_key_service import persist_audio_features as _paf_bpm
                            _paf_bpm(track_key, {
                                "bpm": _tr_bpm,
                                "key": _tr_report.get("key"),
                                "camelot": _tr_report.get("camelot"),
                                "analyzed": True,
                            })
                        except Exception as _bpm_err:
                            logger.debug(f"[ingest] BPM re-persist skipped: {_bpm_err}")
                    # Last.fm enrichment (primary) — community tags, no quota limit
                    try:
                        from services.tagger_service import enrich_track_lastfm as _enrich_lastfm
                        _lfm_ok = _enrich_lastfm(track_key, artist, title)
                    except Exception as _lfm_err:
                        _lfm_ok = False
                        logger.debug(f"[ingest] Last.fm enrichment skipped: {_lfm_err}")
                    # Gemini fallback — unknown-track only; preserves daily quota
                    if not _lfm_ok:
                        try:
                            from services.tagger_service import enrich_track_gemini as _enrich_gemini
                            _enrich_gemini(track_key, final_filepath)
                        except Exception as _gem_err:
                            logger.debug(f"[ingest] Gemini enrichment skipped: {_gem_err}")
                    # DnB BPM correction: librosa can't derive genre hint at staging time.
                    # Re-run with correct hint now that the genre folder is known.
                    _gf_lower = _genre_folder.lower().replace("\\", "/")
                    if any(k in _gf_lower for k in ("dnb", "drum and bass", "d&b")):
                        try:
                            from bpm_key_service import (
                                detect_bpm_and_key as _dbk,
                                write_bpm_key_to_tags as _wbkt,
                                persist_audio_features as _paf,
                            )
                            _dnb_result = _dbk(final_filepath, genre_hint="dnb")
                            if _dnb_result.get("analyzed") and _dnb_result.get("bpm"):
                                _wbkt(final_filepath, _dnb_result["bpm"], _dnb_result.get("key"))
                                _paf(track_key, _dnb_result)
                                logger.info(
                                    f"[ingest] DnB BPM corrected: {title} → "
                                    f"{_dnb_result['bpm']} BPM · {_dnb_result.get('key')}"
                                )
                        except Exception as _dnb_err:
                            logger.debug(f"[ingest] DnB BPM re-analysis skipped: {_dnb_err}")
                    # Techno BPM correction: same half-time problem as DnB (70-75 BPM → 140-150 BPM).
                    elif any(k in _gf_lower for k in ("techno", "industrial")):
                        try:
                            from bpm_key_service import (
                                detect_bpm_and_key as _dbk,
                                write_bpm_key_to_tags as _wbkt,
                                persist_audio_features as _paf,
                            )
                            _techno_result = _dbk(final_filepath, genre_hint="techno")
                            if _techno_result.get("analyzed") and _techno_result.get("bpm"):
                                _wbkt(final_filepath, _techno_result["bpm"], _techno_result.get("key"))
                                _paf(track_key, _techno_result)
                                logger.info(
                                    f"[ingest] Techno BPM corrected: {title} → "
                                    f"{_techno_result['bpm']} BPM · {_techno_result.get('key')}"
                                )
                        except Exception as _techno_err:
                            logger.debug(f"[ingest] Techno BPM re-analysis skipped: {_techno_err}")
                except Exception as _idx_err:
                    _idx_genre_folder = locals().get("_genre_folder", "")
                    logger.error(
                        "[ingest] library_index write FAILED — file is saved on disk but NOT "
                        "in library_index (not dedup-safe, not searchable) | "
                        f"op=database.index_track()->library_index.update_one(upsert=True) "
                        f"exc_type={type(_idx_err).__name__} exc_msg={_idx_err} "
                        f"title={title!r} artist={artist!r} spotify_id={tid!r} "
                        f"identity_key={track_key!r} final_path={final_filepath!r} "
                        f"genre_folder={_idx_genre_folder!r}"
                    )
                    _write_index_recovery_manifest(
                        identity_key=track_key,
                        spotify_id=tid,
                        title=title,
                        artist=artist,
                        filename=result.get("filename", ""),
                        final_path=final_filepath,
                        genre_folder=_idx_genre_folder,
                        genre_confidence=genre_confidence,
                        duration_ms=track_info.get("duration_ms"),
                    )
                _routing_label = (
                    "Needs Sorting" if "NeedsReview" in _genre_folder
                    else "Unclassified" if "Electronic" in _genre_folder and _is_catchall
                    else os.path.basename(_genre_folder)
                )
                _tr = result.get("tagging_report") or {}
                _emit("download_complete", {
                    "title": title, "artist": artist, "status": "completed",
                    "filename": result.get("filename", ""),
                    "folder": _genre_folder,
                    "routing_label": _routing_label,
                    "source": "ingest",
                    "bpm": _tr.get("bpm"),
                    "key": _tr.get("key"),
                    "camelot": _tr.get("camelot"),
                    "energy": _tr.get("energy"),
                })

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

    clear_stop()  # reset any leftover stop flag from a previous run
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_download_single, t, force_folder, force_redownload): t for t in new_tracks}
        for future in as_completed(futures):
            if _stop_requested.is_set():
                logger.info("[ingest] Stop requested — cancelling remaining tracks")
                executor.shutdown(wait=False, cancel_futures=True)
                AUTO_STATUS["status"] = "idle"
                AUTO_STATUS["current"] = "Stopped by user"
                _emit_auto_status()
                _save_ingest_history(saved_ids)
                _save_failure_counts(failure_counts)
                return
            try:
                future.result()
            except Exception as e:
                t = futures[future]
                logger.error(f"[ingest] Unhandled error for {t['title']}: {e}")

    # Persist history
    _save_ingest_history(saved_ids)
    _save_failure_counts(failure_counts)  # PERMANENT SKIP — persist failure counts to disk

    # An unchanged playlist means "nothing to do" only when every song was handled without a retriable failure;
    # otherwise the watch state stays where it was and the next check looks at the playlist again.
    if fail_count[0] == 0:
        _commit_reads(_reads)
    else:
        logger.info(f"[ingest] {fail_count[0]} failure(s) this cycle — the next check will look at the playlist again")

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


def _learn_hand_moves() -> None:
    """Songs you moved by hand since the last cycle teach the router (services/hand_moves.py).
    HAND_MOVE_LEARNING=false switches it off. Needs no Spotify, so it runs even during a 429 cooldown."""
    if os.getenv("HAND_MOVE_LEARNING", "true").strip().lower() in ("0", "false", "no", "off"):
        return
    try:
        from services import hand_moves
        hand_moves.scan()
    except Exception as exc:
        logger.warning(f"[hand-moves] pass failed: {exc}")


def playlist_monitor():
    """Main monitor loop — checks the ingest playlist every CHECK_INTERVAL seconds."""
    time.sleep(10)
    if not is_authenticated():
        logger.warning("[ingest] Playlist monitor SKIPPED - no OAuth token. "
                       "Run 'python auto_downloader.py' to authorize.")
        return
    logger.info(f"[ingest] Playlist monitor started (checks every {effective_poll_interval()} s; "
                "one Spotify request per check unless the playlist changed).")
    while True:
        _learn_hand_moves()
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

        time.sleep(effective_poll_interval())


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
            full_scan=True,                     # "Sync now" looks at every song, not just what changed
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
