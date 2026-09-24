"""
Discord notification service
============================
Non-blocking notifications for download events, sent to a Discord webhook.
If DISCORD_WEBHOOK_URL is not set, every function silently does nothing.
If a notification fails, it logs the error and never crashes the app.
"""
import asyncio
import os
import threading
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Config from .env
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
NOTIFY_ON_SUCCESS = os.getenv('NOTIFY_ON_SUCCESS', 'true').lower() == 'true'
NOTIFY_ON_FAILURE = os.getenv('NOTIFY_ON_FAILURE', 'true').lower() == 'true'
NOTIFY_ON_PLAYLIST = os.getenv('NOTIFY_ON_PLAYLIST_COMPLETE', 'true').lower() == 'true'
STORAGE_THRESHOLD_MB = float(os.getenv('NOTIFY_STORAGE_THRESHOLD_MB', '51200'))   # default 50 GB


def is_discord_enabled():
    """Check if a Discord webhook URL is configured."""
    return bool(DISCORD_WEBHOOK_URL)


# ═══════════════════════════════════════════════════════════════════
# DISCORD SENDER
# ═══════════════════════════════════════════════════════════════════

async def send_discord(
    title: str,
    description: str,
    color: int,
    thumbnail_url: str = None,
    fields: list = None,
):
    """Send a rich embed via the Discord webhook."""
    if not is_discord_enabled():
        return
    try:
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "SpotifyDL"},
            "fields": fields or [],
        }
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        payload = {"embeds": [embed]}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(DISCORD_WEBHOOK_URL, json=payload)
            if r.status_code not in [200, 204]:
                logger.warning(f"[notifications] Discord send failed: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[notifications] Discord error: {e}")


def notify(
    discord_title: str,
    discord_desc: str,
    discord_color: int,
    thumbnail_url: str = None,
    discord_fields: list = None,
):
    """Non-blocking sync wrapper: runs the async sender in a new event loop."""
    if not is_discord_enabled():
        return
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(send_discord(discord_title, discord_desc, discord_color, thumbnail_url, discord_fields))
        loop.close()
    except Exception as e:
        logger.error(f"[notifications] Notification error: {e}")


def _fire(*args):
    """Fire-and-forget in a daemon thread, so a notification never blocks a download."""
    threading.Thread(target=notify, args=args, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════
# TRIGGER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def notify_download_success(track, quality_report=None):
    """Notify on successful download. Called from downloader_service."""
    if not NOTIFY_ON_SUCCESS:
        return

    title = track.get('name', 'Unknown')
    artist = track.get('artists', [{}])[0].get('name', 'Unknown')
    art_url = track.get('album', {}).get('images', [{}])[0].get('url', '')
    bitrate = quality_report.get('bitrate_achieved', 'N/A') if quality_report else 'N/A'
    platform = quality_report.get('source_platform', 'N/A') if quality_report else 'N/A'
    score = quality_report.get('title_similarity_score', 0) if quality_report else 0

    _fire(
        "✅ Download Complete",
        f"**{artist}** — {title}",
        0x1DB954,                                            # Spotify green
        art_url,
        [
            {"name": "Quality", "value": bitrate, "inline": True},
            {"name": "Source", "value": platform, "inline": True},
            {"name": "Match", "value": f"{round(score * 100)}%", "inline": True},
        ],
    )


def notify_download_failure(track, attempt: int, error: str):
    """Notify on download failure. Called from downloader_service."""
    if not NOTIFY_ON_FAILURE:
        return

    title = track.get('name', 'Unknown')
    artist = track.get('artists', [{}])[0].get('name', 'Unknown')

    _fire(
        "❌ Download Failed",
        f"**{artist}** — {title}",
        0xFF0000,                                            # red
        None,
        [
            {"name": "Attempt", "value": f"{attempt}/3", "inline": True},
            {"name": "Error", "value": error[:100], "inline": False},
        ],
    )


def notify_playlist_complete(playlist_name: str, stats: dict):
    """Notify on playlist/ingest sync completion. Called from auto_downloader."""
    if not NOTIFY_ON_PLAYLIST:
        return

    success = stats.get('success', 0)
    failed = stats.get('failed', 0)
    total = stats.get('total', 0)
    duration = stats.get('duration_seconds', 0)
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    storage = stats.get('storage_mb', 0)

    _fire(
        f"🎵 Playlist Complete: {playlist_name}",
        f"Synced {total} tracks",
        0x1DB954,
        None,
        [
            {"name": "✅ Downloaded", "value": str(success), "inline": True},
            {"name": "❌ Failed", "value": str(failed), "inline": True},
            {"name": "💾 Storage", "value": f"{round(storage, 1)}MB", "inline": True},
            {"name": "⏱ Time", "value": f"{minutes}m {seconds}s", "inline": True},
        ],
    )


def notify_storage_warning(used_mb: float, limit_mb: float):
    """Notify when storage usage exceeds the threshold."""
    percentage = round(used_mb / limit_mb * 100, 1)
    _fire(
        "⚠️ Storage Warning",
        f"Used {round(used_mb)}MB of {round(limit_mb)}MB ({percentage}%)",
        0xFFA500,                                            # orange
    )


def notify_ytdlp_error(error_type: str):
    """Notify when the yt-dlp pipeline breaks."""
    _fire(
        "🚨 yt-dlp Error",
        f"Pipeline broken: {error_type[:100]}",
        0xFF0000,
        None,
        [{"name": "Fix", "value": "pip install -U yt-dlp", "inline": False}],
    )


def test_notifications():
    """Send a test notification to Discord."""
    notify_download_success(
        track={
            'name': 'Test Track',
            'artists': [{'name': 'Test Artist'}],
            'album': {'images': [{'url': ''}]},
        },
        quality_report={
            'bitrate_achieved': '320kbps',
            'source_platform': 'youtube',
            'title_similarity_score': 0.95,
        },
    )
    logger.info("[notifications] Test notification sent to Discord")
