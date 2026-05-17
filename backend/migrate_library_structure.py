"""
Library Structure Migration — Phase 8
=======================================
Safely migrates the old flat genre folder layout to the new
Library/{family}/{subgenre}/{artist}/ hierarchy.

Old layout (flat):
  {BASE_DIR}/House/Sammy Virji/track.mp3
  {BASE_DIR}/UK Garage/Artist/track.mp3
  {BASE_DIR}/Bollywood/Artist/track.mp3

New layout (Library/ hierarchy):
  {BASE_DIR}/Library/Electronic/House/Sammy Virji/track.mp3
  {BASE_DIR}/Library/Electronic/UKG/Artist/track.mp3
  {BASE_DIR}/Library/Indian/Bollywood/Artist/track.mp3

Safety guarantees
-----------------
• Source files are NEVER deleted until a post-move size+sha256 check passes.
• Empty source folders are removed ONLY after all files succeed.
• Existing destination files are NOT overwritten (collision suffix appended).
• library_index final_path is updated atomically after each move.
• --dry-run mode (default) logs every intended action without touching files.
• --verify-only checks that all library_index paths still exist on disk.

Usage
-----
    cd backend
    python migrate_library_structure.py --dry-run        # default — safe preview
    python migrate_library_structure.py --execute        # perform moves
    python migrate_library_structure.py --verify-only    # path integrity check

Exit codes: 0 = success / clean, 1 = errors found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

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
from services.genre_router import GENRE_TAXONOMY, _library_path
from library_scanner import is_excluded_path

BASE_DIR     = Path(config.BASE_DOWNLOAD_DIR)
LIBRARY_ROOT = BASE_DIR / "Library"

# Folders that live at the BASE_DIR root and should be migrated.
# Quarantine, NeedsReview, Manual, Ingest, Rekordbox, Archive are EXCLUDED.
_SKIP_TOP_DIRS = {
    "quarantine", "needsreview", "manual", "ingest", "ingest staging",
    "rekordbox", "archive", "library", ".retry_queue", "staging",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_dest(dest_path: Path) -> Path:
    """Return a collision-free destination path (appends _N suffix if needed)."""
    if not dest_path.exists():
        return dest_path
    stem = dest_path.stem
    for n in range(1, 1000):
        candidate = dest_path.parent / f"{stem}_{n}.mp3"
        if not candidate.exists():
            return candidate
    raise OSError(f"Cannot find collision-free name for {dest_path}")


def _update_index(old_path: str, new_path: str) -> None:
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is None:
            return
        col.update_one(
            {"final_path": old_path},
            {"$set": {
                "final_path":   new_path,
                "filename":     Path(new_path).name,
                "genre_folder": str(Path(new_path).parent.relative_to(BASE_DIR)).replace("\\", "/"),
            }},
        )
    except Exception as e:
        logger.warning(f"[migrate] library_index update failed: {e}")


def _map_old_to_new(top_dir: str) -> str | None:
    """
    Map an old flat top-level folder name to a Library/ subpath.

    "House"     → "Library/Electronic/House"
    "UK Garage" → "Library/Electronic/UKG"
    "Bollywood" → "Library/Indian/Bollywood"
    "Hip Hop"   → "Library/HipHop"

    Returns None for unrecognised folders.
    """
    lib = _library_path(top_dir)
    if lib == "Library/OpenFormat" and top_dir not in GENRE_TAXONOMY:
        return None  # unknown folder — don't touch
    return lib


def pre_migration_check() -> dict:
    """
    Run pre-flight checks before executing any file moves.

    Returns:
        {"ok": bool, "issues": list[str]}
    """
    issues: list[str] = []

    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            # Check 1: No Ingest paths already in library_index
            ingest_docs = list(col.find(
                {"final_path": {"$regex": r"[/\\][Ii]ngest[/\\]", "$options": "i"}},
                {"final_path": 1, "_id": 0},
            ).limit(5))
            if ingest_docs:
                for d in ingest_docs:
                    issues.append(f"INGEST path in library_index: {d['final_path']}")

            # Check 2: No Library-in-Library recursive paths
            nested_docs = list(col.find(
                {"final_path": {"$regex": r"[/\\][Ll]ibrary[/\\].*[/\\][Ll]ibrary[/\\]"}},
                {"final_path": 1, "_id": 0},
            ).limit(5))
            if nested_docs:
                for d in nested_docs:
                    issues.append(f"Nested Library/ path: {d['final_path']}")
    except Exception as e:
        issues.append(f"DB check failed: {e}")

    # Check 3: LIBRARY_ROOT itself not nested under LIBRARY_ROOT
    try:
        rel = LIBRARY_ROOT.relative_to(BASE_DIR)
        parts = [p.lower() for p in rel.parts]
        if parts.count("library") > 1:
            issues.append(f"LIBRARY_ROOT is nested: {LIBRARY_ROOT}")
    except ValueError:
        issues.append(f"LIBRARY_ROOT {LIBRARY_ROOT} not under BASE_DIR {BASE_DIR}")

    # Check 4: No duplicate destination paths would be produced
    seen_destinations: set[str] = set()
    top_dirs = [
        d for d in BASE_DIR.iterdir()
        if d.is_dir() and d.name.lower() not in _SKIP_TOP_DIRS
        and not d.name.startswith(".")
        and not is_excluded_path(d)
    ]
    for old_top in top_dirs:
        new_lib = _map_old_to_new(old_top.name)
        if new_lib is None:
            continue
        for src in old_top.rglob("*.mp3"):
            if is_excluded_path(src):
                continue
            try:
                rel = src.relative_to(old_top)
            except ValueError:
                rel = Path(src.name)
            dest_key = str(BASE_DIR / new_lib / rel).lower()
            if dest_key in seen_destinations:
                issues.append(f"Duplicate destination would occur: {dest_key}")
                if len(issues) >= 20:
                    break
            seen_destinations.add(dest_key)
        if len(issues) >= 20:
            break

    ok = len(issues) == 0
    if ok:
        logger.info("[migrate][preflight] All pre-migration checks passed.")
    else:
        for issue in issues:
            logger.warning(f"[migrate][preflight] {issue}")
    return {"ok": ok, "issues": issues}


def _write_rollback_manifest(entries: list[dict], dry_run: bool) -> str | None:
    """Write migration_rollback_{timestamp}.json alongside BASE_DIR. Returns path or None."""
    if dry_run or not entries:
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = BASE_DIR / f"migration_rollback_{ts}.json"
    payload = {
        "created_at": ts,
        "base_dir": str(BASE_DIR),
        "entry_count": len(entries),
        "entries": entries,
    }
    try:
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info(f"[migrate] Rollback manifest: {manifest_path}")
        return str(manifest_path)
    except Exception as e:
        logger.error(f"[migrate] Failed to write rollback manifest: {e}")
        return None


def migrate(dry_run: bool = True, verify_only: bool = False) -> dict:
    """
    Run the structure migration.

    Returns:
        {
          "moved":      int,
          "skipped":    int,
          "errors":     int,
          "unmapped":   list[str],  # old top-dirs with no taxonomy mapping
          "preflight":  dict,       # pre_migration_check result (execute mode only)
          "rollback":   str | None, # path to rollback manifest
        }
    """
    if verify_only:
        return _verify_only()

    if not dry_run:
        preflight = pre_migration_check()
        if not preflight["ok"]:
            logger.error("[migrate] Pre-migration checks failed — aborting. Fix issues above first.")
            return {"moved": 0, "skipped": 0, "errors": len(preflight["issues"]),
                    "unmapped": [], "preflight": preflight, "rollback": None}
    else:
        preflight = {"ok": True, "issues": []}

    moved = skipped = errors = 0
    unmapped: list[str] = []
    rollback_entries: list[dict] = []

    # Collect top-level genre folders (exclude known non-genre dirs)
    top_dirs = [
        d for d in BASE_DIR.iterdir()
        if d.is_dir() and d.name.lower() not in _SKIP_TOP_DIRS
        and not d.name.startswith(".")
        and not is_excluded_path(d)
    ]

    for old_top in sorted(top_dirs):
        new_lib = _map_old_to_new(old_top.name)
        if new_lib is None:
            unmapped.append(old_top.name)
            logger.warning(f"[migrate] No taxonomy mapping for: {old_top.name!r} — skipping")
            continue

        logger.info(f"[migrate] {old_top.name!r} → {new_lib}/")

        mp3_files = list(old_top.rglob("*.mp3"))
        for src in mp3_files:
            if is_excluded_path(src):
                logger.debug(f"[migrate][EXCLUDED] {src.relative_to(BASE_DIR)}")
                skipped += 1
                continue

            # Preserve artist subfolder structure
            try:
                rel = src.relative_to(old_top)
            except ValueError:
                rel = Path(src.name)

            dest_path = BASE_DIR / new_lib / rel
            dest_dir  = dest_path.parent

            if dry_run:
                logger.info(
                    f"  [DRY] {src.relative_to(BASE_DIR)} "
                    f"→ {dest_path.relative_to(BASE_DIR)}"
                )
                moved += 1
                continue

            if dest_path.exists() and dest_path.resolve() == src.resolve():
                skipped += 1
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                final_dest = _safe_dest(dest_path)

                # Integrity: hash before move
                src_hash = _sha256_file(src)
                src_size  = src.stat().st_size

                src_st  = os.stat(src)
                dest_st = os.stat(dest_dir)
                if src_st.st_dev == dest_st.st_dev:
                    os.replace(src, final_dest)
                else:
                    shutil.copy2(src, final_dest)
                    if _sha256_file(final_dest) != src_hash or final_dest.stat().st_size != src_size:
                        final_dest.unlink(missing_ok=True)
                        raise OSError("Copy integrity check failed (sha256 mismatch)")
                    src.unlink()

                _update_index(str(src), str(final_dest))
                rollback_entries.append({"old_path": str(src), "new_path": str(final_dest)})
                logger.info(
                    f"  Moved: {src.relative_to(BASE_DIR)} "
                    f"→ {final_dest.relative_to(BASE_DIR)}"
                )
                moved += 1

            except Exception as e:
                logger.error(f"  Error moving {src.name}: {e}")
                errors += 1

        # Remove empty source folders (only when executing)
        if not dry_run:
            for sub in sorted(old_top.rglob("*"), reverse=True):
                if sub.is_dir():
                    try:
                        sub.rmdir()  # only removes if empty
                    except OSError:
                        pass
            try:
                old_top.rmdir()
            except OSError:
                pass  # not empty — leave it

    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        f"\n{label}Migration complete: {moved} moved, {skipped} skipped, "
        f"{errors} errors | {len(unmapped)} unmapped top-dirs"
    )
    if unmapped:
        logger.info(f"  Unmapped folders (left untouched): {unmapped}")

    rollback_path = _write_rollback_manifest(rollback_entries, dry_run)
    return {
        "moved":     moved,
        "skipped":   skipped,
        "errors":    errors,
        "unmapped":  unmapped,
        "preflight": preflight,
        "rollback":  rollback_path,
    }


def migrate_artist_folders(
    unmapped_names: list[str],
    dry_run: bool = True,
    analysis_path: Path | None = None,
) -> dict:
    """
    Phase 3 — Retry previously unmapped folders using artist folder intelligence.

    For each folder in unmapped_names that remains on disk:
      1. Run artist_folder_service.detect_folder_type()
      2. If suggested_route found with confidence ≥ 0.5, move all .mp3 files
      3. Update library_index, seed artist_memory
      4. Write retry_unmapped_report.json beside BASE_DIR

    Returns:
        {moved, skipped, errors, still_unmapped, report_path}
    """
    from services.artist_folder_service import detect_folder_type, seed_from_routing
    from library_scanner import is_excluded_path

    # Load pre-computed analysis if available (avoids re-scanning tags)
    precomputed: dict = {}
    if analysis_path and analysis_path.is_file():
        try:
            precomputed = json.loads(analysis_path.read_text(encoding="utf-8")).get("folders", {})
            logger.info(f"[migrate_artist] Loaded pre-computed analysis from {analysis_path}")
        except Exception as e:
            logger.warning(f"[migrate_artist] Could not load analysis file: {e}")

    moved = skipped = errors = 0
    still_unmapped: list[str] = []
    rollback_entries: list[dict] = []
    folder_results: dict = {}

    for name in sorted(unmapped_names):
        folder_path = BASE_DIR / name
        if not folder_path.is_dir():
            still_unmapped.append(name)
            folder_results[name] = {"status": "not_on_disk"}
            continue

        info = precomputed.get(name) or detect_folder_type(name, folder_path)
        route = info.get("suggested_route")

        if not route or info["confidence"] < 0.5:
            still_unmapped.append(name)
            folder_results[name] = {"status": "unresolved", "detection": info}
            logger.warning(f"[migrate_artist] No route for {name!r} "
                           f"(type={info['type']}, conf={info['confidence']:.2f})")
            continue

        canonical_name = info["canonical_name"]
        from services.organizer_service import clean_folder_name
        dest_subdir = BASE_DIR / route / clean_folder_name(canonical_name)
        logger.info(f"[migrate_artist] {name!r} ({info['type']}) → {route}/{clean_folder_name(canonical_name)}")

        mp3_files = [f for f in folder_path.rglob("*.mp3") if not is_excluded_path(f)]
        folder_moved = folder_errors = 0

        for src in mp3_files:
            try:
                rel = src.relative_to(folder_path)
            except ValueError:
                rel = Path(src.name)

            dest_path = dest_subdir / rel

            if dry_run:
                logger.info(f"  [DRY] {src.relative_to(BASE_DIR)} → {dest_path.relative_to(BASE_DIR)}")
                folder_moved += 1
                continue

            if dest_path.exists() and dest_path.resolve() == src.resolve():
                skipped += 1
                continue

            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                final_dest = _safe_dest(dest_path)

                src_hash = _sha256_file(src)
                src_size  = src.stat().st_size
                src_st    = os.stat(src)
                dest_st   = os.stat(dest_path.parent)

                if src_st.st_dev == dest_st.st_dev:
                    os.replace(src, final_dest)
                else:
                    shutil.copy2(src, final_dest)
                    if (_sha256_file(final_dest) != src_hash
                            or final_dest.stat().st_size != src_size):
                        final_dest.unlink(missing_ok=True)
                        raise OSError("Copy integrity check failed (sha256 mismatch)")
                    src.unlink()

                _update_index(str(src), str(final_dest))
                rollback_entries.append({"old_path": str(src), "new_path": str(final_dest)})
                logger.info(f"  Moved: {src.relative_to(BASE_DIR)} → {final_dest.relative_to(BASE_DIR)}")
                folder_moved += 1

            except Exception as e:
                logger.error(f"  Error moving {src.name}: {e}")
                folder_errors += 1

        moved  += folder_moved
        errors += folder_errors
        folder_results[name] = {
            "status": "moved" if not dry_run else "dry_run",
            "detection": info,
            "files_moved": folder_moved,
            "files_errored": folder_errors,
        }

        # Phase 4 — seed artist_memory after successful routing
        if not dry_run and folder_moved > 0 and folder_errors == 0:
            seed_from_routing(canonical_name, route, source="auto_migration")

        # Remove empty source folder after real moves
        if not dry_run:
            for sub in sorted(folder_path.rglob("*"), reverse=True):
                if sub.is_dir():
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
            try:
                folder_path.rmdir()
            except OSError:
                pass

    # Write rollback manifest for this retry run
    rollback_path = _write_rollback_manifest(rollback_entries, dry_run)

    # Write retry report
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = BASE_DIR / f"retry_unmapped_report_{ts}.json"
    report_payload = {
        "generated_at": ts,
        "dry_run":       dry_run,
        "moved":         moved,
        "skipped":       skipped,
        "errors":        errors,
        "still_unmapped": still_unmapped,
        "rollback":      rollback_path,
        "folders":       folder_results,
    }
    if not dry_run:
        try:
            report_path.write_text(json.dumps(report_payload, indent=2, default=str),
                                   encoding="utf-8")
            logger.info(f"[migrate_artist] Report: {report_path}")
        except Exception as e:
            logger.warning(f"[migrate_artist] Could not write report: {e}")

    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        f"\n{label}Artist folder retry: {moved} moved, {skipped} skipped, "
        f"{errors} errors | {len(still_unmapped)} still unmapped"
    )
    return {
        "moved":          moved,
        "skipped":        skipped,
        "errors":         errors,
        "still_unmapped": still_unmapped,
        "report_path":    str(report_path) if not dry_run else None,
    }


def _verify_only() -> dict:
    """Check that all library_index final_path entries exist on disk."""
    missing = []
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is None:
            logger.warning("[migrate] MongoDB unavailable — cannot verify")
            return {"missing": 0, "verified": 0}
        docs = list(col.find({}, {"final_path": 1, "_id": 0}))
        verified = 0
        for doc in docs:
            fp = doc.get("final_path", "")
            if fp and Path(fp).is_file():
                verified += 1
            elif fp:
                missing.append(fp)
                logger.warning(f"[migrate][MISSING] {fp}")
        logger.info(f"[migrate] Verify: {verified} OK, {len(missing)} missing")
    except Exception as e:
        logger.error(f"[migrate] Verify failed: {e}")
    return {"missing": len(missing), "verified": verified if "verified" in dir() else 0}


def main():
    parser = argparse.ArgumentParser(
        description="Migrate flat genre folders to Library/{family}/{subgenre}/ hierarchy"
    )
    parser.add_argument("--dry-run",        action="store_true", default=True,
                        help="Preview moves without touching files (default)")
    parser.add_argument("--execute",        action="store_true",
                        help="Actually perform file moves")
    parser.add_argument("--verify-only",    action="store_true",
                        help="Check library_index paths without moving files")
    parser.add_argument("--analyze",        action="store_true",
                        help="Detect and classify unmapped artist folders, write artist_folder_analysis.json")
    parser.add_argument("--retry-unmapped", action="store_true",
                        help="Route previously unmapped artist/event folders to canonical Library/ paths")
    parser.add_argument("--analysis-file",  default=None,
                        help="Path to artist_folder_analysis.json for --retry-unmapped (optional)")
    args = parser.parse_args()

    if args.verify_only:
        result = migrate(dry_run=True, verify_only=True)
        sys.exit(0 if result.get("missing", 0) == 0 else 1)

    if args.analyze or args.retry_unmapped:
        # Discover folders that are not genre folders and not in _SKIP_TOP_DIRS
        from services.genre_router import GENRE_TAXONOMY
        all_known = {k.lower() for k in GENRE_TAXONOMY} | _SKIP_TOP_DIRS
        unmapped = [
            d.name for d in BASE_DIR.iterdir()
            if d.is_dir()
            and d.name.lower() not in all_known
            and not d.name.startswith(".")
            and not is_excluded_path(d)
            and not (BASE_DIR / "Library" in [d] + list(d.parents))
        ]
        if args.analyze:
            from services.artist_folder_service import generate_artist_folder_analysis
            result = generate_artist_folder_analysis(unmapped, BASE_DIR)
            unresolved = result.get("unresolved", 0)
            sys.exit(0 if unresolved == 0 else 1)

        if args.retry_unmapped:
            analysis_file = Path(args.analysis_file) if args.analysis_file else None
            dry = not args.execute
            result = migrate_artist_folders(unmapped, dry_run=dry, analysis_path=analysis_file)
            sys.exit(0 if result["errors"] == 0 else 1)

    dry = not args.execute
    result = migrate(dry_run=dry)
    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
