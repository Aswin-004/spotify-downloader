"""
AI genre classification from TEXT (title + artist) — powered by Groq (model set via
config.GROQ_MODEL / GROQ_MODEL env var — see config.py for which models are
actually enabled on this account and why the current default was chosen).
Reads ID3 tags from the file and sends title + artist to the model.
The result keys are still called gemini_* because older library records store them under those names.
To classify by LISTENING to the song (language, lyrics, vocals), use services/ai_listener.py.
"""
import json
import re
from pathlib import Path

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# ── Public constants — callers that import these still work ─────────────────
class GroqQuotaExceeded(Exception):
    """Kept for backward compatibility — never raised with Groq."""


GROQ_DAILY_BUDGET = 9999  # effectively unlimited


def remaining_quota() -> int:
    return GROQ_DAILY_BUDGET


# ── Groq client (lazy init) ──────────────────────────────────────────────────
_client = None


def _get_client():
    global _client
    if _client is None:
        from groq import Groq
        from config import config
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY not set in .env")
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


# ── ID3 tag reader ───────────────────────────────────────────────────────────
def _read_tags(filepath: str) -> dict:
    try:
        from mutagen.id3 import ID3
        tags = ID3(filepath)
        return {
            "title":  str(tags.get("TIT2", "")).strip(),
            "artist": str(tags.get("TPE1", "")).strip(),
            "genre":  str(tags.get("TCON", "")).strip(),
        }
    except Exception:
        return {"title": "", "artist": "", "genre": ""}


# One list of crates for every Groq caller: the one the listening classifier uses (services/ai_listener.py).
from services.ai_listener import CRATES as _CRATES

_GENRE_LIST = ", ".join(_CRATES)

_SYSTEM = (
    "You are a music genre classifier for a DJ library. "
    "Return ONLY a valid JSON object — no markdown, no explanation. "
    "The genre MUST be exactly one of the listed crate names. What each crate holds: "
    + "; ".join(f"{name}: {desc}" for name, desc in _CRATES.items())
    + ". Only use 'Punjabi' for songs sung in Punjabi; a non-film Hindi/Urdu singer-songwriter is 'Indie'."
)


def _call_groq(title: str, artist: str, existing_genre: str = "") -> dict:
    import time
    from config import config
    prompt = (
        f'Track: "{title}" by "{artist}"'
        + (f' (existing tag: "{existing_genre}")' if existing_genre else "")
        + f"\n\nClassify this track. Return ONLY this JSON:\n"
        + "{"
        + f'"genre": "ONE of: {_GENRE_LIST}", '
        + '"subgenre": "specific style e.g. Speed Garage / Bhangra / Drill", '
        + '"mood": "comma-separated moods e.g. energetic, dark, uplifting", '
        + '"instruments": "comma-separated instruments detected", '
        + '"energy": 0.75, '
        + '"description": "one sentence describing the track"'
        + "}"
    )
    client = _get_client()
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                # 600, not 200: reasoning models (e.g. openai/gpt-oss-20b, this
                # project's default) spend part of the completion budget on an
                # internal reasoning pass before writing the JSON answer — observed
                # 102-198 reasoning tokens per call in production (2026-08-25). At
                # 200 total, several real tracks got finish_reason="length" with
                # content truncated or empty. max_completion_tokens is the
                # non-deprecated name for this param (max_tokens still works but
                # the installed SDK flags it deprecated in favor of this one).
                max_completion_tokens=600,
                temperature=0.2,
            )
            if response.choices[0].finish_reason == "length":
                # Budget exceeded anyway (longer-than-usual reasoning, or a very
                # verbose description) — log it so a future recurrence is
                # immediately diagnosable instead of surfacing only as a generic
                # JSON-decode error below.
                logger.warning(
                    f"[ai] Groq response truncated (finish_reason=length) for "
                    f"title={title!r} artist={artist!r} — usage={getattr(response, 'usage', None)}"
                )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return json.loads(raw)
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait = 60 if attempt == 0 else 120
                logger.warning(f"[ai] rate limit hit — waiting {wait}s before retry {attempt+1}/3")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Groq rate limit: all retries exhausted")


# ── Public API (identical signatures to old groq_service) ──────────────────

def analyze_audio(filepath: str) -> dict:
    """Read ID3 tags, classify via Groq, return dict with gemini_* keys."""
    try:
        t = _read_tags(filepath)
        if not t["title"] and not t["artist"]:
            logger.warning(f"[ai] no ID3 tags in {Path(filepath).name} — skipping")
            return {}
        data = _call_groq(t["title"], t["artist"], t["genre"])
        result = {
            "gemini_genre":       str(data.get("genre", "")),
            "gemini_subgenre":    str(data.get("subgenre", "")),
            "gemini_mood":        str(data.get("mood", "")),
            "gemini_instruments": str(data.get("instruments", "")),
            "gemini_energy":      float(data.get("energy", 0.0)),
            "gemini_description": str(data.get("description", "")),
        }
        logger.info(
            f"[ai] {Path(filepath).name} → genre={result['gemini_genre']} "
            f"mood={result['gemini_mood']} energy={result['gemini_energy']}"
        )
        return result
    except GroqQuotaExceeded:
        raise
    except Exception as e:
        logger.warning(f"[ai] analyze_audio failed for {filepath}: {e}")
        return {}


def identify_audio(filepath: str) -> dict:
    """Read ID3 tags, classify via Groq, return title/artist + gemini_* keys."""
    try:
        t = _read_tags(filepath)
        if not t["title"] and not t["artist"]:
            logger.warning(f"[ai] no ID3 tags in {Path(filepath).name} — skipping")
            return {}
        data = _call_groq(t["title"], t["artist"], t["genre"])
        result = {
            "title":              t["title"],
            "artist":             t["artist"],
            "gemini_genre":       str(data.get("genre", "")),
            "gemini_subgenre":    str(data.get("subgenre", "")),
            "gemini_mood":        str(data.get("mood", "")),
            "gemini_instruments": str(data.get("instruments", "")),
            "gemini_bpm":         None,
            "gemini_key":         "",
            "gemini_energy":      float(data.get("energy", 0.0)),
            "gemini_description": str(data.get("description", "")),
        }
        logger.info(
            f"[ai] identify {Path(filepath).name} → title={result['title']!r} "
            f"artist={result['artist']!r} genre={result['gemini_genre']}"
        )
        return result
    except GroqQuotaExceeded:
        raise
    except Exception as e:
        logger.warning(f"[ai] identify_audio failed for {filepath}: {e}")
        return {}
