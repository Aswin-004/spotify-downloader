"""
Notifications Blueprint
=======================
Notification channel status/test, and permanently-skipped track management.
"""
from flask import Blueprint, jsonify, request

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/api/notifications/test", methods=["POST"])
def test_notifications_route():
    """Send a test notification to Telegram + Discord."""
    try:
        from services.notifications_service import (
            test_notifications,
            is_telegram_enabled,
            is_discord_enabled,
        )
        test_notifications()
        return jsonify({
            "message": "Test notification sent",
            "telegram_enabled": is_telegram_enabled(),
            "discord_enabled": is_discord_enabled(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@notifications_bp.route("/api/notifications/status", methods=["GET"])
def notifications_status():
    """Return which notification channels are enabled."""
    try:
        from services.notifications_service import (
            is_telegram_enabled,
            is_discord_enabled,
            NOTIFY_ON_SUCCESS,
            NOTIFY_ON_FAILURE,
            NOTIFY_ON_PLAYLIST,
            STORAGE_THRESHOLD_MB,
        )
        return jsonify({
            "telegram_enabled": is_telegram_enabled(),
            "discord_enabled": is_discord_enabled(),
            "notify_on_success": NOTIFY_ON_SUCCESS,
            "notify_on_failure": NOTIFY_ON_FAILURE,
            "notify_on_playlist": NOTIFY_ON_PLAYLIST,
            "storage_threshold_mb": STORAGE_THRESHOLD_MB,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@notifications_bp.route("/api/skipped-tracks", methods=["GET"])
def get_skipped_tracks():
    """Return all permanently skipped tracks and their failure counts."""
    try:
        from services.auto_downloader import _load_failure_counts, MAX_FAIL_ATTEMPTS
        counts = _load_failure_counts()
        skipped = {tid: c for tid, c in counts.items() if c >= MAX_FAIL_ATTEMPTS}
        return jsonify({"skipped": skipped, "threshold": MAX_FAIL_ATTEMPTS, "total": len(skipped)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@notifications_bp.route("/api/skipped-tracks/reset", methods=["POST"])
def reset_skipped_tracks():
    """Reset failure counts — optionally for a single track_id or all."""
    try:
        from services.auto_downloader import (
            _load_failure_counts,
            _save_failure_counts,
            _load_ingest_history,
            _save_ingest_history,
        )
        data = request.get_json(silent=True) or {}
        track_id = data.get("track_id")
        counts = _load_failure_counts()
        history = _load_ingest_history()
        if track_id:
            counts.pop(track_id, None)
            history.discard(track_id)
            msg = f"Reset failure count for {track_id}"
        else:
            reset_ids = [tid for tid, c in counts.items() if c >= 3]
            counts.clear()
            for tid in reset_ids:
                history.discard(tid)
            msg = f"Reset all failure counts ({len(reset_ids)} tracks unblocked)"
        _save_failure_counts(counts)
        _save_ingest_history(history)
        return jsonify({"message": msg})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
