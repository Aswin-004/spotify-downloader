"""
backend/services/dj_coach_service.py

Phase 3 — Daily DJ Coach: orchestration layer on top of the Phase 2
Training Engine.

Phase 2 answers "what should I practice?" (training_service.py builds the
5 exercises). Phase 3 answers "how should I practice today's session?" —
it takes Phase 2's output as-is and adds coaching structure around it:
stage ordering/labels, a session objective, a title, an overall difficulty
summary, an estimated duration, and per-exercise coaching instructions/
success criteria.

This module reuses training_service.py (which itself reuses
recommendation_service.py) for every track-selection and scoring decision.
It does NOT recompute similarity, does NOT re-select tracks, and does NOT
touch recommendation scoring. It only reads training_service's finished
exercises and adds coaching-layer text/structure on top.

No LLM, no ML, no external API. No MongoDB writes — this module never
queries Mongo directly at all; it delegates entirely to
training_service.generate_daily_session(), which is itself read-only (or
fully injectable via `docs=`).

DETERMINISM: this module adds no randomness of its own. Every value here
(stage, focus, title, difficulty, duration, instructions) is a pure
function of the already-deterministic Phase 2 `exercises` list, so
identical Phase 2 output always produces identical Phase 3 output. See
training_service.py's own docstring for the seeding strategy.
"""
from datetime import datetime, timezone

from services.training_service import (
    generate_daily_session, EXERCISE_TYPES, _genre_family,
    _BPM_TRANSITION_MAX_DELTA, _ENERGY_TRANSITION_MIN_DELTA,
)

# ---------------------------------------------------------------------------
# Coaching-concept labels layered on top of the fixed Phase 2 exercise order.
# The exercise TYPE (and everything Phase 2 computed about it) is untouched —
# "stage" is purely a coaching/presentation concept.
# ---------------------------------------------------------------------------

STAGE_BY_TYPE = {
    "EASY_HARMONIC": "WARM_UP",
    "BPM_TRANSITION": "TECHNIQUE",
    "ENERGY_TRANSITION": "ENERGY_CONTROL",
    "GENRE_CROSSOVER": "CROSSOVER",
    "CHALLENGE": "CHALLENGE",
}

SKILL_BY_TYPE = {
    "EASY_HARMONIC": "harmonic mixing",
    "BPM_TRANSITION": "BPM control",
    "ENERGY_TRANSITION": "energy management",
    "GENRE_CROSSOVER": "genre crossover",
    "CHALLENGE": "controlled difficult transitions",
}

# One canonical title per possible day's focus. "transition confidence" (the
# sixth vocabulary term from the Phase 3 spec, alongside these five) is used
# in objective descriptions as the session-wide umbrella theme rather than
# as its own focus/title bucket — every exercise, not just one type, builds
# transition confidence, so it doesn't map to a single exercise type.
TITLE_BY_FOCUS = {
    "harmonic mixing": "Smooth Transition Lab",
    "BPM control": "Tempo Control Session",
    "energy management": "Energy Flow Practice",
    "genre crossover": "Genre Bridge Session",
    "controlled difficult transitions": "Transition Challenge",
}

_OBJECTIVE_TITLE_BY_FOCUS = {
    "harmonic mixing": "Sharpen Harmonic Transitions",
    "BPM control": "Build Tempo Control",
    "energy management": "Manage Dancefloor Energy",
    "genre crossover": "Bridge Genres Confidently",
    "controlled difficult transitions": "Push Into Harder Transitions",
}

# Estimated practice time (Section 7) — configurable, centralized, and
# purely an estimate: this module has no way to know how long a DJ actually
# practices, and never claims otherwise.
DURATION_MINUTES_BY_TYPE = {
    "EASY_HARMONIC": 8,
    "BPM_TRANSITION": 10,
    "ENERGY_TRANSITION": 10,
    "GENRE_CROSSOVER": 12,
    "CHALLENGE": 15,
}

# Session-level difficulty label thresholds, applied to the mean of the 5
# exercises' own (already-computed, already-tested) difficulty_score values.
# Centralized and documented, matching training_service.py's own convention
# for its thresholds — no separate/opaque difficulty model is introduced.
_BEGINNER_MAX = 0.25
_INTERMEDIATE_MAX = 0.50


# ---------------------------------------------------------------------------
# Objective / title / difficulty / duration — all pure functions of the
# already-computed Phase 2 exercises list. No randomness, no LLM.
# ---------------------------------------------------------------------------

def _focus_for_exercises(exercises: list) -> str:
    """The day's thematic focus: whichever of the four non-CHALLENGE
    exercises has the highest difficulty_score. CHALLENGE is excluded
    deliberately — training_service.py's own selection rule maximizes its
    difficulty_score by construction (proven in Phase 2 tests), so it would
    always "win" and the session title/focus would never vary day to day.
    Excluding it lets the *practice-progression* focus (exercises 1-4)
    genuinely reflect which skill this particular library snapshot leans
    into hardest. Ties keep the first exercise in fixed type order
    (deterministic; matches training_service's own tie-breaking convention)."""
    candidates = [e for e in exercises if e["type"] != "CHALLENGE"]
    if not candidates:
        candidates = exercises
    best = candidates[0]
    for ex in candidates[1:]:
        if ex["metrics"]["difficulty_score"] > best["metrics"]["difficulty_score"]:
            best = ex
    return SKILL_BY_TYPE.get(best["type"], "harmonic mixing")


def _build_objective(exercises: list) -> dict:
    focus = _focus_for_exercises(exercises)
    skills = [SKILL_BY_TYPE[e["type"]] for e in exercises]
    stages = [STAGE_BY_TYPE[e["type"]] for e in exercises]
    hardest = max(exercises, key=lambda e: e["metrics"]["difficulty_score"])

    description = (
        f"Today's session moves through {', '.join(s.replace('_', ' ').title() for s in stages)}, "
        f"building transition confidence across all five stages. The {hardest['type'].replace('_', ' ').title()} "
        f"exercise ({hardest['source_track']['title']} → {hardest['target_track']['title']}) is the "
        f"session's hardest transition (difficulty score {hardest['metrics']['difficulty_score']}). "
        f"Today's primary focus is {focus}."
    )

    return {
        "title": _OBJECTIVE_TITLE_BY_FOCUS.get(focus, "Practice Today's Session"),
        "focus": focus,
        "description": description,
        "skills": skills,
    }


def _build_difficulty(exercises: list) -> dict:
    scores = [e["metrics"]["difficulty_score"] for e in exercises]
    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

    if avg_score < _BEGINNER_MAX:
        label = "BEGINNER"
    elif avg_score < _INTERMEDIATE_MAX:
        label = "INTERMEDIATE"
    else:
        label = "ADVANCED"

    # Honest progression check rather than an assumed claim: the *overall*
    # span (first exercise easiest, last hardest) is the designed shape —
    # training_service.py's own tests prove CHALLENGE > EASY_HARMONIC always
    # — but the two middle exercises aren't ranked against each other, so a
    # strictly-monotonic sequence isn't guaranteed. Report what's actually
    # true rather than asserting EASY_TO_HARD unconditionally.
    if scores and scores[0] == min(scores) and scores[-1] == max(scores):
        progression = "EASY_TO_HARD"
    else:
        progression = "MIXED"

    return {"score": avg_score, "label": label, "progression": progression}


def _estimated_duration(exercises: list) -> int:
    return sum(DURATION_MINUTES_BY_TYPE.get(e["type"], 0) for e in exercises)


# ---------------------------------------------------------------------------
# Per-exercise coaching content (Sections 8 and 9). PHASE 3.1 REVIEW FIX 4:
# these used to be a full second, parallel step-by-step instruction list —
# largely duplicating (in different words) what mix_plan below now covers in
# more concrete, deck-by-deck detail, and in GENRE_CROSSOVER's case actively
# contradicting Phase 3.1's own accuracy rule ("find a phrase or breakdown"
# claimed structural data — cue points/breakdowns — that doesn't exist in
# this library). `instructions`/`success_criteria` stay on the exercise
# object for API compatibility (existing Phase 3 tests/consumers expect
# them), but `instructions` is now a single short "why this pairing, and
# where to find the detail" summary that always points at mix_plan as the
# primary, actionable source — never a second competing instruction set.
# `success_criteria` is unchanged from pre-3.1 (short checklist items, no
# structural claims, no redundancy risk the way a full instruction list has).
# ---------------------------------------------------------------------------

def _easy_harmonic_coaching(source, target, m):
    relation = m['harmonic_relation'].replace('_', ' ')
    instructions = [
        f"“{source['title']}” → “{target['title']}”: a {relation} pairing, only {m['bpm_delta']} BPM "
        "apart — a low-risk transition for building beatmatching and phrasing confidence. See the "
        "Mix Plan below for the exact deck-by-deck steps.",
    ]
    criteria = [
        "Beats remain aligned throughout the transition.",
        "No obvious bass clash between the two tracks.",
        "The transition occurs on a phrase boundary.",
    ]
    return instructions, criteria


def _bpm_transition_coaching(source, target, m):
    instructions = [
        f"“{source['title']}” → “{target['title']}”: a {m['bpm_delta']} BPM gap "
        f"({source['bpm']} → {target['bpm']}) large enough to need deliberate tempo adjustment. "
        "See the Mix Plan below for the exact deck-by-deck steps.",
    ]
    criteria = [
        "Target BPM is reached smoothly, without an audible snap.",
        "Beats remain aligned once the target tempo is reached.",
        "The tempo change reads as progressive, not sudden.",
    ]
    return instructions, criteria


def _energy_transition_coaching(source, target, m):
    direction = m.get("energy_direction", "BUILD")
    instructions = [
        f"“{source['title']}” → “{target['title']}”: a {direction.lower()} transition "
        f"(source energy {source['energy']} → target {target['energy']}). See the Mix Plan below "
        "for the exact deck-by-deck steps.",
    ]
    criteria = [
        "The energy change feels intentional, not accidental.",
        "No abrupt volume or intensity jump at the transition point.",
        "Harmonic/tempo alignment holds while the energy shifts.",
    ]
    return instructions, criteria


def _genre_crossover_coaching(source, target, m):
    relation = m['harmonic_relation'].replace('_', ' ')
    instructions = [
        f"“{source['title']}” ({source['genre']}) → “{target['title']}” ({target['genre']}): "
        f"technically compatible ({relation}, {m['bpm_delta']} BPM apart) despite the genre gap. "
        "See the Mix Plan below for the exact deck-by-deck steps.",
    ]
    criteria = [
        "The genre crossover sounds controlled, not jarring.",
        "Technical fundamentals (beatmatching, key) hold up despite the genre contrast.",
        "The transition reads as deliberate programming, not a mismatch.",
    ]
    return instructions, criteria


def _challenge_coaching(source, target, m):
    instructions = [
        f"“{source['title']}” → “{target['title']}”: combines a {m['bpm_delta']} BPM gap, "
        f"{m['harmonic_relation'].replace('_', ' ')} key relationship, and an energy delta of "
        f"{m['energy_delta']} — this session's hardest transition. See the Mix Plan below for the "
        "exact deck-by-deck steps.",
    ]
    criteria = [
        "The transition completes without a train wreck (no audible clash or dead air).",
        "You can name which specific factor(s) made this harder than the session's other exercises.",
        "You have a recovery plan ready before attempting the transition live.",
    ]
    return instructions, criteria


_COACHING_BUILDERS = {
    "EASY_HARMONIC": _easy_harmonic_coaching,
    "BPM_TRANSITION": _bpm_transition_coaching,
    "ENERGY_TRANSITION": _energy_transition_coaching,
    "GENRE_CROSSOVER": _genre_crossover_coaching,
    "CHALLENGE": _challenge_coaching,
}


def _coaching_content(exercise_type: str, source: dict, target: dict, metrics: dict):
    """Return (instructions, success_criteria) for one exercise. `source`/
    `target` are the exercise's own source_track/target_track dicts;
    `metrics` is the exercise's own metrics dict. Pure function of already-
    computed Phase 2 data — no additional scoring, no randomness."""
    builder = _COACHING_BUILDERS.get(exercise_type)
    if builder is None:
        raise ValueError(f"unknown exercise_type {exercise_type!r}")
    return builder(source, target, metrics)


# ---------------------------------------------------------------------------
# PHASE 3.1 — MixPlan: structured, step-by-step transition guide.
#
# Upgrades the free-text `instructions`/`success_criteria` above into a
# structured object a UI can render field-by-field (deck setup, cue, start,
# phase 1/2, EQ, finish, listen-for, success) instead of parsing one giant
# string. Built from the SAME already-computed Phase 2 data
# (source_track/target_track/metrics) — no new scoring, no recommendation
# changes, no LLM, no ML.
#
# ACCURACY RULE (see git show 5b579ea:docs/PHASE_3_1_ACTIONABLE_MIX_PLAN.md): this project's
# library_index has no cue-point, intro/outro, breakdown, or phrase-number
# data for any track. Every instruction below that would normally reference
# a specific bar/beat/timestamp instead uses the generic, honest phrasing
# ("a clearly audible 16-bar phrase", "a phrase boundary") — never a
# fabricated number. `limitations` makes this explicit on every MixPlan.
# ---------------------------------------------------------------------------

_PHRASE_START_INSTRUCTION = (
    "Start the target at the beginning of a clearly audible phrase — most dance tracks group into "
    "4, 8, 16, or 32-bar sections, so listen for a clear change (a new instrument entering, a drum "
    "fill, or a shift in intensity) and start Deck B right on that change. No cue-point or "
    "phrase-timestamp data exists in the library for this track, so this has to be judged by ear, "
    "not read off a marker."
)

# Below this key_confidence (audio_features.confidence — the BPM/key
# analysis engine's own confidence, unrelated to recommendation scoring),
# the harmonic relation is flagged as unverified rather than stated flatly.
_KEY_CONFIDENCE_LOW = 0.5

# PHASE 3.1 REVIEW FIX 2: energy_relation must use the SAME "is this a real
# difference" bar training_service.py already established
# (_ENERGY_TRANSITION_MIN_DELTA = 0.05 — the exact threshold that gates
# whether ENERGY_TRANSITION exercises are even selected), not a separately
# invented, looser number. A second, different threshold here would let
# MixPlan label an incidental energy_delta as BUILD/RELEASE on an
# EASY_HARMONIC/BPM_TRANSITION/GENRE_CROSSOVER/CHALLENGE exercise even
# though the project's own standard elsewhere doesn't consider that delta
# meaningful.


def _bpm_adjustment_text(source_bpm, target_bpm, bpm_delta) -> str:
    if source_bpm is None or target_bpm is None or bpm_delta is None:
        return "BPM adjustment unknown — one of the two tracks is missing BPM data."
    if bpm_delta <= 1.0:
        return f"No adjustment needed — {source_bpm} and {target_bpm} BPM are already closely matched."
    direction = "Increase" if target_bpm > source_bpm else "Decrease"
    return (
        f"{direction} Deck B's tempo by {bpm_delta} BPM to match Deck A "
        f"({source_bpm} → {target_bpm} BPM)."
    )


def _energy_relation(energy_delta) -> str:
    """BUILD/RELEASE only when |delta| clears training_service's own
    _ENERGY_TRANSITION_MIN_DELTA bar (0.05) — the identical threshold and
    identical >= comparison training_service.py uses both to select
    ENERGY_TRANSITION exercises and to decide whether an energy shift counts
    as a CHALLENGE difficulty factor. Below that bar: SIMILAR, not a
    fabricated direction. This is the project's existing neutral label —
    training_service.py itself never needs one (its own ENERGY_TRANSITION
    selection already requires >= 0.05), but the other 4 exercise types can
    have any energy_delta, so MixPlan needs a below-threshold label too."""
    if energy_delta is None:
        return "UNKNOWN"
    if abs(energy_delta) >= _ENERGY_TRANSITION_MIN_DELTA:
        return "BUILD" if energy_delta > 0 else "RELEASE"
    return "SIMILAR"


def _deck_assignment(source: dict, target: dict) -> tuple:
    """Deck assignment is fixed and deterministic: source is always Deck A,
    target is always Deck B — no randomness, no per-session variation."""
    deck_a = {"deck": "A", "role": "source", "identity_key": source["identity_key"], "title": source["title"]}
    deck_b = {"deck": "B", "role": "target", "identity_key": target["identity_key"], "title": target["title"]}
    return deck_a, deck_b


def _recommendation_score_and_limitations(source: dict, target: dict, metrics: dict) -> tuple:
    """PHASE 3.1 REVIEW FIX 3: this value is recommendation_service's own
    confidence-aware similarity_score for this pair (already computed by
    Phase 2, reused verbatim, never recomputed) — a measure of how good a
    RECOMMENDATION MATCH this pairing is, not a confidence that the
    MixPlan's own instructions/data are correct. It was previously exposed
    as a field named `confidence`, which invited exactly that
    misreading; renamed to `recommendation_score` to name what it actually
    is. limitations lists what this MixPlan genuinely cannot claim, driven
    only by real fields already on the track (key_confidence, length_class)."""
    recommendation_score = metrics.get("similarity_score")
    limitations = [
        "No cue-point, intro/outro, or phrase-timestamp data exists in the library index for "
        "either track — every phrase/section instruction above is generic guidance, not derived "
        "from this specific recording's actual structure.",
    ]
    for track in (source, target):
        kc = track.get("key_confidence")
        if kc is not None and kc < _KEY_CONFIDENCE_LOW:
            limitations.append(
                f"Key detection confidence for “{track['title']}” is low ({kc}) — verify "
                "the key by ear rather than relying solely on the harmonic relation above."
            )
        if track.get("length_class") == "dj_mix":
            limitations.append(
                f"“{track['title']}” is classified as a long-form DJ mix, not a single track "
                "— apply this plan to a chosen mixing point within it, not the whole recording."
            )
    return recommendation_score, limitations


def _base_mix_plan(source: dict, target: dict, metrics: dict) -> dict:
    """Fields shared by every exercise type — track/deck identity, BPM/key/
    energy comparisons, recommendation_score, limitations. Exercise-type
    builders below layer the technique-specific fields (cue/start/phase_1/
    phase_2/eq/finish/listen_for/success_criteria) on top of this."""
    deck_a, deck_b = _deck_assignment(source, target)
    recommendation_score, limitations = _recommendation_score_and_limitations(source, target, metrics)
    return {
        "source_track": {"identity_key": source["identity_key"], "title": source["title"], "artist": source["artist"]},
        "target_track": {"identity_key": target["identity_key"], "title": target["title"], "artist": target["artist"]},
        "deck_a": deck_a,
        "deck_b": deck_b,
        "source_bpm": source["bpm"],
        "target_bpm": target["bpm"],
        "bpm_adjustment": _bpm_adjustment_text(source["bpm"], target["bpm"], metrics.get("bpm_delta")),
        "source_key": source["camelot"],
        "target_key": target["camelot"],
        "harmonic_relation": metrics.get("harmonic_relation"),
        "source_energy": source["energy"],
        "target_energy": target["energy"],
        "energy_relation": _energy_relation(metrics.get("energy_delta")),
        "start_instruction": _PHRASE_START_INSTRUCTION,
        "recommendation_score": recommendation_score,
        "limitations": limitations,
    }


def _easy_harmonic_mix_plan(source, target, m):
    plan = _base_mix_plan(source, target, m)
    plan["cue_instruction"] = (
        "Headphone-cue Deck B. Listen to its kick drum against Deck A's kick drum (playing on the "
        "main output). Use the jog wheel (or pitch bend button) to nudge Deck B until both kicks "
        "land at exactly the same time, before bringing any of it into the main mix."
    )
    plan["phase_1"] = [
        "Bring Deck B in at low volume with its bass (LOW EQ) turned all the way down, keeping Deck A dominant.",
        "Keep nudging Deck B's jog wheel until the kicks stay together — this is a "
        f"{(m.get('harmonic_relation') or '').replace('_', ' ')} pairing, so the keys shouldn't clash while you do it.",
    ]
    plan["phase_2"] = [
        "Gradually turn up Deck B's mid and high EQ while keeping its bass down.",
        "Listen for the two kick drums again — if they've drifted apart, nudge the jog wheel before touching the bass.",
    ]
    plan["eq_instruction"] = (
        "Turn Deck A's bass (LOW EQ) down to zero while turning Deck B's bass up to normal, in one "
        "smooth move, once the kicks are confirmed together — never run both basslines at full volume at once."
    )
    plan["transition_finish"] = "Turn Deck A's mid and high EQ down to zero, leaving Deck B playing alone."
    plan["listen_for"] = [
        "Both kick drums landing at exactly the same time",
        "No clashing or dissonant notes between the two tracks",
        "No muddy or doubled-up bass while both basslines are still active",
    ]
    plan["success_criteria"] = [
        "The two kick drums stayed lined up for the whole transition.",
        "There was no audible bass collision.",
        "The handoff happened on a phrase boundary, not mid-bar.",
    ]
    return plan


def _bpm_transition_mix_plan(source, target, m):
    plan = _base_mix_plan(source, target, m)
    plan["cue_instruction"] = (
        "Headphone-cue Deck B. Count its kick drums against Deck A's — if Deck B's kicks feel like "
        "they're arriving faster or slower than Deck A's, that's the tempo gap you're about to close "
        "by ear, not by pressing sync."
    )
    plan["phase_1"] = [
        "Move Deck B's pitch/tempo fader a small amount at a time toward Deck A's BPM, listening after each move.",
        "Bring Deck B in quietly, underneath Deck A, while you keep adjusting tempo.",
    ]
    plan["phase_2"] = [
        "Every 8 bars, check the two kick drums again — if they've drifted apart, make another small pitch adjustment.",
        "Once the kicks stay together for a full 8 bars without drifting, begin raising Deck B's level.",
    ]
    plan["eq_instruction"] = (
        "Keep Deck B's bass (LOW EQ) turned down until the tempo is fully matched — bringing the "
        "bass in before the BPMs match will make any remaining drift obvious and unpleasant."
    )
    plan["transition_finish"] = "Once the tempo is fully matched and Deck B is at full level, turn Deck A's EQ down to zero."
    plan["listen_for"] = [
        "Any audible pitch “wobble” as you adjust tempo",
        "The two kick drums drifting apart again after they seemed to match",
        "Whether the tempo change feels gradual rather than sudden",
    ]
    plan["success_criteria"] = [
        f"Reached the target {target['bpm']} BPM without an audible snap.",
        "Beats stayed aligned once the target tempo was reached.",
        "The tempo change read as progressive, not sudden.",
    ]
    return plan


def _energy_transition_mix_plan(source, target, m):
    plan = _base_mix_plan(source, target, m)
    direction = m.get("energy_direction") or plan["energy_relation"]
    building = direction == "BUILD"
    plan["cue_instruction"] = (
        f"Headphone-cue Deck B. Compare how busy it sounds against Deck A — notice the number and "
        f"strength of drums, percussion, and other layered elements in each. Deck B should sound "
        f"{'noticeably fuller or more intense' if building else 'noticeably sparser or calmer'} than "
        f"Deck A before you commit to the {'build' if building else 'release'}."
    )
    plan["phase_1"] = (
        [
            "Bring Deck B in at low volume — don't jump straight to full energy.",
            "Keep Deck A carrying the energy while Deck B's extra layers establish themselves underneath.",
        ] if building else [
            "Start turning Deck A's volume down gradually as Deck B enters.",
            "Reduce Deck A's volume over several bars, not in one abrupt cut.",
        ]
    )
    plan["phase_2"] = (
        [
            "Steadily raise Deck B's volume and EQ presence over several bars, not in one jump.",
            "Keep both kick drums and keys aligned while the energy climbs.",
        ] if building else [
            "Keep lowering Deck A's volume as Deck B settles into the main mix.",
            "Let Deck B's calmer energy carry the room once Deck A is mostly faded.",
        ]
    )
    plan["eq_instruction"] = (
        "Turn Deck A's bass (LOW EQ) down as you turn Deck B's bass up — never leave both basslines "
        "at full volume together, since the combined low end will sound muddy."
    )
    plan["transition_finish"] = (
        "Once Deck B is fully in and carrying the new energy, fade Deck A out completely."
        if building else
        "Once Deck A is fully faded and Deck B is settled at its own energy level, the release is complete."
    )
    # PHASE 3.1 FINAL UX FIX 1: "Harmonic/tempo alignment holding" named a
    # goal without telling the beginner what to actually listen for. Replaced
    # with the same concrete kick-drum/drift check taught in EASY_HARMONIC
    # and BPM_TRANSITION, plus a direct check that the energy actually moved
    # the intended direction (not just "felt" intentional).
    plan["listen_for"] = [
        "Deck B's presence building/easing in gradually, not in one sudden jump",
        "Both kick drums staying together (no rhythmic drift) as Deck B's volume changes",
        f"Deck B's energy actually {'building up' if building else 'easing down'} by the end, not staying flat",
    ]
    plan["success_criteria"] = [
        "The energy change happened gradually, with no sudden volume or intensity jump.",
        "The two kick drums stayed together (no rhythmic drift) throughout.",
        f"The energy clearly {'increased' if building else 'decreased'} by the end of the transition.",
    ]
    return plan


def _genre_crossover_mix_plan(source, target, m):
    plan = _base_mix_plan(source, target, m)
    plan["cue_instruction"] = (
        f"Headphone-cue Deck B. Deck A is {source['genre']} and Deck B is {target['genre']} — the "
        f"genres will sound different, but check what they share technically: tempo "
        f"({source['bpm']} → {target['bpm']} BPM) and key "
        f"({(m.get('harmonic_relation') or '').replace('_', ' ')}). That shared ground is what you'll "
        "lean on to bridge the genre gap."
    )
    plan["phase_1"] = [
        "Bring Deck B in low and slow, matching kicks and cutting its bass the same way you would for any transition.",
        "Keep Deck A's sound dominant until Deck B's beat is locked in underneath it.",
    ]
    plan["phase_2"] = [
        "Gradually shift volume weight toward Deck B, using the matched kicks as the bridge across the genre change.",
        "Give the genre shift several bars to register rather than switching abruptly.",
    ]
    plan["eq_instruction"] = (
        "Handle the bass swap the same as any transition — turn Deck A's bass down as Deck B's bass "
        "comes up. The genre change doesn't require different EQ technique, only more deliberate timing."
    )
    plan["transition_finish"] = "Turn Deck A's remaining EQ down to zero once Deck B is fully established."
    # PHASE 3.1 FINAL UX FIX 1: replaced subjective-only phrasing ("sounds
    # controlled," "reads as deliberate programming," "held up") with
    # concrete, audible checks — every bullet now names something the
    # beginner can actually listen for, not just a judgment call.
    plan["listen_for"] = [
        "Both kick drums remaining together as Deck B's genre-different elements enter",
        "Deck B's instruments/percussion entering without masking or clashing with Deck A's sound",
        "Any harsh or jarring moment as the two styles overlap",
    ]
    plan["success_criteria"] = [
        "The two kick drums stayed together throughout, despite the genre change.",
        "Deck B's elements entered without masking or clashing with Deck A's sound.",
        "There was no harsh or jarring moment as the genres overlapped.",
    ]
    return plan


def _challenge_dims(source, target, m) -> list:
    """PHASE 3.1 REVIEW FIX 1: must reuse training_service._challenge_content()'s
    OWN thresholds (_BPM_TRANSITION_MAX_DELTA=15.0, _ENERGY_TRANSITION_MIN_DELTA=0.05),
    not a second, independently-invented copy — a previous version of this
    function used bare 8.0/0.04, which could name a BPM/energy factor as
    "significant" here while training_service's own `reason` field (built
    from the identical metrics) disagreed, for the same exercise. Using the
    exact same imported constants and comparisons as training_service.py:519-524
    makes the two impossible to disagree on the same input."""
    dims = []
    bpm_delta = m.get("bpm_delta")
    if bpm_delta is not None and bpm_delta > _BPM_TRANSITION_MAX_DELTA:
        dims.append(f"a large BPM gap ({bpm_delta} BPM)")
    relation = m.get("harmonic_relation")
    if relation and relation != "same_key":
        dims.append(f"a {relation.replace('_', ' ')} harmonic relationship")
    energy_delta = m.get("energy_delta")
    if energy_delta is not None and abs(energy_delta) >= _ENERGY_TRANSITION_MIN_DELTA:
        dims.append(f"a notable energy shift ({energy_delta})")
    if _genre_family(source.get("genre")) != _genre_family(target.get("genre")):
        dims.append(f"a genre-family change ({source['genre']} → {target['genre']})")
    if not dims:
        dims.append("a combination of tempo, harmonic, and energy factors")
    return dims


def _challenge_mix_plan(source, target, m):
    plan = _base_mix_plan(source, target, m)
    dims = _challenge_dims(source, target, m)
    dims_str = ", ".join(dims)
    plan["cue_instruction"] = (
        f"Headphone-cue Deck B. Before starting, name every factor working against a clean "
        f"transition here: {dims_str}. Decide which one you'll tackle first."
    )
    plan["phase_1"] = [
        "Tackle the biggest risk factor first — usually tempo: get the kick drums matching before touching key or energy.",
        "Bring Deck B in at minimal volume — don't commit further until that first risk is under control.",
    ]
    plan["phase_2"] = [
        "Address the remaining factors one at a time (key, then energy) rather than all at once.",
        "Keep a loop, filter, or cut ready to trigger immediately if the blend starts to fall apart.",
    ]
    plan["eq_instruction"] = (
        "Move the bass swap in smaller increments than usual — with this many factors in play, a "
        "clean low-end handoff is the highest-risk moment in the transition."
    )
    plan["transition_finish"] = (
        "Complete the handoff only once tempo, key, and energy have all stabilized — don't rush the finish."
    )
    plan["listen_for"] = [
        f"The specific factor(s) making this hard: {dims_str}",
        "Any sign of the transition destabilizing (drift, clash, dead air)",
    ]
    plan["success_criteria"] = [
        "The transition completed without a train wreck (no audible clash or dead air).",
        "You can name which specific factor(s) made this harder than the session's other exercises.",
        "You had a recovery plan ready before attempting it live.",
    ]
    return plan


_MIX_PLAN_BUILDERS = {
    "EASY_HARMONIC": _easy_harmonic_mix_plan,
    "BPM_TRANSITION": _bpm_transition_mix_plan,
    "ENERGY_TRANSITION": _energy_transition_mix_plan,
    "GENRE_CROSSOVER": _genre_crossover_mix_plan,
    "CHALLENGE": _challenge_mix_plan,
}


def build_mix_plan(exercise_type: str, source: dict, target: dict, metrics: dict) -> dict:
    """Build the structured MixPlan for one exercise. `source`/`target` are
    the exercise's own source_track/target_track dicts (training_service's
    `_format_track()` output); `metrics` is the exercise's own metrics dict.
    Pure function of already-computed Phase 2 data — no scoring, no Mongo,
    no randomness, no LLM."""
    builder = _MIX_PLAN_BUILDERS.get(exercise_type)
    if builder is None:
        raise ValueError(f"unknown exercise_type {exercise_type!r}")
    return builder(source, target, metrics)


def _build_coach_summary(exercises: list, objective: dict, difficulty: dict, duration_minutes: int) -> str:
    stage_sequence = " → ".join(STAGE_BY_TYPE[e["type"]].replace("_", " ").title() for e in exercises)
    return (
        f"A {len(exercises)}-exercise session running {stage_sequence}, focused on {objective['focus']} "
        f"({difficulty['label'].lower()}, estimated {duration_minutes} minutes). "
        "Practice criteria are listed per exercise, but this system cannot yet automatically detect "
        "whether a transition was actually executed successfully — that capability belongs to a "
        "future phase."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_daily_coaching_session(date_str: str = None, docs: list = None) -> dict:
    """Build the deterministic daily DJ Coach session on top of Phase 2's
    `generate_daily_session()`. Never recomputes track selection or
    scoring — every exercise, in the same order Phase 2 produced it, is
    simply annotated with a coaching stage label and coaching-specific
    instructions/success criteria.

    Args:
      date_str: "YYYY-MM-DD". Defaults to today (UTC) — passed straight
        through to training_service.generate_daily_session(), which is the
        sole source of the deterministic seed.
      docs: inject a pre-fetched list of library_index docs, exactly like
        training_service.generate_daily_session() — used by tests. When
        None, training_service performs its own single read-only Mongo
        query; this module never queries Mongo directly.

    Returns a dict matching the Phase 3 API contract (see
    git show 5b579ea:docs/PHASE_3_DAILY_DJ_COACH.md): session_id, date, title, objective,
    difficulty, estimated_duration_minutes, exercises, coach_summary,
    future_feedback_supported (always False), generated_at, error.

    Never raises for an insufficient library — mirrors training_service's
    own graceful-failure contract: if fewer than 5 exercises could be
    built, returns whatever could be built plus a clear `error`, never an
    invented exercise.
    """
    training_session = generate_daily_session(date_str=date_str, docs=docs)

    coached_exercises = []
    for ex in training_session["exercises"]:
        # PHASE 3.1 FINAL UX FIX 2: `instructions` still comes from
        # _coaching_content() (a short summary pointing at mix_plan — see
        # FIX 4 in git show 5b579ea:docs/PHASE_3_1_ACTIONABLE_MIX_PLAN.md §13). Its second
        # return value (per-type success criteria) is intentionally
        # DISCARDED here and no longer used for the exercise's own
        # `success_criteria` field — that field is now the exact same list
        # as `mix_plan["success_criteria"]` (derived, not independently
        # authored) so the two can never drift into near-duplicate text
        # again. _coaching_content() itself is untouched (still returns a
        # real criteria list) for backward compatibility with any direct
        # caller/test of that function.
        instructions, _legacy_criteria = _coaching_content(
            ex["type"], ex["source_track"], ex["target_track"], ex["metrics"],
        )
        mix_plan = build_mix_plan(ex["type"], ex["source_track"], ex["target_track"], ex["metrics"])
        coached_exercises.append({
            **ex,
            "stage": STAGE_BY_TYPE[ex["type"]],
            "instructions": instructions,
            "success_criteria": mix_plan["success_criteria"],
            "mix_plan": mix_plan,
        })

    generated_at = datetime.now(timezone.utc).isoformat()

    if not coached_exercises:
        return {
            "session_id": training_session["session_id"],
            "date": training_session["date"],
            "title": None,
            "objective": None,
            "difficulty": None,
            "estimated_duration_minutes": 0,
            "exercises": [],
            "coach_summary": None,
            "future_feedback_supported": False,
            "generated_at": generated_at,
            "error": training_session["error"],
        }

    objective = _build_objective(coached_exercises)
    difficulty = _build_difficulty(coached_exercises)
    duration_minutes = _estimated_duration(coached_exercises)
    title = TITLE_BY_FOCUS.get(objective["focus"], "Daily DJ Practice")
    coach_summary = _build_coach_summary(coached_exercises, objective, difficulty, duration_minutes)

    return {
        "session_id": training_session["session_id"],
        "date": training_session["date"],
        "title": title,
        "objective": objective,
        "difficulty": difficulty,
        "estimated_duration_minutes": duration_minutes,
        "exercises": coached_exercises,
        "coach_summary": coach_summary,
        "future_feedback_supported": False,
        "generated_at": generated_at,
        "error": training_session["error"],
    }
