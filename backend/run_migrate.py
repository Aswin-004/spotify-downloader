"""
Music Library Migrator — CLI Runner
=====================================
Usage:
  python run_migrate.py                               # interactive, uses env BASE_DOWNLOAD_DIR
  python run_migrate.py --source /src --dest /dest   # explicit paths
  python run_migrate.py --dry-run                     # preview only, no file moves
  python run_migrate.py --undo logs/migrate_undo_<ts>.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Ensure Unicode artist names render on Windows terminals (e.g. cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from services.library_migrator import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOGS_DIR,
    build_report_text,
    migrate_library,
    undo_migration,
)
from config import config as app_config


def _default_source() -> Path:
    return Path(app_config.BASE_DOWNLOAD_DIR)


def _default_dest() -> Path:
    return Path(app_config.BASE_DOWNLOAD_DIR + "_organised")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reorganise music library into language/genre folders."
    )
    parser.add_argument("--source", type=Path, default=None,
                        help=f"Source directory (default: BASE_DOWNLOAD_DIR)")
    parser.add_argument("--dest", type=Path, default=None,
                        help=f"Destination directory (default: BASE_DOWNLOAD_DIR_organised)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help=f"artist_categories.json path (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would move — no files are touched")
    parser.add_argument("--undo", type=Path, default=None,
                        help="Undo a previous run using its undo log JSON")
    args = parser.parse_args()

    # ── Undo mode ─────────────────────────────────────────────────
    if args.undo:
        print(f"Undoing migration from: {args.undo}")
        undo_migration(args.undo)
        return

    source = args.source or _default_source()
    dest   = args.dest   or _default_dest()

    if not source.exists():
        print(f"ERROR: Source directory does not exist: {source}")
        sys.exit(1)

    print(f"Source : {source}")
    print(f"Dest   : {dest}")
    print(f"Config : {args.config}")
    if args.dry_run:
        print("Mode   : DRY RUN (no files will be moved)\n")
    else:
        print()

    def progress_cb(done: int, total: int) -> None:
        pct = int(done / total * 100) if total else 0
        print(f"\r  Progress: {done}/{total} ({pct}%)", end="", flush=True)
        if done == total:
            print()

    result = migrate_library(
        source=source,
        dest=dest,
        config_path=args.config,
        interactive=not args.dry_run,
        dry_run=args.dry_run,
        logs_dir=DEFAULT_LOGS_DIR,
        progress_cb=progress_cb,
    )

    print()
    print(build_report_text(
        category_stats=result.category_stats,
        errors=result.errors,
        skipped_artists=result.skipped_artists,
        undo_log_path=result.undo_log_path,
        duration_seconds=result.duration_seconds,
        html=False,
    ))

    if result.errors:
        print("\nERRORS:")
        for e in result.errors:
            print(f"  {e['file']}: {e['error']}")

    if result.non_empty_source_folders:
        print("\nNON-EMPTY SOURCE FOLDERS (left untouched):")
        for folder in result.non_empty_source_folders:
            print(f"  {folder}/")


if __name__ == "__main__":
    main()
