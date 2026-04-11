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

# BPM/KEY — import backfill function
try:  # BPM/KEY
    from bpm_key_service import backfill_library  # BPM/KEY
    _BPM_KEY_AVAILABLE = True  # BPM/KEY
except ImportError:  # BPM/KEY
    _BPM_KEY_AVAILABLE = False  # BPM/KEY

# Create Blueprint
library_bp = Blueprint("library", __name__, url_prefix="/api/library")


# ═══════════════════════════════════════════════════════════════════
# ORGANIZER ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@library_bp.route("/organize", methods=["POST"])
def organize():
    """
    Batch organize all MP3 files in the library using DJ_HYBRID classification.
    
    Note: Mode parameter is now deprecated. Always uses dj_hybrid.
    
    Request body:
        {
            "mode": (deprecated, ignored)
        }
    
    Response:
        {
            "moved": <int>,
            "skipped": <int>,
            "errors": [{"file": <str>, "error": <str>}, ...]
        }
    """
    try:
        # FORCE dj_hybrid — ignore user input
        mode = "dj_hybrid"
        
        logger.info(f"[library] Starting batch organize with mode: dj_hybrid")
        
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
    Uses DJ_HYBRID classification.

    Request body:
        {
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
        # FORCE dj_hybrid — ignore mode parameter if provided
        mode = "dj_hybrid"
        hours = data.get("hours", 24)

        if not isinstance(hours, (int, float)) or hours <= 0:
            return jsonify({"error": "hours must be a positive number"}), 400

        logger.info(f"[library] organize-recent: mode=dj_hybrid, hours={hours}")

        result = organize_recent(mode=mode, hours=int(hours))

        logger.info(f"[library] organize-recent complete: {result}")

        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[library] organize-recent endpoint error: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════
# BPM/KEY BACKFILL ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@library_bp.route("/analyze-bpm", methods=["POST"])
def analyze_bpm():
    """Backfill BPM + key for all existing MP3s."""
    if not _BPM_KEY_AVAILABLE:
        return jsonify({"error": "bpm_key_service not available — install librosa"}), 503
    base_dir = os.getenv("BASE_DOWNLOAD_DIR", "downloads")
    result = backfill_library(base_dir)
    return jsonify(result)
