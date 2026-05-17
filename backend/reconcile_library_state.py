"""
Library Reconciliation Engine — Phase 11
==========================================
Detects and optionally repairs state drift between:
  - Filesystem (MP3 files in BASE_DOWNLOAD_DIR)
  - MongoDB library_index
  - MongoDB download_history
  - Retry manifests (.retry_queue/)
  - Staging folder (Ingest/Staging/)
  - NeedsReview folder

Detection categories
--------------------
  orphaned_files       — MP3 on disk with no library_index entry
  stale_index_entries  — library_index rows whose final_path no longer exists
  missing_history      — library_index row with no download_history match
  broken_paths         — stored paths with wrong separators or non-existent parents
  duplicate_hashes     — two index entries sharing the same content_hash
  missing_metadata     — files lacking TBPM, TKEY, or TXXX:SPOTIFY_ID
  invalid_camelot      — TXXX:INITIALKEY set to a value not in the 24-key map
  missing_artwork      — files without an APIC frame
  staging_stuck        — MP3s in Staging/ older than 30 minutes
  needs_review_stuck   — MP3s in NeedsReview/ older than NEEDS_REVIEW_AGE_DAYS
  orphan_manifests     — retry manifests whose staged file is missing

Usage
-----
    python reconcile_library_state.py --dry-run            # report only
    python reconcile_library_state.py --repair             # fix what's safe to fix
    python reconcile_library_state.py --reindex            # re-add missing index rows
    python reconcile_library_state.py --verify-only --json # machine-readable report

Exit codes: 0 = clean, 1 = issues found, 2 = repair errors
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<7} | {message}", level="INFO")
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logger = logging.getLogger(__name__)

from config import config

BASE_DIR = Path(config.BASE_DOWNLOAD_DIR)
STAGING_DIR = BASE_DIR / "Ingest" / "Staging"
NEEDS_REVIEW_DIR = BASE_DIR / "NeedsReview"
RETRY_QUEUE_DIR = BASE_DIR / "Ingest" / ".retry_queue"

STAGING_STUCK_MINUTES = 30
NEEDS_REVIEW_AGE_DAYS = 7

_VALID_CAMELOT = {
    "1A","2A","3A","4A","5A","6A","7A","8A","9A","10A","11A","12A",
    "1B","2B","3B","4B","5B","6B","7B","8B","9B","10B","11B","12B",
}


# ── ID3 inspection helpers ────────────────────────────────────────────────────

def _read_id3(filepath: str) -> dict | None:
    """Return selected ID3 fields or None on error."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            return {}
        def _txxx(desc: str) -> str:
            f = tags.get(f"TXXX:{desc}")
            return str(f.text[0]).strip() if f and f.text else ""
        def _frame(key: str) -> str:
            f = tags.get(key)
            return str(f.text[0]).strip() if f and f.text else ""
        return {
            "bpm":       _frame("TBPM"),
            "key":       _frame("TKEY"),
            "title":     _frame("TIT2"),
            "artist":    _frame("TPE1"),
            "camelot":   _txxx("INITIALKEY"),
            "spotify_id":_txxx("SPOTIFY_ID"),
            "has_apic":  bool(tags.get("APIC:")),
        }
    except Exception:
        return None


# ── Content hash helper ───────────────────────────────────────────────────────

def _file_sha256(filepath: str, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
        return h.hexdigest()
    except Exception:
        return ""


# ── Filesystem scan ───────────────────────────────────────────────────────────

def _scan_filesystem(base: Path, exclude_dirs: set[str]) -> list[dict]:
    """Walk BASE_DIR recursively, return list of MP3 file records."""
    results = []
    for root, dirs, files in os.walk(base):
        # Skip excluded top-level subdirs
        rel_root = Path(root).relative_to(base)
        if rel_root.parts and rel_root.parts[0] in exclude_dirs:
            dirs.clear()
            continue
        for fname in files:
            if not fname.lower().endswith(".mp3"):
                continue
            fp = os.path.join(root, fname)
            results.append({
                "path": fp,
                "rel":  str(Path(fp).relative_to(base)),
                "size": os.path.getsize(fp),
                "mtime": os.path.getmtime(fp),
            })
    return results


# ── MongoDB queries ───────────────────────────────────────────────────────────

def _load_index() -> list[dict]:
    """Return all library_index documents."""
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        return list(col.find({}, {"_id": 0}))
    except Exception as e:
        logger.warning(f"[reconcile] Cannot load library_index: {e}")
        return []


def _load_history_filenames() -> set[str]:
    """Return set of filenames present in download_history."""
    try:
        from database import get_download_history_collection
        col = get_download_history_collection()
        docs = col.find({}, {"filename": 1, "_id": 0})
        return {d["filename"] for d in docs if d.get("filename")}
    except Exception as e:
        logger.warning(f"[reconcile] Cannot load download_history: {e}")
        return set()


def _load_history_spotify_ids() -> set[str]:
    """Return set of spotify_ids in download_history."""
    try:
        from database import get_download_history_collection
        col = get_download_history_collection()
        docs = col.find({"spotify_id": {"$exists": True, "$ne": ""}}, {"spotify_id": 1, "_id": 0})
        return {d["spotify_id"] for d in docs if d.get("spotify_id")}
    except Exception as e:
        logger.warning(f"[reconcile] Cannot load spotify_ids: {e}")
        return set()


# ── Repair helpers ────────────────────────────────────────────────────────────

def _reindex_file(filepath: str, dry_run: bool) -> bool:
    """Add a missing library_index entry for an on-disk file."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        tags_data = _read_id3(filepath)
        spotify_id = (tags_data or {}).get("spotify_id", "")
        title  = (tags_data or {}).get("title", Path(filepath).stem)
        artist = (tags_data or {}).get("artist", "Unknown")
        rel = str(Path(filepath).relative_to(BASE_DIR))
        parts = Path(rel).parts
        genre_folder = parts[0] if len(parts) > 1 else "Unknown"

        if dry_run:
            logger.info(f"  [DRY] would reindex: {rel}")
            return True

        from services.dedup_service import duplicate_identity_key, content_hash
        identity_key = duplicate_identity_key(spotify_id, title, artist)
        ch = content_hash(title, artist)
        from database import index_track
        index_track(
            identity_key=identity_key,
            spotify_id=spotify_id,
            content_hash=ch,
            title=title,
            artist=artist,
            filename=os.path.basename(filepath),
            final_path=filepath,
            genre_folder=genre_folder,
        )
        logger.info(f"  [reindexed] {rel}")
        return True
    except Exception as e:
        logger.warning(f"  [reindex-fail] {filepath}: {e}")
        return False


def _remove_stale_index(identity_key: str, dry_run: bool) -> bool:
    """Remove a stale library_index entry."""
    if dry_run:
        logger.info(f"  [DRY] would remove stale index: {identity_key}")
        return True
    try:
        from database import remove_from_index
        remove_from_index(identity_key)
        logger.info(f"  [removed] stale index: {identity_key}")
        return True
    except Exception as e:
        logger.warning(f"  [remove-fail] {identity_key}: {e}")
        return False


# ── Detection passes ──────────────────────────────────────────────────────────

def _detect_orphaned_files(fs_files: list[dict], index_paths: set[str]) -> list[dict]:
    """Files on disk with no library_index entry (by path)."""
    return [f for f in fs_files if f["path"] not in index_paths]


def _detect_stale_index(index_docs: list[dict]) -> list[dict]:
    """Index rows whose final_path no longer exists on disk."""
    stale = []
    for doc in index_docs:
        fp = doc.get("final_path", "")
        if fp and not os.path.isfile(fp):
            stale.append(doc)
    return stale


def _detect_missing_history(index_docs: list[dict],
                             history_filenames: set[str],
                             history_spotify_ids: set[str]) -> list[dict]:
    """Index rows with no corresponding download_history entry."""
    missing = []
    for doc in index_docs:
        fname = doc.get("filename", "")
        sid   = doc.get("spotify_id", "")
        has_hist = (fname and fname in history_filenames) or \
                   (sid and sid in history_spotify_ids)
        if not has_hist:
            missing.append(doc)
    return missing


def _detect_duplicate_hashes(index_docs: list[dict]) -> list[list[dict]]:
    """Groups of index entries sharing the same content_hash (>1 entry per hash)."""
    from collections import defaultdict
    by_hash: dict[str, list] = defaultdict(list)
    for doc in index_docs:
        ch = doc.get("content_hash", "")
        if ch:
            by_hash[ch].append(doc)
    return [group for group in by_hash.values() if len(group) > 1]


def _detect_missing_metadata(fs_files: list[dict]) -> list[dict]:
    """Files missing BPM, key, or SPOTIFY_ID tags."""
    issues = []
    for f in fs_files:
        tags = _read_id3(f["path"])
        if tags is None:
            continue  # unreadable — skip
        if not tags.get("bpm") or not tags.get("key") or not tags.get("spotify_id"):
            issues.append({**f, "missing": [
                k for k in ("bpm", "key", "spotify_id") if not tags.get(k)
            ]})
    return issues


def _detect_invalid_camelot(fs_files: list[dict]) -> list[dict]:
    """Files with a TXXX:INITIALKEY value not in the 24-key Camelot map."""
    issues = []
    for f in fs_files:
        tags = _read_id3(f["path"])
        if not tags:
            continue
        camelot = (tags.get("camelot") or "").strip().upper()
        if camelot and camelot not in _VALID_CAMELOT:
            issues.append({**f, "bad_camelot": camelot})
    return issues


def _detect_missing_artwork(fs_files: list[dict]) -> list[dict]:
    """Files with no embedded APIC frame."""
    return [f for f in fs_files if _read_id3(f["path"]) is not None
            and not (_read_id3(f["path"]) or {}).get("has_apic")]


def _detect_staging_stuck(max_age_minutes: int = STAGING_STUCK_MINUTES) -> list[dict]:
    """MP3s in Staging/ older than max_age_minutes."""
    if not STAGING_DIR.is_dir():
        return []
    cutoff = time.time() - max_age_minutes * 60
    stuck = []
    for fp in STAGING_DIR.glob("*.mp3"):
        mtime = fp.stat().st_mtime
        if mtime < cutoff:
            age_min = int((time.time() - mtime) / 60)
            stuck.append({"path": str(fp), "age_minutes": age_min})
    return stuck


def _detect_needs_review_stuck(max_age_days: int = NEEDS_REVIEW_AGE_DAYS) -> list[dict]:
    """MP3s in NeedsReview/ older than max_age_days."""
    if not NEEDS_REVIEW_DIR.is_dir():
        return []
    cutoff = time.time() - max_age_days * 86400
    stuck = []
    for fp in NEEDS_REVIEW_DIR.rglob("*.mp3"):
        mtime = fp.stat().st_mtime
        if mtime < cutoff:
            age_days = int((time.time() - mtime) / 86400)
            stuck.append({"path": str(fp), "age_days": age_days})
    return stuck


# ── Pre-migration temp-file pattern (kept minimal to avoid import coupling) ───
_TEMP_RE = re.compile(r"(\.trimmed\.mp3|_\d{1,2}\.mp3|_temp[^.]*\.mp3|_part\d*\.mp3)$",
                      re.IGNORECASE)


def _detect_temp_files_indexed(index_docs: list[dict]) -> list[dict]:
    """
    Library_index entries whose stored filename matches a known temp pattern.
    These should be cleaned up before running retag_migration.
    """
    issues = []
    for doc in index_docs:
        fname = doc.get("filename", "")
        if fname and _TEMP_RE.search(fname):
            issues.append({
                "identity_key": doc.get("identity_key", ""),
                "filename":     fname,
                "final_path":   doc.get("final_path", ""),
            })
    return issues


def _detect_spotify_id_collisions(index_docs: list[dict]) -> list[list[dict]]:
    """
    Groups of library_index entries that share the same spotify_id but point
    to different file paths.  These will cause identity_key conflicts during
    retag_migration (the unique index on identity_key will reject one of them).
    """
    from collections import defaultdict as _dd
    by_sid: dict[str, list] = _dd(list)
    for doc in index_docs:
        sid = (doc.get("spotify_id") or "").strip()
        if sid:
            by_sid[sid].append(doc)
    return [grp for grp in by_sid.values() if len(grp) > 1]


def _detect_orphan_manifests() -> list[dict]:
    """Retry manifests whose staged file no longer exists."""
    if not RETRY_QUEUE_DIR.is_dir():
        return []
    orphans = []
    for mf in RETRY_QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(mf.read_text())
            staged = data.get("staged_path", "")
            if staged and not os.path.isfile(staged):
                orphans.append({"manifest": str(mf), "staged_path": staged,
                                "spotify_id": data.get("spotify_id", ""),
                                "title": data.get("title", "")})
        except Exception:
            orphans.append({"manifest": str(mf), "error": "unreadable"})
    return orphans


# ── Report builder ────────────────────────────────────────────────────────────

def build_report(
    orphaned_files: list,
    stale_index: list,
    missing_history: list,
    dup_hashes: list,
    missing_metadata: list,
    invalid_camelot: list,
    missing_artwork: list,
    staging_stuck: list,
    needs_review_stuck: list,
    orphan_manifests: list,
    total_fs: int,
    total_index: int,
    # Pre-migration checks (optional — populated when pre_migration=True)
    temp_indexed: list | None = None,
    spotify_id_collisions: list | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    pre_mig_issues = len(temp_indexed or []) + len(spotify_id_collisions or [])
    total_issues = sum([
        len(orphaned_files), len(stale_index), len(missing_history),
        len(dup_hashes), len(missing_metadata), len(invalid_camelot),
        len(missing_artwork), len(staging_stuck), len(needs_review_stuck),
        len(orphan_manifests), pre_mig_issues,
    ])
    report: dict = {
        "generated_at": now,
        "summary": {
            "total_fs_files":        total_fs,
            "total_index_entries":   total_index,
            "total_issues":          total_issues,
            "status": "clean" if total_issues == 0 else (
                "warning" if total_issues < 10 else "critical"
            ),
        },
        "orphaned_files":     [{"path": f["path"], "size": f["size"]} for f in orphaned_files],
        "stale_index_entries":[{"identity_key": d.get("identity_key"), "final_path": d.get("final_path")}
                               for d in stale_index],
        "missing_history":    [{"identity_key": d.get("identity_key"), "title": d.get("title")}
                               for d in missing_history],
        "duplicate_hashes":   [[{"identity_key": e.get("identity_key"), "path": e.get("final_path")}
                                for e in group] for group in dup_hashes],
        "missing_metadata":   [{"path": f["path"], "missing": f.get("missing", [])} for f in missing_metadata],
        "invalid_camelot":    [{"path": f["path"], "value": f.get("bad_camelot")} for f in invalid_camelot],
        "missing_artwork":    [{"path": f["path"]} for f in missing_artwork],
        "staging_stuck":      staging_stuck,
        "needs_review_stuck": needs_review_stuck,
        "orphan_manifests":   orphan_manifests,
    }
    # Pre-migration section (only when checks were requested)
    if temp_indexed is not None:
        report["pre_migration"] = {
            "temp_files_indexed": [
                {"identity_key": d["identity_key"], "filename": d["filename"],
                 "final_path": d["final_path"]}
                for d in temp_indexed
            ],
            "spotify_id_collisions": [
                [{"identity_key": e.get("identity_key"), "final_path": e.get("final_path"),
                  "spotify_id": e.get("spotify_id")}
                 for e in group]
                for group in (spotify_id_collisions or [])
            ],
            "pre_migration_issues": pre_mig_issues,
        }
    return report


# ── Main reconciliation run ───────────────────────────────────────────────────

def reconcile(
    dry_run: bool = True,
    repair: bool = False,
    reindex: bool = False,
    verify_only: bool = False,
    output_json: bool = False,
    pre_migration: bool = False,
) -> dict:
    logger.info(f"[reconcile] Starting {'DRY RUN ' if dry_run else ''}reconciliation of {BASE_DIR}")

    # Skip Ingest and hidden dirs when scanning main library
    exclude = {"Ingest", ".retry_queue"}

    logger.info("[reconcile] Scanning filesystem…")
    fs_files = _scan_filesystem(BASE_DIR, exclude)
    total_fs = len(fs_files)
    logger.info(f"[reconcile] Found {total_fs} MP3 files on disk")

    logger.info("[reconcile] Loading library_index…")
    index_docs = _load_index()
    total_index = len(index_docs)
    index_paths = {d.get("final_path", "") for d in index_docs}
    logger.info(f"[reconcile] library_index has {total_index} entries")

    logger.info("[reconcile] Loading download_history…")
    history_filenames  = _load_history_filenames()
    history_spotify_ids = _load_history_spotify_ids()

    logger.info("[reconcile] Detecting issues…")

    orphaned_files    = _detect_orphaned_files(fs_files, index_paths)
    stale_index       = _detect_stale_index(index_docs)
    missing_history   = _detect_missing_history(index_docs, history_filenames, history_spotify_ids)
    dup_hashes        = _detect_duplicate_hashes(index_docs)
    missing_metadata  = _detect_missing_metadata(fs_files)
    invalid_camelot   = _detect_invalid_camelot(fs_files)
    missing_artwork   = _detect_missing_artwork(fs_files)
    staging_stuck     = _detect_staging_stuck()
    needs_review_stuck = _detect_needs_review_stuck()
    orphan_manifests  = _detect_orphan_manifests()

    # Pre-migration checks (only when --pre-migration flag is set)
    temp_indexed: list | None = None
    spotify_id_collisions: list | None = None
    if pre_migration:
        logger.info("[reconcile] Running pre-migration checks…")
        temp_indexed          = _detect_temp_files_indexed(index_docs)
        spotify_id_collisions = _detect_spotify_id_collisions(index_docs)
        if temp_indexed:
            logger.warning(f"[reconcile] Pre-migration: {len(temp_indexed)} temp file(s) in index")
        if spotify_id_collisions:
            logger.warning(
                f"[reconcile] Pre-migration: {len(spotify_id_collisions)} "
                "spotify_id collision(s) in index"
            )

    report = build_report(
        orphaned_files, stale_index, missing_history, dup_hashes,
        missing_metadata, invalid_camelot, missing_artwork,
        staging_stuck, needs_review_stuck, orphan_manifests,
        total_fs, total_index,
        temp_indexed=temp_indexed,
        spotify_id_collisions=spotify_id_collisions,
    )

    _print_summary(report)

    # Write report file
    report_path = _BACKEND / "reconciliation_report.json"
    if not verify_only:
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info(f"[reconcile] Report written → {report_path}")

    if output_json:
        print(json.dumps(report, indent=2, default=str))

    # Repairs
    repair_errors = 0
    if repair or reindex:
        if reindex and orphaned_files:
            logger.info(f"[reconcile] Reindexing {len(orphaned_files)} orphaned file(s)…")
            for f in orphaned_files:
                if not _reindex_file(f["path"], dry_run):
                    repair_errors += 1

        if repair and stale_index:
            logger.info(f"[reconcile] Removing {len(stale_index)} stale index entries…")
            for doc in stale_index:
                if not _remove_stale_index(doc.get("identity_key", ""), dry_run):
                    repair_errors += 1

        if repair and orphan_manifests:
            logger.info(f"[reconcile] Cleaning {len(orphan_manifests)} orphan manifest(s)…")
            for m in orphan_manifests:
                mp = m.get("manifest", "")
                if mp and os.path.isfile(mp):
                    if not dry_run:
                        dead = RETRY_QUEUE_DIR / "dead"
                        dead.mkdir(exist_ok=True)
                        Path(mp).rename(dead / Path(mp).name)
                        logger.info(f"  [moved to dead] {Path(mp).name}")
                    else:
                        logger.info(f"  [DRY] would move orphan manifest to dead/: {Path(mp).name}")

    return report


def _print_summary(report: dict):
    s = report["summary"]
    status = s["status"].upper()
    color = {"CLEAN": "\033[92m", "WARNING": "\033[93m", "CRITICAL": "\033[91m"}.get(status, "")
    reset = "\033[0m"
    print(f"\n{'='*60}")
    print(f"  {color}Reconciliation: {status}{reset}  "
          f"({s['total_issues']} issue(s) in {s['total_fs_files']} files / {s['total_index_entries']} index entries)")
    print(f"{'='*60}")
    categories = [
        ("orphaned_files",     "Orphaned files (on disk, not in index)"),
        ("stale_index_entries","Stale index entries (path missing)"),
        ("missing_history",    "Missing download_history entries"),
        ("duplicate_hashes",   "Duplicate content hashes"),
        ("missing_metadata",   "Files missing BPM/Key/SpotifyID tags"),
        ("invalid_camelot",    "Invalid Camelot key values"),
        ("missing_artwork",    "Files without embedded artwork"),
        ("staging_stuck",      "Files stuck in Staging (>30min)"),
        ("needs_review_stuck", f"Files stuck in NeedsReview (>{NEEDS_REVIEW_AGE_DAYS}d)"),
        ("orphan_manifests",   "Orphan retry manifests (staged file gone)"),
    ]
    for key, label in categories:
        items = report.get(key, [])
        n = len(items)
        icon = "✅" if n == 0 else ("⚠️ " if n < 5 else "❌")
        print(f"  {icon}  {label}: {n}")

    # Pre-migration section
    pm = report.get("pre_migration")
    if pm is not None:
        print(f"\n  Pre-migration checks:")
        n_temp = len(pm.get("temp_files_indexed", []))
        n_sid  = len(pm.get("spotify_id_collisions", []))
        print(f"  {'✅' if n_temp == 0 else '❌'}  Temp files in library_index: {n_temp}")
        print(f"  {'✅' if n_sid  == 0 else '❌'}  Spotify ID collisions in index: {n_sid}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Reconcile DJ library state across filesystem, MongoDB, and retry queue"
    )
    parser.add_argument("--dry-run",        action="store_true", help="Detect only, no changes")
    parser.add_argument("--repair",         action="store_true", help="Remove stale index rows, move orphan manifests to dead/")
    parser.add_argument("--reindex",        action="store_true", help="Re-add orphaned files to library_index")
    parser.add_argument("--verify-only",    action="store_true", help="Skip writing reconciliation_report.json")
    parser.add_argument("--json",           action="store_true", help="Print JSON report to stdout")
    parser.add_argument("--pre-migration",  action="store_true",
                        help="Run additional pre-migration checks (temp indexed, spotify_id collisions)")
    args = parser.parse_args()

    report = reconcile(
        dry_run=args.dry_run,
        repair=args.repair,
        reindex=args.reindex,
        verify_only=args.verify_only,
        output_json=args.json,
        pre_migration=args.pre_migration,
    )
    issues = report["summary"]["total_issues"]
    sys.exit(0 if issues == 0 else 1)


if __name__ == "__main__":
    main()
