"""
Gemini API service — fetches rich audio metadata (genre, mood, instruments, description).
BPM and key detection are handled by bpm_key_service; this service covers the rest.
"""
import json
import logging
import re
import threading
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_client = None

# Persist quota exhaustion across restarts so the pre-check survives process restarts.
_QUOTA_FLAG_PATH = Path(__file__).parent.parent / ".gemini_quota_exhausted"


def _load_persisted_quota() -> int:
    """Return persisted _budget_count for today, or 0 if no valid record."""
    try:
        text = _QUOTA_FLAG_PATH.read_text().strip()
        flag_date_str, count_str = text.split(",")
        if flag_date_str == str(date.today()):
            return int(count_str)
    except Exception:
        pass
    return 0


def _persist_quota(count: int) -> None:
    try:
        _QUOTA_FLAG_PATH.write_text(f"{date.today()},{count}")
    except Exception:
        pass


class GeminiQuotaExceeded(Exception):
    """Raised when the Gemini daily request budget is exhausted."""


# ── Daily budget tracker ────────────────────────────────────────────────────
# Gemini free tier: 20 requests/day for gemini-2.5-flash.
# We cap internally at DAILY_BUDGET so manual retries and the maintenance
# worker share the quota without racing to exhaust it first.
GEMINI_DAILY_BUDGET = 15  # leaves 5 slots as a buffer below the 20/day hard limit

_budget_lock = threading.Lock()
_budget_count = _load_persisted_quota()  # survives restarts
_budget_date = date.today()


def remaining_quota() -> int:
    """Return how many Gemini calls are left in today's budget."""
    global _budget_count, _budget_date
    with _budget_lock:
        if date.today() != _budget_date:
            return GEMINI_DAILY_BUDGET
        return max(0, GEMINI_DAILY_BUDGET - _budget_count)


def _check_and_increment():
    """Consume one quota slot. Raises GeminiQuotaExceeded if the budget is spent."""
    global _budget_count, _budget_date
    with _budget_lock:
        today = date.today()
        if today != _budget_date:
            _budget_count = 0
            _budget_date = today
        if _budget_count >= GEMINI_DAILY_BUDGET:
            raise GeminiQuotaExceeded(
                f"Gemini daily budget of {GEMINI_DAILY_BUDGET} calls exhausted — resets at midnight"
            )
        _budget_count += 1
        _persist_quota(_budget_count)
        logger.debug(f"[gemini] quota slot {_budget_count}/{GEMINI_DAILY_BUDGET} consumed")


def _exhaust_quota():
    """Pin the internal counter to the max and persist so restarts see it too."""
    global _budget_count
    with _budget_lock:
        _budget_count = GEMINI_DAILY_BUDGET
        _persist_quota(_budget_count)


def _is_quota_error(exc: Exception) -> bool:
    # Only true quota exhaustion — NOT transient 503/rate-limit errors which should be retried
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "resource_exhausted", "resource exhausted", "daily limit", "quota exceeded"))


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("503", "service unavailable", "rate limit", "try again"))


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from config import config
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


_PROMPT = """Analyze this audio file and return a JSON object with exactly these fields:
{
  "genre": "ONE of: Bollywood, Punjabi, Tamil, House, Trance, Drum & Bass, UK Garage, Dubstep, Techno, Grime, Electronic, Hip Hop, R&B, Pop, Latin, or other",
  "subgenre": "subgenre or style string (e.g. Psytrance, Bhangra, Speed Garage)",
  "mood": "comma-separated moods (e.g. energetic, dark, uplifting)",
  "instruments": "comma-separated instruments detected",
  "energy": 0.75,
  "description": "one sentence description of the track"
}
Return only the JSON object, no markdown, no explanation."""


_IDENTIFY_PROMPT = """Listen to this audio track carefully. Return a JSON object only (no markdown):
{
  "title": "song title, or empty string if you cannot identify it",
  "artist": "primary artist name, or empty string",
  "genre": "ONE of: Bollywood, Punjabi, Tamil, House, Trance, Drum & Bass, UK Garage, Dubstep, Techno, Grime, Electronic, Hip Hop, R&B, Pop, Latin",
  "subgenre": "more specific style (e.g. Speed Garage, Bhangra, Drill, Psytrance)",
  "mood": "comma-separated moods (e.g. energetic, dark, romantic)",
  "instruments": "comma-separated instruments detected",
  "bpm": 128,
  "key": "musical key e.g. C# minor, F major",
  "energy": 0.75,
  "description": "one sentence describing the track"
}"""


def analyze_audio(filepath: str) -> dict:
    """Upload audio to Gemini, return metadata dict with gemini_* keys. Returns {} on failure."""
    try:
        _check_and_increment()
        client = _get_client()
        uploaded = client.files.upload(file=filepath)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded, _PROMPT],
        )
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

        result = {
            "gemini_genre":       str(data.get("genre", "")),
            "gemini_subgenre":    str(data.get("subgenre", "")),
            "gemini_mood":        str(data.get("mood", "")),
            "gemini_instruments": str(data.get("instruments", "")),
            "gemini_energy":      float(data.get("energy", 0.0)),
            "gemini_description": str(data.get("description", "")),
        }
        logger.info(
            f"[gemini] {Path(filepath).name} → genre={result['gemini_genre']} "
            f"mood={result['gemini_mood']} energy={result['gemini_energy']}"
        )
        return result
    except GeminiQuotaExceeded:
        raise
    except Exception as e:
        if _is_quota_error(e):
            _exhaust_quota()
            raise GeminiQuotaExceeded(str(e)) from e
        if _is_transient_error(e):
            raise  # transient — don't exhaust quota, let caller retry
        logger.warning(f"[gemini] analyze_audio failed for {filepath}: {e}")
        return {}


def identify_audio(filepath: str) -> dict:
    """Upload to Gemini, identify title/artist + full metadata. Returns {} on failure."""
    try:
        _check_and_increment()
        client = _get_client()
        uploaded = client.files.upload(file=filepath)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded, _IDENTIFY_PROMPT],
        )
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

        result = {
            "title":              str(data.get("title", "")),
            "artist":             str(data.get("artist", "")),
            "gemini_genre":       str(data.get("genre", "")),
            "gemini_subgenre":    str(data.get("subgenre", "")),
            "gemini_mood":        str(data.get("mood", "")),
            "gemini_instruments": str(data.get("instruments", "")),
            "gemini_bpm":         data.get("bpm"),
            "gemini_key":         str(data.get("key", "")),
            "gemini_energy":      float(data.get("energy", 0.0)),
            "gemini_description": str(data.get("description", "")),
        }
        logger.info(
            f"[gemini] identify {Path(filepath).name} → title={result['title']!r} "
            f"artist={result['artist']!r} genre={result['gemini_genre']}"
        )
        return result
    except GeminiQuotaExceeded:
        raise
    except Exception as e:
        if _is_quota_error(e):
            _exhaust_quota()
            raise GeminiQuotaExceeded(str(e)) from e
        if _is_transient_error(e):
            raise  # transient — don't exhaust quota, let caller retry
        logger.warning(f"[gemini] identify_audio failed for {filepath}: {e}")
        return {}
