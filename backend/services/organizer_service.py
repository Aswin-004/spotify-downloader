"""
Organizer Service
=================
• clean_folder_name        — sanitise a string for use as a folder name
• resolve_destination_path — compute collision-safe destination path
• detect_mix_variant       — Phase 4: identify remix/VIP/edit variants
• routing_reason_text      — Phase 9: human-readable routing explanation

Phase 4 — Remix Family Grouping
---------------------------------
Remix/VIP/Edit/Live variants of the same track are grouped inside a
shared ``{base_track_name}/`` subdirectory so related files stay together:

  Library/Electronic/House/Sammy Virji/
    ├── Track Name.mp3               ← flat (original, no variant detected)
    └── Track Name (Club Mix)/
          ├── Original.mp3
          └── Club Mix.mp3

Non-remix tracks remain flat in the artist folder.

Phase 9 — UI Instruction Layer
--------------------------------
``routing_reason_text`` turns the opaque (folder, confidence, source) tuple
into a short human sentence for display in the Telegram bot and web UI.
"""

import hashlib
import os
import shutil
import time
import re
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger
from mutagen.id3 import ID3, ID3NoHeaderError

from config import config

BASE_DOWNLOAD_DIR = Path(config.BASE_DOWNLOAD_DIR)

UNSAFE_CHARS = r'<>:"/\|?*'

# ── Phase 4: mix-variant detection ───────────────────────────────────────────

# Regex patterns that mark a filename as a named mix/edit/VIP/live variant.
# Pattern order matters: more-specific before less-specific.
_MIX_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("VIP",       re.compile(r"\bvip\b",                           re.IGNORECASE)),
    ("Extended",  re.compile(r"\bextended\s*(mix|edit|version)?\b",re.IGNORECASE)),
    ("Club Mix",  re.compile(r"\bclub\s*(mix|edit)\b",             re.IGNORECASE)),
    ("Remix",     re.compile(r"\bremix\b",                         re.IGNORECASE)),
    ("Radio",     re.compile(r"\bradio\s*(edit|mix|version)?\b",   re.IGNORECASE)),  # before Edit: "Radio Edit" must match Radio
    ("Edit",      re.compile(r"\bedit\b",                          re.IGNORECASE)),
    ("Live",      re.compile(r"\blive\b",                          re.IGNORECASE)),
    ("Acoustic",  re.compile(r"\bacoustic\b",                      re.IGNORECASE)),
    ("Instrumental", re.compile(r"\binstrumental\b",               re.IGNORECASE)),
    ("Bootleg",   re.compile(r"\bbootleg\b",                       re.IGNORECASE)),
]

# Strip these suffixes to recover the base track name.
_BASE_STRIP_RE = re.compile(
    r"[\s\-–—]+[\(\[\{]?"
    r"(vip|extended\s*(mix|edit|version)?|club\s*(mix|edit)|remix|edit"
    r"|live|acoustic|radio\s*(edit|mix|version)?|instrumental|bootleg)"
    r"[\)\]\}]?[\s\-–—]*$",
    re.IGNORECASE,
)


def detect_mix_variant(filename: str) -> tuple[str, str]:
    """
    Detect whether a filename represents a named mix variant.

    Returns:
        (base_name, variant_label)

        ``base_name``    — track family root (e.g. "Feel So Close")
        ``variant_label``— human label  (e.g. "Extended Mix", "") or ""
                           when the file is the canonical original.

    Examples:
        "Feel So Close (Extended Mix).mp3" → ("Feel So Close", "Extended")
        "Titanium (VIP).mp3"               → ("Titanium", "VIP")
        "Regular Track.mp3"                → ("Regular Track", "")
    """
    stem = Path(filename).stem

    # Check for variant keyword in the stem
    for label, pattern in _MIX_PATTERNS:
        if pattern.search(stem):
            base = _BASE_STRIP_RE.sub("", stem).strip().strip("-–—").strip()
            if not base:
                base = stem  # safety: never return empty base
            return base, label

    return stem, ""


# ── Collision-safe filename resolution ───────────────────────────────────────

def _collision_safe_name(
    dest_dir: Path,
    filename: str,
    artist_name: str = "",
    spotify_id: str = "",
) -> Path:
    """
    Return a collision-safe filepath under dest_dir using a priority chain:
      1. Original filename                      →  Song.mp3
      2. + clean artist suffix                  →  Song - Sammy Virji.mp3
      3. + first 6 chars of spotify_id          →  Song - 6UXjP1.mp3
      4. + md5 hash of (dest_dir + filename)    →  Song - a3f8b2.mp3

    Never overwrites an existing file. Never returns an empty stem.
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate

    try:
        from services.metrics_service import record_value, M_COLLISION_RESOLVED
        record_value(M_COLLISION_RESOLVED, 1.0, tags={"filename": filename})
    except Exception:
        pass

    stem = Path(filename).stem
    ext  = Path(filename).suffix

    if artist_name:
        safe_artist = clean_folder_name(artist_name)
        candidate = dest_dir / f"{stem} - {safe_artist}{ext}"
        if not candidate.exists():
            return candidate

    if spotify_id:
        candidate = dest_dir / f"{stem} - {spotify_id[:6]}{ext}"
        if not candidate.exists():
            return candidate

    h = hashlib.md5(f"{dest_dir}/{filename}".encode()).hexdigest()[:6]
    return dest_dir / f"{stem} - {h}{ext}"


# ── Folder name sanitisation ──────────────────────────────────────────────────

def clean_folder_name(name: str) -> str:
    """
    Strip filesystem-unsafe characters from a folder name.
    Returns 'Unknown' for empty / whitespace-only input.
    """
    if not name:
        return "Unknown"
    for char in UNSAFE_CHARS:
        name = name.replace(char, "")
    name = name.strip().strip("_")
    while "  " in name:
        name = name.replace("  ", " ")
    return name or "Unknown"


# ── Destination path resolution ───────────────────────────────────────────────

def resolve_destination_path(
    filename: str,
    folder_structure: str,
    group_remix_families: bool = True,
    artist_name: str = "",
    spotify_id: str = "",
) -> Tuple[str, str, str]:
    """
    Resolve the destination folder and filepath, handling:
      • directory creation
      • remix-family subfolder grouping (Phase 4)
      • collision-safe filename (artist suffix → spotify_id → hash)

    Args:
        filename:             Bare filename with .mp3 extension.
        folder_structure:     Relative path from BASE_DOWNLOAD_DIR
                              (e.g. "Library/Electronic/UKG" — flat, no artist).
        group_remix_families: When True (default), remix/VIP/edit variants are
                              placed inside ``{base_name}/`` subfolders.
        artist_name:          Used as first-choice collision suffix.
        spotify_id:           Used as second-choice collision suffix.

    Returns:
        (dest_folder_path, new_filepath, relative_path)
    """
    base_dest = BASE_DOWNLOAD_DIR / folder_structure

    # Phase 4: group remix families into subfolders
    if group_remix_families:
        base_name, variant_label = detect_mix_variant(filename)
        if variant_label:
            family_dir = base_dest / clean_folder_name(base_name)
            family_dir.mkdir(parents=True, exist_ok=True)
            dest_folder = family_dir
        else:
            base_dest.mkdir(parents=True, exist_ok=True)
            dest_folder = base_dest
    else:
        base_dest.mkdir(parents=True, exist_ok=True)
        dest_folder = base_dest

    safe_path = _collision_safe_name(dest_folder, filename, artist_name, spotify_id)
    if safe_path.name != filename:
        logger.info(f"[organizer] Collision resolved: {filename} → {safe_path.name}")

    relative_path = str(safe_path.relative_to(BASE_DOWNLOAD_DIR))
    return str(dest_folder), str(safe_path), relative_path


# ── Phase 9: UI instruction text ─────────────────────────────────────────────

def routing_reason_text(
    folder: str,
    confidence: float,
    source: str,
    title: str = "",
    artist: str = "",
) -> str:
    """
    Return a short, human-readable explanation of why a track was routed
    to a specific folder.  Used by Telegram bot and web UI.

    Args:
        folder:     Relative destination folder path.
        confidence: 0.0–1.0 routing confidence.
        source:     Routing source key (see genre_router.py).
        title:      Track title (for personalisation, optional).
        artist:     Artist name (for personalisation, optional).

    Returns:
        One or two sentences suitable for display to the user.
    """
    pct  = int(confidence * 100)
    name = f"*{title}*" if title else "Track"

    if folder.startswith("NeedsReview"):
        if confidence == 0.0:
            why = "no genre data could be determined"
        else:
            why = f"genre confidence was too low ({pct}%)"
        return (
            f"{name} went to **NeedsReview** because {why}. "
            f"Move it to the correct genre folder — the system will learn from that move."
        )

    if folder.startswith("Quarantine"):
        return (
            f"{name} was sent to **Quarantine** — the file appears broken, "
            f"corrupted, or is a temp artefact. It will not be processed further."
        )

    source_phrases = {
        "artist_override":  "an explicit artist→genre override in config",
        "artist_memory":    "a previous manual move you made (the system remembered)",
        "spotify_genre":    f"Spotify genre tags (conf {pct}%)",
        "raw_spotify":      f"a raw Spotify genre tag (conf {pct}%)",
        "devanagari":       "a Devanagari-script artist name (script heuristic)",
        "musicbrainz":      "MusicBrainz genre data",
        "cache":            "a cached routing decision",
    }
    phrase = source_phrases.get(source, source)
    return f"{name} → **{folder}** via {phrase}."


def needs_review_guidance(artist: str = "", genre_suggestion: str = "") -> str:
    """
    Return actionable guidance for a track stuck in NeedsReview.

    Called by the Telegram /needsreview command or the web UI review panel.
    """
    lines = [
        "**How to improve routing:**",
        "1. Move the file to the correct genre folder (e.g. Library/Electronic/House/).",
        "2. The classifier will remember this artist for future downloads.",
    ]
    if artist:
        lines.append(
            f"3. You can also add **{artist}** to ARTIST_GENRE_OVERRIDE in config.py "
            f"for an immediate, permanent fix."
        )
    if genre_suggestion:
        lines.append(f"4. Suggested genre: **{genre_suggestion}**")
    return "\n".join(lines)


# ── Batch organize public API ─────────────────────────────────────────────────

def organize_library(mode: str = "artist") -> dict:
    """
    Batch-organize all .mp3 files under BASE_DOWNLOAD_DIR.

    Args:
        mode: 'artist' | 'genre' | 'artist_genre'

    Returns:
        { moved: int, skipped: int, errors: list[{file, error}] }
    """
    return _run_organize(mode, since_hours=None)


def organize_recent(mode: str = "artist", hours: int = 24) -> dict:
    """
    Organize .mp3 files modified within the last N hours.
    """
    return _run_organize(mode, since_hours=float(hours))


def _run_organize(mode: str, since_hours: Optional[float]) -> dict:
    moved   = 0
    skipped = 0
    errors  = []
    cutoff  = (time.time() - since_hours * 3600) if since_hours is not None else None

    for root, _dirs, filenames in os.walk(BASE_DOWNLOAD_DIR):
        for fname in filenames:
            if not fname.lower().endswith(".mp3"):
                continue
            src = Path(root) / fname
            if cutoff is not None and src.stat().st_mtime < cutoff:
                continue
            try:
                artist, genre = _read_tags(src)
                folder_structure = _folder_structure(mode, artist, genre)
                target_dir = BASE_DOWNLOAD_DIR / folder_structure

                # Skip if already inside the target directory (or a remix subfolder of it)
                try:
                    src.parent.relative_to(target_dir)
                    skipped += 1
                    continue
                except ValueError:
                    pass

                _, dest_path, _ = resolve_destination_path(
                    fname, folder_structure,
                    group_remix_families=True,
                    artist_name=artist,
                )
                shutil.move(str(src), dest_path)
                logger.info(f"[organizer] {fname} → {dest_path}")
                moved += 1
            except Exception as e:
                logger.warning(f"[organizer] Error organizing {fname}: {e}")
                errors.append({"file": fname, "error": str(e)})

    return {"moved": moved, "skipped": skipped, "errors": errors}


def _read_tags(path: Path) -> Tuple[str, str]:
    try:
        tags   = ID3(str(path))
        artist = str(tags.get("TPE1") or "Unknown").strip() or "Unknown"
        genre  = str(tags.get("TCON") or "Unknown").strip() or "Unknown"
    except ID3NoHeaderError:
        artist, genre = "Unknown", "Unknown"
    return artist, genre


def _folder_structure(mode: str, artist: str, genre: str) -> str:
    a = clean_folder_name(artist)
    g = clean_folder_name(genre)
    if mode == "genre":
        return g
    if mode == "artist_genre":
        return f"{g}/{a}"
    return a  # default: artist
