"""
Rate Limiter — Production Alpha
================================
Flask-Limiter configured with Redis storage (falls back to in-memory
when Redis is unavailable — acceptable for local dev, not production).

Limits are intentionally conservative for alpha:
  - Downloads:       20/hour  per IP  (yt-dlp is slow and CPU-heavy)
  - Playlist sync:    5/hour  per IP  (Spotify API quota guard)
  - Maintenance:      3/hour  per IP  (heavy DB operations)
  - Metadata lookup: 60/hour  per IP  (Spotify metadata, Groq genre)
  - Read endpoints: 300/hour  per IP  (analytics, status, files)
  - Global ceiling: 500/hour  per IP  (hard abuse prevention)

Cooldown responses return JSON with retry_after_seconds so the
frontend can show a countdown rather than a generic error.
"""
from __future__ import annotations

import os
import time
from flask import jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# ── Storage backend ──────────────────────────────────────────────────────────
# Use Redis when available so limits survive dyno restarts and are shared
# across all processes. Fall back to in-memory for local dev without Redis.

_REDIS_URL = os.getenv("REDIS_URL", "")

def _storage_uri() -> str:
    """Return Redis URI if reachable, else fall back to memory.

    In production (Render) the Redis URL is an internal network address that
    is always reachable — skip the probe to avoid blocking cold starts.
    In development the Render-internal Redis URL is NOT accessible from
    localhost, so we probe with a short timeout and fall back to memory.
    """
    if not _REDIS_URL:
        logger.warning("[rate_limiter] REDIS_URL not set — using in-memory storage")
        return "memory://"

    if not _IS_DEV:
        # Production: trust the URL — no blocking probe on cold start
        return _REDIS_URL

    # Development only: probe since Render-internal URLs are unreachable locally
    try:
        import redis as _redis
        c = _redis.from_url(_REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        c.ping()
        logger.info("[rate_limiter] Redis reachable in dev mode")
        return _REDIS_URL
    except Exception:
        logger.warning("[rate_limiter] Redis unreachable in dev — using memory storage")
        return "memory://"


# ── Limiter instance ─────────────────────────────────────────────────────────
# In development (FLASK_ENV=development or Redis unreachable locally) use very
# high limits so artwork/folder-tags/preview requests don't get blocked.
_IS_DEV = os.getenv("FLASK_ENV", "production") == "development"
_STORAGE = _storage_uri()
_IS_MEMORY = _STORAGE == "memory://"

_DEFAULT_LIMIT = "100000 per hour" if (_IS_DEV or _IS_MEMORY) else "500 per hour"

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_STORAGE,
    default_limits=[_DEFAULT_LIMIT],
    headers_enabled=True,       # adds X-RateLimit-Limit / Remaining / Reset headers
    strategy="fixed-window",    # simpler than moving-window, predictable for users
)

LIMIT_READS = "100000 per hour" if (_IS_DEV or _IS_MEMORY) else "300 per hour"


# ── Per-route limit decorators (import these in app.py) ─────────────────────

LIMIT_DOWNLOAD   = "20 per hour"
LIMIT_PLAYLIST   = "5 per hour"
LIMIT_MAINTENANCE= "3 per hour"
LIMIT_METADATA   = "60 per hour"
LIMIT_READS      = "300 per hour"


# ── Custom 429 error handler ─────────────────────────────────────────────────

def _seconds_until_reset(limit_string: str) -> int:
    """
    Estimate seconds until the rate-limit window resets.

    Handles both Flask-Limiter 3.x format ("20 per hour") and
    4.x format ("20 per 1 hour") by checking the LAST token.
    """
    parts = limit_string.split()
    period = parts[-1].lower() if parts else ""
    if period in ("hour", "hours"):
        return 3600
    if period in ("minute", "minutes"):
        return 60
    if period in ("second", "seconds"):
        return 1
    return 3600


def _record_rate_limit_hit_async(path: str) -> None:
    """Fire-and-forget MongoDB metric increment — never blocks the 429 response."""
    import threading
    def _write():
        try:
            from services.metrics_service import increment, M_RATE_LIMIT_HIT
            increment(M_RATE_LIMIT_HIT, tags={"path": path})
        except Exception:
            pass
    threading.Thread(target=_write, daemon=True).start()


def rate_limit_exceeded_handler(e):
    """
    Return a JSON 429 with a human-readable message and retry_after_seconds.
    The frontend uses retry_after_seconds to show a countdown timer.
    """
    # Track rate-limit hits asynchronously — never block the fast-rejection path
    _record_rate_limit_hit_async(request.path)

    # Extract the matched limit description from the error
    limit_str = getattr(e, "description", "500 per hour")
    retry_after = _seconds_until_reset(str(limit_str))

    # Pick a friendly message based on which endpoint was hit
    path = request.path
    if "/download" in path:
        msg = f"Download limit reached. You can download up to 20 tracks per hour. Try again in {retry_after // 60} minutes."
    elif "playlist" in path or "refresh" in path:
        msg = f"Playlist sync limit reached (5 per hour). Try again in {retry_after // 60} minutes."
    elif "maintenance" in path:
        msg = f"Maintenance task limit reached (3 per hour). Try again in {retry_after // 60} minutes."
    else:
        msg = f"Request limit reached. Try again in {retry_after // 60} minutes."

    response = jsonify({
        "error": "rate_limited",
        "message": msg,
        "retry_after_seconds": retry_after,
        "limit": str(limit_str),
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response


# ── Queue depth gate (not a Flask-Limiter concern, but lives here) ───────────

_MAX_QUEUE_DEPTH = int(os.getenv("MAX_QUEUE_DEPTH", "10"))


def check_queue_overload() -> tuple[bool, dict | None]:
    """
    Returns (overloaded: bool, error_response: dict | None).

    Call this before dispatching a download task.  When overloaded,
    return the error_response dict as a 503.
    """
    try:
        from celery_app import is_redis_available, celery_app as _celery_app
        if not is_redis_available():
            return False, None  # no queue to check

        # Use a single inspect object with 0.3s timeout per call.
        # Total worst-case blocking: 0.6s (was 2s with timeout=1.0 × 2 calls).
        inspect = _celery_app.control.inspect(timeout=0.3)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}

        active_count = sum(len(v) for v in active.values())
        queued_count = sum(len(v) for v in reserved.values())
        total = active_count + queued_count

        if total >= _MAX_QUEUE_DEPTH:
            logger.warning(f"[rate_limiter] Queue overload: {total} tasks (limit {_MAX_QUEUE_DEPTH})")
            return True, {
                "error": "server_busy",
                "message": (
                    f"The download queue is full ({total} tasks in progress). "
                    "Please try again in a few minutes."
                ),
                "queue_depth": total,
                "max_queue_depth": _MAX_QUEUE_DEPTH,
            }
    except Exception as exc:
        logger.debug(f"[rate_limiter] Queue depth check failed (non-critical): {exc}")

    return False, None
