"""
master_organise.py — One-shot DJ library reorganiser.

Steps (all idempotent):
  1. Classify Library/Indian/ → Library/Bollywood/ or Library/Punjabi/
  2. Clean up tiny folders (Bass, Dance, Dubstep, Folk, Grime, House, Rock)
  3. Detect and move duplicates → NeedsReview/Duplicates/
  4. Update MongoDB library_index for every moved file
  5. Remove empty folders

Run:
    python master_organise.py --dry-run   (preview — nothing written)
    python master_organise.py             (execute)
"""
from __future__ import annotations
import re, shutil, sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import config

BASE  = Path(config.BASE_DOWNLOAD_DIR)
LIB   = BASE / "Library"
NR    = BASE / "NeedsReview"
DRY   = "--dry-run" in sys.argv

moved = skipped = errors = 0


# ── helpers ───────────────────────────────────────────────────────────────────

def _collision_safe(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem, suf = Path(name).stem, Path(name).suffix
    i = 1
    while True:
        c = dest_dir / f"{stem}_{i}{suf}"
        if not c.exists():
            return c
        i += 1


def mv(src: Path, dest_dir: Path, note: str = "") -> None:
    global moved, skipped, errors
    if not src.is_file():
        errors += 1
        return
    if src.parent.resolve() == dest_dir.resolve():
        skipped += 1
        return
    dest = _collision_safe(dest_dir, src.name)
    tag  = "WOULD" if DRY else "MOVE"
    try:
        rel_src  = src.relative_to(BASE)
        rel_dest = dest_dir.relative_to(BASE)
    except ValueError:
        rel_src  = src
        rel_dest = dest_dir
    print(f"  [{tag}] {str(rel_src):75s} → {rel_dest}{note}")
    if not DRY:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        _update_mongo(src, dest)
    moved += 1


def _update_mongo(old: Path, new: Path) -> None:
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        col.update_one(
            {"final_path": str(old)},
            {"$set": {
                "final_path":   str(new),
                "genre_folder": str(new.parent.relative_to(BASE)),
                "last_seen":    datetime.now(timezone.utc),
            }},
        )
    except Exception as e:
        print(f"    [mongo-warn] {e}")


def rmdir_if_empty(d: Path) -> None:
    if DRY or not d.exists():
        return
    try:
        if not any(d.iterdir()):
            d.rmdir()
            print(f"  [RMDIR] {d.relative_to(BASE)}")
    except Exception:
        pass


def _read_id3(path: Path) -> dict:
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        tags = ID3(str(path))
        artist  = str(tags.get("TPE1", "")).strip()
        title   = str(tags.get("TIT2", "")).strip()
        tcon    = str(tags.get("TCON", "")).strip()
        ggemini = ""
        txxx = tags.get("TXXX:gemini_genre")
        if txxx and txxx.text:
            ggemini = str(txxx.text[0]).strip()
        return {"artist": artist, "title": title, "tcon": tcon, "gemini_genre": ggemini}
    except Exception:
        return {"artist": "", "title": "", "tcon": "", "gemini_genre": ""}


# ── BOLLYWOOD stem matching ────────────────────────────────────────────────────

BOLLYWOOD_STEMS = {
    "sheila ki jawani", "munni badnaam", "kajra re", "fevicol se",
    "chikni chameli", "halkat jawani", "dilbar", "ghagra",
    "anarkali disco chali", "beedi", "choli ke peeche", "saturday saturday",
    "ainvayi ainvayi", "mauja hi mauja", "pappu can't dance", "ooh la la",
    "chamma chamma", "roobaroo", "nashe si chadh gayi", "chaleya",
    "suit suit", "thodi si daaru", "tum ho", "ude dil befikre",
    "lat lag gayee", "desi beat", "lungi dance", "badtameez dil",
    "balam pichkari", "dhinka chika", "photocopy", "baby doll",
    "radha", "tukur tukur", "dabangg", "munna badnaam",
    "item song", "laila main laila", "jawaan", "tip tip barsa pani",
    "humma humma", "kala chashma", "tamma tamma", "didi tera devar deewana",
    "first class", "ghungroo", "besharam rang", "naatu naatu",
    "shershaah", "kesariya", "raataan lambiyan", "bijlee bijlee",
    "ik vaari aa", "ae dil hai mushkil", "dilliwali girlfriend",
    "gallan goodiyaan", "abcd", "aankh marey", "ghoomar",
    "teri mitti", "tujhse naraz nahi", "rabba", "sanam re",
    "humari adhuri kahani", "enna sona", "gerua", "bulleya",
    "jagga jasoos", "saware", "hawayein", "phir bhi tumko",
    "aaj ki raat", "swag se swagat", "tiger zinda hai",
    "aashiq surrender hua", "taaron ke sheher", "paas aa",
    "nimbooda", "dola re dola", "nagada sang dhol",
    "ghoomar rajasthani", "morni banke", "param sundari",
    "naach meri rani", "Nach meri rani",
}

_FROM_FILM_RE = re.compile(r"\(from\s+[\"']?(.+?)[\"']?\)", re.IGNORECASE)


def _classify_indian_file(path: Path) -> str:
    """Return 'Bollywood', 'Punjabi', or '' (unknown → Punjabi default)."""
    from services.genre_router import normalize_genre

    tags    = _read_id3(path)
    artist  = tags["artist"].lower().strip()
    title   = tags["title"].lower().strip()
    tcon    = tags["tcon"]
    gemini  = tags["gemini_genre"]
    fname   = path.stem.lower()

    # 1. ARTIST_GENRE_OVERRIDE
    override = config.ARTIST_GENRE_OVERRIDE.get(artist, "")
    if override:
        canonical = normalize_genre(override)
        if canonical in ("Bollywood", "Telugu"):
            return "Bollywood"
        if canonical in ("Punjabi", "Indian Hip Hop", "Desi Hip Hop"):
            return "Punjabi"
        if canonical == "Tamil":
            return "Tamil"
        # Other genres → keep as is but return Punjabi as fallback for Indian folder
        return "Punjabi"

    # 2. Gemini genre tag
    if gemini:
        canonical = normalize_genre(gemini)
        if canonical == "Bollywood":
            return "Bollywood"
        if canonical == "Punjabi":
            return "Punjabi"
        if canonical == "Tamil":
            return "Tamil"

    # 3. TCON tag
    if tcon:
        canonical = normalize_genre(tcon)
        if canonical == "Bollywood":
            return "Bollywood"
        if canonical == "Punjabi":
            return "Punjabi"
        if canonical == "Tamil":
            return "Tamil"

    # 4. Bollywood stem in filename or title
    for stem in BOLLYWOOD_STEMS:
        if stem in fname or stem in title:
            return "Bollywood"

    # 5. "(From <Film>)" pattern in title
    if _FROM_FILM_RE.search(title) or _FROM_FILM_RE.search(fname):
        return "Bollywood"

    # 6. Hindi characters in title → Bollywood
    if any("ऀ" <= ch <= "ॿ" for ch in title):
        return "Bollywood"

    return ""   # no signal → caller defaults to Punjabi


# ── STEP 1: Library/Indian/ → Bollywood / Punjabi ────────────────────────────

def step1_split_indian():
    print("\n=== STEP 1: Library/Indian/ → Bollywood / Punjabi ===")
    indian = LIB / "Indian"
    if not indian.exists():
        print("  (Library/Indian/ not found — already done)")
        return

    files    = list(indian.rglob("*.mp3"))
    counts   = {"Bollywood": 0, "Punjabi": 0, "Tamil": 0}
    unmatched = 0

    for f in sorted(files):
        genre = _classify_indian_file(f)
        if not genre:
            genre = "Punjabi"   # default — most untagged Indian tracks are Punjabi
            unmatched += 1
        if genre not in counts:
            counts[genre] = 0
        counts[genre] += 1
        mv(f, LIB / genre)

    if not DRY:
        rmdir_if_empty(indian)

    print(f"\n  Breakdown: {dict(counts)}  (unmatched defaults→Punjabi: {unmatched})")


# ── STEP 2: Tiny folder cleanup ───────────────────────────────────────────────

_NUMBERED_RE = re.compile(r"^\d{1,3}\s+-\s+")   # e.g. "17 - Track Name.mp3"

def _is_numbered_rip(path: Path) -> bool:
    return bool(_NUMBERED_RE.match(path.name))


def step2_tiny_folders():
    print("\n=== STEP 2: Tiny folder cleanup ===")

    # Folders that contain only a handful of files and need re-routing
    TINY_FOLDERS: list[tuple[str, str | None]] = [
        # (source_folder_name, default_dest_if_not_numbered_rip)
        ("Bass",    "Electronic"),
        ("Dance",   "Electronic"),
        ("Dubstep", "Dubstep"),      # non-album rips stay in Dubstep
        ("Folk",    "Bollywood"),
        ("Grime",   "Grime"),        # non-album rips stay in Grime
        ("House",   "House"),        # non-album rips stay in House
        ("Rock",    "Electronic"),
    ]

    for folder_name, dest_genre in TINY_FOLDERS:
        src_dir = LIB / folder_name
        if not src_dir.exists():
            continue
        files = list(src_dir.rglob("*.mp3"))
        if not files:
            rmdir_if_empty(src_dir)
            continue

        moved_any = False
        for f in sorted(files):
            if _is_numbered_rip(f):
                mv(f, NR / "Albums", f"  [numbered rip from {folder_name}/]")
            else:
                target = LIB / dest_genre
                if f.parent.resolve() != target.resolve():
                    mv(f, target)
                else:
                    pass  # already in right place
            moved_any = True

        if not DRY:
            rmdir_if_empty(src_dir)


# ── STEP 3: Duplicate detection ───────────────────────────────────────────────

def step3_deduplicate():
    print("\n=== STEP 3: Duplicate detection ===")

    index: dict[tuple[str, str], list[Path]] = {}
    fname_index: dict[str, list[Path]] = {}

    for mp3 in sorted(LIB.rglob("*.mp3")):
        tags   = _read_id3(mp3)
        title  = tags["title"].lower().strip()
        artist = tags["artist"].lower().strip()

        if title and artist:
            key = (title, artist)
            index.setdefault(key, []).append(mp3)

        stem = re.sub(r"\s+", " ", mp3.stem.lower().strip())
        fname_index.setdefault(stem, []).append(mp3)

    dup_dest   = NR / "Duplicates"
    dup_count  = 0

    # Tag-based exact duplicates
    for (title, artist), paths in index.items():
        if len(paths) < 2:
            continue
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        keep = paths[0]
        for dup in paths[1:]:
            print(f"  [DUP-TAG] keep={keep.name}  dup={dup.relative_to(BASE)}")
            mv(dup, dup_dest)
            dup_count += 1

    # Filename fuzzy duplicates (ratio > 0.93, same first letter, min 15 chars)
    stems = list(fname_index.keys())
    checked: set[frozenset] = set()
    for i, s1 in enumerate(stems):
        if not s1 or len(s1) < 15:
            continue
        for s2 in stems[i + 1:]:
            if not s2 or len(s2) < 15 or s1[0] != s2[0]:
                continue
            pair = frozenset([s1, s2])
            if pair in checked:
                continue
            checked.add(pair)
            if SequenceMatcher(None, s1, s2).ratio() > 0.93:
                paths1 = fname_index[s1]
                paths2 = fname_index[s2]
                all_paths = sorted(paths1 + paths2, key=lambda p: p.stat().st_mtime, reverse=True)
                keep = all_paths[0]
                for dup in all_paths[1:]:
                    if dup.exists():
                        print(f"  [DUP-FNAME] keep={keep.name}  dup={dup.relative_to(BASE)}")
                        mv(dup, dup_dest)
                        dup_count += 1

    print(f"  Duplicates found: {dup_count}")


# ── STEP 4: Sweep entire Library for numbered rips ───────────────────────────

def step4_sweep_numbered_rips():
    """
    Scan every Library/{Genre}/ folder for files that look like numbered album
    rips (e.g. "03 - Track Name.mp3") and move them to NeedsReview/Albums/.
    Runs after the main organisation so any future accidental album downloads
    get caught automatically.
    """
    print("\n=== STEP 4: Sweep Library for numbered rips ===")
    found = 0
    for mp3 in sorted(LIB.rglob("*.mp3")):
        if _is_numbered_rip(mp3):
            mv(mp3, NR / "Albums", f"  [numbered rip in {mp3.parent.name}/]")
            found += 1
    if found == 0:
        print("  (none found)")


# ── STEP 5: Resolve stray NeedsReview/{artist}/ folders ──────────────────────

def step5_resolve_needsreview_artists():
    """
    Any NeedsReview/{artist}/ subfolder whose name matches ARTIST_GENRE_OVERRIDE
    gets its files moved to the correct Library folder automatically.
    Covers the case where a download lands in NeedsReview because Spotify was
    rate-limited, but the artist is already in our override map.
    """
    print("\n=== STEP 5: Resolve NeedsReview artist folders via ARTIST_GENRE_OVERRIDE ===")
    from services.genre_router import normalize_genre, GENRE_TAXONOMY

    SKIP = {"Unknown", "Duplicates", "Albums", "USB"}

    resolved = 0
    for d in sorted(NR.iterdir()):
        if not d.is_dir() or d.name in SKIP:
            continue
        artist_key = d.name.lower().strip()
        genre_val  = config.ARTIST_GENRE_OVERRIDE.get(artist_key, "")
        if not genre_val:
            continue   # not in override map — leave for manual review

        canonical = normalize_genre(genre_val)
        entry     = GENRE_TAXONOMY.get(canonical)
        if not entry:
            continue
        dest_folder = entry[1]   # flat Library subpath e.g. "Punjabi"

        files = list(d.rglob("*.mp3"))
        if not files:
            rmdir_if_empty(d)
            continue

        print(f"  {d.name} → Library/{dest_folder}/ ({len(files)} files)")
        for f in sorted(files):
            mv(f, LIB / dest_folder)
        if not DRY:
            rmdir_if_empty(d)
        resolved += len(files)

    if resolved == 0:
        print("  (no matching artist folders found)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    global moved, skipped, errors
    print(f"Base: {BASE}   DRY_RUN={DRY}\n")

    step1_split_indian()
    step2_tiny_folders()
    step3_deduplicate()
    step4_sweep_numbered_rips()
    step5_resolve_needsreview_artists()

    print(f"\n{'─'*60}")
    print(f"Results:  moved={moved}  skipped={skipped}  errors={errors}")

    if DRY:
        print("(DRY RUN — nothing written)")
        return

    print("\n=== Final Library structure ===")
    for d in sorted(LIB.iterdir()):
        if d.is_dir():
            count = len(list(d.rglob("*.mp3")))
            print(f"  {count:4d}  Library/{d.name}/")
    print("\n=== NeedsReview remaining ===")
    if NR.exists():
        for d in sorted(NR.iterdir()):
            if d.is_dir():
                count = len(list(d.rglob("*.mp3")))
                print(f"  {count:4d}  NeedsReview/{d.name}/")


if __name__ == "__main__":
    main()
