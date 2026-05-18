"""
Library Index Repair
====================
Fixes the 3 root causes after the reorganisation:

  1. STALE PATHS   — MongoDB final_path no longer exists on disk.
                     Match by filename → update path in-place.
  2. ORPHANED FILES — On disk but no index entry (manually placed files).
                     Create a minimal index entry (no Spotify data).
  3. GHOST ENTRIES  — Still stale after filename matching (file renamed/gone).
                     Soft-delete (mark missing=True) so they don't pollute counts.

Run:
    python repair_index.py --dry-run   (show what would change)
    python repair_index.py             (execute)
"""
from __future__ import annotations
import sys, re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from config import config
from database import get_library_index_collection

BASE   = Path(config.BASE_DOWNLOAD_DIR)
DRY    = "--dry-run" in sys.argv
col    = get_library_index_collection()

SKIP_PARTS = {"Ingest", "Manual"}


# ── build disk index: filename → Path ────────────────────────────────────────
def _build_disk_index() -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for f in BASE.rglob("*.mp3"):
        if any(p in f.parts for p in SKIP_PARTS):
            continue
        idx.setdefault(f.name, []).append(f)
    return idx


# ── helpers ───────────────────────────────────────────────────────────────────
def _read_spotify_id(path: Path) -> str:
    try:
        from mutagen.id3 import ID3
        tags = ID3(str(path))
        tx = tags.get("TXXX:SPOTIFY_ID")
        return str(tx.text[0]).strip() if (tx and tx.text) else ""
    except Exception:
        return ""

def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── STEP 1: fix stale paths ───────────────────────────────────────────────────
def fix_stale(disk: dict[str, list[Path]]) -> tuple[int, int, int]:
    updated = skipped = ghost = 0

    for doc in col.find({}):
        fp_str = doc.get("final_path", "")
        if not fp_str:
            continue
        fp = Path(fp_str)
        if fp.exists():
            skipped += 1
            continue

        # stale — try to find current location by filename
        candidates = disk.get(fp.name, [])
        if len(candidates) == 1:
            new_path = candidates[0]
            print(f"  [PATH] {fp.name}")
            print(f"         OLD: {fp_str}")
            print(f"         NEW: {new_path}")
            if not DRY:
                col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "final_path": str(new_path),
                        "last_seen": _now(),
                    }}
                )
            updated += 1
        elif len(candidates) > 1:
            # ambiguous — pick the one whose folder matches genre_folder hint
            gf = doc.get("genre_folder", "").replace("\\", "/")
            best = next(
                (c for c in candidates if gf.split("/")[-1].lower() in str(c).lower()),
                candidates[0]
            )
            print(f"  [PATH-AMBIG] {fp.name} → picked {best.relative_to(BASE)}")
            if not DRY:
                col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"final_path": str(best), "last_seen": _now()}}
                )
            updated += 1
        else:
            # file not found anywhere on disk
            print(f"  [GHOST] {fp.name} — not found on disk, marking missing")
            if not DRY:
                col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"missing": True, "last_seen": _now()}}
                )
            ghost += 1

    return updated, skipped, ghost


# ── STEP 2: index orphaned files ──────────────────────────────────────────────
def index_orphans(disk: dict[str, list[Path]]) -> int:
    # build set of already-indexed paths
    indexed_paths = {
        doc["final_path"]
        for doc in col.find({"final_path": {"$exists": True}}, {"final_path": 1})
        if doc.get("final_path")
    }

    added = 0
    for paths in disk.values():
        for path in paths:
            if str(path) in indexed_paths:
                continue
            # truly orphaned
            sp_id = _read_spotify_id(path)
            identity = f"sp:{sp_id}" if sp_id else f"file:{path.stem}"
            # skip if identity already exists
            if col.find_one({"identity_key": identity}):
                continue

            genre_folder = str(path.parent.relative_to(BASE))
            print(f"  [ADD] {path.relative_to(BASE)}")
            if not DRY:
                col.insert_one({
                    "identity_key":   identity,
                    "spotify_id":     sp_id,
                    "filename":       path.name,
                    "final_path":     str(path),
                    "genre_folder":   genre_folder,
                    "indexed_at":     _now(),
                    "last_seen":      _now(),
                    "missing":        False,
                    "audio_features": {},
                })
            added += 1

    return added


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"Building disk index…  DRY_RUN={DRY}")
    disk = _build_disk_index()
    total_disk = sum(len(v) for v in disk.values())
    print(f"  {total_disk} MP3s on disk ({len(disk)} unique filenames)")
    print(f"  {col.count_documents({})} MongoDB entries\n")

    print("=== STEP 1: Fix stale paths ===")
    updated, skipped, ghost = fix_stale(disk)
    print(f"\n  updated={updated}  already_ok={skipped}  ghost={ghost}")

    print("\n=== STEP 2: Index orphaned files ===")
    added = index_orphans(disk)
    print(f"\n  added={added}")

    print(f"\n{'─'*60}")
    print(f"Total:  paths_fixed={updated}  orphans_indexed={added}  ghosts_marked={ghost}")
    if DRY:
        print("(DRY RUN — nothing written)")
    else:
        final_count = col.count_documents({})
        stale_remaining = sum(
            1 for doc in col.find({"missing": {"$ne": True}})
            if doc.get("final_path") and not Path(doc["final_path"]).exists()
        )
        print(f"Index now has {final_count} entries, {stale_remaining} still stale")


if __name__ == "__main__":
    main()
