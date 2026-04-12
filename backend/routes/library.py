"""
Library Routes Blueprint
=========================
Handles library management endpoints.
"""

from flask import Blueprint, jsonify
import os

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# BPM/KEY — import backfill function
try:  # BPM/KEY
    from bpm_key_service import backfill_library  # BPM/KEY
    _BPM_KEY_AVAILABLE = True  # BPM/KEY
except ImportError:  # BPM/KEY
    _BPM_KEY_AVAILABLE = False  # BPM/KEY

# Create Blueprint
library_bp = Blueprint("library", __name__, url_prefix="/api/library")


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
