"""
Groq "ears": listen to a song and pick its crate when nothing else knows it.

1. listen()   — cut a 25 s clip (at 30 % of the song; a second one at 60 % when the first has no singing),
                send it to Groq Whisper and learn the sung LANGUAGE, a few words of LYRICS and whether there
                are VOCALS at all.
2. classify() — give Groq's chat model the title, artist, language, lyrics and tempo plus the list of YOUR
                crates (each with a one-line description) and let it choose exactly one of them.
3. is_instrumental() — used by the downloader: a song that should be sung in a South Asian language
                (Bollywood, Punjabi ...) but whose download neither has lyrics nor sounds South Asian in three
                clips is a karaoke/instrumental upload and is rejected.

Measured on the real library (2026-09-24): whisper-large-v3 hears Hindi/Urdu/Punjabi correctly where the
turbo model mis-hears Hindi as English, so large-v3 is the default. Whisper never says "no speech" reliably
(no_speech_prob was 0 on instrumentals); instead it writes a filler word ("Music", "موسیقی") or a YouTube
phrase ("Subscribe"), so vocals = at least a few real words left after removing those.

Everything here fails soft: no key, no ffmpeg, a Groq error or a rate limit returns an empty answer and the
caller carries on (evidence → AI → Electronic catch-all). Groq free tier: ~2,000 Whisper requests a day.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from typing import Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)


WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
CLIP_SECONDS = 25
MIN_SUNG_WORDS = 4

# Crates a song may be filed in, with the description the model sees. Order = how they are listed.
CRATES: dict[str, str] = {
    "Bollywood":     "Hindi/Urdu FILM songs and their remixes, qawwali and sufi (Nusrat Fateh Ali Khan), Bollywood party songs",
    "Punjabi":       "songs sung in Punjabi: bhangra, Punjabi pop, Punjabi film songs (Diljit, AP Dhillon, Karan Aujla)",
    "Tamil":         "Tamil / Telugu / other South Indian songs (Anirudh, A.R. Rahman Tamil work)",
    "Indie":         "independent (NOT film) singer-songwriter / acoustic / soft pop, mostly Hindi/Urdu desi indie "
                     "(Anuv Jain, Prateek Kuhad, rohh, Noor, Asim Azhar, Hasan Raheem) and Western indie",
    "Indian Hip Hop": "rap by Indian or Pakistani artists (Seedhe Maut, Divine, KR$NA, Talha Anjum)",
    "International Hip Hop": "rap / trap / drill by artists from outside South Asia",
    "R&B":           "R&B, soul, slow jams",
    "Pop":           "mainstream Western pop (Michael Jackson, Dua Lipa, The Weeknd pop singles)",
    "Latin":         "reggaeton, Latin pop, afrobeats, amapiano, afro/latin house",
    "House":         "house, deep/tech/afro house, melodic house",
    "Techno":        "techno, melodic techno, hard techno",
    "Trance":        "trance, psytrance, progressive trance",
    "Drum & Bass":   "drum and bass, jungle, liquid DnB (about 170-176 BPM)",
    "Dubstep":       "dubstep, riddim, bass music, trap-EDM (Skrillex, Nucleya)",
    "UK Garage":     "UK garage, speed garage, 2-step, bassline",
    "Electronic":    "other electronic/dance music that fits none of the crates above",
}

# What Whisper writes for music with nobody singing (lower-case, punctuation stripped).
_FILLER = re.compile(
    r"\b(?:music|musique|musica|música|musik|موسیقی|موسيقى|संगीत|thank you|thanks for watching|you|"
    r"subscribe|suscríbete al canal|suscribete al canal|like and subscribe|applause)\b",
    re.IGNORECASE,
)
_SCRIPTS = (("ऀ", "ॿ", "Hindi"), ("਀", "੿", "Punjabi"), ("஀", "௿", "Tamil"),
            ("ఀ", "౿", "Telugu"), ("؀", "ۿ", "Urdu"))

_cache: dict = {}
_cache_lock = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────

def _client():
    from services.groq_service import _get_client
    return _get_client()


def script_language(*texts: str) -> str:
    """The language a title/artist is WRITTEN in, from its script ('' for Latin letters)."""
    for text in texts:
        for ch in text or "":
            for lo, hi, name in _SCRIPTS:
                if lo <= ch <= hi:
                    return name
    return ""


def sung_words(text: str) -> int:
    """How many real sung words a Whisper transcript holds once filler words are removed."""
    cleaned = _FILLER.sub(" ", text or "")
    # Whitespace-separated tokens with at least two letters/marks: \w alone splits Hindi words at their
    # vowel signs (matras are "marks", not word characters).
    return sum(1 for tok in cleaned.split()
               if sum(1 for ch in tok if unicodedata.category(ch)[0] in "LM") >= 2)


def _duration(path: str) -> float:
    try:
        from mutagen import File
        return float(File(path).info.length)
    except Exception:
        return 0.0


def _clip(path: str, start: float, ffmpeg: Optional[str] = None) -> bytes:
    if ffmpeg is None:
        from services.downloader_service import _find_ffmpeg_binary
        ffmpeg = _find_ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{start:.1f}", "-t", str(CLIP_SECONDS), "-i", path,
         "-ac", "1", "-ar", "16000", "-b:a", "48k", "-f", "mp3", "-"],
        capture_output=True, timeout=60,
    ).stdout
    if len(out) < 2000:
        raise RuntimeError("ffmpeg produced no audio")
    return out


def _transcribe(audio: bytes) -> tuple[str, str]:
    """(language, text) for one clip. Retries once on a Groq rate limit."""
    for attempt in range(2):
        try:
            r = _client().audio.transcriptions.create(
                file=("clip.mp3", audio), model=WHISPER_MODEL, response_format="verbose_json", temperature=0.0,
            )
            return (getattr(r, "language", "") or "").strip().title(), (getattr(r, "text", "") or "").strip()
        except Exception as exc:
            if attempt == 0 and ("429" in str(exc) or "rate" in str(exc).lower()):
                time.sleep(20)
                continue
            raise
    return "", ""


# ── public API ───────────────────────────────────────────────────────────────

def listen(path: str, *, positions=(0.30, 0.60), stop_on_vocals: bool = True, transcribe=None, clip=None) -> dict:
    """
    {'language': 'Urdu', 'languages': ['Urdu', 'Urdu'], 'lyrics': '...', 'has_vocals': True, 'clips': 2}
    — or {} when listening is impossible (no key/ffmpeg/Groq).

    `language` is the language of the first clip with real lyrics, else the language Whisper detected most
    often. Detection works even when Whisper refuses to transcribe singing over a beat (measured: sung Urdu
    comes back as language=Urdu with the text "موسیقی", an instrumental as language=English with "Music").
    `transcribe`/`clip` are injectable for tests.
    """
    key = (os.path.abspath(path), os.path.getsize(path) if os.path.exists(path) else 0,
           tuple(positions), stop_on_vocals)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    transcribe = transcribe or _transcribe
    clip = clip or _clip
    dur = _duration(path)
    starts = [dur * p for p in positions] if dur > 3 * CLIP_SECONDS else [0.0]
    result = {"language": "", "languages": [], "lyrics": "", "has_vocals": False, "clips": 0}
    try:
        for start in starts:
            lang, text = transcribe(clip(path, start))
            result["clips"] += 1
            if lang:
                result["languages"].append(lang)
            if sung_words(text) >= MIN_SUNG_WORDS:
                if not result["has_vocals"]:
                    result.update(language=lang, lyrics=text[:300], has_vocals=True)
                if stop_on_vocals:
                    break
    except Exception as exc:
        logger.info(f"[ai-listen] could not listen to {os.path.basename(path)}: {exc}")
        return {}
    if not result["language"] and result["languages"]:
        langs = result["languages"]
        result["language"] = max(set(langs), key=langs.count)
    with _cache_lock:
        _cache[key] = result
    return result


# Languages Whisper reports for South Asian singing ("Panjabi" is Whisper's spelling).
INDIC_LANGUAGES = frozenset({"Hindi", "Urdu", "Punjabi", "Panjabi", "Tamil", "Telugu", "Bengali", "Marathi",
                             "Gujarati", "Kannada", "Malayalam", "Sindhi", "Nepali"})
# Crates whose songs are sung in a South Asian language: the only ones the instrumental check runs for,
# because only there does Whisper's language detection separate singing from a backing track.
INDIC_VOCAL_CRATES = frozenset({"Bollywood", "Punjabi", "Tamil", "Indian Hip Hop"})


def is_instrumental(path: str, **kw) -> bool:
    """
    For a song that should be SUNG in a South Asian language: True when three clips (30/50/70 %) have no
    lyrics AND none of them even sounds South Asian — the signature of a karaoke/instrumental upload
    (measured on 'Make Some Noise For The Desi Boyz': English + "Music" in every clip). False when unsure
    or when Groq is unavailable: a wrong rejection costs a good download, so this only says yes on clear audio.
    """
    heard = listen(path, positions=(0.30, 0.50, 0.70), stop_on_vocals=True, **kw)
    if not heard or heard["clips"] < 2:
        return False
    if heard["has_vocals"]:
        return False
    return not any(lang in INDIC_LANGUAGES for lang in heard["languages"])


def _prompt(title, artist, heard, bpm, crates) -> str:
    lines = [f'Title: "{title}"', f'Artist: "{artist}"']
    written = script_language(title, artist)
    if written:
        lines.append(f"Title/artist written in: {written} script")
    if heard:
        if heard.get("has_vocals"):
            lines.append(f"Sung language (heard by Whisper): {heard.get('language') or 'unknown'}")
            lines.append(f'Lyrics heard: "{heard.get("lyrics", "")[:200]}"')
        elif heard.get("language") in INDIC_LANGUAGES:
            lines.append(f"Voice heard in {heard['language']} (Whisper could not transcribe the lyrics over the beat)")
        else:
            lines.append("No lyrics could be transcribed from two 25-second clips (may be instrumental)")
    if bpm:
        lines.append(f"Tempo (from the file's tag; may be half or double the real tempo): {float(bpm):.0f} BPM")
    menu = "\n".join(f"- {name}: {desc}" for name, desc in crates.items())
    return (
        "\n".join(lines)
        + "\n\nPick the ONE DJ crate this song belongs in. Crates:\n" + menu
        + "\n\nRules: Hindi and Urdu are the same spoken language for this purpose. A non-film Hindi/Urdu "
          "singer-songwriter song is Indie, not Bollywood. Use Electronic only when it is electronic music "
          "that fits no other crate. If you do not know the artist, decide from language, lyrics and tempo.\n"
          'Return ONLY JSON: {"crate": "<exact crate name>", "confidence": 0.0-1.0, "reason": "<short>"}'
    )


def classify(path: str, title: str, artist: str, *, bpm=None, crates: Optional[dict] = None,
             heard: Optional[dict] = None, chat=None) -> dict:
    """
    {'crate': 'Indie', 'confidence': 0.8, 'reason': '...', 'language': 'Urdu', 'has_vocals': True}
    or {} when Groq is unavailable / answers with something that is not one of `crates`.
    """
    crates = crates or CRATES
    if heard is None:
        heard = listen(path) if path and os.path.exists(path) else {}
    prompt = _prompt(title, artist, heard, bpm, crates)
    try:
        if chat is None:
            from config import config
            resp = _client().chat.completions.create(
                model=os.getenv("GROQ_CLASSIFY_MODEL", "") or config.GROQ_MODEL,
                messages=[{"role": "system", "content": "You are an expert DJ sorting a DJ library. Answer with JSON only."},
                          {"role": "user", "content": prompt}],
                max_completion_tokens=800, temperature=0.1,
            )
            raw = (resp.choices[0].message.content or "").strip()
        else:
            raw = chat(prompt)
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception as exc:
        logger.info(f"[ai-listen] Groq could not classify {title!r}: {exc}")
        return {}
    crate = str(data.get("crate", "")).strip()
    match = next((c for c in crates if c.lower() == crate.lower()), "")
    if not match:
        logger.info(f"[ai-listen] Groq answered {crate!r} for {title!r}, which is not one of your crates")
        return {}
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    # An Indian crate for a song Whisper heard sung in a non-Indian language, with nothing Indian in its
    # title/artist, is the model guessing from a name it does not know (seen live: an English-vocal electronic
    # track called "Indian Hip Hop"). Such an answer drops below the move bar and the song stays in the catch-all.
    if (match in INDIC_VOCAL_CRATES and heard and heard.get("has_vocals")
            and heard.get("language") not in INDIC_LANGUAGES and not script_language(title, artist)):
        conf = min(conf, 0.3)
    out = {"crate": match, "confidence": conf, "reason": str(data.get("reason", ""))[:200],
           "language": heard.get("language", "") if heard else "", "has_vocals": bool(heard and heard.get("has_vocals"))}
    logger.info(f"[ai-listen] {title} - {artist} → {match} ({conf:.0%}; {out['language'] or 'no vocals'}): {out['reason']}")
    return out
