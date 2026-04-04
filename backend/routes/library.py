"""
Library Routes Blueprint
=========================
Handles library management endpoints including:
  - Batch file organization by artist/genre
  - Library statistics and filtering
"""

from flask import Blueprint, request, jsonify
import os

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from services.organizer_service import organize_library, organize_recent

# Create Blueprint
library_bp = Blueprint("library", __name__, url_prefix="/api/library")


# ═══════════════════════════════════════════════════════════════════
# ORGANIZER ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@library_bp.route("/organize", methods=["POST"])
def organize():
    """
    Batch organize all MP3 files in the library based on organization mode.
    
    Request body:
        {
            "mode": "artist" | "genre" | "artist_genre"
        }
    
    Response:
        {
            "moved": <int>,
            "skipped": <int>,
            "errors": [{"file": <str>, "error": <str>}, ...]
        }
    """
    try:
        data = request.get_json() or {}
        mode = data.get("mode", "artist")
        
        # Validate mode
        if mode not in ["artist", "genre", "artist_genre"]:
            return jsonify({
                "error": f"Invalid mode: {mode}. Expected 'artist', 'genre', or 'artist_genre'"
            }), 400
        
        logger.info(f"[library] Starting batch organize with mode: {mode}")
        
        result = organize_library(mode=mode)
        
        logger.info(f"[library] Batch organize complete: {result}")
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"[library] organize endpoint error: {e}")
        return jsonify({
            "error": str(e)
        }), 500


# ═══════════════════════════════════════════════════════════════════
# ORGANIZE-RECENT ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@library_bp.route("/organize-recent", methods=["POST"])
def organize_recent_endpoint():
    """
    Organize MP3 files in the library root that were modified within the last N hours.

    Request body:
        {
            "mode": "artist" | "genre" | "artist_genre",
            "hours": <int>   (default: 24)
        }

    Response:
        {
            "moved": <int>,
            "skipped": <int>,
            "scanned": <int>,
            "errors": [{"file": <str>, "error": <str>}, ...]
        }
    """
    try:
        data = request.get_json() or {}
        mode = data.get("mode", "artist")
        hours = data.get("hours", 24)

        if mode not in ["artist", "genre", "artist_genre"]:
            return jsonify({
                "error": f"Invalid mode: {mode}. Expected 'artist', 'genre', or 'artist_genre'"
            }), 400

        if not isinstance(hours, (int, float)) or hours <= 0:
            return jsonify({"error": "hours must be a positive number"}), 400

        logger.info(f"[library] organize-recent: mode={mode}, hours={hours}")

        result = organize_recent(mode=mode, hours=int(hours))

        logger.info(f"[library] organize-recent complete: {result}")

        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[library] organize-recent endpoint error: {e}")
        return jsonify({"error": str(e)}), 500
