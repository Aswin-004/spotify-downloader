"""
backfill_lastfm.py — Last.fm metadata backfill for library_index.

Incremental, idempotent, resumable.  Enriches tracks that are missing
audio_features.lastfm_genre without touching file locations or routing.

Usage:
  python backfill_lastfm.py               # process up to 200 tracks (default)
  python backfill_lastfm.py --limit 500   # process up to 500 tracks
  python backfill_lastfm.py --limit 0     # process all remaining (may exceed 5-min UI timeout)
  python backfill_lastfm.py --dry-run     # preview counts only — no DB writes
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# ── CLI ───────────────────────────────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv
LIMIT = 200  # safe default: ~50 s at 4 req/s, well inside the 5-min UI timeout
for _i, _a in enumerate(sys.argv[1:], 1):
    if _a == "--limit" and _i < len(sys.argv) - 1:
        try:
            LIMIT = int(sys.argv[_i + 1])
        except (ValueError, IndexError):
            pass

# ── Checkpoint (atomic write) ─────────────────────────────────────────────────
_CHECKPOINT = Path(__file__).parent / ".lastfm_backfill_checkpoint.json"


def _load_ckpt() -> dict:
    try:
        return json.loads(_CHECKPOINT.read_text())
    except Exception:
        return {}


def _save_ckpt(data: dict) -> None:
    tmp = _CHECKPOINT.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_CHECKPOINT)
    except Exception:
        pass


# ── MongoDB query constants ───────────────────────────────────────────────────
# Tracks not yet attempted — audio_features.lastfm_genre field absent entirely
_Q_PENDING = {"audio_features.lastfm_genre": {"$exists": False}}

# Tracks where a previous run found no Last.fm data (empty-string sentinel).
# These are excluded from the next run automatically (field exists = "").
# Use {"$set": {"audio_features.lastfm_genre": None}} to force a re-attempt.
_Q_NO_DATA = {"audio_features.lastfm_genre": ""}

# Placeholder artist values — never useful for Last.fm lookup
# Also includes genre labels stored as artist (corrupted metadata pattern in this library)
_PLACEHOLDERS = frozenset({
    "unknown", "various artists", "various", "n/a", "va", "",
    # Genre labels stored as artist field (corrupted Spotify metadata)
    "electronic", "indian", "bollywood", "hiphop", "hip hop",
    "dance", "punjabi", "house", "techno", "grime", "latin",
    "r&b", "pop", "rock", "metal", "classical", "jazz",
})


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    from services.lastfm_service import enrich_from_lastfm, enrich_cache_size, is_available
    from database import get_library_index_collection

    if not is_available():
        print("✗ LASTFM_API_KEY not configured — aborting")
        return 1

    lib = get_library_index_collection()

    # ── Counts for header ─────────────────────────────────────────────────────
    n_pending  = lib.count_documents(_Q_PENDING)
    n_no_data  = lib.count_documents(_Q_NO_DATA)
    n_enriched = lib.count_documents(
        {"audio_features.lastfm_genre": {"$exists": True, "$ne": ""}}
    )

    ckpt = _load_ckpt()

    print("=== Last.fm Metadata Backfill ===")
    print(f"Not yet tried    : {n_pending}")
    print(f"Tried / no data  : {n_no_data}  (sentinel written; will not be retried)")
    print(f"Already enriched : {n_enriched}")
    print(f"Batch limit      : {LIMIT if LIMIT else 'unlimited (all remaining)'}")
    print(f"Mode             : {'DRY RUN — no writes' if DRY_RUN else 'live'}")
    if ckpt.get("runs"):
        print(
            f"Cumulative       : {ckpt.get('enriched_total', 0)} enriched, "
            f"{ckpt.get('skipped_total', 0)} skipped "
            f"across {ckpt['runs']} previous run(s)"
        )
    print()

    if n_pending == 0:
        print("✓ All tracks have been attempted — nothing to do")
        if n_no_data:
            print(f"  ({n_no_data} tracks had no Last.fm data; clear sentinel to force retry)")
        return 0

    # ── Query cursor ──────────────────────────────────────────────────────────
    cursor = lib.find(
        _Q_PENDING,
        {"identity_key": 1, "title": 1, "artist": 1, "genre_folder": 1},
    )
    if LIMIT:
        cursor = cursor.limit(LIMIT)

    processed = enriched = skipped = failed = 0
    t_start = time.monotonic()

    for doc in cursor:
        ik     = (doc.get("identity_key") or "").strip()
        artist = (doc.get("artist")       or "").strip()
        title  = (doc.get("title")        or "").strip()
        gf     = str(doc.get("genre_folder") or "")

        # ── Skip placeholder artists ──────────────────────────────────────────
        if not ik or artist.lower() in _PLACEHOLDERS:
            if ik and not DRY_RUN:
                # Write sentinel so this track is excluded on every future run
                try:
                    lib.update_one(
                        {"identity_key": ik},
                        {"$set": {"audio_features.lastfm_genre": ""}},
                        upsert=False,
                    )
                except Exception:
                    pass
            skipped += 1
            processed += 1
            continue

        if DRY_RUN:
            print(f"  [dry] {artist[:22]:22} / {title[:28]:28} | {gf[-22:]}")
            processed += 1
            continue

        # ── Enrich (rate-limited internally at 4 req/s) ───────────────────────
        data = enrich_from_lastfm(artist, title)

        if data:
            try:
                upd = lib.update_one(
                    {"identity_key": ik},
                    {"$set": {f"audio_features.{k}": v for k, v in data.items()}},
                    upsert=False,
                )
                if upd.matched_count:
                    enriched += 1
                else:
                    # identity_key not found — index out of sync, skip silently
                    failed += 1
            except Exception as exc:
                print(f"  [warn] DB write failed for {ik[:24]}: {exc}")
                failed += 1
        else:
            # No Last.fm data — write empty-string sentinel to avoid re-querying
            try:
                lib.update_one(
                    {"identity_key": ik},
                    {"$set": {"audio_features.lastfm_genre": ""}},
                    upsert=False,
                )
            except Exception:
                pass
            skipped += 1

        processed += 1

        # Progress log every 10 tracks
        if processed % 10 == 0:
            elapsed = time.monotonic() - t_start
            rate = processed / elapsed if elapsed > 0 else 0
            print(
                f"  [{processed:4}/{n_pending}]"
                f"  enriched={enriched}"
                f"  skipped={skipped}"
                f"  failed={failed}"
                f"  rate={rate:.1f}/s"
                f"  cache={enrich_cache_size()}"
            )

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed  = time.monotonic() - t_start
    n_remain = lib.count_documents(_Q_PENDING)

    print()
    print("=== Run Summary ===")
    print(f"Processed : {processed}")
    print(f"Enriched  : {enriched}")
    print(f"Skipped   : {skipped}  (no Last.fm data or placeholder artist)")
    print(f"Failed    : {failed}  (identity_key missing or DB write error)")
    if elapsed > 0.1:
        print(f"Elapsed   : {elapsed:.1f}s  ({processed / elapsed:.1f} tracks/s)")
    print(f"Remaining : {n_remain}  (tracks still pending enrichment)")
    print(f"Cache     : {enrich_cache_size()} in-process entries")
    print()
    if n_remain == 0:
        print("✓ Backfill complete — all eligible tracks have been processed")
    elif not DRY_RUN:
        print(f"Re-run to continue ({n_remain} tracks still pending)")

    # ── Checkpoint ────────────────────────────────────────────────────────────
    if not DRY_RUN:
        prev = _load_ckpt()
        _save_ckpt({
            "runs":           prev.get("runs", 0) + 1,
            "enriched_total": prev.get("enriched_total", 0) + enriched,
            "skipped_total":  prev.get("skipped_total", 0) + skipped,
            "failed_total":   prev.get("failed_total", 0) + failed,
            "remaining":      n_remain,
            "last_run":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed":      n_remain == 0,
        })

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
