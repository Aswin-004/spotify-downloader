"""
Library Audio Analytics & Clustering
======================================
Computes distributions, rule-based clusters, and dataset quality assessment
from persisted audio_features in library_index. No ML models, no embeddings.

Writes: reports/library_audio_analytics.json
"""
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

from database import get_library_index_collection

REPORTS_DIR = BACKEND_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# ── BPM band definitions ──────────────────────────────────────────────────────
BPM_BANDS = [
    ("slow",     60,  90,  "Ballads, slow jams, chill"),
    ("mid",      90,  110, "Hip-hop, Bollywood, R&B"),
    ("groove",   110, 125, "Pop, UKG, nu-disco"),
    ("house",    125, 140, "House, tech-house, afrobeats"),
    ("intense",  140, 160, "Hardstyle, fast house, bhangra"),
    ("dnb",      160, 195, "Drum & Bass, jungle"),
    ("extreme",  195, 999, "Outlier / mislabelled tempo"),
]

# ── Energy thresholds (derived from dataset: avg 0.173, range 0.045-0.489) ───
RMS_LOW_MAX    = 0.10
RMS_MID_MAX    = 0.18
RMS_HIGH_MAX   = 0.28  # above = very high

# ── Spectral brightness ───────────────────────────────────────────────────────
SC_DARK_MAX    = 1800.0
SC_BRIGHT_MIN  = 2900.0

# ── Camelot wheel structure ───────────────────────────────────────────────────
CAMELOT_KEYS = [f"{n}{s}" for n in range(1, 13) for s in ("A", "B")]

def _camelot_neighbors(key: str) -> list[str]:
    """Return harmonically compatible Camelot keys (same letter ±1, opposite letter same number)."""
    if not key or len(key) < 2:
        return []
    num_str = key[:-1]
    suffix  = key[-1]
    try:
        num = int(num_str)
    except ValueError:
        return []
    opposite = "B" if suffix == "A" else "A"
    prev_num = 12 if num == 1  else num - 1
    next_num = 1  if num == 12 else num + 1
    return [
        f"{prev_num}{suffix}",   # -1 same side
        f"{next_num}{suffix}",   # +1 same side
        f"{num}{opposite}",      # parallel (same number, other side)
    ]


# ── stats helpers ─────────────────────────────────────────────────────────────

def _percentile(sorted_vals: list, pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * pct / 100
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0, "min": None, "max": None, "mean": None,
                "median": None, "p25": None, "p75": None, "std": None}
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n
    variance = sum((x - mean) ** 2 for x in s) / n
    return {
        "count":  n,
        "min":    round(s[0],    3),
        "max":    round(s[-1],   3),
        "mean":   round(mean,    3),
        "median": round(_percentile(s, 50), 3),
        "p25":    round(_percentile(s, 25), 3),
        "p75":    round(_percentile(s, 75), 3),
        "std":    round(math.sqrt(variance), 3),
    }


def _histogram(vals: list[float], bins: list[tuple]) -> dict:
    """bins = [(label, lo, hi), ...]  hi is exclusive except last."""
    counts = {label: 0 for label, lo, hi in bins}
    for v in vals:
        for label, lo, hi in bins:
            if lo <= v < hi:
                counts[label] += 1
                break
    total = len(vals)
    return {
        label: {"count": c, "pct": round(c / total * 100, 1) if total else 0.0}
        for label, c in counts.items()
    }


# ── main analytics ────────────────────────────────────────────────────────────

def run_analytics() -> dict:
    col = get_library_index_collection()

    total_indexed = col.count_documents({})
    total_with_af = col.count_documents({"audio_features": {"$exists": True}})

    # Pull all docs in one pass — 856 docs is small
    docs = list(col.find(
        {"audio_features": {"$exists": True}},
        {"identity_key": 1, "genre_folder": 1, "audio_features": 1,
         "title": 1, "artist": 1, "_id": 0},
    ))

    # ── extract feature vectors ───────────────────────────────────────────────
    bpms:     list[float] = []
    rmss:     list[float] = []
    scs:      list[float] = []
    zcrs:     list[float] = []
    durs:     list[float] = []
    confs:    list[float] = []
    key_counts:     defaultdict[str, int] = defaultdict(int)
    camelot_counts: defaultdict[str, int] = defaultdict(int)
    mode_counts: dict[str, int] = {"maj": 0, "min": 0}

    corrupt_fields:  list[dict] = []
    outlier_tracks:  list[dict] = []
    missing_optional: list[str] = []

    genre_bpm: defaultdict[str, list[float]] = defaultdict(list)
    genre_rms: defaultdict[str, list[float]] = defaultdict(list)

    ROUTING_FIELDS = {"final_path", "genre_folder", "identity_key",
                      "spotify_id", "content_hash", "filename"}
    routing_mutations: list[dict] = []

    for doc in docs:
        af  = doc.get("audio_features", {})
        ik  = doc.get("identity_key", "")
        genre = doc.get("genre_folder", "Unknown")

        # routing mutation guard
        contamination = ROUTING_FIELDS & set(af.keys())
        if contamination:
            routing_mutations.append({"identity_key": ik, "keys": list(contamination)})

        bpm   = af.get("bpm")
        rms   = af.get("rms_energy")
        sc    = af.get("spectral_centroid_mean")
        zcr   = af.get("zero_crossing_rate")
        dur   = af.get("duration_sec")
        conf  = af.get("confidence")
        key   = af.get("key", "")
        km    = af.get("key_mode", "")
        camelot = af.get("camelot", "")

        # corruption check
        def _bad(v, lo=None, hi=None):
            if v is None:
                return True
            if not isinstance(v, (int, float)):
                return True
            if math.isnan(v) or math.isinf(v):
                return True
            if lo is not None and v < lo:
                return True
            if hi is not None and v > hi:
                return True
            return False

        bad_fields = []
        if _bad(bpm,  40, 220): bad_fields.append("bpm")
        if _bad(rms,  0,  1.0): bad_fields.append("rms_energy")
        if _bad(sc,   0,  10000): bad_fields.append("spectral_centroid_mean")
        if bad_fields:
            corrupt_fields.append({"identity_key": ik, "bad_fields": bad_fields,
                                   "bpm": bpm, "rms": rms})

        if bpm is not None and not _bad(bpm, 40, 220):
            bpms.append(float(bpm))
            genre_bpm[genre].append(float(bpm))

            # BPM outlier: extreme ends or below sanity
            if bpm < 65 or bpm > 195:
                outlier_tracks.append({
                    "identity_key": ik,
                    "reason": f"bpm_outlier:{bpm}",
                    "bpm": bpm,
                    "genre": genre,
                })

        if rms is not None and not _bad(rms, 0, 1.0):
            rmss.append(float(rms))
            genre_rms[genre].append(float(rms))

        if sc is not None and not _bad(sc, 0, 10000):
            scs.append(float(sc))

        if zcr is not None:
            zcrs.append(float(zcr))
        else:
            missing_optional.append(ik)

        if dur is not None:
            durs.append(float(dur))

        if conf is not None:
            confs.append(float(conf))
            # low-confidence outlier
            if conf < 0.30 and key:
                outlier_tracks.append({
                    "identity_key": ik,
                    "reason": f"low_key_confidence:{conf:.2f}",
                    "key": key,
                    "confidence": conf,
                    "genre": genre,
                })

        if km in mode_counts:
            mode_counts[km] += 1
        if key:
            key_counts[key] += 1
        if camelot:
            camelot_counts[camelot] += 1

    # ── BPM distribution ─────────────────────────────────────────────────────
    bpm_hist = _histogram(
        bpms,
        [(label, lo, hi) for label, lo, hi, _ in BPM_BANDS],
    )
    bpm_stats = _stats(bpms)

    # top BPM ranges (band name + desc)
    top_bpm_ranges = sorted(
        [
            {"band": label, "range": f"{lo}-{hi}", "description": desc,
             **bpm_hist[label]}
            for label, lo, hi, desc in BPM_BANDS
        ],
        key=lambda x: -x["count"],
    )

    # ── Key & Camelot distributions ───────────────────────────────────────────
    top_keys = sorted(
        [{"key": k, "count": v, "pct": round(v / len(bpms) * 100, 1)}
         for k, v in key_counts.items()],
        key=lambda x: -x["count"],
    )[:15]

    top_camelot = sorted(
        [{"camelot": k, "count": v, "pct": round(v / len(bpms) * 100, 1)}
         for k, v in camelot_counts.items()],
        key=lambda x: -x["count"],
    )[:15]

    # ── Energy distributions ──────────────────────────────────────────────────
    rms_hist = _histogram(rmss, [
        ("low",       0.00, RMS_LOW_MAX),
        ("mid_low",   RMS_LOW_MAX, RMS_MID_MAX),
        ("mid_high",  RMS_MID_MAX, RMS_HIGH_MAX),
        ("high",      RMS_HIGH_MAX, 999),
    ])
    rms_stats = _stats(rmss)

    sc_hist = _histogram(scs, [
        ("dark",      0,          SC_DARK_MAX),
        ("balanced",  SC_DARK_MAX, SC_BRIGHT_MIN),
        ("bright",    SC_BRIGHT_MIN, 99999),
    ])
    sc_stats = _stats(scs)

    # ── Genre BPM ranges ─────────────────────────────────────────────────────
    genre_bpm_summary = {}
    for g, vals in sorted(genre_bpm.items()):
        if not vals:
            continue
        s = sorted(vals)
        genre_bpm_summary[g] = {
            "count": len(s),
            "bpm_min":    int(s[0]),
            "bpm_max":    int(s[-1]),
            "bpm_median": int(_percentile(s, 50)),
            "bpm_p25":    int(_percentile(s, 25)),
            "bpm_p75":    int(_percentile(s, 75)),
            "dominant_band": max(
                BPM_BANDS,
                key=lambda b: sum(1 for v in vals if b[1] <= v < b[2])
            )[0],
        }

    # ── Rule-based clusters ───────────────────────────────────────────────────
    clusters: dict[str, list[dict]] = defaultdict(list)

    for doc in docs:
        af    = doc.get("audio_features", {})
        ik    = doc.get("identity_key", "")
        genre = doc.get("genre_folder", "Unknown")
        bpm   = af.get("bpm") or 0
        rms   = af.get("rms_energy") or 0.0
        sc    = af.get("spectral_centroid_mean") or 0.0
        cam   = af.get("camelot", "")

        entry = {"identity_key": ik, "bpm": bpm, "camelot": cam, "genre": genre}

        # BPM-only clusters
        for label, lo, hi, _ in BPM_BANDS:
            if lo <= bpm < hi:
                clusters[f"bpm_{label}"].append(entry)
                break

        # Energy clusters
        if rms < RMS_LOW_MAX:
            clusters["energy_low"].append(entry)
        elif rms < RMS_MID_MAX:
            clusters["energy_mid_low"].append(entry)
        elif rms < RMS_HIGH_MAX:
            clusters["energy_mid_high"].append(entry)
        else:
            clusters["energy_high"].append(entry)

        # Combined BPM + energy clusters (most useful for DJ prep)
        if 125 <= bpm < 140 and rms >= RMS_MID_MAX:
            clusters["high_energy_house"].append(entry)
        if 160 <= bpm < 195 and rms >= RMS_MID_MAX:
            clusters["high_energy_dnb"].append(entry)
        if bpm < 110 and sc < SC_DARK_MAX:
            clusters["warm_low_tempo"].append(entry)
        if 110 <= bpm < 145 and sc >= SC_BRIGHT_MIN:
            clusters["bright_mid_tempo"].append(entry)

    # Camelot harmonic compatibility groups
    harmonic_groups = {}
    for cam_key in CAMELOT_KEYS:
        members = [
            {"identity_key": doc["identity_key"],
             "bpm": doc["audio_features"].get("bpm"),
             "genre": doc.get("genre_folder", "")}
            for doc in docs
            if doc["audio_features"].get("camelot") in ([cam_key] + _camelot_neighbors(cam_key))
        ]
        if members:
            harmonic_groups[cam_key] = {
                "center":      cam_key,
                "neighbors":   _camelot_neighbors(cam_key),
                "total_tracks":len(members),
                "sample":      members[:5],
            }

    cluster_summary = {
        k: {"count": len(v), "sample": v[:3]}
        for k, v in sorted(clusters.items())
    }

    # ── Outlier dedup ─────────────────────────────────────────────────────────
    seen_outliers: set[str] = set()
    unique_outliers = []
    for o in outlier_tracks:
        key_sig = f"{o['identity_key']}:{o['reason']}"
        if key_sig not in seen_outliers:
            seen_outliers.add(key_sig)
            unique_outliers.append(o)

    # ── Dataset quality ───────────────────────────────────────────────────────
    has_bpm     = sum(1 for d in docs if d["audio_features"].get("bpm") is not None)
    has_key     = sum(1 for d in docs if d["audio_features"].get("key") is not None)
    has_camelot = sum(1 for d in docs if d["audio_features"].get("camelot") is not None)
    has_rms     = sum(1 for d in docs if d["audio_features"].get("rms_energy") is not None)
    has_sc      = sum(1 for d in docs if d["audio_features"].get("spectral_centroid_mean") is not None)
    has_zcr     = sum(1 for d in docs if d["audio_features"].get("zero_crossing_rate") is not None)
    has_dur     = sum(1 for d in docs if d["audio_features"].get("duration_sec") is not None)
    n = len(docs)

    # ── ML readiness bottleneck ───────────────────────────────────────────────
    low_conf_count = sum(1 for c in confs if c < 0.50)
    ml_bottlenecks = []
    if low_conf_count > n * 0.20:
        ml_bottlenecks.append({
            "bottleneck":  "high_low_confidence_rate",
            "count":       low_conf_count,
            "pct":         round(low_conf_count / n * 100, 1),
            "description": "Key detection confidence < 0.50 on >20% of library. "
                           "Suggests short/percussive tracks where chroma is ambiguous.",
            "fix":         "Extend analysis window; weight chroma over longer segment; "
                           "or flag low-conf key as None and retag manually.",
        })
    if len(unique_outliers) > n * 0.05:
        ml_bottlenecks.append({
            "bottleneck":  "high_outlier_rate",
            "count":       len(unique_outliers),
            "pct":         round(len(unique_outliers) / n * 100, 1),
            "description": "More than 5% of tracks have BPM/confidence outliers.",
            "fix":         "Review BPM outliers — likely mis-detected tempo or mislabelled genre.",
        })
    if not ml_bottlenecks:
        ml_bottlenecks.append({
            "bottleneck":  "none",
            "description": "Dataset quality is sufficient for clustering and recommendation tasks.",
        })

    # ── Assemble report ───────────────────────────────────────────────────────
    report = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "total_indexed":    total_indexed,
            "with_features":    total_with_af,
            "analyzed_this_run":n,
        },
        "feature_coverage": {
            "bpm":                   {"count": has_bpm,     "pct": round(has_bpm/n*100,1)},
            "key":                   {"count": has_key,     "pct": round(has_key/n*100,1)},
            "camelot":               {"count": has_camelot, "pct": round(has_camelot/n*100,1)},
            "rms_energy":            {"count": has_rms,     "pct": round(has_rms/n*100,1)},
            "spectral_centroid_mean":{"count": has_sc,      "pct": round(has_sc/n*100,1)},
            "zero_crossing_rate":    {"count": has_zcr,     "pct": round(has_zcr/n*100,1)},
            "duration_sec":          {"count": has_dur,     "pct": round(has_dur/n*100,1)},
        },
        "bpm_analytics": {
            "stats":      bpm_stats,
            "histogram":  bpm_hist,
            "top_ranges": top_bpm_ranges,
            "mode_split": mode_counts,
        },
        "key_analytics": {
            "top_keys":    top_keys,
            "top_camelot": top_camelot,
        },
        "energy_analytics": {
            "rms_stats":   rms_stats,
            "rms_histogram": rms_hist,
            "sc_stats":    sc_stats,
            "sc_histogram": sc_hist,
            "zcr_stats":   _stats(zcrs),
            "duration_stats": _stats(durs),
        },
        "genre_bpm_ranges": genre_bpm_summary,
        "clusters": {
            "summary": cluster_summary,
            "harmonic_groups": {
                k: v for k, v in harmonic_groups.items()
                if v["total_tracks"] >= 5
            },
        },
        "outliers": {
            "count": len(unique_outliers),
            "tracks": unique_outliers[:30],
        },
        "validation": {
            "corrupt_fields":    corrupt_fields,
            "routing_mutations": routing_mutations,
            "routing_safe":      len(routing_mutations) == 0,
            "corrupt_count":     len(corrupt_fields),
            "library_safe":      len(corrupt_fields) == 0 and len(routing_mutations) == 0,
        },
        "ml_readiness": {
            "dataset_size":      n,
            "key_conf_mean":     round(sum(confs)/len(confs), 3) if confs else 0.0,
            "low_conf_count":    low_conf_count,
            "low_conf_pct":      round(low_conf_count / n * 100, 1) if n else 0.0,
            "cluster_count":     sum(len(v) for v in clusters.values()),
            "bottlenecks":       ml_bottlenecks,
        },
    }

    out = REPORTS_DIR / "library_audio_analytics.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    _print_summary(report)
    print(f"\n[analytics] Report → {out}")
    return report


def _print_summary(r: dict):
    ds = r["dataset"]
    fc = r["feature_coverage"]
    ba = r["bpm_analytics"]
    ea = r["energy_analytics"]
    cl = r["clusters"]
    ml = r["ml_readiness"]
    val = r["validation"]

    print(f"\n{'='*54}")
    print(f"  LIBRARY AUDIO ANALYTICS")
    print(f"{'='*54}")
    print(f"  Tracks analyzed : {ds['analyzed_this_run']} / {ds['total_indexed']}")

    print(f"\n  Feature coverage:")
    for feat, info in fc.items():
        print(f"    {feat:<30} {info['count']:>4}  ({info['pct']}%)")

    print(f"\n  BPM  — mean={ba['stats']['mean']}  "
          f"med={ba['stats']['median']}  "
          f"std={ba['stats']['std']}  "
          f"range=[{ba['stats']['min']}-{ba['stats']['max']}]")
    print(f"  BPM bands:")
    for band in ba["top_ranges"]:
        bar = "█" * max(1, int(band["pct"] / 2))
        print(f"    {band['band']:<10} {band['range']:<10} {band['count']:>4} ({band['pct']:>5}%)  {bar}")

    print(f"\n  Key mode  maj={r['bpm_analytics']['mode_split']['maj']}  "
          f"min={r['bpm_analytics']['mode_split']['min']}")
    print(f"  Top keys: " +
          "  ".join(f"{k['key']}:{k['count']}" for k in r["key_analytics"]["top_keys"][:8]))
    print(f"  Top camelot: " +
          "  ".join(f"{k['camelot']}:{k['count']}" for k in r["key_analytics"]["top_camelot"][:8]))

    rs = ea["rms_stats"]
    print(f"\n  RMS energy — mean={rs['mean']}  p25={rs['p25']}  p75={rs['p75']}")
    print(f"  Energy bands: " +
          "  ".join(f"{k}:{v['count']}" for k, v in ea["rms_histogram"].items()))

    print(f"\n  Clusters:")
    for name, info in sorted(cl["summary"].items()):
        print(f"    {name:<25} {info['count']:>4} tracks")
    print(f"  Harmonic groups (≥5 tracks): {len(cl['harmonic_groups'])}")

    print(f"\n  Outliers: {r['outliers']['count']}")
    for o in r["outliers"]["tracks"][:5]:
        print(f"    {o['reason']}  {o.get('genre','')}")

    print(f"\n  Validation:")
    print(f"    Library safe    : {val['library_safe']}")
    print(f"    Routing safe    : {val['routing_safe']}")
    print(f"    Corrupt fields  : {val['corrupt_count']}")

    print(f"\n  ML readiness:")
    print(f"    Key conf mean  : {ml['key_conf_mean']}")
    print(f"    Low-conf tracks: {ml['low_conf_count']} ({ml['low_conf_pct']}%)")
    for b in ml["bottlenecks"]:
        print(f"    Bottleneck: [{b['bottleneck']}] {b['description'][:80]}")


# ── Track similarity ──────────────────────────────────────────────────────────

_SIM_WEIGHTS = {"camelot": 0.40, "bpm": 0.30, "energy": 0.20, "spectral": 0.10}
_BPM_TOLERANCE      = 20.0    # delta at which BPM score → 0
_ENERGY_TOLERANCE   = 0.15    # RMS delta at which energy score → 0
_SPECTRAL_TOLERANCE = 1500.0  # Hz delta at which spectral score → 0


def _camelot_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b in _camelot_neighbors(a):
        return 0.75
    return 0.0


def _proximity_score(val_a, val_b, tolerance: float) -> float:
    if val_a is None or val_b is None:
        return 0.0
    if not isinstance(val_a, (int, float)) or not isinstance(val_b, (int, float)):
        return 0.0
    if math.isnan(val_a) or math.isnan(val_b):
        return 0.0
    return max(0.0, 1.0 - abs(float(val_a) - float(val_b)) / tolerance)


def _compute_similarity(af_a: dict, af_b: dict) -> dict:
    cam = _camelot_score(af_a.get("camelot", ""), af_b.get("camelot", ""))
    bpm = _proximity_score(af_a.get("bpm"), af_b.get("bpm"), _BPM_TOLERANCE)
    eng = _proximity_score(af_a.get("rms_energy"), af_b.get("rms_energy"), _ENERGY_TOLERANCE)
    spc = _proximity_score(
        af_a.get("spectral_centroid_mean"),
        af_b.get("spectral_centroid_mean"),
        _SPECTRAL_TOLERANCE,
    )
    total = (
        _SIM_WEIGHTS["camelot"]  * cam +
        _SIM_WEIGHTS["bpm"]      * bpm +
        _SIM_WEIGHTS["energy"]   * eng +
        _SIM_WEIGHTS["spectral"] * spc
    )
    return {
        "score":          round(total, 4),
        "camelot_score":  round(cam, 4),
        "bpm_score":      round(bpm, 4),
        "energy_score":   round(eng, 4),
        "spectral_score": round(spc, 4),
    }


def find_similar_tracks(
    identity_key: str,
    top_k: int = 10,
    _docs: list[dict] | None = None,
) -> list[dict]:
    """Rule-based similarity using bpm, camelot, rms_energy, spectral_centroid."""
    if _docs is None:
        col = get_library_index_collection()
        _docs = list(col.find(
            {"audio_features": {"$exists": True}},
            {"identity_key": 1, "title": 1, "artist": 1,
             "genre_folder": 1, "audio_features": 1, "_id": 0},
        ))

    source = next((d for d in _docs if d["identity_key"] == identity_key), None)
    if source is None:
        return []

    af_src  = source.get("audio_features", {})
    seen: set[str] = {identity_key}
    results: list[dict] = []

    for doc in _docs:
        ik = doc["identity_key"]
        if ik in seen:
            continue
        seen.add(ik)

        af  = doc.get("audio_features", {})
        sim = _compute_similarity(af_src, af)

        bpm_s = af_src.get("bpm")
        bpm_d = af.get("bpm")
        rms_s = af_src.get("rms_energy")
        rms_d = af.get("rms_energy")

        results.append({
            "identity_key":      ik,
            "title":             doc.get("title", ""),
            "artist":            doc.get("artist", ""),
            "genre":             doc.get("genre_folder", ""),
            "camelot":           af.get("camelot", ""),
            "bpm":               af.get("bpm"),
            "rms_energy":        af.get("rms_energy"),
            "spectral_centroid": af.get("spectral_centroid_mean"),
            "harmonic_compatible": sim["camelot_score"] >= 0.75,
            "bpm_delta": (
                round(abs(float(bpm_s) - float(bpm_d)), 1)
                if bpm_s is not None and bpm_d is not None else None
            ),
            "energy_delta": (
                round(abs(float(rms_s) - float(rms_d)), 4)
                if rms_s is not None and rms_d is not None else None
            ),
            **sim,
        })

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


def run_similarity_report(sample_size: int = 50) -> dict:
    col  = get_library_index_collection()
    docs = list(col.find(
        {"audio_features": {"$exists": True}},
        {"identity_key": 1, "title": 1, "artist": 1,
         "genre_folder": 1, "audio_features": 1, "_id": 0},
    ))

    def _has_full_features(d: dict) -> bool:
        af = d.get("audio_features", {})
        return all(
            af.get(f) is not None
            for f in ("bpm", "camelot", "rms_energy", "spectral_centroid_mean")
        )

    full_docs = [d for d in docs if _has_full_features(d)]
    step      = max(1, len(full_docs) // sample_size)
    sampled   = full_docs[::step][:sample_size]

    similarity_map: list[dict] = []
    all_top_scores: list[float] = []
    camelot_miss = bpm_wide = weak_energy = 0

    for doc in sampled:
        ik      = doc["identity_key"]
        af      = doc.get("audio_features", {})
        matches = find_similar_tracks(ik, top_k=10, _docs=docs)
        top     = matches[0] if matches else None

        if top:
            all_top_scores.append(top["score"])
            if not top["harmonic_compatible"]:
                camelot_miss += 1
            if top.get("bpm_delta") is not None and top["bpm_delta"] > 8:
                bpm_wide += 1
            if top["energy_score"] < 0.5:
                weak_energy += 1

        similarity_map.append({
            "source": {
                "identity_key": ik,
                "title":        doc.get("title", ""),
                "artist":       doc.get("artist", ""),
                "genre":        doc.get("genre_folder", ""),
                "camelot":      af.get("camelot", ""),
                "bpm":          af.get("bpm"),
                "rms_energy":   af.get("rms_energy"),
            },
            "top_similar": matches,
        })

    n = len(sampled)
    avg_top = round(sum(all_top_scores) / len(all_top_scores), 4) if all_top_scores else 0.0

    bottlenecks: list[dict] = []
    if camelot_miss > n * 0.30:
        bottlenecks.append({
            "area":        "camelot_compatibility",
            "severity":    "high",
            "description": f"{camelot_miss}/{n} sampled tracks have no harmonically compatible top match.",
            "next_action": "Enrich camelot tagging; run backfill_audio_features for missing keys.",
        })
    if bpm_wide > n * 0.25:
        bottlenecks.append({
            "area":        "bpm_proximity",
            "severity":    "medium",
            "description": f"{bpm_wide}/{n} top matches have BPM delta > 8.",
            "next_action": "Library lacks density in some BPM bands; expand collection or widen tolerance.",
        })
    if weak_energy > n * 0.25:
        bottlenecks.append({
            "area":        "energy_similarity",
            "severity":    "medium",
            "description": f"{weak_energy}/{n} top matches have energy score < 0.50.",
            "next_action": "Wide RMS spread; consider per-genre energy normalization.",
        })
    if avg_top < 0.50:
        bottlenecks.append({
            "area":        "overall_similarity",
            "severity":    "high",
            "description": f"Average top-match score {avg_top} is below 0.50 — recommendation quality weak.",
            "next_action": "Run backfill_audio_features; check camelot + BPM coverage first.",
        })
    if not bottlenecks:
        bottlenecks.append({
            "area":        "none",
            "severity":    "none",
            "description": "Similarity quality acceptable. No critical bottlenecks.",
            "next_action": "Increase sample_size or add zcr/duration dimensions for finer resolution.",
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "weights":            _SIM_WEIGHTS,
            "bpm_tolerance":      _BPM_TOLERANCE,
            "energy_tolerance":   _ENERGY_TOLERANCE,
            "spectral_tolerance": _SPECTRAL_TOLERANCE,
        },
        "summary": {
            "library_size":         len(docs),
            "tracks_with_features": len(full_docs),
            "sampled":              n,
            "avg_top_match_score":  avg_top,
            "camelot_miss_rate":    round(camelot_miss / n * 100, 1) if n else 0.0,
            "bpm_wide_rate":        round(bpm_wide    / n * 100, 1) if n else 0.0,
            "weak_energy_rate":     round(weak_energy / n * 100, 1) if n else 0.0,
        },
        "bottlenecks":    bottlenecks,
        "similarity_map": similarity_map,
    }

    out = REPORTS_DIR / "track_similarity_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    _print_similarity_summary(report)
    print(f"\n[similarity] Report → {out}")
    return report


def _print_similarity_summary(r: dict) -> None:
    s = r["summary"]
    print(f"\n{'='*54}")
    print(f"  TRACK SIMILARITY REPORT")
    print(f"{'='*54}")
    print(f"  Library size          : {s['library_size']}")
    print(f"  Tracks with features  : {s['tracks_with_features']}")
    print(f"  Sampled               : {s['sampled']}")
    print(f"  Avg top-match score   : {s['avg_top_match_score']}")
    print(f"  Camelot miss rate     : {s['camelot_miss_rate']}%")
    print(f"  BPM wide-match rate   : {s['bpm_wide_rate']}%")
    print(f"  Weak energy rate      : {s['weak_energy_rate']}%")
    print(f"\n  Bottlenecks:")
    for b in r["bottlenecks"]:
        sev = b["severity"].upper()
        print(f"    [{sev}] {b['area']}: {b['description'][:78]}")
        print(f"          → {b['next_action'][:78]}")


# ── Playlist sequencing ───────────────────────────────────────────────────────

_FLOW_MODES      = ("warmup", "peak", "cooldown", "mixed")
_BPM_JUMP_HARD   = 15.0   # reject candidate if bpm delta exceeds this
_BPM_JUMP_SOFT   = 25.0   # relaxed fallback threshold
_ARTIST_WINDOW   = 4      # no artist repeat within this many steps
_WEAK_TRANS_SCORE = 0.40  # transition score below this is flagged weak
_WEAK_BPM_JUMP   = 12.0   # bpm jump above this is flagged weak


def _flow_bonus(
    cur_bpm, cand_bpm,
    _cur_rms, cand_rms,
    flow: str,
) -> float:
    if flow == "warmup":
        if cur_bpm is not None and cand_bpm is not None and cand_bpm > cur_bpm:
            return 0.10
    elif flow == "cooldown":
        if cur_bpm is not None and cand_bpm is not None and cand_bpm < cur_bpm:
            return 0.10
    elif flow == "peak":
        if cand_rms is not None and cand_rms >= RMS_MID_MAX:
            return 0.10
    return 0.0


def _pick_next(
    current: dict,
    pool: list[dict],
    used_keys: set[str],
    recent_artists: list[str],
    flow: str,
    relax: bool = False,
) -> tuple | None:
    """Return (adjusted_score, doc, sim) for best next candidate, or None."""
    cur_af  = current.get("audio_features", {})
    cur_bpm   = cur_af.get("bpm")
    bpm_limit = _BPM_JUMP_SOFT if relax else _BPM_JUMP_HARD

    best: tuple | None = None
    for doc in pool:
        ik = doc["identity_key"]
        if ik in used_keys:
            continue
        artist = doc.get("artist", "")
        if not relax and artist and artist in recent_artists[-_ARTIST_WINDOW:]:
            continue

        af       = doc.get("audio_features", {})
        cand_bpm = af.get("bpm")
        cand_rms = af.get("rms_energy")

        if cur_bpm is not None and cand_bpm is not None:
            if abs(float(cur_bpm) - float(cand_bpm)) > bpm_limit:
                continue

        sim   = _compute_similarity(cur_af, af)
        bonus = _flow_bonus(cur_bpm, cand_bpm, None, cand_rms, flow)
        adj   = sim["score"] + bonus

        if best is None or adj > best[0]:
            best = (adj, doc, sim)

    return best


def generate_playlist_sequence(
    seed_identity_key: str,
    length: int = 20,
    flow: str = "mixed",
    _docs: list[dict] | None = None,
) -> dict:
    """Build a sequenced playlist from a seed track.

    flow: warmup | peak | cooldown | mixed
    Returns full sequence, transitions, progressions, quality metrics.
    """
    if flow not in _FLOW_MODES:
        raise ValueError(f"flow must be one of {_FLOW_MODES}")

    if _docs is None:
        col = get_library_index_collection()
        _docs = list(col.find(
            {"audio_features": {"$exists": True}},
            {"identity_key": 1, "title": 1, "artist": 1,
             "genre_folder": 1, "audio_features": 1, "_id": 0},
        ))

    seed = next((d for d in _docs if d["identity_key"] == seed_identity_key), None)
    if seed is None:
        return {"error": f"seed '{seed_identity_key}' not found"}

    sequence:       list[dict] = [seed]
    used_keys:      set[str]   = {seed_identity_key}
    recent_artists: list[str]  = [seed.get("artist", "")]
    transitions:    list[dict] = []
    pool = [d for d in _docs if d["identity_key"] != seed_identity_key]

    for step in range(length - 1):
        current = sequence[-1]
        result  = _pick_next(current, pool, used_keys, recent_artists, flow, relax=False)
        if result is None:
            result = _pick_next(current, pool, used_keys, recent_artists, flow, relax=True)
        if result is None:
            break

        _adj, next_doc, sim = result
        ik     = next_doc["identity_key"]
        af_cur = current.get("audio_features", {})
        af_nxt = next_doc.get("audio_features", {})
        bpm_c  = af_cur.get("bpm")
        bpm_n  = af_nxt.get("bpm")
        rms_c  = af_cur.get("rms_energy")
        rms_n  = af_nxt.get("rms_energy")

        bpm_jump = (
            round(abs(float(bpm_c) - float(bpm_n)), 1)
            if bpm_c is not None and bpm_n is not None else None
        )
        energy_delta = (
            round(float(rms_n) - float(rms_c), 4)
            if rms_c is not None and rms_n is not None else None
        )
        is_weak = (
            sim["score"] < _WEAK_TRANS_SCORE or
            (bpm_jump is not None and bpm_jump > _WEAK_BPM_JUMP) or
            sim["camelot_score"] == 0.0
        )

        transitions.append({
            "step":              step + 1,
            "from_key":          current["identity_key"],
            "to_key":            ik,
            "transition_score":  round(sim["score"], 4),
            "camelot_score":     sim["camelot_score"],
            "bpm_score":         sim["bpm_score"],
            "energy_score":      sim["energy_score"],
            "camelot_from":      af_cur.get("camelot", ""),
            "camelot_to":        af_nxt.get("camelot", ""),
            "bpm_from":          bpm_c,
            "bpm_to":            bpm_n,
            "bpm_jump":          bpm_jump,
            "energy_delta":      energy_delta,
            "harmonic_compatible": sim["camelot_score"] >= 0.75,
            "is_weak":           is_weak,
        })

        sequence.append(next_doc)
        used_keys.add(ik)
        recent_artists.append(next_doc.get("artist", ""))

    # ── Ordered sequence list ─────────────────────────────────────────────────
    seq_list = []
    for i, doc in enumerate(sequence):
        af = doc.get("audio_features", {})
        seq_list.append({
            "position":          i + 1,
            "identity_key":      doc["identity_key"],
            "title":             doc.get("title", ""),
            "artist":            doc.get("artist", ""),
            "genre":             doc.get("genre_folder", ""),
            "camelot":           af.get("camelot", ""),
            "bpm":               af.get("bpm"),
            "rms_energy":        af.get("rms_energy"),
            "spectral_centroid": af.get("spectral_centroid_mean"),
        })

    # ── Quality metrics ───────────────────────────────────────────────────────
    t_scores   = [t["transition_score"] for t in transitions]
    weak_trans = [t for t in transitions if t["is_weak"]]
    bpm_jumps  = [t["bpm_jump"] for t in transitions if t["bpm_jump"] is not None]
    harm_ok    = sum(1 for t in transitions if t["harmonic_compatible"])

    avg_trans    = round(sum(t_scores)   / len(t_scores),   4) if t_scores  else 0.0
    avg_bpm_jump = round(sum(bpm_jumps)  / len(bpm_jumps),  2) if bpm_jumps else 0.0

    bpm_prog = [sequence[0].get("audio_features", {}).get("bpm")] + [
        t["bpm_to"] for t in transitions
    ]
    cam_prog = [
        doc.get("audio_features", {}).get("camelot", "") for doc in sequence
    ]
    rms_prog = [
        round(doc.get("audio_features", {}).get("rms_energy") or 0.0, 4)
        for doc in sequence
    ]

    return {
        "seed_identity_key":   seed_identity_key,
        "flow":                flow,
        "length_requested":    length,
        "length_achieved":     len(sequence),
        "sequence":            seq_list,
        "transitions":         transitions,
        "bpm_progression":     bpm_prog,
        "camelot_progression": cam_prog,
        "energy_progression":  rms_prog,
        "quality": {
            "avg_transition_score":    avg_trans,
            "harmonic_compatible_pct": round(harm_ok / len(transitions) * 100, 1) if transitions else 0.0,
            "weak_transitions":        len(weak_trans),
            "weak_transition_pct":     round(len(weak_trans) / len(transitions) * 100, 1) if transitions else 0.0,
            "avg_bpm_jump":            avg_bpm_jump,
            "max_bpm_jump":            round(max(bpm_jumps), 1) if bpm_jumps else 0.0,
        },
        "weak_transitions": weak_trans,
    }


def run_playlist_report(
    seed_identity_key: str,
    length: int = 20,
    flow: str = "mixed",
) -> dict:
    col  = get_library_index_collection()
    docs = list(col.find(
        {"audio_features": {"$exists": True}},
        {"identity_key": 1, "title": 1, "artist": 1,
         "genre_folder": 1, "audio_features": 1, "_id": 0},
    ))

    result = generate_playlist_sequence(
        seed_identity_key, length=length, flow=flow, _docs=docs
    )
    if "error" in result:
        print(f"[playlist] Error: {result['error']}")
        return result

    q = result["quality"]
    bottlenecks: list[dict] = []

    if q["weak_transition_pct"] > 30.0:
        bottlenecks.append({
            "area":        "transition_quality",
            "severity":    "high",
            "description": f"{q['weak_transitions']} weak transitions ({q['weak_transition_pct']}%). "
                           "Sequence has rough edges.",
            "next_action": "Expand camelot + BPM coverage. Consider N-step lookahead.",
        })
    if q["harmonic_compatible_pct"] < 60.0:
        bottlenecks.append({
            "area":        "harmonic_compatibility",
            "severity":    "high",
            "description": f"Only {q['harmonic_compatible_pct']}% of transitions are harmonically compatible.",
            "next_action": "Enrich camelot tagging before using playlist in production.",
        })
    if q["avg_bpm_jump"] > 8.0:
        bottlenecks.append({
            "area":        "bpm_smoothness",
            "severity":    "medium",
            "description": f"Average BPM jump {q['avg_bpm_jump']} — transitions feel abrupt.",
            "next_action": "Add tracks in sparse BPM bands or use bridge-track logic.",
        })
    if q["avg_transition_score"] < 0.45:
        bottlenecks.append({
            "area":        "chain_quality",
            "severity":    "high",
            "description": f"Avg transition score {q['avg_transition_score']} < 0.45 — chain weakly linked.",
            "next_action": "Run backfill_audio_features first. Camelot + BPM completeness is bottleneck.",
        })
    if not bottlenecks:
        bottlenecks.append({
            "area":        "none",
            "severity":    "none",
            "description": "Playlist quality is good. No critical bottlenecks.",
            "next_action": "Implement N-step lookahead greedy for further smoothing.",
        })

    report = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "seed_identity_key":   seed_identity_key,
        "flow":                flow,
        "length_requested":    length,
        "length_achieved":     result["length_achieved"],
        "sequence":            result["sequence"],
        "transitions":         result["transitions"],
        "bpm_progression":     result["bpm_progression"],
        "camelot_progression": result["camelot_progression"],
        "energy_progression":  result["energy_progression"],
        "quality":             q,
        "weak_transitions":    result["weak_transitions"],
        "bottlenecks":         bottlenecks,
    }

    out = REPORTS_DIR / "playlist_sequence_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    _print_playlist_summary(report)
    print(f"\n[playlist] Report → {out}")
    return report


def _print_playlist_summary(r: dict) -> None:
    q = r["quality"]
    print(f"\n{'='*54}")
    print(f"  PLAYLIST SEQUENCE REPORT  [{r['flow'].upper()}]")
    print(f"{'='*54}")
    print(f"  Length           : {r['length_achieved']} / {r['length_requested']}")
    print(f"  Avg trans score  : {q['avg_transition_score']}")
    print(f"  Harmonic compat  : {q['harmonic_compatible_pct']}%")
    print(f"  Weak transitions : {q['weak_transitions']} ({q['weak_transition_pct']}%)")
    print(f"  Avg BPM jump     : {q['avg_bpm_jump']}  Max: {q['max_bpm_jump']}")
    print(f"\n  Sequence:")
    for t in r["sequence"]:
        cam = (t.get("camelot") or "?  ")[:3]
        bpm = str(t.get("bpm") or "?").rjust(6)
        rms = f"{(t.get('rms_energy') or 0):.3f}"
        art = (t.get("artist") or "")[:20].ljust(20)
        ttl = (t.get("title")  or "")[:28]
        print(f"    {t['position']:>2}. [{cam}] {bpm} bpm  rms={rms}  {art} — {ttl}")
    if r["weak_transitions"]:
        print(f"\n  Weak transitions ({len(r['weak_transitions'])}):")
        for wt in r["weak_transitions"][:5]:
            print(f"    step {wt['step']}: score={wt['transition_score']}  "
                  f"bpm_jump={wt['bpm_jump']}  "
                  f"{wt['camelot_from'] or '?'}→{wt['camelot_to'] or '?'}")
    print(f"\n  Bottlenecks:")
    for b in r["bottlenecks"]:
        print(f"    [{b['severity'].upper()}] {b['area']}: {b['description'][:78]}")
        print(f"          → {b['next_action'][:78]}")


# ── Music intelligence graph visualizations ───────────────────────────────────

GRAPHS_DIR = REPORTS_DIR / "graphs"


def _load_docs_for_graphs() -> list[dict]:
    col = get_library_index_collection()
    return list(col.find(
        {"audio_features": {"$exists": True}},
        {"identity_key": 1, "title": 1, "artist": 1,
         "genre_folder": 1, "audio_features": 1, "_id": 0},
    ))


def _safe_label(doc: dict, max_len: int = 22) -> str:
    title  = (doc.get("title")  or doc.get("identity_key", "?"))[:max_len]
    artist = (doc.get("artist") or "")[:16]
    return f"{title}<br>{artist}" if artist else title


def build_similarity_graph(
    docs: list[dict],
    threshold: float = 0.55,
    max_nodes: int = 120,
) -> None:
    """Track similarity network: nodes=tracks, edges=similarity≥threshold."""
    try:
        import networkx as nx
        import plotly.graph_objects as go
    except ImportError as e:
        print(f"[graphs] Missing dependency: {e}. Run: pip install networkx plotly")
        return

    step  = max(1, len(docs) // max_nodes)
    nodes = docs[::step][:max_nodes]

    G = nx.Graph()
    for doc in nodes:
        af = doc.get("audio_features", {})
        G.add_node(
            doc["identity_key"],
            label   = _safe_label(doc),
            bpm     = af.get("bpm") or 0,
            camelot = af.get("camelot") or "?",
            energy  = round(af.get("rms_energy") or 0.0, 3),
            genre   = doc.get("genre_folder", "Unknown"),
        )

    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            sim = _compute_similarity(
                a.get("audio_features", {}),
                b.get("audio_features", {}),
            )
            if sim["score"] >= threshold:
                G.add_edge(a["identity_key"], b["identity_key"], weight=sim["score"])

    if len(G.nodes) == 0:
        print("[graphs] similarity_graph: no nodes — skipping")
        return

    pos = nx.spring_layout(G, seed=42, k=0.6)

    edge_x, edge_y = [], []
    for u, v in G.edges():
        x0, y0 = pos[u]; x1, y1 = pos[v]
        edge_x += [x0, x1, None]; edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(width=0.8, color="rgba(120,160,255,0.25)"),
        hoverinfo="none", name="Similarity link",
    )

    node_x, node_y, node_text, node_hover, node_color, node_size = [], [], [], [], [], []
    for nid in G.nodes:
        x, y = pos[nid]
        d     = G.nodes[nid]
        deg   = G.degree(nid)
        node_x.append(x); node_y.append(y)
        node_text.append(d["label"])
        node_color.append(d["bpm"])
        node_size.append(max(10, min(28, deg * 3 + 10)))
        node_hover.append(
            f"<b>{d['label']}</b><br>"
            f"━━━━━━━━━━━━━━━━━<br>"
            f"🎵 BPM: <b>{d['bpm']}</b><br>"
            f"🎹 Camelot Key: <b>{d['camelot']}</b><br>"
            f"⚡ Energy (RMS): <b>{d['energy']}</b><br>"
            f"🎼 Genre: <b>{d['genre']}</b><br>"
            f"🔗 Similar to <b>{deg}</b> other tracks"
        )

    node_trace = go.Scatter(
        x=node_x, y=node_y, mode="markers+text",
        text=node_text, textposition="top center",
        textfont=dict(size=7, color="#cccccc"),
        hovertext=node_hover, hoverinfo="text",
        name="Track",
        marker=dict(
            size=node_size,
            color=node_color,
            colorscale="Plasma",
            colorbar=dict(
                title=dict(text="BPM<br><sub>Higher = faster tempo</sub>", side="right"),
                thickness=14, len=0.6,
                tickfont=dict(size=9),
            ),
            line=dict(width=0.8, color="rgba(255,255,255,0.3)"),
            opacity=0.88,
        ),
    )

    # how-to-read annotation box
    how_to_read = (
        "<b>HOW TO READ THIS GRAPH</b><br>"
        "● Each dot = one track in your library<br>"
        "● A line between two dots = they sound similar<br>"
        "  (same BPM range + compatible key + close energy)<br>"
        "● Tight clusters = tracks that mix well together<br>"
        "● Isolated dots = unique-sounding tracks with few matches<br>"
        "● Dot color = BPM (yellow=fast, purple=slow)<br>"
        "● Dot size = number of similar tracks connected to it<br>"
        f"<br><i>Showing {len(G.nodes)} tracks · {len(G.edges)} similarity links</i><br>"
        f"<i>Min similarity score to draw a link: {threshold}</i>"
    )

    fig = go.Figure(
        data=[edge_trace, node_trace],
        layout=go.Layout(
            title=dict(
                text="Track Similarity Network — Which Tracks Sound Alike?",
                font=dict(size=17, color="white"),
                x=0.5, xanchor="center",
            ),
            showlegend=False,
            hovermode="closest",
            margin=dict(b=20, l=10, r=10, t=60),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            paper_bgcolor="#0c0c14",
            plot_bgcolor="#0c0c14",
            font=dict(color="white", family="monospace"),
            annotations=[
                dict(
                    text=how_to_read,
                    align="left",
                    showarrow=False,
                    xref="paper", yref="paper",
                    x=0.01, y=0.01,
                    xanchor="left", yanchor="bottom",
                    bgcolor="rgba(10,10,30,0.82)",
                    bordercolor="#2a3a5a",
                    borderwidth=1,
                    borderpad=8,
                    font=dict(size=10, color="#a8b8d0"),
                ),
            ],
        ),
    )

    out = GRAPHS_DIR / "similarity_graph.html"
    fig.write_html(str(out), include_plotlyjs="cdn")
    print(f"[graphs] similarity_graph → {out}  ({len(G.nodes)} nodes, {len(G.edges)} edges)")


def build_bpm_scatter(docs: list[dict]) -> None:
    """BPM vs Energy scatter with BPM band zones, colored by genre."""
    try:
        import plotly.graph_objects as go
    except ImportError as e:
        print(f"[graphs] Missing dependency: {e}"); return

    genre_map: dict[str, dict] = {}
    for doc in docs:
        af    = doc.get("audio_features", {})
        bpm   = af.get("bpm")
        rms   = af.get("rms_energy")
        sc    = af.get("spectral_centroid_mean")
        genre = doc.get("genre_folder", "Unknown")
        if bpm is None or rms is None:
            continue
        g = genre_map.setdefault(genre, {"x": [], "y": [], "sz": [], "hover": []})
        g["x"].append(float(bpm))
        g["y"].append(float(rms))
        # dot size = spectral brightness (bigger = brighter/more treble)
        g["sz"].append(max(5, min(20, (sc or 1500) / 180)) if sc else 8)
        g["hover"].append(
            f"<b>{_safe_label(doc)}</b><br>"
            f"━━━━━━━━━━━━━━━━━<br>"
            f"🎵 BPM: <b>{bpm}</b><br>"
            f"⚡ Energy (RMS): <b>{round(float(rms), 3)}</b><br>"
            f"🔆 Brightness (SC): <b>{round(float(sc), 0) if sc else '?'}</b><br>"
            f"🎹 Camelot: <b>{af.get('camelot', '?')}</b><br>"
            f"🎼 Genre: <b>{genre}</b>"
        )

    # BPM band background zones
    band_shapes = []
    band_labels_annot = []
    band_zones = [
        (60,  90,  "rgba(80,40,120,0.12)",  "Slow / Ballads"),
        (90,  110, "rgba(40,80,120,0.12)",  "Mid / Hip-Hop"),
        (110, 125, "rgba(40,120,80,0.12)",  "Groove / Pop"),
        (125, 140, "rgba(120,120,40,0.12)", "House"),
        (140, 160, "rgba(160,80,40,0.12)",  "Intense"),
        (160, 200, "rgba(160,40,40,0.12)",  "DnB / Fast"),
    ]
    for lo, hi, color, label in band_zones:
        band_shapes.append(dict(
            type="rect", xref="x", yref="paper",
            x0=lo, x1=hi, y0=0, y1=1,
            fillcolor=color, line_width=0, layer="below",
        ))
        band_labels_annot.append(dict(
            x=(lo + hi) / 2, y=1.01, xref="x", yref="paper",
            text=label, showarrow=False,
            font=dict(size=8, color="#888"),
            textangle=-30, xanchor="center",
        ))

    traces = []
    for genre, g in sorted(genre_map.items()):
        traces.append(go.Scatter(
            x=g["x"], y=g["y"],
            mode="markers",
            name=genre,
            marker=dict(
                size=g["sz"], opacity=0.82,
                line=dict(width=0.4, color="rgba(255,255,255,0.3)"),
            ),
            hovertext=g["hover"], hoverinfo="text",
        ))

    how_to_read = (
        "<b>HOW TO READ THIS CHART</b><br>"
        "● Each dot = one track<br>"
        "● X-axis: BPM (tempo) — left=slow, right=fast<br>"
        "● Y-axis: Energy (RMS) — bottom=quiet, top=loud/intense<br>"
        "● Dot size: Spectral brightness — bigger=more treble/brighter sound<br>"
        "● Dot color: Genre<br>"
        "● Background bands: BPM zones (Slow → DnB)<br>"
        "<br><b>What you learn:</b><br>"
        "Clusters in the same zone = tracks that can be mixed together.<br>"
        "High-right = high-energy fast tracks. Low-left = chill/slow."
    )

    fig = go.Figure(data=traces)
    fig.update_layout(
        title=dict(
            text="BPM vs Energy — Where Does Each Track Sit in Your Library?",
            font=dict(size=16, color="white"),
            x=0.5, xanchor="center",
        ),
        xaxis=dict(
            title=dict(text="BPM  (Tempo — beats per minute)", font=dict(size=12)),
            gridcolor="#222", range=[50, 210],
        ),
        yaxis=dict(
            title=dict(text="RMS Energy  (0 = silent  →  0.5 = very loud)", font=dict(size=12)),
            gridcolor="#222",
        ),
        hovermode="closest",
        paper_bgcolor="#0c0c14",
        plot_bgcolor="#0e0e1a",
        font=dict(color="white", family="monospace"),
        legend=dict(
            bgcolor="rgba(15,15,30,0.9)", bordercolor="#2a3a5a",
            borderwidth=1, font=dict(size=10),
            title=dict(text="Genre", font=dict(size=11)),
        ),
        shapes=band_shapes,
        annotations=band_labels_annot + [
            dict(
                text=how_to_read,
                align="left", showarrow=False,
                xref="paper", yref="paper",
                x=1.0, y=0.0,
                xanchor="right", yanchor="bottom",
                bgcolor="rgba(10,10,30,0.82)",
                bordercolor="#2a3a5a", borderwidth=1, borderpad=8,
                font=dict(size=10, color="#a8b8d0"),
            ),
        ],
    )

    out = GRAPHS_DIR / "bpm_clusters.html"
    fig.write_html(str(out), include_plotlyjs="cdn")
    print(f"[graphs] bpm_clusters → {out}")


def build_camelot_graph(docs: list[dict]) -> None:
    """Camelot harmonic compatibility wheel.

    Outer ring = A keys (minor scale).
    Inner ring = B keys (major scale).
    Lines = safe harmonic mixing pairs.
    """
    try:
        import plotly.graph_objects as go
    except ImportError as e:
        print(f"[graphs] Missing dependency: {e}"); return

    key_bpms:   dict[str, list[float]] = defaultdict(list)
    key_tracks: dict[str, list[str]]   = defaultdict(list)
    for doc in docs:
        af  = doc.get("audio_features", {})
        cam = af.get("camelot", "")
        bpm = af.get("bpm")
        if cam:
            key_tracks[cam].append(_safe_label(doc))
            if bpm is not None:
                key_bpms[cam].append(float(bpm))

    if not key_tracks:
        print("[graphs] camelot_graph: no camelot data — skipping"); return

    positions: dict[str, tuple] = {}
    for n in range(1, 13):
        angle = math.radians((n - 1) * 30 - 90)
        positions[f"{n}A"] = (math.cos(angle) * 2.0, math.sin(angle) * 2.0)
        positions[f"{n}B"] = (math.cos(angle) * 1.15, math.sin(angle) * 1.15)

    # ring label markers (non-interactive, just for orientation)
    ring_annots = []
    for n in range(1, 13):
        angle = math.radians((n - 1) * 30 - 90)
        # outer ring tick
        ring_annots.append(dict(
            x=math.cos(angle) * 2.55, y=math.sin(angle) * 2.55,
            text=str(n), showarrow=False,
            font=dict(size=8, color="#445566"), xref="x", yref="y",
        ))

    # compatibility edges
    drawn: set[frozenset] = set()
    edge_x, edge_y = [], []
    all_keys_with_tracks = set(key_tracks.keys())
    for key in list(all_keys_with_tracks):
        if key not in positions:
            continue
        for nb in _camelot_neighbors(key):
            pair = frozenset({key, nb})
            if pair in drawn or nb not in positions:
                continue
            drawn.add(pair)
            x0, y0 = positions[key]; x1, y1 = positions[nb]
            edge_x += [x0, x1, None]; edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(width=1.2, color="rgba(100,200,255,0.28)"),
        hoverinfo="none", name="Harmonic link",
    )

    # ── A nodes (minor, outer ring) ───────────────────────────────────────────
    ax, ay, a_sz, a_col, a_txt, a_hov = [], [], [], [], [], []
    bx, by, b_sz, b_col, b_txt, b_hov = [], [], [], [], [], []

    for key, xy in sorted(positions.items()):
        count   = len(key_tracks.get(key, []))
        avg_bpm = round(sum(key_bpms[key]) / len(key_bpms[key]), 1) if key_bpms.get(key) else 0
        size    = max(10, min(40, count * 3 + 10))
        sample  = "<br>".join(key_tracks[key][:4]) if key in key_tracks else "— no tracks —"
        tip = (
            f"<b style='font-size:13px'>{key}</b>  "
            f"({'minor' if key.endswith('A') else 'major'})<br>"
            f"━━━━━━━━━━━━━━━━━<br>"
            f"🎵 Tracks in library: <b>{count}</b><br>"
            f"⏱ Avg BPM: <b>{avg_bpm}</b><br>"
            f"🔗 Compatible with: "
            f"<b>{', '.join(_camelot_neighbors(key))}</b><br>"
            f"<br><i>Sample tracks:</i><br>{sample}"
        )
        if key.endswith("A"):
            ax.append(xy[0]); ay.append(xy[1])
            a_sz.append(size); a_col.append(avg_bpm)
            a_txt.append(key); a_hov.append(tip)
        else:
            bx.append(xy[0]); by.append(xy[1])
            b_sz.append(size); b_col.append(avg_bpm)
            b_txt.append(key); b_hov.append(tip)

    a_trace = go.Scatter(
        x=ax, y=ay, mode="markers+text",
        text=a_txt, textposition="middle center",
        textfont=dict(size=9, color="white", family="monospace"),
        hovertext=a_hov, hoverinfo="text",
        name="A keys — Minor scale (outer ring)",
        marker=dict(
            size=a_sz, color=a_col, colorscale="Plasma",
            colorbar=dict(
                title=dict(text="Avg BPM", side="right"),
                thickness=12, len=0.5, x=1.02,
            ),
            symbol="circle",
            line=dict(width=1.5, color="rgba(180,140,255,0.6)"),
            opacity=0.9,
        ),
    )
    b_trace = go.Scatter(
        x=bx, y=by, mode="markers+text",
        text=b_txt, textposition="middle center",
        textfont=dict(size=9, color="white", family="monospace"),
        hovertext=b_hov, hoverinfo="text",
        name="B keys — Major scale (inner ring)",
        marker=dict(
            size=b_sz, color=b_col, colorscale="Viridis",
            showscale=False,
            symbol="square",
            line=dict(width=1.5, color="rgba(100,220,255,0.6)"),
            opacity=0.9,
        ),
    )

    how_to_read = (
        "<b>HOW TO READ THE CAMELOT WHEEL</b><br>"
        "● The wheel is used by DJs to mix tracks harmonically<br>"
        "  (so they don't clash musically)<br>"
        "<br>"
        "● <b>Outer ring (circles) = A keys = Minor scale</b><br>"
        "● <b>Inner ring (squares) = B keys = Major scale</b><br>"
        "● Numbers 1–12 = musical keys arranged in a circle<br>"
        "<br>"
        "● <b>Blue lines = safe mixing pairs</b> (adjacent keys,<br>"
        "  or same number across A↔B, sound good together)<br>"
        "<br>"
        "● <b>Dot size</b> = how many tracks in your library use that key<br>"
        "● <b>Dot color</b> = average BPM of those tracks<br>"
        "<br>"
        "<b>What you learn:</b><br>"
        "Large dots = your library's dominant keys.<br>"
        "Small/missing dots = keys you have very few tracks in.<br>"
        "Hover any key to see which tracks use it."
    )

    fig = go.Figure(data=[edge_trace, a_trace, b_trace])
    fig.update_layout(
        title=dict(
            text="Camelot Harmonic Compatibility Wheel — Safe Mixing Keys",
            font=dict(size=16, color="white"),
            x=0.5, xanchor="center",
        ),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(10,10,30,0.85)", bordercolor="#2a3a5a",
            borderwidth=1, font=dict(size=10), x=0.01, y=0.99,
            xanchor="left", yanchor="top",
        ),
        hovermode="closest",
        margin=dict(b=20, l=10, r=20, t=60),
        xaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            scaleanchor="y", scaleratio=1, range=[-3.2, 3.2],
        ),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-3.2, 3.2]),
        paper_bgcolor="#0c0c14", plot_bgcolor="#0c0c14",
        font=dict(color="white", family="monospace"),
        annotations=ring_annots + [
            dict(
                text="OUTER RING<br>A = Minor",
                x=0, y=2.75, xref="x", yref="y", showarrow=False,
                font=dict(size=9, color="#8866aa"), xanchor="center",
            ),
            dict(
                text="INNER RING<br>B = Major",
                x=0, y=1.4, xref="x", yref="y", showarrow=False,
                font=dict(size=9, color="#4488aa"), xanchor="center",
            ),
            dict(
                text=how_to_read,
                align="left", showarrow=False,
                xref="paper", yref="paper",
                x=1.0, y=0.0,
                xanchor="right", yanchor="bottom",
                bgcolor="rgba(10,10,30,0.82)",
                bordercolor="#2a3a5a", borderwidth=1, borderpad=8,
                font=dict(size=10, color="#a8b8d0"),
            ),
        ],
    )

    out = GRAPHS_DIR / "camelot_graph.html"
    fig.write_html(str(out), include_plotlyjs="cdn")
    print(f"[graphs] camelot_graph → {out}")


def build_energy_distribution(docs: list[dict]) -> None:
    """Energy analytics: histogram, per-genre box plots, spectral centroid, density scatter."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as e:
        print(f"[graphs] Missing dependency: {e}"); return

    genre_rms: dict[str, list[float]] = defaultdict(list)
    genre_sc:  dict[str, list[float]] = defaultdict(list)
    all_rms:   list[float] = []

    for doc in docs:
        af    = doc.get("audio_features", {})
        rms   = af.get("rms_energy")
        sc    = af.get("spectral_centroid_mean")
        genre = doc.get("genre_folder", "Unknown")
        if rms is not None:
            genre_rms[genre].append(float(rms))
            all_rms.append(float(rms))
        if sc is not None:
            genre_sc[genre].append(float(sc))

    if not all_rms:
        print("[graphs] energy_distribution: no rms data — skipping"); return

    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            "① Energy Distribution — How loud/intense is your full library?",
            "② Energy by Genre — Which genres are louder?",
            "③ Spectral Brightness by Genre — Which genres sound brighter/darker?",
            "④ Energy vs Brightness — Are louder tracks also brighter?",
        ),
        vertical_spacing=0.14,
        horizontal_spacing=0.1,
    )

    # ① full RMS histogram with zone lines
    fig.add_trace(
        go.Histogram(
            x=all_rms, nbinsx=40,
            name="All tracks",
            marker_color="#7b68ee",
            hovertemplate="Energy: %{x:.3f}<br>Track count: %{y}<extra></extra>",
        ),
        row=1, col=1,
    )
    # energy zone reference lines
    for val, label, color in [
        (RMS_LOW_MAX,  "Quiet threshold",     "rgba(80,200,255,0.5)"),
        (RMS_MID_MAX,  "Mid-energy threshold","rgba(80,255,140,0.5)"),
        (RMS_HIGH_MAX, "High-energy threshold","rgba(255,180,60,0.5)"),
    ]:
        fig.add_vline(x=val, line_dash="dot", line_color=color, row=1, col=1)
        fig.add_annotation(
            x=val, y=1.04, xref="x1", yref="paper",
            text=label, showarrow=False,
            font=dict(size=8, color=color), textangle=-30,
        )

    # ② energy box per genre — showlegend=True so genre names are visible
    genre_colors = [
        "#7b68ee","#ff6b9d","#4fc3f7","#81c784","#ffb74d",
        "#e57373","#ba68c8","#4dd0e1","#aed581","#ffd54f",
    ]
    for idx, (genre, vals) in enumerate(sorted(genre_rms.items())):
        fig.add_trace(
            go.Box(
                y=vals, name=genre,
                boxpoints="outliers", marker_size=3, line_width=1.2,
                marker_color=genre_colors[idx % len(genre_colors)],
                hovertemplate=(
                    f"<b>{genre}</b><br>"
                    "Energy: %{y:.3f}<extra></extra>"
                ),
            ),
            row=1, col=2,
        )

    # ③ spectral centroid box per genre
    for idx, (genre, vals) in enumerate(sorted(genre_sc.items())):
        fig.add_trace(
            go.Box(
                y=vals, name=genre, showlegend=False,
                boxpoints="outliers", marker_size=3, line_width=1.2,
                marker_color=genre_colors[idx % len(genre_colors)],
                hovertemplate=(
                    f"<b>{genre}</b><br>"
                    "Brightness (Hz): %{y:.0f}<extra></extra>"
                ),
            ),
            row=2, col=1,
        )
    # brightness zone lines
    fig.add_hline(y=SC_DARK_MAX,   line_dash="dot",
                  line_color="rgba(100,160,255,0.5)", row=2, col=1)
    fig.add_hline(y=SC_BRIGHT_MIN, line_dash="dot",
                  line_color="rgba(255,220,80,0.5)",  row=2, col=1)
    fig.add_annotation(
        x=1.0, y=SC_DARK_MAX, xref="x3 domain", yref="y3",
        text="← Dark", showarrow=False, font=dict(size=8, color="#6699cc"),
    )
    fig.add_annotation(
        x=1.0, y=SC_BRIGHT_MIN, xref="x3 domain", yref="y3",
        text="← Bright", showarrow=False, font=dict(size=8, color="#ccaa33"),
    )

    # ④ energy vs spectral centroid scatter
    sc_x, rms_y, hover_d, sc_colors = [], [], [], []
    genre_list = sorted(genre_rms.keys())
    genre_idx  = {g: i for i, g in enumerate(genre_list)}
    for doc in docs:
        af    = doc.get("audio_features", {})
        rms   = af.get("rms_energy")
        sc    = af.get("spectral_centroid_mean")
        genre = doc.get("genre_folder", "Unknown")
        if rms is not None and sc is not None:
            sc_x.append(float(sc)); rms_y.append(float(rms))
            sc_colors.append(genre_idx.get(genre, 0))
            hover_d.append(
                f"<b>{_safe_label(doc)}</b><br>"
                f"Energy: {round(float(rms), 3)}<br>"
                f"Brightness: {round(float(sc), 0)} Hz<br>"
                f"Genre: {genre}"
            )
    fig.add_trace(
        go.Scatter(
            x=sc_x, y=rms_y, mode="markers",
            marker=dict(
                size=5, color=sc_colors,
                colorscale="Rainbow", opacity=0.65,
                line=dict(width=0.2, color="white"),
            ),
            hovertext=hover_d, hoverinfo="text",
            name="Track",
            showlegend=False,
        ),
        row=2, col=2,
    )

    # axis titles on all subplots
    fig.update_xaxes(title_text="RMS Energy  (0=silent → 0.5=very loud)",
                     row=1, col=1, gridcolor="#222")
    fig.update_yaxes(title_text="Number of tracks", row=1, col=1, gridcolor="#222")
    fig.update_xaxes(title_text="Genre", row=1, col=2, gridcolor="#222")
    fig.update_yaxes(title_text="RMS Energy", row=1, col=2, gridcolor="#222")
    fig.update_xaxes(title_text="Genre", row=2, col=1, gridcolor="#222")
    fig.update_yaxes(title_text="Spectral Centroid (Hz) — higher = brighter",
                     row=2, col=1, gridcolor="#222")
    fig.update_xaxes(title_text="Spectral Centroid (Hz)  — Brightness",
                     row=2, col=2, gridcolor="#222")
    fig.update_yaxes(title_text="RMS Energy  — Loudness",
                     row=2, col=2, gridcolor="#222")

    how_to_read = (
        "<b>HOW TO READ THESE 4 PANELS</b><br>"
        "<b>①</b> Shape of your library's loudness — most tracks cluster where?<br>"
        "   Dotted lines show quiet / mid / loud thresholds.<br>"
        "<b>②</b> Genre energy comparison — box = typical range,<br>"
        "   dots = outlier tracks in each genre.<br>"
        "<b>③</b> Spectral brightness — high Hz = bright/trebly (e.g. EDM).<br>"
        "   Low Hz = dark/bassy (e.g. DnB, hip-hop).<br>"
        "<b>④</b> Correlation — if loud tracks are also bright,<br>"
        "   you'll see dots in the top-right corner.<br>"
        "<br><b>Key terms:</b><br>"
        "RMS Energy = average loudness/intensity of the audio waveform.<br>"
        "Spectral Centroid = average frequency (brightness/darkness of tone)."
    )

    fig.update_layout(
        title=dict(
            text="Energy & Brightness Analytics — Understanding Your Library's Sound Profile",
            font=dict(size=15, color="white"),
            x=0.5, xanchor="center",
        ),
        paper_bgcolor="#0c0c14",
        plot_bgcolor="#0e0e1a",
        font=dict(color="white", family="monospace"),
        height=900,
        showlegend=True,
        legend=dict(
            bgcolor="rgba(10,10,30,0.85)", bordercolor="#2a3a5a",
            borderwidth=1, font=dict(size=9),
            title=dict(text="Genre", font=dict(size=10)),
            x=1.02, y=0.75, xanchor="left",
        ),
        annotations=[
            dict(
                text=how_to_read,
                align="left", showarrow=False,
                xref="paper", yref="paper",
                x=0.0, y=-0.08,
                xanchor="left", yanchor="top",
                bgcolor="rgba(10,10,30,0.82)",
                bordercolor="#2a3a5a", borderwidth=1, borderpad=10,
                font=dict(size=10, color="#a8b8d0"),
            ),
        ],
    )

    out = GRAPHS_DIR / "energy_distribution.html"
    fig.write_html(str(out), include_plotlyjs="cdn")
    print(f"[graphs] energy_distribution → {out}")


# ── PyVis physics similarity graph ───────────────────────────────────────────

# Genre → neon hex color (dark-theme palette)
_GENRE_COLORS: dict[str, str] = {
    "Hip-Hop":       "#bf5fff",
    "R&B":           "#ff5faf",
    "Pop":           "#5fd7ff",
    "Electronic":    "#00ffcc",
    "House":         "#00d7ff",
    "Tech-House":    "#00d7af",
    "Drum & Bass":   "#ff5f00",
    "Jungle":        "#ff8700",
    "Bollywood":     "#ffaf00",
    "Afrobeats":     "#d7ff00",
    "Dancehall":     "#afd700",
    "Reggaeton":     "#5fff87",
    "Latin":         "#5faf5f",
    "Rock":          "#ff5f5f",
    "Soul":          "#d75faf",
    "Jazz":          "#af87ff",
    "Classical":     "#87afff",
    "Unknown":       "#4e4e4e",
}
_DEFAULT_NODE_COLOR = "#5f87af"


def _genre_color(genre: str) -> str:
    for key, color in _GENRE_COLORS.items():
        if key.lower() in genre.lower():
            return color
    return _DEFAULT_NODE_COLOR


def _rms_to_node_size(rms) -> int:
    """Map RMS energy [0.0–0.5] → node size [10–38]."""
    if rms is None:
        return 16
    return max(10, min(38, int(float(rms) * 100 + 10)))


# Distinct community palette — intentionally different from genre neons
_COMMUNITY_PALETTE = [
    "#ff6b6b", "#ffd93d", "#6bcb77", "#4d96ff", "#ff922b",
    "#da77f2", "#63e6be", "#748ffc", "#f783ac", "#a9e34b",
    "#74c0fc", "#ffa94d", "#38d9a9", "#e599f7", "#ff8787",
    "#ffe066", "#8ce99a", "#4dabf7", "#c084fc", "#fb923c",
]


def build_similarity_graph_pyvis(
    docs: list[dict],
    top_edges_per_node: int = 4,
    min_score: float = 0.45,
    max_nodes: int = 150,
) -> None:
    """Adaptive top-N edge similarity constellation via PyVis / vis.js.

    Community detection (Louvain → greedy fallback) drives node colors.
    Betweenness centrality drives node size.
    Labels hidden by default; revealed on hover via injected JS.
    """
    try:
        from pyvis.network import Network
        import networkx as nx
    except ImportError as e:
        print(f"[graphs] Missing dependency: {e}. Run: pip install pyvis networkx")
        return

    step  = max(1, len(docs) // max_nodes)
    nodes = docs[::step][:max_nodes]
    n     = len(nodes)

    # ── pairwise scores (upper triangle) ─────────────────────────────────────
    scores: dict[tuple[int, int], dict] = {}
    for i, a in enumerate(nodes):
        for j in range(i + 1, n):
            sim = _compute_similarity(
                a.get("audio_features", {}),
                nodes[j].get("audio_features", {}),
            )
            if sim["score"] >= min_score:
                scores[(i, j)] = sim

    # per-node top-N kept edges
    node_best: dict[int, list[tuple[float, int]]] = {i: [] for i in range(n)}
    for (i, j), sim in scores.items():
        node_best[i].append((sim["score"], j))
        node_best[j].append((sim["score"], i))

    kept_pairs: set[frozenset] = set()
    for i, candidates in node_best.items():
        candidates.sort(reverse=True)
        for score, j in candidates[:top_edges_per_node]:
            kept_pairs.add(frozenset({i, j}))

    # degree
    node_degree: dict[int, int] = defaultdict(int)
    for pair in kept_pairs:
        for idx in sorted(pair):
            node_degree[idx] += 1

    # ── community detection ───────────────────────────────────────────────────
    G_nx = nx.Graph()
    G_nx.add_nodes_from(range(n))
    for pair in kept_pairs:
        i, j = sorted(pair)
        w = scores.get((i, j), {}).get("score", 0.5)
        G_nx.add_edge(i, j, weight=w)

    try:
        raw_comms = list(nx.community.louvain_communities(G_nx, seed=42))
        comm_algo = "Louvain"
    except Exception:
        try:
            raw_comms = list(nx.community.greedy_modularity_communities(G_nx))
            comm_algo = "Greedy Modularity"
        except Exception:
            raw_comms = [set(range(n))]
            comm_algo = "None"

    # sort largest community first so most-connected cluster = index 0
    raw_comms.sort(key=len, reverse=True)
    partition: dict[int, int] = {}
    for cid, members in enumerate(raw_comms):
        for node_idx in members:
            partition[node_idx] = cid

    num_comms   = len(raw_comms)
    comm_colors = (
        _COMMUNITY_PALETTE[:num_comms]
        + ["#607080"] * max(0, num_comms - len(_COMMUNITY_PALETTE))
    )

    # ── betweenness centrality ────────────────────────────────────────────────
    try:
        centrality = nx.betweenness_centrality(G_nx, normalized=True, weight="weight")
    except Exception:
        centrality = {i: 0.0 for i in range(n)}

    # ── track dominant genre + avg BPM per community ─────────────────────────
    comm_genre_count: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    comm_bpm_vals:    dict[int, list[float]]     = defaultdict(list)
    for idx, doc in enumerate(nodes):
        cid   = partition.get(idx, 0)
        genre = doc.get("genre_folder", "Unknown")
        comm_genre_count[cid][genre] += 1
        bpm_v = doc.get("audio_features", {}).get("bpm")
        if bpm_v is not None:
            comm_bpm_vals[cid].append(float(bpm_v))

    community_meta = []
    for cid in range(num_comms):
        genre_counts = comm_genre_count.get(cid, {"Unknown": 1})
        top_genre    = max(genre_counts, key=lambda g: genre_counts[g])
        bpm_list     = comm_bpm_vals.get(cid, [])
        avg_bpm      = round(sum(bpm_list) / len(bpm_list)) if bpm_list else None
        community_meta.append({
            "id":      cid,
            "color":   comm_colors[cid],
            "size":    len(raw_comms[cid]),
            "genre":   top_genre,
            "avg_bpm": avg_bpm,
            "label":   f"Cluster {cid + 1}  {top_genre}  ({len(raw_comms[cid])} tracks)",
        })

    # ── build PyVis network ───────────────────────────────────────────────────
    net = Network(height="100vh", width="100%", bgcolor="#050508",
                  font_color="#b0b8cc", directed=False)

    net.set_options("""
{
  "physics": {
    "enabled": true,
    "solver": "barnesHut",
    "barnesHut": {
      "gravitationalConstant": -14000,
      "centralGravity": 0.12,
      "springLength": 200,
      "springConstant": 0.018,
      "damping": 0.09,
      "avoidOverlap": 0.8
    },
    "stabilization": {
      "enabled": true,
      "iterations": 250,
      "updateInterval": 15,
      "onlyDynamicEdges": false,
      "fit": true
    },
    "minVelocity": 0.4,
    "maxVelocity": 28,
    "timestep": 0.42
  },
  "edges": {
    "smooth": { "enabled": true, "type": "dynamic", "roundness": 0.45 },
    "scaling": { "min": 0.4, "max": 6 },
    "selectionWidth": 3,
    "hoverWidth": 2
  },
  "nodes": {
    "shape": "dot",
    "scaling": { "min": 12, "max": 50 },
    "font": { "size": 0, "face": "monospace", "color": "#ffffff",
              "strokeWidth": 2, "strokeColor": "#000010" },
    "borderWidth": 1,
    "shadow": { "enabled": true, "size": 18, "x": 0, "y": 0,
                "color": "rgba(0,0,0,0.75)" }
  },
  "interaction": {
    "hover": true,
    "tooltipDelay": 60,
    "navigationButtons": false,
    "keyboard": { "enabled": true, "bindToWindow": false },
    "zoomView": true,
    "dragView": true,
    "dragNodes": true,
    "multiselect": true,
    "selectConnectedEdges": true,
    "hoverConnectedEdges": true
  }
}
""")

    # ── add nodes ─────────────────────────────────────────────────────────────
    for idx, doc in enumerate(nodes):
        ik     = doc["identity_key"]
        af     = doc.get("audio_features", {})
        genre  = doc.get("genre_folder", "Unknown")
        rms    = af.get("rms_energy")
        bpm    = af.get("bpm")
        cam    = af.get("camelot") or "?"
        key    = af.get("key")    or "?"
        artist = (doc.get("artist") or "").strip()
        title  = (doc.get("title")  or ik).strip()

        degree    = node_degree.get(idx, 0)
        cid       = partition.get(idx, 0)
        cent      = centrality.get(idx, 0.0)
        c_color   = comm_colors[cid] if cid < len(comm_colors) else "#607080"
        c_glow    = _lighten(c_color)
        genre_col = _genre_color(genre)

        # size: base (energy) + centrality bonus + degree bonus
        base_sz   = _rms_to_node_size(rms)
        cent_bonus = int(cent * 26)
        deg_bonus  = min(10, degree * 2)
        if degree == 0:
            size, opacity, bw = max(8, base_sz - 3), 0.28, 1
            node_color = {
                "background": "#12121e",
                "border":     "#2a2a3a",
                "highlight":  {"background": c_color, "border": c_glow},
                "hover":      {"background": c_color, "border": "#ffffff"},
            }
        elif degree >= top_edges_per_node or cent > 0.05:
            # bridge / hub node — community color + bright glow
            size, opacity, bw = min(50, base_sz + cent_bonus + deg_bonus), 0.96, 2
            node_color = {
                "background": c_color,
                "border":     c_glow,
                "highlight":  {"background": "#ffffff", "border": c_glow},
                "hover":      {"background": c_glow,   "border": "#ffffff"},
            }
        else:
            size, opacity, bw = min(38, base_sz + cent_bonus + deg_bonus // 2), 0.78, 1
            node_color = {
                "background": c_color,
                "border":     c_color + "88",
                "highlight":  {"background": "#ffffff", "border": c_glow},
                "hover":      {"background": c_glow,   "border": "#ffffff"},
            }

        # readable short label (shown on hover via JS)
        short_title  = title[:16]  if len(title)  > 16  else title
        short_artist = artist[:13] if len(artist) > 13 else artist
        label = f"{short_title}\n{short_artist}" if short_artist else short_title

        rms_str  = f"{float(rms):.3f}"  if rms  is not None else "—"
        bpm_str  = str(int(bpm))        if bpm  is not None else "—"

        cent_pct = f"{cent * 100:.1f}%"
        deg_tag  = (
            "★ Hub"      if cent > 0.05 or degree >= top_edges_per_node
            else ("◦ Isolated" if degree == 0 else f"{degree} links")
        )
        cluster_label = community_meta[cid]["label"] if cid < len(community_meta) else f"Cluster {cid+1}"

        tooltip = (
            f"<div style='"
            f"font-family:monospace;font-size:12px;line-height:1.65;"
            f"padding:11px 15px;min-width:200px;"
            f"background:linear-gradient(135deg,#080812,#0e0e1e);"
            f"border:1px solid {c_color}55;"
            f"border-left:3px solid {c_color};"
            f"border-radius:7px;color:#ccd8ec;"
            f"box-shadow:0 0 22px {c_color}28'>"
            # title + artist
            f"<div style='color:{c_color};font-size:13px;font-weight:bold;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
            f"max-width:210px;margin-bottom:2px'>{title}</div>"
            f"<div style='color:#7080a0;font-size:11px;margin-bottom:8px'>{artist}</div>"
            # divider
            f"<div style='border-top:1px solid #1e2a40;margin-bottom:7px'></div>"
            # stats grid
            f"<div style='display:grid;grid-template-columns:auto 1fr;gap:2px 12px;"
            f"font-size:11px'>"
            f"<span style='color:#4a5a6a'>BPM</span>"
            f"<span style='color:#dde8ff'>{bpm_str}</span>"
            f"<span style='color:#4a5a6a'>Camelot</span>"
            f"<span style='color:{genre_col};font-weight:bold'>{cam}</span>"
            f"<span style='color:#4a5a6a'>Key</span>"
            f"<span style='color:#dde8ff'>{key}</span>"
            f"<span style='color:#4a5a6a'>Genre</span>"
            f"<span style='color:{genre_col}'>{genre}</span>"
            f"<span style='color:#4a5a6a'>Energy</span>"
            f"<span style='color:#dde8ff'>{rms_str}</span>"
            f"<span style='color:#4a5a6a'>Centrality</span>"
            f"<span style='color:#aaccff'>{cent_pct}  {deg_tag}</span>"
            f"<span style='color:#4a5a6a'>Cluster</span>"
            f"<span style='color:{c_color}'>{cluster_label}</span>"
            f"</div></div>"
        )

        net.add_node(
            ik,
            label=label,
            title=tooltip,
            color=node_color,
            size=size,
            opacity=opacity,
            borderWidth=bw,
            borderWidthSelected=4,
        )

    # ── add deduplicated top-N edges ──────────────────────────────────────────
    edge_count = 0
    for pair in kept_pairs:
        i, j = sorted(pair)
        sim  = scores.get((i, j))
        if sim is None:
            continue
        s     = sim["score"]
        width = round((s - min_score) / max(0.01, 1.0 - min_score) * 5.0 + 0.4, 2)
        alpha = round(0.07 + s * 0.28, 2)

        # same-community edges warmer; cross-community cooler
        ci, cj = partition.get(i, -1), partition.get(j, -1)
        cam_a  = nodes[i].get("audio_features", {}).get("camelot", "")
        cam_b  = nodes[j].get("audio_features", {}).get("camelot", "")
        harmonic = cam_a and cam_b and (cam_a == cam_b or cam_b in _camelot_neighbors(cam_a))

        if ci == cj:
            edge_color = (
                f"rgba(200,160,255,{alpha + 0.12})"
                if harmonic else f"rgba(100,160,240,{alpha + 0.06})"
            )
        else:
            edge_color = f"rgba(60,90,150,{max(0.04, alpha - 0.04)})"

        net.add_edge(
            nodes[i]["identity_key"],
            nodes[j]["identity_key"],
            value=width,
            color=edge_color,
            title=(
                f"<span style='font-family:monospace;font-size:11px;color:#99aacc'>"
                f"score <b style='color:#aaddff'>{s}</b>"
                f"{'  &#9836; harmonic' if harmonic else ''}"
                f"{'  &#128279; same cluster' if ci == cj else ''}"
                f"<br>camelot <b>{sim['camelot_score']}</b> &nbsp; "
                f"bpm <b>{sim['bpm_score']}</b> &nbsp; "
                f"energy <b>{sim['energy_score']}</b></span>"
            ),
        )
        edge_count += 1

    # ── collect JS metadata for interactive features ──────────────────────────
    node_metas = []
    for idx, doc in enumerate(nodes):
        af = doc.get("audio_features", {})
        node_metas.append({
            "id":     doc["identity_key"],
            "title":  (doc.get("title")  or doc["identity_key"]).strip(),
            "artist": (doc.get("artist") or "").strip(),
            "bpm":    af.get("bpm"),
            "cid":    partition.get(idx, 0),
            "cent":   round(centrality.get(idx, 0.0), 4),
        })

    edge_metas = []
    for pair in kept_pairs:
        i2, j2 = sorted(pair)
        sim2 = scores.get((i2, j2))
        if sim2:
            edge_metas.append({
                "from":  nodes[i2]["identity_key"],
                "to":    nodes[j2]["identity_key"],
                "score": round(sim2["score"], 3),
            })

    sorted_cents = sorted([(centrality.get(i, 0.0), i) for i in range(n)], reverse=True)
    bridge_cutoff = max(5, n // 10)
    bridge_ids = [
        nodes[i]["identity_key"]
        for _, i in sorted_cents[:bridge_cutoff]
        if centrality.get(i, 0.0) >= 0.03
    ]

    _inject_pyvis_controls(
        net, min_score, len(nodes), edge_count, top_edges_per_node,
        community_meta=community_meta, comm_algo=comm_algo,
        node_metas=node_metas, edge_metas=edge_metas, bridge_ids=bridge_ids,
    )
    print(
        f"[graphs] similarity_graph_3d → {GRAPHS_DIR / 'similarity_graph_3d.html'}"
        f"  ({len(nodes)} nodes, {edge_count} edges, "
        f"{num_comms} communities [{comm_algo}])"
    )


def _lighten(hex_color: str) -> str:
    """Lighten a hex color by blending toward white ~30%."""
    try:
        h  = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r  = min(255, r + 60); g = min(255, g + 60); b = min(255, b + 60)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


def _inject_pyvis_controls(
    net,
    threshold: float,
    node_count: int,
    edge_count: int = 0,
    top_n: int = 4,
    community_meta: list = None,
    comm_algo: str = "",
    node_metas: list = None,
    edge_metas: list = None,
    bridge_ids: list = None,
) -> None:
    """Save PyVis graph to HTML, then inject full intelligence explorer UI."""
    import tempfile
    import os as _os

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".html")
    _os.close(tmp_fd)
    net.save_graph(tmp_path)
    html = Path(tmp_path).read_text(encoding="utf-8")
    _os.unlink(tmp_path)

    community_meta = community_meta or []
    node_metas     = node_metas     or []
    edge_metas     = edge_metas     or []
    bridge_ids     = bridge_ids     or []

    # ── legend ────────────────────────────────────────────────────────────────
    if community_meta:
        legend_items = "".join(
            '<div class="legend-row" data-cid="{cid}">'
            '<div class="legend-dot" style="background:{col};box-shadow:0 0 6px {col}88"></div>'
            '<span>{lbl}</span></div>'.format(cid=cm["id"], col=cm["color"], lbl=cm["label"])
            for cm in community_meta
        )
        legend_title = "&#128280; Clusters"
    else:
        legend_items = "".join(
            '<div class="legend-row">'
            '<div class="legend-dot" style="background:{col};box-shadow:0 0 6px {col}88"></div>'
            '<span>{genre}</span></div>'.format(col=color, genre=genre)
            for genre, color in sorted(_GENRE_COLORS.items()) if genre != "Unknown"
        )
        legend_title = "&#127912; Genre"

    # ── BPM range for slider ──────────────────────────────────────────────────
    bpm_vals = [m["bpm"] for m in node_metas if m.get("bpm") is not None]
    bpm_min  = int(min(bpm_vals)) if bpm_vals else 60
    bpm_max  = int(max(bpm_vals)) if bpm_vals else 200

    # ── JS data ───────────────────────────────────────────────────────────────
    algo_tag  = f" [{comm_algo}]" if comm_algo else ""
    num_comms = len(community_meta)

    data_js = (
        "<script>\n"
        "const NODE_META = " + json.dumps(node_metas) + ";\n"
        "const EDGE_META = " + json.dumps(edge_metas) + ";\n"
        "const BRIDGE_IDS = " + json.dumps(bridge_ids) + ";\n"
        "const COMMUNITY_META = " + json.dumps(community_meta) + ";\n"
        "</script>\n"
    )

    stat_html = (
        f'<div class="gc-stat">'
        f'Nodes: <b>{node_count}</b> &nbsp; Edges: <b>{edge_count}</b><br>'
        f'Clusters: <b>{num_comms}</b>{algo_tag}<br>'
        f'Top-{top_n}/node &nbsp; Min score: {threshold}'
        f'</div>'
    )

    # ── community checkboxes with stats ──────────────────────────────────────
    def _comm_check_html(cm):
        bpm_tag = f' · {cm["avg_bpm"]} BPM' if cm.get("avg_bpm") else ""
        sublabel = f'{cm["genre"]}  {cm["size"]} tracks{bpm_tag}'
        return (
            '<label class="comm-check">'
            '<input type="checkbox" checked data-cid="{cid}" onchange="toggleCluster({cid},this.checked)">'
            '<span class="comm-dot" style="background:{col}"></span>'
            '<span class="comm-label">'
            '<b style="color:{col}">Cluster {n}</b>'
            '<span class="comm-sub">{sub}</span>'
            '</span></label>'
        ).format(cid=cm["id"], col=cm["color"], n=cm["id"]+1, sub=sublabel)

    comm_checks = "".join(_comm_check_html(cm) for cm in community_meta) \
        if community_meta else '<span style="color:#445566;font-size:10px">No clusters</span>'

    controls_html = (
        '<div id="graph-controls" class="gc-panel">'
        '<h4>&#9881; Controls</h4>'
        '<button class="gc-btn" id="btn-physics">&#9889; Pause Physics</button>'
        '<button class="gc-btn" onclick="safeNet(function(n){n.fit()})">&#8982; Fit View</button>'
        '<button class="gc-btn" id="btn-labels">&#128065; Show Labels</button>'
        '<button class="gc-btn" onclick="toggleFullscreen()">&#x26F6; Fullscreen</button>'
        '<button class="gc-btn" id="btn-bridge">&#11088; Bridge Tracks</button>'
        '<button class="gc-btn" id="btn-reset-focus" style="display:none;border-color:rgba(255,100,100,0.5);color:#ff9988">&#8634; Reset Focus</button>'
        + stat_html +
        '</div>'
    )

    search_html = (
        '<div id="search-panel" class="gc-panel">'
        '<h4>&#128269; Search</h4>'
        '<input id="search-input" type="text" placeholder="title or artist..." autocomplete="off">'
        '<div id="search-results"></div>'
        '</div>'
    )

    bpm_html = (
        '<div id="bpm-panel" class="gc-panel">'
        '<h4>&#9835; BPM Filter</h4>'
        f'<div class="range-row"><span id="bpm-lo">{bpm_min}</span> – <span id="bpm-hi">{bpm_max}</span></div>'
        f'<input id="bpm-min" type="range" min="{bpm_min}" max="{bpm_max}" value="{bpm_min}" step="1" oninput="onBpmChange()">'
        f'<input id="bpm-max" type="range" min="{bpm_min}" max="{bpm_max}" value="{bpm_max}" step="1" oninput="onBpmChange()">'
        '<div class="range-row" style="margin-top:10px"><span style="color:#445566">Edges</span></div>'
        '<div class="density-btns">'
        '<button class="gc-btn density-btn active" data-d="0" onclick="setDensity(0)">Sparse</button>'
        '<button class="gc-btn density-btn" data-d="1" onclick="setDensity(1)">Balanced</button>'
        '<button class="gc-btn density-btn" data-d="2" onclick="setDensity(2)">Dense</button>'
        '</div>'
        '</div>'
    )

    comm_panel_html = (
        '<div id="comm-panel" class="gc-panel">'
        '<h4>&#128280; Communities</h4>'
        + comm_checks +
        '<button class="gc-btn" style="margin-top:8px" onclick="showAllClusters()">&#10003; Show All</button>'
        '<button class="gc-btn" onclick="showBridgesOnly()">&#128279; Inter-Cluster Bridges</button>'
        '</div>'
    )

    focus_panel_html = (
        '<div id="focus-panel" class="gc-panel">'
        '<h4 id="fp-title">&#9673; Track Focus</h4>'
        '<div id="fp-body"></div>'
        '</div>'
    )

    legend_html = (
        '<div id="legend-panel" class="gc-panel">'
        f'<h4>{legend_title}</h4>'
        + legend_items +
        '</div>'
    )

    hint_html = (
        '<div id="hint-bar" class="gc-panel">'
        '<b>Hover</b> node → label &nbsp;|&nbsp; '
        '<b>Click</b> node → focus mode<br>'
        '<b>Drag</b> to reposition &nbsp;|&nbsp; '
        '<b>Scroll</b> to zoom &nbsp;|&nbsp; '
        '<b>Bright</b> nodes = bridge tracks'
        '</div>'
    )

    css = """\
<style>
* { box-sizing: border-box; }
body { margin: 0; overflow: hidden;
  background: radial-gradient(ellipse at 50% 40%, #07071a 0%, #020208 100%) !important; }

.gc-panel {
  position: fixed; z-index: 9999;
  background: rgba(6, 8, 22, 0.93);
  border: 1px solid rgba(60, 100, 180, 0.32);
  border-radius: 10px;
  padding: 11px 15px;
  font-family: 'Courier New', monospace;
  color: #c0cce0;
  box-shadow: 0 0 22px rgba(20, 70, 180, 0.16), inset 0 0 0 1px rgba(255,255,255,0.025);
  backdrop-filter: blur(7px);
  transition: opacity 0.25s ease;
}
.gc-panel h4 {
  margin: 0 0 9px; color: #88bbff;
  font-size: 11px; letter-spacing: 2px; text-transform: uppercase;
}
#graph-controls { top: 14px; left: 14px; min-width: 168px; }
#search-panel   { top: 14px; left: 198px; min-width: 220px; }
#bpm-panel      { top: 14px; left: 434px; min-width: 180px; }
#comm-panel     { top: 14px; right: 14px; min-width: 200px; max-height: 80vh; overflow-y: auto; }
#focus-panel    {
  top: 50%; right: 14px; transform: translateY(-50%);
  min-width: 220px; max-width: 260px;
  display: none;
  border-color: rgba(130, 200, 255, 0.4);
}
#legend-panel   { bottom: 14px; left: 14px; max-height: 240px; overflow-y: auto; }
#hint-bar       { bottom: 14px; right: 14px; font-size: 10px; color: #3a4a60; line-height: 1.8; }

.gc-btn {
  display: block; width: 100%; margin-top: 5px;
  background: rgba(12, 30, 65, 0.8);
  border: 1px solid rgba(40, 100, 180, 0.45);
  color: #88ccff; border-radius: 5px;
  padding: 5px 10px; cursor: pointer;
  font-family: 'Courier New', monospace; font-size: 11px;
  text-align: left;
  transition: background 0.18s, border-color 0.18s, color 0.18s;
}
.gc-btn:hover { background: rgba(22, 50, 110, 0.92); border-color: rgba(80,160,255,0.7); color: #bbddff; }
.gc-btn.active { background: rgba(30, 70, 160, 0.85); border-color: rgba(100,180,255,0.7); color: #ddeeff; }
.gc-stat {
  margin-top: 10px; padding-top: 8px;
  border-top: 1px solid rgba(40,80,140,0.28);
  font-size: 10px; color: #445566; line-height: 1.7;
}
.gc-stat b { color: #5577aa; }

#search-input {
  width: 100%; background: rgba(8, 14, 34, 0.9);
  border: 1px solid rgba(40, 100, 180, 0.45);
  border-radius: 5px; color: #b0c8e8;
  padding: 5px 9px; font-family: 'Courier New', monospace; font-size: 11px;
  outline: none; transition: border-color 0.2s;
}
#search-input:focus { border-color: rgba(100, 180, 255, 0.7); }
#search-results {
  margin-top: 6px; max-height: 160px; overflow-y: auto;
  scrollbar-width: thin; scrollbar-color: #223355 transparent;
}
.sr-item {
  padding: 4px 7px; cursor: pointer; border-radius: 4px;
  font-size: 10px; color: #8899bb; transition: background 0.12s;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sr-item:hover { background: rgba(30, 60, 130, 0.7); color: #cce0ff; }
.sr-item b { color: #88aadd; }

input[type=range] {
  width: 100%; accent-color: #4488cc; margin-top: 4px;
  background: transparent; cursor: pointer;
}
.range-row { font-size: 10px; color: #6688aa; margin-top: 3px; }
.density-btns { display: flex; gap: 4px; margin-top: 5px; }
.density-btn { flex: 1; text-align: center; }

.comm-check {
  display: flex; align-items: center; gap: 6px;
  font-size: 10px; color: #8899bb; margin: 4px 0; cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.comm-check input { cursor: pointer; accent-color: #4488cc; }
.comm-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.comm-label { display: flex; flex-direction: column; }
.comm-sub { font-size: 9px; color: #3a5070; margin-top: 1px; }

.legend-row { display: flex; align-items: center; gap: 7px; margin: 4px 0; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
#legend-panel span { font-size: 11px; color: #b0bcd0; }
#hint-bar b { color: #5577aa; }

#fp-body { font-size: 11px; line-height: 1.75; color: #99aabb; }
#fp-body b { color: #aaccff; }
#fp-body .fp-row { display: flex; justify-content: space-between; gap: 8px; }
#fp-body .fp-label { color: #3a5070; }

.vis-tooltip {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
  max-width: none !important;
  white-space: normal !important;
}
</style>"""

    js = """\
<script>
(function() {
  var _physicsOn   = true;
  var _labelsOn    = false;
  var _bridgeOnly  = false;
  var _focusMode   = false;
  var _focusedNode = null;
  var _hiddenClusters = {};
  var _bpmMin = null, _bpmMax = null;
  var _density = 1;
  var _bridgePulseTimer = null;
  var _tooltipFixed = false;

  /* ── helpers ──────────────────────────────────────────────────────────── */
  function safeNet(fn) {
    if (typeof network !== 'undefined') try { fn(network); } catch(e) {}
  }

  function nodeIds() {
    return (typeof nodes !== 'undefined') ? nodes.getIds() : [];
  }

  function edgeIds() {
    return (typeof edges !== 'undefined') ? edges.getIds() : [];
  }

  /* ── physics / view ───────────────────────────────────────────────────── */
  window.safeNet = safeNet;

  function togglePhysics() {
    _physicsOn = !_physicsOn;
    safeNet(function(n) { n.setOptions({ physics: { enabled: _physicsOn } }); });
    var btn = document.getElementById('btn-physics');
    if (btn) btn.textContent = _physicsOn ? '⏸ Pause Physics' : '▶ Resume Physics';
  }

  function toggleLabels() {
    _labelsOn = !_labelsOn;
    if (typeof nodes !== 'undefined') {
      nodes.update(nodeIds().map(function(id) {
        return { id: id, font: { size: _labelsOn ? 10 : 0 } };
      }));
    }
    var btn = document.getElementById('btn-labels');
    if (btn) btn.textContent = _labelsOn ? '🙈 Hide Labels' : '👁 Show Labels';
  }

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen && document.documentElement.requestFullscreen();
    } else {
      document.exitFullscreen && document.exitFullscreen();
    }
  }

  /* ── search ───────────────────────────────────────────────────────────── */
  function doSearch(q) {
    var res = document.getElementById('search-results');
    if (!res) return;
    if (!q.trim()) { res.innerHTML = ''; return; }
    var ql = q.toLowerCase();
    var matches = NODE_META.filter(function(m) {
      return m.title.toLowerCase().indexOf(ql) >= 0 || m.artist.toLowerCase().indexOf(ql) >= 0;
    }).slice(0, 12);
    res.innerHTML = matches.map(function(m) {
      var t = m.title.length > 22 ? m.title.slice(0,22)+'…' : m.title;
      var a = m.artist ? (' <b>'+m.artist.slice(0,16)+'</b>') : '';
      return '<div class="sr-item" data-nid="' + encodeURIComponent(m.id) + '">' + t + a + '</div>';
    }).join('');
  }

  window.zoomToNode = function(id) {
    if (typeof nodes === 'undefined') return;
    safeNet(function(n) {
      n.focus(id, { scale: 1.8, animation: { duration: 700, easingFunction: 'easeInOutQuad' } });
      n.selectNodes([id]);
    });
    nodes.update([{ id: id, font: { size: 13, color: '#ffff99' } }]);
    setTimeout(function() {
      nodes.update([{ id: id, font: { size: _labelsOn ? 10 : 0 } }]);
    }, 2200);
    enterFocusMode(id);
    document.getElementById('search-results').innerHTML = '';
    var inp = document.getElementById('search-input');
    if (inp) inp.value = '';
  };

  /* ── focus mode ───────────────────────────────────────────────────────── */
  function enterFocusMode(nodeId) {
    if (typeof nodes === 'undefined' || typeof network === 'undefined') return;
    _focusMode   = true;
    _focusedNode = nodeId;
    var connected = network.getConnectedNodes(nodeId);
    var connSet   = {};
    connected.forEach(function(id) { connSet[id] = true; });
    nodes.update(nodeIds().map(function(id) {
      if (id === nodeId || connSet[id]) return { id: id, opacity: 1.0 };
      return { id: id, opacity: 0.07 };
    }));
    var meta = NODE_META.find(function(m) { return m.id === nodeId; });
    if (meta) {
      var panel = document.getElementById('focus-panel');
      var title = document.getElementById('fp-title');
      var body  = document.getElementById('fp-body');
      if (panel && body) {
        title.textContent = '● ' + (meta.title.length > 22 ? meta.title.slice(0,22)+'…' : meta.title);
        var bpmStr  = meta.bpm ? Math.round(meta.bpm)+' BPM' : '—';
        var centPct = (meta.cent * 100).toFixed(1) + '%';
        var commCol = (COMMUNITY_META[meta.cid] || {}).color || '#88aacc';
        var commLbl = (COMMUNITY_META[meta.cid] || {}).label || ('Cluster '+(meta.cid+1));
        body.innerHTML =
          '<div class="fp-row"><span class="fp-label">Artist</span><b>'+meta.artist+'</b></div>' +
          '<div class="fp-row"><span class="fp-label">BPM</span><b>'+bpmStr+'</b></div>' +
          '<div class="fp-row"><span class="fp-label">Centrality</span><b>'+centPct+'</b></div>' +
          '<div class="fp-row"><span class="fp-label">Neighbors</span><b>'+connected.length+'</b></div>' +
          '<div class="fp-row"><span class="fp-label">Cluster</span>' +
          '<b style="color:'+commCol+'">'+commLbl+'</b></div>';
        panel.style.display = 'block';
      }
    }
    var btn = document.getElementById('btn-reset-focus');
    if (btn) btn.style.display = 'block';
  }

  function resetFocusMode() {
    _focusMode   = false;
    _focusedNode = null;
    if (typeof nodes !== 'undefined') {
      nodes.update(nodeIds().map(function(id) { return { id: id, opacity: 1 }; }));
    }
    var panel = document.getElementById('focus-panel');
    if (panel) panel.style.display = 'none';
    var btn = document.getElementById('btn-reset-focus');
    if (btn) btn.style.display = 'none';
    applyFilters();
  }

  /* ── bridge tracks ────────────────────────────────────────────────────── */
  function toggleBridgeOnly() {
    _bridgeOnly = !_bridgeOnly;
    var btn = document.getElementById('btn-bridge');
    if (btn) {
      btn.textContent = _bridgeOnly ? '✖ Show All' : '⭐ Bridge Tracks';
      btn.classList.toggle('active', _bridgeOnly);
    }
    applyFilters();
  }

  function startBridgePulse() {
    if (!BRIDGE_IDS.length || typeof nodes === 'undefined') return;
    var phase = 0;
    _bridgePulseTimer = setInterval(function() {
      if (typeof nodes === 'undefined') return;
      phase = 1 - phase;
      nodes.update(BRIDGE_IDS.map(function(id) {
        return { id: id, borderWidth: phase ? 4 : 2 };
      }));
    }, 900);
  }

  /* ── community controls ───────────────────────────────────────────────── */
  window.toggleCluster = function(cid, show) {
    _hiddenClusters[cid] = !show;
    applyFilters();
  };

  window.showAllClusters = function() {
    _hiddenClusters = {};
    document.querySelectorAll('.comm-check input').forEach(function(cb) { cb.checked = true; });
    applyFilters();
  };

  window.showBridgesOnly = function() {
    if (typeof nodes === 'undefined' || typeof edges === 'undefined') return;
    var bridgeSet = {};
    BRIDGE_IDS.forEach(function(id) { bridgeSet[id] = true; });
    // find cross-community edges
    edges.update(edgeIds().map(function(id) {
      var e  = edges.get(id);
      var ma = NODE_META.find(function(m) { return m.id === e.from; });
      var mb = NODE_META.find(function(m) { return m.id === e.to; });
      var cross = ma && mb && ma.cid !== mb.cid;
      return { id: id, hidden: !cross };
    }));
    nodes.update(nodeIds().map(function(id) {
      return { id: id, opacity: bridgeSet[id] ? 1.0 : 0.1 };
    }));
  };

  /* ── BPM filter ───────────────────────────────────────────────────────── */
  window.onBpmChange = function() {
    var lo = parseInt(document.getElementById('bpm-min').value);
    var hi = parseInt(document.getElementById('bpm-max').value);
    if (lo > hi) { lo = hi; document.getElementById('bpm-min').value = lo; }
    document.getElementById('bpm-lo').textContent = lo;
    document.getElementById('bpm-hi').textContent = hi;
    _bpmMin = lo; _bpmMax = hi;
    applyFilters();
  };

  /* ── edge density ─────────────────────────────────────────────────────── */
  window.setDensity = function(level) {
    _density = level;
    document.querySelectorAll('.density-btn').forEach(function(b) {
      b.classList.toggle('active', parseInt(b.dataset.d) === level);
    });
    applyEdgeDensity(level);
  };

  function applyEdgeDensity(level) {
    if (typeof edges === 'undefined' || !EDGE_META.length) return;
    var pcts   = [0.28, 0.60, 1.0];
    var pct    = pcts[level] || 0.6;
    var sorted = EDGE_META.slice().sort(function(a, b) { return b.score - a.score; });
    var keep   = Math.max(1, Math.ceil(sorted.length * pct));
    var keepSet = {};
    sorted.slice(0, keep).forEach(function(e) {
      keepSet[e.from + '|' + e.to] = true;
      keepSet[e.to + '|' + e.from] = true;
    });
    edges.update(edgeIds().map(function(id) {
      var e = edges.get(id);
      return { id: id, hidden: !keepSet[e.from + '|' + e.to] };
    }));
  }

  /* ── combined filter pass ─────────────────────────────────────────────── */
  function applyFilters() {
    if (typeof nodes === 'undefined') return;
    var bridgeSet = {};
    BRIDGE_IDS.forEach(function(id) { bridgeSet[id] = true; });
    nodes.update(NODE_META.map(function(m) {
      var hidden = false;
      if (_hiddenClusters[m.cid]) hidden = true;
      if (_bridgeOnly && !bridgeSet[m.id]) hidden = true;
      if (_bpmMin !== null && _bpmMax !== null && m.bpm !== null) {
        if (m.bpm < _bpmMin || m.bpm > _bpmMax) hidden = true;
      }
      return { id: m.id, hidden: hidden };
    }));
    applyEdgeDensity(_density);
  }

  /* ── hover / select events ────────────────────────────────────────────── */
  /* vis-network 9.x uses innerText for string titles (XSS guard).
     Convert every HTML string title to a DOM element so appendChild is used. */
  function fixTooltipHTML() {
    if (typeof nodes === 'undefined') return;
    var all = nodes.get();
    var upd = [];
    all.forEach(function(n) {
      if (typeof n.title === 'string' && n.title.indexOf('<') !== -1) {
        var el = document.createElement('div');
        el.innerHTML = n.title;
        upd.push({ id: n.id, title: el });
      }
    });
    if (upd.length) nodes.update(upd);
  }

  function attachEvents() {
    if (typeof network === 'undefined' || typeof nodes === 'undefined') return;
    network.on('hoverNode', function(p) {
      if (!_tooltipFixed) { fixTooltipHTML(); _tooltipFixed = true; }
      nodes.update([{ id: p.node, font: { size: 11, color: '#ffffff' } }]);
    });
    network.on('blurNode', function(p) {
      if (!_labelsOn) nodes.update([{ id: p.node, font: { size: 0 } }]);
    });
    network.on('click', function(p) {
      if (p.nodes && p.nodes.length > 0) {
        enterFocusMode(p.nodes[0]);
      } else if (_focusMode) {
        resetFocusMode();
      }
    });
    network.on('selectNode', function(p) {
      var connected = network.getConnectedNodes(p.nodes[0]);
      nodes.update(connected.map(function(id) {
        return { id: id, font: { size: 9, color: '#88aacc' } };
      }));
      nodes.update([{ id: p.nodes[0], font: { size: 12, color: '#ffffff' } }]);
    });
    network.on('deselectNode', function() {
      if (!_labelsOn) {
        nodes.update(nodeIds().map(function(id) { return { id: id, font: { size: 0 } }; }));
      }
    });
  }

  /* ── init ─────────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function() {
    var phyBtn = document.getElementById('btn-physics');
    if (phyBtn) phyBtn.addEventListener('click', togglePhysics);
    var lblBtn = document.getElementById('btn-labels');
    if (lblBtn) lblBtn.addEventListener('click', toggleLabels);
    var brBtn  = document.getElementById('btn-bridge');
    if (brBtn)  brBtn.addEventListener('click', toggleBridgeOnly);
    var rfBtn  = document.getElementById('btn-reset-focus');
    if (rfBtn)  rfBtn.addEventListener('click', resetFocusMode);
    var fsSelf = document.querySelector('[onclick="toggleFullscreen()"]');
    if (fsSelf) { fsSelf.removeAttribute('onclick'); fsSelf.addEventListener('click', toggleFullscreen); }

    var sinput = document.getElementById('search-input');
    if (sinput) {
      sinput.addEventListener('input', function() { doSearch(this.value); });
      sinput.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') { this.value = ''; document.getElementById('search-results').innerHTML = ''; }
      });
    }
    var sres = document.getElementById('search-results');
    if (sres) {
      sres.addEventListener('click', function(e) {
        var el = e.target.closest ? e.target.closest('.sr-item') : e.target;
        if (el && el.dataset && el.dataset.nid) zoomToNode(decodeURIComponent(el.dataset.nid));
      });
    }

    setTimeout(function() {
      attachEvents();
      startBridgePulse();
      setDensity(1);
      fixTooltipHTML();
    }, 700);
  });
})();
</script>"""

    overlay = "\n".join([data_js, css, controls_html, search_html, bpm_html, comm_panel_html, focus_panel_html, legend_html, hint_html, js])

    if "</body>" in html:
        patched = html.replace("</body>", overlay + "\n</body>", 1)
    else:
        patched = html + "\n" + overlay

    out_path = GRAPHS_DIR / "similarity_graph_3d.html"
    out_path.write_text(patched, encoding="utf-8")


def run_graph_visualizations(
    sim_threshold: float = 0.55,
    max_sim_nodes: int = 120,
) -> None:
    """Generate all four interactive HTML graph artifacts."""
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    docs = _load_docs_for_graphs()
    if not docs:
        print("[graphs] No documents with audio_features found.")
        return

    print(f"[graphs] Loaded {len(docs)} docs. Building visualizations...")
    build_similarity_graph(docs, threshold=sim_threshold, max_nodes=max_sim_nodes)
    build_bpm_scatter(docs)
    build_camelot_graph(docs)
    build_energy_distribution(docs)
    build_similarity_graph_pyvis(docs, min_score=sim_threshold, max_nodes=max_sim_nodes)
    print(f"\n[graphs] All artifacts → {GRAPHS_DIR}")


if __name__ == "__main__":
    import sys as _sys
    if "--similarity" in _sys.argv:
        sample = int(next(
            (a.split("=")[1] for a in _sys.argv if a.startswith("--sample=")), "50"
        ))
        run_similarity_report(sample_size=sample)
    elif "--playlist" in _sys.argv:
        seed_arg = next(
            (a.split("=")[1] for a in _sys.argv if a.startswith("--seed=")), None
        )
        if not seed_arg:
            print("Usage: --playlist --seed=<identity_key> [--length=20] [--flow=mixed|warmup|peak|cooldown]")
            _sys.exit(1)
        length_arg = int(next(
            (a.split("=")[1] for a in _sys.argv if a.startswith("--length=")), "20"
        ))
        flow_arg = next(
            (a.split("=")[1] for a in _sys.argv if a.startswith("--flow=")), "mixed"
        )
        run_playlist_report(seed_arg, length=length_arg, flow=flow_arg)
    elif "--graphs" in _sys.argv:
        thresh = float(next(
            (a.split("=")[1] for a in _sys.argv if a.startswith("--threshold=")), "0.55"
        ))
        nodes = int(next(
            (a.split("=")[1] for a in _sys.argv if a.startswith("--nodes=")), "120"
        ))
        run_graph_visualizations(sim_threshold=thresh, max_sim_nodes=nodes)
    else:
        run_analytics()
