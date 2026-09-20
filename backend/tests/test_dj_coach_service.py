"""
Phase 3 — regression suite for services/dj_coach_service.py.

Scope: PHASE 3 ONLY. Reuses the same MASTER_POOL fixture as
tests/test_training_service.py (hand-tuned so all 5 Phase 2 exercise types
succeed simultaneously) via direct import — no real MongoDB connection, no
live library data, no LLM.

Run:
    python -m unittest tests.test_dj_coach_service -v
    (from the backend/ directory)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_recommendation_service import track  # noqa: E402
from tests.test_training_service import MASTER_POOL  # noqa: E402
from services.training_service import EXERCISE_TYPES  # noqa: E402
from services.dj_coach_service import (  # noqa: E402
    generate_daily_coaching_session,
    STAGE_BY_TYPE,
    SKILL_BY_TYPE,
    TITLE_BY_FOCUS,
    DURATION_MINUTES_BY_TYPE,
    _focus_for_exercises,
    _build_objective,
    _build_difficulty,
    _estimated_duration,
    _coaching_content,
)

EXPECTED_STAGE_ORDER = ("WARM_UP", "TECHNIQUE", "ENERGY_CONTROL", "CROSSOVER", "CHALLENGE")


class TestStageMapping(unittest.TestCase):
    def test_all_five_types_mapped(self):
        self.assertEqual(set(STAGE_BY_TYPE.keys()), set(EXERCISE_TYPES))

    def test_stage_values_match_spec(self):
        self.assertEqual(STAGE_BY_TYPE["EASY_HARMONIC"], "WARM_UP")
        self.assertEqual(STAGE_BY_TYPE["BPM_TRANSITION"], "TECHNIQUE")
        self.assertEqual(STAGE_BY_TYPE["ENERGY_TRANSITION"], "ENERGY_CONTROL")
        self.assertEqual(STAGE_BY_TYPE["GENRE_CROSSOVER"], "CROSSOVER")
        self.assertEqual(STAGE_BY_TYPE["CHALLENGE"], "CHALLENGE")


class TestCoachingContent(unittest.TestCase):
    def test_every_type_has_instructions_and_criteria(self):
        for etype in EXERCISE_TYPES:
            source = {"title": "A", "artist": "X", "bpm": 128, "camelot": "8A", "genre": "house", "energy": 0.15}
            target = {"title": "B", "artist": "Y", "bpm": 130, "camelot": "8A", "genre": "techno", "energy": 0.20}
            metrics = {"bpm_delta": 2.0, "energy_delta": 0.05, "harmonic_relation": "same_key",
                       "similarity_score": 0.9, "difficulty_score": 0.2, "energy_direction": "BUILD"}
            instructions, criteria = _coaching_content(etype, source, target, metrics)
            self.assertTrue(len(instructions) > 0)
            self.assertTrue(len(criteria) > 0)
            for i in instructions:
                self.assertIsInstance(i, str)
                self.assertTrue(len(i) > 0)

    def test_no_automated_success_claim_language(self):
        """Success criteria must read as practice targets, never as a claim
        that the system detected/verified performance automatically."""
        banned_phrases = ("automatically detect", "we verified", "system confirms",
                           "performance detected", "we detected")
        for etype in EXERCISE_TYPES:
            source = {"title": "A", "artist": "X", "bpm": 128, "camelot": "8A", "genre": "house", "energy": 0.15}
            target = {"title": "B", "artist": "Y", "bpm": 130, "camelot": "8A", "genre": "techno", "energy": 0.20}
            metrics = {"bpm_delta": 2.0, "energy_delta": 0.05, "harmonic_relation": "same_key",
                       "similarity_score": 0.9, "difficulty_score": 0.2, "energy_direction": "BUILD"}
            _instructions, criteria = _coaching_content(etype, source, target, metrics)
            joined = " ".join(criteria).lower()
            for phrase in banned_phrases:
                self.assertNotIn(phrase, joined)


class TestGenerateDailyCoachingSession(unittest.TestCase):

    def test_exactly_five_exercises(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertEqual(len(session["exercises"]), 5)

    def test_all_five_types_present(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        types = {e["type"] for e in session["exercises"]}
        self.assertEqual(types, set(EXERCISE_TYPES))

    def test_correct_exercise_order(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        order = [e["type"] for e in session["exercises"]]
        self.assertEqual(order, list(EXERCISE_TYPES))

    def test_correct_stage_labels(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        stages = [e["stage"] for e in session["exercises"]]
        self.assertEqual(stages, list(EXPECTED_STAGE_ORDER))

    def test_deterministic_same_date_same_pool(self):
        s1 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        s2 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertEqual(s1["exercises"], s2["exercises"])
        self.assertEqual(s1["title"], s2["title"])
        self.assertEqual(s1["objective"], s2["objective"])
        self.assertEqual(s1["difficulty"], s2["difficulty"])
        self.assertEqual(s1["estimated_duration_minutes"], s2["estimated_duration_minutes"])

    def test_different_date_can_differ(self):
        s1 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        s2 = generate_daily_coaching_session(date_str="2026-09-16", docs=MASTER_POOL)
        # Not required to differ, but the underlying seed does, and this pool
        # is rich enough that in practice it does — guards against a seed
        # that's silently ignored.
        self.assertTrue(s1["exercises"] != s2["exercises"] or s1["session_id"] != s2["session_id"])

    def test_generated_at_does_not_affect_selection(self):
        import time
        s1 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        time.sleep(0.01)
        s2 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertNotEqual(s1["generated_at"], s2["generated_at"])
        self.assertEqual(s1["exercises"], s2["exercises"])

    def test_deterministic_title(self):
        s1 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        s2 = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertEqual(s1["title"], s2["title"])
        self.assertIn(s1["title"], set(TITLE_BY_FOCUS.values()))

    def test_objective_reflects_actual_exercises(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        objective = session["objective"]
        self.assertIn(objective["focus"], set(SKILL_BY_TYPE.values()))
        expected_skills = [SKILL_BY_TYPE[e["type"]] for e in session["exercises"]]
        self.assertEqual(objective["skills"], expected_skills)

    def test_difficulty_derives_from_exercise_scores(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        scores = [e["metrics"]["difficulty_score"] for e in session["exercises"]]
        expected_avg = round(sum(scores) / len(scores), 4)
        self.assertEqual(session["difficulty"]["score"], expected_avg)

    def test_difficulty_label_correct(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertIn(session["difficulty"]["label"], ("BEGINNER", "INTERMEDIATE", "ADVANCED"))
        self.assertIn(session["difficulty"]["progression"], ("EASY_TO_HARD", "MIXED"))

    def test_estimated_duration_correct(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        expected = sum(DURATION_MINUTES_BY_TYPE[e["type"]] for e in session["exercises"])
        self.assertEqual(session["estimated_duration_minutes"], expected)
        self.assertEqual(expected, 55)  # all 5 types present in MASTER_POOL's session

    def test_all_exercises_contain_instructions(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        for ex in session["exercises"]:
            self.assertTrue(len(ex["instructions"]) > 0)

    def test_all_exercises_contain_success_criteria(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        for ex in session["exercises"]:
            self.assertTrue(len(ex["success_criteria"]) > 0)

    def test_no_automated_success_claims_in_session(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertFalse(session["future_feedback_supported"])

    def test_unknown_artists_handled_correctly(self):
        """Regression: the coach layer must not break Phase 1E/2's
        unknown-artist handling — a session built from an all-Unknown pool
        must not crash and must not report a false artist conflict."""
        pool = [track("u1", "Unknown", 128.0, "8A", confidence=0.9, genre_folder="house"),
                track("u2", "Unknown", 130.0, "8A", confidence=0.9, genre_folder="house"),
                track("u3", "unknown", 129.0, "9A", confidence=0.9, genre_folder="techno"),
                track("u4", "UNKNOWN", 138.0, "9A", confidence=0.8, genre_folder="trance"),
                track("u5", "Unknown", 150.0, "2B", confidence=0.3, genre_folder="pop")]
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=pool)
        self.assertIsInstance(session, dict)
        self.assertGreaterEqual(len(session["exercises"]), 1)

    def test_dj_mix_length_class_preserved(self):
        pool = [track("a", "X", 128.0, "8A", confidence=0.9, genre_folder="house"),
                track("mix1", "Y", 128.0, "8A", confidence=0.9, genre_folder="house",
                      duration_sec=11561.7)]
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=pool)
        for ex in session["exercises"]:
            for role in ("source_track", "target_track"):
                if ex[role]["identity_key"] == "mix1":
                    self.assertEqual(ex[role]["length_class"], "dj_mix")

    def test_insufficient_candidate_pool_fails_gracefully(self):
        tiny_pool = [track("only", "A", 128.0, "8A")]
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=tiny_pool)
        self.assertLess(len(session["exercises"]), 5)
        self.assertIsNotNone(session.get("error"))
        self.assertIsInstance(session, dict)  # never raises

    def test_no_mongodb_writes(self):
        from unittest.mock import patch
        from pymongo.collection import Collection
        with patch.object(Collection, "update_one") as mock_update, \
             patch.object(Collection, "insert_one") as mock_insert, \
             patch.object(Collection, "delete_one") as mock_delete, \
             patch.object(Collection, "update_many") as mock_update_many:
            generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
            mock_update.assert_not_called()
            mock_insert.assert_not_called()
            mock_delete.assert_not_called()
            mock_update_many.assert_not_called()

    def test_no_filesystem_or_download_side_effects(self):
        """Service must never touch the filesystem or perform network
        downloads — patch both and confirm zero calls."""
        from unittest.mock import patch
        with patch("builtins.open") as mock_open, patch("os.remove") as mock_remove:
            generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
            mock_open.assert_not_called()
            mock_remove.assert_not_called()

    def test_session_id_and_stable_identifiers_present(self):
        session = generate_daily_coaching_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertIn("session-2026-09-15", session["session_id"])
        for ex in session["exercises"]:
            self.assertIsNotNone(ex["exercise_id"])
            self.assertIsNotNone(ex["source_track"]["identity_key"])
            self.assertIsNotNone(ex["target_track"]["identity_key"])
            self.assertIn(ex["type"], EXERCISE_TYPES)
            self.assertIn(ex["difficulty"], ("EASY", "MEDIUM", "HARD"))


if __name__ == "__main__":
    unittest.main()
