#!/usr/bin/env python3
"""
cleanup_wrong_tracks.py
=======================
Find WRONG music files in the library (karaoke / instrumental re-recordings, wrong songs),
send them to the Recycle Bin on YOUR say-so, and re-queue them so the next ingest cycle
downloads the correct version.

Two steps, on purpose:

  1) scan   READ-ONLY. Lists suspects, writes a report, touches nothing.

       python cleanup_wrong_tracks.py scan
       python cleanup_wrong_tracks.py scan --names "Make Some Noise For The Desi Boyz"
       python cleanup_wrong_tracks.py scan --names-file wrong.txt
       python cleanup_wrong_tracks.py scan --verify-audio        (slow: fingerprints every file)

  2) apply  PREVIEW by default. Add --yes to really do it.

       python cleanup_wrong_tracks.py apply --report reports/wrong_tracks_20260919_101500.json
       python cleanup_wrong_tracks.py apply --report ... --yes

What gets marked for removal by default (the report shows every reason):
  user    files whose name matches something YOU listed with --names / --names-file
  high    files whose audio fingerprint PROVES they are a karaoke/instrumental/other-song
          recording (needs --verify-audio; see services/audio_verifier.py)
  medium  files whose filename/title/artist merely CONTAINS "karaoke"/"instrumental"/...
          -> listed for review but NOT selected by default (a real DJ instrumental
          would look the same). Include some with --only, e.g.  --only 3,7,10-12

Removal is recoverable: files go to the Windows Recycle Bin (Restore puts them back).
If that is unavailable they are moved to <library>/_TO_DELETE/<time>/ instead.
Nothing in this tool permanently deletes a file.

After removal each track is (a) dropped from the Mongo library index and (b) re-queued
(services/requeue_service.py), so the next ingest cycle downloads it again with the
stricter matcher + audio verification. The backend must be running, with an ingest
playlist configured, for that cycle to happen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Only the karaoke / instrumental family — deliberately narrower than the download-time reject
# list ("cover", "clip", "reverb" ... are common in legitimate titles and would flood a scan).
WRONG_VERSION_KEYWORDS = [
    "karaoke", "karaokes", "karoke", "kareoke", "karaoké",
    "instrumental", "instrumentals", "backing track", "backing tracks", "minus one",
    "off vocal", "vocals removed", "vocal removed", "without vocals", "no vocals",
    "vocal free", "sing along", "singalong", "beat only", "music only", "bgm",
]

_RANK = {"user": 3, "high": 2, "medium": 1}


# ═══════════════════════════════════════════════════════════════════════════
# scan
# ═══════════════════════════════════════════════════════════════════════════

def read_tags(path: Path) -> Dict[str, str]:
    """Title / artist / Spotify id from ID3 ('' when absent or unreadable)."""
    out = {"title": "", "artist": "", "spotify_id": ""}
    try:
        from mutagen.id3 import ID3
        tags = ID3(str(path))
    except Exception:
        return out

    def first(key: str) -> str:
        frame = tags.get(key)
        text = getattr(frame, "text", None) if frame is not None else None
        return str(text[0]).strip() if text else ""

    out["title"], out["artist"] = first("TIT2"), first("TPE1")
    out["spotify_id"] = first("TXXX:SPOTIFY_ID")
    return out


def split_filename(stem: str) -> tuple:
    """'Title - Artist' (this app's naming) -> (title, artist)."""
    if " - " in stem:
        title, artist = stem.rsplit(" - ", 1)
        return title.strip(), artist.strip()
    return stem.strip(), ""


def iter_mp3(root: Path, folder: str = "Library") -> List[Path]:
    base = root / folder if folder else root
    return sorted(p for p in base.rglob("*.mp3")
                  if p.is_file() and "_TO_DELETE" not in p.parts)


def name_matches(path: Path, tags: Dict[str, str], names: Iterable[str]) -> List[str]:
    haystacks = [path.stem.lower(), f"{tags['title']} - {tags['artist']}".lower(), str(path).lower()]
    hits = []
    for name in names:
        n = name.strip().lower()
        if n and any(n in h for h in haystacks):
            hits.append(name.strip())
    return hits


def keyword_reasons(path: Path, tags: Dict[str, str]) -> List[str]:
    from services.strict_matcher import REJECT_CHANNEL_KEYWORDS, has_reject_keyword
    reasons = []
    kw = has_reject_keyword(path.stem, keywords=WRONG_VERSION_KEYWORDS)
    if kw:
        reasons.append(f"filename contains '{kw}'")
    kw = has_reject_keyword(tags["title"], keywords=WRONG_VERSION_KEYWORDS)
    if kw and not reasons:
        reasons.append(f"title tag contains '{kw}'")
    kw = has_reject_keyword(tags["artist"], keywords=REJECT_CHANNEL_KEYWORDS)
    if kw:
        reasons.append(f"artist tag '{tags['artist']}' looks like a karaoke/cover act ('{kw}')")
    return reasons


def audio_verdict(path: Path, tags: Dict[str, str], cache: dict) -> dict:
    """AcoustID verdict for a file, cached by path+size+mtime so an interrupted scan can resume."""
    from services.audio_verifier import verify_recording
    st = path.stat()
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    if key not in cache:
        title, artist = tags["title"], tags["artist"]
        if not title:
            title, guess_artist = split_filename(path.stem)
            artist = artist or guess_artist
        cache[key] = verify_recording(str(path), title, artist).to_dict()
    return cache[key]


def scan_library(
    root: Path,
    *,
    folder: str = "Library",
    names: Optional[List[str]] = None,
    verify_audio: bool = False,
    limit: Optional[int] = None,
    cache_path: Optional[Path] = None,
    progress: Optional[Callable[[int, int, Path], None]] = None,
) -> List[dict]:
    """READ-ONLY. Returns one entry per suspect file."""
    names = names or []
    files = iter_mp3(root, folder)
    if limit:
        files = files[:limit]

    cache: dict = {}
    if verify_audio and cache_path and cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    entries: List[dict] = []
    for i, path in enumerate(files, 1):
        if progress:
            progress(i, len(files), path)
        tags = read_tags(path)
        reasons: List[str] = []
        confidence = ""
        audio = None

        hits = name_matches(path, tags, names)
        if hits:
            reasons.append("you listed: " + ", ".join(f"'{h}'" for h in hits))
            confidence = "user"

        kw = keyword_reasons(path, tags)
        if kw:
            reasons.extend(kw)
            confidence = confidence or "medium"

        if verify_audio:
            audio = audio_verdict(path, tags, cache)
            if audio["status"] in ("suspect_version", "mismatch"):
                reasons.append(f"audio fingerprint: {audio['reason']}")
                if _RANK.get("high", 0) > _RANK.get(confidence, 0):
                    confidence = "high"
            if cache_path and i % 25 == 0:
                _save_cache(cache_path, cache)

        if reasons:
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)
            entries.append({
                "path": str(path), "rel_path": rel, "filename": path.name,
                "title": tags["title"], "artist": tags["artist"], "spotify_id": tags["spotify_id"],
                "reasons": reasons, "confidence": confidence,
                "delete": confidence in ("user", "high"),
                "audio": audio,
            })

    if verify_audio and cache_path:
        _save_cache(cache_path, cache)
    for n, e in enumerate(entries, 1):
        e["idx"] = n
    return entries


def _save_cache(cache_path: Path, cache: dict) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# report
# ═══════════════════════════════════════════════════════════════════════════

def write_report(entries: List[dict], root: Path, out_dir: Optional[Path] = None) -> tuple:
    out_dir = out_dir or REPORTS_DIR          # looked up at CALL time so tests can redirect it
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"wrong_tracks_{stamp}.json"
    txt_path = out_dir / f"wrong_tracks_{stamp}.txt"
    json_path.write_text(json.dumps(
        {"root": str(root), "created": stamp, "entries": entries}, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(render_report(entries), encoding="utf-8")
    return json_path, txt_path


def render_report(entries: List[dict]) -> str:
    if not entries:
        return "No suspect files found.\n"
    lines = []
    for e in entries:
        action = "REMOVE" if e["delete"] else "review"
        lines.append(f"[{e['idx']:>3}] {action:<6} {e['confidence']:<6} {e['rel_path']}")
        for reason in e["reasons"]:
            lines.append(f"        - {reason}")
    n_del = sum(1 for e in entries if e["delete"])
    lines.append("")
    lines.append(f"{len(entries)} suspect file(s): {n_del} marked REMOVE by default, {len(entries) - n_del} to review.")
    return "\n".join(lines) + "\n"


def load_report(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_index_list(text: Optional[str]) -> Set[int]:
    """'1,4,7-9' -> {1,4,7,8,9}"""
    out: Set[int] = set()
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def select_entries(entries: List[dict], only: Optional[str] = None, skip: Optional[str] = None) -> List[dict]:
    """Default: entries marked delete. --only ADDS the listed indexes; --skip removes indexes."""
    chosen = {e["idx"] for e in entries if e.get("delete")}
    chosen |= parse_index_list(only)
    chosen -= parse_index_list(skip)
    return [e for e in entries if e["idx"] in chosen]


# ═══════════════════════════════════════════════════════════════════════════
# apply
# ═══════════════════════════════════════════════════════════════════════════

def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def remove_from_library_index(path: str) -> int:
    """Drop the Mongo library_index record(s) that point at a removed file."""
    from database import get_library_index_collection
    col = get_library_index_collection()
    variants = list({path, path.replace("\\", "/"), path.replace("/", "\\")})
    return col.delete_many({"final_path": {"$in": variants}}).deleted_count


def apply_report(
    entries: List[dict],
    root: Path,
    *,
    dry_run: bool = True,
    quarantine_only: bool = False,
    remove_fn: Optional[Callable[[str, str, str], Optional[str]]] = None,
    index_remove_fn: Optional[Callable[[str], int]] = None,
    requeue_fn: Optional[Callable[[List[dict], str], dict]] = None,
    out: Callable[[str], None] = print,
) -> dict:
    """
    Remove the given entries (recoverably) and re-queue them. With dry_run=True nothing changes.
    The three *_fn hooks exist so tests can run without a real Recycle Bin / database.
    """
    if remove_fn is None:
        from services.recycle import remove_file as remove_fn
    if index_remove_fn is None:
        index_remove_fn = remove_from_library_index
    if requeue_fn is None:
        from services.requeue_service import requeue_tracks as requeue_fn

    result = {"selected": len(entries), "removed": [], "skipped": [], "index_removed": 0,
              "requeue": None, "dry_run": dry_run}
    mode = "quarantine" if quarantine_only else "recycle"
    to_requeue: List[dict] = []

    for e in entries:
        path = Path(e["path"])
        problem = None
        if path.suffix.lower() != ".mp3":
            problem = "not an .mp3"
        elif not _within(root, path):
            problem = "outside the library root"
        elif path.is_symlink():
            problem = "is a symlink"
        elif not path.is_file():
            problem = "file not found (already removed?)"
        if problem:
            result["skipped"].append((e["path"], problem))
            out(f"  skip   {e['rel_path']}  ({problem})")
            continue

        if dry_run:
            out(f"  would remove  {e['rel_path']}")
            result["removed"].append((e["path"], "dry-run"))
            continue

        how = remove_fn(str(path), str(root), mode)
        if not how:
            result["skipped"].append((e["path"], "could not be removed"))
            out(f"  FAILED {e['rel_path']}")
            continue
        out(f"  removed ({'Recycle Bin' if how == 'recycle-bin' else 'moved to ' + how})  {e['rel_path']}")
        result["removed"].append((e["path"], how))
        try:
            result["index_removed"] += index_remove_fn(str(path))
        except Exception as exc:
            out(f"    (library index not updated: {exc})")
        to_requeue.append({"spotify_id": e.get("spotify_id", ""),
                           "title": e.get("title") or split_filename(path.stem)[0],
                           "artist": e.get("artist") or split_filename(path.stem)[1]})

    if to_requeue and not dry_run:
        try:
            result["requeue"] = requeue_fn(to_requeue, "removed as a wrong version (cleanup_wrong_tracks)")
        except Exception as exc:
            result["requeue"] = {"error": str(exc)}
            out(f"  WARNING: re-queue failed: {exc}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def _collect_names(args) -> List[str]:
    names = list(args.names or [])
    if args.names_file:
        names += [ln.strip() for ln in Path(args.names_file).read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
    return names


def cmd_scan(args) -> int:
    from config import config
    root = Path(args.root or config.BASE_DOWNLOAD_DIR)
    if not root.is_dir():
        print(f"Library root not found: {root}")
        return 2
    if args.verify_audio and os.getenv("AUDIO_VERIFY_MODE", "").strip().lower() == "off":
        os.environ["AUDIO_VERIFY_MODE"] = "flag"     # 'off' would make every verdict inconclusive

    def progress(i, n, p):
        if args.verify_audio or i % 250 == 0 or i == n:
            print(f"\r  scanning {i}/{n}  {p.name[:60]:<60}", end="", flush=True)

    print(f"Scanning {root / args.folder}  (read-only)")
    entries = scan_library(
        root, folder=args.folder, names=_collect_names(args), verify_audio=args.verify_audio,
        limit=args.limit, cache_path=REPORTS_DIR / "verify_cache.json", progress=progress,
    )
    print()
    print(render_report(entries))
    if entries:
        json_path, txt_path = write_report(entries, root)
        print(f"Report:  {json_path}\n         {txt_path}")
        print("\nNothing has been changed. To act on it:\n"
              f"  python cleanup_wrong_tracks.py apply --report \"{json_path}\"          (preview)\n"
              f"  python cleanup_wrong_tracks.py apply --report \"{json_path}\" --yes    (do it)")
    return 0


def cmd_apply(args) -> int:
    report = load_report(Path(args.report))
    root = Path(args.root or report["root"])
    entries = select_entries(report["entries"], only=args.only, skip=args.skip)
    if not entries:
        print("Nothing selected. (Entries marked 'review' are only included with --only N,M,...)")
        return 0

    dry_run = not args.yes
    print(("PREVIEW — nothing will change. Re-run with --yes to apply.\n" if dry_run
           else "APPLYING — files go to the Recycle Bin (recoverable).\n"))
    print(f"{len(entries)} file(s) selected:")
    result = apply_report(entries, root, dry_run=dry_run, quarantine_only=args.quarantine)

    print()
    if dry_run:
        print(f"Preview only: {len(result['removed'])} would be removed, {len(result['skipped'])} skipped.")
        return 0
    print(f"Removed: {len(result['removed'])}   Skipped/failed: {len(result['skipped'])}   "
          f"Library-index records dropped: {result['index_removed']}")
    if result["requeue"]:
        print(f"Re-queued for the next ingest cycle: {result['requeue']}")
    print("\nRestore any file: Recycle Bin -> right-click -> Restore.\n"
          "The next ingest cycle (backend running, ingest playlist configured) will download the correct versions.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Find and remove wrong music files, then re-queue them.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="READ-ONLY: list suspect files and write a report")
    s.add_argument("--root", help="library root (default: BASE_DOWNLOAD_DIR)")
    s.add_argument("--folder", default="Library", help="folder under the root to scan (default: Library)")
    s.add_argument("--names", nargs="*", help="files whose name contains any of these are marked for removal")
    s.add_argument("--names-file", help="text file, one name per line ('#' comments allowed)")
    s.add_argument("--verify-audio", action="store_true",
                   help="fingerprint every file via AcoustID (slow, but PROVES karaoke/wrong-song audio)")
    s.add_argument("--limit", type=int, help="only scan the first N files (testing)")
    s.set_defaults(fn=cmd_scan)

    a = sub.add_parser("apply", help="remove the files in a report (preview unless --yes)")
    a.add_argument("--report", required=True, help="a wrong_tracks_*.json produced by 'scan'")
    a.add_argument("--root", help="override the library root stored in the report")
    a.add_argument("--only", help="also include these report indexes, e.g. 3,7,10-12")
    a.add_argument("--skip", help="exclude these report indexes")
    a.add_argument("--yes", action="store_true", help="actually remove (default is a preview)")
    a.add_argument("--quarantine", action="store_true",
                   help="move to <library>/_TO_DELETE instead of the Recycle Bin")
    a.set_defaults(fn=cmd_apply)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
