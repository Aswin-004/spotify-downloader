"""
Phase 3.1 — regression suite for the structured MixPlan
(services/dj_coach_service.py :: build_mix_plan()).

Scope: PHASE 3.1 ONLY. Reuses the same MASTER_POOL fixture as
tests/test_training_service.py / tests/test_dj_coach_service.py via direct
import — no real MongoDB connection, no live library data, no LLM.

Covers, per the Phase 3.1 task spec's required-tests list (1-15):
  1.  every exercise type gets a MixPlan
  2.  source/target are correct
  3.  Deck A/B deterministic
  4.  BPM comes from actual tracks
  5.  Camelot relation is correct
  6.  energy relation is correct
  7.  no fake timestamps
  8.  missing structural data uses safe generic instructions
  9.  deterministic output
  10-12. existing Phase 2 / Phase 3 / recommendation tests still pass
        (run separately via `python -m unittest discover`, not duplicated
        here — see docs/PHASE_3_1_ACTIONABLE_MIX_PLAN.md "Testing")
  13. unknown artist handling unchanged
  14. long-track (dj_mix) handling unchanged
  15. Spotify identity behavior unchanged (out of scope for this module —
      Phase 3.1 touches no Spotify/ID3/Mongo-identity code at all; verified
      structurally by grep in this file's own TestNoForbiddenTouchpoints)

PHASE 3.1 REVIEW FIXES — additional regression tests (class
TestReviewFixes below) for every issue the review found:
  1. CHALLENGE BPM classification matches training_service's own threshold
  2. CHALLENGE energy classification matches training_service's own threshold
  3. energy_relation does not label BUILD/RELEASE below the 0.05 threshold
  4. energy_relation at exactly 0.05 classifies as significant
  5. recommendation_score has the correct value (== similarity_score)
  6. no misleading `confidence` field remains
  7. GENRE_CROSSOVER's old instructions contain no unsupported "breakdown" claim
  8. no "find the phrase"/"find a phrase" unsupported instruction anywhere
  9-13. covered by the existing classes above / separate suites, not duplicated

PHASE 3.1 FINAL UX FIX — additional regression tests (class
TestFinalUXFix below):
  1. ENERGY_TRANSITION listen_for contains concrete audible checks
  2. ENERGY_TRANSITION success_criteria contains concrete checks
  3. GENRE_CROSSOVER listen_for contains concrete audible checks
  4. GENRE_CROSSOVER success_criteria contains concrete checks
  5. no subjective-only success criteria remain in any of the 5 types
  6. exercise.success_criteria is derived from (== not independently
     duplicating) mix_plan.success_criteria
  7. API backward compatibility: instructions/success_criteria fields
     still present and non-empty
  8. all 5 exercise types still generate a MixPlan (re-covered here too)
  9. determinism unchanged (re-covered here too)
  10. existing Phase 2/Phase 3/recommendation suites pass — run separately,
      not duplicated here

Run:
    python -m unittest tests.test_mix_plan -v
    (from the backend/ directory)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_recommendation_service import track  # noqa: E402
from tests.test_training_service import MASTER_POOL  # noqa: E402
from services.training_service import (  # noqa: E402
    EXERCISE_TYPES, _BPM_TRANSITION_MAX_DELTA, _ENERGY_TRANSITION_MIN_DELTA,
)
from services.dj_coach_service import (  # noqa: E402
    generate_daily_coaching_session,
    build_mix_plan,
    _bpm_adjustment_text,
    _energy_relation,
    _deck_assignment,
    _challenge_dims,
    _coaching_content,
)

MIX_PLAN_KEYS = {
    "source_track", "target_track", "deck_a", "deck_b",
    "source_bpm", "target_bpm", "bpm_adjustment",
    "source_key", "target_key", "harmonic_relation",
    "source_energy", "target_energy", "energy_relation",
    "cue_instruction", "start_instruction", "phase_1", "phase_2",
    "eq_instruction", "transition_finish", "listen_for", "success_criteria",
    "recommendation_score", "limitations",
}

# Any bare number immediately followed by a time/beat/bar unit reads as a
# fabricated structural timestamp — e.g. "at 1:32", "bar 64", "beat 12".
_FABRICATED_TIMESTAMP_RE = re.compile(
    r"\b\d+\s*(?:s|sec|seconds|min|minute|minutes)\b|"
    r"\b\d{1,2}:\d{2}\b|"
    r"\bbar\s+\d+\b|\bbeat\s+\d+\b|\bat\s+\d+:\d+\b",
    re.IGNORECASE,
)


def _sample_exercise_data():
    source = {"identity_key": "sp:a", "title": "Track A", "artist": "Artist X",
              "bpm": 128, "key": "Cm", "camelot": "8A", "key_confidence": 0.9,
              "energy": 0.15, "genre": "house", "length_class": "track"}
    target = {"identity_key": "sp:b", "title": "Track B", "artist": "Artist Y",
              "bpm": 130, "key": "Cm", "camelot": "8A", "key_confidence": 0.85,
              "energy": 0.20, "genre": "techno", "length_class": "track"}
    metrics = {"bpm_delta": 2.0, "energy_delta": 0.05, "harmonic_relation": "same_key",
               "similarity_score": 0.9, "difficulty_score": 0.2, "energy_direction": "BUILD"}
    return source, target, metrics


class TestMixPlanShape(unittest.TestCase):

    def test_1_every_exercise_type_gets_a_mix_plan(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            self.assertIsInstance(plan, dict)
            missing = MIX_PLAN_KEYS - set(plan.keys())
            self.assertEqual(missing, set(), f"{etype} missing fields: {missing}")

    def test_frontend_never_needs_to_parse_a_giant_string(self):
        """Every phase/listen_for/success_criteria field is a list, not one
        giant blob — the UI can render field-by-field."""
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            for key in ("phase_1", "phase_2", "listen_for", "success_criteria", "limitations"):
                self.assertIsInstance(plan[key], list, f"{etype}.{key} should be a list")
                self.assertTrue(all(isinstance(x, str) for x in plan[key]))
            for key in ("cue_instruction", "start_instruction", "eq_instruction", "transition_finish"):
                self.assertIsInstance(plan[key], str, f"{etype}.{key} should be a string")


class TestSourceTargetCorrectness(unittest.TestCase):

    def test_2_source_and_target_are_correct(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("EASY_HARMONIC", source, target, metrics)
        self.assertEqual(plan["source_track"]["identity_key"], "sp:a")
        self.assertEqual(plan["source_track"]["title"], "Track A")
        self.assertEqual(plan["target_track"]["identity_key"], "sp:b")
        self.assertEqual(plan["target_track"]["title"], "Track B")

    def test_3_deck_assignment_deterministic(self):
        source, target, metrics = _sample_exercise_data()
        for _ in range(5):
            deck_a, deck_b = _deck_assignment(source, target)
            self.assertEqual(deck_a["deck"], "A")
            self.assertEqual(deck_a["role"], "source")
            self.assertEqual(deck_a["identity_key"], "sp:a")
            self.assertEqual(deck_b["deck"], "B")
            self.assertEqual(deck_b["role"], "target")
            self.assertEqual(deck_b["identity_key"], "sp:b")

    def test_3_deck_assignment_in_full_session_matches_source_target(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            plan = ex["mix_plan"]
            self.assertEqual(plan["deck_a"]["identity_key"], ex["source_track"]["identity_key"])
            self.assertEqual(plan["deck_b"]["identity_key"], ex["target_track"]["identity_key"])


class TestRealDataUsage(unittest.TestCase):

    def test_4_bpm_comes_from_actual_tracks(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            plan = ex["mix_plan"]
            self.assertEqual(plan["source_bpm"], ex["source_track"]["bpm"])
            self.assertEqual(plan["target_bpm"], ex["target_track"]["bpm"])

    def test_5_camelot_relation_is_correct(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            plan = ex["mix_plan"]
            self.assertEqual(plan["source_key"], ex["source_track"]["camelot"])
            self.assertEqual(plan["target_key"], ex["target_track"]["camelot"])
            self.assertEqual(plan["harmonic_relation"], ex["metrics"]["harmonic_relation"])

    def test_6_energy_relation_is_correct(self):
        cases = [
            (0.10, "BUILD"), (-0.10, "RELEASE"), (0.001, "SIMILAR"), (None, "UNKNOWN"),
        ]
        for delta, expected in cases:
            self.assertEqual(_energy_relation(delta), expected)

    def test_6_energy_relation_matches_session_energy_direction_when_present(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            if ex["type"] == "ENERGY_TRANSITION":
                self.assertEqual(ex["mix_plan"]["energy_relation"], ex["metrics"]["energy_direction"])

    def test_bpm_adjustment_uses_real_bpm_values(self):
        self.assertIn("128", _bpm_adjustment_text(128, 136, 8.0))
        self.assertIn("136", _bpm_adjustment_text(128, 136, 8.0))
        self.assertIn("8.0", _bpm_adjustment_text(128, 136, 8.0))
        self.assertIn("No adjustment", _bpm_adjustment_text(128, 128.5, 0.5))
        self.assertIn("unknown", _bpm_adjustment_text(None, 128, None).lower())

    def test_no_invented_bpm_key_energy_values(self):
        """Every numeric MixPlan field traces back to the exercise's own
        source_track/target_track/metrics — nothing is a new computation."""
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            plan = ex["mix_plan"]
            self.assertEqual(plan["source_energy"], ex["source_track"]["energy"])
            self.assertEqual(plan["target_energy"], ex["target_track"]["energy"])
            self.assertEqual(plan["recommendation_score"], ex["metrics"]["similarity_score"])


class TestNoFakeTimestamps(unittest.TestCase):

    def test_7_no_fabricated_timestamps_anywhere_in_mix_plan(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            plan = ex["mix_plan"]
            text_fields = [plan["cue_instruction"], plan["start_instruction"],
                            plan["eq_instruction"], plan["transition_finish"]]
            text_fields += plan["phase_1"] + plan["phase_2"] + plan["listen_for"] + plan["success_criteria"]
            for text in text_fields:
                match = _FABRICATED_TIMESTAMP_RE.search(text)
                self.assertIsNone(match, f"fabricated timestamp-like text found: {match and match.group()!r} in {text!r}")

    def test_8_start_instruction_uses_safe_generic_phrasing(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            lowered = plan["start_instruction"].lower()
            self.assertTrue(
                "phrase" in lowered or "section change" in lowered,
                f"{etype} start_instruction should use generic phrase-boundary language: {plan['start_instruction']!r}",
            )

    def test_8_limitations_always_disclose_missing_structural_data(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            joined = " ".join(plan["limitations"]).lower()
            self.assertIn("cue-point", joined)


class TestDeterminism(unittest.TestCase):

    def test_9_same_inputs_produce_identical_mix_plan(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            p1 = build_mix_plan(etype, source, target, metrics)
            p2 = build_mix_plan(etype, source, target, metrics)
            self.assertEqual(p1, p2)

    def test_9_full_session_mix_plans_deterministic(self):
        s1 = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        s2 = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        self.assertEqual(
            [e["mix_plan"] for e in s1["exercises"]],
            [e["mix_plan"] for e in s2["exercises"]],
        )


class TestRegressionsUnchanged(unittest.TestCase):

    def test_13_unknown_artist_handling_unchanged(self):
        pool = [track("u1", "Unknown", 128.0, "8A", confidence=0.9, genre_folder="house"),
                track("u2", "Unknown", 130.0, "8A", confidence=0.9, genre_folder="house"),
                track("u3", "unknown", 129.0, "9A", confidence=0.9, genre_folder="techno"),
                track("u4", "UNKNOWN", 138.0, "9A", confidence=0.8, genre_folder="trance"),
                track("u5", "Unknown", 150.0, "2B", confidence=0.3, genre_folder="pop")]
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=pool)
        self.assertIsInstance(session, dict)
        self.assertGreaterEqual(len(session["exercises"]), 1)
        for ex in session["exercises"]:
            self.assertIn("mix_plan", ex)

    def test_14_dj_mix_length_class_flagged_in_limitations(self):
        pool = [track("a", "X", 128.0, "8A", confidence=0.9, genre_folder="house"),
                track("mix1", "Y", 128.0, "8A", confidence=0.9, genre_folder="house",
                      duration_sec=11561.7)]
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=pool)
        for ex in session["exercises"]:
            for role in ("source_track", "target_track"):
                if ex[role]["identity_key"] == "mix1":
                    self.assertEqual(ex[role]["length_class"], "dj_mix")
                    joined = " ".join(ex["mix_plan"]["limitations"])
                    self.assertIn("long-form DJ mix", joined)

    def test_15_no_forbidden_touchpoints_in_this_module(self):
        """Phase 3.1 must not touch Spotify identity, ID3 tags, downloads,
        or Mongo writes. Static check on the actual source of the module
        this phase modified."""
        src = Path(__file__).resolve().parent.parent / "services" / "dj_coach_service.py"
        text = src.read_text(encoding="utf-8")
        for forbidden in ("spotify_id", "TXXX", "ID3(", "update_one", "insert_one",
                           "delete_one", "yt_dlp", "requests.get", "urlopen"):
            self.assertNotIn(forbidden, text, f"forbidden touchpoint {forbidden!r} found in dj_coach_service.py")

    def test_existing_phase3_fields_still_present(self):
        """mix_plan is additive — every pre-3.1 field must still be there."""
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            for key in ("exercise_id", "type", "title", "difficulty", "source_track",
                        "target_track", "metrics", "instructions", "success_criteria",
                        "reason", "stage"):
                self.assertIn(key, ex)


class TestReviewFixes(unittest.TestCase):
    """Regression tests for every issue found in the Phase 3.1 review
    (docs/PHASE_3_1_ACTIONABLE_MIX_PLAN.md 'Review fixes' section)."""

    # ---- Fix 1: CHALLENGE BPM dims must use training_service's own threshold ----

    def test_1_challenge_bpm_dims_does_not_flag_at_or_below_threshold(self):
        source, target, _ = _sample_exercise_data()
        m = {"bpm_delta": _BPM_TRANSITION_MAX_DELTA, "harmonic_relation": "same_key",
             "energy_delta": 0.0, "similarity_score": 0.9, "difficulty_score": 0.1}
        dims = _challenge_dims(source, target, m)
        self.assertFalse(any("BPM gap" in d for d in dims),
                          f"bpm_delta == _BPM_TRANSITION_MAX_DELTA ({_BPM_TRANSITION_MAX_DELTA}) must not be flagged")

    def test_1_challenge_bpm_dims_flags_above_threshold(self):
        source, target, _ = _sample_exercise_data()
        m = {"bpm_delta": _BPM_TRANSITION_MAX_DELTA + 0.1, "harmonic_relation": "same_key",
             "energy_delta": 0.0, "similarity_score": 0.9, "difficulty_score": 0.1}
        dims = _challenge_dims(source, target, m)
        self.assertTrue(any("BPM gap" in d for d in dims))

    def test_1_challenge_bpm_dims_agrees_with_training_service_reason(self):
        """End-to-end: whenever the real CHALLENGE exercise's own `reason`
        (built by training_service._challenge_content) calls out a BPM gap,
        the MixPlan's dims must too, and vice versa — same threshold, same
        input, so they can no longer disagree."""
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        challenge = next((e for e in session["exercises"] if e["type"] == "CHALLENGE"), None)
        if challenge is None:
            self.skipTest("no CHALLENGE exercise built from MASTER_POOL for this date")
        bpm_delta = challenge["metrics"]["bpm_delta"]
        reason_flags_bpm = bpm_delta is not None and bpm_delta > _BPM_TRANSITION_MAX_DELTA
        dims = _challenge_dims(challenge["source_track"], challenge["target_track"], challenge["metrics"])
        mix_plan_flags_bpm = any("BPM gap" in d for d in dims)
        self.assertEqual(reason_flags_bpm, mix_plan_flags_bpm)
        # and the mix_plan's own cue_instruction (built from the same dims) must agree too
        cue_flags_bpm = "BPM gap" in challenge["mix_plan"]["cue_instruction"]
        self.assertEqual(reason_flags_bpm, cue_flags_bpm)

    # ---- Fix 1: CHALLENGE energy dims must use training_service's own threshold ----

    def test_2_challenge_energy_dims_does_not_flag_below_threshold(self):
        source, target, _ = _sample_exercise_data()
        m = {"bpm_delta": 2.0, "harmonic_relation": "same_key",
             "energy_delta": _ENERGY_TRANSITION_MIN_DELTA - 0.001,
             "similarity_score": 0.9, "difficulty_score": 0.1}
        dims = _challenge_dims(source, target, m)
        self.assertFalse(any("energy shift" in d for d in dims))

    def test_2_challenge_energy_dims_flags_at_threshold(self):
        source, target, _ = _sample_exercise_data()
        m = {"bpm_delta": 2.0, "harmonic_relation": "same_key",
             "energy_delta": _ENERGY_TRANSITION_MIN_DELTA,
             "similarity_score": 0.9, "difficulty_score": 0.1}
        dims = _challenge_dims(source, target, m)
        self.assertTrue(any("energy shift" in d for d in dims))

    def test_2_challenge_energy_dims_agrees_with_training_service_reason(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        challenge = next((e for e in session["exercises"] if e["type"] == "CHALLENGE"), None)
        if challenge is None:
            self.skipTest("no CHALLENGE exercise built from MASTER_POOL for this date")
        energy_delta = challenge["metrics"]["energy_delta"]
        reason_flags_energy = energy_delta is not None and abs(energy_delta) >= _ENERGY_TRANSITION_MIN_DELTA
        dims = _challenge_dims(challenge["source_track"], challenge["target_track"], challenge["metrics"])
        mix_plan_flags_energy = any("energy shift" in d for d in dims)
        self.assertEqual(reason_flags_energy, mix_plan_flags_energy)

    # ---- Fix 2: energy_relation threshold ----

    def test_3_energy_relation_no_build_or_release_below_threshold(self):
        just_below = _ENERGY_TRANSITION_MIN_DELTA - 0.001
        self.assertEqual(_energy_relation(just_below), "SIMILAR")
        self.assertEqual(_energy_relation(-just_below), "SIMILAR")

    def test_4_energy_relation_at_exactly_threshold_is_significant(self):
        self.assertEqual(_energy_relation(_ENERGY_TRANSITION_MIN_DELTA), "BUILD")
        self.assertEqual(_energy_relation(-_ENERGY_TRANSITION_MIN_DELTA), "RELEASE")

    def test_energy_relation_clearly_above_threshold(self):
        self.assertEqual(_energy_relation(_ENERGY_TRANSITION_MIN_DELTA + 0.10), "BUILD")
        self.assertEqual(_energy_relation(-(_ENERGY_TRANSITION_MIN_DELTA + 0.10)), "RELEASE")

    def test_energy_relation_reuses_training_service_constant_not_a_copy(self):
        """Guards against a second, silently-drifted threshold ever being
        reintroduced: 0.05 - epsilon must be neutral, 0.05 must not be,
        directly against the imported constant, not a hardcoded literal."""
        self.assertEqual(_energy_relation(_ENERGY_TRANSITION_MIN_DELTA - 1e-9), "SIMILAR")
        self.assertEqual(_energy_relation(_ENERGY_TRANSITION_MIN_DELTA), "BUILD")

    # ---- Fix 3: recommendation_score field ----

    def test_5_recommendation_score_correct_value(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            self.assertEqual(ex["mix_plan"]["recommendation_score"], ex["metrics"]["similarity_score"])

    def test_6_no_confidence_field_remains(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            self.assertNotIn("confidence", plan,
                              f"{etype}: misleading 'confidence' field should no longer exist")
            self.assertIn("recommendation_score", plan)

    # ---- Fix 4: old Phase 3 instructions must not contradict MixPlan ----

    def test_7_genre_crossover_no_breakdown_claim(self):
        source, target, metrics = _sample_exercise_data()
        instructions, _criteria = _coaching_content("GENRE_CROSSOVER", source, target, metrics)
        joined = " ".join(instructions).lower()
        self.assertNotIn("breakdown", joined)

    def test_8_no_find_the_phrase_claim_in_any_old_instructions(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            instructions, _criteria = _coaching_content(etype, source, target, metrics)
            joined = " ".join(instructions).lower()
            self.assertNotIn("find a phrase", joined)
            self.assertNotIn("find the phrase", joined)

    def test_old_instructions_point_to_mix_plan_as_primary(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            instructions, _criteria = _coaching_content(etype, source, target, metrics)
            joined = " ".join(instructions).lower()
            self.assertIn("mix plan", joined)

    def test_old_instructions_no_longer_duplicate_mix_plan_step_by_step_detail(self):
        """The old instructions field must stay short (a summary + pointer),
        not a second full step-by-step list duplicating mix_plan."""
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            instructions, _criteria = _coaching_content(etype, source, target, metrics)
            self.assertLessEqual(len(instructions), 2, f"{etype}: instructions should be a short summary, not a full plan")

    # ---- Fix 5: beginner UX concreteness ----

    _BANNED_VAGUE_PHRASES = (
        "confirm its beat grid", "nudge pitch to lock", "confirm the beat grids are still phase-locked",
        "confirm phase-locked", "gauge its perceived intensity", "gauge perceived intensity",
        "identify the shared rhythmic/technical ground", "manage the bass handoff",
    )

    def test_no_flagged_vague_phrases_in_mix_plan(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            all_text = " ".join([
                plan["cue_instruction"], plan["start_instruction"], plan["eq_instruction"],
                plan["transition_finish"], *plan["phase_1"], *plan["phase_2"],
                *plan["listen_for"], *plan["success_criteria"],
            ]).lower()
            for phrase in self._BANNED_VAGUE_PHRASES:
                self.assertNotIn(phrase, all_text, f"{etype}: found banned vague phrase {phrase!r}")

    def test_concrete_kick_drum_technique_present_for_beatmatching(self):
        """The concrete replacement technique (kick-drum alignment by ear)
        must actually appear where beatmatching is taught, not just have the
        old vague phrase removed."""
        source, target, metrics = _sample_exercise_data()
        for etype in ("EASY_HARMONIC", "BPM_TRANSITION"):
            plan = build_mix_plan(etype, source, target, metrics)
            all_text = " ".join([plan["cue_instruction"], *plan["phase_1"], *plan["phase_2"]]).lower()
            self.assertIn("kick", all_text)

    def test_energy_transition_cue_instruction_is_concrete(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("ENERGY_TRANSITION", source, target, metrics)
        lowered = plan["cue_instruction"].lower()
        self.assertTrue(
            "drums" in lowered or "percussion" in lowered,
            f"ENERGY_TRANSITION cue_instruction should name concrete elements to compare: {plan['cue_instruction']!r}",
        )

    def test_genre_crossover_cue_instruction_names_concrete_shared_ground(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("GENRE_CROSSOVER", source, target, metrics)
        lowered = plan["cue_instruction"].lower()
        self.assertIn("bpm", lowered)


class TestFinalUXFix(unittest.TestCase):
    """Regression tests for the Phase 3.1 final UX fix (docs/PHASE_3_1_ACTIONABLE_MIX_PLAN.md §14)."""

    _SUBJECTIVE_ONLY_PHRASES = (
        "sounds controlled", "sounded controlled",
        "reads as deliberate programming", "read as deliberate programming",
        "held up despite", "harmonic/tempo alignment holding", "harmonic and tempo alignment held",
    )

    # ---- 1/2: ENERGY_TRANSITION concreteness ----

    def test_1_energy_transition_listen_for_is_concrete(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("ENERGY_TRANSITION", source, target, metrics)
        joined = " ".join(plan["listen_for"]).lower()
        self.assertIn("kick", joined)
        self.assertIn("drift", joined)
        for phrase in self._SUBJECTIVE_ONLY_PHRASES:
            self.assertNotIn(phrase, joined)

    def test_2_energy_transition_success_criteria_is_concrete(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("ENERGY_TRANSITION", source, target, metrics)
        joined = " ".join(plan["success_criteria"]).lower()
        self.assertIn("kick", joined)
        self.assertIn("drift", joined)
        for phrase in self._SUBJECTIVE_ONLY_PHRASES:
            self.assertNotIn(phrase, joined)

    def test_energy_transition_direction_reflected_in_criteria(self):
        """The energy-direction wording must actually track BUILD vs RELEASE,
        not just always say the same thing regardless of direction."""
        source, target, metrics = _sample_exercise_data()
        build_metrics = dict(metrics, energy_direction="BUILD")
        release_metrics = dict(metrics, energy_direction="RELEASE")
        build_plan = build_mix_plan("ENERGY_TRANSITION", source, target, build_metrics)
        release_plan = build_mix_plan("ENERGY_TRANSITION", source, target, release_metrics)
        self.assertIn("increased", " ".join(build_plan["success_criteria"]).lower())
        self.assertIn("decreased", " ".join(release_plan["success_criteria"]).lower())

    # ---- 3/4: GENRE_CROSSOVER concreteness ----

    def test_3_genre_crossover_listen_for_is_concrete(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("GENRE_CROSSOVER", source, target, metrics)
        joined = " ".join(plan["listen_for"]).lower()
        self.assertIn("kick", joined)
        for phrase in self._SUBJECTIVE_ONLY_PHRASES:
            self.assertNotIn(phrase, joined)

    def test_4_genre_crossover_success_criteria_is_concrete(self):
        source, target, metrics = _sample_exercise_data()
        plan = build_mix_plan("GENRE_CROSSOVER", source, target, metrics)
        joined = " ".join(plan["success_criteria"]).lower()
        self.assertIn("kick", joined)
        for phrase in self._SUBJECTIVE_ONLY_PHRASES:
            self.assertNotIn(phrase, joined)

    # ---- 5: no subjective-only success criteria remain anywhere ----

    def test_5_no_subjective_only_success_criteria_in_any_type(self):
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            plan = build_mix_plan(etype, source, target, metrics)
            joined = " ".join(plan["success_criteria"] + plan["listen_for"]).lower()
            for phrase in self._SUBJECTIVE_ONLY_PHRASES:
                self.assertNotIn(phrase, joined, f"{etype}: found subjective-only phrase {phrase!r}")

    # ---- 6: success_criteria derived from mix_plan, not independently duplicated ----

    def test_6_exercise_success_criteria_derived_from_mix_plan_not_duplicated(self):
        """exercise.success_criteria must be the SAME list as
        exercise.mix_plan.success_criteria (a single source of truth), not
        a second, independently-authored near-duplicate text."""
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            self.assertEqual(ex["success_criteria"], ex["mix_plan"]["success_criteria"])

    # ---- 7: API backward compatibility ----

    def test_7_api_backward_compatibility_fields_present(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        for ex in session["exercises"]:
            self.assertIn("instructions", ex)
            self.assertIn("success_criteria", ex)
            self.assertIsInstance(ex["instructions"], list)
            self.assertIsInstance(ex["success_criteria"], list)
            self.assertTrue(len(ex["instructions"]) > 0)
            self.assertTrue(len(ex["success_criteria"]) > 0)

    def test_7_coaching_content_function_itself_unchanged_contract(self):
        """_coaching_content() must still work standalone (its own return
        value is no longer wired into the final success_criteria field, but
        the function itself, and any direct caller/test of it, is untouched)."""
        source, target, metrics = _sample_exercise_data()
        for etype in EXERCISE_TYPES:
            instructions, criteria = _coaching_content(etype, source, target, metrics)
            self.assertTrue(len(instructions) > 0)
            self.assertTrue(len(criteria) > 0)

    # ---- 8: all 5 types still generate a MixPlan ----

    def test_8_all_five_types_still_generate_mix_plan(self):
        session = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        types_with_plan = {e["type"] for e in session["exercises"] if e.get("mix_plan")}
        self.assertEqual(types_with_plan, set(EXERCISE_TYPES))

    # ---- 9: determinism unchanged ----

    def test_9_determinism_unchanged_after_final_ux_fix(self):
        s1 = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        s2 = generate_daily_coaching_session(date_str="2026-09-18", docs=MASTER_POOL)
        self.assertEqual(
            [(e["mix_plan"], e["success_criteria"]) for e in s1["exercises"]],
            [(e["mix_plan"], e["success_criteria"]) for e in s2["exercises"]],
        )


if __name__ == "__main__":
    unittest.main()
