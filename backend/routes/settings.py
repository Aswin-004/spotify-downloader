"""
Settings Blueprint
==================
App configuration, custom folder mappings, and scan-folder endpoints.
"""
from flask import Blueprint, jsonify, request
import os

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

import settings_store as _settings_store
from services.auto_downloader import BASE_DOWNLOAD_DIR

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/api/settings/app-config", methods=["GET"])
def settings_get_app_config():
    try:
        return jsonify(_settings_store.for_frontend()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/api/settings/app-config", methods=["POST"])
def settings_save_app_config():
    data = request.get_json(silent=True) or {}
    try:
        updated = _settings_store.save(data)
        _settings_store.apply_to_config()

        if "ingest_playlist_id" in data:
            try:
                import services.auto_downloader as _ad
                _ad.INGEST_PLAYLIST_ID = updated.get("ingest_playlist_id", "")
            except Exception:
                pass

        return jsonify({"success": True, "config": _settings_store.for_frontend()}), 200
    except Exception as e:
        logger.error(f"[settings] Failed to save config: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/api/settings/scan-folders", methods=["GET"])
def settings_scan_folders():
    try:
        if not BASE_DOWNLOAD_DIR or not os.path.isdir(BASE_DOWNLOAD_DIR):
            return jsonify({"folders": [], "warning": "Music folder not configured or does not exist"}), 200
        entries = [
            e for e in os.listdir(BASE_DOWNLOAD_DIR)
            if os.path.isdir(os.path.join(BASE_DOWNLOAD_DIR, e)) and not e.startswith(".")
        ]
        return jsonify({"folders": sorted(entries)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/api/settings/custom-folders", methods=["GET"])
def settings_get_custom_folders():
    try:
        from database import get_custom_folder_mappings_collection
        docs = list(get_custom_folder_mappings_collection().find({}, {"_id": 0}))
        return jsonify({"mappings": docs}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/api/settings/custom-folders", methods=["POST"])
def settings_save_custom_folder():
    data = request.get_json(silent=True) or {}
    folder_name = (data.get("folder_name") or "").strip()
    genre_label = (data.get("genre_label") or "").strip()
    if not folder_name or not genre_label:
        return jsonify({"error": "folder_name and genre_label are required"}), 400
    try:
        from datetime import datetime
        from database import get_custom_folder_mappings_collection
        get_custom_folder_mappings_collection().update_one(
            {"folder_name": folder_name},
            {"$set": {
                "folder_name": folder_name,
                "genre_label": genre_label,
                "updated_at": datetime.utcnow().isoformat(),
            }},
            upsert=True,
        )
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@settings_bp.route("/api/settings/custom-folders/<path:folder_name>", methods=["DELETE"])
def settings_delete_custom_folder(folder_name):
    try:
        from database import get_custom_folder_mappings_collection
        get_custom_folder_mappings_collection().delete_one({"folder_name": folder_name})
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
