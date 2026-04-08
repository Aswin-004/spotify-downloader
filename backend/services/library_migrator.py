"""
Music Library Migrator
======================
Reorganises a folder-per-artist library into flat language/genre buckets.

Public API:
  load_config(config_path) -> Dict
  save_config(config_path, data) -> None
  migrate_library(source, dest, config_path, *, interactive, dry_run, logs_dir, progress_cb) -> MigrationResult
  build_report_text(category_stats, errors, skipped_artists, undo_log_path, duration_seconds, html) -> str
  undo_migration(undo_log_path) -> None
"""

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac"}

# Default config path relative to this file: backend/config/artist_categories.json
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "artist_categories.json"

# Default logs dir: backend/logs/
DEFAULT_LOGS_DIR = Path(__file__).parent.parent / "logs"


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

def load_config(config_path: Path) -> Dict:
    """Load artist_categories.json. Raises FileNotFoundError if missing."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config_path: Path, data: Dict) -> None:
    """Write artist_categories.json, creating parent dirs if needed."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
# FILE OPERATION HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def md5_file(path: Path) -> str:
    """Return MD5 hex digest of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_dest_path(dest_root: Path, category: str, filename: str) -> Path:
    """
    Return collision-free destination path inside dest_root/category/.
    If dest_root/category/filename already exists, appends _1, _2, etc. to stem.
    Creates the category directory if it does not exist.
    """
    dest_folder = Path(dest_root) / category
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest_path = dest_folder / filename
    if not dest_path.exists():
        return dest_path
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while (dest_folder / f"{stem}_{counter}{suffix}").exists():
        counter += 1
    return dest_folder / f"{stem}_{counter}{suffix}"


def copy_verify_delete(src: Path, dest: Path) -> bool:
    """
    Copy src → dest, verify MD5 checksums match, then delete src.
    If checksums differ: deletes the bad copy, leaves src untouched, returns False.
    Returns True on success.
    """
    src_md5 = md5_file(src)
    shutil.copy2(str(src), str(dest))
    dest_md5 = md5_file(dest)
    if src_md5 != dest_md5:
        dest.unlink(missing_ok=True)
        logger.error(f"[migrator] MD5 mismatch {src.name} — bad copy deleted, source kept")
        return False
    src.unlink()
    return True


# ═══════════════════════════════════════════════════════════════════
# SCANNER AND RESOLVER
# ═══════════════════════════════════════════════════════════════════

def scan_source_folders(source: Path) -> Dict[str, List[Path]]:
    """
    Scan source for artist subdirectories that contain at least one audio file.
    Returns {folder_name: [sorted list of audio file Paths]}.
    Root-level files are ignored — only subfolders are considered.
    """
    result: Dict[str, List[Path]] = {}
    for folder in sorted(Path(source).iterdir()):
        if not folder.is_dir():
            continue
        audio_files = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        )
        if audio_files:
            result[folder.name] = audio_files
    return result


def resolve_artists(
    artist_folders: List[str],
    mappings: Dict[str, Optional[str]],
) -> Tuple[Dict[str, str], List[str]]:
    """
    Split artist folder names into resolved and unresolved.
    - resolved: {artist_name: category} for artists with a non-null mapping
    - unresolved: list of artists with null mapping or missing from mappings entirely
    """
    resolved: Dict[str, str] = {}
    unresolved: List[str] = []
    for artist in artist_folders:
        category = mappings.get(artist)
        if category:
            resolved[artist] = category
        else:
            unresolved.append(artist)
    return resolved, unresolved
