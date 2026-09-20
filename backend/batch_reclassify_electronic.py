#!/usr/bin/env python3
"""
Batch-reclassify catch-all tracks sitting in Library/Electronic that the
lightweight library_audit.py can't resolve (no ARTIST_GENRE_OVERRIDE /
knowledge-base match) and that the maintenance worker's auto-retagger can't
reach (missing the TXXX:routing_source=catchall tag).

Runs each file through the SAME fallback chain as the web UI's "retag
catch-all" button (services/catchall_reclassifier.py):
  ARTIST_GENRE_OVERRIDE -> Spotify artist search -> Spotify title search ->
  EVIDENCE VOTE (Last.fm + MusicBrainz + remixer/script/title hints + BPM,
  AcoustID fingerprint only if those can't settle it; services/genre_evidence.py)
  -> Groq LLM classification from title+artist (last resort). That last step
  lives in services/gemini_service.py — the module/field names ("gemini_*") are
  stale from before it was migrated to Groq; it calls the Groq API
  (config.GROQ_API_KEY) exclusively, not Gemini, and has no hard daily quota.

The vote ABSTAINS unless the evidence is strong and clearly ahead, so a track it
can't place stays in Electronic (the "unresolved" list shows why). An answer that
rests only on artist-level tags, a title search or a Groq guess is UNVERIFIED:
it is listed for review and NOT moved by --execute unless you add
--include-ai-guesses. Expect roughly 5-8 s per track (MusicBrainz allows 1
request/s); answers are cached on disk, so a re-run is fast.

SAFE BY DEFAULT: dry-run only, touches nothing, until you pass --execute.

Usage:
    # Dry-run (default) — print the move plan, touch nothing
    python batch_reclassify_electronic.py

    # Limit to the first 20 files (useful for a quick test run)
    python batch_reclassify_electronic.py --limit 20

    # Skip the Groq AI classification step entirely (free-tier-only chain;
    # some tracks will stay unresolved rather than calling Groq at all)
    python batch_reclassify_electronic.py --skip-ai

    # Execute moves (after reviewing dry-run output)
    python batch_reclassify_electronic.py --execute

    # Point at a different folder
    python batch_reclassify_electronic.py --folder "Library/Electronic"
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from mutagen.id3 import ID3, ID3NoHeaderError

BASE_DOWNLOAD_DIR = Path(config.BASE_DOWNLOAD_DIR)

# Small pause between tracks so we don't hammer Spotify/Last.fm/MusicBrainz
# rate limits across a couple hundred files in one run.
DELAY_BETWEEN_TRACKS_SEC = 1.0


def main():
    parser = argparse.ArgumentParser(description="Batch-reclassify catch-all tracks in Library/Electronic")
    parser.add_argument("--execute", action="store_true", help="Actually move files (default: dry-run)")
    parser.add_argument("--folder", default="Library/Electronic", help="Folder relative to BASE_DOWNLOAD_DIR to scan")
    parser.add_argument("--limit", type=int, default=None, help="Max number of files to process this run")
    parser.add_argument("--skip-ai", action="store_true", help="Never call the Groq AI classification step (last resort, step 7)")
    parser.add_argument(
        "--include-ai-guesses", action="store_true",
        help="With --execute, also move UNVERIFIED matches: a Groq text guess, a Spotify "
             "title-search hit, or a genre vote resting only on artist-level tags. Without "
             "this flag, --execute still classifies and reports them but does not move "
             "them, so you can review them separately.",
    )
    parser.add_argument("--only-tag", default=None, help="Only process files whose filename contains this substring (quick single-track test)")
    args = parser.parse_args()

    from services.catchall_reclassifier import classify_and_route_catchall_track

    scan_dir = BASE_DOWNLOAD_DIR / args.folder
    if not scan_dir.is_dir():
        print(f"Not found: {scan_dir}")
        return

    files = sorted(scan_dir.glob("*.mp3"))
    if args.only_tag:
        files = [f for f in files if args.only_tag.lower() in f.name.lower()]
    if args.limit:
        files = files[: args.limit]

    dry_run = not args.execute
    hold_ai_matches = not args.include_ai_guesses

    print("=" * 70)
    print("BATCH RECLASSIFY — Library/Electronic catch-all tracks")
    print(f"  Folder:            {scan_dir}")
    print(f"  Mode:              {'DRY-RUN (no files moved)' if dry_run else 'EXECUTE'}")
    print(f"  Files to scan:     {len(files)}")
    print(f"  Skip AI step:      {'yes' if args.skip_ai else 'no'}")
    if not dry_run:
        print(f"  Move AI guesses:   {'yes' if args.include_ai_guesses else 'no (held for review — use --include-ai-guesses to move them too)'}")
    print("=" * 70)

    verified_moves, ai_moves, ai_held, unresolved, quota_hits, errors = [], [], [], [], [], []

    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name} ...", end=" ", flush=True)
        try:
            result = classify_and_route_catchall_track(
                str(f), dry_run=dry_run, skip_ai_step=args.skip_ai, hold_ai_matches=hold_ai_matches,
            )
        except Exception as e:
            print(f"ERROR: {e}")
            errors.append((f.name, str(e)))
            continue

        entry = (f.name, result["new_folder"], result["source"])
        if result["reason"] == "already in correct folder":
            print("already correct, marked reviewed" if not dry_run else "already correct")
        elif result["quota_exhausted"]:
            print("AI STEP RATE-LIMITED/QUOTA EXHAUSTED — stopping run")
            quota_hits.append(f.name)
            break
        elif result["moved"] and result["ai_guess"] and not dry_run and hold_ai_matches:
            print(f"-> UNVERIFIED, held for review -> {result['new_folder']} (via {result['source']})")
            ai_held.append(entry)
        elif result["moved"]:
            tag = " [UNVERIFIED]" if result["ai_guess"] else ""
            verb = "would move" if dry_run else "moved"
            conf = f", {result['confidence']:.0%}" if result.get("confidence") else ""
            print(f"-> {verb} -> {result['new_folder']} (via {result['source']}{conf}){tag}")
            (ai_moves if result["ai_guess"] else verified_moves).append(entry)
        else:
            why = f" — {result['evidence']}" if result.get("evidence") else ""
            print(f"unresolved ({result['reason']}){why}")
            unresolved.append(f.name)

        time.sleep(DELAY_BETWEEN_TRACKS_SEC)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    verb = "Would move" if dry_run else "Moved"
    print(f"{verb} (VERIFIED — artist override / track-level Last.fm or MusicBrainz evidence / fingerprint): {len(verified_moves)}")
    for name, folder, source in verified_moves[:30]:
        print(f"  {name}  ->  {folder}  ({source})")
    if len(verified_moves) > 30:
        print(f"  ... and {len(verified_moves) - 30} more")

    print(f"\n{verb} (UNVERIFIED — artist-level tags only, title-search match or Groq guess; spot-check every one): {len(ai_moves)}")
    for name, folder, source in ai_moves[:30]:
        print(f"  {name}  ->  {folder}  ({source})")
    if len(ai_moves) > 30:
        print(f"  ... and {len(ai_moves) - 30} more")

    if ai_held:
        print(f"\nHeld for review (UNVERIFIED, NOT moved — rerun with --include-ai-guesses to move): {len(ai_held)}")
        for name, folder, source in ai_held[:30]:
            print(f"  {name}  ->  {folder}  ({source})")
        if len(ai_held) > 30:
            print(f"  ... and {len(ai_held) - 30} more")

    print(f"\nUnresolved:   {len(unresolved)}")
    print(f"Errors:       {len(errors)}")
    for name, err in errors[:10]:
        print(f"  {name}: {err}")
    if quota_hits:
        print(f"Stopped early: AI step rate-limited/exhausted after {i}/{len(files)} files")


if __name__ == "__main__":
    main()
