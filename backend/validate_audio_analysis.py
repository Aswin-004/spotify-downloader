"""
Local Audio Analysis Validation Audit
======================================
Validates librosa BPM/key fallback quality across 100 mixed tracks.
Compares librosa output against existing Spotify-derived ID3 tags.
Detects: unstable BPM, DnB halftime errors, key normalization issues, missing Camelot.
Writes: reports/local_audio_analysis_validation.json
"""

import json
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path

# ── bootstrap backend sys.path ────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

from config import config
from bpm_key_service import detect_bpm_and_key, PITCH_CLASSES

# ── constants ─────────────────────────────────────────────────────────────────

BASE_DIR = Path(config.BASE_DOWNLOAD_DIR)
REPORTS_DIR = BACKEND_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

CAMELOT_MAP = {
    (0,  "maj"): "8B",  (0,  "min"): "5A",
    (1,  "maj"): "3B",  (1,  "min"): "12A",
    (2,  "maj"): "10B", (2,  "min"): "7A",
    (3,  "maj"): "5B",  (3,  "min"): "2A",
    (4,  "maj"): "12B", (4,  "min"): "9A",
    (5,  "maj"): "7B",  (5,  "min"): "4A",
    (6,  "maj"): "2B",  (6,  "min"): "11A",
    (7,  "maj"): "9B",  (7,  "min"): "6A",
    (8,  "maj"): "4B",  (8,  "min"): "1A",
    (9,  "maj"): "11B", (9,  "min"): "8A",
    (10, "maj"): "6B",  (10, "min"): "3A",
    (11, "maj"): "1B",  (11, "min"): "10A",
}

PITCH_TO_IDX = {p: i for i, p in enumerate(PITCH_CLASSES)}

# Genre slug → folder keyword patterns (case-insensitive substring match on path parts)
GENRE_PATTERNS = {
    "House":    ["house"],
    "DnB":      ["dnb", "drum", "drum and bass", "drum&bass", "d&b"],
    "UKG":      ["ukg", "uk garage", "ukgarage", "garage"],
    "Dubstep":  ["dubstep"],
    "Bollywood":["bollywood"],
    "Punjabi":  ["punjabi"],
}

# DnB BPM range after halving (detect false halftime): if genre==DnB and BPM in this range
DNB_HALFTIME_SUSPECT_RANGE = (75, 105)
DNB_NATIVE_RANGE            = (155, 200)  # authentic DnB BPM before any correction

# BPM deviation threshold to flag discord vs Spotify tag (>= this = large deviation)
BPM_LARGE_DEV_THRESHOLD = 15

SAMPLES_PER_GENRE = 17  # ~102 total across 6 genres


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_id3_tags(filepath: Path) -> dict:
    """Read existing Spotify-derived TBPM / TKEY / TXXX:CAMELOT tags."""
    out = {"spotify_bpm": None, "spotify_key": None, "spotify_camelot": None}
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(str(filepath))
        except ID3NoHeaderError:
            return out

        tbpm = tags.get("TBPM")
        if tbpm:
            try:
                out["spotify_bpm"] = int(float(str(tbpm)))
            except (ValueError, TypeError):
                pass

        tkey = tags.get("TKEY")
        if tkey:
            out["spotify_key"] = str(tkey).strip()

        for frame in tags.getall("TXXX"):
            desc = (frame.desc or "").upper()
            if desc in ("CAMELOT", "INITIALKEY"):
                out["spotify_camelot"] = str(frame.text[0]).strip()
                break
    except Exception:
        pass
    return out


def _to_camelot(key_root: str, key_mode: str) -> str | None:
    """Convert (key_root, key_mode) to Camelot notation."""
    if not key_root or not key_mode:
        return None
    idx = PITCH_TO_IDX.get(key_root)
    if idx is None:
        return None
    mode = "maj" if key_mode in ("maj", "major") else "min"
    return CAMELOT_MAP.get((idx, mode))


def _classify_genre(filepath: Path) -> str:
    """Infer genre slug from folder path parts."""
    parts_lower = [p.lower() for p in filepath.parts]
    for genre, keywords in GENRE_PATTERNS.items():
        for kw in keywords:
            if any(kw in part for part in parts_lower):
                return genre
    return "Unknown"


def _collect_files(per_genre: int) -> dict[str, list[Path]]:
    """Collect up to per_genre MP3 files per genre from BASE_DIR."""
    buckets: dict[str, list[Path]] = {g: [] for g in GENRE_PATTERNS}
    all_mp3s = list(BASE_DIR.rglob("*.mp3"))
    random.shuffle(all_mp3s)

    for mp3 in all_mp3s:
        genre = _classify_genre(mp3)
        if genre in buckets and len(buckets[genre]) < per_genre:
            buckets[genre].append(mp3)

        if all(len(v) >= per_genre for v in buckets.values()):
            break

    return buckets


# ── core audit ────────────────────────────────────────────────────────────────

def run_validation(samples_per_genre: int = SAMPLES_PER_GENRE) -> dict:
    random.seed(42)
    buckets = _collect_files(samples_per_genre)

    genre_stats: dict[str, dict] = {}
    all_issues_unstable_bpm:  list[dict] = []
    all_issues_halftime_dnb:  list[dict] = []
    all_issues_key_norm:      list[dict] = []
    all_issues_missing_camelot: list[dict] = []
    all_issues_bpm_large_dev: list[dict] = []
    failed_tracks:            list[dict] = []
    bpm_samples:              list[dict] = []
    key_samples:              list[dict] = []
    camelot_samples:          list[dict] = []

    total_files          = 0
    total_bpm_success    = 0
    total_key_success    = 0
    total_camelot_success= 0
    total_latency        = 0.0
    bpm_compared         = 0
    bpm_agree_count      = 0
    key_agree_count      = 0
    key_compared         = 0

    for genre, files in buckets.items():
        g_total = len(files)
        g_bpm_ok = g_key_ok = g_camelot_ok = g_fail = 0
        g_latency_sum = 0.0
        g_issues: list[str] = []

        for mp3 in files:
            existing = _read_id3_tags(mp3)
            t0 = time.perf_counter()
            result = detect_bpm_and_key(str(mp3), genre_hint=genre)
            elapsed = time.perf_counter() - t0
            total_files    += 1
            total_latency  += elapsed
            g_latency_sum  += elapsed

            if not result["analyzed"]:
                g_fail += 1
                failed_tracks.append({
                    "file":  mp3.name,
                    "genre": genre,
                    "error": result.get("error"),
                })
                continue

            bpm      = result["bpm"]
            key_root = result.get("key_root")
            key_mode = result.get("key_mode")
            camelot  = _to_camelot(key_root, key_mode)

            if bpm is not None:
                g_bpm_ok      += 1
                total_bpm_success += 1
            if key_root:
                g_key_ok      += 1
                total_key_success += 1
            if camelot:
                g_camelot_ok  += 1
                total_camelot_success += 1

            # ── issue detection ──────────────────────────────────────

            # 1. DnB halftime error: BPM suspiciously low for DnB
            if genre == "DnB" and bpm is not None:
                if DNB_HALFTIME_SUSPECT_RANGE[0] <= bpm <= DNB_HALFTIME_SUSPECT_RANGE[1]:
                    issue = {
                        "file": mp3.name,
                        "detected_bpm": bpm,
                        "probable_native_bpm": bpm * 2,
                        "note": "bpm_key_service halved BPM (>160 threshold) — DnB should be tagged at native tempo",
                    }
                    all_issues_halftime_dnb.append(issue)
                    g_issues.append(f"halftime_dnb:{mp3.name}")

            # 2. Missing Camelot mapping
            if key_root and not camelot:
                issue = {"file": mp3.name, "genre": genre, "key_root": key_root, "key_mode": key_mode}
                all_issues_missing_camelot.append(issue)
                g_issues.append(f"missing_camelot:{mp3.name}")

            # 3. Key normalization: key_root should be in PITCH_CLASSES
            if key_root and key_root not in PITCH_TO_IDX:
                issue = {"file": mp3.name, "genre": genre, "key_root": key_root}
                all_issues_key_norm.append(issue)
                g_issues.append(f"key_norm_error:{mp3.name}")

            # 4. Unstable BPM: BPM is None but analysis succeeded (key detected, bpm missed)
            if result["analyzed"] and bpm is None and key_root:
                issue = {"file": mp3.name, "genre": genre, "confidence": result.get("confidence")}
                all_issues_unstable_bpm.append(issue)
                g_issues.append(f"unstable_bpm:{mp3.name}")

            # 5. BPM large deviation vs Spotify tag
            sp_bpm = existing["spotify_bpm"]
            sp_key = existing["spotify_key"]
            if sp_bpm and bpm:
                bpm_compared += 1
                delta = abs(bpm - sp_bpm)
                if delta <= BPM_LARGE_DEV_THRESHOLD:
                    bpm_agree_count += 1
                else:
                    issue = {
                        "file": mp3.name,
                        "genre": genre,
                        "librosa_bpm": bpm,
                        "spotify_bpm": sp_bpm,
                        "delta": delta,
                    }
                    all_issues_bpm_large_dev.append(issue)
                    g_issues.append(f"bpm_deviation:{mp3.name}(Δ{delta})")

            if sp_key and key_root:
                key_compared += 1
                # TKEY tag written as "A min" / "D# maj" by our tagger
                # Fallback: handle "Am" / "A" compact notation too
                sp_key_norm = sp_key.strip()
                if " min" in sp_key_norm.lower():
                    sp_root = sp_key_norm.split()[0]
                    sp_mode = "min"
                elif " maj" in sp_key_norm.lower():
                    sp_root = sp_key_norm.split()[0]
                    sp_mode = "maj"
                elif sp_key_norm.endswith("m") and len(sp_key_norm) <= 3:
                    sp_root = sp_key_norm[:-1]
                    sp_mode = "min"
                else:
                    sp_root = sp_key_norm
                    sp_mode = "maj"
                lib_mode = "min" if key_mode == "min" else "maj"
                if sp_root == key_root and sp_mode == lib_mode:
                    key_agree_count += 1

            # ── collect samples (first 5 per genre) ─────────────────
            if len([s for s in bpm_samples if s.get("genre") == genre]) < 5:
                bpm_samples.append({
                    "file":        mp3.name,
                    "genre":       genre,
                    "librosa_bpm": bpm,
                    "spotify_bpm": existing["spotify_bpm"],
                    "latency_sec": round(elapsed, 2),
                })
            if len([s for s in key_samples if s.get("genre") == genre]) < 5:
                key_samples.append({
                    "file":       mp3.name,
                    "genre":      genre,
                    "key_root":   key_root,
                    "key_mode":   key_mode,
                    "spotify_key":existing["spotify_key"],
                    "confidence": round(result.get("confidence", 0.0), 3),
                })
            if camelot and len([s for s in camelot_samples if s.get("genre") == genre]) < 5:
                camelot_samples.append({
                    "file":    mp3.name,
                    "genre":   genre,
                    "camelot": camelot,
                    "spotify_camelot": existing["spotify_camelot"],
                })

        genre_stats[genre] = {
            "total":           g_total,
            "analyzed":        g_total - g_fail,
            "bpm_success":     g_bpm_ok,
            "key_success":     g_key_ok,
            "camelot_success": g_camelot_ok,
            "failed":          g_fail,
            "avg_latency_sec": round(g_latency_sum / g_total, 2) if g_total else 0.0,
            "issues":          g_issues,
        }

    # ── build report ──────────────────────────────────────────────────────────
    bpm_success_rate    = total_bpm_success    / total_files if total_files else 0.0
    key_success_rate    = total_key_success    / total_files if total_files else 0.0
    camelot_success_rate= total_camelot_success/ total_files if total_files else 0.0
    avg_latency         = total_latency        / total_files if total_files else 0.0

    # ── bottleneck assessment ─────────────────────────────────────────────────
    bottlenecks = []
    if all_issues_halftime_dnb:
        bottlenecks.append({
            "issue":      "DnB halftime BPM error",
            "severity":   "high",
            "count":      len(all_issues_halftime_dnb),
            "root_cause": (
                "bpm_key_service.py line 84: `if bpm > 160: bpm = bpm // 2` fires for native DnB "
                f"tempo (~160-180 BPM), halving it to {DNB_HALFTIME_SUSPECT_RANGE[0]}-{DNB_HALFTIME_SUSPECT_RANGE[1]} BPM. "
                "Fix: raise the halving guard to > 190, or skip halving when genre_folder contains 'dnb'."
            ),
            "fix": "raise threshold: `if bpm > 190:` in bpm_key_service.py line 84",
        })
    if all_issues_bpm_large_dev:
        bottlenecks.append({
            "issue":    "Large BPM deviation vs Spotify reference",
            "severity": "medium",
            "count":    len(all_issues_bpm_large_dev),
            "root_cause": (
                "librosa beat_track on 30s mid-section can disagree with Spotify's full-track analysis. "
                "Percussive-sparse sections (intros, breakdowns) reduce beat-tracking accuracy."
            ),
            "fix": "extend analysis window or use full-track median over multiple segments",
        })
    if all_issues_key_norm:
        bottlenecks.append({
            "issue":    "Key normalization error",
            "severity": "medium",
            "count":    len(all_issues_key_norm),
            "root_cause": "key_root returned by bpm_key_service not in PITCH_CLASSES — enharmonic alias (e.g. Db vs C#)",
            "fix": "add enharmonic alias normalization table in bpm_key_service",
        })
    if avg_latency > 5.0:
        bottlenecks.append({
            "issue":    "High per-track analysis latency",
            "severity": "medium",
            "count":    total_files,
            "root_cause": f"avg {avg_latency:.1f}s/track — 30s audio load + chroma_cqt is CPU-heavy",
            "fix": "reduce analysis_duration to 20s, or cache results in MongoDB after first run",
        })
    if not bottlenecks:
        bottlenecks.append({
            "issue":    "None critical",
            "severity": "low",
            "note":     "Analysis quality is acceptable; consider increasing sample coverage.",
        })

    report = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "library_base":  str(BASE_DIR),
        "summary": {
            "total_files":          total_files,
            "bpm_success_rate":     round(bpm_success_rate, 3),
            "key_success_rate":     round(key_success_rate, 3),
            "camelot_success_rate": round(camelot_success_rate, 3),
            "avg_latency_sec":      round(avg_latency, 2),
            "failed_analyses":      len(failed_tracks),
            "genres_covered":       [g for g, v in genre_stats.items() if v["total"] > 0],
        },
        "genre_breakdown": genre_stats,
        "detected_issues": {
            "halftime_dnb":        all_issues_halftime_dnb,
            "unstable_bpm":        all_issues_unstable_bpm,
            "bpm_large_deviation": all_issues_bpm_large_dev,
            "key_normalization":   all_issues_key_norm,
            "missing_camelot":     all_issues_missing_camelot,
        },
        "spotify_comparison": {
            "bpm_compared":        bpm_compared,
            "bpm_agreement_rate":  round(bpm_agree_count  / bpm_compared,  3) if bpm_compared  else None,
            "key_compared":        key_compared,
            "key_agreement_rate":  round(key_agree_count  / key_compared,  3) if key_compared  else None,
            "bpm_threshold_used":  BPM_LARGE_DEV_THRESHOLD,
        },
        "samples": {
            "bpm_samples":     bpm_samples,
            "key_samples":     key_samples,
            "camelot_samples": camelot_samples,
        },
        "failed_tracks": failed_tracks,
        "bottleneck_assessment": bottlenecks,
    }

    out_path = REPORTS_DIR / "local_audio_analysis_validation.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"[audit] Report written → {out_path}")
    _print_summary(report)
    return report


def _print_summary(report: dict):
    s = report["summary"]
    print("\n=== LOCAL AUDIO ANALYSIS VALIDATION ===")
    print(f"  Files audited  : {s['total_files']}")
    print(f"  BPM success    : {s['bpm_success_rate']*100:.1f}%")
    print(f"  Key success    : {s['key_success_rate']*100:.1f}%")
    print(f"  Camelot success: {s['camelot_success_rate']*100:.1f}%")
    print(f"  Avg latency    : {s['avg_latency_sec']:.2f}s/track")
    print(f"  Failed         : {s['failed_analyses']}")

    sc = report["spotify_comparison"]
    if sc["bpm_compared"]:
        print(f"\n  BPM agreement vs Spotify  : {sc['bpm_agreement_rate']*100:.1f}% "
              f"(threshold ±{sc['bpm_threshold_used']} BPM, n={sc['bpm_compared']})")
    if sc["key_compared"]:
        print(f"  Key agreement vs Spotify  : {sc['key_agreement_rate']*100:.1f}% "
              f"(n={sc['key_compared']})")

    issues = report["detected_issues"]
    print(f"\n  DnB halftime errors  : {len(issues['halftime_dnb'])}")
    print(f"  BPM large deviations : {len(issues['bpm_large_deviation'])}")
    print(f"  Unstable BPM         : {len(issues['unstable_bpm'])}")
    print(f"  Key normalization    : {len(issues['key_normalization'])}")
    print(f"  Missing Camelot      : {len(issues['missing_camelot'])}")

    print("\n  Genre breakdown:")
    for genre, gs in report["genre_breakdown"].items():
        if gs["total"] == 0:
            continue
        bpm_pct = gs["bpm_success"] / gs["total"] * 100
        key_pct = gs["key_success"] / gs["total"] * 100
        print(f"    {genre:<12} n={gs['total']:3d}  BPM={bpm_pct:.0f}%  key={key_pct:.0f}%  "
              f"lat={gs['avg_latency_sec']:.1f}s  fail={gs['failed']}")

    print("\n  Bottlenecks:")
    for b in report["bottleneck_assessment"]:
        sev = b.get("severity", "?").upper()
        print(f"    [{sev}] {b['issue']}" + (f" (n={b.get('count','')})" if b.get("count") else ""))
        if b.get("fix"):
            print(f"           → {b['fix']}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=SAMPLES_PER_GENRE,
                    help=f"files per genre (default {SAMPLES_PER_GENRE})")
    args = ap.parse_args()
    run_validation(samples_per_genre=args.samples)
