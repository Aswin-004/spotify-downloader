#!/usr/bin/env python3
"""
eval_genre_classifier.py
========================
MEASURE a genre classifier against YOUR library before trusting it to move files.

Ground truth = your existing folders (Library/<Genre>/...). For every track the classifier
predicts a genre; this tool reports, honestly:

  * coverage      how often it dares to answer at all
  * precision     how often its answers match your folders
  * ... at confidence thresholds (0.5 / 0.7 / 0.8 / 0.9) so you can pick a safe cut-off
  * per-folder    precision / recall / F1 for House, Techno, Trance, Bollywood, ...
  * confusion     "true Techno predicted as House: 23"
  * disagreements tracks where the classifier is CONFIDENT and your folder disagrees.
                  Either the classifier is wrong or the FILE IS MISFILED — this list is
                  also a to-do list for finding techno/trance sitting in House.
  * BPM report    the real BPM range of each of your folders (from ID3 TBPM), used to
                  calibrate BPM sanity checks from your data rather than guesses.

Read-only: it never moves, tags or deletes anything.

Caveat: folder labels are only as good as your sorting. The catch-all folders (Electronic,
NeedsReview, ...) are EXCLUDED as ground truth by default; a predicted "Electronic" counts as
"I don't know". A large disagreement count can mean the folder is wrong — check the CSV.

Usage:
    python eval_genre_classifier.py --classifier static                 # offline, instant
    python eval_genre_classifier.py --classifier evidence               # offline signals only
    python eval_genre_classifier.py --classifier evidence --online --per-folder 60
    python eval_genre_classifier.py --bpm-report --write-bpm-priors reports/bpm_priors.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Folders that are NOT a trustworthy label (catch-alls / holding areas).
DEFAULT_EXCLUDE = ("Electronic", "NeedsReview", "Quarantine", "Manual", "Duplicates", "_TO_DELETE")
# A prediction equal to one of these means "I don't know".
ABSTAIN_GENRES = {"", "Electronic", "NeedsReview"}
THRESHOLDS = (0.5, 0.7, 0.8, 0.9)
DISAGREEMENT_MIN_CONFIDENCE = 0.8


# ═══════════════════════════════════════════════════════════════════════════
# data
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Sample:
    path: str
    rel: str
    label: str                 # the folder it currently sits in = ground truth
    artist: str
    title: str
    bpm: Optional[float] = None
    spotify_id: str = ""


@dataclass
class Prediction:
    genre: str                 # a folder label such as "Techno"
    confidence: float
    source: str = ""
    verified: bool = True      # False = rests on artist-level tags / hints only (held for review)


def label_of(genre: str) -> str:
    """'Library/House' -> 'House'; 'NeedsReview/Artist' -> 'NeedsReview'; 'House' -> 'House'."""
    g = (genre or "").replace("\\", "/").strip("/")
    if g.startswith("Library/"):
        g = g[len("Library/"):]
    return g.split("/")[0] if g else ""


def _read_sample_tags(path: Path) -> dict:
    out = {"title": "", "artist": "", "bpm": None, "spotify_id": ""}
    try:
        from mutagen.id3 import ID3
        tags = ID3(str(path))
    except Exception:
        return out

    def first(key: str) -> str:
        frame = tags.get(key)
        text = getattr(frame, "text", None) if frame is not None else None
        return str(text[0]).strip() if text else ""

    out["title"], out["artist"] = first("TIT2"), first("TPE1")
    out["spotify_id"] = first("TXXX:SPOTIFY_ID")
    raw = first("TBPM")
    try:
        bpm = float(raw)
        out["bpm"] = bpm if 40 <= bpm <= 300 else None
    except (TypeError, ValueError):
        out["bpm"] = None
    return out


def collect_samples(
    root: Path,
    *,
    folder: str = "Library",
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    per_folder: Optional[int] = None,
    limit: Optional[int] = None,
    seed: int = 0,
) -> List[Sample]:
    """One Sample per .mp3 under root/folder, labelled by its first sub-folder."""
    base = root / folder if folder else root
    excluded = {e.lower() for e in exclude}
    by_label: Dict[str, List[Path]] = defaultdict(list)
    for p in sorted(base.rglob("*.mp3")):
        if not p.is_file():
            continue
        parts = p.relative_to(base).parts
        if len(parts) < 2:
            continue                                   # sits directly in the root: no label
        label = parts[0]
        if label.lower() in excluded:
            continue
        by_label[label].append(p)

    rng = random.Random(seed)
    chosen: List[Tuple[str, Path]] = []
    for label in sorted(by_label):
        files = by_label[label]
        if per_folder and len(files) > per_folder:
            files = rng.sample(files, per_folder)
        chosen.extend((label, f) for f in sorted(files))
    if limit:
        chosen = chosen[:limit]

    samples = []
    for label, p in chosen:
        t = _read_sample_tags(p)
        samples.append(Sample(
            path=str(p), rel=str(p.relative_to(root)) if p.is_relative_to(root) else str(p),
            label=label, artist=t["artist"], title=t["title"], bpm=t["bpm"], spotify_id=t["spotify_id"],
        ))
    return samples


# ═══════════════════════════════════════════════════════════════════════════
# metrics (pure)
# ═══════════════════════════════════════════════════════════════════════════

def _is_answer(pred: Optional[Prediction]) -> bool:
    return pred is not None and label_of(pred.genre) not in ABSTAIN_GENRES


def _ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def evaluate(
    samples: List[Sample],
    predictions: Dict[str, Optional[Prediction]],
    thresholds: Iterable[float] = THRESHOLDS,
    disagreement_min_confidence: float = DISAGREEMENT_MIN_CONFIDENCE,
) -> dict:
    rows = []
    for s in samples:
        p = predictions.get(s.path)
        answered = _is_answer(p)
        predicted = label_of(p.genre) if answered else ""
        rows.append((s, p, answered, predicted, answered and predicted == s.label))

    total = len(rows)
    answered_rows = [r for r in rows if r[2]]
    correct = sum(1 for r in answered_rows if r[4])

    by_threshold = []
    for t in thresholds:
        sel = [r for r in answered_rows if r[1].confidence >= t]
        by_threshold.append({
            "threshold": t,
            "answered": len(sel),
            "coverage": _ratio(len(sel), total),
            "precision": _ratio(sum(1 for r in sel if r[4]), len(sel)),
        })

    labels = sorted({s.label for s in samples})
    per_folder = {}
    for lab in labels:
        support = sum(1 for r in rows if r[0].label == lab)
        predicted_as = [r for r in answered_rows if r[3] == lab]
        tp = sum(1 for r in predicted_as if r[4])
        prec, rec = _ratio(tp, len(predicted_as)), _ratio(tp, support)
        per_folder[lab] = {
            "support": support, "predicted": len(predicted_as), "correct": tp,
            "precision": prec, "recall": rec,
            "f1": round(2 * prec * rec / (prec + rec), 4) if (prec + rec) else 0.0,
        }

    # The split that matters for automation: what would be MOVED unattended (verified) vs what
    # would only be OFFERED for review (unverified).
    by_trust = {}
    for name, flag in (("verified", True), ("unverified", False)):
        sel = [r for r in answered_rows if getattr(r[1], "verified", True) == flag]
        by_trust[name] = {"answered": len(sel), "coverage": _ratio(len(sel), total),
                          "precision": _ratio(sum(1 for r in sel if r[4]), len(sel))}

    confusion = Counter((r[0].label, r[3]) for r in answered_rows if not r[4])
    disagreements = sorted(
        ({"path": r[0].path, "artist": r[0].artist, "title": r[0].title, "folder": r[0].label,
          "predicted": r[3], "confidence": round(r[1].confidence, 3), "source": r[1].source}
         for r in answered_rows if not r[4] and r[1].confidence >= disagreement_min_confidence),
        key=lambda d: -d["confidence"],
    )

    return {
        "total": total,
        "answered": len(answered_rows),
        "coverage": _ratio(len(answered_rows), total),
        "precision": _ratio(correct, len(answered_rows)),
        "by_threshold": by_threshold,
        "by_trust": by_trust,
        "per_folder": per_folder,
        "confusion": [{"true": t, "predicted": p, "count": c} for (t, p), c in confusion.most_common(25)],
        "disagreements": disagreements,
    }


def percentile(sorted_vals: List[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def bpm_report(samples: List[Sample]) -> Dict[str, dict]:
    """Per folder: how many tracks have a BPM tag and where the middle 80% of them sit."""
    by_label: Dict[str, List[float]] = defaultdict(list)
    counts: Counter = Counter()
    for s in samples:
        counts[s.label] += 1
        if s.bpm:
            by_label[s.label].append(s.bpm)
    out = {}
    for lab in sorted(counts):
        vals = sorted(by_label.get(lab, []))
        out[lab] = {
            "tracks": counts[lab], "with_bpm": len(vals),
            "median": round(percentile(vals, 50), 1) if vals else None,
            "p10": round(percentile(vals, 10), 1) if vals else None,
            "p90": round(percentile(vals, 90), 1) if vals else None,
        }
    return out


def suggest_bpm_priors(report: Dict[str, dict], min_tracks: int = 15, pad: float = 2.0) -> Dict[str, List[float]]:
    """[p10 - pad, p90 + pad] per folder — only where there is enough data to mean something."""
    return {
        lab: [round(r["p10"] - pad, 1), round(r["p90"] + pad, 1)]
        for lab, r in report.items()
        if r["with_bpm"] >= min_tracks and r["p10"] is not None
    }


# ═══════════════════════════════════════════════════════════════════════════
# classifiers
# ═══════════════════════════════════════════════════════════════════════════

Classifier = Callable[[Sample], Optional[Prediction]]


def make_static_classifier() -> Classifier:
    """Offline + DB-free: the hard-coded artist overrides and the static knowledge base only."""
    from config import config
    from services.genre_router import _library_path, normalize_artist_key

    def predict(s: Sample) -> Optional[Prediction]:
        override = config.ARTIST_GENRE_OVERRIDE.get(normalize_artist_key(s.artist))
        if override:
            return Prediction(label_of(_library_path(override)), 1.0, "artist_override")
        try:
            from services.artist_knowledge_service import lookup_artist_knowledge
            kb = lookup_artist_knowledge(s.artist)
        except Exception:
            kb = None
        if kb:
            return Prediction(label_of(_library_path(kb["genre"])), float(kb["confidence"]), "knowledge_base")
        return None

    return predict


def make_evidence_classifier(online: bool, bpm_priors: Optional[dict] = None,
                             use_fingerprint: bool = False, use_llm: bool = False,
                             min_confidence: Optional[float] = None) -> Classifier:
    """The multi-signal voting engine (services/genre_evidence.py)."""
    from services.genre_evidence import classify_track, default_fetchers

    def predict(s: Sample) -> Optional[Prediction]:
        d = classify_track(s.artist, s.title, path=s.path, bpm=s.bpm, online=online, bpm_priors=bpm_priors,
                           use_fingerprint=use_fingerprint, use_llm=use_llm, min_confidence=min_confidence)
        if d is None or d.abstain:
            return None
        return Prediction(d.genre, d.confidence, "evidence:" + ",".join(d.sources), verified=d.verified)

    # Network answers are cached on disk; make sure the last few reach it.
    predict.flush = lambda: default_fetchers().cache.flush() if online else None
    return predict


CLASSIFIERS = {"static": lambda a: make_static_classifier(),
               "evidence": lambda a: make_evidence_classifier(
                   a.online, load_priors(a.bpm_priors), use_fingerprint=a.fingerprint, use_llm=a.llm,
                   min_confidence=a.min_confidence)}


def load_priors(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"(could not read BPM priors {path}: {exc} — using built-in defaults)")
        return None


def run_classifier(samples: List[Sample], predict: Classifier,
                   progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Optional[Prediction]]:
    out: Dict[str, Optional[Prediction]] = {}
    for i, s in enumerate(samples, 1):
        try:
            out[s.path] = predict(s)
        except Exception:
            out[s.path] = None                      # a crashing classifier abstains; it is never fatal
        if progress:
            progress(i, len(samples))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# output
# ═══════════════════════════════════════════════════════════════════════════

def render_summary(name: str, m: dict) -> str:
    L = [f"Classifier: {name}", "=" * 70,
         f"Tracks scored (excluding catch-all folders): {m['total']}",
         f"Answered: {m['answered']}  (coverage {m['coverage']:.1%})",
         f"Precision on answered: {m['precision']:.1%}", "",
         "Precision if you only trust answers at or above a confidence:"]
    for t in m["by_threshold"]:
        L.append(f"  >= {t['threshold']:.2f}   coverage {t['coverage']:>6.1%}   precision {t['precision']:>6.1%}"
                 f"   ({t['answered']} tracks)")
    v, u = m["by_trust"]["verified"], m["by_trust"]["unverified"]
    L += ["", "By trust (verified = would be moved unattended; unverified = only offered for review):",
          f"  verified     coverage {v['coverage']:>6.1%}   precision {v['precision']:>6.1%}   ({v['answered']} tracks)",
          f"  unverified   coverage {u['coverage']:>6.1%}   precision {u['precision']:>6.1%}   ({u['answered']} tracks)"]
    L += ["", f"{'Folder':<16}{'tracks':>7}{'predicted':>10}{'precision':>11}{'recall':>9}{'F1':>7}", "-" * 60]
    for lab, r in sorted(m["per_folder"].items(), key=lambda kv: -kv[1]["support"]):
        L.append(f"{lab:<16}{r['support']:>7}{r['predicted']:>10}{r['precision']:>11.1%}{r['recall']:>9.1%}{r['f1']:>7.2f}")
    if m["confusion"]:
        L += ["", "Most common mix-ups (your folder -> classifier said):"]
        L += [f"  {c['true']:<14} -> {c['predicted']:<14} {c['count']}" for c in m["confusion"][:10]]
    L += ["", f"Confident disagreements (>= {DISAGREEMENT_MIN_CONFIDENCE}): {len(m['disagreements'])}  "
              "<- misfiled tracks OR classifier mistakes; review the CSV"]
    return "\n".join(L)


def render_bpm(report: Dict[str, dict]) -> str:
    L = ["BPM per folder (from ID3 TBPM)", "=" * 70,
         f"{'Folder':<16}{'tracks':>7}{'w/ BPM':>8}{'median':>9}{'p10':>8}{'p90':>8}", "-" * 56]
    for lab, r in sorted(report.items(), key=lambda kv: -kv[1]["tracks"]):
        f = lambda v: f"{v:>8.1f}" if v is not None else f"{'-':>8}"
        L.append(f"{lab:<16}{r['tracks']:>7}{r['with_bpm']:>8}{f(r['median']):>9}{f(r['p10'])}{f(r['p90'])}")
    return "\n".join(L)


def write_outputs(name: str, metrics: dict, bpm: dict, out_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jp = out_dir / f"genre_eval_{name}_{stamp}.json"
    cp = out_dir / f"genre_eval_{name}_{stamp}_disagreements.csv"
    jp.write_text(json.dumps({"classifier": name, "metrics": {k: v for k, v in metrics.items() if k != "disagreements"},
                              "bpm": bpm}, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(cp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "title", "folder", "predicted", "confidence", "source"])
        w.writeheader()
        w.writerows(metrics["disagreements"])
    return jp, cp


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Measure a genre classifier against your library folders (read-only).")
    ap.add_argument("--root", help="library root (default: BASE_DOWNLOAD_DIR)")
    ap.add_argument("--folder", default="Library", help="folder under the root holding the genre folders")
    ap.add_argument("--classifier", choices=sorted(CLASSIFIERS), default="static")
    ap.add_argument("--online", action="store_true", help="let the evidence classifier call Last.fm / MusicBrainz")
    ap.add_argument("--min-confidence", type=float, default=0.3,
                    help="evidence classifier answer cut-off for THIS measurement (default 0.3 so the whole "
                         "precision-vs-confidence curve is visible; the live default is 0.6)")
    ap.add_argument("--fingerprint", action="store_true", help="with --online: also AcoustID-fingerprint each file (slow)")
    ap.add_argument("--llm", action="store_true", help="with --online: also add the (weak) Groq text-guess vote")
    ap.add_argument("--per-folder", type=int, help="random sample of at most N tracks per folder (limits API use)")
    ap.add_argument("--limit", type=int, help="only the first N samples (testing)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--exclude", nargs="*", default=list(DEFAULT_EXCLUDE), help="folders NOT to use as ground truth")
    ap.add_argument("--bpm-report", action="store_true", help="only print BPM ranges per folder")
    ap.add_argument("--write-bpm-priors", help="write suggested per-folder BPM ranges to this JSON file")
    ap.add_argument("--bpm-priors", help="BPM priors JSON for the evidence classifier (from --write-bpm-priors)")
    args = ap.parse_args(argv)

    from config import config
    root = Path(args.root or config.BASE_DOWNLOAD_DIR)
    if not (root / args.folder).is_dir():
        print(f"Not found: {root / args.folder}")
        return 2

    samples = collect_samples(root, folder=args.folder, exclude=args.exclude, per_folder=args.per_folder,
                              limit=args.limit, seed=args.seed)
    print(f"{len(samples)} labelled tracks from {root / args.folder} (read-only)\n")
    if not samples:
        return 0

    bpm = bpm_report(samples)
    if args.write_bpm_priors:
        Path(args.write_bpm_priors).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write_bpm_priors).write_text(json.dumps(suggest_bpm_priors(bpm), indent=2), encoding="utf-8")
        print(f"BPM priors written: {args.write_bpm_priors}\n")
    if args.bpm_report:
        print(render_bpm(bpm))
        return 0

    predict = CLASSIFIERS[args.classifier](args)
    preds = run_classifier(samples, predict,
                           progress=lambda i, n: print(f"\r  classifying {i}/{n}", end="", flush=True) if i % 25 == 0 or i == n else None)
    print()
    getattr(predict, "flush", lambda: None)()
    metrics = evaluate(samples, preds)
    print(render_summary(args.classifier, metrics), "\n")
    print(render_bpm(bpm))
    jp, cp = write_outputs(args.classifier, metrics, bpm)
    print(f"\nReport: {jp}\nDisagreements (review for misfiled tracks): {cp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
