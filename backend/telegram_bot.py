"""
Telegram Bot Controller — Spotify Meta Downloader
==================================================
Runs in a background daemon thread inside the Flask process.
Provides full remote control over the downloader via Telegram commands.

Commands:
  /start          — welcome message + command list
  /status         — current download + auto-downloader status
  /pause          — pause the auto downloader
  /resume         — resume the auto downloader
  /progress       — current download progress
  /library N      — last N downloaded tracks (default 10, paginated 5/page)
  /find query     — search library by artist or title (top 5)
  /location       — folder path of the last downloaded file
  /skipped        — list permanently failed tracks
  /reset_skipped  — clear ingest_failures.json (unblock all)
  /storage        — disk usage of the download directory
  /help           — show this message
  <spotify_url>   — download any Spotify track / playlist / album immediately

Security:
  Every handler checks auth_check() — all messages not from TELEGRAM_CHAT_ID
  are rejected immediately.

Thread model:
  The bot runs in a single daemon thread with its own asyncio event loop.
  Spotify downloads are dispatched into ADDITIONAL daemon threads so they
  never block the bot's event loop.  Messages from download threads are sent
  synchronously via httpx (already a project dependency).

Circular-import safety:
  AUTO_DOWNLOADER_PAUSED is defined here at module level.
  auto_downloader.py imports it from here.
  This module does NOT import from auto_downloader / app at module level —
  all cross-module state is accessed lazily inside handler bodies.
"""

import asyncio
import functools
import json
import os
import re
import shutil
import threading
from collections import defaultdict
from datetime import datetime as dt, timedelta
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()


# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION — loaded from .env
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Gracefully strip any non-digit chars from TELEGRAM_CHAT_ID.
# (.env had '7438454756S' — trailing S would crash int())
_raw_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
try:
    TELEGRAM_CHAT_ID: int | None = int(re.sub(r"\D", "", _raw_chat_id)) if _raw_chat_id else None
except (ValueError, TypeError):
    TELEGRAM_CHAT_ID = None


# ═══════════════════════════════════════════════════════════════════
# SHARED PAUSE FLAG — imported by services/auto_downloader.py
# ═══════════════════════════════════════════════════════════════════

# NOT set  → auto downloader runs normally.
# SET      → auto downloader pauses before starting next batch.
# Commands: /pause sets it, /resume clears it.
AUTO_DOWNLOADER_PAUSED: threading.Event = threading.Event()


def _sync_pause_state_from_db() -> None:
    """Load pause state from MongoDB at startup."""
    try:
        db = _get_db()
        doc = db.app_settings.find_one({"_id": "auto_downloader_paused"})
        if doc and doc.get("value"):
            AUTO_DOWNLOADER_PAUSED.set()
            logger.info("[telegram_bot] Pause state from DB: PAUSED")
        else:
            AUTO_DOWNLOADER_PAUSED.clear()
            logger.info("[telegram_bot] Pause state from DB: RUNNING")
    except Exception as e:
        logger.warning(f"[telegram_bot] Could not load pause state: {e}")
        AUTO_DOWNLOADER_PAUSED.clear()


def _persist_pause_state(paused: bool) -> bool:
    """Save pause state to MongoDB."""
    try:
        db = _get_db()
        db.app_settings.update_one(
            {"_id": "auto_downloader_paused"},
            {"$set": {"value": paused, "updated_at": dt.utcnow()}},
            upsert=True,
        )
        logger.info(f"[telegram_bot] Pause state saved: {paused}")
        return True
    except Exception as e:
        logger.error(f"[telegram_bot] Persist failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# TELEGRAM LIBRARY IMPORT (graceful fallback if PTB absent)
# ═══════════════════════════════════════════════════════════════════

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        filters,
        ContextTypes,
    )
    _PTB_AVAILABLE = True
except ImportError:
    _PTB_AVAILABLE = False
    logger.warning("[telegram_bot] python-telegram-bot not installed — bot disabled")


# ═══════════════════════════════════════════════════════════════════
# LAZY STATE ACCESSORS
# (all cross-module imports happen inside function bodies to avoid
#  circular imports at module load time)
# ═══════════════════════════════════════════════════════════════════

def _get_auto_status() -> dict:
    """Return a snapshot of AUTO_STATUS from auto_downloader."""
    try:
        from services.auto_downloader import AUTO_STATUS
        return dict(AUTO_STATUS)
    except Exception:
        return {}


def _get_download_status() -> dict:
    """Return a snapshot of download_status from app."""
    try:
        import app as _app  # safe: app is fully loaded before bot receives any command
        return dict(_app.download_status)
    except Exception:
        return {}


def _get_queue_status() -> dict:
    """Return a snapshot of download_queue_status from downloader_service."""
    try:
        from services.downloader_service import download_queue_status
        return dict(download_queue_status)
    except Exception:
        return {}


def _get_db():
    """Return the MongoDB database instance."""
    from database import _get_db as __get_db
    return __get_db()


def _get_base_dir() -> str:
    """Return BASE_DOWNLOAD_DIR from environment."""
    return os.getenv("BASE_DOWNLOAD_DIR", "downloads")


def _get_failures_file() -> str:
    """Absolute path to ingest_failures.json (sits in backend root)."""
    from pathlib import Path
    backend_root = Path(__file__).resolve().parent
    return str(backend_root / "ingest_failures.json")


# ═══════════════════════════════════════════════════════════════════
# ERROR HANDLING DECORATOR
# ═══════════════════════════════════════════════════════════════════

def handle_command_error(cmd_name: str):
    """Decorator to safely catch all command errors."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
            try:
                return await func(update, context)
            except Exception as e:
                logger.exception(f"[{cmd_name}] Error: {e}")
                try:
                    from telegram.error import TelegramError
                    if isinstance(e, TelegramError):
                        await update.message.reply_text("⚠️ Telegram error. Try again soon.")
                    else:
                        await update.message.reply_text(f"❌ Command failed: {str(e)[:80]}")
                except Exception:
                    logger.error(f"[{cmd_name}] Could not send error message")
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self, max_calls: int = 10, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls: dict = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = dt.utcnow()
        cutoff = now - timedelta(seconds=self.window_seconds)
        self.calls[user_id] = [t for t in self.calls[user_id] if t > cutoff]
        if len(self.calls[user_id]) < self.max_calls:
            self.calls[user_id].append(now)
            return True
        return False

    def get_reset_time(self, user_id: int) -> int:
        if not self.calls[user_id]:
            return 0
        oldest = self.calls[user_id][0]
        reset = oldest + timedelta(seconds=self.window_seconds)
        return max(0, int((reset - dt.utcnow()).total_seconds()))


_rate_limiter = RateLimiter(max_calls=10, window_seconds=60)


# ═══════════════════════════════════════════════════════════════════
# UTILITY HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_bytes(n: int) -> str:
    """Format a byte count into human-readable string (B / KB / MB / GB / TB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _send_message_sync(chat_id: int, text: str) -> None:
    """
    Send a Telegram message synchronously via the Bot API HTTP endpoint.
    Used from background download threads where there is no event loop.
    Reuses httpx which is already a project dependency (notifications_service).
    """
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"[telegram_bot] _send_message_sync failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# SECURITY — auth gate used by every command handler
# ═══════════════════════════════════════════════════════════════════

async def _auth_check(update: "Update") -> bool:
    """Return True if message is from the authorised chat. Rejects others."""
    if TELEGRAM_CHAT_ID is None:
        return False
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        logger.warning(
            f"[telegram_bot] Rejected message from chat_id={update.effective_chat.id}"
        )
        return False
    return True


# ═══════════════════════════════════════════════════════════════════
# HELP TEXT
# ═══════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "🎵 <b>Spotify Downloader Bot</b>\n\n"
    "<b>Available commands:</b>\n"
    "/status        — current download status\n"
    "/pause         — pause auto downloader\n"
    "/resume        — resume auto downloader\n"
    "/progress      — current download progress\n"
    "/library N     — last N downloaded tracks (default 10)\n"
    "/find query    — search library by artist or title\n"
    "/location      — folder path of last downloaded file\n"
    "/skipped       — list permanently failed tracks\n"
    "/reset_skipped — unblock all skipped tracks\n"
    "/storage       — disk usage of download directory\n"
    "/organize      — reorganise library into language/genre folders\n"
    "/help          — show this message\n\n"
    "Or send any Spotify link to download immediately."
)


# ═══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return
    await update.message.reply_text(
        f"👋 <b>Welcome to Spotify Downloader Bot!</b>\n\n{HELP_TEXT}",
        parse_mode="HTML",
    )


async def cmd_help(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


@handle_command_error("cmd_status")
async def cmd_status(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_status] Rate limit: user {user_id}")
        return

    auto  = _get_auto_status()
    dl    = _get_download_status()
    queue = _get_queue_status()

    paused_label = "⏸ <b>PAUSED</b>" if AUTO_DOWNLOADER_PAUSED.is_set() else "▶ Running"
    auto_status  = auto.get("status", "idle").capitalize()
    dl_status    = dl.get("status", "idle").capitalize()
    pending      = len(queue.get("pending", []))
    last_track   = auto.get("last", "")

    lines = [
        "📊 <b>Status</b>",
        "",
        f"🔄 <b>Manual download:</b>  {dl_status}",
        f"🤖 <b>Auto downloader:</b>  {auto_status} | {paused_label}",
        f"📋 <b>Queue:</b>           {pending} track(s) pending",
    ]
    if auto.get("current"):
        lines.append(f"▶️  <b>Now:</b>            {auto['current']}")
    if last_track:
        lines.append(f"🎵 <b>Last:</b>            {last_track}")
    if auto.get("last_checked"):
        lines.append(f"🕐 <b>Last checked:</b>    {auto['last_checked']}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_pause(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return
    try:
        AUTO_DOWNLOADER_PAUSED.set()
        _persist_pause_state(True)
        logger.info("[telegram_bot] Auto downloader paused via /pause")
        await update.message.reply_text(
            "⏸ <b>Auto downloader paused.</b>\n\nSend /resume to restart.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(f"[cmd_pause] Error: {e}")
        await update.message.reply_text(f"❌ Failed: {str(e)[:80]}")


async def cmd_resume(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return
    try:
        AUTO_DOWNLOADER_PAUSED.clear()
        _persist_pause_state(False)
        logger.info("[telegram_bot] Auto downloader resumed via /resume")
        await update.message.reply_text(
            "▶ <b>Auto downloader resumed.</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(f"[cmd_resume] Error: {e}")
        await update.message.reply_text(f"❌ Failed: {str(e)[:80]}")


async def cmd_progress(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    dl    = _get_download_status()
    auto  = _get_auto_status()
    queue = _get_queue_status()

    if dl.get("status") in ("downloading", "starting"):
        # Manual / API-triggered download in progress
        track = dl.get("current", "Unknown")
        pct   = dl.get("progress", 0)
        msg = (
            f"⏳ <b>Downloading:</b> {track}\n"
            f"📊 <b>Progress:</b>    {pct}%"
        )
    elif auto.get("status") == "downloading":
        # Auto-ingest download in progress
        current   = auto.get("current", "Unknown")
        pct       = auto.get("progress", 0)
        completed = auto.get("completed", 0)
        total     = auto.get("total", 0)
        msg = (
            f"⏳ <b>Auto Downloading:</b> {current}\n"
            f"📊 <b>Progress:</b>         {pct}%  ({completed}/{total} tracks)"
        )
    else:
        current_q = queue.get("current") or ""
        if current_q:
            msg = f"💤 <b>Idle.</b> Last queued: {current_q}"
        else:
            msg = "💤 <b>No download in progress.</b>"

    await update.message.reply_text(msg, parse_mode="HTML")


# ─── /library — paginated listing ─────────────────────────────────

_LIB_PAGE_SIZE = 5


@handle_command_error("cmd_library")
async def cmd_library(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_library] Rate limit: user {user_id}")
        return

    try:
        n = int(context.args[0]) if context.args else 10
        n = max(1, min(n, 100))
    except (ValueError, IndexError):
        n = 10

    try:
        db = _get_db()
        docs = list(
            db.download_history.find(
                {},
                {
                    "_id": 0,
                    "track_title": 1,
                    "artist": 1,
                    "filename": 1,
                    "folder": 1,
                    "downloaded_at": 1,
                    "bitrate_achieved": 1,
                },
            )
            .sort("downloaded_at", -1)
            .limit(n)
        )
    except Exception as e:
        logger.error(f"[telegram_bot] /library DB error: {e}")
        await update.message.reply_text(f"❌ Database error: {e}")
        return

    if not docs:
        await update.message.reply_text("📭 No downloads found in history.")
        return

    # Cache full list for pagination callbacks
    context.user_data["lib_docs"]  = docs
    context.user_data["lib_page"]  = 0
    context.user_data["lib_total"] = n

    await _render_library_page(
        target=update.message,
        docs=docs,
        page=0,
        edit=False,
    )


async def _render_library_page(
    target,           # Message (for send) or CallbackQuery (for edit)
    docs: list,
    page: int,
    edit: bool = False,
) -> None:
    """Build and send/edit a page of library results with Prev / Next buttons."""
    total    = len(docs)
    start    = page * _LIB_PAGE_SIZE
    end      = min(start + _LIB_PAGE_SIZE, total)
    n_pages  = max(1, (total + _LIB_PAGE_SIZE - 1) // _LIB_PAGE_SIZE)

    lines = [f"📚 <b>Library</b>  —  {total} track(s)  (page {page + 1}/{n_pages})\n"]
    for i, doc in enumerate(docs[start:end], start=start + 1):
        title   = doc.get("track_title") or "Unknown"
        artist  = doc.get("artist", "Unknown")
        bitrate = doc.get("bitrate_achieved", "")
        suffix  = f"  <i>· {bitrate}</i>" if bitrate else ""
        lines.append(f"{i}. <b>{title}</b> — {artist}{suffix}")

    text = "\n".join(lines)

    # Pagination row
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀ Prev", callback_data=f"lib_page_{page - 1}"))
    if end < total:
        row.append(InlineKeyboardButton("Next ▶", callback_data=f"lib_page_{page + 1}"))

    markup = InlineKeyboardMarkup([row]) if row else None

    if edit:
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def handle_library_pagination(
    update: "Update", context: "ContextTypes.DEFAULT_TYPE"
) -> None:
    """Handle Prev / Next button presses — edits existing message in-place."""
    query = update.callback_query
    await query.answer()

    # Auth check on callback
    if TELEGRAM_CHAT_ID is not None and query.message.chat.id != TELEGRAM_CHAT_ID:
        return

    try:
        page = int(query.data.split("_")[-1])
    except (ValueError, IndexError):
        return

    docs = context.user_data.get("lib_docs")
    if not docs:
        await query.edit_message_text(
            "❌ Session expired. Run /library again.", parse_mode="HTML"
        )
        return

    context.user_data["lib_page"] = page
    await _render_library_page(target=query, docs=docs, page=page, edit=True)


# ─── /find — regex search ─────────────────────────────────────────

@handle_command_error("cmd_find")
async def cmd_find(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_find] Rate limit: user {user_id}")
        return

    if not context.args:
        await update.message.reply_text("Usage: /find &lt;query&gt;", parse_mode="HTML")
        return

    query_str = " ".join(context.args)

    try:
        db    = _get_db()
        regex = {"$regex": query_str, "$options": "i"}
        docs  = list(
            db.download_history.find(
                {"$or": [{"track_title": regex}, {"artist": regex}]},
                {"_id": 0, "track_title": 1, "artist": 1, "folder": 1, "filename": 1},
            ).limit(5)
        )
    except Exception as e:
        logger.error(f"[telegram_bot] /find DB error: {e}")
        await update.message.reply_text(f"❌ Database error: {e}")
        return

    if not docs:
        await update.message.reply_text(
            f'🔍 No results for "<b>{query_str}</b>".', parse_mode="HTML"
        )
        return

    lines = [f'🔍 <b>Results for "{query_str}":</b>\n']
    for i, doc in enumerate(docs, 1):
        title    = doc.get("track_title") or "Unknown"
        artist   = doc.get("artist", "Unknown")
        folder   = doc.get("folder", "")
        filename = doc.get("filename", "")
        path_str = f"{folder}\\{filename}" if folder and filename else (filename or folder)
        lines.append(f"{i}. <b>{title}</b> — {artist}")
        if path_str:
            lines.append(f"   📁 <code>{path_str}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─── /location — last download path ───────────────────────────────

@handle_command_error("cmd_location")
async def cmd_location(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_location] Rate limit: user {user_id}")
        return

    try:
        db   = _get_db()
        docs = list(
            db.download_history.find(
                {"filename": {"$exists": True, "$ne": ""}},
                {
                    "_id": 0,
                    "track_title": 1,
                    "artist": 1,
                    "folder": 1,
                    "filename": 1,
                    "relative_path": 1,
                },
            )
            .sort("downloaded_at", -1)
            .limit(1)
        )
        doc = docs[0] if docs else None
    except Exception as e:
        logger.error(f"[telegram_bot] /location DB error: {e}")
        await update.message.reply_text(f"❌ Database error: {e}")
        return

    if not doc:
        await update.message.reply_text("📭 No downloads found.")
        return

    base     = _get_base_dir()
    title    = doc.get("track_title", "Unknown")
    artist   = doc.get("artist", "Unknown")
    folder   = doc.get("folder", "")
    filename = doc.get("filename", "")
    rel      = doc.get("relative_path") or (
        os.path.join(folder, filename) if folder else filename
    )
    full_path = os.path.join(base, rel) if rel else os.path.join(base, folder, filename)

    msg = (
        f"📍 <b>Last download location:</b>\n\n"
        f"🎵 <b>Track :</b>  {title}\n"
        f"🎤 <b>Artist:</b>  {artist}\n"
        f"📁 <b>Folder:</b>  {folder or '(root)'}\n"
        f"💾 <b>File  :</b>  {filename}\n"
        f"🗂  <b>Full path:</b>\n<code>{full_path}</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# ─── /skipped — permanently failed tracks ─────────────────────────

@handle_command_error("cmd_skipped")
async def cmd_skipped(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_skipped] Rate limit: user {user_id}")
        return

    failures_file = _get_failures_file()
    try:
        with open(failures_file, "r") as f:
            counts: dict = json.load(f)
    except FileNotFoundError:
        counts = {}
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading failures file: {e}")
        return

    # MAX_FAIL_ATTEMPTS = 3 (matches auto_downloader.py)
    MAX_ATTEMPTS = 3
    skipped = {tid: c for tid, c in counts.items() if c >= MAX_ATTEMPTS}

    if not skipped:
        await update.message.reply_text("✅ No permanently skipped tracks.")
        return

    lines = [f"🚫 <b>Permanently skipped tracks: {len(skipped)}</b>\n"]
    for i, (tid, count) in enumerate(list(skipped.items())[:20], 1):
        lines.append(f"{i}. <code>{tid}</code> — failed {count} times")
    if len(skipped) > 20:
        lines.append(f"\n… and {len(skipped) - 20} more.")
    lines.append("\nUse /reset_skipped to unblock all.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─── /reset_skipped — clear ingest_failures.json ──────────────────

async def cmd_reset_skipped(
    update: "Update", context: "ContextTypes.DEFAULT_TYPE"
) -> None:
    if not await _auth_check(update):
        return

    failures_file = _get_failures_file()
    try:
        with open(failures_file, "w") as f:
            json.dump({}, f, indent=2)
        logger.info("[telegram_bot] ingest_failures.json cleared via /reset_skipped")
        await update.message.reply_text(
            "✅ <b>All skipped tracks unblocked.</b>\n"
            "They will be retried on the next ingest cycle.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"[telegram_bot] /reset_skipped error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


# ─── /storage — disk usage report ─────────────────────────────────

@handle_command_error("cmd_storage")
async def cmd_storage(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_storage] Rate limit: user {user_id}")
        return

    base = _get_base_dir()
    if not os.path.isdir(base):
        await update.message.reply_text(
            f"❌ Download directory not found:\n<code>{base}</code>", parse_mode="HTML"
        )
        return

    total_bytes = 0
    total_mp3s  = 0
    subfolders: set = set()

    for root, dirs, files in os.walk(base):
        rel = os.path.relpath(root, base)
        if rel != ".":
            subfolders.add(rel.split(os.sep)[0])
        for fname in files:
            if fname.lower().endswith(".mp3"):
                total_mp3s += 1
                try:
                    total_bytes += os.path.getsize(os.path.join(root, fname))
                except OSError:
                    pass

    try:
        free_bytes = shutil.disk_usage(base).free
        free_str   = _fmt_bytes(free_bytes)
    except Exception:
        free_str = "N/A"

    msg = (
        f"💾 <b>Storage Report</b>\n\n"
        f"📁 <b>Directory:</b>\n<code>{base}</code>\n\n"
        f"📊 <b>Total size  :</b>  {_fmt_bytes(total_bytes)}\n"
        f"🎵 <b>Total files :</b>  {total_mp3s} MP3s\n"
        f"📂 <b>Subfolders  :</b>  {len(subfolders)}\n"
        f"💿 <b>Free space  :</b>  {free_str}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


@handle_command_error("cmd_organize")
async def cmd_organize(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_organize] Rate limit: user {user_id}")
        return

    from config import config as _cfg
    source  = _cfg.BASE_DOWNLOAD_DIR
    dest    = _cfg.BASE_DOWNLOAD_DIR + "_organised"
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"🗂 <b>Starting library migration...</b>\n\n"
        f"📁 <b>Source:</b> <code>{source}</code>\n"
        f"📁 <b>Dest  :</b> <code>{dest}</code>\n\n"
        f"I'll send progress updates every 25 files.",
        parse_mode="HTML",
    )
    logger.info(f"[cmd_organize] Dispatching migration thread (source={source})")

    t = threading.Thread(
        target=_run_migration_thread,
        args=(source, dest, chat_id),
        daemon=True,
        name="tg-migrate",
    )
    t.start()


# ═══════════════════════════════════════════════════════════════════
# SPOTIFY LINK HANDLER
# ═══════════════════════════════════════════════════════════════════

_SPOTIFY_URL_RE = re.compile(
    r"https?://open\.spotify\.com/(track|playlist|album)/[A-Za-z0-9]+"
)


def _run_migration_thread(source: str, dest: str, chat_id: int) -> None:
    """
    Run migrate_library in a background daemon thread.
    Sends: start confirmation → progress every 25 files → final report.
    Always non-interactive (Telegram cannot read stdin).
    """
    from pathlib import Path
    from services.library_migrator import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_LOGS_DIR,
        migrate_library,
        build_report_text,
    )

    def _send(msg: str) -> None:
        _send_message_sync(chat_id, msg)

    def progress_cb(done: int, total: int) -> None:
        if total > 0 and (done % 25 == 0 or done == total):
            _send(f"⏳ {done}/{total} files processed...")

    try:
        result = migrate_library(
            source=Path(source),
            dest=Path(dest),
            config_path=DEFAULT_CONFIG_PATH,
            interactive=False,
            dry_run=False,
            logs_dir=DEFAULT_LOGS_DIR,
            progress_cb=progress_cb,
        )
        report = build_report_text(
            category_stats=result.category_stats,
            errors=result.errors,
            skipped_artists=result.skipped_artists,
            undo_log_path=result.undo_log_path,
            duration_seconds=result.duration_seconds,
            html=True,
        )
        _send(f"✅ <b>Migration complete!</b>\n\n{report}")
        logger.info(f"[telegram_bot] /organize done: moved={result.files_moved}")
    except Exception as e:
        logger.exception(f"[telegram_bot] /organize thread error: {e}")
        _send(f"❌ <b>Migration failed:</b>\n{str(e)[:300]}")


def _run_spotify_download(url: str, chat_id: int) -> None:
    """
    Download a Spotify track / playlist / album in a background daemon thread.

    Uses synchronous httpx calls (not the PTB Bot object) to send progress
    messages, so there is no dependency on the main bot event loop.
    Never blocks the Telegram polling loop.
    """
    def _send(msg: str) -> None:
        _send_message_sync(chat_id, msg)

    try:
        from services.spotify_service import get_spotify_service
        from services.downloader_service import get_downloader_service, sanitize_filename
        from utils import extract_spotify_id

        sp       = get_spotify_service()
        dl       = get_downloader_service()
        base     = _get_base_dir()
        url_info = extract_spotify_id(url)
        url_type = url_info["type"]

        if url_type == "track":
            meta        = sp.get_track_metadata(url)
            title       = meta["title"]
            artist      = meta["artist"]
            duration_ms = meta.get("duration_ms")
            art_url     = meta.get("album_art_url")

            _send(f"⬇️ <b>Got it! Downloading:</b>\n{title} — {artist}")

            out_dir = os.path.join(base, "Artists", sanitize_filename(artist))
            os.makedirs(out_dir, exist_ok=True)

            result = dl.download_track(
                title,
                artist,
                duration_ms=duration_ms,
                output_dir=out_dir,
                album_art_url=art_url,
            )

            if result["status"] == "success":
                folder = os.path.relpath(out_dir, base)
                _send(
                    f"✅ <b>Downloaded:</b> {title}\n"
                    f"📁 <b>Saved to:</b>   {folder}\\"
                )
                logger.info(f"[telegram_bot] Downloaded via bot: {title} — {artist}")
            else:
                msg = result.get("message", "Unknown error")
                _send(f"❌ <b>Download failed:</b>\n{msg}")
                logger.warning(f"[telegram_bot] Download failed: {title} — {artist}: {msg}")

        elif url_type in ("playlist", "album"):
            if url_type == "playlist":
                tracks      = sp.get_playlist_tracks(url)
                folder_name = "Playlist"
                try:
                    user_sp = sp._get_user_sp()
                    if user_sp:
                        info        = user_sp.playlist(url_info["id"], fields="name")
                        folder_name = info.get("name", "Playlist")
                except Exception:
                    pass
                folder_name = sanitize_filename(folder_name)
            else:
                album_data  = sp.get_album_tracks(url)
                tracks      = album_data["tracks"]
                folder_name = sanitize_filename(album_data.get("name", "Album"))

            total   = len(tracks)
            out_dir = os.path.join(base, folder_name)
            os.makedirs(out_dir, exist_ok=True)

            _send(
                f"⬇️ <b>Got it! Downloading {url_type}:</b>\n"
                f"{folder_name}  ({total} tracks)"
            )

            success_count = 0
            for track in tracks:
                t_title = track["title"]
                t_artist = track["artist"]
                fname   = sanitize_filename(t_title)
                result  = dl.download_track(
                    t_title,
                    t_artist,
                    output_dir=out_dir,
                    output_filename=fname,
                )
                if result["status"] == "success":
                    success_count += 1

            _send(
                f"✅ <b>{url_type.capitalize()} complete:</b> {folder_name}\n"
                f"📊 <b>Downloaded:</b> {success_count}/{total} tracks\n"
                f"📁 <b>Saved to:</b>   {folder_name}\\"
            )
            logger.info(
                f"[telegram_bot] {url_type} done: {folder_name} "
                f"({success_count}/{total})"
            )

        else:
            _send("❌ Unsupported Spotify URL type (expected track, playlist, or album).")

    except Exception as e:
        logger.error(f"[telegram_bot] Download error for {url}: {e}")
        _send(f"❌ <b>Download failed:</b>\n{str(e)[:300]}")


async def handle_spotify_link(
    update: "Update", context: "ContextTypes.DEFAULT_TYPE"
) -> None:
    """
    Invoked for any text message (non-command) that contains a Spotify URL.
    Validates the URL, sends an acknowledgement, then dispatches the download
    into a separate daemon thread so the bot event loop is never blocked.
    """
    if not await _auth_check(update):
        return

    text  = update.message.text.strip()
    match = _SPOTIFY_URL_RE.search(text)
    if not match:
        # Message matched the regex filter but group capture failed — shouldn't happen
        await update.message.reply_text(
            "❓ Couldn't parse that Spotify URL. "
            "Make sure it's a track, playlist, or album link."
        )
        return

    url     = match.group(0)
    chat_id = update.effective_chat.id

    # Dispatch to daemon thread — never blocks the bot event loop
    t = threading.Thread(
        target=_run_spotify_download,
        args=(url, chat_id),
        daemon=True,
        name=f"tg-dl-{url[-10:]}",
    )
    t.start()
    logger.info(f"[telegram_bot] Dispatched download thread for: {url}")


# ═══════════════════════════════════════════════════════════════════
# BOT THREAD
# ═══════════════════════════════════════════════════════════════════

def _run_bot() -> None:
    """
    Build and run the Telegram Application (blocking).
    Called from start_bot_thread() in a daemon thread with its own event loop.
    """
    _sync_pause_state_from_db()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ── Command handlers ──────────────────────────────────────────
    ptb_app.add_handler(CommandHandler("start",         cmd_start))
    ptb_app.add_handler(CommandHandler("help",          cmd_help))
    ptb_app.add_handler(CommandHandler("status",        cmd_status))
    ptb_app.add_handler(CommandHandler("pause",         cmd_pause))
    ptb_app.add_handler(CommandHandler("resume",        cmd_resume))
    ptb_app.add_handler(CommandHandler("progress",      cmd_progress))
    ptb_app.add_handler(CommandHandler("library",       cmd_library))
    ptb_app.add_handler(CommandHandler("find",          cmd_find))
    ptb_app.add_handler(CommandHandler("location",      cmd_location))
    ptb_app.add_handler(CommandHandler("skipped",       cmd_skipped))
    ptb_app.add_handler(CommandHandler("reset_skipped", cmd_reset_skipped))
    ptb_app.add_handler(CommandHandler("storage",       cmd_storage))
    ptb_app.add_handler(CommandHandler("organize",      cmd_organize))

    # ── /library Prev / Next pagination ──────────────────────────
    ptb_app.add_handler(
        CallbackQueryHandler(handle_library_pagination, pattern=r"^lib_page_\d+$")
    )

    # ── Spotify link handler ──────────────────────────────────────
    # Catches text messages (non-command) containing an open.spotify.com URL
    ptb_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(_SPOTIFY_URL_RE),
            handle_spotify_link,
        )
    )

    logger.info("[telegram_bot] All handlers registered. Starting polling...")
    ptb_app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)


def start_bot_thread() -> None:
    """
    Start the Telegram bot in a background daemon thread.
    Called once from app.py at startup.
    The thread is daemon=True so it terminates automatically when Flask exits.
    """
    if not _PTB_AVAILABLE:
        logger.warning("[telegram_bot] python-telegram-bot not installed — bot disabled")
        return
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("[telegram_bot] TELEGRAM_BOT_TOKEN not set — bot disabled")
        return
    if TELEGRAM_CHAT_ID is None:
        logger.warning(
            "[telegram_bot] TELEGRAM_CHAT_ID not set or could not be parsed — bot disabled"
        )
        return

    thread = threading.Thread(target=_run_bot, daemon=True, name="telegram-bot")
    thread.start()
    logger.success(
        f"[telegram_bot] Bot started in background thread "
        f"(chat_id={TELEGRAM_CHAT_ID})"
    )
