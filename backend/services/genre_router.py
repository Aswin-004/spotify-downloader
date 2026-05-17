"""
Genre Router
============
Resolves the destination folder for a downloaded track using
Spotify artist genre tags. Single source of truth for all
folder routing decisions.

Target structure (flat — genre-only, no artist subfolder):
  Library/Electronic/House/{song}.mp3
  Library/Electronic/UKG/{song}.mp3
  Library/Indian/Bollywood/{song}.mp3
  Library/HipHop/{song}.mp3
  NeedsReview/{artist}/       ← unknown genre or low confidence (keeps artist folder)
  Quarantine/                 ← broken/temp/corrupt only (NOT for unknown music)

Confidence levels:
  1.0 — ARTIST_GENRE_OVERRIDE  (explicit manual mapping)
  1.0 — artist_memory_service  (learned from user moves)
  0.9 — MusicBrainz genre      (high-quality signal)
  0.7 — Spotify genre matched against SPOTIFY_GENRE_MAP
  0.4 — Raw Spotify genre tag  (cleaned, no map match)
  0.3 — Devanagari artist-name heuristic (script-based)
  0.0 — No genre data          → NeedsReview (never Uncategorized)

Phase 1 — Universal Genre Taxonomy
Phase 2 — Artist memory integration
Phase 3 — NeedsReview / Quarantine strict separation
Phase 9 — Routing explanation text for UI
"""

from typing import Any

from loguru import logger

from config import config
from services.organizer_service import clean_folder_name


# ── Target directory constants ────────────────────────────────────────────────

LIBRARY_ROOT = "Library"
NEEDS_REVIEW_DIR = "NeedsReview"
CONFIDENCE_THRESHOLD = 0.5          # below this → NeedsReview (never Uncategorized)

# ── Confidence levels (unchanged API) ─────────────────────────────────────────

CONFIDENCE_ARTIST_OVERRIDE  = 1.0
CONFIDENCE_MEMORY           = 1.0   # learned from confirmed user moves
CONFIDENCE_MB_GENRE         = 0.9
CONFIDENCE_SPOTIFY_MAP      = 0.7
CONFIDENCE_RAW_SPOTIFY      = 0.4
CONFIDENCE_DEVANAGARI       = 0.3
CONFIDENCE_UNCATEGORIZED    = 0.0


# ── Phase 1: Universal Genre Taxonomy ─────────────────────────────────────────
#
# Maps canonical genre folder name (=SPOTIFY_GENRE_MAP output value)
#   → (genre_family, library_subpath_under_Library/)
#
# Genre families:  "Electronic" | "Indian" | "Global"
# Global genres sit directly under Library/ (no intermediate family folder).

GENRE_TAXONOMY: dict[str, tuple[str, str]] = {
    # ── Electronic ────────────────────────────────────────────────────────
    "House":         ("Electronic", "Electronic/House"),
    "Afro House":    ("Electronic", "Electronic/Afro House"),
    "UK Garage":     ("Electronic", "Electronic/UKG"),
    "UK Bass":       ("Electronic", "Electronic/UKG"),
    "Grime":         ("Electronic", "Electronic/UKG"),
    "Drum and Bass": ("Electronic", "Electronic/Drum and Bass"),
    "Dubstep":       ("Electronic", "Electronic/Dubstep"),
    "Bass":          ("Electronic", "Electronic/Bass"),
    "Electronic":    ("Electronic", "Electronic/Electronic"),
    "Techno":        ("Electronic", "Electronic/Techno"),
    "Trance":        ("Electronic", "Electronic/Electronic"),
    "Dance":         ("Electronic", "Electronic/Electronic"),
    "Ambient":       ("Electronic", "Electronic/Electronic"),
    "Lo-Fi":         ("Electronic", "Electronic/Electronic"),
    # ── Indian ────────────────────────────────────────────────────────────
    "Bollywood":     ("Indian",     "Indian/Bollywood"),
    "Punjabi":       ("Indian",     "Indian/Punjabi"),
    "Indian":        ("Indian",     "Indian/Hindi Pop"),
    "Tamil":         ("Indian",     "Indian/Tamil"),
    "Telugu":        ("Indian",     "Indian/Telugu"),
    "Indian Hip Hop": ("Indian",    "Indian/HipHop"),
    "Desi Hip Hop":  ("Indian",    "Indian/HipHop"),
    # ── Global (directly under Library/) ─────────────────────────────────
    "Hip Hop":       ("Global",     "HipHop"),
    "R&B":           ("Global",     "OpenFormat"),
    "Pop":           ("Global",     "Pop"),
    "K-Pop":         ("Global",     "Pop"),
    "J-Pop":         ("Global",     "Pop"),
    "Asian Pop":     ("Global",     "Pop"),
    "Rock":          ("Global",     "Rock"),
    "Metal":         ("Global",     "Rock"),
    "Jazz":          ("Global",     "OpenFormat"),
    "Blues":         ("Global",     "OpenFormat"),
    "Classical":     ("Global",     "OpenFormat"),
    "Reggae":        ("Global",     "OpenFormat"),
    "Afrobeats":     ("Global",     "OpenFormat"),
    "Latin":         ("Global",     "OpenFormat"),
    "Folk":          ("Global",     "OpenFormat"),
    "Country":       ("Global",     "OpenFormat"),
}


# ── Phase 1 public API ────────────────────────────────────────────────────────

def normalize_genre(raw: str) -> str:
    """
    Map a raw genre string from any source (Spotify, MusicBrainz, filename,
    heuristic) to a canonical genre name that is a key in GENRE_TAXONOMY.

    Falls back to the SPOTIFY_GENRE_MAP lookup, then empty string.
    """
    if not raw:
        return ""
    raw_lower = raw.lower().strip()
    # 1. Exact match in taxonomy (case-insensitive)
    for canonical in GENRE_TAXONOMY:
        if canonical.lower() == raw_lower:
            return canonical
    # 2. SPOTIFY_GENRE_MAP lookup (longest key wins)
    sorted_keys = sorted(config.SPOTIFY_GENRE_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in raw_lower:
            mapped = config.SPOTIFY_GENRE_MAP[key]
            if mapped in GENRE_TAXONOMY:
                return mapped
    # 3. Partial match within taxonomy keys
    for canonical in GENRE_TAXONOMY:
        if canonical.lower() in raw_lower or raw_lower in canonical.lower():
            return canonical
    return ""


def resolve_genre_family(genre: str) -> str:
    """Return the genre family: 'Electronic', 'Indian', or 'Global'."""
    canonical = normalize_genre(genre)
    entry = GENRE_TAXONOMY.get(canonical)
    return entry[0] if entry else "Global"


def resolve_subgenre(genre: str) -> str:
    """Return the library subpath for a canonical genre (e.g. 'Electronic/House')."""
    canonical = normalize_genre(genre)
    entry = GENRE_TAXONOMY.get(canonical)
    return entry[1] if entry else ""


def _library_path(genre_folder: str) -> str:
    """
    Convert a canonical genre folder name to a Library/-prefixed subpath.

      "UK Garage"  → "Library/Electronic/UKG"
      "Bollywood"  → "Library/Indian/Bollywood"
      "Hip Hop"    → "Library/HipHop"
      "(unknown)"  → "Library/OpenFormat"

    Never returns Uncategorized — unknown genres go to OpenFormat or NeedsReview.
    """
    if not genre_folder:
        return f"{LIBRARY_ROOT}/OpenFormat"
    entry = GENRE_TAXONOMY.get(genre_folder)
    if entry:
        return f"{LIBRARY_ROOT}/{entry[1]}"
    # Fallback: search by subpath suffix (handles partial matches)
    gf_lower = genre_folder.lower()
    for key, (family, subpath) in GENRE_TAXONOMY.items():
        if subpath.split("/")[-1].lower() == gf_lower or key.lower() == gf_lower:
            return f"{LIBRARY_ROOT}/{subpath}"
    return f"{LIBRARY_ROOT}/OpenFormat"


# ── Phase 9: Routing explanation for UI ───────────────────────────────────────

def get_routing_explanation(folder: str, confidence: float, source: str,
                            matched_tag: str = "") -> str:
    """
    Return a short human-readable sentence explaining why a track was routed
    to a given folder.  Used by the UI to show routing rationale.

    Examples:
      "Routed to Library/Electronic/UKG via Spotify genre tag 'uk garage' (conf 70%)"
      "Low-confidence genre — routed to NeedsReview for manual review"
      "Learned from a previous manual move (conf 100%)"
    """
    pct = int(confidence * 100)
    if folder.startswith(NEEDS_REVIEW_DIR):
        return (
            f"Genre confidence too low ({pct}%) — sent to NeedsReview for manual review. "
            f"Move to the correct genre folder to teach the classifier."
        )
    source_labels = {
        "artist_override":  "explicit artist override",
        "artist_memory":    "learned from a previous manual move",
        "knowledge_base":   "artist knowledge base (static profile)",
        "spotify_genre":    f"Spotify genre tag '{matched_tag}'",
        "raw_spotify":      f"raw Spotify genre '{matched_tag}'",
        "devanagari":       "Devanagari script artist-name heuristic",
        "musicbrainz":      "MusicBrainz genre lookup",
        "cache":            "cached result",
    }
    label = source_labels.get(source, source)
    return f"Routed to {folder} via {label} (conf {pct}%)."


# ── In-memory caches ──────────────────────────────────────────────────────────

_genre_cache: dict      = {}
_confidence_cache: dict = {}
_source_cache: dict     = {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def map_genre_string(genre_str: str) -> str:
    """
    Map a raw genre string to a Library/-prefixed folder path.

    Checks SPOTIFY_GENRE_MAP first (longest-key wins), then falls back to a
    cleaned raw genre string routed through GENRE_TAXONOMY.  Returns an empty
    string for empty input.

    Used by the MusicBrainz branch in auto_downloader to resolve genre paths.
    """
    if not genre_str:
        return ""
    genre_lower = genre_str.lower().strip()
    sorted_keys = sorted(config.SPOTIFY_GENRE_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in genre_lower:
            flat = config.SPOTIFY_GENRE_MAP[key]
            return _library_path(flat)
    # Raw fallback → OpenFormat
    cleaned = clean_folder_name(genre_str.title())
    lib = _library_path(cleaned)
    return lib or f"{LIBRARY_ROOT}/OpenFormat"


def _matches_devanagari(text: str) -> bool:
    if not text:
        return False
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def _match_genre(genres: list) -> str:
    """
    Return first SPOTIFY_GENRE_MAP value that matches any genre tag (flat name).
    Returns "" on no match.
    """
    sorted_keys = sorted(config.SPOTIFY_GENRE_MAP.keys(), key=len, reverse=True)
    for genre in genres:
        genre_lower = genre.lower()
        for key in sorted_keys:
            if key in genre_lower:
                return config.SPOTIFY_GENRE_MAP[key]
    return ""


def _get_artist_override(artist_name: str) -> str:
    key = artist_name.lower().strip()
    return config.ARTIST_GENRE_OVERRIDE.get(key, "")


def _resolve_core(
    artist_id: str, artist_name: str, sp: Any
) -> tuple[str, float, str, str]:
    """
    Core resolution — returns (folder_path, confidence, source, matched_tag).

    folder_path is a Library/-prefixed flat path WITHOUT artist subfolder:
      e.g. "Library/Electronic/UKG"

    NeedsReview retains artist subfolder for triage:
      e.g. "NeedsReview/Sammy Virji"

    For low confidence: returns "NeedsReview/{artist}" — NEVER Uncategorized.
    Never raises.
    """
    clean_artist = clean_folder_name(artist_name)

    # 1. Cache hit
    if artist_id and artist_id in _genre_cache:
        cached = _genre_cache[artist_id]
        conf   = _confidence_cache.get(artist_id, CONFIDENCE_SPOTIFY_MAP)
        source = _source_cache.get(artist_id, "cache")
        logger.debug(f"[genre_router] cache hit: {artist_name} → {cached} ({conf:.2f})")
        return cached, conf, source, ""

    # 2. Artist-name override (config) — highest confidence
    override_folder = _get_artist_override(artist_name)
    if override_folder:
        lib = _library_path(override_folder)
        result = lib
        if artist_id:
            _genre_cache[artist_id]      = result
            _confidence_cache[artist_id] = CONFIDENCE_ARTIST_OVERRIDE
            _source_cache[artist_id]     = "artist_override"
        logger.info(f"[genre_router] {artist_name} → {result} (artist override, conf=1.0)")
        return result, CONFIDENCE_ARTIST_OVERRIDE, "artist_override", artist_name

    # 3. Phase 2: Artist memory service — learned from confirmed user moves
    try:
        from services.artist_memory_service import lookup_artist as _mem_lookup
        mem = _mem_lookup(artist_name)
        if mem and mem.get("confidence", 0) >= CONFIDENCE_THRESHOLD:
            lib    = _library_path(mem["genre"])
            result = lib
            conf   = float(mem["confidence"])
            if artist_id:
                _genre_cache[artist_id]      = result
                _confidence_cache[artist_id] = conf
                _source_cache[artist_id]     = "artist_memory"
            logger.info(f"[genre_router] {artist_name} → {result} (artist memory, conf={conf:.2f})")
            return result, conf, "artist_memory", mem["genre"]
    except Exception:
        pass  # memory service unavailable — continue

    # 3.5 Artist Knowledge Base — static profiles with aliases + multilingual names
    try:
        from services.artist_knowledge_service import lookup_artist_knowledge
        kb_hit = lookup_artist_knowledge(artist_name)
        if kb_hit:
            lib    = _library_path(kb_hit["genre"])
            result = lib
            conf   = kb_hit["confidence"]
            if artist_id:
                _genre_cache[artist_id]      = result
                _confidence_cache[artist_id] = conf
                _source_cache[artist_id]     = "knowledge_base"
            logger.info(
                f"[genre_router] {artist_name} → {result} "
                f"(knowledge_base, conf={conf:.2f}, genre={kb_hit['genre']})"
            )
            return result, conf, "knowledge_base", kb_hit["genre"]
    except Exception:
        pass

    # No artist_id → NeedsReview immediately
    if not artist_id:
        result = f"{NEEDS_REVIEW_DIR}/{clean_artist}"
        logger.info(f"[genre_router] {artist_name} → {result} (no artist_id)")
        return result, CONFIDENCE_UNCATEGORIZED, "uncategorized", ""

    # 4. Spotify API fetch
    try:
        artist_obj = sp.artist(artist_id)
        genres     = artist_obj.get("genres", []) or []
    except Exception as e:
        result = f"{NEEDS_REVIEW_DIR}/{clean_artist}"
        logger.warning(
            f"[genre_router] sp.artist({artist_id}) failed for {artist_name}: {e} "
            f"→ {result}"
        )
        _genre_cache[artist_id]      = result
        _confidence_cache[artist_id] = CONFIDENCE_UNCATEGORIZED
        _source_cache[artist_id]     = "uncategorized"
        return result, CONFIDENCE_UNCATEGORIZED, "uncategorized", ""

    # 5. Match against SPOTIFY_GENRE_MAP
    flat_genre  = _match_genre(genres)
    matched_tag = ""
    confidence  = CONFIDENCE_UNCATEGORIZED
    source      = "uncategorized"

    if flat_genre:
        confidence  = CONFIDENCE_SPOTIFY_MAP
        source      = "spotify_genre"
        for tag in genres:
            tag_lower = tag.lower()
            for key in config.SPOTIFY_GENRE_MAP:
                if key in tag_lower:
                    matched_tag = tag
                    break
            if matched_tag:
                break

    # 6. Devanagari heuristic — low confidence, not a genre signal
    if not flat_genre and _matches_devanagari(artist_name):
        flat_genre  = "Indian"
        matched_tag = "devanagari-artist-name"
        confidence  = CONFIDENCE_DEVANAGARI
        source      = "devanagari"

    # 7. Raw first Spotify genre tag — moderate confidence
    if not flat_genre:
        if genres:
            raw        = genres[0].title()
            logger.info(f"[raw-genre] {raw}")
            flat_genre = config.SPOTIFY_GENRE_MAP.get(
                raw.lower(),
                clean_folder_name(raw),
            )
            matched_tag = f"raw-genre:{raw}"
            confidence  = CONFIDENCE_RAW_SPOTIFY
            source      = "raw_spotify"

    # 8. Phase 3: Route low-confidence to NeedsReview — NEVER Uncategorized
    if not flat_genre or confidence < CONFIDENCE_THRESHOLD:
        result = f"{NEEDS_REVIEW_DIR}/{clean_artist}"
        _genre_cache[artist_id]      = result
        _confidence_cache[artist_id] = confidence
        _source_cache[artist_id]     = source
        return result, confidence, source, matched_tag

    # 9. Build Library/ path (flat — no artist subfolder)
    lib    = _library_path(flat_genre)
    result = lib
    _genre_cache[artist_id]      = result
    _confidence_cache[artist_id] = confidence
    _source_cache[artist_id]     = source
    return result, confidence, source, matched_tag


# ── Public API (preserved signatures) ────────────────────────────────────────

def resolve_genre_folder(artist_id: str, artist_name: str, sp: Any) -> str:
    """
    Resolve the subfolder path for a track based on its Spotify artist genres.

    Returns a relative path like ``"Library/Electronic/UKG/Sammy Virji"``
    or ``"NeedsReview/Sammy Virji"`` for low-confidence decisions.

    Never raises — falls back to NeedsReview on any error.
    """
    folder, confidence, source, matched_tag = _resolve_core(artist_id, artist_name, sp)
    logger.info(
        f"[genre_router] {artist_name} → {folder} "
        f"(source={source}, conf={confidence:.2f}, matched='{matched_tag}')"
    )
    return folder


def resolve_genre_folder_with_confidence(
    artist_id: str, artist_name: str, sp: Any
) -> tuple[str, float, str]:
    """
    Like resolve_genre_folder but also returns (confidence, source).

    Returns:
        (folder_path, confidence, source)
        e.g. ("Library/Electronic/UKG/Sammy Virji", 0.7, "spotify_genre")

    Low-confidence tracks return NeedsReview/ paths — callers can check
    whether ``folder.startswith("NeedsReview")`` for UI differentiation.
    """
    folder, confidence, source, matched_tag = _resolve_core(artist_id, artist_name, sp)
    logger.info(
        f"[genre_router] {artist_name} → {folder} "
        f"(source={source}, conf={confidence:.2f}, matched='{matched_tag}')"
    )
    return folder, confidence, source


def clear_genre_cache() -> None:
    """Clear all in-memory genre caches — call after updating SPOTIFY_GENRE_MAP."""
    global _genre_cache, _confidence_cache, _source_cache
    _genre_cache = {}
    _confidence_cache = {}
    _source_cache = {}
    logger.info("[genre_router] Genre cache cleared")
