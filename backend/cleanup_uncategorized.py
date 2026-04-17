"""
Cleanup Uncategorized — Bulk artist folder reorganization
==========================================================
Scans Ingest/Uncategorized/ and moves artist folders to appropriate 
genre buckets based on ID3 genres or a manual fallback map.

Usage:
  python cleanup_uncategorized.py          # Dry-run (default)
  python cleanup_uncategorized.py --dry-run
  python cleanup_uncategorized.py --execute

Dry-run mode shows what WOULD be moved without making changes.
--execute mode performs actual file operations.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

load_dotenv()

BASE_DOWNLOAD_DIR = os.getenv("BASE_DOWNLOAD_DIR", "/music")
UNCATEGORIZED_DIR = Path(BASE_DOWNLOAD_DIR) / "Ingest" / "Uncategorized"
INGEST_BASE = Path(BASE_DOWNLOAD_DIR) / "Ingest"

# Manual fallback genre map for untagged artists
MANUAL_GENRE_MAP = {
    # Trance / Psytrance
    "avan7":                "Trance",
    "blastoyz":             "Trance",
    "vini vici":            "Trance",
    "astrix":               "Trance",
    "talamasca":            "Trance",
    "massivebass":          "Trance",
    "crazy box":            "Trance",
    "becker":               "Trance",
    "bl4ck hole":           "Trance",
    "burn in noise":        "Trance",
    "dang3r":               "Trance",
    "pandora":              "Trance",
    "rising dust":          "Trance",
    "wizthemc":             "Trance",   # add this
    "auguste":              "Trance",   # add this
    "sajanka":              "Trance",
    "wrecked machines":     "Trance",
    "witchislav":           "Trance",
    "unstable":             "Trance",
    "phantom br":           "Trance",
    "harmonika":            "Trance",
    "quartzo":              "Trance",
    "capital monkey":       "Trance",
    "absolem":              "Trance",
    "serve cold":           "Trance",
    "special m":            "Trance",
    "synthatic":            "Trance",
    "2fox":                 "Trance",
    "3form":                "Trance",
    "audiobass":            "Trance",
    "bindi project":        "Trance",
    "black 21":             "Trance",
    "chemical noise":       "Trance",
    "cr3wfx":               "Trance",
    "djapatox":             "Trance",
    "from space":           "Trance",
    "jacob":                "Trance",
    "konaefiz":             "Trance",
    "kore-g":               "Trance",
    "mantraman (uk)":       "Trance",
    "memento mori":         "Trance",
    "moonshine":            "Trance",
    "remind":               "Trance",
    "rivo":                 "Trance",
    "rush avenue":          "Trance",
    "s3n0":                 "Trance",
    "theff":                "Trance",
    "vanco":                "Trance",
    "vermont (br)":         "Trance",
    "wisllow":              "Trance",
    "zanon":                "Trance",

    # Afro House / Melodic House
    "adam port":            "Afro House",
    "moblack":              "Afro House",
    "nitefreak":            "Afro House",
    "rampa":                "Afro House",
    "themba":               "Afro House",
    "reznik":               "Afro House",
    "super flu":            "Afro House",
    "anyma":                "Afro House",
    "marten lou":           "Afro House",
    "gianni romano":        "Afro House",
    "calussa":              "Afro House",
    "joezi":                "Afro House",
    "ahmed spins":          "Afro House",
    "kiko franco":          "Afro House",
    "salif keita":          "Afro House",
    "naomi sharon":         "Afro House",
    "nico de andrea":       "Afro House",
    "nico morano":          "Afro House",
    "tai woffinden":        "Afro House",
    "luedji luna":          "Afro House",
    "massuma":              "Afro House",
    "maxi meraki":          "Afro House",
    "maz":                  "Afro House",
    "pupa nas t":           "Afro House",
    "rose caviar":          "Afro House",
    "alice dimar":          "Afro House",
    "amani amara":          "Afro House",
    "babalos":              "Afro House",
    "bayé":                 "Afro House",
    "benji":                "Afro House",
    "bensy":                "Afro House",
    "bhaskar":              "Afro House",
    "deco (be)":            "Afro House",
    "durs":                 "Afro House",
    "eden shalev":          "Afro House",
    "feva":                 "Afro House",
    "feva#":                "Afro House",
    "gordo":                "Afro House",
    "grace (de)":           "Afro House",
    "grigoré":              "Afro House",
    "hydawai":              "Afro House",
    "lazare":               "Afro House",
    "louis bongo":          "Afro House",
    "lukas & frank":        "Afro House",
    "major7":               "Afro House",
    "mercuriall":           "Afro House",
    "merzzy":               "Afro House",
    "paul hadi":            "Afro House",
    "samm (be)":            "Afro House",
    "skore (br)":           "Afro House",
    "thales dumbra":        "Afro House",
    "thierry von der warth":"Afro House",
    "tiwoan & lance":       "Afro House",
    "vegas (brazil)":       "Afro House",
    "wayofdk":              "Afro House",
    "wizthemc":             "Afro House",
    "ale vaz":              "Afro House",
    "ankhoï":               "Afro House",
    "arun":                 "Afro House",
    "aspx":                 "Afro House",
    "adassiya":             "Afro House",
    "adrian fyrla":         "Afro House",
    "afro medusa":          "Afro House",
    "alex wann":            "Afro House",
    "Auguste":              "Afro House",
    "backhaze":             "Afro House",
    "beek":                 "Afro House",
    "benedix":              "Afro House",
    "conki":                "Afro House",
    "dany comaro":          "Afro House",
    "distinctside":         "Afro House",
    "dj lewis":             "Afro House",
    "dzp":                  "Afro House",
    "emotional":            "Afro House",
    "eriice":               "Afro House",
    "jerry ropero":         "Afro House",
    "ksbr":                 "Afro House",
    "merzzy":               "Afro House",
    "ravi":                 "Afro House",
    "s3n0":                 "Afro House",
    "tayna":                "Afro House",

    # House
    "dennis ferrer":        "House",
    "disclosure":           "House",
    "hannah wants":         "House",
    "honeyluv":             "House",
    "hugel":                "House",
    "zerb":                 "House",
    "fireboy dml":          "House",

    # Afrobeats
    "fireboy dml":          "Afrobeats",
    # ── Bollywood ─────────────────────────────────────────────────
    "a.r. rahman":           "Bollywood",
    "aastha gill":           "Bollywood",
    "abhijeet srivastava":   "Bollywood",
    "ajay-atul":             "Bollywood",
    "amaal mallik":          "Bollywood",
    "amit trivedi":          "Bollywood",
    "anirudh ravichander":   "Bollywood",
    "ankit tiwari":          "Bollywood",
    "arijit singh":          "Bollywood",
    "armaan malik":          "Bollywood",
    "asees kaur":            "Bollywood",
    "atif aslam":            "Bollywood",
    "badshah":               "Bollywood",
    "benny dayal":           "Bollywood",
    "bhoomi trivedi":        "Bollywood",
    "dj chetas":             "Bollywood",
    "farhan akhtar":         "Bollywood",
    "himesh reshammiya":     "Bollywood",
    "holy goof":             "Bollywood",
    "jeet gannguli":         "Bollywood",
    "kamaal khan":           "Bollywood",
    "kk":                    "Bollywood",
    "labh janjua":           "Bollywood",
    "mamta sharma":          "Bollywood",
    "meet bros anjjan":      "Bollywood",
    "mika singh":            "Bollywood",
    "mohit chauhan":         "Bollywood",
    "neha kakkar":           "Bollywood",
    "nucleya":               "Bollywood",
    "palash muchhal":        "Bollywood",
    "pritam":                "Bollywood",
    "rajat nagpal":          "Bollywood",
    "rochak kohli":          "Bollywood",
    "roop kumar rathod":     "Bollywood",
    "roop kumar rathod":     "Bollywood",   
    "sachet tandon":         "Bollywood",
    "sachin-jigar":          "Bollywood",
    "saleem shahzada":       "Bollywood",
    "salim-sulaiman":        "Bollywood",
    "salim–sulaiman":        "Bollywood",
    "shaarib toshi":         "Bollywood",
    "shankar mahadevan":     "Bollywood",
    "shankar-ehsaan-loy":    "Bollywood",
    "shefali alvares":       "Bollywood",
    "sohail sen":            "Bollywood",
    "sona mohapatra":        "Bollywood",
    "sonu nigam":            "Bollywood",
    "sukhwinder singh":      "Bollywood",
    "tony kakkar":           "Bollywood",
    "vishal mishra":         "Bollywood",
    "vishal-shekhar":        "Bollywood",
    "wajid":                 "Bollywood",
    "yashita sharma":        "Bollywood",
    "yashraj":               "Bollywood",
    "zack Knight":           "Bollywood",
    "jagjit singh":          "Bollywood",
    "tarun sagar":           "Bollywood",
    "ved sharma":            "Bollywood",
    "sabat batin":           "Bollywood",
    "omer inayat":           "Bollywood",
    "dox":                   "Bollywood",
    # ── Punjabi ───────────────────────────────────────────────────
    "akhil":                 "Punjabi",
    "diljit dosanjh":        "Punjabi",
    "fazilpuria":            "Punjabi",
    "garry sandhu":          "Punjabi",
    "guru randhawa":         "Punjabi",
    "ikka":                  "Punjabi",
    "imran khan":            "Punjabi",
    "karan aujla":           "Punjabi",
    "king":                  "Punjabi",
    "navv inder":            "Punjabi",
    "pav dharia":            "Punjabi",
    "prabh singh":           "Punjabi",
    "rdb":                   "Punjabi",
    "shubh":                 "Punjabi",
    "sukha":                 "Punjabi",
    "sukhbir":               "Punjabi",
    "sunanda sharma":        "Punjabi",
    "talwiinder":            "Punjabi",
    "the prophec":           "Punjabi",
    "yo yo honey singh":     "Punjabi",
    "zack knight":           "Punjabi",
}


def _get_id3_genre(mp3_path: Path) -> str:
    """Try to extract genre from ID3 TPE1 tag."""
    try:
        from mutagen.id3 import ID3
        audio = ID3(str(mp3_path))
        # TPE1 = lead performer/artist (not genre actually)
        # TCON = genre
        if "TCON" in audio:
            genre = audio["TCON"].text
            if genre and len(genre) > 0:
                return genre[0]
    except Exception:
        pass
    return None


def _find_genre_for_artist(artist_folder: Path) -> str:
    """
    Find genre for an artist folder:
    1. Check ID3 tags in any .mp3 file inside it
    2. Fall back to MANUAL_GENRE_MAP for the artist name (case-insensitive)
    3. Return None if not found.
    """
    # Try to read ID3 genre from any mp3
    for mp3 in artist_folder.glob("*.mp3"):
        genre = _get_id3_genre(mp3)
        if genre:
            return genre

    # Try manual map (case-insensitive)
    artist_name_lower = artist_folder.name.lower()
    if artist_name_lower in MANUAL_GENRE_MAP:
        return MANUAL_GENRE_MAP[artist_name_lower]

    return None


def cleanup(dry_run: bool = True):
    """Scan Uncategorized and move artist folders to genre buckets."""
    if not UNCATEGORIZED_DIR.exists():
        print(f"[INFO] {UNCATEGORIZED_DIR} does not exist — nothing to clean.")
        return

    artist_folders = [d for d in UNCATEGORIZED_DIR.iterdir() if d.is_dir()]
    if not artist_folders:
        print(f"[INFO] {UNCATEGORIZED_DIR} is empty — nothing to clean.")
        return

    moved = 0
    skipped = 0

    for artist_folder in sorted(artist_folders):
        artist_name = artist_folder.name
        genre = _find_genre_for_artist(artist_folder)

        if genre:
            src = artist_folder
            dst_dir = INGEST_BASE / genre
            mode_label = "[DRY RUN]" if dry_run else "[MOVED]"
            
            # Collect all .mp3 files to move
            mp3_files = list(artist_folder.glob("*.mp3"))
            if mp3_files:
                print(
                    f"{mode_label} {src.relative_to(BASE_DOWNLOAD_DIR)} → "
                    f"{dst_dir.relative_to(BASE_DOWNLOAD_DIR)} (genre: {genre}, {len(mp3_files)} files)"
                )
                if not dry_run:
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    for mp3_file in mp3_files:
                        dest_file = dst_dir / mp3_file.name
                        # Handle file collisions
                        if dest_file.exists():
                            stem = mp3_file.stem
                            n = 1
                            while (dst_dir / f"{stem}_{n}.mp3").exists():
                                n += 1
                            dest_file = dst_dir / f"{stem}_{n}.mp3"
                        shutil.move(str(mp3_file), str(dest_file))
                    # Delete empty artist folder if all files were moved
                    if not any(artist_folder.iterdir()):
                        artist_folder.rmdir()
                    moved += 1
                else:
                    moved += 1
            else:
                print(f"[SKIPPED] {artist_folder.relative_to(INGEST_BASE)} — no .mp3 files")
                skipped += 1
        else:
            print(f"[SKIPPED] {artist_folder.relative_to(INGEST_BASE)} — not in manual map")
            skipped += 1

    print(f"\n{'─' * 70}")
    if dry_run:
        print(f"DRY RUN COMPLETE")
        print(f"  Would move: {moved}")
        print(f"  Would skip: {skipped}")
        print(f"\nRe-run with --execute to apply changes.")
    else:
        print(f"CLEANUP COMPLETE")
        print(f"  Moved: {moved}")
        print(f"  Skipped: {skipped}")

    # Check if Uncategorized is now empty
    remaining = list(UNCATEGORIZED_DIR.iterdir())
    if not remaining:
        print(f"✅ {UNCATEGORIZED_DIR} is now empty (not deleting folder).")
    else:
        print(f"⚠️  {len(remaining)} folders remain in {UNCATEGORIZED_DIR}")


def main():
    parser = argparse.ArgumentParser(
        description="Reorganize Uncategorized artist folders into genre buckets."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be moved (default behavior)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the moves (disables dry-run)",
    )

    args = parser.parse_args()

    if args.execute:
        dry_run = False
        print("🔴 EXECUTE MODE — Making actual changes")
    else:
        dry_run = True
        print("🟢 DRY RUN MODE — No changes will be made")

    print(f"\nScanning: {UNCATEGORIZED_DIR}\n")
    cleanup(dry_run=dry_run)


if __name__ == "__main__":
    main()
