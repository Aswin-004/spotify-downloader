"""
backend/services/recommendation_service.py

Deterministic DJ track-recommendation and set-sequencing engine.

PHASE 0 — recovered from git history, adapted, and hardened.
Scope: PHASE 0 ONLY. No VirtualDJ integration, no live-copilot UI, no ML,
no LLM in the recommendation loop, no architecture redesign. See the
Phase 0 engineering report for full context.

PROVENANCE
----------
This module recovers and adapts the recommendation-scoring core that
previously lived in `backend/library_analytics.py`, deleted in commit
9d50e72 as part of an unrelated "pre-deploy cleanup" that also removed
graph-visualization/dashboard code this module does NOT revive. The
original source was retrieved read-only via:

    git show 9d50e72^:backend/library_analytics.py

and never restored into the working tree.

The following are ported VERBATIM (identical constants and logic to the
recovered original) because they were already working, tested-by-usage
code, and changing them was out of scope for Phase 0:
  - CAMELOT_KEYS, _camelot_neighbors, _camelot_score, _proximity_score,
    _compute_similarity   (weighted similarity: camelot 0.40 / bpm 0.30 /
    energy 0.20 / spectral 0.10)
  - find_similar_tracks
  - _FLOW_MODES, _BPM_JUMP_HARD/_SOFT, _ARTIST_WINDOW,
    _WEAK_TRANS_SCORE, _WEAK_BPM_JUMP, RMS_MID_MAX
  - _flow_bonus, _pick_next, generate_playlist_sequence
    (greedy next-track sequencing with hard/soft BPM-jump limits, an
    artist-repeat exclusion window, and warmup/peak/cooldown flow bonuses)

This module deliberately does NOT revive: run_analytics, _print_summary,
run_similarity_report, run_playlist_report, _print_playlist_summary,
_print_similarity_summary (stdout report-printing functions), or
_load_docs_for_graphs and anything graph/dashboard-related (pyvis /
matplotlib network-graph generation). None of that is recommendation
logic and reviving it was explicitly out of scope for Phase 0.

ADAPTATIONS (new in this module, not present in the original)
---------------------------------------------------------------
  1. Key-detection confidence awareness (`_confidence_factor`,
     `_compute_similarity_confidence_aware`). `audio_features.confidence`
     comes from bpm_key_service.py's Krumhansl-Schmuckler correlation
     and is frequently low across this library (library-wide audit:
     mean 0.614, 26.5% of tracks below 0.50). The original algorithm
     trusted the detected Camelot key unconditionally. This module
     down-weights the camelot term of the score by the lower of the two
     tracks' confidences whenever it falls below _KEY_CONFIDENCE_THRESHOLD,
     using the smallest deterministic rule that satisfies the
     requirement (see `_confidence_factor`'s docstring for the exact
     rule). This ONLY affects the new `recommend_next()` path —
     `find_similar_tracks()` and `generate_playlist_sequence()` keep
     calling the original, unadjusted `_compute_similarity()` so their
     behavior stays byte-for-byte identical to the recovered algorithm.
  2. Pool-relative energy normalization (`_normalize_energy_pool`).
     `rms_energy` is a raw, uncalibrated librosa RMS amplitude reading
     — not a fixed 0-10 "DJ energy" scale. Where a normalized 0-1 value
     is useful for display, it is computed via min-max normalization
     against the CANDIDATE POOL's own observed range, never against an
     invented absolute scale. This is presentation-only: it does not
     feed the similarity/ranking math, which continues to use raw
     rms_energy deltas against a fixed tolerance (as the original did).
  3. Incomplete-candidate exclusion (`_is_scoreable`,
     `get_candidate_pool`). A candidate missing `bpm` or `camelot` is
     excluded from the pool entirely rather than silently scored via
     `_proximity_score`/`_camelot_score` returning 0.0 for missing
     values (i.e. "maximally dissimilar") — the original behavior would
     have systematically pushed sparse-metadata tracks to the bottom of
     every ranking rather than leaving them out with a clear count.
  4. `missing: {"$ne": True}` filtering against library_index's
     soft-delete flag (see repair_index.py, which established this same
     filter for its own on-disk-count check) — applied to every default
     Mongo query in this module so files confirmed gone from disk are
     never recommended or sequenced.
  5. New primary entry point `recommend_next()`, with an explicit,
     documented result shape. The original module had no single
     "give me the next N tracks" API — equivalent logic was embedded
     inside `generate_playlist_sequence()`'s single-best-pick loop
     (`_pick_next`, which this module also reuses unchanged). This
     module generalizes that single-best pattern into a ranked top-N
     while reusing the same hard/soft BPM-jump and artist-window rules.

No ML, no LLM, no VirtualDJ integration. Pure deterministic scoring over
metadata already stored in `library_index`. This module has no
third-party dependencies beyond the Python standard library and
`database.get_library_index_collection()`.
"""

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — carried over unchanged from the recovered library_analytics.py
# ---------------------------------------------------------------------------

CAMELOT_KEYS = [f"{n}{s}" for n in range(1, 13) for s in ("A", "B")]

_SIM_WEIGHTS = {"camelot": 0.40, "bpm": 0.30, "energy": 0.20, "spectral": 0.10}
_BPM_TOLERANCE = 20.0       # delta at which BPM score -> 0
_ENERGY_TOLERANCE = 0.15    # RMS delta at which energy score -> 0
_SPECTRAL_TOLERANCE = 1500.0  # Hz delta at which spectral score -> 0

_FLOW_MODES = ("warmup", "peak", "cooldown", "mixed")
_BPM_JUMP_HARD = 15.0    # reject candidate if bpm delta exceeds this
_BPM_JUMP_SOFT = 25.0    # relaxed fallback threshold
_ARTIST_WINDOW = 4       # no artist repeat within this many steps
_WEAK_TRANS_SCORE = 0.40  # transition score below this is flagged weak
_WEAK_BPM_JUMP = 12.0     # bpm jump above this is flagged weak

RMS_MID_MAX = 0.18  # absolute rms_energy threshold used by the "peak" flow bonus

# --- New in this module (Phase 0 additions; not present in the original) ---

# Below this key-detection confidence, camelot compatibility is down-weighted
# rather than trusted outright. 0.5 matches the midpoint already used
# library-wide (see backend/reports/library_audio_analytics.json) as the
# threshold between "reasonably confident" and "weak" detections.
_KEY_CONFIDENCE_THRESHOLD = 0.5

# A candidate must have both of these to be scoreable at all.
_REQUIRED_FIELDS = ("bpm", "camelot")


# ---------------------------------------------------------------------------
# Camelot wheel — verbatim from the recovered original
# ---------------------------------------------------------------------------

def _camelot_neighbors(key: str) -> list:
    """Return harmonically compatible Camelot keys (same letter +/-1, opposite letter same number)."""
    if not key or len(key) < 2:
        return []
    num_str = key[:-1]
    suffix = key[-1]
    try:
        num = int(num_str)
    except ValueError:
        return []
    opposite = "B" if suffix == "A" else "A"
    prev_num = 12 if num == 1 else num - 1
    next_num = 1 if num == 12 else num + 1
    return [
        f"{prev_num}{suffix}",  # -1 same side
        f"{next_num}{suffix}",  # +1 same side
        f"{num}{opposite}",     # parallel (same number, other side)
    ]


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
    """Original, unadjusted similarity — used by find_similar_tracks() and
    generate_playlist_sequence() so their behavior stays identical to the
    recovered algorithm."""
    cam = _camelot_score(af_a.get("camelot", ""), af_b.get("camelot", ""))
    bpm = _proximity_score(af_a.get("bpm"), af_b.get("bpm"), _BPM_TOLERANCE)
    eng = _proximity_score(af_a.get("rms_energy"), af_b.get("rms_energy"), _ENERGY_TOLERANCE)
    spc = _proximity_score(
        af_a.get("spectral_centroid_mean"),
        af_b.get("spectral_centroid_mean"),
        _SPECTRAL_TOLERANCE,
    )
    total = (
        _SIM_WEIGHTS["camelot"] * cam +
        _SIM_WEIGHTS["bpm"] * bpm +
        _SIM_WEIGHTS["energy"] * eng +
        _SIM_WEIGHTS["spectral"] * spc
    )
    return {
        "score": round(total, 4),
        "camelot_score": round(cam, 4),
        "bpm_score": round(bpm, 4),
        "energy_score": round(eng, 4),
        "spectral_score": round(spc, 4),
    }


# ---------------------------------------------------------------------------
# NEW in this module: key-confidence-aware similarity (adaptation #1)
# ---------------------------------------------------------------------------

def _confidence_factor(confidence) -> float:
    """Map a key-detection confidence value to a trust multiplier in [0, 1]
    for the camelot-compatibility term.

    Rule (smallest deterministic rule that satisfies the requirement):
      - confidence missing / non-numeric / NaN -> 0.5
        (neutral: we cannot verify trust either way, so neither fully
        honor nor fully discard the camelot match)
      - confidence >= _KEY_CONFIDENCE_THRESHOLD -> 1.0
        (full trust — identical to the original algorithm's behavior)
      - confidence <  _KEY_CONFIDENCE_THRESHOLD -> confidence / threshold
        (linear ramp from 0.0 at confidence=0 up to 1.0 at the threshold)
    """
    if confidence is None or not isinstance(confidence, (int, float)):
        return 0.5
    if isinstance(confidence, float) and math.isnan(confidence):
        return 0.5
    conf = float(confidence)
    if conf >= _KEY_CONFIDENCE_THRESHOLD:
        return 1.0
    if conf <= 0:
        return 0.0
    return conf / _KEY_CONFIDENCE_THRESHOLD


def _compute_similarity_confidence_aware(af_a: dict, af_b: dict) -> dict:
    """Confidence-aware variant of `_compute_similarity`, used only by
    `recommend_next()`. Down-weights the camelot term by the lower of the
    two tracks' key-detection confidences — a harmonic match is only as
    trustworthy as its less-confident half. bpm/energy/spectral terms are
    untouched.

    Returns everything `_compute_similarity` returns, plus:
      camelot_score_base : the original, unadjusted camelot score (0/0.75/1.0)
      camelot_score       : overwritten with the confidence-adjusted value
      confidence_factor    : the multiplier that was applied
      score                : recomputed total using the adjusted camelot term
    """
    base = _compute_similarity(af_a, af_b)
    confs = [
        c for c in (af_a.get("confidence"), af_b.get("confidence"))
        if isinstance(c, (int, float)) and not (isinstance(c, float) and math.isnan(c))
    ]
    factor = _confidence_factor(min(confs)) if confs else _confidence_factor(None)
    cam_adjusted = round(base["camelot_score"] * factor, 4)
    total_adjusted = (
        _SIM_WEIGHTS["camelot"] * cam_adjusted +
        _SIM_WEIGHTS["bpm"] * base["bpm_score"] +
        _SIM_WEIGHTS["energy"] * base["energy_score"] +
        _SIM_WEIGHTS["spectral"] * base["spectral_score"]
    )
    return {
        **base,
        "camelot_score_base": base["camelot_score"],
        "camelot_score": cam_adjusted,
        "confidence_factor": round(factor, 4),
        "score": round(total_adjusted, 4),
    }


# ---------------------------------------------------------------------------
# NEW in this module: pool-relative energy normalization (adaptation #2)
# ---------------------------------------------------------------------------

def _normalize_energy_pool(docs: list) -> dict:
    """Min-max normalize rms_energy to [0, 1] across the given pool, keyed by
    identity_key. Display-only — does not feed the similarity/ranking math.
    Returns {} if no track in the pool has a usable rms_energy value."""
    vals = []
    for d in docs:
        v = (d.get("audio_features") or {}).get("rms_energy")
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            vals.append(float(v))
    if not vals:
        return {}
    lo, hi = min(vals), max(vals)
    spread = hi - lo
    out = {}
    for d in docs:
        ik = d.get("identity_key")
        v = (d.get("audio_features") or {}).get("rms_energy")
        if ik is None or not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
            continue
        out[ik] = round((float(v) - lo) / spread, 4) if spread > 0 else 0.5
    return out


# ---------------------------------------------------------------------------
# NEW in this module: candidate-pool retrieval (adaptations #3 and #4)
# ---------------------------------------------------------------------------

def _is_scoreable(doc: dict) -> bool:
    af = doc.get("audio_features") or {}
    return all(af.get(f) not in (None, "") for f in _REQUIRED_FIELDS)


def get_candidate_pool(exclude_identity_keys=None, docs=None) -> list:
    """Return library_index docs eligible for recommendation:
      - `missing` is not True (repair_index.py's soft-delete flag)
      - has both `bpm` and `camelot` in audio_features (see _is_scoreable)
      - identity_key not in `exclude_identity_keys`

    Pass `docs` to score against an already-fetched/injected list instead of
    querying MongoDB (used by tests and by recommend_next(), which fetches
    once and reuses the same list for the current-track lookup).
    """
    exclude = set(exclude_identity_keys or ())
    if docs is None:
        from database import get_library_index_collection
        col = get_library_index_collection()
        docs = list(col.find(
            {"audio_features": {"$exists": True}, "missing": {"$ne": True}},
            {"identity_key": 1, "title": 1, "artist": 1, "genre_folder": 1,
             "audio_features": 1, "missing": 1, "_id": 0},
        ))
    pool = []
    for doc in docs:
        ik = doc.get("identity_key")
        if not ik or ik in exclude:
            continue
        if doc.get("missing") is True:
            continue
        if not _is_scoreable(doc):
            continue
        pool.append(doc)
    return pool


# ---------------------------------------------------------------------------
# find_similar_tracks — verbatim from the recovered original, plus the
# `missing` filter (adaptation #4) on its own default query
# ---------------------------------------------------------------------------

def find_similar_tracks(identity_key: str, top_k: int = 10, _docs=None) -> list:
    """Rule-based similarity using bpm, camelot, rms_energy, spectral_centroid."""
    if _docs is None:
        from database import get_library_index_collection
        col = get_library_index_collection()
        _docs = list(col.find(
            {"audio_features": {"$exists": True}, "missing": {"$ne": True}},
            {"identity_key": 1, "title": 1, "artist": 1,
             "genre_folder": 1, "audio_features": 1, "_id": 0},
        ))

    source = next((d for d in _docs if d.get("identity_key") == identity_key), None)
    if source is None:
        return []

    af_src = source.get("audio_features", {})
    seen = {identity_key}
    results = []

    for doc in _docs:
        ik = doc.get("identity_key")
        if ik is None or ik in seen:
            continue
        seen.add(ik)

        af = doc.get("audio_features", {})
        sim = _compute_similarity(af_src, af)

        bpm_s = af_src.get("bpm")
        bpm_d = af.get("bpm")
        rms_s = af_src.get("rms_energy")
        rms_d = af.get("rms_energy")

        results.append({
            "identity_key": ik,
            "title": doc.get("title", ""),
            "artist": doc.get("artist", ""),
            "genre": doc.get("genre_folder", ""),
            "camelot": af.get("camelot", ""),
            "bpm": af.get("bpm"),
            "rms_energy": af.get("rms_energy"),
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


# ---------------------------------------------------------------------------
# Playlist sequencing — verbatim from the recovered original, plus the
# `missing` filter (adaptation #4) on its own default query
# ---------------------------------------------------------------------------

def _flow_bonus(cur_bpm, cand_bpm, _cur_rms, cand_rms, flow: str) -> float:
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


def _pick_next(current: dict, pool: list, used_keys: set, recent_artists: list,
                flow: str, relax: bool = False):
    """Return (adjusted_score, doc, sim) for best next candidate, or None."""
    cur_af = current.get("audio_features", {})
    cur_bpm = cur_af.get("bpm")
    bpm_limit = _BPM_JUMP_SOFT if relax else _BPM_JUMP_HARD

    best = None
    for doc in pool:
        ik = doc.get("identity_key")
        if ik in used_keys:
            continue
        artist = doc.get("artist", "")
        if not relax and artist and artist in recent_artists[-_ARTIST_WINDOW:]:
            continue

        af = doc.get("audio_features", {})
        cand_bpm = af.get("bpm")
        cand_rms = af.get("rms_energy")

        if cur_bpm is not None and cand_bpm is not None:
            if abs(float(cur_bpm) - float(cand_bpm)) > bpm_limit:
                continue

        sim = _compute_similarity(cur_af, af)
        bonus = _flow_bonus(cur_bpm, cand_bpm, None, cand_rms, flow)
        adj = sim["score"] + bonus

        if best is None or adj > best[0]:
            best = (adj, doc, sim)

    return best


def generate_playlist_sequence(seed_identity_key: str, length: int = 20,
                                flow: str = "mixed", _docs=None) -> dict:
    """Build a sequenced playlist from a seed track.

    flow: warmup | peak | cooldown | mixed
    Returns full sequence, transitions, progressions, quality metrics.
    """
    if flow not in _FLOW_MODES:
        raise ValueError(f"flow must be one of {_FLOW_MODES}")

    if _docs is None:
        from database import get_library_index_collection
        col = get_library_index_collection()
        _docs = list(col.find(
            {"audio_features": {"$exists": True}, "missing": {"$ne": True}},
            {"identity_key": 1, "title": 1, "artist": 1,
             "genre_folder": 1, "audio_features": 1, "_id": 0},
        ))

    seed = next((d for d in _docs if d.get("identity_key") == seed_identity_key), None)
    if seed is None:
        return {"error": f"seed '{seed_identity_key}' not found"}

    sequence = [seed]
    used_keys = {seed_identity_key}
    recent_artists = [seed.get("artist", "")]
    transitions = []
    pool = [d for d in _docs if d.get("identity_key") != seed_identity_key]

    for step in range(length - 1):
        current = sequence[-1]
        result = _pick_next(current, pool, used_keys, recent_artists, flow, relax=False)
        if result is None:
            result = _pick_next(current, pool, used_keys, recent_artists, flow, relax=True)
        if result is None:
            break

        _adj, next_doc, sim = result
        ik = next_doc.get("identity_key")
        af_cur = current.get("audio_features", {})
        af_nxt = next_doc.get("audio_features", {})
        bpm_c = af_cur.get("bpm")
        bpm_n = af_nxt.get("bpm")
        rms_c = af_cur.get("rms_energy")
        rms_n = af_nxt.get("rms_energy")

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
            "step": step + 1,
            "from_key": current.get("identity_key"),
            "to_key": ik,
            "transition_score": round(sim["score"], 4),
            "camelot_score": sim["camelot_score"],
            "bpm_score": sim["bpm_score"],
            "energy_score": sim["energy_score"],
            "camelot_from": af_cur.get("camelot", ""),
            "camelot_to": af_nxt.get("camelot", ""),
            "bpm_from": bpm_c,
            "bpm_to": bpm_n,
            "bpm_jump": bpm_jump,
            "energy_delta": energy_delta,
            "harmonic_compatible": sim["camelot_score"] >= 0.75,
            "is_weak": is_weak,
        })

        sequence.append(next_doc)
        used_keys.add(ik)
        recent_artists.append(next_doc.get("artist", ""))

    seq_list = []
    for i, doc in enumerate(sequence):
        af = doc.get("audio_features", {})
        seq_list.append({
            "position": i + 1,
            "identity_key": doc.get("identity_key"),
            "title": doc.get("title", ""),
            "artist": doc.get("artist", ""),
            "genre": doc.get("genre_folder", ""),
            "camelot": af.get("camelot", ""),
            "bpm": af.get("bpm"),
            "rms_energy": af.get("rms_energy"),
            "spectral_centroid": af.get("spectral_centroid_mean"),
        })

    t_scores = [t["transition_score"] for t in transitions]
    weak_trans = [t for t in transitions if t["is_weak"]]
    bpm_jumps = [t["bpm_jump"] for t in transitions if t["bpm_jump"] is not None]
    harm_ok = sum(1 for t in transitions if t["harmonic_compatible"])

    avg_trans = round(sum(t_scores) / len(t_scores), 4) if t_scores else 0.0
    avg_bpm_jump = round(sum(bpm_jumps) / len(bpm_jumps), 2) if bpm_jumps else 0.0

    bpm_prog = [sequence[0].get("audio_features", {}).get("bpm")] + [
        t["bpm_to"] for t in transitions
    ]
    cam_prog = [doc.get("audio_features", {}).get("camelot", "") for doc in sequence]
    rms_prog = [
        round(doc.get("audio_features", {}).get("rms_energy") or 0.0, 4)
        for doc in sequence
    ]

    return {
        "seed_identity_key": seed_identity_key,
        "flow": flow,
        "length_requested": length,
        "length_achieved": len(sequence),
        "sequence": seq_list,
        "transitions": transitions,
        "bpm_progression": bpm_prog,
        "camelot_progression": cam_prog,
        "energy_progression": rms_prog,
        "quality": {
            "avg_transition_score": avg_trans,
            "harmonic_compatible_pct": round(harm_ok / len(transitions) * 100, 1) if transitions else 0.0,
            "weak_transitions": len(weak_trans),
            "weak_transition_pct": round(len(weak_trans) / len(transitions) * 100, 1) if transitions else 0.0,
            "avg_bpm_jump": avg_bpm_jump,
            "max_bpm_jump": round(max(bpm_jumps), 1) if bpm_jumps else 0.0,
        },
        "weak_transitions": weak_trans,
    }


# ---------------------------------------------------------------------------
# NEW in this module (adaptation #5): recommend_next()
# ---------------------------------------------------------------------------

def _rank_candidates(current: dict, pool: list, flow: str, recent_artists_window: list,
                      relax: bool) -> list:
    """Score every candidate in `pool` against `current` and return them
    sorted best-first as (adjusted_score, doc, sim, bonus) tuples. Applies
    the same hard/soft BPM-jump limit and artist-repeat window as
    _pick_next(), generalized from "pick the single best" to "rank them all".
    """
    cur_af = current.get("audio_features", {})
    cur_bpm = cur_af.get("bpm")
    bpm_limit = _BPM_JUMP_SOFT if relax else _BPM_JUMP_HARD

    scored = []
    for doc in pool:
        artist = doc.get("artist", "")
        if not relax and artist and artist in recent_artists_window:
            continue

        af = doc.get("audio_features", {})
        cand_bpm = af.get("bpm")
        cand_rms = af.get("rms_energy")

        if cur_bpm is not None and cand_bpm is not None:
            if abs(float(cur_bpm) - float(cand_bpm)) > bpm_limit:
                continue

        sim = _compute_similarity_confidence_aware(cur_af, af)
        bonus = _flow_bonus(cur_bpm, cand_bpm, None, cand_rms, flow)
        adjusted = round(sim["score"] + bonus, 4)
        scored.append((adjusted, doc, sim, bonus))

    scored.sort(key=lambda x: -x[0])
    return scored


def recommend_next(current_identity_key: str, top_n: int = 5, flow: str = "mixed",
                    recently_played=None, recent_artists=None, docs=None) -> dict:
    """Return the top `top_n` next-track recommendations for the track
    currently playing (`current_identity_key`).

    Args:
      current_identity_key: identity_key of the track currently playing.
      top_n: how many ranked recommendations to return.
      flow: "warmup" | "peak" | "cooldown" | "mixed" — see _flow_bonus().
      recently_played: identity_keys to hard-exclude (already played this
        set), in addition to the current track itself.
      recent_artists: artist names played recently, most-recent-last; only
        the last _ARTIST_WINDOW entries are used to block repeats. Defaults
        to [current track's artist] if not supplied.
      docs: inject a pre-fetched list of library_index docs (each needs at
        least identity_key, title, artist, genre_folder, audio_features,
        missing) instead of querying MongoDB — used by tests, and reused
        internally so a single call only queries the database once.

    Returns a dict:
      {
        "current_identity_key": str,
        "current_title": str,
        "current_artist": str,
        "flow": str,
        "top_n_requested": int,
        "recommendations": [
          {
            "identity_key", "title", "artist", "genre",
            "camelot", "bpm", "rms_energy", "energy_normalized",
            "spectral_centroid",
            "score",                 # confidence-adjusted similarity (0-1ish)
            "flow_bonus", "adjusted_score",  # score + flow_bonus
            "camelot_score",         # confidence-adjusted camelot term
            "camelot_score_base",    # unadjusted camelot term (0 / 0.75 / 1.0)
            "confidence_factor",     # trust multiplier applied to camelot_score
            "bpm_score", "energy_score", "spectral_score",
            "bpm_jump",
            "harmonic_compatible",   # camelot_score_base >= 0.75
            "key_confidence_low",    # confidence_factor < 1.0
            "is_weak_transition",
          }, ...
        ],
        "candidates_considered": int,
        "candidates_excluded_missing_flag": int,
        "candidates_excluded_incomplete_metadata": int,
        "candidates_excluded_recently_played": int,
        "generated_at": ISO 8601 UTC timestamp,
        "error": str | None,
      }

    Deterministic: identical (docs, current_identity_key, top_n, flow,
    recently_played, recent_artists) inputs always produce identical
    "recommendations" ordering and scores. Never raises for missing or
    malformed metadata on individual candidates — those are excluded and
    counted instead; only an unknown `flow` value raises ValueError.
    """
    if flow not in _FLOW_MODES:
        raise ValueError(f"flow must be one of {_FLOW_MODES}")

    if docs is None:
        from database import get_library_index_collection
        col = get_library_index_collection()
        docs = list(col.find(
            {"audio_features": {"$exists": True}},
            {"identity_key": 1, "title": 1, "artist": 1, "genre_folder": 1,
             "audio_features": 1, "missing": 1, "_id": 0},
        ))

    generated_at = datetime.now(timezone.utc).isoformat()
    base_result = {
        "current_identity_key": current_identity_key,
        "flow": flow,
        "top_n_requested": top_n,
        "recommendations": [],
        "candidates_considered": 0,
        "candidates_excluded_missing_flag": 0,
        "candidates_excluded_incomplete_metadata": 0,
        "candidates_excluded_recently_played": 0,
        "generated_at": generated_at,
    }

    current = next((d for d in docs if d.get("identity_key") == current_identity_key), None)
    if current is None:
        return {**base_result, "current_title": "", "current_artist": "",
                "error": f"identity_key '{current_identity_key}' not found in supplied library data."}

    if not _is_scoreable(current):
        return {**base_result,
                "current_title": current.get("title", ""),
                "current_artist": current.get("artist", ""),
                "error": "current track is missing bpm and/or camelot — cannot score recommendations."}

    recently_played_set = set(recently_played or ())
    exclude_keys = {current_identity_key} | recently_played_set

    missing_flagged = sum(1 for d in docs if d.get("missing") is True)
    incomplete_excluded = sum(
        1 for d in docs
        if d.get("identity_key") not in exclude_keys
        and d.get("missing") is not True
        and not _is_scoreable(d)
    )

    pool = get_candidate_pool(exclude_identity_keys=exclude_keys, docs=docs)

    if not pool:
        return {**base_result,
                "current_title": current.get("title", ""),
                "current_artist": current.get("artist", ""),
                "candidates_excluded_missing_flag": missing_flagged,
                "candidates_excluded_incomplete_metadata": incomplete_excluded,
                "candidates_excluded_recently_played": len(recently_played_set),
                "error": None}

    recent_artists_window = list(recent_artists) if recent_artists else [current.get("artist", "")]
    recent_artists_window = [a for a in recent_artists_window if a][-_ARTIST_WINDOW:]

    energy_norm = _normalize_energy_pool(pool + [current])

    ranked = _rank_candidates(current, pool, flow, recent_artists_window, relax=False)
    if len(ranked) < top_n:
        seen = {r[1].get("identity_key") for r in ranked}
        relaxed = _rank_candidates(current, pool, flow, recent_artists_window, relax=True)
        for r in relaxed:
            ik = r[1].get("identity_key")
            if ik not in seen:
                ranked.append(r)
                seen.add(ik)
        ranked.sort(key=lambda x: -x[0])

    cur_af = current.get("audio_features", {})
    cur_bpm = cur_af.get("bpm")

    recommendations = []
    for adjusted_score, cand, sim, bonus in ranked[:top_n]:
        af = cand.get("audio_features") or {}
        cand_bpm = af.get("bpm")
        bpm_jump = (
            round(abs(float(cur_bpm) - float(cand_bpm)), 1)
            if cur_bpm is not None and cand_bpm is not None else None
        )
        is_weak = (
            sim["score"] < _WEAK_TRANS_SCORE or
            (bpm_jump is not None and bpm_jump > _WEAK_BPM_JUMP) or
            sim["camelot_score_base"] == 0.0
        )
        cand_ik = cand.get("identity_key")
        recommendations.append({
            "identity_key": cand_ik,
            "title": cand.get("title", ""),
            "artist": cand.get("artist", ""),
            "genre": cand.get("genre_folder", ""),
            "camelot": af.get("camelot", ""),
            "bpm": cand_bpm,
            "rms_energy": af.get("rms_energy"),
            "energy_normalized": energy_norm.get(cand_ik),
            "spectral_centroid": af.get("spectral_centroid_mean"),
            "score": sim["score"],
            "flow_bonus": bonus,
            "adjusted_score": adjusted_score,
            "camelot_score": sim["camelot_score"],
            "camelot_score_base": sim["camelot_score_base"],
            "confidence_factor": sim["confidence_factor"],
            "bpm_score": sim["bpm_score"],
            "energy_score": sim["energy_score"],
            "spectral_score": sim["spectral_score"],
            "bpm_jump": bpm_jump,
            "harmonic_compatible": sim["camelot_score_base"] >= 0.75,
            "key_confidence_low": sim["confidence_factor"] < 1.0,
            "is_weak_transition": is_weak,
        })

    return {
        **base_result,
        "current_title": current.get("title", ""),
        "current_artist": current.get("artist", ""),
        "recommendations": recommendations,
        "candidates_considered": len(pool),
        "candidates_excluded_missing_flag": missing_flagged,
        "candidates_excluded_incomplete_metadata": incomplete_excluded,
        "candidates_excluded_recently_played": len(recently_played_set),
        "error": None,
    }
