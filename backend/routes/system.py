"""
System Blueprint
================
Lightweight status endpoints — auto-downloader state, queue, API usage.
"""
from flask import Blueprint, jsonify

from services.auto_downloader import AUTO_STATUS
from services.downloader_service import download_queue_status
from services.spotify_service import get_api_usage

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/auto-status", methods=["GET"])
def auto_status():
    return jsonify(AUTO_STATUS), 200


@system_bp.route("/api/queue-status", methods=["GET"])
def queue_status():
    return jsonify(download_queue_status), 200


@system_bp.route("/api/api-usage", methods=["GET"])
def api_usage():
    return jsonify(get_api_usage()), 200
