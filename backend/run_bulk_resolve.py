"""
NeedsReview Bulk Resolver — CLI Runner
=======================================
Moves high-confidence Indian artists out of NeedsReview/ into the correct
Library/ path using the curated NEEDS_REVIEW_RESOLUTION_MAP (conf 0.95).

Usage
-----
    # Preview — nothing moves
    python run_bulk_resolve.py

    # Live move
    python run_bulk_resolve.py --live

    # Diagnose one artist (always read-only)
    python run_bulk_resolve.py --diagnose "A.R. Rahman"

    # Override base directory
    python run_bulk_resolve.py --live --base-dir /path/to/DJ_music
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from config import config as app_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-resolve NeedsReview artists into Library/."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Execute moves (default: dry-run preview only)",
    )
    parser.add_argument(
        "--base-dir", type=Path, default=None, metavar="DIR",
        help="Override BASE_DOWNLOAD_DIR",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.90, metavar="CONF",
        help="Minimum confidence to move (default: 0.90)",
    )
    parser.add_argument(
        "--diagnose", type=str, default=None, metavar="ARTIST",
        help="Trace a single artist through every pipeline layer (read-only)",
    )
    args = parser.parse_args()

    base = args.base_dir or Path(app_config.BASE_DOWNLOAD_DIR)
    if not base.is_dir():
        print(f"ERROR: base directory not found: {base}")
        sys.exit(1)

    from services.reclassification_service import (
        bulk_resolve_needsreview_artists,
        diagnose_needsreview_move,
    )

    # ── Diagnose mode ─────────────────────────────────────────────────────────
    if args.diagnose:
        diag = diagnose_needsreview_move(args.diagnose, base_dir=base, dry_run=not args.live)
        print(json.dumps({k: v for k, v in diag.items() if k != "layers"}, indent=2))
        if diag.get("blocked_reason"):
            print(f"\nBLOCKED: {diag['blocked_reason']}")
        if diag.get("report_path"):
            print(f"\nReport → {diag['report_path']}")
        sys.exit(0)

    # ── Bulk resolve ──────────────────────────────────────────────────────────
    dry_run = not args.live
    mode    = "DRY RUN (preview)" if dry_run else "LIVE (files will move)"
    print(f"Base dir  : {base}")
    print(f"Mode      : {mode}")
    print(f"Threshold : {args.threshold}")
    print()

    result = bulk_resolve_needsreview_artists(
        base_dir=base,
        dry_run=dry_run,
        confidence_threshold=args.threshold,
    )

    print(f"Artists resolved : {result['moved_artists']}")
    print(f"Files moved      : {result['total_files_moved']}")
    print(f"Skipped          : {result['skipped_artists']}")
    print(f"Unresolved       : {result['unresolved_artists']}")
    print(f"Errors           : {result['errors']}")
    if result.get("report_path"):
        print(f"Report           : {result['report_path']}")

    if dry_run:
        print("\nRe-run with --live to execute the moves.")


if __name__ == "__main__":
    main()
