"""
audio_genre_model.py
====================
Learn what THIS library's genres sound like, from tracks whose genre is already confirmed, and use
that to place the tracks no metadata source can place.

Training labels (never a guess):
  "ok"    the folder agrees with independent evidence (curated table / Last.fm / ...): label = folder
  "hand"  a file YOU moved by hand (its genre tag names another crate): label = the folder you chose

Honesty checks built in (a genre model that "works" on its own training data proves nothing):
  * out-of-fold predictions (K-fold) for every training track — never scored on itself;
  * a second test: train on the "ok" tracks only, then score the "hand" tracks. Those come from the
    same "unsorted" pool as the tracks the model will be asked to place, and their labels are a
    human's, so that number is the realistic one;
  * a per-crate confidence threshold chosen so that, on the out-of-fold data, predictions above it
    are right >= `target_precision` of the time — below it the model stays silent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "reports" / "audio_genre_model.joblib"
CATCH_ALL = {"Electronic", "NeedsReview"}
MIN_PER_CLASS = 12               # a crate with fewer confirmed tracks is not learnable
MIN_CONFIDENCE = 0.55            # never act below this, whatever the per-crate threshold says
MIN_THRESHOLD_SUPPORT = 6        # predictions needed above a threshold to trust its measured precision


@dataclass
class TrainingSet:
    X: np.ndarray
    y: np.ndarray
    kinds: List[str]                           # "ok" | "hand"
    paths: List[str]


@dataclass
class AudioModel:
    estimator: object
    classes: List[str]
    thresholds: Dict[str, float]               # crate -> confidence needed to act on a prediction of it
    feature_names: List[str]
    report: dict = field(default_factory=dict)
    # path -> (crate, confidence) from a model that never saw that track (K-fold): what to use
    # for the training tracks themselves, so they are never judged by a model that memorised them
    oof: Dict[str, Tuple[str, float]] = field(default_factory=dict)
    # crate -> confidence a track must clear before it may be MOVED on sound alone (see choose_move_thresholds)
    move_thresholds: Dict[str, float] = field(default_factory=dict)


# ── training data ──────────────────────────────────────────────────────────────────────────────
def training_rows(rows: Sequence[dict]) -> List[Tuple[dict, str]]:
    """[(row, kind)] for every row that can serve as a confirmed example."""
    out: List[Tuple[dict, str]] = []
    for r in rows:
        if r.get("folder") in CATCH_ALL:
            continue
        hand = bool(r.get("hand_placed")) or (bool(r.get("tag_folder")) and r["tag_folder"] != r["folder"])
        if hand:
            out.append((r, "hand"))
        elif r.get("action") == "ok":
            out.append((r, "ok"))
    return out


def build_training_set(rows: Sequence[dict], get_features: Callable[[str], Optional[List[float]]]) -> TrainingSet:
    X, y, kinds, paths = [], [], [], []
    for r, kind in training_rows(rows):
        vec = get_features(r["path"])
        if vec is None:
            continue
        X.append(vec)
        y.append(r["folder"])
        kinds.append(kind)
        paths.append(r["path"])
    return TrainingSet(np.asarray(X, dtype=float).reshape(len(X), -1) if X else np.zeros((0, 0)),
                       np.asarray(y, dtype=object), kinds, paths)


def _drop_rare(ts: TrainingSet, min_per_class: int) -> TrainingSet:
    counts = {c: int((ts.y == c).sum()) for c in set(ts.y.tolist())}
    keep = np.asarray([counts[c] >= min_per_class for c in ts.y], dtype=bool)
    return TrainingSet(ts.X[keep], ts.y[keep], [k for k, m in zip(ts.kinds, keep) if m],
                       [p for p, m in zip(ts.paths, keep) if m])


# ── models ─────────────────────────────────────────────────────────────────────────────────────
def candidate_estimators(seed: int = 0) -> Dict[str, object]:
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "logistic": make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=4000, class_weight="balanced")),
        "forest": RandomForestClassifier(n_estimators=400, min_samples_leaf=2, class_weight="balanced_subsample",
                                         n_jobs=-1, random_state=seed),
        "boosting": HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=200,
                                                   l2_regularization=1.0, random_state=seed),
    }


def out_of_fold_proba(estimator, X: np.ndarray, y: np.ndarray, *, folds: int = 5, seed: int = 0) -> Tuple[np.ndarray, List[str]]:
    """Every row scored by a model that never saw it. Returns (proba[n, classes], classes)."""
    from sklearn.base import clone
    from sklearn.model_selection import StratifiedKFold

    classes = sorted(set(y.tolist()))
    idx = {c: i for i, c in enumerate(classes)}
    k = max(2, min(folds, min(int((y == c).sum()) for c in classes)))
    proba = np.zeros((len(y), len(classes)))
    for tr, te in StratifiedKFold(n_splits=k, shuffle=True, random_state=seed).split(X, y):
        m = clone(estimator).fit(X[tr], y[tr])
        p = m.predict_proba(X[te])
        for j, c in enumerate(m.classes_):
            proba[te, idx[c]] = p[:, j]
    return proba, classes


def choose_thresholds(proba: np.ndarray, y: np.ndarray, classes: Sequence[str], target_precision: float = 0.9) -> Dict[str, float]:
    """Per crate: the lowest confidence at which out-of-fold predictions of that crate are right
    >= target_precision of the time (needs MIN_THRESHOLD_SUPPORT predictions); 1.01 = never act."""
    pred = np.asarray(classes)[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    out: Dict[str, float] = {}
    for c in classes:
        sel = pred == c
        best = 1.01
        for t in sorted(set(np.round(conf[sel], 3).tolist())):
            above = sel & (conf >= t)
            n = int(above.sum())
            if n >= MIN_THRESHOLD_SUPPORT and float((y[above] == c).mean()) >= target_precision:
                best = max(t, MIN_CONFIDENCE)
                break
        out[c] = best
    return out


def choose_move_thresholds(proba: np.ndarray, y: np.ndarray, classes: Sequence[str], margin: float = 0.02) -> Dict[str, float]:
    """
    Per predicted crate: the highest confidence at which the model was EVER wrong on a confirmed
    track (out-of-fold), plus a margin; 1.01 = never move on sound alone.

    choose_thresholds() measures precision among predictions OF a crate — but on the training
    population, where e.g. Bollywood is a third of all tracks, so "predict Bollywood" is usually right.
    A MOVE applies to a track that sits in a DIFFERENT folder, where that base rate does not hold
    (measured on the real library: Brazilian, Latin and R&B tracks "sounding like Bollywood" at
    55-67%). On confirmed data every disagreement with the folder is a model mistake, so the bar
    for acting on one is: more confident than any mistake the model has made.
    """
    pred = np.asarray(classes)[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    out: Dict[str, float] = {}
    for c in classes:
        wrong = (pred == c) & (y != c)
        bar = float(conf[wrong].max()) + margin if wrong.any() else MIN_CONFIDENCE
        out[c] = 1.01 if bar > 1.0 else max(bar, MIN_CONFIDENCE)
    return out


def per_class_report(proba: np.ndarray, y: np.ndarray, classes: Sequence[str], thresholds: Dict[str, float]) -> Dict[str, dict]:
    pred = np.asarray(classes)[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    rep = {}
    for c in classes:
        actual, said = y == c, pred == c
        acted = said & (conf >= thresholds.get(c, 1.01))
        rep[c] = {
            "support": int(actual.sum()),
            "recall": round(float((said & actual).sum() / max(1, actual.sum())), 3),
            "precision": round(float((said & actual).sum() / max(1, said.sum())), 3),
            "threshold": thresholds.get(c, 1.01),
            "acted": int(acted.sum()),
            "acted_precision": round(float((acted & actual).sum() / max(1, acted.sum())), 3) if acted.sum() else None,
        }
    return rep


def train(ts: TrainingSet, feature_names: Sequence[str], *, target_precision: float = 0.9, seed: int = 0,
          folds: int = 5) -> Tuple[AudioModel, np.ndarray]:
    """Pick the best candidate by out-of-fold log-loss, fit it on everything, choose thresholds.
    Returns (model, out_of_fold_proba aligned with `ts` after rare crates were dropped)."""
    from sklearn.base import clone
    from sklearn.metrics import accuracy_score, log_loss

    ts = _drop_rare(ts, MIN_PER_CLASS)
    if len(ts.y) < 40 or len(set(ts.y.tolist())) < 2:
        raise ValueError("not enough confirmed tracks to learn from")

    scored = {}
    for name, est in candidate_estimators(seed).items():
        proba, classes = out_of_fold_proba(est, ts.X, ts.y, folds=folds, seed=seed)
        pred = np.asarray(classes)[proba.argmax(axis=1)]
        scored[name] = {"log_loss": float(log_loss(ts.y, np.clip(proba, 1e-6, 1), labels=classes)),
                        "accuracy": float(accuracy_score(ts.y, pred)), "proba": proba}
    best = min(scored, key=lambda n: scored[n]["log_loss"])
    proba, classes = scored[best]["proba"], sorted(set(ts.y.tolist()))
    thresholds = choose_thresholds(proba, ts.y, classes, target_precision)
    move_thresholds = choose_move_thresholds(proba, ts.y, classes)
    est = clone(candidate_estimators(seed)[best]).fit(ts.X, ts.y)

    # the realistic test: train on evidence-confirmed tracks only, score the tracks a human placed
    hand_report = None
    kinds = np.asarray(ts.kinds)
    if (kinds == "hand").sum() >= 10 and (kinds == "ok").sum() >= 40:
        m = clone(candidate_estimators(seed)[best]).fit(ts.X[kinds == "ok"], ts.y[kinds == "ok"])
        Xh, yh = ts.X[kinds == "hand"], ts.y[kinds == "hand"]
        ph = m.predict_proba(Xh)
        pred_h = np.asarray(m.classes_)[ph.argmax(axis=1)]
        conf_h = ph.max(axis=1)
        acted = np.asarray([conf_h[i] >= thresholds.get(pred_h[i], 1.01) for i in range(len(yh))])
        hand_report = {
            "n": int(len(yh)), "accuracy_all": round(float((pred_h == yh).mean()), 3),
            "acted": int(acted.sum()),
            "acted_precision": round(float((pred_h[acted] == yh[acted]).mean()), 3) if acted.any() else None,
        }

    report = {
        "chosen": best, "n_train": int(len(ts.y)), "classes": classes,
        "candidates": {n: {"log_loss": round(v["log_loss"], 3), "accuracy": round(v["accuracy"], 3)} for n, v in scored.items()},
        "per_class": per_class_report(proba, ts.y, classes, thresholds),
        "hand_placed_test": hand_report, "target_precision": target_precision,
        "move_thresholds": {c: (None if v > 1.0 else round(v, 2)) for c, v in move_thresholds.items()},
    }
    oof = {path: (classes[int(i)], float(proba[k, int(i)]))
           for k, (path, i) in enumerate(zip(ts.paths, proba.argmax(axis=1)))}
    return AudioModel(est, list(est.classes_), thresholds, list(feature_names), report, oof, move_thresholds), proba


# ── prediction ─────────────────────────────────────────────────────────────────────────────────
def predict(model: AudioModel, X: np.ndarray) -> List[Tuple[str, float]]:
    """[(crate, confidence)] for each row of X."""
    if len(X) == 0:
        return []
    p = model.estimator.predict_proba(X)
    return [(model.classes[int(i)], float(p[k, int(i)])) for k, i in enumerate(p.argmax(axis=1))]


def acts(model: AudioModel, crate: str, confidence: float) -> bool:
    """True when the model is confident ENOUGH about `crate` to act on it."""
    return confidence >= max(MIN_CONFIDENCE, model.thresholds.get(crate, 1.01)) and model.thresholds.get(crate, 1.01) <= 1.0


def acts_to_move(model: AudioModel, crate: str, confidence: float) -> bool:
    """True when the model may MOVE a track to `crate` on sound alone: it must clear both the precision
    bar AND the stricter 'more confident than any mistake it has made' bar."""
    bar = getattr(model, "move_thresholds", {}).get(crate, 1.01)
    return bar <= 1.0 and confidence >= bar and acts(model, crate, confidence)


def save(model: AudioModel, path: Optional[Path] = None) -> Path:
    import joblib
    p = Path(path) if path else DEFAULT_MODEL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, p)
    return p


def load(path: Optional[Path] = None) -> Optional[AudioModel]:
    """Load a model saved by save().

    SECURITY: joblib is pickle underneath, so loading executes code from the file. That is safe
    here ONLY because the file is written by this application into its own local reports/
    directory (train() -> save()) and is never downloaded, uploaded or received from anywhere.
    Never point this at a model file from another machine or an untrusted source.
    """
    import joblib
    p = Path(path) if path else DEFAULT_MODEL_PATH
    try:
        return joblib.load(p) if p.is_file() else None
    except Exception:
        return None
