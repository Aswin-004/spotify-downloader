"""
Lock Service — process-safe coordination for the ingest pipeline.

Two implementations, same interface:
  RedisLock  — distributed lock via Redis SET NX PX (safe across processes/workers)
  ThreadLock — thin wrapper around threading.Lock (single-process fallback)

Selection is automatic: RedisLock is used when the REDIS_URL env var is set
and redis-py is installed and the server is reachable; otherwise ThreadLock.

Usage
-----
    from services.lock_service import acquire_registry_lock

    with acquire_registry_lock():
        # mutate shared ingest state safely
        ...

The context manager always succeeds — it never raises on timeout or Redis
failure, it degrades to the thread-level lock instead.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# ── Redis availability probe ──────────────────────────────────────────────────

_redis_client = None
_redis_checked = False
_redis_lock_meta = threading.Lock()  # guards _redis_client / _redis_checked


def _get_redis():
    """Return a redis.Redis client if available, else None. Cached after first call."""
    global _redis_client, _redis_checked
    with _redis_lock_meta:
        if _redis_checked:
            return _redis_client
        _redis_checked = True
        redis_url = os.getenv("REDIS_URL", "")
        if not redis_url:
            logger.debug("[lock_service] REDIS_URL not set — using threading.Lock fallback")
            return None
        try:
            import redis as _redis_mod
            client = _redis_mod.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            _redis_client = client
            logger.info(f"[lock_service] Redis available at {redis_url}")
        except Exception as e:
            logger.warning(f"[lock_service] Redis not reachable ({e}) — using threading.Lock fallback")
            _redis_client = None
        return _redis_client


# ── Thread-level fallback lock (per named resource) ──────────────────────────

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _get_thread_lock(name: str) -> threading.Lock:
    with _thread_locks_guard:
        if name not in _thread_locks:
            _thread_locks[name] = threading.Lock()
        return _thread_locks[name]


# ── Redis lock implementation ─────────────────────────────────────────────────

class _RedisLock:
    """
    Redis SET NX PX distributed lock.

    Guarantees the lock is released only by the holder (token check on delete).
    TTL prevents indefinite hold if the process crashes.
    """

    def __init__(self, client, key: str, ttl_ms: int = 30_000):
        self._client = client
        self._key = f"lock:{key}"
        self._ttl_ms = ttl_ms
        self._token = str(uuid.uuid4())
        self._acquired = False

    def acquire(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok = self._client.set(self._key, self._token, nx=True, px=self._ttl_ms)
            if ok:
                self._acquired = True
                return True
            time.sleep(0.05)
        return False

    def release(self):
        if not self._acquired:
            return
        # Only delete if we still own it (Lua script for atomicity)
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            self._client.eval(script, 1, self._key, self._token)
        except Exception as e:
            logger.warning(f"[lock_service] Redis release failed: {e}")
        self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


# ── Public API ────────────────────────────────────────────────────────────────

@contextmanager
def acquire_lock(name: str = "ingest_registry", ttl_ms: int = 30_000, timeout: float = 10.0):
    """
    Context manager that acquires the named lock.

    Tries Redis first; falls back to threading.Lock on failure or unavailability.
    Never raises — on timeout it proceeds without the lock and logs a warning
    (safer than deadlocking the ingest pipeline).

    Args:
        name:    Lock name (Redis key suffix / thread-lock dict key).
        ttl_ms:  Redis lock TTL in milliseconds. Ignored for thread lock.
        timeout: Max seconds to wait before proceeding unlocked.
    """
    client = _get_redis()
    if client is not None:
        lock = _RedisLock(client, name, ttl_ms)
        acquired = False
        try:
            acquired = lock.acquire(timeout=timeout)
            if not acquired:
                logger.warning(f"[lock_service] Redis lock '{name}' timed out — proceeding without lock")
            yield
        except Exception as e:
            logger.warning(f"[lock_service] Redis lock error: {e} — degrading to thread lock")
            thread_lock = _get_thread_lock(name)
            with thread_lock:
                yield
            return
        finally:
            if acquired:
                lock.release()
    else:
        thread_lock = _get_thread_lock(name)
        acquired = thread_lock.acquire(timeout=timeout)
        try:
            if not acquired:
                logger.warning(f"[lock_service] Thread lock '{name}' timed out — proceeding without lock")
            yield
        finally:
            if acquired:
                thread_lock.release()


# Convenience alias used by auto_downloader
acquire_registry_lock = lambda: acquire_lock("ingest_registry")
