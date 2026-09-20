"""
backend/services/training_service.py

Phase 2 — deterministic DJ Training Engine.

Transforms the existing analyzed library into a daily DJ practice session
of exactly 5 exercises (EASY_HARMONIC, BPM_TRANSITION, ENERGY_TRANSITION,
GENRE_CROSSOVER, CHALLENGE). This module does NOT implement a second
recommendation/scoring engine: every pairwise comparison is delegated to
services/recommendation_service.py's own confidence-aware similarity
function. training_service.py only adds exercise-selection rules,
difficulty scoring, and human-readable instructions on top of that.

No LLM, no ML, no external API, no VirtualDJ integration. No MongoDB
writes — this module only ever reads (or accepts injected `docs=`, exactly
like recommend_next()/find_similar_tracks()/generate_playlist_sequence()).
No physical file access at all.

DETERMINISM
-----------
The daily selection is seeded from `date_str` plus a fingerprint of the
candidate pool's identity_keys (see `_daily_seed`) — never from
`datetime.now()` or any other wall-clock value, and never from the
uncontrolled global `random` module. All randomness inside this module
goes through a single `random.Random(seed)` instance created once per
`generate_daily_session()` call and threaded through every builder in a
fixed order, so identical (date, library snapshot) inputs always produce
identical exercises. `generated_at` is computed separately purely for
display and never feeds the seed.

CANDIDATE SAFETY
-----------------
Candidates come from `recommendation_service.get_candidate_pool()` (the
same pool `recommend_next()` uses — excludes `missing: True` and requires
bpm+camelot), then narrowed further by `_is_training_safe()` for BPM
range/NaN/inf/malformed-Camelot guards the recommendation engine doesn't
itself apply. Phase 1E semantics are preserved unchanged: unknown/empty
artists are never treated as a real artist identity (`_is_known_artist`),
and DJ mixes keep whatever `classify_track_length()` already reports —
this module classifies, it does not exclude or re-score them.
"""
import hashlib
import math
import random
import re
from datetime import datetime, timezone

from services.recommendation_service import (
    get_candidate_pool,
    _is_scoreable,
    _is_known_artist,
    _compute_similarity_confidence_aware,
    classify_track_length,
    _BPM_JUMP_HARD,
    _BPM_JUMP_SOFT,
    _ENERGY_TOLERANCE,
)

EXERCISE_TYPES = (
    "EASY_HARMONIC",
    "BPM_TRANSITION",
    "ENERGY_TRANSITION",
    "GENRE_CROSSOVER",
    "CHALLENGE",
)

# ---------------------------------------------------------------------------
# Centralized, documented thresholds (Section 9 — no black-box scoring).
# ---------------------------------------------------------------------------

# A candidate's bpm must additionally fall in this range to be training-safe.
# Matches bpm_key_service.detect_bpm_and_key()'s own sanity check (40-250);
# get_candidate_pool() only checks bpm/camelot *presence*, not range/format,
# so this module adds that check itself rather than trusting upstream data
# blindly (Section 7 — candidate safety).
_VALID_BPM_MIN, _VALID_BPM_MAX = 40.0, 250.0
_CAMELOT_RE = re.compile(r"^(1[0-2]|[1-9])[AB]$")

_EASY_MAX_BPM_DELTA = 5.0
_BPM_TRANSITION_MIN_DELTA = 8.0
_BPM_TRANSITION_MAX_DELTA = 15.0
# ~1/3 of recommendation_service._ENERGY_TOLERANCE (0.15) — big enough to be
# an audible dancefloor-energy change, small enough that plenty of real
# tracks qualify.
_ENERGY_TRANSITION_MIN_DELTA = 0.05
# CHALLENGE never exceeds the engine's own hard-reject ceiling — "harder"
# never means "an transition recommend_next() would itself refuse".
_CHALLENGE_MAX_BPM_DELTA = _BPM_JUMP_SOFT
# Floor so CHALLENGE can't select a pairing so incoherent it isn't a
# transition at all (Section 2: "DO NOT select an intentionally bad
# transition").
_CHALLENGE_MIN_SIMILARITY = 0.15
# GENRE_CROSSOVER is MEDIUM below this computed difficulty, HARD at/above it.
_GENRE_CROSSOVER_HARD_THRESHOLD = 0.35

# Weights for the transparent difficulty model (Section 9). Each component
# is normalized to [0, 1] before weighting; weights sum to 1.0.
_DIFFICULTY_WEIGHTS = {
    "bpm": 0.35,
    "harmonic": 0.30,
    "energy": 0.20,
    "genre": 0.10,
    "confidence": 0.05,
}


# ---------------------------------------------------------------------------
# Genre-family normalization (Section 8) — comparison-only, never scoring,
# never a replacement for genre_router's taxonomy.
# ---------------------------------------------------------------------------

def _genre_family(genre_folder) -> str:
    """Normalize a library_index `genre_folder` value to a comparable
    "family" label for GENRE_CROSSOVER selection only.

    Existing genre_folder values are the top-level library folder name,
    sometimes prefixed with "Library/" (e.g. "Library/Bollywood", "House",
    "Same Day Cleaning"). The leaf path segment already IS this project's
    genre grouping (it's what genre_router.py routes tracks into by
    folder), so this just takes that leaf, case-folded for comparison. The
    original, unmodified genre_folder is always what's displayed to
    callers via `_format_track()` — this helper is comparison-only and
    never touches recommendation scoring.
    """
    if not genre_folder or not isinstance(genre_folder, str):
        return ""
    return genre_folder.strip().split("/")[-1].strip().lower()


# ---------------------------------------------------------------------------
# Candidate safety (Section 7)
# ---------------------------------------------------------------------------

def _is_training_safe(doc: dict) -> bool:
    """Additional safety filter layered ON TOP of
    recommendation_service.get_candidate_pool() (which only checks that
    bpm/camelot are present, not that they're well-formed). Excludes
    invalid BPM range, malformed Camelot, and NaN/inf numeric fields —
    exactly the exclusions Section 7 lists that get_candidate_pool() does
    not itself apply.
    """
    if doc.get("missing") is True:
        return False
    if not _is_scoreable(doc):
        return False
    af = doc.get("audio_features") or {}
    bpm = af.get("bpm")
    if not isinstance(bpm, (int, float)) or isinstance(bpm, bool):
        return False
    if isinstance(bpm, float) and (math.isnan(bpm) or math.isinf(bpm)):
        return False
    if not (_VALID_BPM_MIN <= bpm <= _VALID_BPM_MAX):
        return False
    camelot = af.get("camelot")
    if not isinstance(camelot, str) or not _CAMELOT_RE.match(camelot):
        return False
    for field in ("rms_energy", "spectral_centroid_mean", "confidence"):
        val = af.get(field)
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return False
    return True


def _artist_conflicts(doc: dict, used_artists: set) -> bool:
    """True if `doc`'s artist is a REAL (non-placeholder) identity already
    used elsewhere in this session. Placeholder artists ("Unknown", empty,
    ...) never conflict with anything — see recommendation_service's own
    _is_known_artist(), reused here unchanged (Phase 1E semantics)."""
    artist = doc.get("artist", "")
    return _is_known_artist(artist) and artist in used_artists


# ---------------------------------------------------------------------------
# Pairwise metrics — delegates ALL scoring to recommendation_service.
# ---------------------------------------------------------------------------

def _pair_metrics(af_a: dict, af_b: dict) -> dict:
    """Compute transition metrics for a candidate pair using
    recommendation_service._compute_similarity_confidence_aware() as the
    sole scoring source (Section 1: "do NOT create a second recommendation/
    scoring engine"). Adds only presentational derivations on top:
    bpm_delta, energy_delta, and a 3-value harmonic_relation label."""
    sim = _compute_similarity_confidence_aware(af_a, af_b)

    bpm_a, bpm_b = af_a.get("bpm"), af_b.get("bpm")
    bpm_delta = (
        round(abs(float(bpm_a) - float(bpm_b)), 1)
        if bpm_a is not None and bpm_b is not None else None
    )
    energy_a, energy_b = af_a.get("rms_energy"), af_b.get("rms_energy")
    energy_delta = (
        round(float(energy_b) - float(energy_a), 4)
        if energy_a is not None and energy_b is not None else None
    )

    base = sim["camelot_score_base"]
    if base >= 1.0:
        harmonic_relation = "same_key"
    elif base >= 0.75:
        harmonic_relation = "adjacent_key"
    else:
        harmonic_relation = "incompatible"

    return {
        "similarity_score": sim["score"],
        "camelot_score_base": base,
        "camelot_score": sim["camelot_score"],
        "confidence_factor": sim["confidence_factor"],
        "bpm_score": sim["bpm_score"],
        "energy_score": sim["energy_score"],
        "spectral_score": sim["spectral_score"],
        "bpm_delta": bpm_delta,
        "energy_delta": energy_delta,
        "harmonic_relation": harmonic_relation,
    }


# ---------------------------------------------------------------------------
# Difficulty model (Section 9) — transparent, explainable, deterministic.
# ---------------------------------------------------------------------------

def _difficulty_score(metrics: dict, genre_different: bool) -> float:
    """Weighted sum of five normalized [0, 1] components. Higher = harder.
    No ML, no hidden terms — every component is directly explainable from
    metrics already surfaced on the exercise. Weights are centralized in
    _DIFFICULTY_WEIGHTS above."""
    bpm_delta = metrics.get("bpm_delta")
    camelot_base = metrics.get("camelot_score_base", 0.0)
    energy_delta = metrics.get("energy_delta")
    confidence_factor = metrics.get("confidence_factor")
    if confidence_factor is None:
        confidence_factor = 0.5

    bpm_component = min(bpm_delta / _BPM_JUMP_SOFT, 1.0) if bpm_delta is not None else 0.0
    harmonic_component = 1.0 - camelot_base
    energy_component = (
        min(abs(energy_delta) / _ENERGY_TOLERANCE, 1.0) if energy_delta is not None else 0.0
    )
    genre_component = 1.0 if genre_different else 0.0
    confidence_component = 1.0 - confidence_factor

    score = (
        _DIFFICULTY_WEIGHTS["bpm"] * bpm_component
        + _DIFFICULTY_WEIGHTS["harmonic"] * harmonic_component
        + _DIFFICULTY_WEIGHTS["energy"] * energy_component
        + _DIFFICULTY_WEIGHTS["genre"] * genre_component
        + _DIFFICULTY_WEIGHTS["confidence"] * confidence_component
    )
    return round(score, 4)


# ---------------------------------------------------------------------------
# Deterministic seed (Section 5)
# ---------------------------------------------------------------------------

def _daily_seed(date_str: str, pool: list) -> int:
    """Derive a deterministic integer seed from `date_str` plus a stable
    fingerprint of the candidate pool's identity_keys. No timestamps, no
    uncontrolled randomness — identical (date, pool) always yields the same
    seed; a changed library (tracks added/removed) changes the fingerprint
    and therefore the selection, which is the intended "personalization
    follows the actual library" behavior (Section 6)."""
    identity_keys = sorted(d.get("identity_key") for d in pool if d.get("identity_key"))
    fingerprint = hashlib.sha256("|".join(identity_keys).encode("utf-8")).hexdigest()
    material = f"{date_str}:{fingerprint}"
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)


def _shuffled_sources(pool: list, used_keys: set, rng: random.Random) -> list:
    candidates = [d for d in pool if d.get("identity_key") not in used_keys]
    order = candidates[:]
    rng.shuffle(order)
    return order


# ---------------------------------------------------------------------------
# Generic deterministic pair search, reused by all 5 exercise builders.
# ---------------------------------------------------------------------------

def _find_best_pair(pool, used_keys, used_artists, rng, is_valid, score_fn=None):
    """Shuffle candidate sources once (seeded rng), then for each — in that
    fixed order — scan the whole pool for the best-scoring valid target.
    Returns the FIRST source that has at least one valid target, paired
    with its single best match (highest score wins; ties keep whichever is
    found first in `pool`'s own order, so results stay deterministic for a
    fixed pool).

    Runs two passes: first requiring neither side's artist to be a REAL
    artist already used elsewhere in the session (`used_artists`), then —
    only if that finds nothing — a relaxed pass without that constraint.
    This mirrors recommend_next()'s own strict/relax fallback, applied here
    to artist variety instead of the BPM-jump limit. Placeholder artists
    ("Unknown", empty, ...) never participate in this check at all (Phase
    1E semantics, via _artist_conflicts/_is_known_artist) — an all-Unknown
    pool behaves exactly like the relaxed pass from the start.

    `is_valid(source_doc, cand_doc, metrics) -> bool` decides validity.
    `score_fn(source_doc, cand_doc, metrics) -> float` ranks candidates;
    defaults to metrics["similarity_score"] (recommendation_service's own
    confidence-aware similarity — no separate scoring engine).

    Returns (source_doc, target_doc, metrics) or None.
    """
    used_artists = used_artists or set()
    sources = _shuffled_sources(pool, used_keys, rng)

    for relax_artist in (False, True):
        for source in sources:
            if not relax_artist and _artist_conflicts(source, used_artists):
                continue
            src_af = source.get("audio_features") or {}
            src_key = source.get("identity_key")
            best = None
            for cand in pool:
                cand_key = cand.get("identity_key")
                if cand_key in used_keys or cand_key == src_key:
                    continue
                if not relax_artist and _artist_conflicts(cand, used_artists):
                    continue
                cand_af = cand.get("audio_features") or {}
                metrics = _pair_metrics(src_af, cand_af)
                if not is_valid(source, cand, metrics):
                    continue
                score = score_fn(source, cand, metrics) if score_fn else metrics["similarity_score"]
                if best is None or score > best[2]:
                    best = (cand, metrics, score)
            if best is not None:
                return source, best[0], best[1]
    return None


# ---------------------------------------------------------------------------
# The 5 exercise builders (Section 2)
# ---------------------------------------------------------------------------

def _build_easy_harmonic(pool, used_keys, rng, used_artists=None):
    def valid(source, cand, m):
        return (
            m["bpm_delta"] is not None and m["bpm_delta"] <= _EASY_MAX_BPM_DELTA
            and m["harmonic_relation"] in ("same_key", "adjacent_key")
        )
    return _find_best_pair(pool, used_keys, used_artists, rng, valid)


def _build_bpm_transition(pool, used_keys, rng, used_artists=None):
    def valid(source, cand, m):
        return (
            m["bpm_delta"] is not None
            and _BPM_TRANSITION_MIN_DELTA <= m["bpm_delta"] <= _BPM_TRANSITION_MAX_DELTA
            and m["harmonic_relation"] != "incompatible"
        )
    return _find_best_pair(pool, used_keys, used_artists, rng, valid)


def _build_energy_transition(pool, used_keys, rng, used_artists=None):
    def valid(source, cand, m):
        return (
            m["energy_delta"] is not None
            and abs(m["energy_delta"]) >= _ENERGY_TRANSITION_MIN_DELTA
            and m["harmonic_relation"] != "incompatible"
            and (m["bpm_delta"] is None or m["bpm_delta"] <= _BPM_JUMP_SOFT)
        )
    result = _find_best_pair(pool, used_keys, used_artists, rng, valid)
    if result is None:
        return None
    source, target, metrics = result
    metrics = dict(metrics)
    metrics["energy_direction"] = "BUILD" if metrics["energy_delta"] > 0 else "RELEASE"
    return source, target, metrics


def _build_genre_crossover(pool, used_keys, rng, used_artists=None):
    def valid(source, cand, m):
        src_family = _genre_family(source.get("genre_folder"))
        cand_family = _genre_family(cand.get("genre_folder"))
        if not src_family or not cand_family or src_family == cand_family:
            return False
        # "technical transition must still be defensible" — allow an
        # incompatible key ONLY if the BPM gap is still within the hard
        # (not soft) limit; genre is context, never a scoring override.
        if m["harmonic_relation"] == "incompatible" and (
            m["bpm_delta"] is None or m["bpm_delta"] > _BPM_JUMP_HARD
        ):
            return False
        return True
    return _find_best_pair(pool, used_keys, used_artists, rng, valid)


def _build_challenge(pool, used_keys, rng, used_artists=None):
    def valid(source, cand, m):
        if m["bpm_delta"] is None or m["bpm_delta"] > _CHALLENGE_MAX_BPM_DELTA:
            return False
        if m["similarity_score"] < _CHALLENGE_MIN_SIMILARITY:
            return False
        return True

    def score(source, cand, m):
        genre_different = _genre_family(source.get("genre_folder")) != _genre_family(cand.get("genre_folder"))
        return _difficulty_score(m, genre_different)

    return _find_best_pair(pool, used_keys, used_artists, rng, valid, score_fn=score)


_BUILDERS = (
    ("EASY_HARMONIC", "EASY", _build_easy_harmonic),
    ("BPM_TRANSITION", "MEDIUM", _build_bpm_transition),
    ("ENERGY_TRANSITION", "MEDIUM", _build_energy_transition),
    ("GENRE_CROSSOVER", None, _build_genre_crossover),  # difficulty computed dynamically
    ("CHALLENGE", "HARD", _build_challenge),
)


# ---------------------------------------------------------------------------
# Exercise / track formatting (Section 3) — stable public contract.
# ---------------------------------------------------------------------------

def _format_track(doc: dict) -> dict:
    af = doc.get("audio_features") or {}
    return {
        "identity_key": doc.get("identity_key"),
        "title": doc.get("title", ""),
        "artist": doc.get("artist", ""),
        "bpm": af.get("bpm"),
        "key": af.get("key", ""),
        "camelot": af.get("camelot", ""),
        "key_confidence": af.get("confidence"),
        "energy": af.get("rms_energy"),
        "genre": doc.get("genre_folder", ""),
        "length_class": classify_track_length(af.get("duration_sec")),
    }


def _a_or_an(phrase: str) -> str:
    return "an" if phrase[:1].lower() in "aeiou" else "a"


def _easy_harmonic_content(source, target, m):
    relation = m["harmonic_relation"].replace("_", " ")
    instructions = [
        f"Beatmatch {source['title']} into {target['title']} within a few bars.",
        "Focus on tight phrasing — align the 8/16-bar structure before blending.",
        f"Use a clean {relation} harmonic transition with a smooth EQ/filter sweep rather than a hard cut.",
    ]
    success = [
        "Beat grids stay locked throughout the transition.",
        "No audible key clash during the overlap.",
        "The outgoing track's low end is cleared before the new bassline enters.",
    ]
    reason = (
        f"{source['title']} ({source['bpm']} BPM, {source['camelot']}) and {target['title']} "
        f"({target['bpm']} BPM, {target['camelot']}) are {relation} and only "
        f"{m['bpm_delta']} BPM apart — a low-risk pairing for building confidence with "
        "beatmatching and phrasing."
    )
    return instructions, success, reason


def _bpm_transition_content(source, target, m):
    instructions = [
        f"Source BPM: {source['bpm']}. Target BPM: {target['bpm']}. Difference: {m['bpm_delta']} BPM.",
        "Ride the pitch/tempo fader gradually across the transition rather than jumping straight "
        "to the target tempo.",
        "Use a longer blend (extra bars) to give the room time to adjust to the new tempo.",
    ]
    success = [
        "The tempo change feels progressive, not sudden.",
        "Both tracks stay in phrase throughout the ramp.",
    ]
    reason = (
        f"A {m['bpm_delta']} BPM gap ({source['bpm']} → {target['bpm']}) is large enough to "
        "require deliberate tempo adjustment, but still within a manageable, non-jarring range."
    )
    return instructions, success, reason


def _energy_transition_content(source, target, m):
    direction = m["energy_direction"]
    verb = "Build" if direction == "BUILD" else "Release"
    instructions = [
        f"{verb} the energy from {source['title']} into {target['title']}.",
        f"Source energy: {source['energy']}. Target energy: {target['energy']}.",
        "Time the shift off the room (or your own judgement) rather than forcing it early.",
    ]
    success = [
        f"The {direction.lower()} feels intentional, not abrupt.",
        "Harmonic/tempo coherence holds while the energy shifts.",
    ]
    reason = (
        f"Energy moves from {source['energy']} to {target['energy']} "
        f"({'up' if direction == 'BUILD' else 'down'}) while staying technically compatible — "
        f"practice for deliberately {'building' if direction == 'BUILD' else 'releasing'} "
        "dancefloor energy."
    )
    return instructions, success, reason


def _genre_crossover_content(source, target, m):
    relation = m["harmonic_relation"].replace("_", " ")
    instructions = [
        f"Bridge {source['genre']} ({source['title']}) into {target['genre']} ({target['title']}).",
        f"Lean on the shared technical ground ({relation}, {m['bpm_delta']} BPM apart) to carry "
        "the transition across the genre gap.",
        "Consider a longer blend, or an a cappella/instrumental bridge if your library supports it.",
    ]
    success = [
        "The genre shift reads as a deliberate programming choice, not a jarring mismatch.",
        "Technical fundamentals (beatmatching, key) hold up despite the genre contrast.",
    ]
    reason = (
        f"{source['title']} ({source['genre']}) and {target['title']} ({target['genre']}) belong to "
        "different genre families but remain technically compatible enough to bridge — practice "
        "moving the room across styles without losing the mix."
    )
    return instructions, success, reason


def _challenge_content(source, target, m, genre_different):
    dims = []
    if m["bpm_delta"] is not None and m["bpm_delta"] > _BPM_TRANSITION_MAX_DELTA:
        dims.append(f"a larger BPM gap ({m['bpm_delta']} BPM)")
    if m["harmonic_relation"] != "same_key":
        relation = m["harmonic_relation"].replace("_", " ")
        dims.append(f"{_a_or_an(relation)} {relation} harmonic relationship")
    if m["energy_delta"] is not None and abs(m["energy_delta"]) >= _ENERGY_TRANSITION_MIN_DELTA:
        dims.append(f"a notable energy shift ({m['energy_delta']})")
    if genre_different:
        dims.append(f"a genre-family change ({source['genre']} → {target['genre']})")
    if not dims:
        dims.append("a combination of tempo, harmonic, and energy factors")
    dims_str = ", ".join(dims)

    instructions = [
        f"This transition combines {dims_str} — plan your approach before mixing live.",
        "Break it into stages: match tempo first, then bridge key/energy deliberately.",
        "Have a fallback ready (loop, filter, or acappella) in case the blend doesn't land first try.",
    ]
    success = [
        "The transition completes without a train wreck (no audible clash or dead air).",
        "You can name which specific factor(s) made this harder than the session's other exercises.",
    ]
    reason = (
        f"Combines {dims_str} — a deliberately demanding but technically defensible pairing "
        f"(difficulty score {m.get('difficulty_score')}), meant to push beyond this session's other "
        "four exercises."
    )
    return instructions, success, reason


_CONTENT_BUILDERS = {
    "EASY_HARMONIC": _easy_harmonic_content,
    "BPM_TRANSITION": _bpm_transition_content,
    "ENERGY_TRANSITION": _energy_transition_content,
    "GENRE_CROSSOVER": _genre_crossover_content,
}


def _format_exercise(exercise_type, difficulty, source_doc, target_doc, metrics, date_str, genre_different):
    source = _format_track(source_doc)
    target = _format_track(target_doc)

    metrics_out = {
        "bpm_delta": metrics.get("bpm_delta"),
        "energy_delta": metrics.get("energy_delta"),
        "harmonic_relation": metrics.get("harmonic_relation"),
        "similarity_score": metrics.get("similarity_score"),
        "difficulty_score": _difficulty_score(metrics, genre_different),
    }
    if "energy_direction" in metrics:
        metrics_out["energy_direction"] = metrics["energy_direction"]

    if exercise_type == "CHALLENGE":
        instructions, success, reason = _challenge_content(source, target, metrics_out, genre_different)
    else:
        instructions, success, reason = _CONTENT_BUILDERS[exercise_type](source, target, metrics_out)

    return {
        "exercise_id": f"{date_str}-{exercise_type.lower()}",
        "type": exercise_type,
        "title": f"{exercise_type.replace('_', ' ').title()}: {source['title']} → {target['title']}",
        "difficulty": difficulty,
        "source_track": source,
        "target_track": target,
        "metrics": metrics_out,
        "instructions": instructions,
        "success_criteria": success,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Extension points for future phases (Section 6) — deliberately unused here.
# ---------------------------------------------------------------------------

def _default_skill_profile() -> dict:
    """Placeholder shape for a future learned skill profile (Phase 5+).
    Not populated or consumed anywhere in Phase 2 — no historical skill
    data is pretended to exist."""
    return {"skill_profile": None, "session_history": [], "completed_exercises": [], "user_feedback": []}


# ---------------------------------------------------------------------------
# Main entry point (Section 4)
# ---------------------------------------------------------------------------

def generate_daily_session(date_str: str = None, docs: list = None) -> dict:
    """Build the deterministic daily training session.

    Args:
      date_str: "YYYY-MM-DD". Defaults to today (UTC). Drives the
        deterministic seed together with the candidate pool's fingerprint —
        never combined with a timestamp.
      docs: inject a pre-fetched list of library_index docs (same shape
        recommend_next() expects) instead of querying MongoDB — used by
        tests, and by anything that already has the docs in hand. When
        None, performs exactly one read-only Mongo query (no writes ever).

    Returns a dict matching the Phase 2 session contract (see
    docs/PHASE_2_DJ_TRAINING_ENGINE.md): session_id, date, title,
    difficulty, exercise_count, exercises, library_stats, generated_at,
    error. `exercise_count` is 5 only when all 5 types could be built;
    otherwise fewer, with `error` naming which type(s) failed and why —
    this function never raises for an insufficient library, and never
    invents a track to force the count to 5.
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).date().isoformat()

    if docs is None:
        from database import get_library_index_collection
        col = get_library_index_collection()
        docs = list(col.find(
            {"audio_features": {"$exists": True}, "missing": {"$ne": True}},
            {"identity_key": 1, "title": 1, "artist": 1, "genre_folder": 1,
             "audio_features": 1, "missing": 1, "_id": 0},
        ))

    base_pool = get_candidate_pool(docs=docs)
    training_pool = [d for d in base_pool if _is_training_safe(d)]

    generated_at = datetime.now(timezone.utc).isoformat()
    session_id = f"session-{date_str}"
    library_stats = {
        "docs_considered": len(docs),
        "candidate_pool": len(base_pool),
        "training_pool": len(training_pool),
    }
    base_result = {
        "session_id": session_id,
        "date": date_str,
        "title": f"Daily DJ Training — {date_str}",
        "difficulty": None,
        "exercise_count": 0,
        "exercises": [],
        "library_stats": library_stats,
        "generated_at": generated_at,
    }

    if len(training_pool) < 2:
        return {
            **base_result,
            "error": (
                f"insufficient training pool ({len(training_pool)} usable track"
                f"{'s' if len(training_pool) != 1 else ''}) — at least 2 are required to "
                "build even one exercise."
            ),
        }

    seed = _daily_seed(date_str, training_pool)
    rng = random.Random(seed)

    used_keys = set()
    used_artists = set()
    exercises = []
    failures = []

    for exercise_type, fixed_difficulty, builder in _BUILDERS:
        result = builder(training_pool, used_keys, rng, used_artists)
        if result is None:
            failures.append(exercise_type)
            continue

        source_doc, target_doc, metrics = result
        genre_different = (
            _genre_family(source_doc.get("genre_folder")) != _genre_family(target_doc.get("genre_folder"))
        )
        difficulty = fixed_difficulty
        if difficulty is None:  # GENRE_CROSSOVER — computed dynamically
            score = _difficulty_score(metrics, genre_different)
            difficulty = "HARD" if score >= _GENRE_CROSSOVER_HARD_THRESHOLD else "MEDIUM"

        exercise = _format_exercise(
            exercise_type, difficulty, source_doc, target_doc, metrics, date_str, genre_different,
        )
        exercises.append(exercise)

        used_keys.add(source_doc.get("identity_key"))
        used_keys.add(target_doc.get("identity_key"))
        for doc in (source_doc, target_doc):
            artist = doc.get("artist", "")
            if _is_known_artist(artist):
                used_artists.add(artist)

    result = {
        **base_result,
        "difficulty": "MIXED" if exercises else None,
        "exercise_count": len(exercises),
        "exercises": exercises,
    }
    if failures:
        result["error"] = (
            f"could not build {len(failures)} of {len(EXERCISE_TYPES)} required exercise types "
            f"({', '.join(failures)}) — insufficient distinct/valid candidates remaining in the "
            "training pool after uniqueness constraints. No track was invented to fill the gap."
        )
    else:
        result["error"] = None
    return result
