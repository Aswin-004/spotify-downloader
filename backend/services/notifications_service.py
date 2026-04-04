# NOTIFICATION — Telegram + Discord notification service
"""
Telegram & Discord Notification Service
=========================================
Non-blocking notifications for download events.
If credentials are not configured, silently skips.
If a notification fails, logs error and never crashes the app.
"""

import httpx  # NOTIFICATION
import asyncio  # NOTIFICATION
import os  # NOTIFICATION
import threading  # NOTIFICATION
from datetime import datetime  # NOTIFICATION
from dotenv import load_dotenv  # NOTIFICATION

load_dotenv()  # NOTIFICATION

# NOTIFICATION — Loguru / stdlib fallback
try:  # NOTIFICATION
    from loguru import logger  # NOTIFICATION
except ImportError:  # NOTIFICATION
    import logging  # NOTIFICATION
    logger = logging.getLogger(__name__)  # NOTIFICATION

# NOTIFICATION — Config from .env
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')  # NOTIFICATION
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')  # NOTIFICATION
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')  # NOTIFICATION
NOTIFY_ON_SUCCESS = os.getenv('NOTIFY_ON_SUCCESS', 'true').lower() == 'true'  # NOTIFICATION
NOTIFY_ON_FAILURE = os.getenv('NOTIFY_ON_FAILURE', 'true').lower() == 'true'  # NOTIFICATION
NOTIFY_ON_PLAYLIST = os.getenv('NOTIFY_ON_PLAYLIST_COMPLETE', 'true').lower() == 'true'  # NOTIFICATION
STORAGE_THRESHOLD_MB = float(os.getenv('NOTIFY_STORAGE_THRESHOLD_MB', '5000'))  # NOTIFICATION


def is_telegram_enabled():  # NOTIFICATION
    """Check if Telegram bot token and chat ID are configured."""  # NOTIFICATION
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)  # NOTIFICATION


def is_discord_enabled():  # NOTIFICATION
    """Check if Discord webhook URL is configured."""  # NOTIFICATION
    return bool(DISCORD_WEBHOOK_URL)  # NOTIFICATION


# ═══════════════════════════════════════════════════════════════════
# NOTIFICATION — TELEGRAM SENDER
# ═══════════════════════════════════════════════════════════════════

async def send_telegram(message: str, parse_mode: str = "HTML"):  # NOTIFICATION
    """Send a message via Telegram Bot API."""  # NOTIFICATION
    if not is_telegram_enabled():  # NOTIFICATION
        return  # NOTIFICATION
    try:  # NOTIFICATION
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"  # NOTIFICATION
        payload = {  # NOTIFICATION
            "chat_id": TELEGRAM_CHAT_ID,  # NOTIFICATION
            "text": message,  # NOTIFICATION
            "parse_mode": parse_mode,  # NOTIFICATION
        }  # NOTIFICATION
        async with httpx.AsyncClient(timeout=10) as client:  # NOTIFICATION
            r = await client.post(url, json=payload)  # NOTIFICATION
            if r.status_code != 200:  # NOTIFICATION
                logger.warning(f"[notifications] Telegram send failed: {r.text[:200]}")  # NOTIFICATION
    except Exception as e:  # NOTIFICATION
        logger.error(f"[notifications] Telegram error: {e}")  # NOTIFICATION


# ═══════════════════════════════════════════════════════════════════
# NOTIFICATION — DISCORD SENDER
# ═══════════════════════════════════════════════════════════════════

async def send_discord(  # NOTIFICATION
    title: str,  # NOTIFICATION
    description: str,  # NOTIFICATION
    color: int,  # NOTIFICATION
    thumbnail_url: str = None,  # NOTIFICATION
    fields: list = None,  # NOTIFICATION
):  # NOTIFICATION
    """Send a rich embed via Discord webhook."""  # NOTIFICATION
    if not is_discord_enabled():  # NOTIFICATION
        return  # NOTIFICATION
    try:  # NOTIFICATION
        embed = {  # NOTIFICATION
            "title": title,  # NOTIFICATION
            "description": description,  # NOTIFICATION
            "color": color,  # NOTIFICATION
            "timestamp": datetime.utcnow().isoformat(),  # NOTIFICATION
            "footer": {"text": "SpotifyDL"},  # NOTIFICATION
            "fields": fields or [],  # NOTIFICATION
        }  # NOTIFICATION
        if thumbnail_url:  # NOTIFICATION
            embed["thumbnail"] = {"url": thumbnail_url}  # NOTIFICATION
        payload = {"embeds": [embed]}  # NOTIFICATION
        async with httpx.AsyncClient(timeout=10) as client:  # NOTIFICATION
            r = await client.post(DISCORD_WEBHOOK_URL, json=payload)  # NOTIFICATION
            if r.status_code not in [200, 204]:  # NOTIFICATION
                logger.warning(f"[notifications] Discord send failed: {r.text[:200]}")  # NOTIFICATION
    except Exception as e:  # NOTIFICATION
        logger.error(f"[notifications] Discord error: {e}")  # NOTIFICATION


# ═══════════════════════════════════════════════════════════════════
# NOTIFICATION — SEND TO BOTH PLATFORMS
# ═══════════════════════════════════════════════════════════════════

async def notify_both(  # NOTIFICATION
    telegram_msg: str,  # NOTIFICATION
    discord_title: str,  # NOTIFICATION
    discord_desc: str,  # NOTIFICATION
    discord_color: int,  # NOTIFICATION
    thumbnail_url: str = None,  # NOTIFICATION
    discord_fields: list = None,  # NOTIFICATION
):  # NOTIFICATION
    """Send notifications to both Telegram and Discord concurrently."""  # NOTIFICATION
    tasks = []  # NOTIFICATION
    if is_telegram_enabled():  # NOTIFICATION
        tasks.append(send_telegram(telegram_msg))  # NOTIFICATION
    if is_discord_enabled():  # NOTIFICATION
        tasks.append(send_discord(  # NOTIFICATION
            discord_title,  # NOTIFICATION
            discord_desc,  # NOTIFICATION
            discord_color,  # NOTIFICATION
            thumbnail_url,  # NOTIFICATION
            discord_fields,  # NOTIFICATION
        ))  # NOTIFICATION
    if tasks:  # NOTIFICATION
        await asyncio.gather(*tasks, return_exceptions=True)  # NOTIFICATION


def notify(  # NOTIFICATION
    telegram_msg: str,  # NOTIFICATION
    discord_title: str,  # NOTIFICATION
    discord_desc: str,  # NOTIFICATION
    discord_color: int,  # NOTIFICATION
    thumbnail_url: str = None,  # NOTIFICATION
    discord_fields: list = None,  # NOTIFICATION
):  # NOTIFICATION
    """Non-blocking sync wrapper — runs async notify_both in a new event loop."""  # NOTIFICATION
    try:  # NOTIFICATION
        loop = asyncio.new_event_loop()  # NOTIFICATION
        loop.run_until_complete(notify_both(  # NOTIFICATION
            telegram_msg,  # NOTIFICATION
            discord_title,  # NOTIFICATION
            discord_desc,  # NOTIFICATION
            discord_color,  # NOTIFICATION
            thumbnail_url,  # NOTIFICATION
            discord_fields,  # NOTIFICATION
        ))  # NOTIFICATION
        loop.close()  # NOTIFICATION
    except Exception as e:  # NOTIFICATION
        logger.error(f"[notifications] Notification error: {e}")  # NOTIFICATION


# ═══════════════════════════════════════════════════════════════════
# NOTIFICATION — TRIGGER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def notify_download_success(track, quality_report=None):  # NOTIFICATION
    """Notify on successful download. Called from downloader_service."""  # NOTIFICATION
    if not NOTIFY_ON_SUCCESS:  # NOTIFICATION
        return  # NOTIFICATION

    title = track.get('name', 'Unknown')  # NOTIFICATION
    artist = track.get('artists', [{}])[0].get('name', 'Unknown')  # NOTIFICATION
    art_url = track.get('album', {}).get('images', [{}])[0].get('url', '')  # NOTIFICATION
    bitrate = quality_report.get('bitrate_achieved', 'N/A') if quality_report else 'N/A'  # NOTIFICATION
    platform = quality_report.get('source_platform', 'N/A') if quality_report else 'N/A'  # NOTIFICATION
    score = quality_report.get('title_similarity_score', 0) if quality_report else 0  # NOTIFICATION

    telegram_msg = (  # NOTIFICATION
        f"✅ <b>Downloaded:</b> {artist} - {title}\n"  # NOTIFICATION
        f"📊 <b>Quality:</b> {bitrate} | <b>Source:</b> {platform}\n"  # NOTIFICATION
        f"🎯 <b>Match Score:</b> {round(score * 100)}%"  # NOTIFICATION
    )  # NOTIFICATION

    # NOTIFICATION — Fire-and-forget in daemon thread (never blocks download)
    threading.Thread(  # NOTIFICATION
        target=notify,  # NOTIFICATION
        args=(  # NOTIFICATION
            telegram_msg,  # NOTIFICATION
            "✅ Download Complete",  # NOTIFICATION
            f"**{artist}** — {title}",  # NOTIFICATION
            0x1DB954,  # NOTIFICATION — Spotify green
            art_url,  # NOTIFICATION
            [  # NOTIFICATION
                {"name": "Quality", "value": bitrate, "inline": True},  # NOTIFICATION
                {"name": "Source", "value": platform, "inline": True},  # NOTIFICATION
                {"name": "Match", "value": f"{round(score * 100)}%", "inline": True},  # NOTIFICATION
            ],  # NOTIFICATION
        ),  # NOTIFICATION
        daemon=True,  # NOTIFICATION
    ).start()  # NOTIFICATION


def notify_download_failure(track, attempt: int, error: str):  # NOTIFICATION
    """Notify on download failure. Called from downloader_service."""  # NOTIFICATION
    if not NOTIFY_ON_FAILURE:  # NOTIFICATION
        return  # NOTIFICATION

    title = track.get('name', 'Unknown')  # NOTIFICATION
    artist = track.get('artists', [{}])[0].get('name', 'Unknown')  # NOTIFICATION

    telegram_msg = (  # NOTIFICATION
        f"❌ <b>Failed:</b> {artist} - {title}\n"  # NOTIFICATION
        f"🔄 <b>Attempt:</b> {attempt}/3\n"  # NOTIFICATION
        f"💥 <b>Reason:</b> {error[:100]}"  # NOTIFICATION
    )  # NOTIFICATION

    # NOTIFICATION — Fire-and-forget in daemon thread
    threading.Thread(  # NOTIFICATION
        target=notify,  # NOTIFICATION
        args=(  # NOTIFICATION
            telegram_msg,  # NOTIFICATION
            "❌ Download Failed",  # NOTIFICATION
            f"**{artist}** — {title}",  # NOTIFICATION
            0xFF0000,  # NOTIFICATION — Red
            None,  # NOTIFICATION
            [  # NOTIFICATION
                {"name": "Attempt", "value": f"{attempt}/3", "inline": True},  # NOTIFICATION
                {"name": "Error", "value": error[:100], "inline": False},  # NOTIFICATION
            ],  # NOTIFICATION
        ),  # NOTIFICATION
        daemon=True,  # NOTIFICATION
    ).start()  # NOTIFICATION


def notify_playlist_complete(playlist_name: str, stats: dict):  # NOTIFICATION
    """Notify on playlist/ingest sync completion. Called from auto_downloader."""  # NOTIFICATION
    if not NOTIFY_ON_PLAYLIST:  # NOTIFICATION
        return  # NOTIFICATION

    success = stats.get('success', 0)  # NOTIFICATION
    failed = stats.get('failed', 0)  # NOTIFICATION
    total = stats.get('total', 0)  # NOTIFICATION
    duration = stats.get('duration_seconds', 0)  # NOTIFICATION
    minutes = int(duration // 60)  # NOTIFICATION
    seconds = int(duration % 60)  # NOTIFICATION
    storage = stats.get('storage_mb', 0)  # NOTIFICATION

    telegram_msg = (  # NOTIFICATION
        f"🎵 <b>Playlist Sync Done:</b> {playlist_name}\n"  # NOTIFICATION
        f"✅ <b>Downloaded:</b> {success}\n"  # NOTIFICATION
        f"❌ <b>Failed:</b> {failed}\n"  # NOTIFICATION
        f"💾 <b>Storage:</b> {round(storage, 1)}MB\n"  # NOTIFICATION
        f"⏱ <b>Time:</b> {minutes}m {seconds}s"  # NOTIFICATION
    )  # NOTIFICATION

    # NOTIFICATION — Fire-and-forget in daemon thread
    threading.Thread(  # NOTIFICATION
        target=notify,  # NOTIFICATION
        args=(  # NOTIFICATION
            telegram_msg,  # NOTIFICATION
            f"🎵 Playlist Complete: {playlist_name}",  # NOTIFICATION
            f"Synced {total} tracks",  # NOTIFICATION
            0x1DB954,  # NOTIFICATION
            None,  # NOTIFICATION
            [  # NOTIFICATION
                {"name": "✅ Downloaded", "value": str(success), "inline": True},  # NOTIFICATION
                {"name": "❌ Failed", "value": str(failed), "inline": True},  # NOTIFICATION
                {"name": "💾 Storage", "value": f"{round(storage, 1)}MB", "inline": True},  # NOTIFICATION
                {"name": "⏱ Time", "value": f"{minutes}m {seconds}s", "inline": True},  # NOTIFICATION
            ],  # NOTIFICATION
        ),  # NOTIFICATION
        daemon=True,  # NOTIFICATION
    ).start()  # NOTIFICATION


def notify_storage_warning(used_mb: float, limit_mb: float):  # NOTIFICATION
    """Notify when storage usage exceeds threshold."""  # NOTIFICATION
    percentage = round(used_mb / limit_mb * 100, 1)  # NOTIFICATION

    telegram_msg = (  # NOTIFICATION
        f"⚠️ <b>Storage Warning!</b>\n"  # NOTIFICATION
        f"📁 <b>Used:</b> {round(used_mb)}MB / {round(limit_mb)}MB "  # NOTIFICATION
        f"({percentage}%)"  # NOTIFICATION
    )  # NOTIFICATION

    # NOTIFICATION — Fire-and-forget in daemon thread
    threading.Thread(  # NOTIFICATION
        target=notify,  # NOTIFICATION
        args=(  # NOTIFICATION
            telegram_msg,  # NOTIFICATION
            "⚠️ Storage Warning",  # NOTIFICATION
            f"Used {round(used_mb)}MB of {round(limit_mb)}MB ({percentage}%)",  # NOTIFICATION
            0xFFA500,  # NOTIFICATION — Orange
        ),  # NOTIFICATION
        daemon=True,  # NOTIFICATION
    ).start()  # NOTIFICATION


def notify_ytdlp_error(error_type: str):  # NOTIFICATION
    """Notify when yt-dlp pipeline breaks."""  # NOTIFICATION
    telegram_msg = (  # NOTIFICATION
        f"🚨 <b>yt-dlp Pipeline Broken!</b>\n"  # NOTIFICATION
        f"🔧 <b>Error:</b> {error_type[:100]}\n"  # NOTIFICATION
        f"💡 <b>Fix:</b> pip install -U yt-dlp"  # NOTIFICATION
    )  # NOTIFICATION

    # NOTIFICATION — Fire-and-forget in daemon thread
    threading.Thread(  # NOTIFICATION
        target=notify,  # NOTIFICATION
        args=(  # NOTIFICATION
            telegram_msg,  # NOTIFICATION
            "🚨 yt-dlp Error",  # NOTIFICATION
            f"Pipeline broken: {error_type[:100]}",  # NOTIFICATION
            0xFF0000,  # NOTIFICATION
            None,  # NOTIFICATION
            [{"name": "Fix", "value": "pip install -U yt-dlp", "inline": False}],  # NOTIFICATION
        ),  # NOTIFICATION
        daemon=True,  # NOTIFICATION
    ).start()  # NOTIFICATION


def test_notifications():  # NOTIFICATION
    """Send a test notification to both Telegram and Discord."""  # NOTIFICATION
    notify_download_success(  # NOTIFICATION
        track={  # NOTIFICATION
            'name': 'Test Track',  # NOTIFICATION
            'artists': [{'name': 'Test Artist'}],  # NOTIFICATION
            'album': {'images': [{'url': ''}]},  # NOTIFICATION
        },  # NOTIFICATION
        quality_report={  # NOTIFICATION
            'bitrate_achieved': '320kbps',  # NOTIFICATION
            'source_platform': 'youtube',  # NOTIFICATION
            'title_similarity_score': 0.95,  # NOTIFICATION
        },  # NOTIFICATION
    )  # NOTIFICATION
    logger.info("[notifications] Test notification sent to Telegram + Discord")  # NOTIFICATION
