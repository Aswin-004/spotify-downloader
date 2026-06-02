"""
backfill_tcon.py
================
Writes the TCON (genre) ID3 tag to every Library file that's missing it,
using the folder name as the authoritative genre source.

Without TCON, DJ software (Rekordbox, Serato, djay) can't filter by genre.

Run:
    python backfill_tcon.py --dry-run   (show what would change)
    python backfill_tcon.py             (execute)
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import os
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("REDIRECT_URI", "http://127.0.0.1:8888/callback")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")

from config import config
from mutagen.id3 import ID3, TCON, ID3NoHeaderError

BASE = Path(config.BASE_DOWNLOAD_DIR)
LIB  = BASE / "Library"
DRY  = "--dry-run" in sys.argv

# Map folder name → TCON value written to ID3 tag
# Uses standard genre strings recognised by Rekordbox/Serato
FOLDER_TO_TCON: dict[str, str] = {
    "Bollywood":   "Bollywood",
    "Drum & Bass": "Drum and Bass",
    "Dubstep":     "Dubstep",
    "Electronic":  "Electronic",
    "Grime":       "Grime",
    "Hip Hop":     "Hip Hop",
    "House":       "House",
    "Latin":       "Latin",
    "Pop":         "Pop",
    "Punjabi":     "Punjabi",
    "R&B":         "R&B",
    "Tamil":       "Tamil",
    "Techno":      "Techno",
    "Trance":      "Trance",
    "UK Garage":   "UK Garage",
}


def main() -> None:
    print(f"Writing TCON genre tags to Library/  DRY_RUN={DRY}\n")
    written = skipped = already = 0

    for folder in sorted(LIB.iterdir()):
        if not folder.is_dir():
            continue
        genre_tag = FOLDER_TO_TCON.get(folder.name)
        if not genre_tag:
            print(f"  [skip folder] {folder.name} — no TCON mapping")
            continue

        files = sorted(folder.glob("*.mp3"))
        folder_written = 0
        for f in files:
            try:
                tags = ID3(str(f))
                existing = str(tags.get("TCON", "")).strip()
                if existing:
                    already += 1
                    continue
                print(f"  {folder.name}/{f.name[:55]}")
                if not DRY:
                    tags["TCON"] = TCON(encoding=3, text=[genre_tag])
                    tags.save(str(f))
                written += 1
                folder_written += 1
            except ID3NoHeaderError:
                skipped += 1
            except Exception as e:
                print(f"    [warn] {f.name}: {e}")
                skipped += 1

        if folder_written:
            print(f"  → {folder.name}: wrote {folder_written} TCON tags")

    print(f"\nDone: written={written}  already_had={already}  skipped={skipped}")
    if DRY:
        print("(DRY RUN — nothing written)")


if __name__ == "__main__":
    main()
