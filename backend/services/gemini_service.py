"""
Gemini API service — fetches rich audio metadata (genre, mood, instruments, description).
BPM and key detection are handled by bpm_key_service; this service covers the rest.
"""
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_client = None


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
    except Exception as e:
        logger.warning(f"[gemini] analyze_audio failed for {filepath}: {e}")
        return {}


def identify_audio(filepath: str) -> dict:
    """Upload to Gemini, identify title/artist + full metadata. Returns {} on failure."""
    try:
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
    except Exception as e:
        logger.warning(f"[gemini] identify_audio failed for {filepath}: {e}")
        return {}
