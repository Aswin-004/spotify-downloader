"""
Queue Manager — Production Alpha
==================================
Centralizes Celery queue operations:
  - Duplicate task prevention via Redis dedup keys
  - Stuck task cleanup (tasks running > MAX_TASK_AGE_SECONDS)
  - Queue depth reporting
  - Worker heartbeat visibility
  - Redis pub/sub bridge subscription (Celery → Flask SocketIO)

All operations degrade gracefully when Redis / Celery is unavailable.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEDUP_TTL_SECONDS = int(os.getenv("TASK_DEDUP_TTL", "1800"))  # 30 min
MAX_TASK_AGE_SECONDS = int(os.getenv("MAX_TASK_AGE", "1200"))  # 20 min
STUCK_CLEANUP_INTERVAL = int(os.getenv("STUCK_CLEANUP_INTERVAL", "300"))  # every 5 min

_DEDUP_PREFIX = "task:dedup:"
_SOCKETIO_BRIDGE_CHANNEL = "socketio_bridge"


# ── Redis client helper ───────────────────────────────────────────────────────

_redis_client: Optional[object] = None
_redis_lock = threading.Lock()


def _get_redis():
    global _redis_client
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis as _redis_lib
            client = _redis_lib.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            _redis_client = client
        except Exception:
            pass
    return _redis_client


# ── Duplicate task prevention ─────────────────────────────────────────────────

def _dedup_key(title: str, artist: str) -> str:
    safe = f"{title.lower().strip()}:{artist.lower().strip()}"
    return f"{_DEDUP_PREFIX}{safe[:120]}"


def is_duplicate_task(title: str, artist: str) -> bool:
    """
    Return True if a download task for this title+artist is already
    active or recently queued.  Use claim_task_slot() instead when
    you intend to also register the task — it is atomic.
    """
    r = _get_redis()
    if r is not None:
        key = _dedup_key(title, artist)
        try:
            return bool(r.exists(key))
        except Exception:
            pass
    return _local_dedup_check(title, artist)


def claim_task_slot(title: str, artist: str, task_id: str = "") -> bool:
    """
    Atomically check-and-register a download task slot.

    Uses Redis SET NX (set-if-not-exists) to eliminate the race condition
    between checking for a duplicate and marking the task as active.

    Returns:
        True  — slot claimed successfully (no duplicate; task may proceed)
        False — slot already held (duplicate; task should be skipped)
    """
    r = _get_redis()
    if r is not None:
        key = _dedup_key(title, artist)
        try:
            # SET NX EX: only sets when key does not exist; returns True on success
            result = r.set(key, task_id or "1", nx=True, ex=DEDUP_TTL_SECONDS)
            return result is True  # None = key already existed (duplicate)
        except Exception:
            pass
    return _local_dedup_claim(title, artist)


def register_task(title: str, artist: str, task_id: str = "") -> None:
    """Mark a download as active. Prefer claim_task_slot() for new code — it is atomic."""
    r = _get_redis()
    if r is not None:
        key = _dedup_key(title, artist)
        try:
            r.setex(key, DEDUP_TTL_SECONDS, task_id or "1")
            return
        except Exception:
            pass
    _local_dedup_register(title, artist)


def deregister_task(title: str, artist: str) -> None:
    """Remove the dedup marker when a task completes or fails."""
    r = _get_redis()
    if r is not None:
        key = _dedup_key(title, artist)
        try:
            r.delete(key)
            return
        except Exception:
            pass
    _local_dedup_remove(title, artist)


# ── In-process dedup fallback (single-process dev mode) ──────────────────────

_local_dedup: dict[str, float] = {}  # key → expiry timestamp
_local_dedup_lock = threading.Lock()


def _local_dedup_check(title: str, artist: str) -> bool:
    key = _dedup_key(title, artist)
    with _local_dedup_lock:
        exp = _local_dedup.get(key)
        if exp and time.time() < exp:
            return True
        if exp:
            del _local_dedup[key]
    return False


def _local_dedup_claim(title: str, artist: str) -> bool:
    """Atomic check-and-set using the in-process lock. Returns True if claimed."""
    key = _dedup_key(title, artist)
    now = time.time()
    with _local_dedup_lock:
        exp = _local_dedup.get(key)
        if exp and now < exp:
            return False  # already active
        _local_dedup[key] = now + DEDUP_TTL_SECONDS
        return True  # claimed


def _local_dedup_register(title: str, artist: str) -> None:
    key = _dedup_key(title, artist)
    with _local_dedup_lock:
        _local_dedup[key] = time.time() + DEDUP_TTL_SECONDS


def _local_dedup_remove(title: str, artist: str) -> None:
    key = _dedup_key(title, artist)
    with _local_dedup_lock:
        _local_dedup.pop(key, None)


# ── Queue depth reporting ─────────────────────────────────────────────────────

def get_queue_depth() -> dict:
    """
    Return current queue state.

    Returns:
        {
          "active": int,    # tasks currently being processed
          "queued": int,    # tasks waiting in the queue
          "total":  int,
          "workers": int,   # number of live workers
          "celery_available": bool,
        }
    """
    try:
        from celery_app import is_redis_available, celery_app as _celery_app
        if not is_redis_available():
            return {"active": 0, "queued": 0, "total": 0, "workers": 0, "celery_available": False}

        # 0.5s timeout per call: 3 calls × 0.5s = 1.5s worst case (was 4.5s).
        # ping() is a separate call because it uses broadcast differently.
        inspect = _celery_app.control.inspect(timeout=0.5)
        active_map   = inspect.active()   or {}
        reserved_map = inspect.reserved() or {}
        ping_map     = inspect.ping()     or {}

        active  = sum(len(v) for v in active_map.values())
        queued  = sum(len(v) for v in reserved_map.values())
        workers = len(ping_map)

        return {
            "active": active,
            "queued": queued,
            "total": active + queued,
            "workers": workers,
            "celery_available": True,
        }
    except Exception as exc:
        logger.debug(f"[queue_manager] get_queue_depth failed: {exc}")
        return {"active": 0, "queued": 0, "total": 0, "workers": 0, "celery_available": False}


# ── Stuck task cleanup ────────────────────────────────────────────────────────

def cleanup_stuck_tasks() -> int:
    """
    Revoke Celery tasks that have been active for > MAX_TASK_AGE_SECONDS.
    Returns the number of tasks revoked.

    Safe to call from a background thread on startup or periodically.
    """
    revoked = 0
    try:
        from celery_app import is_redis_available, celery_app as _celery_app
        if not is_redis_available():
            return 0

        inspect = _celery_app.control.inspect(timeout=2.0)
        active_map = inspect.active() or {}
        now = time.time()

        for worker_name, tasks in active_map.items():
            for task in tasks:
                started = task.get("time_start") or 0
                age = now - started
                if age > MAX_TASK_AGE_SECONDS:
                    task_id = task.get("id")
                    name    = task.get("name", "unknown")
                    logger.warning(
                        f"[queue_manager] Revoking stuck task {task_id} "
                        f"({name}, age={age:.0f}s > {MAX_TASK_AGE_SECONDS}s)"
                    )
                    _celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                    revoked += 1

        if revoked:
            logger.info(f"[queue_manager] Revoked {revoked} stuck task(s)")
    except Exception as exc:
        logger.debug(f"[queue_manager] cleanup_stuck_tasks failed (non-critical): {exc}")

    return revoked


def start_stuck_task_cleanup_thread() -> None:
    """Start a background daemon thread that cleans up stuck tasks periodically."""
    def _loop():
        # Initial delay so the app is fully booted before first cleanup
        time.sleep(60)
        while True:
            try:
                cleanup_stuck_tasks()
            except Exception:
                pass
            time.sleep(STUCK_CLEANUP_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="stuck-task-cleanup")
    t.start()
    logger.info(f"[queue_manager] Stuck task cleanup thread started (interval={STUCK_CLEANUP_INTERVAL}s)")


# ── Redis pub/sub bridge ──────────────────────────────────────────────────────
# Celery workers publish Socket.IO events to the 'socketio_bridge' Redis channel
# (see tasks.py:_emit_socketio_event).  The Flask process subscribes here and
# re-emits them to connected WebSocket clients.

_bridge_thread: Optional[object] = None  # True once started (socketio background task, not a Thread)


def start_socketio_bridge(socketio_instance) -> None:
    """
    Subscribe to the Redis socketio_bridge channel and forward events to
    the Flask SocketIO instance.  Must be called after socketio is created.

    Includes automatic restart: if the Redis connection drops, the bridge
    reconnects with exponential back-off (up to 30s between attempts).

    No-op when Redis is unavailable at startup.
    """
    global _bridge_thread, _redis_client
    if _bridge_thread is not None:
        return  # already started

    if _get_redis() is None:
        logger.info("[queue_manager] Redis unavailable — socketio bridge not started")
        return

    # Reset the Redis client so the pub/sub connection is created fresh inside
    # the Gunicorn worker process, not inherited from the master.  A connection
    # inherited across fork runs in the wrong greenlet context and blocks the
    # gevent event loop, causing WORKER TIMEOUT after 120 s.
    _redis_client = None

    def _listener_loop():
        """Outer loop: reconnect on any connection failure."""
        global _bridge_thread
        backoff = 1
        while True:
            r = _get_redis()
            if r is None:
                logger.warning("[queue_manager] Bridge: Redis unavailable, retrying in %ds", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1  # reset on successful connection
            try:
                pubsub = r.pubsub()
                pubsub.subscribe(_SOCKETIO_BRIDGE_CHANNEL)
                logger.info("[queue_manager] SocketIO bridge subscribed to Redis channel")
                for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        event = payload.get("event")
                        data  = payload.get("data", {})
                        if event:
                            socketio_instance.emit(event, data)
                    except Exception as exc:
                        logger.debug(f"[queue_manager] Bridge message error: {exc}")
            except Exception as exc:
                logger.warning(f"[queue_manager] SocketIO bridge disconnected ({exc}), reconnecting in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                # Force Redis client re-probe on next iteration
                global _redis_client
                _redis_client = None

    # Use socketio.start_background_task() — creates a gevent greenlet inside
    # the worker's event loop instead of a threading.Thread created in the master.
    # threading.Thread after fork runs in the wrong greenlet context (different
    # otid) and cannot yield to gevent, blocking the event loop until WORKER TIMEOUT.
    socketio_instance.start_background_task(target=_listener_loop)
    _bridge_thread = True  # sentinel: not None = already started
    logger.info("[queue_manager] SocketIO bridge started as gevent background task (auto-reconnect enabled)")
