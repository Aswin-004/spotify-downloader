"""
Legacy Track Identification Service
====================================
Identifies legacy MP3 files (spotify_id=unknown/missing) by:

  1. Read ID3 tags (title, artist, album, duration)
  2. Fallback to filename parsing  ("Artist - Song.mp3", "Song.mp3")
  3. Normalize (unicode, smart quotes, feat., remix suffixes)
  4. Artist alias normalization  (weekend → The Weeknd)
  5. Spotify search — up to 3 query strategies
  6. Confidence scoring (weighted similarity)

Confidence bands:
  >= 0.90  AUTO_ACCEPT   — enrich + tag + update index
  0.75-0.89 ACCEPT_WARN  — enrich + tag (with warning)
  0.60-0.74 NEEDS_REVIEW — log + report only, no retag
  < 0.60   UNIDENTIFIED  — log + unresolved report only

Checkpointing:
  Writes reports/legacy_identification_checkpoint.json every CHECKPOINT_EVERY
  files so a batch can resume after a crash/restart.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parent.parent
_REPORTS_DIR = _BACKEND / "reports"

# ── Confidence thresholds ──────────────────────────────────────────────────────
CONF_AUTO_ACCEPT  = 0.90
CONF_ACCEPT_WARN  = 0.75
CONF_NEEDS_REVIEW = 0.60

# ── Batch checkpointing ───────────────────────────────────────────────────────
CHECKPOINT_EVERY = 50

# ── Normalization helpers ─────────────────────────────────────────────────────
_SMART_QUOTES_MAP = str.maketrans("“”‘’‛‟„",
                                  "\"\"''''\"")

_FEAT_PARENS_RE  = re.compile(
    r"\s*[\(\[]\s*(?:ft|feat|featuring|with)\.?\s+[^\)\]]+[\)\]]",
    re.IGNORECASE,
)
_FEAT_INLINE_RE  = re.compile(
    r"\s+(?:ft|feat|featuring)\.?\s+.+$",
    re.IGNORECASE,
)
_REMIX_PARENS_RE = re.compile(
    r"\s*[\(\[]\s*(?:original\s+|extended\s+|club\s+|radio\s+)?"
    r"(?:vip|remix|edit|mix|version|remaster(?:ed)?|deluxe|"
    r"anniversary(?:\s+edition)?|live\s+(?:at\s+\S+|version)?|acoustic|"
    r"instrumental|bootleg|dub(?:\s+mix)?|flip|rework)"
    r"[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_MULTI_SPACE_RE = re.compile(r"\s{2,}")

# ── Track-number / folder-context constants ───────────────────────────────────
_TRACK_NUM_RE      = re.compile(r"^(\d{1,3})\s*[-\.]\s*")
_TRACK_NUM_ONLY_RE = re.compile(r"^\d{1,3}$")

_FOLDER_GENRE_KW = frozenset({
    "bollywood", "punjabi", "hindi", "tamil", "telugu", "marathi",
    "indian", "dnb", "trance", "house", "techno", "electronic",
    "garage", "dubstep", "jungle", "ambient", "hiphop", "edm", "grime",
})
_FOLDER_PLAYLIST_KW = frozenset({
    "playlist", "hits", "best", "top", "chart", "collection",
    "compilation", "essential", "greatest",
})
_FOLDER_SET_RIP_KW = frozenset({"b2b", "boiler", "festival", "rip"})

# Bracket noise from event rips / promo posts — stripped before other processing
_BRACKET_NOISE_RE = re.compile(
    r"\s*[\(\[]\s*(?:"
    r"free\s*(?:download|dl)|hq|320(?:\s*kbps)?|"
    r"dj\s+tool|clip|preview|"
    r"radio\s+rip|set\s+rip|premiere|exclusive|"
    r"out\s+now|buy\s*=\s*dl|buy\s+for\s+free"
    r")[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

# Module-level remix token set + helper for raw-title detection
_REMIX_TOKEN_SET = frozenset({
    "remix", "vip", "edit", "extended", "club", "radio", "mix",
    "bootleg", "dub", "flip", "rework", "instrumental", "version",
})
_WORD_RE = re.compile(r"\b([a-zA-Z]+)\b")


def _get_remix_flags(text: str) -> frozenset:
    """Extract remix-category tokens from text (bracket-agnostic)."""
    return frozenset(m.group(1).lower() for m in _WORD_RE.finditer(text)) & _REMIX_TOKEN_SET


def normalize_for_search(text: str, strip_remix: bool = True) -> str:
    """Normalize a title/artist string for fuzzy comparison and Spotify search."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    t = t.translate(_SMART_QUOTES_MAP)
    t = _BRACKET_NOISE_RE.sub("", t)
    t = _FEAT_PARENS_RE.sub("", t)
    t = _FEAT_INLINE_RE.sub("", t)
    if strip_remix:
        t = _REMIX_PARENS_RE.sub("", t)
    t = _MULTI_SPACE_RE.sub(" ", t).strip().lower()
    return t


# ── Artist alias table (lower-case key → canonical display name) ──────────────
_ARTIST_ALIASES: dict[str, str] = {
    # Pop / R&B
    "weekend":              "The Weeknd",
    "the weekend":          "The Weeknd",
    "weeknd":               "The Weeknd",
    "fred again":           "Fred again..",
    "michael jackson":      "Michael Jackson",
    "eminem":               "Eminem",
    "j. cole":              "J. Cole",
    "travis scott":         "Travis Scott",
    "lil wayne":            "Lil Wayne",
    # DnB
    "drum & bass rampage":  "Drum and Bass Rampage",
    "d&b rampage":          "Drum and Bass Rampage",
    "dnb rampage":          "Drum and Bass Rampage",
    "chase and status":     "Chase & Status",
    "andy c":               "Andy C",
    "sub focus":            "Sub Focus",
    "subfocus":             "Sub Focus",
    "high contrast":        "High Contrast",
    "shy fx":               "Shy FX",
    "shyfx":                "Shy FX",
    "nosia":                "Noisia",
    "dbridge":              "dBridge",
    "d bridge":             "dBridge",
    "bcee":                 "BCee",
    "alix perez":           "Alix Perez",
    "total science":        "Total Science",
    "dj hazard":            "DJ Hazard",
    "dj marky":             "DJ Marky",
    "lenzman":              "Lenzman",
    "skeptical":            "Skeptical",
    "goldie":               "Goldie",
    "calibre":              "Calibre",
    "logistics":            "Logistics",
    "pendulum":             "Pendulum",
    "camo and krooked":     "Camo & Krooked",
    "camo krooked":         "Camo & Krooked",
    "concord dawn":         "Concord Dawn",
    # Electronic / House / Techno
    "four tet":             "Four Tet",
    "fourtet":              "Four Tet",
    "burial":               "Burial",
    "bicep":                "Bicep",
    "jon hopkin":           "Jon Hopkins",
    "floating points":      "Floating Points",
    "bonobo":               "Bonobo",
    "caribou":              "Caribou",
    "disclosure":           "Disclosure",
    "moderat":              "Moderat",
    "apparat":              "Apparat",
    "richie hawtin":        "Richie Hawtin",
    "plastikman":           "Plastikman",
    "adam beyer":           "Adam Beyer",
    "solomun":              "Solomun",
    "peggy gou":            "Peggy Gou",
}


def resolve_artist_alias(name: str) -> str:
    """Map an alias to the canonical display name; returns name unchanged if unknown."""
    return _ARTIST_ALIASES.get(name.lower().strip(), name)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class LegacyIdentificationResult:
    filepath: str
    matched: bool
    spotify_id: str
    artist: str
    title: str
    confidence: float
    confidence_reason: str
    match_source: str          # "spotify_search" | "unidentified" | "error"
    duration_delta_sec: float
    normalized_artist: str
    normalized_title: str
    reroute_recommended: bool
    target_genre_family: str
    target_subgenre: str


# ── ID3 / filename readers ────────────────────────────────────────────────────

def _read_id3_metadata(filepath: Path) -> tuple[str, str, str, int]:
    """Return (title, artist, album, duration_ms) from ID3 tags."""
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            audio = ID3(str(filepath))
        except ID3NoHeaderError:
            audio = {}

        def _tag(key: str) -> str:
            frame = audio.get(key)
            if frame and hasattr(frame, "text") and frame.text:
                return str(frame.text[0]).strip()
            return ""

        title  = _tag("TIT2")
        artist = _tag("TPE1")
        album  = _tag("TALB")

        try:
            mp3 = MP3(str(filepath))
            duration_ms = int(mp3.info.length * 1000)
        except Exception:
            duration_ms = 0

        return title, artist, album, duration_ms
    except Exception as e:
        logger.debug(f"[legacy_id] ID3 read failed for {filepath.name}: {e}")
        return "", "", "", 0


def _parse_filename(filepath: Path) -> tuple[str, str]:
    """
    Parse artist / title from filename.

    Patterns:
      "Artist - Song (Remix).mp3"  → ("Artist", "Song (Remix)")
      "Song.mp3"                   → (parent_folder_name, "Song")
    """
    stem = filepath.stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return filepath.parent.name, stem


# ── Spotify search ────────────────────────────────────────────────────────────

def _search_spotify(title: str, artist: str) -> list[dict]:
    """
    Search Spotify for tracks matching title+artist.
    Tries 3 query strategies in order, returns first non-empty result set.
    """
    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service()
    except Exception as e:
        logger.warning(f"[legacy_id] Spotify unavailable: {e}")
        return []

    queries = []
    if title and artist:
        queries.append(f'track:"{title}" artist:"{artist}"')
        queries.append(f'"{title}" "{artist}"')
    if title:
        queries.append(f'"{title}"')

    for query in queries:
        try:
            raw = sp._call_with_backoff(sp.sp.search, q=query, type="track", limit=5)
            tracks = (raw or {}).get("tracks", {}).get("items", [])
            if tracks:
                return [_extract_candidate(t) for t in tracks if t]
        except Exception as e:
            logger.debug(f"[legacy_id] Search '{query}' failed: {e}")

    return []


def _extract_candidate(track: dict) -> dict:
    artists = track.get("artists", [])
    images  = (track.get("album") or {}).get("images", [])
    return {
        "id":          track.get("id", ""),
        "title":       track.get("name", ""),
        "artist":      artists[0]["name"] if artists else "",
        "album":       (track.get("album") or {}).get("name", ""),
        "duration_ms": track.get("duration_ms", 0),
        "art_url":     images[0]["url"] if images else "",
        "popularity":  track.get("popularity", 0),
    }


# ── Candidate scoring ─────────────────────────────────────────────────────────

def _score_candidate(
    candidate: dict,
    query_title: str,
    query_artist: str,
    duration_ms: int,
    query_album: str = "",
    raw_query_title: str = "",
) -> tuple[float, str, float]:
    """
    Score a Spotify candidate against the query.

    Weights:
      title_sim    0.40
      artist_sim   0.30
      duration     0.20
      remix_match  0.05
      album_sim    0.05

    Returns (confidence, reason_str, delta_sec).
    """
    cand_title      = normalize_for_search(candidate.get("title", ""))
    cand_title_raw  = normalize_for_search(candidate.get("title", ""), strip_remix=False)
    cand_artist     = normalize_for_search(candidate.get("artist", ""), strip_remix=False)
    cand_album      = normalize_for_search(candidate.get("album",  ""), strip_remix=False)

    title_sim  = SequenceMatcher(None, query_title,  cand_title).ratio()
    artist_sim = SequenceMatcher(None, query_artist, cand_artist).ratio()

    # Duration score — wider window for live/extended tracks
    _LIVE_TOKENS = frozenset({"live", "extended", "session"})
    raw_ref = raw_query_title or query_title
    is_live = bool(_get_remix_flags(raw_ref) & _LIVE_TOKENS)
    cand_dur = candidate.get("duration_ms", 0)
    if duration_ms and cand_dur:
        delta_ms = abs(duration_ms - cand_dur)
        delta_s  = delta_ms / 1000.0
        if   delta_s <= 2:                          dur_score = 1.00
        elif delta_s <= 5:                          dur_score = 0.80
        elif delta_s <= 10:                         dur_score = 0.50
        elif delta_s <= 20:                         dur_score = 0.20
        elif is_live and delta_s <= 45:             dur_score = 0.15
        elif is_live and delta_s <= 90:             dur_score = 0.05
        else:                                       dur_score = 0.00
    else:
        dur_score = 0.50
        delta_s   = 0.0

    # Remix token overlap — use raw titles so tokens inside brackets are visible
    q_remix = _get_remix_flags(raw_ref)
    c_remix = _get_remix_flags(cand_title_raw)
    if q_remix and c_remix:
        remix_score = len(q_remix & c_remix) / max(len(q_remix | c_remix), 1)
    elif not q_remix and not c_remix:
        remix_score = 1.0
    else:
        remix_score = 0.0

    # Album similarity
    if query_album and cand_album:
        album_score = SequenceMatcher(None, query_album, cand_album).ratio()
    elif cand_album:
        album_score = 0.5
    else:
        album_score = 0.0

    confidence = (
        title_sim   * 0.40 +
        artist_sim  * 0.30 +
        dur_score   * 0.20 +
        remix_score * 0.05 +
        album_score * 0.05
    )

    reason = (
        f"title={title_sim:.2f} artist={artist_sim:.2f} "
        f"dur={dur_score:.2f}(Δ{delta_s:.1f}s) remix={remix_score:.2f}"
    )
    return confidence, reason, delta_s


def _pick_best(
    candidates: list[dict],
    query_title: str,
    query_artist: str,
    duration_ms: int,
    query_album: str = "",
    raw_query_title: str = "",
) -> Optional[tuple[str, dict, float, str, float]]:
    """Return (spotify_id, candidate, confidence, reason, delta_sec) or None."""
    best: Optional[tuple] = None
    for cand in candidates:
        if not cand.get("id"):
            continue
        conf, reason, delta_s = _score_candidate(
            cand, query_title, query_artist, duration_ms, query_album,
            raw_query_title=raw_query_title,
        )
        if best is None or conf > best[2]:
            best = (cand["id"], cand, conf, reason, delta_s)

    if best is None or best[2] < CONF_NEEDS_REVIEW:
        return None
    return best


# ── Genre resolution ──────────────────────────────────────────────────────────

def _resolve_genre_for_artist(artist_name: str) -> tuple[str, str]:
    """Return (family, subpath) by fetching artist genres from Spotify."""
    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service()
        genres = sp.get_artist_genres(artist_name)
        if not genres:
            return "", ""
        from services.genre_router import normalize_genre, GENRE_TAXONOMY
        canonical = normalize_genre(genres[0])
        if canonical and canonical in GENRE_TAXONOMY:
            return GENRE_TAXONOMY[canonical]
    except Exception:
        pass
    return "", ""


# ── Track-number stripping ────────────────────────────────────────────────────

def _strip_track_number(stem: str) -> tuple[str, "int | None"]:
    """Strip leading track-number prefix from a filename stem.

    Handles '01 - Title', '02.Title', '17 - Back 2 Back'.
    Returns (clean_stem, track_number) or (stem, None) if no prefix found.
    Empty remainder is never returned (avoids stripping single-digit titles).
    """
    m = _TRACK_NUM_RE.match(stem)
    if m:
        clean = stem[m.end():].strip()
        if clean:
            return clean, int(m.group(1))
    return stem, None


def _looks_like_track_number(text: str) -> bool:
    return bool(_TRACK_NUM_ONLY_RE.match(text.strip()))


# ── Folder context inference ──────────────────────────────────────────────────

_WORD_SPLIT_RE = re.compile(r"\b([a-z]+)\b")


def infer_parent_folder_context(folder: Path) -> dict:
    """
    Classify a parent folder to provide semantic hints for track identification.

    folder_type values:
      "album"        — 'Artist - Album' pattern, or numbered siblings + named grandparent
      "artist"       — consistent 'Artist - ' prefix across siblings
      "playlist"     — playlist/compilation keywords
      "genre_bucket" — known genre name
      "set_rip"      — DJ set rip keywords (b2b, boiler, festival, rip)
      "unknown"      — default fallback

    Returns dict with:
      folder_type, folder_name, artist_hint, album_hint,
      has_track_numbers, confidence
    """
    ctx: dict = {
        "folder_type":      "unknown",
        "folder_name":      folder.name,
        "artist_hint":      "",
        "album_hint":       "",
        "has_track_numbers": False,
        "confidence":       0.0,
    }

    if not folder.is_dir():
        return ctx

    name       = folder.name
    name_lower = name.lower()
    name_words = frozenset(_WORD_SPLIT_RE.findall(name_lower))

    # Collect sibling MP3s
    try:
        siblings = [f for f in folder.iterdir() if f.suffix.lower() == ".mp3"]
    except Exception:
        siblings = []

    numbered = sum(1 for f in siblings if _TRACK_NUM_RE.match(f.stem))
    if siblings:
        ctx["has_track_numbers"] = (numbered / len(siblings)) >= 0.5

    # ── 1. 'Artist - Album' folder name ──────────────────────────────────────
    if " - " in name:
        parts = name.split(" - ", 1)
        ctx.update({
            "folder_type": "album",
            "artist_hint": parts[0].strip(),
            "album_hint":  parts[1].strip(),
            "confidence":  0.85 if ctx["has_track_numbers"] else 0.65,
        })
        return ctx

    # ── 2. Genre bucket ───────────────────────────────────────────────────────
    if name_words & _FOLDER_GENRE_KW:
        ctx.update({"folder_type": "genre_bucket", "confidence": 0.85})
        return ctx

    # ── 3. Set rip ────────────────────────────────────────────────────────────
    if name_words & _FOLDER_SET_RIP_KW:
        ctx.update({"folder_type": "set_rip", "confidence": 0.70})
        return ctx

    # ── 4. Playlist ───────────────────────────────────────────────────────────
    if name_words & _FOLDER_PLAYLIST_KW:
        ctx.update({"folder_type": "playlist", "confidence": 0.75})
        return ctx

    # ── 5. Numbered tracks → album; use grandparent as artist hint ────────────
    if ctx["has_track_numbers"] and siblings:
        grandparent_name = folder.parent.name if folder.parent else ""
        ctx.update({
            "folder_type": "album",
            "album_hint":  name,
            "artist_hint": grandparent_name,
            "confidence":  0.70,
        })
        return ctx

    # ── 6. Consistent 'Artist - ' prefix across siblings → artist folder ──────
    if siblings:
        artist_prefix_counts: dict[str, int] = {}
        for sib in siblings[:15]:
            if " - " in sib.stem:
                prefix = sib.stem.split(" - ", 1)[0].strip().lower()
                artist_prefix_counts[prefix] = artist_prefix_counts.get(prefix, 0) + 1
        if artist_prefix_counts:
            top_prefix, top_count = max(artist_prefix_counts.items(), key=lambda x: x[1])
            if top_count >= max(2, len(siblings) // 2):
                ctx.update({
                    "folder_type": "artist",
                    "artist_hint": siblings[0].stem.split(" - ", 1)[0].strip()
                                   if " - " in siblings[0].stem else name,
                    "confidence":  0.75,
                })
                return ctx

    # ── 7. Fallback: treat any non-empty folder name as loose artist hint ────────
    if name:
        ctx.update({
            "folder_type": "artist",
            "artist_hint": name,
            "confidence":  0.40,
        })
    return ctx


def _apply_context_boost(
    confidence: float,
    reason: str,
    context: dict,
    candidate: dict,
    _norm_artist: str,
) -> tuple[float, str]:
    """
    Apply safe post-score confidence boosts derived from folder context.

    Rules (all capped so total boost ≤ 0.05, result ≤ 0.99):
    - album folder + album-name match (sim ≥ 0.80) → +0.03
    - album folder + artist-name match (sim ≥ 0.80) → +0.02 (additive with album)
    - artist folder + artist-name match (sim ≥ 0.75) → +0.04
    - genre_bucket / playlist / set_rip / unknown    → no boost
    - Already ≥ 0.95                                 → no boost
    """
    if confidence >= 0.95:
        return confidence, reason

    folder_type = context.get("folder_type", "unknown")
    boost       = 0.0
    notes: list[str] = []

    if folder_type == "album":
        album_hint  = context.get("album_hint", "")
        artist_hint = context.get("artist_hint", "")
        cand_album  = normalize_for_search(candidate.get("album", ""))
        cand_artist = normalize_for_search(candidate.get("artist", ""))

        if album_hint and cand_album:
            sim = SequenceMatcher(None, normalize_for_search(album_hint), cand_album).ratio()
            if sim >= 0.80:
                boost += 0.03
                notes.append(f"+album_ctx({sim:.2f})")

        if artist_hint and cand_artist:
            sim = SequenceMatcher(None, normalize_for_search(artist_hint), cand_artist).ratio()
            if sim >= 0.80:
                boost += 0.02
                notes.append(f"+artist_ctx({sim:.2f})")

    elif folder_type == "artist":
        artist_hint = context.get("artist_hint", "")
        cand_artist = normalize_for_search(candidate.get("artist", ""))
        if artist_hint and cand_artist:
            sim = SequenceMatcher(None, normalize_for_search(artist_hint), cand_artist).ratio()
            if sim >= 0.75:
                boost += 0.04
                notes.append(f"+artist_ctx({sim:.2f})")

    boost = min(boost, 0.05)
    if boost > 0:
        return min(confidence + boost, 0.99), reason + " " + " ".join(notes)
    return confidence, reason


# ── Core identification ───────────────────────────────────────────────────────

def identify_file(filepath: str | Path) -> LegacyIdentificationResult:
    """
    Identify a single legacy MP3 file via the 6-step pipeline.

    This function is read-only — it never modifies tags or moves files.
    """
    filepath = Path(filepath)

    # Step 0: Folder context
    folder_ctx = infer_parent_folder_context(filepath.parent)

    # Step 1: ID3 tags
    id3_title, id3_artist, id3_album, duration_ms = _read_id3_metadata(filepath)

    # Step 2: Filename fallback
    if not id3_title:
        id3_artist_fb, id3_title = _parse_filename(filepath)
        # Strip numeric track prefix when stem had no ' - ' separator
        id3_title_stripped, _tnum = _strip_track_number(id3_title)
        if _tnum is not None:
            id3_title = id3_title_stripped
        if not id3_artist:
            # Replace a bare track-number fallback (e.g. "17") with the folder
            # context artist hint — avoids searching Spotify with "17" as artist
            if _looks_like_track_number(id3_artist_fb) and folder_ctx.get("artist_hint"):
                id3_artist = folder_ctx["artist_hint"]
            else:
                id3_artist = id3_artist_fb

    # Step 3: Normalize
    norm_title      = normalize_for_search(id3_title)
    norm_title_raw  = normalize_for_search(id3_title, strip_remix=False)
    norm_album      = normalize_for_search(id3_album, strip_remix=False)

    # Step 4: Artist alias
    canon_artist = resolve_artist_alias(id3_artist)
    norm_artist  = normalize_for_search(canon_artist, strip_remix=False)

    def _unidentified(reason: str) -> LegacyIdentificationResult:
        return LegacyIdentificationResult(
            filepath=str(filepath), matched=False, spotify_id="",
            artist=id3_artist, title=id3_title, confidence=0.0,
            confidence_reason=reason, match_source="unidentified",
            duration_delta_sec=0.0, normalized_artist=norm_artist,
            normalized_title=norm_title, reroute_recommended=False,
            target_genre_family="", target_subgenre="",
        )

    if not norm_title:
        return _unidentified("could not determine title")

    # Step 5: Spotify search
    candidates = _search_spotify(norm_title, norm_artist)
    if not candidates:
        return _unidentified("no Spotify candidates found")

    # Use album hint from folder context when no ID3 album tag is present
    effective_album = norm_album or normalize_for_search(
        folder_ctx.get("album_hint", ""), strip_remix=False
    )

    # Step 6: Score
    best = _pick_best(candidates, norm_title, norm_artist, duration_ms, effective_album,
                      raw_query_title=norm_title_raw)
    if best is None:
        return _unidentified(f"best score < {CONF_NEEDS_REVIEW}")

    spotify_id, candidate, confidence, reason, delta_s = best
    # Safe post-score boost from folder context (≤ +0.05, capped at 0.99)
    confidence, reason = _apply_context_boost(confidence, reason, folder_ctx, candidate, norm_artist)

    genre_family, target_subgenre = _resolve_genre_for_artist(candidate.get("artist", ""))

    return LegacyIdentificationResult(
        filepath=str(filepath),
        matched=True,
        spotify_id=spotify_id,
        artist=candidate.get("artist", id3_artist),
        title=candidate.get("title", id3_title),
        confidence=confidence,
        confidence_reason=reason,
        match_source="spotify_search",
        duration_delta_sec=delta_s,
        normalized_artist=norm_artist,
        normalized_title=norm_title,
        reroute_recommended=confidence >= CONF_ACCEPT_WARN,
        target_genre_family=genre_family,
        target_subgenre=target_subgenre,
    )


# ── Batch processing with checkpointing ──────────────────────────────────────

def _save_checkpoint(results: list[LegacyIdentificationResult], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"results": [asdict(r) for r in results]}, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[legacy_id] Checkpoint save failed: {e}")


def identify_batch(
    filepaths: list[str | Path],
    checkpoint_path: Path | None = None,
    dry_run: bool = False,
) -> list[LegacyIdentificationResult]:
    """
    Identify a list of files with optional checkpoint resume.

    dry_run: identification is always read-only, so this flag only
             suppresses the "would enrich" log messages for callers.
    """
    results: list[LegacyIdentificationResult] = []
    processed: set[str] = set()

    if checkpoint_path and checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            for r in data.get("results", []):
                results.append(LegacyIdentificationResult(**r))
                processed.add(r["filepath"])
            logger.info(f"[legacy_id] Resumed checkpoint: {len(processed)} already done")
        except Exception as e:
            logger.warning(f"[legacy_id] Checkpoint corrupt, starting fresh: {e}")

    todo  = [Path(fp) for fp in filepaths if str(fp) not in processed]
    total = len(todo)
    logger.info(f"[legacy_id] Identifying {total} file(s)")

    for i, fp in enumerate(todo, 1):
        try:
            result = identify_file(fp)
            results.append(result)

            band = (
                "AUTO_ACCEPT"  if result.confidence >= CONF_AUTO_ACCEPT  else
                "ACCEPT_WARN"  if result.confidence >= CONF_ACCEPT_WARN  else
                "NEEDS_REVIEW" if result.confidence >= CONF_NEEDS_REVIEW else
                "UNIDENTIFIED"
            )
            logger.info(
                f"  [{i}/{total}] {fp.name} → {band} "
                f"conf={result.confidence:.2f} "
                f"'{result.title}' / '{result.artist}'"
            )
        except Exception as e:
            logger.error(f"  [{i}/{total}] ERROR {fp.name}: {e}")
            results.append(LegacyIdentificationResult(
                filepath=str(fp), matched=False, spotify_id="",
                artist="", title="", confidence=0.0,
                confidence_reason=f"Exception: {e}", match_source="error",
                duration_delta_sec=0.0, normalized_artist="", normalized_title="",
                reroute_recommended=False, target_genre_family="", target_subgenre="",
            ))

        if checkpoint_path and (i % CHECKPOINT_EVERY == 0 or i == total):
            _save_checkpoint(results, checkpoint_path)

    return results


# ── Report writers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _count_by_genre(results: list[LegacyIdentificationResult]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        key = r.target_subgenre or r.target_genre_family or "Unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def write_identification_report(
    results: list[LegacyIdentificationResult],
    reports_dir: Path | None = None,
) -> None:
    """Write legacy_identification_report.json, unresolved_tracks.json, enrichment_summary.json."""
    if reports_dir is None:
        reports_dir = _REPORTS_DIR

    auto_accepted  = [r for r in results if r.matched and r.confidence >= CONF_AUTO_ACCEPT]
    warn_accepted  = [r for r in results if r.matched and CONF_ACCEPT_WARN  <= r.confidence < CONF_AUTO_ACCEPT]
    needs_review   = [r for r in results if r.matched and CONF_NEEDS_REVIEW <= r.confidence < CONF_ACCEPT_WARN]
    unresolved     = [r for r in results if not r.matched or r.confidence < CONF_NEEDS_REVIEW]
    enriched       = auto_accepted + warn_accepted
    total          = len(results)

    _write_json(reports_dir / "legacy_identification_report.json", {
        "generated_at": _now_iso(),
        "summary": {
            "total_processed":  total,
            "auto_accepted":    len(auto_accepted),
            "accept_with_warn": len(warn_accepted),
            "needs_review":     len(needs_review),
            "unresolved":       len(unresolved),
            "enriched":         len(enriched),
            "id_rate_pct":      round(len(enriched) / max(total, 1) * 100, 1),
        },
        "auto_accepted":    [asdict(r) for r in auto_accepted],
        "accept_with_warn": [asdict(r) for r in warn_accepted],
        "needs_review":     [asdict(r) for r in needs_review],
    })

    _write_json(reports_dir / "unresolved_tracks.json", {
        "generated_at": _now_iso(),
        "count":  len(unresolved),
        "tracks": [asdict(r) for r in unresolved],
    })

    _write_json(reports_dir / "enrichment_summary.json", {
        "generated_at":       _now_iso(),
        "enriched":           len(enriched),
        "reroute_candidates": sum(1 for r in enriched if r.reroute_recommended),
        "by_genre":           _count_by_genre(enriched),
        "tracks":             [asdict(r) for r in enriched],
    })

    logger.info(
        f"[legacy_id] Reports → {reports_dir} | "
        f"{len(enriched)}/{total} identified ({round(len(enriched)/max(total,1)*100,1)}%)"
    )


# ── Phase 4: Canonical artist folder rename analysis ─────────────────────────

def analyze_canonical_renames(
    base_dir: Path,
    reports_dir: Path | None = None,
    dry_run: bool = True,
    apply_renames: bool = False,
) -> dict:
    """
    Find artist folders whose name is an alias of a canonical name.
    Writes canonical_artist_renames.json.

    apply_renames=True: safely rename + update library_index paths.
    NEVER produces Library/Library/ nesting.
    """
    if reports_dir is None:
        reports_dir = _REPORTS_DIR

    library_dir = base_dir / "Library"
    renames: list[dict] = []

    if library_dir.is_dir():
        for folder in library_dir.rglob("*"):
            if not folder.is_dir():
                continue
            rel = folder.relative_to(library_dir)
            if len(rel.parts) < 2:
                continue
            current = folder.name
            canonical = resolve_artist_alias(current)
            if canonical == current:
                continue
            renames.append({
                "folder":         str(folder),
                "current_name":   current,
                "canonical_name": canonical,
                "parent":         str(folder.parent),
                "applied":        False,
            })

    if apply_renames and not dry_run:
        _apply_canonical_renames(renames, base_dir)

    report = {
        "generated_at":     _now_iso(),
        "dry_run":          dry_run,
        "rename_candidates": len(renames),
        "renames":          renames,
    }
    _write_json(reports_dir / "canonical_artist_renames.json", report)
    logger.info(f"[rename] {len(renames)} rename candidate(s) found")
    return report


def _apply_canonical_renames(renames: list[dict], base_dir: Path) -> None:
    """Safely rename artist folders; update library_index paths."""
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
    except Exception:
        col = None

    for rename in renames:
        src = Path(rename["folder"])
        dst = src.parent / rename["canonical_name"]

        if dst == src or dst.exists():
            continue

        # Guard: never produce Library/Library/
        try:
            rel_parent = dst.parent.relative_to(base_dir)
            if "Library" in str(rel_parent).split("\\")[0].split("/")[0]:
                pass  # parent is under Library/ — that is expected
        except ValueError:
            pass

        if "Library" in rename["canonical_name"]:
            logger.warning(f"[rename] Skipping — canonical name contains 'Library': {rename}")
            continue

        try:
            src.rename(dst)
            rename["applied"] = True
            logger.info(f"[rename] {src.name} → {dst.name}")

            if col is not None:
                old_str = str(src)
                new_str = str(dst)
                for doc in col.find({"final_path": {"$regex": re.escape(old_str)}}):
                    new_path = doc["final_path"].replace(old_str, new_str)
                    col.update_one({"_id": doc["_id"]}, {"$set": {"final_path": new_path}})
        except Exception as e:
            logger.error(f"[rename] Failed {src.name}: {e}")


# ── Phase 5: Trance bucket analysis ──────────────────────────────────────────

def analyze_trance_bucket(
    trance_dir: Path,
    reports_dir: Path | None = None,
    min_confidence: float = 0.85,
) -> dict:
    """
    Analyse the Trance folder for misrouted tracks.

    For each MP3:
      - Read TXXX:SPOTIFY_ID + TPE1 from ID3
      - Fetch artist genres from Spotify
      - Compare detected genre vs Trance
      - Flag safe_to_move=True when confidence >= min_confidence AND genre != Trance

    Writes reports/trance_bucket_analysis.json.
    NO automatic moves — report only.
    """
    if reports_dir is None:
        reports_dir = _REPORTS_DIR

    if not trance_dir.is_dir():
        logger.warning(f"[trance] Directory not found: {trance_dir}")
        return {"error": str(trance_dir)}

    files = list(trance_dir.rglob("*.mp3"))
    logger.info(f"[trance] Analysing {len(files)} files in {trance_dir}")

    tracks = [_analyze_trance_file(fp, min_confidence) for fp in files]
    safe   = [t for t in tracks if t.get("safe_to_move")]

    report = {
        "generated_at":          _now_iso(),
        "trance_dir":            str(trance_dir),
        "total_files":           len(files),
        "safe_to_move_count":    len(safe),
        "min_confidence":        min_confidence,
        "tracks":                tracks,
    }
    _write_json(reports_dir / "trance_bucket_analysis.json", report)
    logger.info(f"[trance] {len(safe)}/{len(files)} safe to move (conf >= {min_confidence})")
    return report


def _analyze_trance_file(fp: Path, min_confidence: float) -> dict:
    entry: dict = {
        "filepath":              str(fp),
        "filename":              fp.name,
        "spotify_id":            "",
        "artist":                "",
        "detected_genre":        "",
        "detected_confidence":   0.0,
        "reroute_recommendation": "",
        "safe_to_move":          False,
        "reason":                "",
    }

    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            audio = ID3(str(fp))
        except ID3NoHeaderError:
            audio = {}

        def _tag(key: str) -> str:
            f = audio.get(key)
            if f and hasattr(f, "text") and f.text:
                return str(f.text[0]).strip()
            return ""

        txxx = audio.get("TXXX:SPOTIFY_ID")
        if txxx and txxx.text:
            entry["spotify_id"] = str(txxx.text[0]).strip()
        entry["artist"] = _tag("TPE1")
    except Exception as e:
        entry["reason"] = f"ID3 read error: {e}"
        return entry

    artist = entry["artist"]
    if not artist:
        entry["reason"] = "no artist tag — run legacy identification first"
        return entry

    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service()
        genres = sp.get_artist_genres(artist)

        if not genres:
            entry["reason"] = "no genre data from Spotify"
            return entry

        from services.genre_router import normalize_genre, GENRE_TAXONOMY
        canonical = normalize_genre(genres[0])

        if canonical == "Trance":
            entry.update({
                "detected_genre": "Trance",
                "detected_confidence": 0.9,
                "reason": "correctly classified as Trance",
            })
            return entry

        if canonical and canonical in GENRE_TAXONOMY:
            family, subpath = GENRE_TAXONOMY[canonical]
            confidence = 0.9
            reroute    = f"Library/{subpath}/{artist}/"
            entry.update({
                "detected_genre":        canonical,
                "detected_confidence":   confidence,
                "reroute_recommendation": reroute,
                "safe_to_move":          confidence >= min_confidence,
                "reason":                f"detected as {canonical} via Spotify genre",
            })
        else:
            entry["reason"] = f"genre '{genres[0]}' not in taxonomy"
    except Exception as e:
        entry["reason"] = f"Spotify error: {e}"

    return entry


# ── Folder context report ─────────────────────────────────────────────────────

def analyze_folder_context(
    base_dir: Path,
    reports_dir: Path | None = None,
) -> dict:
    """
    Scan base_dir recursively for MP3 parent folders, classify each one via
    infer_parent_folder_context(), and write reports/folder_context_analysis.json.

    Covers Library/, NeedsReview/, and any other sub-tree under base_dir.
    Read-only — no files are moved or retagged.
    """
    if reports_dir is None:
        reports_dir = _REPORTS_DIR

    album_folders:   list[dict] = []
    artist_folders:  list[dict] = []
    genre_folders:   list[dict] = []
    set_rip_folders: list[dict] = []
    playlist_folders: list[dict] = []
    ambiguous_folders: list[dict] = []

    seen_folders: set[Path] = set()

    for mp3 in base_dir.rglob("*.mp3"):
        parent = mp3.parent
        if parent in seen_folders:
            continue
        seen_folders.add(parent)

        ctx  = infer_parent_folder_context(parent)
        try:
            tracks = [f.name for f in parent.iterdir() if f.suffix.lower() == ".mp3"]
        except Exception:
            tracks = []

        entry = {
            "folder":           str(parent.relative_to(base_dir)),
            "folder_type":      ctx["folder_type"],
            "artist_hint":      ctx["artist_hint"],
            "album_hint":       ctx["album_hint"],
            "has_track_numbers": ctx["has_track_numbers"],
            "confidence":       ctx["confidence"],
            "track_count":      len(tracks),
            "tracks":           sorted(tracks),
        }

        ftype = ctx["folder_type"]
        if ftype == "album":
            album_folders.append(entry)
        elif ftype == "artist":
            artist_folders.append(entry)
        elif ftype == "genre_bucket":
            genre_folders.append(entry)
        elif ftype == "set_rip":
            set_rip_folders.append(entry)
        elif ftype == "playlist":
            playlist_folders.append(entry)
        else:
            ambiguous_folders.append(entry)

    total_folders = len(seen_folders)
    total_tracks  = sum(
        e["track_count"]
        for e in album_folders + artist_folders + genre_folders
        + set_rip_folders + playlist_folders + ambiguous_folders
    )

    report = {
        "generated_at":    _now_iso(),
        "base_dir":        str(base_dir),
        "summary": {
            "total_folders":    total_folders,
            "total_tracks":     total_tracks,
            "album_folders":    len(album_folders),
            "artist_folders":   len(artist_folders),
            "genre_folders":    len(genre_folders),
            "set_rip_folders":  len(set_rip_folders),
            "playlist_folders": len(playlist_folders),
            "ambiguous_folders": len(ambiguous_folders),
        },
        "album_folders":    sorted(album_folders,   key=lambda x: x["folder"]),
        "artist_folders":   sorted(artist_folders,  key=lambda x: x["folder"]),
        "genre_folders":    sorted(genre_folders,   key=lambda x: x["folder"]),
        "set_rip_folders":  sorted(set_rip_folders, key=lambda x: x["folder"]),
        "playlist_folders": sorted(playlist_folders,key=lambda x: x["folder"]),
        "ambiguous_folders": sorted(ambiguous_folders, key=lambda x: x["folder"]),
    }

    _write_json(reports_dir / "folder_context_analysis.json", report)
    logger.info(
        f"[folder_ctx] {total_folders} folders classified — "
        f"album={len(album_folders)} artist={len(artist_folders)} "
        f"genre={len(genre_folders)} ambiguous={len(ambiguous_folders)}"
    )
    return report
