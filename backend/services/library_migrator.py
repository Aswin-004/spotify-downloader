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


# ═══════════════════════════════════════════════════════════════════
# INTERACTIVE PROMPT + UNDO LOG
# ═══════════════════════════════════════════════════════════════════

def prompt_unresolved(
    unresolved: List[str],
    categories: List[str],
    config_path: Path,
    config_data: Dict,
) -> Dict[str, str]:
    """
    Interactively prompt user to assign each unresolved artist to a category.
    Saves each answer to config_path immediately after it is given.
    Returns {artist: category} for resolved items only (skipped items are omitted).
    """
    newly_resolved: Dict[str, str] = {}
    for artist in unresolved:
        print(f'\nArtist: "{artist}"')
        for i, cat in enumerate(categories, 1):
            print(f"  [{i}] {cat}")
        print(f"  [{len(categories) + 1}] Skip")
        while True:
            try:
                raw = input("> ").strip()
                choice = int(raw)
            except (ValueError, EOFError):
                print("  Invalid input, skipping.")
                break
            if 1 <= choice <= len(categories):
                category = categories[choice - 1]
                newly_resolved[artist] = category
                config_data["mappings"][artist] = category
                save_config(config_path, config_data)
                logger.info(f"[migrator] Assigned '{artist}' → '{category}', saved")
                break
            elif choice == len(categories) + 1:
                logger.info(f"[migrator] Skipped '{artist}'")
                break
            else:
                print(f"  Please enter 1–{len(categories) + 1}")
    return newly_resolved


def write_undo_log(entries: List[Dict], logs_dir: Path) -> Path:
    """
    Write undo log JSON to logs_dir/migrate_undo_YYYYMMDD_HHMMSS.json.
    Returns the path of the written file.
    Each entry: {"from": "<dest path>", "to": "<source path>"}
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"migrate_undo_{ts}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    logger.info(f"[migrator] Undo log written: {log_path}")
    return log_path


# ═══════════════════════════════════════════════════════════════════
# REPORT BUILDER
# ═══════════════════════════════════════════════════════════════════

CATEGORIES_ORDER = ["Punjabi", "English", "Hindi", "House"]


def build_report_text(
    category_stats: Dict[str, Dict],
    errors: List[Dict],
    skipped_artists: List[str],
    undo_log_path: Optional[str],
    duration_seconds: float,
    html: bool = False,
) -> str:
    """
    Build the final migration report as a plain string or HTML <pre> block.
    category_stats format: {category: {"files": int, "bytes": int}}
    """
    lines = ["MUSIC LIBRARY MIGRATION REPORT", "=" * 32]

    total_files = 0
    total_bytes = 0
    for cat in CATEGORIES_ORDER:
        stats = category_stats.get(cat, {"files": 0, "bytes": 0})
        f, b = stats["files"], stats["bytes"]
        total_files += f
        total_bytes += b
        lines.append(f"{cat:<9}: {f:>4} files  ({_fmt_bytes(b)})")

    lines.append("-" * 32)
    lines.append(f"{'Total':<9}: {total_files:>4} files  ({_fmt_bytes(total_bytes)})")
    lines.append(f"Errors   : {len(errors)}")

    if skipped_artists:
        lines.append(f"Skipped artists (unresolved): {', '.join(skipped_artists)}")

    if undo_log_path:
        lines.append(f"Undo log : {undo_log_path}")

    mins = int(duration_seconds // 60)
    secs = int(duration_seconds % 60)
    lines.append(f"Duration : {mins}m {secs}s")

    text = "\n".join(lines)
    return f"<pre>{text}</pre>" if html else text


# ═══════════════════════════════════════════════════════════════════
# MIGRATION ENGINE
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MigrationResult:
    files_moved: int = 0
    errors: List[Dict] = field(default_factory=list)
    skipped_artists: List[str] = field(default_factory=list)
    non_empty_source_folders: List[str] = field(default_factory=list)
    category_stats: Dict[str, Dict] = field(default_factory=dict)
    undo_entries: List[Dict] = field(default_factory=list)
    undo_log_path: Optional[str] = None
    duration_seconds: float = 0.0


def migrate_library(
    source: Path,
    dest: Path,
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    interactive: bool = False,
    dry_run: bool = False,
    logs_dir: Path = DEFAULT_LOGS_DIR,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> MigrationResult:
    """
    Migrate all audio files from artist-folder source into flat category buckets at dest.
    """
    import time
    start = time.time()

    result = MigrationResult()
    result.category_stats = {cat: {"files": 0, "bytes": 0} for cat in CATEGORIES_ORDER}

    config_data = load_config(Path(config_path))
    categories = config_data["categories"]
    mappings = config_data["mappings"]

    source_folders = scan_source_folders(Path(source))
    if not source_folders:
        logger.info(f"[migrator] No artist folders with audio found in {source}")
        result.duration_seconds = time.time() - start
        return result

    resolved, unresolved = resolve_artists(list(source_folders.keys()), mappings)

    if unresolved and interactive and sys.stdin.isatty():
        newly_resolved = prompt_unresolved(unresolved, categories, Path(config_path), config_data)
        resolved.update(newly_resolved)
        unresolved = [a for a in unresolved if a not in newly_resolved]

    result.skipped_artists = unresolved

    total_files = sum(len(files) for artist, files in source_folders.items() if artist in resolved)

    files_done = 0
    for artist, category in resolved.items():
        audio_files = source_folders.get(artist, [])
        for src_file in audio_files:
            if dry_run:
                logger.info(f"[migrator] DRY RUN: {src_file.name} → {category}/")
                files_done += 1
                if progress_cb:
                    progress_cb(files_done, total_files)
                continue

            dest_path = resolve_dest_path(Path(dest), category, src_file.name)
            src_bytes = src_file.stat().st_size

            ok = copy_verify_delete(src_file, dest_path)
            if ok:
                result.files_moved += 1
                result.undo_entries.append({
                    "from": str(dest_path),
                    "to": str(src_file),
                })
                result.category_stats[category]["files"] += 1
                result.category_stats[category]["bytes"] += src_bytes
                logger.info(f"[migrator] ✓ {src_file.name} → {category}/")
            else:
                result.errors.append({"file": str(src_file), "error": "MD5 mismatch"})

            files_done += 1
            if progress_cb:
                progress_cb(files_done, total_files)

            try:
                from database import _get_db
                db = _get_db()
                db.download_history.update_one(
                    {"filename": src_file.name},
                    {"$set": {"folder": category, "relative_path": str(dest_path)}},
                )
            except Exception as db_err:
                logger.debug(f"[migrator] MongoDB update skipped: {db_err}")

    if not dry_run:
        for artist in resolved:
            artist_dir = Path(source) / artist
            if artist_dir.exists():
                remaining = list(artist_dir.iterdir())
                if not remaining:
                    artist_dir.rmdir()
                    logger.info(f"[migrator] Removed empty folder: {artist}/")
                else:
                    result.non_empty_source_folders.append(artist)
                    logger.warning(f"[migrator] Non-empty folder left: {artist}/ ({len(remaining)} items)")

    if result.undo_entries and not dry_run:
        undo_path = write_undo_log(result.undo_entries, Path(logs_dir))
        result.undo_log_path = str(undo_path)

    result.duration_seconds = time.time() - start
    logger.info(
        f"[migrator] Done: moved={result.files_moved}, "
        f"skipped={len(result.skipped_artists)}, errors={len(result.errors)}"
    )
    return result


def undo_migration(undo_log_path: Path) -> None:
    """
    Reverse a migration using its undo log.
    Each entry: {"from": "<dest path>", "to": "<original source path>"}
    """
    undo_log_path = Path(undo_log_path)
    if not undo_log_path.exists():
        raise FileNotFoundError(f"Undo log not found: {undo_log_path}")
    with open(undo_log_path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    logger.info(f"[migrator] Undoing {len(entries)} moves from {undo_log_path.name}")
    for entry in entries:
        src = Path(entry["from"])
        dest = Path(entry["to"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        logger.info(f"[migrator] Restored: {src.name} → {dest.parent.name}/")
    print(f"Undo complete: {len(entries)} files restored.")
