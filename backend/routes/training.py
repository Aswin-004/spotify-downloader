"""
DJ Training Blueprint (Phase 2)
===============================
Deterministic daily DJ practice session, built on top of the existing
recommendation engine. Read-only — never mutates the library.
"""
from flask import Blueprint, jsonify, request

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

training_bp = Blueprint("training", __name__)


@training_bp.route("/api/dj/training/today", methods=["GET"])
def get_todays_training_session():
    """Return the deterministic daily DJ training session (exactly 5
    exercises when the library allows it). Optional `?date=YYYY-MM-DD`
    overrides the session date (defaults to today, UTC) — used for
    determinism verification and testing, not required for normal use."""
    try:
        from services.training_service import generate_daily_session
        date_str = request.args.get("date") or None
        session = generate_daily_session(date_str=date_str)
        return jsonify(session), 200
    except Exception as e:
        logger.error(f"[training] failed to generate daily session: {e}")
        return jsonify({"error": str(e)}), 500
