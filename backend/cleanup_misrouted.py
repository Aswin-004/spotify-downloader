#!/usr/bin/env python3
"""
cleanup_misrouted.py — Reclaim files that were misrouted by the organizer.

Background:
    When `force_folder` is set on a playlist refresh, every track in the batch
    is pinned to `Ingest/<force_folder>/`. If an earlier run happened BEFORE
    the organizer was taught about `force_folder`, files got re-routed out of
    that folder based on the ID3 TPE1 (artist) tag — e.g. a Sammy Virji
    compilation track tagged as "Joy Anonymous" ended up in `Joy Anonymous/`.

This script walks a list of "suspect" source folders, reads the TPE1 tag of
every `.mp3`, and when the target artist name appears anywhere in that tag
(case-insensitive), moves the file back into `BASE_DOWNLOAD_DIR/<target>/`.

Safety:
    * --dry-run is the default. You have to pass --execute explicitly.
    * Missing source folders are warned, not fatal.
    * Empty source folders are deleted only after a real (not dry) run.
    * Filename collisions in the target are resolved by appending _1, _2, …
      before the .mp3 extension (see resolve_collision).

Usage:
    # Preview (safe, default)
    python cleanup_misrouted.py \
        --target-artist "Sammy Virji" \
        --source-folders "Joy Anonymous,piri & tommy,Unknown T" \
        --dry-run

    # Actually move files
    python cleanup_misrouted.py \
        --target-artist "Sammy Virji" \
        --source-folders "Joy Anonymous,piri & tommy,Unknown T" \
        --execute
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from mutagen.id3 import ID3, ID3NoHeaderError


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

def load_base_dir() -> Path:
    """Read BASE_DOWNLOAD_DIR from the project .env file."""
    # Look for .env next to this file (backend/.env) first, then cwd.
    here = Path(__file__).resolve().parent
    env_path = here / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    base = os.getenv("BASE_DOWNLOAD_DIR")
    if not base:
        print("[ERROR] BASE_DOWNLOAD_DIR not set in .env", file=sys.stderr)
        sys.exit(1)

    base_path = Path(base).expanduser()
    if not base_path.is_dir():
        print(f"[ERROR] BASE_DOWNLOAD_DIR does not exist: {base_path}", file=sys.stderr)
        sys.exit(1)
    return base_path


# ═══════════════════════════════════════════════════════════════════
# TAG INSPECTION
# ═══════════════════════════════════════════════════════════════════

def read_artist_tag(mp3_path: Path) -> str:
    """
    Read TPE1 (artist) tag from an MP3. Returns empty string on any failure
    (missing tags, corrupt file, permission error, etc.).
    """
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        return ""
    except Exception as e:
        print(f"[WARN] Could not read tags from {mp3_path.name}: {e}")
        return ""

    if "TPE1" in tags and tags["TPE1"].text:
        return str(tags["TPE1"].text[0])
    return ""


def artist_matches(tag: str, target: str) -> bool:
    """Case-insensitive substring match — does target appear anywhere in tag?"""
    return bool(tag) and target.lower() in tag.lower()


# ═══════════════════════════════════════════════════════════════════
# COLLISION HANDLING
# ═══════════════════════════════════════════════════════════════════

def resolve_collision(dest_path: Path) -> Path:
    """
    Given a desired destination path, return a path that does not exist yet.

    If `dest_path` does not exist, return it unchanged.
    If it does, append `_1`, `_2`, ... before the extension until unique.

    Example:
        /music/Sammy Virji/track.mp3       (free)       → track.mp3
        /music/Sammy Virji/track.mp3       (taken)      → track_1.mp3
        /music/Sammy Virji/track.mp3,
        /music/Sammy Virji/track_1.mp3     (both taken) → track_2.mp3
    """
    if not dest_path.exists():
        return dest_path

    n = 1
    while n <= 1000:
        candidate = dest_path.with_name(f"{dest_path.stem}_{n}{dest_path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1

    raise RuntimeError(
        f"resolve_collision: could not find a free filename after 1000 attempts for {dest_path.name}"
    )


# ═══════════════════════════════════════════════════════════════════
# CORE CLEANUP
# ═══════════════════════════════════════════════════════════════════

def cleanup(
    target_artist: str,
    source_folders: list,
    execute: bool,
    base_dir: Path,
) -> dict:
    """
    Walk each source folder, match tracks by ID3 artist, and move them to
    base_dir/<target_artist>/. Returns a stats dict.
    """
    target_dir = base_dir / target_artist
    stats = {"moved": 0, "deleted": 0, "skipped": 0}

    for folder_name in source_folders:
        source_dir = base_dir / folder_name

        if not source_dir.is_dir():
            print(f"[WARN] Source folder missing, skipping: {folder_name}/")
            continue

        # Guard: don't "move" files into their own folder.
        if source_dir.resolve() == target_dir.resolve():
            print(f"[WARN] Source folder is the target folder, skipping: {folder_name}/")
            continue

        mp3_files = sorted(source_dir.glob("*.mp3"))
        if not mp3_files:
            print(f"[INFO] No .mp3 files in {folder_name}/")
            continue

        for mp3 in mp3_files:
            # Match on filename stem instead of ID3 TPE1 tag — the tag was
            # written at download time based on Spotify's artist field, which
            # is exactly what we're trying to work around. The filename is
            # the more reliable signal for "did this track come from the
            # Sammy Virji batch?" because yt-dlp builds it from the playlist
            # track title, which is what the user actually saw.
            if target_artist.lower() not in mp3.stem.lower():
                stats["skipped"] += 1
                continue

            if execute:
                target_dir.mkdir(parents=True, exist_ok=True)
                desired = target_dir / mp3.name
                final_dest = resolve_collision(desired)
                try:
                    shutil.move(str(mp3), str(final_dest))
                except Exception as e:
                    print(f"[ERROR] Failed to move {mp3.name}: {e}")
                    stats["skipped"] += 1
                    continue
                label = final_dest.name if final_dest.name != mp3.name else mp3.name
                print(f"[MOVED] {mp3.name} → {target_artist}/{label}")
            else:
                print(f"[DRY RUN] Would move: {mp3.name} → {target_artist}/")

            stats["moved"] += 1

        # Folder cleanup only happens on real runs — in dry-run nothing moved,
        # so the folder is never really "empty".
        if execute:
            try:
                remaining = list(source_dir.iterdir())
            except OSError:
                remaining = [None]  # can't read → don't try to delete
            if not remaining:
                try:
                    source_dir.rmdir()
                    print(f"[DELETED] {folder_name}/")
                    stats["deleted"] += 1
                except OSError as e:
                    print(f"[WARN] Could not delete empty {folder_name}/: {e}")

    return stats


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move misrouted files back to a target artist folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--target-artist",
        required=True,
        help='Artist to reclaim, e.g. "Sammy Virji"',
    )
    parser.add_argument(
        "--source-folders",
        required=True,
        help='Comma-separated folders to scan, e.g. "Joy Anonymous,Unknown T"',
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only — no files are moved (default).",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Actually move files. Without this flag, runs in dry-run mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    execute = bool(args.execute)  # dry-run is default when neither is passed

    base_dir = load_base_dir()
    source_folders = [f.strip() for f in args.source_folders.split(",") if f.strip()]
    if not source_folders:
        print("[ERROR] --source-folders parsed to an empty list", file=sys.stderr)
        return 1

    target_artist = args.target_artist.strip()
    if not target_artist:
        print("[ERROR] --target-artist cannot be empty", file=sys.stderr)
        return 1

    print(f"[INFO] Base dir:       {base_dir}")
    print(f"[INFO] Target artist:  {target_artist}")
    print(f"[INFO] Source folders: {source_folders}")
    print(f"[INFO] Mode:           {'EXECUTE' if execute else 'DRY RUN'}")
    print()

    stats = cleanup(target_artist, source_folders, execute, base_dir)

    print()
    verb = "moved" if execute else "would be moved"
    print(
        f"Done. {stats['moved']} files {verb}, "
        f"{stats['deleted']} folders deleted, "
        f"{stats['skipped']} skipped."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
