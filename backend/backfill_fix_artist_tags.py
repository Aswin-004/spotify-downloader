"""
backfill_fix_artist_tags.py
===========================
Finds Library files where the artist (TPE1) tag contains a genre name
instead of a real artist (e.g. "Bollywood", "Indian", "Electronic") and
fixes them by searching Spotify for the track title to get the real artist.

Also fixes title/artist swap errors (where the song title ended up in TPE1).

Run:
    python backfill_fix_artist_tags.py --dry-run   (preview)
    python backfill_fix_artist_tags.py             (execute)
    python backfill_fix_artist_tags.py --folder Bollywood  (one folder)
"""
from __future__ import annotations
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import os
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("REDIRECT_URI", "http://127.0.0.1:8888/callback")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")

from config import config
from mutagen.id3 import ID3, TPE1, TIT2, ID3NoHeaderError

BASE = Path(config.BASE_DOWNLOAD_DIR) / "Library"
DRY  = "--dry-run" in sys.argv

FOLDER = None
_args  = sys.argv[1:]
if "--folder" in _args:
    _i = _args.index("--folder")
    if _i + 1 < len(_args):
        FOLDER = _args[_i + 1]

# Artist values that are clearly genre names, not real artists
FAKE_ARTISTS = {"bollywood", "indian", "electronic", "punjabi", "latin",
                "hip hop", "r&b", "house", "trance", "techno", "pop",
                "grime", "dubstep", "uk garage", "drum and bass", "tamil"}


def _search_spotify(sp, title: str) -> tuple[str, str]:
    """Return (artist, correct_title) from Spotify or ('', '')."""
    try:
        clean = title.split(" - ")[0].strip()
        # Strip " (From...)" suffix
        import re
        clean = re.sub(r'\s*[\(\[]From[^\)\]]*[\)\]]', '', clean, flags=re.IGNORECASE).strip()
        results = sp.search(q=f"track:{clean}", type="track", limit=1)
        items   = results.get("tracks", {}).get("items", [])
        if items:
            track  = items[0]
            artist = track.get("artists", [{}])[0].get("name", "")
            title  = track.get("name", "")
            return artist, title
    except Exception as e:
        print(f"    Spotify error: {e}")
    return "", ""


def main() -> None:
    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service().sp
    except Exception as e:
        print(f"Spotify unavailable: {e}")
        return

    scan = BASE / FOLDER if FOLDER else BASE
    print(f"Scanning: {scan.relative_to(BASE.parent)}  DRY_RUN={DRY}\n")

    fixed = skipped = no_match = 0

    for mp3 in sorted(scan.rglob("*.mp3")):
        try:
            tags   = ID3(str(mp3))
            artist = str(tags.get("TPE1", "")).strip()
            title  = str(tags.get("TIT2", mp3.stem)).strip()
        except (ID3NoHeaderError, Exception):
            continue

        artist_lower = artist.lower()

        # Case 1: artist is a genre name — search Spotify by title for real artist
        if artist_lower in FAKE_ARTISTS:
            print(f"  [FAKE_ARTIST] {mp3.name}")
            print(f"    artist={artist!r}  title={title!r}")
            if not DRY:
                new_artist, new_title = _search_spotify(sp, title)
                if new_artist:
                    tags["TPE1"] = TPE1(encoding=3, text=[new_artist])
                    if new_title:
                        tags["TIT2"] = TIT2(encoding=3, text=[new_title])
                    tags.save(str(mp3))
                    print(f"    ✓ fixed → artist={new_artist!r}")
                    fixed += 1
                else:
                    print(f"    ✗ no Spotify match — skipped")
                    no_match += 1
                time.sleep(1.0)
            else:
                skipped += 1
            continue

        # Case 2: title looks like a song title stored in artist field
        # Heuristic: artist field contains spaces + brackets typical of song titles
        # e.g. "Piya Tu Ab To Aaja", "Ride It (Kya Yehi Pyaar Hai)"
        if (len(artist.split()) >= 4 and
                ("(" in artist or "[" in artist or " - " in artist) and
                title.lower() in ("remix", "hindi version", "radio edit", "")):
            print(f"  [SWAPPED_TAGS] {mp3.name}")
            print(f"    artist={artist!r}  title={title!r}")
            if not DRY:
                # The artist field is actually the title — search Spotify for real artist
                new_artist, new_title = _search_spotify(sp, artist)
                if new_artist:
                    tags["TIT2"] = TIT2(encoding=3, text=[new_title or artist])
                    tags["TPE1"] = TPE1(encoding=3, text=[new_artist])
                    tags.save(str(mp3))
                    print(f"    ✓ fixed → artist={new_artist!r} title={new_title or artist!r}")
                    fixed += 1
                else:
                    # At minimum fix title to be the artist field value, clear bad artist
                    tags["TIT2"] = TIT2(encoding=3, text=[artist])
                    tags["TPE1"] = TPE1(encoding=3, text=[""])
                    tags.save(str(mp3))
                    print(f"    ~ partial fix — title set, artist cleared (no Spotify match)")
                    fixed += 1
                time.sleep(1.0)
            else:
                skipped += 1

    print(f"\nDone: fixed={fixed}  no_match={no_match}  skipped_dry={skipped}")


if __name__ == "__main__":
    main()
