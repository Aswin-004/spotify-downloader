"""
Genre Blueprint
===============
Genre overrides, catch-all track review, Gemini quota, and genre-cache management.
"""
import os
from pathlib import Path
from flask import Blueprint, jsonify, request

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from services.auto_downloader import BASE_DOWNLOAD_DIR

genre_bp = Blueprint("genre", __name__)


@genre_bp.route("/api/gemini-quota", methods=["GET"])
def gemini_quota():
    """Return AI classifier status (Groq-backed, effectively unlimited)."""
    try:
        from services.gemini_service import remaining_quota, GEMINI_DAILY_BUDGET
        remaining = remaining_quota()
        return jsonify({"remaining": remaining, "total": GEMINI_DAILY_BUDGET, "exhausted": False})
    except Exception as e:
        return jsonify({"remaining": 0, "total": 0, "exhausted": True, "error": str(e)})


@genre_bp.route("/api/genre-overrides", methods=["GET"])
def get_genre_overrides():
    """List all learned artist→genre mappings from artist_memory."""
    try:
        from database import get_artist_memory_collection
        col = get_artist_memory_collection()
        if col is None:
            return jsonify({"overrides": []}), 200
        docs = list(col.find({}, {"_id": 0}).sort("last_seen", -1).limit(500))
        for d in docs:
            if "last_seen" in d and hasattr(d["last_seen"], "isoformat"):
                d["last_seen"] = d["last_seen"].isoformat()
        return jsonify({"overrides": docs}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@genre_bp.route("/api/genre-override/<artist_key>", methods=["DELETE"])
def delete_genre_override(artist_key):
    """Remove a learned artist→genre mapping."""
    try:
        from services.artist_memory_service import forget_artist as _forget_artist
        deleted = _forget_artist(artist_key)
        return jsonify({"ok": True, "deleted": deleted}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@genre_bp.route("/api/catchall-tracks", methods=["GET"])
def get_catchall_tracks():
    """Return all tracks currently sitting in Library/Electronic/ (the catch-all bucket)."""
    from mutagen.id3 import ID3
    from bpm_key_service import tkey_to_camelot as _t2c
    electronic_dir = Path(BASE_DOWNLOAD_DIR) / "Library" / "Electronic"
    items = []
    if electronic_dir.is_dir():
        for fp in sorted(electronic_dir.glob("*.mp3")):
            try:
                tags = ID3(str(fp))
                if str(tags.get("TXXX:catchall_reviewed", "")).strip() == "1":
                    continue
                title = str(tags.get("TIT2", fp.stem)).strip() or fp.stem
                artist = str(tags.get("TPE1", "")).strip()
                conf_tag = tags.get("TXXX:gemini_confidence")
                confidence = float(str(conf_tag).strip()) if conf_tag else 0.0
                bpm_tag = tags.get("TBPM")
                bpm_val = int(float(str(bpm_tag.text[0]).strip())) if bpm_tag and bpm_tag.text else None
                tkey_tag = tags.get("TKEY")
                tkey_str = str(tkey_tag.text[0]).strip() if tkey_tag and tkey_tag.text else ""
                camelot = _t2c(tkey_str)
            except Exception:
                title = fp.stem
                artist = ""
                confidence = 0.0
                bpm_val = None
                camelot = ""
            items.append({
                "title": title,
                "artist": artist,
                "confidence": confidence,
                "suggested_folder": "Library/Electronic",
                "filename": fp.name,
                "id": fp.stem,
                "bpm": bpm_val,
                "camelot_key": camelot,
            })
    return jsonify(items), 200


@genre_bp.route("/api/clear-genre-cache", methods=["POST"])
def clear_genre_cache_endpoint():
    """Clear the in-memory genre cache after updating SPOTIFY_GENRE_MAP."""
    try:
        from services.genre_router import clear_genre_cache as _clear
        _clear()
        return jsonify({"status": "ok", "message": "Genre cache cleared"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
