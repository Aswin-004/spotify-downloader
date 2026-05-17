"""
Artist Memory Service — Phase 2
================================
Learns artist → genre associations from confirmed routing events
(manual user moves, high-confidence Spotify routing, artist overrides).

Future downloads by the same artist get a confidence boost so they route
directly to the correct Library/ folder without needing a Spotify API call.

Storage
-------
MongoDB collection ``artist_memory``:

  artist_key    str     — normalized lowercase key  (primary lookup)
  aliases       list    — known display-name variants
  genre         str     — canonical genre name (GENRE_TAXONOMY key, e.g. "House")
  family        str     — genre family ("Electronic" | "Indian" | "Global")
  move_count    int     — times this artist/genre pair was confirmed
  confidence    float   — min(1.0, move_count * 0.25); caps at 1.0 after 4 moves
  last_seen     datetime
  source        str     — "manual_move" | "artist_override" | "spotify_genre"

Aliases
-------
Artists often have alternate spellings ("WSTRN", "Wstrn", "wstrn").
`record_move` stores the display name as an alias every call, so all
variants map to the same record.

Confidence aging
----------------
Confidence decreases toward 0.5 if a record has not been seen in > 180 days.
It never drops below 0.5 so a stale memory still routes correctly — it just
loses its "certain" status and the routing system may supplement it with a
Spotify API call.
"""
from __future__ import annotations

import unicodedata
import re
from datetime import datetime, timedelta, timezone

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ALIAS_LIMIT = 20           # cap stored aliases per artist
_CONFIDENCE_PER_MOVE = 0.25 # reach 1.0 after 4 confirmed moves
_AGING_DAYS = 180           # days before confidence starts decaying
_AGING_FLOOR = 0.5          # never decay below this


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize_artist(name: str) -> str:
    """Lowercase, NFKD-normalise, collapse whitespace, drop unsafe chars."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(_UNSAFE, "", text)
    return " ".join(text.lower().split()).strip()


# ── MongoDB access (lazy) ─────────────────────────────────────────────────────

def _get_col():
    """Return the artist_memory collection, or None if MongoDB unavailable."""
    try:
        from database import get_artist_memory_collection
        return get_artist_memory_collection()
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def record_move(
    artist: str,
    genre: str,
    source: str = "manual_move",
    family: str = "",
) -> None:
    """
    Record a confirmed artist → genre association.

    Call this whenever a track is successfully routed with high confidence
    or a user manually moves a NeedsReview track to its canonical folder.

    Args:
        artist: Display name of the artist.
        genre:  Canonical genre name (must be a GENRE_TAXONOMY key, e.g. "House").
        source: "manual_move" | "artist_override" | "spotify_genre"
        family: Genre family; auto-resolved from GENRE_TAXONOMY if omitted.
    """
    if not artist or not genre:
        return
    col = _get_col()
    if col is None:
        return

    key = _normalize_artist(artist)
    if not key:
        return

    if not family:
        try:
            from services.genre_router import GENRE_TAXONOMY
            entry = GENRE_TAXONOMY.get(genre)
            family = entry[0] if entry else "Global"
        except Exception:
            family = "Global"

    now = datetime.now(timezone.utc)
    try:
        existing = col.find_one({"artist_key": key})
        if existing:
            new_count = existing.get("move_count", 0) + 1
            new_conf  = min(1.0, new_count * _CONFIDENCE_PER_MOVE)
            aliases   = existing.get("aliases", [])
            if artist not in aliases:
                aliases = (aliases + [artist])[-_ALIAS_LIMIT:]
            col.update_one(
                {"artist_key": key},
                {"$set": {
                    "genre":      genre,
                    "family":     family,
                    "move_count": new_count,
                    "confidence": new_conf,
                    "last_seen":  now,
                    "source":     source,
                    "aliases":    aliases,
                }},
            )
        else:
            col.insert_one({
                "artist_key": key,
                "aliases":    [artist],
                "genre":      genre,
                "family":     family,
                "move_count": 1,
                "confidence": _CONFIDENCE_PER_MOVE,
                "last_seen":  now,
                "source":     source,
            })
        logger.debug(f"[artist_memory] Recorded: {artist!r} → {genre} ({source})")
    except Exception as e:
        logger.warning(f"[artist_memory] record_move failed for {artist!r}: {e}")


def lookup_artist(artist: str) -> dict | None:
    """
    Return the memory record for an artist, or None if unknown.

    The returned dict always contains:
      genre, family, confidence, move_count, last_seen, source

    Confidence is age-adjusted: records older than _AGING_DAYS days decay
    toward _AGING_FLOOR proportionally.
    """
    if not artist:
        return None
    col = _get_col()
    if col is None:
        return None

    key = _normalize_artist(artist)
    if not key:
        return None

    try:
        doc = col.find_one({"artist_key": key}, {"_id": 0})
        if doc is None:
            # Try alias search (case-insensitive substring fallback)
            doc = col.find_one(
                {"aliases": {"$regex": f"^{re.escape(artist)}$", "$options": "i"}},
                {"_id": 0},
            )
        if doc is None:
            return None

        # Confidence aging
        last_seen = doc.get("last_seen")
        if last_seen and isinstance(last_seen, datetime):
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - last_seen).days
            if age_days > _AGING_DAYS:
                decay = (age_days - _AGING_DAYS) / _AGING_DAYS
                raw_conf = doc.get("confidence", _CONFIDENCE_PER_MOVE)
                doc["confidence"] = max(_AGING_FLOOR, raw_conf - decay * raw_conf)

        return doc
    except Exception as e:
        logger.warning(f"[artist_memory] lookup_artist failed for {artist!r}: {e}")
        return None


def forget_artist(artist: str) -> bool:
    """Remove all memory for an artist (e.g. when a move is undone). Returns True if deleted."""
    col = _get_col()
    if col is None:
        return False
    key = _normalize_artist(artist)
    if not key:
        return False
    try:
        result = col.delete_one({"artist_key": key})
        return result.deleted_count > 0
    except Exception:
        return False


def seed_artist_memory(force: bool = False) -> int:
    """
    Pre-seed artist_memory with a curated list of well-known artists.

    Idempotent: existing records are updated (move_count incremented),
    not replaced.  Safe to call multiple times.

    Args:
        force: When True, seed even if a record already exists (refreshes).

    Returns:
        Count of records written/updated.
    """
    # Curated seed list — (display_name, canonical_genre, family)
    _SEEDS: list[tuple[str, str, str]] = [
        # ── Indian — Tamil ────────────────────────────────────────────
        ("Anirudh Ravichander",  "Tamil",         "Indian"),
        ("A.R. Rahman",          "Tamil",         "Indian"),
        ("Sid Sriram",           "Tamil",         "Indian"),
        ("Santhosh Narayanan",   "Tamil",         "Indian"),
        ("Yuvan Shankar Raja",   "Tamil",         "Indian"),
        ("D. Imman",             "Tamil",         "Indian"),
        ("Harris Jayaraj",       "Tamil",         "Indian"),
        ("G.V. Prakash Kumar",   "Tamil",         "Indian"),
        # ── Indian — Telugu ───────────────────────────────────────────
        ("Devi Sri Prasad",      "Telugu",        "Indian"),
        ("Thaman S",             "Telugu",        "Indian"),
        ("MM Keeravaani",        "Telugu",        "Indian"),
        # ── Indian — Punjabi ──────────────────────────────────────────
        ("Karan Aujla",          "Punjabi",       "Indian"),
        ("AP Dhillon",           "Punjabi",       "Indian"),
        ("Shubh",                "Punjabi",       "Indian"),
        ("Juss",                 "Punjabi",       "Indian"),
        ("Harkirat Sangha",      "Punjabi",       "Indian"),
        ("Sidhu Moosewala",      "Punjabi",       "Indian"),
        ("Diljit Dosanjh",       "Punjabi",       "Indian"),
        ("Jass Manak",           "Punjabi",       "Indian"),
        ("Parmish Verma",        "Punjabi",       "Indian"),
        ("Mankirt Aulakh",       "Punjabi",       "Indian"),
        ("Hardy Sandhu",         "Punjabi",       "Indian"),
        ("Ammy Virk",            "Punjabi",       "Indian"),
        ("Gurdas Maan",          "Punjabi",       "Indian"),
        # ── Indian — Bollywood ────────────────────────────────────────
        ("King",                 "Indian",        "Indian"),
        ("Arijit Singh",         "Bollywood",     "Indian"),
        ("Sachet Tandon",        "Bollywood",     "Indian"),
        ("Pritam",               "Bollywood",     "Indian"),
        ("Vishal-Shekhar",       "Bollywood",     "Indian"),
        ("Shreya Ghoshal",       "Bollywood",     "Indian"),
        ("Neha Kakkar",          "Bollywood",     "Indian"),
        ("Badshah",              "Bollywood",     "Indian"),
        ("Sonu Nigam",           "Bollywood",     "Indian"),
        ("Himesh Reshammiya",    "Bollywood",     "Indian"),
        ("Atif Aslam",           "Bollywood",     "Indian"),
        ("Armaan Malik",         "Bollywood",     "Indian"),
        ("Jubin Nautiyal",       "Bollywood",     "Indian"),
        ("B Praak",              "Bollywood",     "Indian"),
        ("Guru Randhawa",        "Bollywood",     "Indian"),
        ("Shankar-Ehsaan-Loy",   "Bollywood",     "Indian"),
        ("Amit Trivedi",         "Bollywood",     "Indian"),
        # ── Indian — HipHop ──────────────────────────────────────────
        ("Seedhe Maut",          "Indian Hip Hop","Indian"),
        ("Divine",               "Indian Hip Hop","Indian"),
        ("KR$NA",                "Indian Hip Hop","Indian"),
        ("Emiway Bantai",        "Indian Hip Hop","Indian"),
        ("Mc Stan",              "Indian Hip Hop","Indian"),
        ("Prabh Deep",           "Indian Hip Hop","Indian"),
        ("Brodha V",             "Indian Hip Hop","Indian"),
        ("Nucleya",              "Indian Hip Hop","Indian"),
        # ── Marathi ──────────────────────────────────────────────────
        ("Ajay-Atul",            "Indian",        "Indian"),
        ("Avadhoot Gupte",       "Indian",        "Indian"),
        # ── Electronic — UKG ─────────────────────────────────────────
        ("Sammy Virji",          "UK Garage",     "Electronic"),
        ("WSTRN",                "UK Garage",     "Electronic"),
        ("Conducta",             "UK Garage",     "Electronic"),
        ("Zetts",                "UK Garage",     "Electronic"),
        ("Craig David",          "UK Garage",     "Electronic"),
        ("MJ Cole",              "UK Garage",     "Electronic"),
        # ── Electronic — Dubstep / Bass ───────────────────────────────
        ("Hamdi",                "Electronic",    "Electronic"),
        ("Skrillex",             "Electronic",    "Electronic"),
        ("KSHMR",                "Electronic",    "Electronic"),
        ("Illenium",             "Electronic",    "Electronic"),
        ("Martin Garrix",        "Electronic",    "Electronic"),
        ("Hardwell",             "Electronic",    "Electronic"),
        ("Diplo",                "Electronic",    "Electronic"),
        ("DJ Snake",             "Electronic",    "Electronic"),
        ("Zedd",                 "Electronic",    "Electronic"),
        ("Deadmau5",             "Electronic",    "Electronic"),
        # ── Electronic — House ────────────────────────────────────────
        ("Fred again..",         "House",         "Electronic"),
        ("Four Tet",             "House",         "Electronic"),
        ("Bicep",                "House",         "Electronic"),
        ("Calvin Harris",        "House",         "Electronic"),
        ("Solomun",              "House",         "Electronic"),
        ("Peggy Gou",            "House",         "Electronic"),
        ("Boris Brejcha",        "House",         "Electronic"),
        ("Fisher",               "House",         "Electronic"),
        ("Chris Lake",           "House",         "Electronic"),
        ("Disclosure",           "House",         "Electronic"),
        # ── Electronic — Drum & Bass ──────────────────────────────────
        ("Chase & Status",       "Drum and Bass", "Electronic"),
        ("Andy C",               "Drum and Bass", "Electronic"),
        ("Shy FX",               "Drum and Bass", "Electronic"),
        ("Sub Focus",            "Drum and Bass", "Electronic"),
        ("Dimension",            "Drum and Bass", "Electronic"),
        ("Pendulum",             "Drum and Bass", "Electronic"),
        ("Noisia",               "Drum and Bass", "Electronic"),
        ("Goldie",               "Drum and Bass", "Electronic"),
        # ── Global — Hip Hop ─────────────────────────────────────────
        ("Drake",                "Hip Hop",       "Global"),
        ("Kendrick Lamar",       "Hip Hop",       "Global"),
        ("J. Cole",              "Hip Hop",       "Global"),
        ("Travis Scott",         "Hip Hop",       "Global"),
        ("Tyler the Creator",    "Hip Hop",       "Global"),
        ("Central Cee",          "Hip Hop",       "Global"),
        ("Dave",                 "Hip Hop",       "Global"),
        # ── Global — Grime ───────────────────────────────────────────
        ("Skepta",               "Grime",         "Global"),
        ("Stormzy",              "Grime",         "Global"),
        ("AJ Tracey",            "Grime",         "Global"),
        ("Ghetts",               "Grime",         "Global"),
    ]

    col = _get_col()
    if col is None:
        logger.warning("[artist_memory] seed_artist_memory: MongoDB unavailable — skipping")
        return 0

    count = 0
    for display_name, genre, family in _SEEDS:
        try:
            key = _normalize_artist(display_name)
            if not force:
                existing = col.find_one({"artist_key": key})
                if existing:
                    # Already seeded — just refresh last_seen
                    from datetime import datetime, timezone
                    col.update_one(
                        {"artist_key": key},
                        {"$set": {"last_seen": datetime.now(timezone.utc)}},
                    )
                    count += 1
                    continue
            record_move(display_name, genre, source="seed", family=family)
            count += 1
        except Exception as e:
            logger.warning(f"[artist_memory] seed failed for {display_name!r}: {e}")

    logger.info(f"[artist_memory] seed_artist_memory: {count} record(s) written/updated")
    return count


def bulk_record_overrides() -> int:
    """
    Seed artist_memory from ARTIST_GENRE_OVERRIDE in config.
    Safe to call multiple times (upsert behaviour via record_move).
    Returns count of records written.
    """
    try:
        from config import config
        count = 0
        for artist, genre in config.ARTIST_GENRE_OVERRIDE.items():
            record_move(artist, genre, source="artist_override")
            count += 1
        logger.info(f"[artist_memory] Seeded {count} override(s) from config")
        return count
    except Exception as e:
        logger.warning(f"[artist_memory] bulk_record_overrides failed: {e}")
        return 0
