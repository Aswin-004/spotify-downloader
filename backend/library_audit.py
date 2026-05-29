#!/usr/bin/env python3
"""
Library Audit — scan existing Library/ files and re-route wrongly-placed ones.

Walks every Library/{genre}/*.mp3, reads ID3 artist tag, runs it through the
genre router (artist override + knowledge base, no Spotify API needed), and
flags files whose current folder does not match their expected genre.

Usage:
    # Dry-run (default) — print the move plan, touch nothing
    python library_audit.py

    # Fix known-corrupt ID3 tags first, then audit
    python library_audit.py --fix-tags

    # Execute moves (after reviewing the dry-run output)
    python library_audit.py --fix-tags --execute

    # Point at a different library root
    python library_audit.py --library "D:/DJ Music"

Options:
    --execute           Actually move files (default: dry-run, prints plan only)
    --fix-tags          Write corrected ID3 artist tags for known-corrupt files
                        before routing (run this first to fix metadata corruption)
    --library PATH      DJ music root dir (default: C:/Users/Aswin-pc/Desktop/DJ music)
    --min-confidence N  Only move files whose routing confidence >= N (default: 0.7)
    --genre FOLDER      Audit a single genre folder only (e.g. Bollywood)
"""

import argparse
import os
import sys
import shutil

# Make backend imports available when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError, TPE1, TIT2

# ── Known ID3-corrupt files ───────────────────────────────────────────────────
# Maps lowercase filename stem → dict of fields to overwrite.
# These files have wrong artist tags written by a bad MusicBrainz lookup.
KNOWN_TAG_CORRECTIONS: dict[str, dict] = {
    "taka": {
        "artist": "Skrillex",
    },
    "cure my desire (feat. clementine douglas) - themba remix": {
        "artist": "Themba",
    },
    "maria maria": {
        "artist": "Carlos Santana",
    },
}

DEFAULT_LIBRARY_ROOT = r"C:\Users\Aswin-pc\Desktop\DJ music"
LIBRARY_SUBDIR       = "Library"
MIN_CONFIDENCE       = 0.7


# ─────────────────────────────────────────────────────────────────────────────

def read_id3_tags(filepath: str) -> dict:
    """Return {'artist': ..., 'title': ...} from ID3 tags, empty strings on failure."""
    try:
        tags = ID3(filepath)
        artist = str(tags.get("TPE1", "")).strip()
        title  = str(tags.get("TIT2", "")).strip()
        return {"artist": artist, "title": title}
    except (ID3NoHeaderError, Exception):
        return {"artist": "", "title": ""}


def write_id3_artist(filepath: str, new_artist: str) -> bool:
    """Overwrite TPE1 (artist) tag. Returns True on success."""
    try:
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            tags = ID3()
        tags["TPE1"] = TPE1(encoding=3, text=new_artist)
        tags.save(filepath)
        return True
    except Exception as e:
        print(f"  ERROR writing tag to {filepath}: {e}")
        return False


def fix_known_tags(library_path: str, dry_run: bool = True) -> list[dict]:
    """
    Walk library_path, find files matching KNOWN_TAG_CORRECTIONS by filename stem,
    and (if not dry_run) write the corrected artist tag.

    Returns list of correction records.
    """
    corrections = []
    for root, dirs, files in os.walk(library_path):
        dirs[:] = [d for d in dirs if d.lower() not in ("quarantine", "needsreview", "__pycache__")]
        for fname in files:
            if not fname.lower().endswith(".mp3"):
                continue
            stem = os.path.splitext(fname)[0].lower()
            correction = KNOWN_TAG_CORRECTIONS.get(stem)
            if not correction:
                continue
            fpath = os.path.join(root, fname)
            current = read_id3_tags(fpath)
            new_artist = correction.get("artist", current["artist"])
            if current["artist"].lower() == new_artist.lower():
                continue  # already correct
            record = {
                "path": fpath,
                "filename": fname,
                "old_artist": current["artist"],
                "new_artist": new_artist,
            }
            corrections.append(record)
            if not dry_run:
                ok = write_id3_artist(fpath, new_artist)
                record["applied"] = ok
            else:
                record["applied"] = False
    return corrections


def route_artist(artist_name: str) -> tuple[str, float, str]:
    """
    Route artist_name through genre_router (no Spotify API).

    Returns (folder_path, confidence, source).
    folder_path is e.g. "Library/Dubstep" or "NeedsReview/Skrillex".
    """
    try:
        from services.genre_router import resolve_genre_folder_with_confidence
        folder, conf, source = resolve_genre_folder_with_confidence(
            artist_id="",
            artist_name=artist_name,
            sp=None,
        )
        return folder, conf, source
    except Exception as e:
        return f"NeedsReview/{artist_name}", 0.0, f"error: {e}"


def genre_from_path(folder_path: str) -> str:
    """
    Extract the genre folder name from a router-returned path.
    "Library/Dubstep" → "Dubstep"
    "NeedsReview/Skrillex" → None
    """
    if not folder_path or folder_path.startswith("NeedsReview"):
        return None
    parts = folder_path.replace("\\", "/").split("/")
    # Last non-empty segment is the genre subfolder
    return parts[-1] if parts else None


def build_move_manifest(
    library_path: str,
    min_confidence: float = MIN_CONFIDENCE,
    genre_filter: str = None,
) -> list[dict]:
    """
    Scan library_path/Library/{genre}/ and return a list of move records
    for files that are in the wrong genre folder.

    Applies KNOWN_TAG_CORRECTIONS in-memory before routing so corrupt-tag
    files are caught even in dry-run (before --fix-tags writes to disk).

    Each record:
        src_path, filename, current_genre, expected_genre,
        artist, confidence, source
    """
    lib_dir = os.path.join(library_path, LIBRARY_SUBDIR)
    if not os.path.isdir(lib_dir):
        print(f"ERROR: Library not found at {lib_dir}")
        return []

    moves = []
    skipped_low_conf = 0
    skipped_no_artist = 0

    genre_dirs = [d for d in os.listdir(lib_dir)
                  if os.path.isdir(os.path.join(lib_dir, d))
                  and d.lower() not in ("quarantine",)]
    if genre_filter:
        genre_dirs = [d for d in genre_dirs if d.lower() == genre_filter.lower()]

    for genre_dir in sorted(genre_dirs):
        genre_folder = os.path.join(lib_dir, genre_dir)
        for fname in sorted(os.listdir(genre_folder)):
            if not fname.lower().endswith(".mp3"):
                continue
            fpath = os.path.join(genre_folder, fname)
            tags  = read_id3_tags(fpath)
            artist = tags["artist"]

            # Apply in-memory tag correction so corrupt-tag files are caught
            # even before --fix-tags writes the corrected tag to disk
            stem = os.path.splitext(fname)[0].lower()
            correction = KNOWN_TAG_CORRECTIONS.get(stem)
            if correction and correction.get("artist"):
                artist = correction["artist"]

            if not artist:
                skipped_no_artist += 1
                continue

            routed_path, conf, source = route_artist(artist)

            if conf < min_confidence:
                skipped_low_conf += 1
                continue

            expected_genre = genre_from_path(routed_path)
            if not expected_genre:
                skipped_low_conf += 1
                continue

            if expected_genre.lower() == genre_dir.lower():
                continue  # already in the right folder

            moves.append({
                "src_path":       fpath,
                "filename":       fname,
                "current_genre":  genre_dir,
                "expected_genre": expected_genre,
                "artist":         artist,
                "confidence":     conf,
                "source":         source,
            })

    if skipped_low_conf:
        print(f"  (skipped {skipped_low_conf} files: routing confidence below {min_confidence:.0%})")
    if skipped_no_artist:
        print(f"  (skipped {skipped_no_artist} files: no ID3 artist tag)")

    return moves


def execute_moves(moves: list[dict], library_path: str) -> tuple[int, int]:
    """
    Perform the moves in the manifest.
    Returns (success_count, fail_count).
    """
    lib_dir = os.path.join(library_path, LIBRARY_SUBDIR)
    success = fail = 0

    for m in moves:
        dest_dir  = os.path.join(lib_dir, m["expected_genre"])
        dest_path = os.path.join(dest_dir, m["filename"])

        # Resolve filename conflicts
        if os.path.exists(dest_path):
            stem, ext = os.path.splitext(m["filename"])
            i = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(dest_dir, f"{stem}_{i}{ext}")
                i += 1

        try:
            os.makedirs(dest_dir, exist_ok=True)
            os.replace(m["src_path"], dest_path)
            _update_index(m, dest_path)
            success += 1
            print(f"  MOVED  {m['filename']}")
            print(f"         {m['current_genre']} → {m['expected_genre']}")
        except Exception as e:
            fail += 1
            print(f"  ERROR  {m['filename']}: {e}")

    return success, fail


def _update_index(move_record: dict, new_path: str):
    """Update MongoDB library_index with the new file path. Silently skips on failure."""
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        col.update_one(
            {"final_path": move_record["src_path"]},
            {"$set": {
                "final_path":    new_path,
                "genre_folder":  move_record["expected_genre"],
            }},
        )
    except Exception:
        pass  # DB not available — index will resync on next app start


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DJ Library Audit — re-route misplaced tracks")
    parser.add_argument("--execute",        action="store_true",
                        help="Actually move files (default: dry-run)")
    parser.add_argument("--fix-tags",       action="store_true",
                        help="Write corrected ID3 artist tags for known-corrupt files")
    parser.add_argument("--library",        default=DEFAULT_LIBRARY_ROOT,
                        help=f"DJ music root directory (default: {DEFAULT_LIBRARY_ROOT})")
    parser.add_argument("--min-confidence", type=float, default=MIN_CONFIDENCE,
                        help=f"Minimum routing confidence to act on (default: {MIN_CONFIDENCE})")
    parser.add_argument("--genre",          default=None,
                        help="Audit a single genre folder only (e.g. Bollywood)")
    args = parser.parse_args()

    dry_run = not args.execute
    lib_root = args.library

    print("=" * 70)
    print("DJ LIBRARY AUDIT")
    print(f"  Library:        {lib_root}")
    print(f"  Mode:           {'DRY-RUN (no files moved)' if dry_run else 'EXECUTE'}")
    print(f"  Fix tags:       {'yes' if args.fix_tags else 'no'}")
    print(f"  Min confidence: {args.min_confidence:.0%}")
    if args.genre:
        print(f"  Genre filter:   {args.genre}")
    print("=" * 70)

    # ── Phase 1: Fix known-corrupt ID3 tags ──────────────────────────────────
    if args.fix_tags:
        print("\n── Phase 1: ID3 tag corrections ─────────────────────────────────────")
        corrections = fix_known_tags(
            os.path.join(lib_root, LIBRARY_SUBDIR),
            dry_run=dry_run,
        )
        if not corrections:
            print("  No tag corrections needed.")
        else:
            for c in corrections:
                status = ("would fix" if dry_run else ("FIXED" if c.get("applied") else "FAILED"))
                print(f"  {status:8s}  {c['filename']}")
                print(f"             artist: {c['old_artist']!r} → {c['new_artist']!r}")
        print()

    # ── Phase 2: Build move manifest ─────────────────────────────────────────
    print("── Phase 2: Scanning library ────────────────────────────────────────")
    moves = build_move_manifest(
        lib_root,
        min_confidence=args.min_confidence,
        genre_filter=args.genre,
    )

    if not moves:
        print("  No misplaced files found. Library looks clean.")
        return

    print(f"\n  Found {len(moves)} misplaced file(s):\n")
    col_w = max(len(m["filename"]) for m in moves)
    print(f"  {'FILE':{col_w}}  {'FROM':<14}  {'TO':<14}  CONF  SOURCE")
    print(f"  {'-'*col_w}  {'-'*14}  {'-'*14}  ----  ------")
    for m in moves:
        print(
            f"  {m['filename']:{col_w}}  "
            f"{m['current_genre']:<14}  "
            f"{m['expected_genre']:<14}  "
            f"{m['confidence']:.0%}  "
            f"{m['source']}"
        )

    # ── Phase 3: Execute moves ────────────────────────────────────────────────
    print()
    if dry_run:
        print("DRY-RUN complete. Re-run with --execute to apply the moves above.")
    else:
        print(f"── Phase 3: Moving {len(moves)} file(s) ─────────────────────────────────")
        ok, fail = execute_moves(moves, lib_root)
        print(f"\n  Done — {ok} moved, {fail} failed.")


if __name__ == "__main__":
    main()
