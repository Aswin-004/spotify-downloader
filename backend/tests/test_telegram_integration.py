"""
Integration tests for Telegram bot handlers.
Run with: pytest tests/test_telegram_integration.py -v
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_auth_check_authorized():
    update = MagicMock()
    update.effective_chat.id = 12345

    with patch("telegram_bot.TELEGRAM_CHAT_ID", 12345):
        from telegram_bot import _auth_check
        assert await _auth_check(update) is True


@pytest.mark.asyncio
async def test_auth_check_unauthorized():
    update = MagicMock()
    update.effective_chat.id = 99999
    update.message.reply_text = AsyncMock()

    with patch("telegram_bot.TELEGRAM_CHAT_ID", 12345):
        from telegram_bot import _auth_check
        assert await _auth_check(update) is False
        update.message.reply_text.assert_called()


@pytest.mark.asyncio
async def test_auth_check_no_chat_id():
    update = MagicMock()
    update.effective_chat.id = 12345

    with patch("telegram_bot.TELEGRAM_CHAT_ID", None):
        from telegram_bot import _auth_check
        assert await _auth_check(update) is False


def test_rate_limiter_allows_under_limit():
    from telegram_bot import RateLimiter
    limiter = RateLimiter(max_calls=2, window_seconds=60)

    assert limiter.is_allowed(123) is True
    assert limiter.is_allowed(123) is True


def test_rate_limiter_blocks_over_limit():
    from telegram_bot import RateLimiter
    limiter = RateLimiter(max_calls=2, window_seconds=60)

    limiter.is_allowed(123)
    limiter.is_allowed(123)
    assert limiter.is_allowed(123) is False  # 3rd call blocked


def test_rate_limiter_independent_users():
    from telegram_bot import RateLimiter
    limiter = RateLimiter(max_calls=1, window_seconds=60)

    assert limiter.is_allowed(111) is True
    assert limiter.is_allowed(222) is True  # different user — not blocked
    assert limiter.is_allowed(111) is False  # same user — blocked


def test_rate_limiter_reset_time():
    from telegram_bot import RateLimiter
    limiter = RateLimiter(max_calls=1, window_seconds=10)

    limiter.is_allowed(123)
    reset_time = limiter.get_reset_time(123)
    assert 0 < reset_time <= 10


def test_rate_limiter_reset_time_empty():
    from telegram_bot import RateLimiter
    limiter = RateLimiter(max_calls=10, window_seconds=60)

    assert limiter.get_reset_time(999) == 0


def test_persist_pause_state_saves_true():
    from telegram_bot import _persist_pause_state

    mock_db = MagicMock()
    mock_db.app_settings.update_one = MagicMock()

    with patch("telegram_bot._get_db", return_value=mock_db):
        result = _persist_pause_state(True)

    assert result is True
    mock_db.app_settings.update_one.assert_called_once()
    call_args = mock_db.app_settings.update_one.call_args
    assert call_args[0][0] == {"_id": "auto_downloader_paused"}
    assert call_args[0][1]["$set"]["value"] is True


def test_persist_pause_state_handles_db_error():
    from telegram_bot import _persist_pause_state

    with patch("telegram_bot._get_db", side_effect=Exception("DB down")):
        result = _persist_pause_state(True)

    assert result is False


def test_sync_pause_state_sets_event_when_paused():
    from telegram_bot import _sync_pause_state_from_db, AUTO_DOWNLOADER_PAUSED

    AUTO_DOWNLOADER_PAUSED.clear()
    mock_db = MagicMock()
    mock_db.app_settings.find_one.return_value = {"_id": "auto_downloader_paused", "value": True}

    with patch("telegram_bot._get_db", return_value=mock_db):
        _sync_pause_state_from_db()

    assert AUTO_DOWNLOADER_PAUSED.is_set()


def test_sync_pause_state_clears_event_when_running():
    from telegram_bot import _sync_pause_state_from_db, AUTO_DOWNLOADER_PAUSED

    AUTO_DOWNLOADER_PAUSED.set()
    mock_db = MagicMock()
    mock_db.app_settings.find_one.return_value = {"_id": "auto_downloader_paused", "value": False}

    with patch("telegram_bot._get_db", return_value=mock_db):
        _sync_pause_state_from_db()

    assert not AUTO_DOWNLOADER_PAUSED.is_set()


def test_sync_pause_state_clears_on_db_error():
    from telegram_bot import _sync_pause_state_from_db, AUTO_DOWNLOADER_PAUSED

    AUTO_DOWNLOADER_PAUSED.set()
    with patch("telegram_bot._get_db", side_effect=Exception("DB down")):
        _sync_pause_state_from_db()

    assert not AUTO_DOWNLOADER_PAUSED.is_set()


# ── Task 8: Telegram /organize ───────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_organize_dispatches_thread():
    update = MagicMock()
    update.effective_chat.id = 12345
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("telegram_bot.TELEGRAM_CHAT_ID", 12345), \
         patch("telegram_bot._rate_limiter") as mock_rl, \
         patch("telegram_bot.threading.Thread") as mock_thread:
        mock_rl.is_allowed.return_value = True
        mock_thread.return_value.start = MagicMock()
        from telegram_bot import cmd_organize
        await cmd_organize(update, context)
        assert update.message.reply_text.called
        mock_thread.return_value.start.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_organize_rate_limited():
    update = MagicMock()
    update.effective_chat.id = 12345
    update.effective_user.id = 99
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("telegram_bot.TELEGRAM_CHAT_ID", 12345), \
         patch("telegram_bot._rate_limiter") as mock_rl:
        mock_rl.is_allowed.return_value = False
        mock_rl.get_reset_time.return_value = 30
        from telegram_bot import cmd_organize
        await cmd_organize(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "Rate limited" in text or "⏱️" in text
