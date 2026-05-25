"""
Genre Router
============
Resolves the destination folder for a downloaded track using
Spotify artist genre tags. Single source of truth for all
folder routing decisions.

Target structure (flat — genre-only, no artist subfolder):
  Library/House/{song}.mp3
  Library/UK Garage/{song}.mp3
  Library/Bollywood/{song}.mp3
  Library/Hip Hop/{song}.mp3
  NeedsReview/{artist}/       ← unknown genre or low confidence (keeps artist folder)

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

import unicodedata
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
    # ── Electronic — each sub-genre gets its own DJ crate ─────────────────
    "Electronic":    ("Electronic", "Electronic"),
    "Trance":        ("Electronic", "Trance"),
    "Psytrance":     ("Electronic", "Trance"),
    "House":         ("Electronic", "House"),
    "Afro House":    ("Electronic", "House"),
    "Deep House":    ("Electronic", "House"),
    "UK Garage":     ("Electronic", "UK Garage"),
    "Speed Garage":  ("Electronic", "UK Garage"),
    "UK Bass":       ("Electronic", "UK Garage"),
    "Grime":         ("Electronic", "Grime"),
    "UK Drill":      ("Electronic", "Grime"),
    "Drum and Bass": ("Electronic", "Drum & Bass"),
    "Jungle":        ("Electronic", "Drum & Bass"),
    "Dubstep":       ("Electronic", "Dubstep"),
    "Brostep":       ("Electronic", "Dubstep"),
    "Techno":        ("Electronic", "Techno"),
    "Industrial":    ("Electronic", "Techno"),
    "Bass":          ("Electronic", "Electronic"),   # generic → catch-all
    "Dance":         ("Electronic", "Electronic"),
    "Ambient":       ("Electronic", "Electronic"),
    "Lo-Fi":         ("Electronic", "Electronic"),
    # ── Indian (flat) ─────────────────────────────────────────────────────
    "Bollywood":     ("Indian",     "Bollywood"),
    "Punjabi":       ("Indian",     "Punjabi"),
    "Indian":        ("Indian",     "Punjabi"),      # ambiguous → Punjabi crate
    "Tamil":         ("Indian",     "Tamil"),
    "Telugu":        ("Indian",     "Bollywood"),    # → Bollywood crate
    "Indian Hip Hop":("Indian",     "Punjabi"),
    "Desi Hip Hop":  ("Indian",     "Punjabi"),
    # ── Global (flat) ─────────────────────────────────────────────────────
    "Hip Hop":       ("Global",     "Hip Hop"),
    "R&B":           ("Global",     "R&B"),
    "Soul":          ("Global",     "R&B"),
    "Pop":           ("Global",     "Pop"),
    "K-Pop":         ("Global",     "Pop"),
    "J-Pop":         ("Global",     "Pop"),
    "Asian Pop":     ("Global",     "Pop"),
    "Latin":         ("Global",     "Latin"),
    "Afrobeats":     ("Global",     "Latin"),
    "Reggae":        ("Global",     "Latin"),
    "Reggaeton":     ("Global",     "Latin"),
    "Rock":          ("Global",     "Electronic"),   # → Electronic for DJ
    "Metal":         ("Global",     "Electronic"),
    "Folk":          ("Global",     "Bollywood"),    # → Bollywood (Nimbooda etc)
    "Country":       ("Global",     "Pop"),
    "Jazz":          ("Global",     "R&B"),
    "Blues":         ("Global",     "R&B"),
    "Classical":     ("Global",     "Pop"),
}


# ── Phase 1 public API ────────────────────────────────────────────────────────

def normalize_artist_key(name: str) -> str:
    """
    Canonical artist name → lookup key.

    NFKD decompose → strip combining diacritics → lowercase → collapse whitespace.
    Identical to artist_knowledge_service._normalize and produces the same key
    regardless of which call site resolves an artist name.

    Use this for every ARTIST_GENRE_OVERRIDE and cache-key lookup.
    Do NOT use for filesystem folder names (use clean_folder_name for those).
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c) and unicodedata.category(c) != "Cc")
    return " ".join(text.lower().split())


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
    for key in _get_sorted_map_keys():
        if key in raw_lower:
            mapped = config.SPOTIFY_GENRE_MAP[key]
            if mapped in GENRE_TAXONOMY:
                return mapped
    # 3. Partial match within taxonomy keys
    for canonical in GENRE_TAXONOMY:
        if canonical.lower() in raw_lower or raw_lower in canonical.lower():
            return canonical
    logger.warning(f"[genre_router] Unknown genre — not in taxonomy or SPOTIFY_GENRE_MAP: {raw!r} → add to config.py")
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

      "UK Garage"  → "Library/UK Garage"
      "Bollywood"  → "Library/Bollywood"
      "Hip Hop"    → "Library/Hip Hop"
      "(unknown)"  → "Library/Electronic"  (catch-all)

    Never returns Uncategorized — unknown genres fall back to Electronic.
    """
    if not genre_folder:
        return f"{LIBRARY_ROOT}/Electronic"
    entry = GENRE_TAXONOMY.get(genre_folder)
    if entry:
        return f"{LIBRARY_ROOT}/{entry[1]}"
    # Fallback: search by subpath suffix (handles partial matches)
    gf_lower = genre_folder.lower()
    for key, (family, subpath) in GENRE_TAXONOMY.items():
        if subpath.lower() == gf_lower or key.lower() == gf_lower:
            return f"{LIBRARY_ROOT}/{subpath}"
    logger.warning(f"[genre_router] No library folder for {genre_folder!r} — falling back to Electronic catch-all")
    return f"{LIBRARY_ROOT}/Electronic"


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

# Sorted SPOTIFY_GENRE_MAP keys — computed once, invalidated by clear_genre_cache().
# All callers use this so longest-key-wins is identical on every code path.
_sorted_map_keys: list  = []


def _get_sorted_map_keys() -> list:
    """Return SPOTIFY_GENRE_MAP keys sorted longest-first, cached for the process lifetime."""
    global _sorted_map_keys
    if not _sorted_map_keys:
        _sorted_map_keys = sorted(config.SPOTIFY_GENRE_MAP.keys(), key=len, reverse=True)
    return _sorted_map_keys


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
    for key in _get_sorted_map_keys():
        if key in genre_lower:
            flat = config.SPOTIFY_GENRE_MAP[key]
            return _library_path(flat)
    # Raw fallback → OpenFormat
    cleaned = clean_folder_name(genre_str.title())
    lib = _library_path(cleaned)
    return lib or f"{LIBRARY_ROOT}/Electronic"


def _matches_devanagari(text: str) -> bool:
    if not text:
        return False
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def _match_genre(genres: list) -> str:
    """
    Return the first canonical genre resolved from a list of Spotify genre tags.

    Delegates to normalize_genre() per tag so taxonomy exact-match, longest-key-wins
    SPOTIFY_GENRE_MAP, and partial-taxonomy fallback are applied consistently.
    Identical inputs always produce identical outputs regardless of call path.
    Returns "" if no tag resolves to a known genre.
    """
    for genre in genres:
        canonical = normalize_genre(genre)
        if canonical:
            logger.debug(f"[genre_router] _match_genre: {genre!r} → {canonical!r}")
            return canonical
    return ""


def _get_artist_override(artist_name: str) -> str:
    key = normalize_artist_key(artist_name)
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

    # 2.5. User-defined custom folder mapping (Settings page)
    # Match folder_name against the artist name so tracks by a specific artist
    # (e.g. "Sammy Virji") route to the right folder; genre_label is the target folder.
    try:
        from database import get_custom_folder_mappings_collection
        import re as _re
        _col = get_custom_folder_mappings_collection()
        for _doc in _col.find({}):
            _folder_key = (_doc.get("folder_name") or "").strip()
            if _folder_key and _re.match(rf"^{_re.escape(_folder_key)}$", artist_name, _re.IGNORECASE):
                _genre_label = _doc.get("genre_label", _folder_key)
                _path = _library_path(_genre_label)
                if artist_id:
                    _genre_cache[artist_id]      = _path
                    _confidence_cache[artist_id] = 0.95
                    _source_cache[artist_id]     = "custom_folder"
                logger.info(f"[genre_router] {artist_name} → {_path} (custom folder mapping, conf=0.95)")
                return _path, 0.95, "custom_folder", _genre_label
    except Exception:
        pass  # custom mappings unavailable — continue

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
            for key in _get_sorted_map_keys():
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
        logger.debug(f"[genre_router] {artist_name} → Indian (Devanagari script heuristic, conf={CONFIDENCE_DEVANAGARI})")

    # 7. Raw first Spotify genre tag — moderate confidence
    if not flat_genre:
        if genres:
            raw        = genres[0].title()
            logger.debug(f"[genre_router] {artist_name} — no map match, trying raw Spotify tag: {raw!r}")
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
    global _genre_cache, _confidence_cache, _source_cache, _sorted_map_keys
    _genre_cache = {}
    _confidence_cache = {}
    _source_cache = {}
    _sorted_map_keys = []
    logger.info("[genre_router] Genre cache cleared (including sorted-keys cache)")
