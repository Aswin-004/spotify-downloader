"""
Artist Folder Intelligence Service — Rollout Phases 1 & 2
==========================================================
Detects when a top-level folder represents an artist/DJ/event/label collection
(rather than a genre) and routes it to the canonical Library/ path.

Used by:
  migrate_library_structure.py --retry-unmapped
  reclassification_service._try_reroute() fallback
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from config import config

# ── Known artist aliases (lower-case key → canonical display name) ────────────
_ARTIST_ALIASES: dict[str, str] = {
    "weekend":                "The Weeknd",
    "the weekend":            "The Weeknd",
    "weeknd":                 "The Weeknd",
    "fred again":             "Fred again..",
    "drum & bass rampage":    "Drum and Bass Rampage",
    "d&b rampage":            "Drum and Bass Rampage",
    "dnb rampage":            "Drum and Bass Rampage",
    "chase and status":       "Chase & Status",
    "andy c":                 "Andy C",
    "michael jackson":        "Michael Jackson",
}

# ── Pattern-based folder type detection ──────────────────────────────────────
_EVENT_RE = re.compile(
    r"\b(rampage|festival|creamfields|fabric|rinse|night|live\s*at|presents|"
    r"showcase|compilation|pack|vol\.?\s*\d|volume\s*\d|warm.?up|b2b|rave)\b",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"\b(records|recordings|hospital|medic|metalheadz|ram\s*records|"
    r"ministry\s*of\s*sound)\b",
    re.IGNORECASE,
)
_COLLECTION_RE = re.compile(
    r"\b(best\s*of|greatest\s*hits|anthology|collection|essentials|"
    r"favourites|favorites|playlist)\b",
    re.IGNORECASE,
)
_DJ_RE = re.compile(r"(?:^|\s)dj\s+|(?:^|\s)mc\s+|\bselector\b", re.IGNORECASE)

# ── Genre hints from folder name keywords ─────────────────────────────────────
_FOLDER_GENRE_HINTS: list[tuple[str, str]] = [
    ("drum and bass", "Drum and Bass"),
    ("drum & bass",   "Drum and Bass"),
    ("dnb",           "Drum and Bass"),
    ("d&b",           "Drum and Bass"),
    ("rampage",       "Drum and Bass"),
    ("uk garage",     "UK Garage"),
    ("ukg",           "UK Garage"),
    ("garage",        "UK Garage"),
    ("grime",         "Grime"),
    ("dubstep",       "Dubstep"),
    ("hip hop",       "Hip Hop"),
    ("hiphop",        "Hip Hop"),
    ("house",         "House"),
    ("techno",        "Techno"),
    ("trance",        "Trance"),
    ("bass",          "Electronic"),
]


# ── Public helpers ────────────────────────────────────────────────────────────

def resolve_alias(name: str) -> str:
    """Return canonical display name for a possibly-aliased folder/artist name."""
    return _ARTIST_ALIASES.get(name.lower().strip(), name)


def _genre_from_name(folder_name: str) -> str:
    """Extract a genre hint from folder name keywords."""
    lower = folder_name.lower()
    for keyword, genre in _FOLDER_GENRE_HINTS:
        if keyword in lower:
            return genre
    return ""


def _scan_folder_tags(folder_path: Path, max_files: int = 10) -> dict:
    """
    Sample ID3 tags from up to max_files MP3s.
    Returns {"artists": Counter, "genres": Counter, "spotify_ids": set}
    """
    artists: Counter = Counter()
    genres: Counter = Counter()
    spotify_ids: set = set()
    for fp in list(folder_path.rglob("*.mp3"))[:max_files]:
        try:
            from mutagen.id3 import ID3
            audio = ID3(str(fp))
            tpe1 = audio.get("TPE1")
            if tpe1 and tpe1.text:
                artists[str(tpe1.text[0]).strip()] += 1
            tcon = audio.get("TCON")
            if tcon and tcon.text:
                genres[str(tcon.text[0]).strip()] += 1
            txxx = audio.get("TXXX:SPOTIFY_ID")
            if txxx and txxx.text:
                spotify_ids.add(str(txxx.text[0]).strip())
        except Exception:
            pass
    return {"artists": artists, "genres": genres, "spotify_ids": spotify_ids}


def _route_from_override(artist_name: str) -> str | None:
    """Check ARTIST_GENRE_OVERRIDE. Returns Library/ path or None."""
    try:
        from services.genre_router import _library_path, normalize_artist_key
        genre = config.ARTIST_GENRE_OVERRIDE.get(normalize_artist_key(artist_name), "")
        return _library_path(genre) if genre else None
    except Exception:
        return None


def _route_from_memory(artist_name: str) -> str | None:
    """Check artist_memory for a known genre. Returns Library/ path or None."""
    try:
        from services.artist_memory_service import lookup_artist
        from services.genre_router import _library_path, CONFIDENCE_THRESHOLD
        mem = lookup_artist(artist_name)
        if mem and mem.get("confidence", 0) >= CONFIDENCE_THRESHOLD:
            return _library_path(mem["genre"])
    except Exception:
        pass
    return None


def _route_by_genre(genre: str) -> str | None:
    """Return Library/ path for a genre string via map_genre_string."""
    if not genre:
        return None
    try:
        from services.genre_router import map_genre_string
        result = map_genre_string(genre)
        return result or None
    except Exception:
        return None


# ── Core detection ────────────────────────────────────────────────────────────

def detect_folder_type(folder_name: str, folder_path: Path) -> dict:
    """
    Determine if a top-level folder is artist/dj_artist/event/label/collection/unknown.

    Returns {
        type, confidence, canonical_name, suggested_route, source, file_count
    }
    """
    canonical = resolve_alias(folder_name)
    file_count = len(list(folder_path.rglob("*.mp3"))) if folder_path.is_dir() else 0

    def _result(t, conf, route, src):
        return {"type": t, "confidence": conf, "canonical_name": canonical,
                "suggested_route": route, "source": src, "file_count": file_count}

    # 1. Config override → highest confidence (no IO)
    override_route = _route_from_override(canonical)
    if override_route:
        return _result("artist", 1.0, override_route, "config_override")

    # 2. Event pattern
    if _EVENT_RE.search(folder_name):
        genre = _genre_from_name(folder_name)
        return _result("event", 0.8 if genre else 0.5,
                       _route_by_genre(genre), "name_pattern_event")

    # 3. Label pattern
    if _LABEL_RE.search(folder_name):
        genre = _genre_from_name(folder_name)
        return _result("label", 0.7, _route_by_genre(genre), "name_pattern_label")

    # 4. Collection pattern
    if _COLLECTION_RE.search(folder_name):
        return _result("collection", 0.6, None, "name_pattern_collection")

    # 5. Artist memory lookup (fast, no disk IO)
    mem_route = _route_from_memory(canonical)
    if mem_route:
        return _result("artist", 0.9, mem_route, "artist_memory")

    # 6. DJ prefix → treat as artist, still try routing
    if _DJ_RE.search(folder_name):
        route = _route_from_memory(canonical) or _route_from_override(canonical)
        return _result("dj_artist", 0.75, route, "name_pattern_dj")

    # 7. Genre hint from folder name (e.g. "Drum & Bass Rampage")
    genre_hint = _genre_from_name(folder_name)
    if genre_hint:
        return _result("event", 0.6, _route_by_genre(genre_hint), "name_genre_hint")

    # 8. ID3 tag scan — dominant artist check
    if folder_path.is_dir() and file_count > 0:
        tag_data = _scan_folder_tags(folder_path)
        if tag_data["artists"]:
            top_artist, count = tag_data["artists"].most_common(1)[0]
            total = sum(tag_data["artists"].values())
            if count / total >= 0.7:
                tag_route = (_route_from_override(top_artist)
                             or _route_from_memory(top_artist))
                if not tag_route and tag_data["genres"]:
                    top_genre = tag_data["genres"].most_common(1)[0][0]
                    tag_route = _route_by_genre(top_genre)
                if tag_route:
                    return _result("artist", 0.75, tag_route, "id3_dominant_artist")

    return _result("unknown", 0.0, None, "unresolved")


def route_artist_folder(folder_name: str, folder_path: Path | None = None) -> str | None:
    """
    Return canonical Library/ relative path for an artist folder (without artist suffix).
    Returns None if routing cannot be determined.

    Phase 4: seeds artist_memory on successful resolution.
    """
    canonical = resolve_alias(folder_name)
    route = _route_from_override(canonical) or _route_from_memory(canonical)
    if not route and folder_path and folder_path.is_dir():
        info = detect_folder_type(folder_name, folder_path)
        if info["confidence"] >= 0.5:
            route = info.get("suggested_route")
    if route:
        seed_from_routing(canonical, route, source="auto_migration")
    return route


def seed_from_routing(artist_name: str, genre_path: str,
                      source: str = "auto_migration") -> None:
    """
    Persist artist→genre into artist_memory so future downloads route immediately.
    genre_path is a Library/ relative path e.g. "Library/Electronic/UKG".
    """
    try:
        from services.genre_router import GENRE_TAXONOMY, LIBRARY_ROOT
        from services.artist_memory_service import record_move
        rel = genre_path.removeprefix(f"{LIBRARY_ROOT}/")
        for canonical, (family, subpath) in GENRE_TAXONOMY.items():
            if subpath == rel or subpath.endswith("/" + rel.split("/")[-1]):
                record_move(artist_name, canonical, source=source, family=family)
                logger.info(f"[artist_folder] Seeded memory: {artist_name!r} → {canonical} ({source})")
                return
        # Fallback: last path component as genre guess
        record_move(artist_name, rel.split("/")[-1], source=source)
    except Exception as e:
        logger.debug(f"[artist_folder] seed_from_routing failed for {artist_name!r}: {e}")


# ── Analysis report ───────────────────────────────────────────────────────────

def generate_artist_folder_analysis(
    unmapped_folders: list[str],
    base_dir: Path,
    output_path: Path | None = None,
) -> dict:
    """
    Analyze unmapped folders, classify each, and write artist_folder_analysis.json.
    Returns the full analysis dict.
    """
    if output_path is None:
        output_path = base_dir / "artist_folder_analysis.json"

    results: dict = {}
    for name in unmapped_folders:
        folder_path = base_dir / name
        if not folder_path.is_dir():
            results[name] = {
                "type": "missing", "confidence": 0.0,
                "canonical_name": name, "suggested_route": None,
                "source": "not_on_disk", "file_count": 0,
            }
            continue
        results[name] = detect_folder_type(name, folder_path)

    resolved   = [n for n, r in results.items() if r.get("suggested_route")]
    unresolved = [n for n, r in results.items() if not r.get("suggested_route")]

    payload = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_unmapped": len(unmapped_folders),
        "resolved":       len(resolved),
        "unresolved":     len(unresolved),
        "resolved_list":  resolved,
        "unresolved_list": unresolved,
        "folders":        results,
    }
    try:
        output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info(f"[artist_folder] Analysis: {len(resolved)} resolved, "
                    f"{len(unresolved)} unresolved → {output_path}")
    except Exception as e:
        logger.warning(f"[artist_folder] Could not write analysis: {e}")

    return payload
