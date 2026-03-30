# CELERY UPGRADE
"""
Celery Application Configuration
=================================
Redis as broker AND result backend.
Graceful fallback: if Redis is unavailable the app continues
using the existing threading-based download pipeline.

NOTE: Celery and redis are imported lazily so that eventlet.monkey_patch()
in app.py finishes before any threading primitives are created.
"""
import os

# ── Broker / backend URLs (configurable via env) ──────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ── Redis availability probe (runs before Celery is loaded) ──────────────
_redis_available: bool | None = None   # None = not yet probed

def is_redis_available() -> bool:
    """Check once whether Redis is reachable. Cached after first call."""
    global _redis_available
    if _redis_available is not None:
        return _redis_available
    try:
        import redis as _redis_lib                          # lazy import
        r = _redis_lib.Redis.from_url(REDIS_URL, socket_connect_timeout=2)
        r.ping()
        _redis_available = True
    except Exception:
        _redis_available = False
    return _redis_available


# ── Lazy Celery app construction ─────────────────────────────────────────
# Imported as:  from celery_app import celery_app
# The actual Celery() instance is built on first access so that
# eventlet.monkey_patch() has already run in the importing process.

_celery_app_instance = None

def _make_celery():
    global _celery_app_instance
    if _celery_app_instance is not None:
        return _celery_app_instance

    from celery import Celery                                # lazy import

    _celery_app_instance = Celery(
        "spotify_downloader",
        broker=REDIS_URL,
        backend=REDIS_URL,
    )

    _celery_app_instance.conf.update(
        # Serialisation
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",

        # Concurrency
        worker_concurrency=3,  # CELERY FIX: reduced from 4 to 3 to match download limits

        # Task tracking
        task_track_started=True,

        # Timeouts
        task_soft_time_limit=600,   # 10 min soft
        task_time_limit=900,        # 15 min hard

        # Result expiry (24 h)
        result_expires=86400,

        # Task discovery — look for tasks in tasks.py
        include=["tasks"],

        # Misc
        worker_hijack_root_logger=False,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,  # CELERY FIX: suppress Celery 6.0 deprecation warning
    )
    return _celery_app_instance


class _CeleryProxy:
    """Thin proxy so `from celery_app import celery_app` works at import
    time while deferring the real Celery() construction to first use."""
    def __getattr__(self, name):
        return getattr(_make_celery(), name)

    def __call__(self, *args, **kwargs):  # CELERY FIX: make proxy callable for CLI
        return _make_celery()(*args, **kwargs)

    @property
    def __class__(self):  # CELERY FIX: Celery CLI checks isinstance()
        from celery import Celery
        return Celery

celery_app = _CeleryProxy()

# CELERY FIX: expose `app` at module level — Celery CLI looks for this
# when invoked as `celery -A celery_app worker`
app = celery_app  # CELERY FIX
