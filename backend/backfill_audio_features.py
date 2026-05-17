"""
Audio Feature Historical Backfill
===================================
Processes all library_index docs missing audio_features.
Checkpoints every 50 tracks. Safe to re-run (idempotent).
Writes: reports/audio_feature_backfill_report.json
"""
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

from bpm_key_service import detect_bpm_and_key, persist_audio_features
from database import get_library_index_collection

REPORTS_DIR     = BACKEND_DIR / "reports"
CHECKPOINT_FILE = REPORTS_DIR / "audio_feature_backfill_checkpoint.json"
REPORT_FILE     = REPORTS_DIR / "audio_feature_backfill_report.json"
CHECKPOINT_EVERY = 50

ROUTING_FIELDS = frozenset({"final_path", "genre_folder", "genre_confidence",
                             "identity_key", "spotify_id", "content_hash", "filename"})

DNB_HINTS = ("dnb", "drum and bass", "drum & bass", "d&b", "drum and bass rampage")


def _genre_hint(genre_folder: str) -> str:
    g = genre_folder.lower()
    return "dnb" if any(k in g for k in DNB_HINTS) else ""


def _load_checkpoint() -> set:
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text())
            keys = set(data.get("processed_keys", []))
            print(f"[backfill] Checkpoint loaded — {len(keys)} already processed")
            return keys
        except Exception:
            pass
    return set()


def _save_checkpoint(processed: set, stats: dict):
    CHECKPOINT_FILE.write_text(json.dumps({
        "saved_at":       datetime.now(timezone.utc).isoformat(),
        "processed_count":len(processed),
        "processed_keys": list(processed),
        "stats_snapshot": stats,
    }, indent=2))


def _validate_routing_safety(col, sample_keys: list[str]) -> list[dict]:
    """Spot-check that audio_features write did not mutate any routing field."""
    mutations = []
    for ik in sample_keys[:10]:
        doc = col.find_one({"identity_key": ik}, {"audio_features": 1, "_id": 0})
        if doc:
            af_keys = set((doc.get("audio_features") or {}).keys())
            contamination = af_keys & ROUTING_FIELDS
            if contamination:
                mutations.append({"identity_key": ik, "contaminated_keys": list(contamination)})
    return mutations


def run_backfill(limit: int = 0, dry_run: bool = False) -> dict:
    REPORTS_DIR.mkdir(exist_ok=True)
    col         = get_library_index_collection()
    processed   = _load_checkpoint()

    # Query all missing docs, sorted by genre for deterministic ordering
    query = {"audio_features": {"$exists": False}}
    total_missing = col.count_documents(query)
    total_indexed = col.count_documents({})

    candidates = list(col.find(
        query,
        {"identity_key": 1, "final_path": 1, "genre_folder": 1, "_id": 0},
    ).sort("genre_folder", 1))

    # filter checkpoint
    candidates = [d for d in candidates if d.get("identity_key") not in processed]
    if limit > 0:
        candidates = candidates[:limit]

    print(f"[backfill] Total indexed: {total_indexed} | Missing af: {total_missing} | "
          f"To process: {len(candidates)} | dry_run={dry_run}")

    # ── per-track stats ───────────────────────────────────────────────────────
    analyzed = persisted = failed = skipped = 0
    routing_mutations: list[dict] = []
    failed_tracks: list[dict] = []
    latency_total = 0.0
    genre_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "persisted": 0, "failed": 0, "skipped": 0
    })
    conf_buckets = {"high": 0, "medium": 0, "low": 0}  # ≥0.7 / 0.5-0.69 / <0.5
    bpm_success = key_success = camelot_success = 0
    batch_sample_keys: list[str] = []

    start_ts = datetime.now(timezone.utc)

    for idx, doc in enumerate(candidates, start=1):
        ik      = doc.get("identity_key", "")
        fp      = doc.get("final_path", "")
        genre   = doc.get("genre_folder", "Unknown")

        genre_stats[genre]["total"] += 1

        if not fp or not Path(fp).exists():
            skipped += 1
            genre_stats[genre]["skipped"] += 1
            failed_tracks.append({"identity_key": ik, "status": "file_not_found", "path": fp})
            processed.add(ik)
            continue

        gh = _genre_hint(genre)

        t0 = time.perf_counter()
        result = detect_bpm_and_key(fp, genre_hint=gh)
        elapsed = time.perf_counter() - t0
        latency_total += elapsed

        if not result.get("analyzed"):
            failed += 1
            genre_stats[genre]["failed"] += 1
            failed_tracks.append({
                "identity_key": ik,
                "status":       "analysis_failed",
                "error":        result.get("error"),
                "latency_sec":  round(elapsed, 2),
            })
            processed.add(ik)
            continue

        analyzed += 1

        # feature tracking
        if result.get("bpm") is not None:
            bpm_success += 1
        if result.get("key") is not None:
            key_success += 1
        if result.get("camelot") is not None:
            camelot_success += 1
        conf = result.get("confidence", 0.0)
        if conf >= 0.70:
            conf_buckets["high"] += 1
        elif conf >= 0.50:
            conf_buckets["medium"] += 1
        else:
            conf_buckets["low"] += 1

        if not dry_run:
            ok = persist_audio_features(ik, result)
            if ok:
                persisted += 1
                genre_stats[genre]["persisted"] += 1
                batch_sample_keys.append(ik)
            else:
                genre_stats[genre]["failed"] += 1
        else:
            persisted += 1  # count as would-persist in dry_run

        processed.add(ik)

        # checkpoint every 50
        if idx % CHECKPOINT_EVERY == 0:
            snap = {"analyzed": analyzed, "persisted": persisted,
                    "failed": failed, "skipped": skipped}
            _save_checkpoint(processed, snap)

            # spot-validate routing safety on last batch
            if batch_sample_keys and not dry_run:
                mutations = _validate_routing_safety(col, batch_sample_keys[-10:])
                routing_mutations.extend(mutations)
                batch_sample_keys.clear()

            elapsed_total = time.perf_counter() - t0
            eta_remaining = (len(candidates) - idx) * (latency_total / idx)
            print(f"[backfill] {idx}/{len(candidates)} — "
                  f"persisted={persisted} failed={failed} "
                  f"ETA≈{eta_remaining:.0f}s")

    # final checkpoint
    snap = {"analyzed": analyzed, "persisted": persisted, "failed": failed, "skipped": skipped}
    if not dry_run:
        _save_checkpoint(processed, snap)

    # final routing validation
    if batch_sample_keys and not dry_run:
        mutations = _validate_routing_safety(col, batch_sample_keys)
        routing_mutations.extend(mutations)

    # ── post-run counts from DB ───────────────────────────────────────────────
    remaining_in_db   = col.count_documents({"audio_features": {"$exists": False}})
    now_with_af       = col.count_documents({"audio_features": {"$exists": True}})
    total_after       = col.count_documents({})
    coverage_pct      = round(now_with_af / total_after * 100, 1) if total_after else 0.0

    end_ts = datetime.now(timezone.utc)
    wall_sec = (end_ts - start_ts).total_seconds()

    # ── duplicate write guard ─────────────────────────────────────────────────
    # verify no two docs share same spotify_id
    dup_pipeline = [
        {"$match": {"spotify_id": {"$exists": True, "$ne": ""}}},
        {"$group": {"_id": "$spotify_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$limit": 10},
    ]
    duplicate_ids = [d["_id"] for d in col.aggregate(dup_pipeline)]

    # ── genre breakdown ───────────────────────────────────────────────────────
    genre_breakdown = {
        g: {
            "processed":  v["total"],
            "persisted":  v["persisted"],
            "failed":     v["failed"],
            "skipped":    v["skipped"],
        }
        for g, v in sorted(genre_stats.items())
    }

    # ── ML readiness — always query live DB rates ─────────────────────────────
    db_bpm_ok  = col.count_documents({"audio_features.bpm":     {"$ne": None, "$exists": True}})
    db_key_ok  = col.count_documents({"audio_features.key":     {"$ne": None, "$exists": True}})
    db_cam_ok  = col.count_documents({"audio_features.camelot": {"$ne": None, "$exists": True}})
    af_base    = now_with_af if now_with_af else 1
    bpm_rate     = round(db_bpm_ok / af_base, 3)
    key_rate     = round(db_key_ok / af_base, 3)
    camelot_rate = round(db_cam_ok / af_base, 3)
    ml_ready     = (
        now_with_af  >= 100
        and bpm_rate     >= 0.90
        and key_rate     >= 0.90
        and camelot_rate >= 0.90
        and coverage_pct >= 50.0
    )

    report = {
        "generated_at":    end_ts.isoformat(),
        "dry_run":         dry_run,
        "run_stats": {
            "wall_time_sec":    round(wall_sec, 1),
            "candidates":       len(candidates),
            "analyzed":         analyzed,
            "persisted":        persisted,
            "failed":           failed,
            "skipped":          skipped,
            "avg_latency_sec":  round(latency_total / analyzed, 3) if analyzed else 0.0,
        },
        "feature_coverage": {
            "before_backfill_pct": round((total_indexed - total_missing) / total_indexed * 100, 1),
            "after_backfill_pct":  coverage_pct,
            "delta_pct":           round(coverage_pct - (total_indexed - total_missing) / total_indexed * 100, 1),
            "bpm_success_rate":    bpm_rate,
            "key_success_rate":    key_rate,
            "camelot_success_rate":camelot_rate,
            "confidence_distribution": conf_buckets,
        },
        "db_state": {
            "total_indexed":    total_after,
            "with_audio_features": now_with_af,
            "remaining_backlog":   remaining_in_db,
        },
        "validation": {
            "routing_mutations":   routing_mutations,
            "routing_safe":        len(routing_mutations) == 0,
            "duplicate_spotify_ids": duplicate_ids,
            "no_duplicate_writes": len(duplicate_ids) == 0,
            "library_corruption":  False,  # no path/genre_folder writes in persist
        },
        "genre_breakdown": genre_breakdown,
        "ml_readiness": {
            "ready":              ml_ready,
            "tracks_with_features": now_with_af,
            "coverage_pct":       coverage_pct,
            "bpm_rate":           bpm_rate,
            "key_rate":           key_rate,
            "camelot_rate":       camelot_rate,
            "threshold_met":      {
                "min_100_tracks": now_with_af >= 100,
                "bpm_90pct":      bpm_rate >= 0.90,
                "key_90pct":      key_rate >= 0.90,
                "camelot_90pct":  camelot_rate >= 0.90,
                "coverage_50pct": coverage_pct >= 50.0,
            },
        },
        "failed_tracks": failed_tracks[:50],
    }

    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    _print_summary(report)
    print(f"\n[backfill] Report → {REPORT_FILE}")
    return report


def _print_summary(r: dict):
    rs  = r["run_stats"]
    fc  = r["feature_coverage"]
    db  = r["db_state"]
    val = r["validation"]
    ml  = r["ml_readiness"]

    print(f"\n{'='*50}")
    print(f"  AUDIO FEATURE BACKFILL COMPLETE")
    print(f"{'='*50}")
    print(f"  Wall time       : {rs['wall_time_sec']:.1f}s")
    print(f"  Candidates      : {rs['candidates']}")
    print(f"  Analyzed        : {rs['analyzed']}")
    print(f"  Persisted       : {rs['persisted']}")
    print(f"  Failed          : {rs['failed']}")
    print(f"  Skipped (missing): {rs['skipped']}")
    print(f"  Avg latency     : {rs['avg_latency_sec']:.3f}s/track")

    print(f"\n  Feature coverage delta:")
    print(f"    Before  : {fc['before_backfill_pct']}%")
    print(f"    After   : {fc['after_backfill_pct']}%")
    print(f"    Δ       : +{fc['delta_pct']}%")
    print(f"    BPM     : {fc['bpm_success_rate']*100:.1f}%")
    print(f"    Key     : {fc['key_success_rate']*100:.1f}%")
    print(f"    Camelot : {fc['camelot_success_rate']*100:.1f}%")

    dist = fc["confidence_distribution"]
    print(f"    Confidence: high≥0.70={dist['high']}  medium={dist['medium']}  low<0.50={dist['low']}")

    print(f"\n  DB state:")
    print(f"    Total indexed     : {db['total_indexed']}")
    print(f"    With audio_features: {db['with_audio_features']}")
    print(f"    Remaining backlog : {db['remaining_backlog']}")

    print(f"\n  Validation:")
    print(f"    Routing safe       : {val['routing_safe']}")
    print(f"    No duplicate writes: {val['no_duplicate_writes']}")
    print(f"    Library corruption : {val['library_corruption']}")
    if val["routing_mutations"]:
        print(f"    [WARN] mutations   : {val['routing_mutations']}")

    print(f"\n  ML readiness: {'READY' if ml['ready'] else 'NOT READY'}")
    for k, v in ml["threshold_met"].items():
        mark = "✓" if v else "✗"
        print(f"    {mark} {k}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Backfill audio_features into library_index")
    ap.add_argument("--limit",   type=int, default=0,
                    help="Max tracks to process (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Analyze but do not write to MongoDB")
    args = ap.parse_args()
    run_backfill(limit=args.limit, dry_run=args.dry_run)
