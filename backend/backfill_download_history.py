"""
backfill_download_history.py
============================
Creates minimal download_history entries for library_index files that have
no matching record — typically manually-placed files or tracks downloaded
before history tracking was implemented.

Each synthetic entry marks the track as present in the library so:
  - Reconciliation no longer flags them as "missing history"
  - Duplicate detection won't re-download them if the Spotify URL is pasted

Run:
    python backfill_download_history.py --dry-run   (show what would change)
    python backfill_download_history.py             (execute)
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from database import get_library_index_collection, get_download_history_collection

DRY = "--dry-run" in sys.argv


def _now() -> datetime:
    return datetime.now(timezone.utc)


def main():
    print(f"Building download_history backfill…  DRY_RUN={DRY}")

    index_col   = get_library_index_collection()
    history_col = get_download_history_collection()

    # Build set of filenames already in download_history
    existing = {
        doc["filename"]
        for doc in history_col.find({}, {"filename": 1})
        if doc.get("filename")
    }
    print(f"  download_history has {len(existing)} entries")

    # Find library_index docs with no history match
    index_docs = list(index_col.find({"missing": {"$ne": True}}))
    print(f"  library_index has {len(index_docs)} active entries")

    to_add = [d for d in index_docs if d.get("filename") and d["filename"] not in existing]
    print(f"  {len(to_add)} files need a synthetic history entry\n")

    added = 0
    for doc in to_add:
        filename    = doc.get("filename", "")
        final_path  = doc.get("final_path", "")
        genre_folder= doc.get("genre_folder", "")
        spotify_id  = doc.get("spotify_id", "")

        # Derive title / artist from filename (best effort)
        stem = Path(filename).stem
        if " - " in stem:
            parts  = stem.rsplit(" - ", 1)
            title  = parts[0].strip()
            artist = parts[1].strip()
        else:
            title  = stem
            artist = ""

        print(f"  [ADD] {filename}")
        if not DRY:
            history_col.insert_one({
                "filename":      filename,
                "final_path":    final_path,
                "genre_folder":  genre_folder,
                "spotify_id":    spotify_id,
                "track_title":   title,
                "artist":        artist,
                "source":        "backfill",
                "backfilled_at": _now(),
                "timestamp":     _now(),
                # Mark clearly as synthetic so analytics can filter if needed
                "synthetic":     True,
            })
        added += 1

    print(f"\nTotal: {added} entries {'would be ' if DRY else ''}added")
    if not DRY:
        print(f"download_history now has {history_col.count_documents({})} entries")


if __name__ == "__main__":
    main()
