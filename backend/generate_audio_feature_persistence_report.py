"""
Audio Feature Persistence Report
=================================
Queries library_index for documents with audio_features sub-document.
Validates: no routing/path mutations, no duplicate writes, feature coverage.
Writes: reports/audio_feature_persistence_report.json
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

from database import get_library_index_collection

REPORTS_DIR = BACKEND_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

ROUTING_FIELDS = {"final_path", "genre_folder", "genre_confidence", "identity_key",
                  "spotify_id", "content_hash", "filename"}
EXPECTED_AF_KEYS = {"bpm", "key", "key_root", "key_mode", "camelot", "confidence",
                    "analysis_source", "analysis_version", "analysis_timestamp"}
OPTIONAL_AF_KEYS = {"duration_sec", "rms_energy", "spectral_centroid_mean", "zero_crossing_rate"}


def run_report() -> dict:
    col = get_library_index_collection()
    total_indexed = col.count_documents({})
    with_af = col.count_documents({"audio_features": {"$exists": True}})
    without_af = total_indexed - with_af

    analyzed_tracks = []
    failed_writes: list[dict] = []
    genre_coverage: dict[str, dict] = defaultdict(lambda: {"total": 0, "with_af": 0})
    version_counts: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    optional_present: dict[str, int] = {k: 0 for k in OPTIONAL_AF_KEYS}
    bpm_null_count = 0
    key_null_count = 0
    camelot_null_count = 0
    latency_sum = 0.0
    latency_n = 0

    # ── validation state ────────────────────────────────────────────────────
    routing_mutations: list[dict] = []  # docs where audio_features contains routing fields
    duplicate_spotify_ids: list[str] = []

    seen_sp_ids: dict[str, str] = {}  # spotify_id → identity_key (dedup check)

    cursor = col.find(
        {},
        {
            "identity_key": 1, "spotify_id": 1, "final_path": 1,
            "genre_folder": 1, "audio_features": 1, "_id": 0,
        },
    )

    for doc in cursor:
        ik = doc.get("identity_key", "")
        sp_id = doc.get("spotify_id", "")
        genre = doc.get("genre_folder", "Unknown")
        genre_coverage[genre]["total"] += 1

        # duplicate spotify_id guard
        if sp_id:
            if sp_id in seen_sp_ids and seen_sp_ids[sp_id] != ik:
                duplicate_spotify_ids.append(sp_id)
            else:
                seen_sp_ids[sp_id] = ik

        af = doc.get("audio_features")
        if not af:
            continue

        genre_coverage[genre]["with_af"] += 1

        # routing mutation guard — audio_features must not contain routing fields
        contamination = ROUTING_FIELDS & set(af.keys())
        if contamination:
            routing_mutations.append({"identity_key": ik, "contaminated_keys": list(contamination)})

        version_counts[af.get("analysis_version", "unknown")] += 1
        source_counts[af.get("analysis_source", "unknown")] += 1

        if af.get("bpm") is None:
            bpm_null_count += 1
        if af.get("key") is None:
            key_null_count += 1
        if af.get("camelot") is None:
            camelot_null_count += 1

        for opt in OPTIONAL_AF_KEYS:
            if af.get(opt) is not None:
                optional_present[opt] += 1

        analyzed_tracks.append({
            "identity_key":    ik,
            "bpm":             af.get("bpm"),
            "key":             af.get("key"),
            "camelot":         af.get("camelot"),
            "analysis_source": af.get("analysis_source"),
            "analysis_version":af.get("analysis_version"),
            "timestamp":       af.get("analysis_timestamp"),
            "genre":           genre,
        })

    # ── feature coverage rates ───────────────────────────────────────────────
    af_count = len(analyzed_tracks)
    bpm_success_rate    = (af_count - bpm_null_count)    / af_count if af_count else 0.0
    key_success_rate    = (af_count - key_null_count)    / af_count if af_count else 0.0
    camelot_success_rate= (af_count - camelot_null_count)/ af_count if af_count else 0.0

    # ── ml readiness ────────────────────────────────────────────────────────
    optional_coverage = {k: round(v / af_count, 3) if af_count else 0.0
                         for k, v in optional_present.items()}
    all_optional_covered = all(v > 0.5 for v in optional_coverage.values())
    ml_ready = (
        af_count > 0
        and bpm_success_rate >= 0.90
        and key_success_rate >= 0.90
        and camelot_success_rate >= 0.90
        and all_optional_covered
    )

    genre_breakdown = {
        g: {
            "total":       v["total"],
            "with_features": v["with_af"],
            "coverage_pct":  round(v["with_af"] / v["total"] * 100, 1) if v["total"] else 0.0,
        }
        for g, v in sorted(genre_coverage.items())
    }

    report = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_indexed":          total_indexed,
            "with_audio_features":    with_af,
            "without_audio_features": without_af,
            "coverage_pct":           round(with_af / total_indexed * 100, 1) if total_indexed else 0.0,
            "bpm_success_rate":       round(bpm_success_rate, 3),
            "key_success_rate":       round(key_success_rate, 3),
            "camelot_success_rate":   round(camelot_success_rate, 3),
        },
        "persisted_schema": {
            "sub_document":   "audio_features",
            "core_fields":    sorted(EXPECTED_AF_KEYS),
            "optional_fields":sorted(OPTIONAL_AF_KEYS),
            "analysis_version": list(version_counts.keys()),
            "analysis_source":  list(source_counts.keys()),
        },
        "optional_feature_coverage": optional_coverage,
        "genre_breakdown": genre_breakdown,
        "db_update_counts": {
            "total_persisted":    with_af,
            "by_version":         dict(version_counts),
            "by_source":          dict(source_counts),
        },
        "validation": {
            "routing_mutations":     routing_mutations,
            "duplicate_spotify_ids": duplicate_spotify_ids,
            "routing_safe":          len(routing_mutations) == 0,
            "no_duplicate_writes":   len(duplicate_spotify_ids) == 0,
        },
        "ml_readiness": {
            "ready":           ml_ready,
            "reason":          (
                "All core features present with ≥90% coverage and optional features ≥50% coverage"
                if ml_ready else
                "Insufficient coverage — run more ingestion cycles to populate audio_features"
            ),
            "features_available": sorted(
                EXPECTED_AF_KEYS | OPTIONAL_AF_KEYS
            ),
        },
        "analyzed_tracks_sample": analyzed_tracks[:50],
        "failed_writes": failed_writes,
    }

    out = REPORTS_DIR / "audio_feature_persistence_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"[report] Written → {out}")
    _print_summary(report)
    return report


def _print_summary(r: dict):
    s = r["summary"]
    v = r["validation"]
    ml = r["ml_readiness"]
    print(f"\n=== AUDIO FEATURE PERSISTENCE REPORT ===")
    print(f"  Total indexed      : {s['total_indexed']}")
    print(f"  With audio_features: {s['with_audio_features']} ({s['coverage_pct']}%)")
    print(f"  BPM success rate   : {s['bpm_success_rate']*100:.1f}%")
    print(f"  Key success rate   : {s['key_success_rate']*100:.1f}%")
    print(f"  Camelot success    : {s['camelot_success_rate']*100:.1f}%")
    print(f"\n  Optional coverage:")
    for k, v2 in r["optional_feature_coverage"].items():
        print(f"    {k:<30} {v2*100:.1f}%")
    print(f"\n  Routing safe       : {v['routing_safe']}")
    print(f"  No duplicate writes: {v['no_duplicate_writes']}")
    if v["routing_mutations"]:
        print(f"  [WARN] routing mutations: {v['routing_mutations']}")
    if v["duplicate_spotify_ids"]:
        print(f"  [WARN] duplicate spotify_ids: {len(v['duplicate_spotify_ids'])}")
    print(f"\n  ML ready: {ml['ready']}")
    print(f"  {ml['reason']}")
    print(f"\n  Genre breakdown:")
    for g, gs in r["genre_breakdown"].items():
        if gs["total"] > 0:
            print(f"    {g:<30} {gs['with_features']:>4}/{gs['total']:>4}  ({gs['coverage_pct']}%)")


def run_backfill(limit: int = 20) -> dict:
    """
    Backfill audio_features for existing library_index docs missing the field.
    Processes up to `limit` tracks. Safe: additive only, skips already-filled docs.
    """
    import time
    from bpm_key_service import detect_bpm_and_key, persist_audio_features

    col = get_library_index_collection()
    candidates = list(col.find(
        {"audio_features": {"$exists": False}},
        {"identity_key": 1, "final_path": 1, "genre_folder": 1, "_id": 0},
    ).limit(limit))

    analyzed = failed = persisted = skipped = 0
    latency_total = 0.0
    per_track: list[dict] = []

    for doc in candidates:
        ik = doc.get("identity_key", "")
        fp = doc.get("final_path", "")
        genre = doc.get("genre_folder", "")

        if not fp or not Path(fp).exists():
            skipped += 1
            per_track.append({"identity_key": ik, "status": "file_not_found", "path": fp})
            continue

        _dnb = any(k in genre.lower() for k in ("dnb", "drum and bass", "d&b"))
        genre_hint = "dnb" if _dnb else ""

        t0 = time.perf_counter()
        result = detect_bpm_and_key(fp, genre_hint=genre_hint)
        elapsed = time.perf_counter() - t0
        latency_total += elapsed

        if not result.get("analyzed"):
            failed += 1
            per_track.append({"identity_key": ik, "status": "analysis_failed",
                               "error": result.get("error"), "latency_sec": round(elapsed, 2)})
            continue

        analyzed += 1
        ok = persist_audio_features(ik, result)
        if ok:
            persisted += 1
        per_track.append({
            "identity_key":    ik,
            "status":          "persisted" if ok else "persist_skipped",
            "bpm":             result.get("bpm"),
            "key":             result.get("key"),
            "camelot":         result.get("camelot"),
            "latency_sec":     round(elapsed, 2),
        })

    stats = {
        "candidates": len(candidates),
        "analyzed":   analyzed,
        "persisted":  persisted,
        "failed":     failed,
        "skipped":    skipped,
        "avg_latency_sec": round(latency_total / analyzed, 2) if analyzed else 0.0,
    }
    print(f"\n[backfill] {stats}")
    for t in per_track:
        status = t.get("status")
        bpm = t.get("bpm")
        camelot = t.get("camelot")
        lat = t.get("latency_sec", "")
        print(f"  {t['identity_key']}  {status}  BPM={bpm}  camelot={camelot}  {lat}s")
    return stats


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0,
                    help="Backfill N existing tracks before generating report (0 = report only)")
    args = ap.parse_args()
    if args.backfill > 0:
        run_backfill(limit=args.backfill)
    run_report()
