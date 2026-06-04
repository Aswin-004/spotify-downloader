"""
Analytics Blueprint
===================
Read-only analytics endpoints — all proxy directly to analytics_service.
"""
from flask import Blueprint, jsonify, request

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from services.analytics_service import (
    get_overview_stats,
    get_downloads_per_day,
    get_top_artists,
    get_source_breakdown,
    get_tagging_breakdown,
    get_recent_downloads,
    get_failed_downloads,
    get_cache_analytics,
    get_tagging_failure_summary,
    get_weekly_download_stats,
)

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/api/analytics/overview")
def analytics_overview():
    return jsonify(get_overview_stats())


@analytics_bp.route("/api/analytics/downloads-per-day")
def analytics_downloads_per_day():
    days = request.args.get("days", 30, type=int)
    return jsonify(get_downloads_per_day(days))


@analytics_bp.route("/api/analytics/top-artists")
def analytics_top_artists():
    limit = request.args.get("limit", 10, type=int)
    return jsonify(get_top_artists(limit))


@analytics_bp.route("/api/analytics/source-breakdown")
def analytics_source_breakdown():
    return jsonify(get_source_breakdown())


@analytics_bp.route("/api/analytics/tagging-breakdown")
def analytics_tagging_breakdown():
    return jsonify(get_tagging_breakdown())


@analytics_bp.route("/api/analytics/recent")
def analytics_recent():
    return jsonify(get_recent_downloads())


@analytics_bp.route("/api/analytics/failed")
def analytics_failed():
    return jsonify(get_failed_downloads())


@analytics_bp.route("/api/cache-analytics")
def cache_analytics():
    try:
        return jsonify(get_cache_analytics())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/tagging-failures/summary")
def tagging_failures_summary():
    try:
        return jsonify(get_tagging_failure_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analytics_bp.route("/api/download-history/stats")
def download_history_stats():
    try:
        return jsonify(get_weekly_download_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
