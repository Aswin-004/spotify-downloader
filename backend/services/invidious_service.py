"""
Invidious Service
=================
Uses public Invidious instances (YouTube proxy network) to bypass
datacenter IP bot-blocking for audio downloads.

yt-dlp can download from Invidious URLs natively, routing the actual
audio stream through the Invidious server rather than directly from
YouTube.  This means Render's blocked datacenter IP is never used for
the actual video stream — only the Invidious instance's IP is.
"""
import time
import requests

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Public Invidious instances — tried in order, first healthy one wins
_INSTANCES = [
    "https://inv.nadeko.net",
    "https://yewtu.be",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacydev.net",
    "https://vid.puffyan.us",
    "https://invidious.lunar.icu",
]

_cached_instance: str | None = None
_cache_expires: float = 0
_CACHE_TTL = 300  # 5 minutes


def get_working_instance() -> str | None:
    """Return a working Invidious instance URL. Cached for 5 minutes."""
    global _cached_instance, _cache_expires

    if _cached_instance and time.time() < _cache_expires:
        return _cached_instance

    for instance in _INSTANCES:
        try:
            # Lightweight ping — just check the stats endpoint
            resp = requests.get(
                f"{instance}/api/v1/stats",
                timeout=5,
                headers={"User-Agent": "spotify-downloader/1.0"},
            )
            if resp.status_code == 200:
                _cached_instance = instance
                _cache_expires = time.time() + _CACHE_TTL
                logger.debug(f"[invidious] Using instance: {instance}")
                return instance
        except Exception:
            continue

    logger.warning("[invidious] All instances unavailable")
    return None


def get_invidious_url(video_id: str) -> str | None:
    """
    Return an Invidious watch URL for the given YouTube video ID.
    Returns None if no Invidious instance is available.
    """
    instance = get_working_instance()
    if not instance:
        return None
    return f"{instance}/watch?v={video_id}"
