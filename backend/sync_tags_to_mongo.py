"""
sync_tags_to_mongo.py
=====================
After using Mp3tag (or any external tagger) to write TXXX:SPOTIFY_ID to
Library files, run this to sync the tag back into the MongoDB library_index.

What it does:
  - Reads TXXX:SPOTIFY_ID from each Library/*.mp3 ID3 tag
  - Finds the matching library_index document by filename (stable key)
  - Updates ONLY spotify_id + last_seen — nothing else is touched
  - identity_key, content_hash, final_path, genre_folder are all left alone

What it does NOT do:
  - Create new documents (uses update_one, not upsert)
  - Change identity_key (would violate the unique index)
  - Touch audio_features, fingerprints, or any other field

Run:
    python sync_tags_to_mongo.py --dry-run          (preview — nothing written)
    python sync_tags_to_mongo.py                    (execute all Library folders)
    python sync_tags_to_mongo.py --folder Bollywood (single folder only)
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
import os
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("REDIRECT_URI", "http://127.0.0.1:8888/callback")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")

from config import config
from database import get_library_index_collection
from mutagen.id3 import ID3, ID3NoHeaderError

BASE = Path(config.BASE_DOWNLOAD_DIR)
LIB  = BASE / "Library"
DRY  = "--dry-run" in sys.argv

# Optional single-folder filter
FOLDER: str | None = None
_args = sys.argv[1:]
if "--folder" in _args:
    _i = _args.index("--folder")
    if _i + 1 < len(_args):
        FOLDER = _args[_i + 1]


def _read_spotify_id(path: Path) -> str:
    try:
        tags  = ID3(str(path))
        frame = tags.get("TXXX:SPOTIFY_ID")
        return str(frame.text[0]).strip() if frame and frame.text else ""
    except (ID3NoHeaderError, Exception):
        return ""


def main() -> None:
    col  = get_library_index_collection()
    scan = LIB / FOLDER if FOLDER else LIB

    print(f"Syncing TXXX:SPOTIFY_ID → MongoDB  DRY_RUN={DRY}")
    print(f"Scanning: {scan.relative_to(BASE)}\n")

    updated = already_ok = no_tag = no_doc = 0

    for f in sorted(scan.rglob("*.mp3")):
        new_sid = _read_spotify_id(f)

        if not new_sid:
            no_tag += 1
            continue

        doc = col.find_one({"filename": f.name}, {"_id": 1, "spotify_id": 1})
        if not doc:
            no_doc += 1
            continue

        old_sid = doc.get("spotify_id", "")
        if old_sid == new_sid:
            already_ok += 1
            continue

        print(f"  {f.name}")
        print(f"    {old_sid!r} → {new_sid!r}")

        if not DRY:
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "spotify_id": new_sid,
                    "last_seen":  datetime.now(timezone.utc),
                }}
            )
        updated += 1

    print(f"\nResults:")
    print(f"  Updated in MongoDB : {updated}")
    print(f"  Already correct    : {already_ok}")
    print(f"  No ID3 tag (skip)  : {no_tag}")
    print(f"  Not in DB (skip)   : {no_doc}")
    if DRY:
        print("\n(DRY RUN — nothing was written)")


if __name__ == "__main__":
    main()
