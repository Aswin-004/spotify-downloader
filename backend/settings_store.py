"""
settings_store.py
=================
Single source of truth for all user-configurable settings.

Priority (highest to lowest):
  1. user_config.json  — written by the Settings UI
  2. .env              — developer fallback / initial defaults

Call load() to get current settings.
Call save(patch) to persist a partial update.
Call apply_to_config() to push values into the live config object so
  the backend picks them up without a restart.
"""
import json
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_CONFIG_PATH = Path(__file__).parent / "user_config.json"
_lock = threading.Lock()

# ── Keys that are safe to return to the frontend (no masking needed) ─────────
_PUBLIC_KEYS = {
    "base_download_dir",
    "ingest_playlist_id",
    "check_interval",
    "redirect_uri",
    "fpcalc_path",
    "notify_storage_threshold_mb",
    "telegram_chat_id",
}

# ── Keys that hold secrets — returned masked unless full=True ─────────────────
_SECRET_KEYS = {
    "spotify_client_id",
    "spotify_client_secret",
    "gemini_api_key",
    "lastfm_api_key",
    "acoustid_api_key",
    "telegram_bot_token",
}

# ── Defaults read from .env (never overwrite user values, only fill gaps) ────
def _env_defaults() -> dict:
    return {
        "base_download_dir":           os.getenv("BASE_DOWNLOAD_DIR", ""),
        "ingest_playlist_id":          os.getenv("INGEST_PLAYLIST_ID", ""),
        "spotify_client_id":           os.getenv("SPOTIFY_CLIENT_ID", ""),
        "spotify_client_secret":       os.getenv("SPOTIFY_CLIENT_SECRET", ""),
        "gemini_api_key":              os.getenv("GEMINI_API_KEY", ""),
        "lastfm_api_key":              os.getenv("LASTFM_API_KEY", ""),
        "acoustid_api_key":            os.getenv("ACOUSTID_API_KEY", ""),
        "fpcalc_path":                 os.getenv("FPCALC_PATH", ""),
        "telegram_bot_token":          os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id":            os.getenv("TELEGRAM_CHAT_ID", ""),
        "check_interval":              int(os.getenv("CHECK_INTERVAL", "500")),
        "redirect_uri":                os.getenv("REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        "notify_storage_threshold_mb": int(os.getenv("NOTIFY_STORAGE_THRESHOLD_MB", "100000")),
    }


def load() -> dict:
    """Return merged settings: user_config.json values override .env defaults."""
    defaults = _env_defaults()
    try:
        stored = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        stored = {}
    merged = {**defaults, **{k: v for k, v in stored.items() if v not in (None, "")}}
    return merged


def save(patch: dict) -> dict:
    """
    Merge patch into user_config.json and return the full updated settings.
    Only non-empty values overwrite existing entries.
    """
    with _lock:
        try:
            current = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        for k, v in patch.items():
            if v is not None and v != "":
                current[k] = v
            elif k in current and v == "":
                # Explicit empty string = user cleared the field → remove it
                # so .env default takes over
                current.pop(k, None)
        _CONFIG_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return load()


def for_frontend(full_secrets: bool = False) -> dict:
    """
    Return settings safe for the frontend.
    Secrets are masked (first 4 chars + ***) unless full_secrets=True.
    """
    data = load()
    out = {}
    for k, v in data.items():
        if k in _SECRET_KEYS and not full_secrets:
            if v:
                out[k] = v[:4] + "***" if len(v) > 4 else "***"
            else:
                out[k] = ""
        else:
            out[k] = v
    # Always include a flag so the UI knows if essential setup is complete
    out["_setup_complete"] = bool(
        data.get("base_download_dir")
        and data.get("spotify_client_id")
        and data.get("spotify_client_secret")
    )
    return out


def apply_to_config():
    """
    Push current settings into the live config object and key services
    so changes take effect without a backend restart.
    """
    from config import config as _cfg
    settings = load()

    if settings.get("base_download_dir"):
        _cfg.BASE_DOWNLOAD_DIR = settings["base_download_dir"]
        _cfg.DOWNLOAD_PATH = os.path.join(settings["base_download_dir"], "Manual")

    if settings.get("spotify_client_id"):
        _cfg.SPOTIFY_CLIENT_ID = settings["spotify_client_id"]
        os.environ["SPOTIFY_CLIENT_ID"] = settings["spotify_client_id"]
    if settings.get("spotify_client_secret"):
        _cfg.SPOTIFY_CLIENT_SECRET = settings["spotify_client_secret"]
        os.environ["SPOTIFY_CLIENT_SECRET"] = settings["spotify_client_secret"]

    if settings.get("gemini_api_key"):
        _cfg.GEMINI_API_KEY = settings["gemini_api_key"]
        os.environ["GEMINI_API_KEY"] = settings["gemini_api_key"]

    if settings.get("lastfm_api_key"):
        _cfg.LASTFM_API_KEY = settings["lastfm_api_key"]
        os.environ["LASTFM_API_KEY"] = settings["lastfm_api_key"]

    if settings.get("acoustid_api_key"):
        os.environ["ACOUSTID_API_KEY"] = settings["acoustid_api_key"]

    if settings.get("fpcalc_path"):
        os.environ["FPCALC_PATH"] = settings["fpcalc_path"]

    if settings.get("telegram_bot_token"):
        os.environ["TELEGRAM_BOT_TOKEN"] = settings["telegram_bot_token"]
    if settings.get("telegram_chat_id"):
        os.environ["TELEGRAM_CHAT_ID"] = settings["telegram_chat_id"]

    if settings.get("ingest_playlist_id"):
        _cfg.INGEST_PLAYLIST_ID = settings["ingest_playlist_id"]

    if settings.get("check_interval"):
        _cfg.CHECK_INTERVAL = int(settings["check_interval"])

    if settings.get("notify_storage_threshold_mb"):
        os.environ["NOTIFY_STORAGE_THRESHOLD_MB"] = str(settings["notify_storage_threshold_mb"])

    if settings.get("redirect_uri"):
        _cfg.REDIRECT_URI = settings["redirect_uri"]
        os.environ["REDIRECT_URI"] = settings["redirect_uri"]
