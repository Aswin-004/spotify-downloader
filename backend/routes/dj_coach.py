"""
DJ Coach Blueprint (Phase 3)
============================
Deterministic daily DJ coaching session, built on top of the Phase 2
Training Engine. Read-only — never mutates the library.
"""
from flask import Blueprint, jsonify, request

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

dj_coach_bp = Blueprint("dj_coach", __name__)


@dj_coach_bp.route("/api/dj/coach/today", methods=["GET"])
def get_todays_coaching_session():
    """Return the deterministic daily DJ Coach session (exactly 5 staged,
    coached exercises when the library allows it). Optional
    `?date=YYYY-MM-DD` overrides the session date (defaults to today,
    UTC) — same convention as GET /api/dj/training/today, which this does
    not replace or modify."""
    try:
        from services.dj_coach_service import generate_daily_coaching_session
        date_str = request.args.get("date") or None
        session = generate_daily_coaching_session(date_str=date_str)
        return jsonify(session), 200
    except Exception as e:
        logger.error(f"[dj_coach] failed to generate daily coaching session: {e}")
        return jsonify({"error": str(e)}), 500
