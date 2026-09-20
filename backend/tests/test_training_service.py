"""
Phase 2 — regression suite for services/training_service.py.

Scope: PHASE 2 ONLY. Every candidate below is a hand-built dict passed via
the `docs=` dependency-injection parameter — no real MongoDB connection, no
live library data. Mirrors the pattern established in
tests/test_recommendation_service.py (stdlib unittest, docs= injection,
identical library_index-shaped fixtures) — reuses that file's own `track()`
builder directly rather than duplicating it.

Run:
    python -m unittest tests.test_training_service -v
    (from the backend/ directory)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_recommendation_service import track  # noqa: E402
from services.training_service import (  # noqa: E402
    generate_daily_session,
    EXERCISE_TYPES,
    _genre_family,
    _pair_metrics,
    _difficulty_score,
    _is_training_safe,
    _daily_seed,
    _build_easy_harmonic,
    _build_bpm_transition,
    _build_energy_transition,
    _build_genre_crossover,
    _build_challenge,
)


# ---------------------------------------------------------------------------
# Master pool — sized and hand-tuned so every one of the 5 exercise types has
# at least one valid pairing simultaneously, with enough distinct tracks that
# the "avoid reusing tracks across exercises" rule never forces a failure.
# ---------------------------------------------------------------------------
MASTER_POOL = [
    # House cluster around 128 BPM / 8A — feeds EASY_HARMONIC + ENERGY_TRANSITION
    track("h1", "Artist A", 128.0, "8A", rms_energy=0.15, confidence=0.9, genre_folder="house", duration_sec=200.0),
    track("h2", "Artist B", 130.0, "8A", rms_energy=0.16, confidence=0.9, genre_folder="house", duration_sec=210.0),
    track("h3", "Artist C", 138.0, "9A", rms_energy=0.30, confidence=0.85, genre_folder="house", duration_sec=220.0),
    track("h4", "Artist D", 128.0, "8A", rms_energy=0.35, confidence=0.8, genre_folder="house", duration_sec=230.0),
    track("h5", "Artist E", 127.0, "8A", rms_energy=0.05, confidence=0.8, genre_folder="house", duration_sec=190.0),
    # Techno — different genre family, close harmonically to the house cluster
    track("t1", "Artist F", 129.0, "8A", rms_energy=0.20, confidence=0.85, genre_folder="techno", duration_sec=240.0),
    track("t2", "Artist G", 150.0, "2B", rms_energy=0.40, confidence=0.4, genre_folder="techno", duration_sec=250.0),
    track("t3", "Artist H", 132.0, "9A", rms_energy=0.22, confidence=0.75, genre_folder="techno", duration_sec=260.0),
    # Trance with Unknown-artist tracks — feeds artist-conflict tests
    track("u1", "Unknown", 128.0, "8A", rms_energy=0.15, confidence=0.7, genre_folder="trance", duration_sec=200.0),
    track("u2", "Unknown", 129.0, "8A", rms_energy=0.16, confidence=0.7, genre_folder="trance", duration_sec=205.0),
    track("u3", "unknown", 130.0, "9A", rms_energy=0.10, confidence=0.6, genre_folder="trance", duration_sec=195.0),
    # Filler variety
    track("f1", "Artist I", 100.0, "1A", rms_energy=0.12, confidence=0.6, genre_folder="hiphop", duration_sec=180.0),
    track("f2", "Artist J", 160.0, "5B", rms_energy=0.28, confidence=0.65, genre_folder="pop", duration_sec=200.0),
    track("f3", "Artist K", 122.0, "11A", rms_energy=0.18, confidence=0.7, genre_folder="house", duration_sec=210.0),
    track("f4", "Artist L", 140.0, "3B", rms_energy=0.25, confidence=0.55, genre_folder="techno", duration_sec=225.0),
    track("f5", "Artist M", 118.0, "6A", rms_energy=0.14, confidence=0.72, genre_folder="trance", duration_sec=215.0),
    track("f6", "Artist N", 135.0, "10B", rms_energy=0.33, confidence=0.68, genre_folder="pop", duration_sec=205.0),
    track("f7", "Artist O", 108.0, "4A", rms_energy=0.09, confidence=0.6, genre_folder="hiphop", duration_sec=190.0),
    track("f8", "Artist P", 145.0, "7B", rms_energy=0.31, confidence=0.5, genre_folder="techno", duration_sec=230.0),
    # Damaged / invalid entries — must never appear in a generated session
    track("bad_missing_meta", "Artist Q", None, None, genre_folder="house"),
    track("bad_out_of_range_bpm", "Artist R", 500.0, "8A", genre_folder="house"),
    track("bad_malformed_camelot", "Artist S", 128.0, "ZZ", genre_folder="house"),
    track("bad_nan_bpm", "Artist T", float("nan"), "8A", genre_folder="house"),
    track("bad_soft_deleted", "Artist U", 128.0, "8A", genre_folder="house", missing=True),
    # A DJ mix — must classify as dj_mix wherever it appears, never silently as "track"
    track("mix1", "Artist V", 128.0, "8A", rms_energy=0.15, confidence=0.8,
          genre_folder="house", duration_sec=11561.7),
]


class TestGenreFamily(unittest.TestCase):
    def test_strips_library_prefix(self):
        self.assertEqual(_genre_family("Library/Bollywood"), "bollywood")

    def test_bare_name_unchanged_lowercased(self):
        self.assertEqual(_genre_family("House"), "house")

    def test_multi_word_folder_preserved(self):
        self.assertEqual(_genre_family("Same Day Cleaning"), "same day cleaning")

    def test_empty_or_none_safe(self):
        self.assertEqual(_genre_family(""), "")
        self.assertEqual(_genre_family(None), "")


class TestPairMetrics(unittest.TestCase):
    def test_same_key_same_bpm_is_easy(self):
        m = _pair_metrics({"bpm": 128, "camelot": "8A", "rms_energy": 0.15, "confidence": 0.9},
                           {"bpm": 128, "camelot": "8A", "rms_energy": 0.15, "confidence": 0.9})
        self.assertEqual(m["harmonic_relation"], "same_key")
        self.assertEqual(m["bpm_delta"], 0.0)

    def test_neighbor_key_is_adjacent(self):
        m = _pair_metrics({"bpm": 128, "camelot": "8A", "confidence": 0.9},
                           {"bpm": 128, "camelot": "9A", "confidence": 0.9})
        self.assertEqual(m["harmonic_relation"], "adjacent_key")

    def test_incompatible_key(self):
        m = _pair_metrics({"bpm": 128, "camelot": "8A", "confidence": 0.9},
                           {"bpm": 128, "camelot": "2B", "confidence": 0.9})
        self.assertEqual(m["harmonic_relation"], "incompatible")

    def test_energy_delta_sign_reflects_direction(self):
        m = _pair_metrics({"bpm": 128, "camelot": "8A", "rms_energy": 0.10},
                           {"bpm": 128, "camelot": "8A", "rms_energy": 0.30})
        self.assertGreater(m["energy_delta"], 0)


class TestTrainingSafety(unittest.TestCase):
    def test_valid_track_is_safe(self):
        self.assertTrue(_is_training_safe(track("x", "A", 128.0, "8A")))

    def test_out_of_range_bpm_unsafe(self):
        self.assertFalse(_is_training_safe(track("x", "A", 500.0, "8A")))
        self.assertFalse(_is_training_safe(track("x", "A", 10.0, "8A")))

    def test_malformed_camelot_unsafe(self):
        self.assertFalse(_is_training_safe(track("x", "A", 128.0, "ZZ")))
        self.assertFalse(_is_training_safe(track("x", "A", 128.0, "13A")))

    def test_nan_bpm_unsafe(self):
        self.assertFalse(_is_training_safe(track("x", "A", float("nan"), "8A")))

    def test_missing_flag_unsafe(self):
        self.assertFalse(_is_training_safe(track("x", "A", 128.0, "8A", missing=True)))


class TestDailySeed(unittest.TestCase):
    def test_same_date_and_pool_same_seed(self):
        s1 = _daily_seed("2026-09-15", MASTER_POOL)
        s2 = _daily_seed("2026-09-15", MASTER_POOL)
        self.assertEqual(s1, s2)

    def test_different_date_different_seed(self):
        s1 = _daily_seed("2026-09-15", MASTER_POOL)
        s2 = _daily_seed("2026-09-16", MASTER_POOL)
        self.assertNotEqual(s1, s2)

    def test_different_pool_different_seed(self):
        s1 = _daily_seed("2026-09-15", MASTER_POOL)
        s2 = _daily_seed("2026-09-15", MASTER_POOL[:-1])
        self.assertNotEqual(s1, s2)


class TestIndividualBuilders(unittest.TestCase):
    """Each builder in isolation, with tiny controlled pools."""

    def test_easy_harmonic_respects_bpm_and_key_constraints(self):
        pool = [track("a", "X", 128.0, "8A", confidence=0.9),
                track("b", "Y", 130.0, "8A", confidence=0.9),   # valid: delta=2, same key
                track("c", "Z", 160.0, "8A", confidence=0.9)]   # invalid: delta=32
        result = _build_easy_harmonic(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(result)
        source, target, metrics = result
        self.assertLessEqual(metrics["bpm_delta"], 5.0)
        self.assertIn(metrics["harmonic_relation"], ("same_key", "adjacent_key"))
        self.assertNotEqual(target["identity_key"], "c")

    def test_bpm_transition_delta_in_expected_band(self):
        pool = [track("a", "X", 128.0, "8A"),
                track("b", "Y", 139.0, "8A"),   # delta=11, in band
                track("c", "Z", 129.0, "8A")]   # delta=1, too small
        result = _build_bpm_transition(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(result)
        _s, _t, metrics = result
        self.assertGreaterEqual(metrics["bpm_delta"], 8.0)
        self.assertLessEqual(metrics["bpm_delta"], 15.0)

    def test_bpm_transition_never_exceeds_hard_reject_limits(self):
        from services.recommendation_service import _BPM_JUMP_SOFT
        pool = [track("a", "X", 128.0, "8A"),
                track("b", "Y", 128.0 + 40.0, "8A")]  # far beyond any acceptable band
        result = _build_bpm_transition(pool, set(), __import__("random").Random(1))
        if result is not None:
            _s, _t, metrics = result
            self.assertLessEqual(metrics["bpm_delta"], _BPM_JUMP_SOFT)

    def test_energy_transition_has_meaningful_delta_and_direction(self):
        pool = [track("a", "X", 128.0, "8A", rms_energy=0.10),
                track("b", "Y", 128.0, "8A", rms_energy=0.35)]
        result = _build_energy_transition(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(result)
        _s, _t, metrics = result
        self.assertGreater(abs(metrics["energy_delta"]), 0.05)
        self.assertIn(metrics["energy_direction"], ("BUILD", "RELEASE"))

    def test_genre_crossover_uses_different_family(self):
        pool = [track("a", "X", 128.0, "8A", genre_folder="house"),
                track("b", "Y", 129.0, "8A", genre_folder="techno"),
                track("c", "Z", 128.0, "8A", genre_folder="house")]
        result = _build_genre_crossover(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(result)
        source, target, _metrics = result
        self.assertNotEqual(_genre_family(source.get("genre_folder")),
                             _genre_family(target.get("genre_folder")))

    def test_challenge_is_harder_than_easy(self):
        pool = [track("a", "X", 128.0, "8A", confidence=0.9),
                track("easy_b", "Y", 130.0, "8A", confidence=0.9),
                track("hard_b", "Z", 150.0, "2B", confidence=0.3, genre_folder="techno")]
        easy = _build_easy_harmonic(pool, set(), __import__("random").Random(1))
        challenge = _build_challenge(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(easy)
        self.assertIsNotNone(challenge)
        easy_score = _difficulty_score(easy[2], genre_different=False)
        chal_score = _difficulty_score(challenge[2],
                                        genre_different=_genre_family(challenge[0].get("genre_folder"))
                                        != _genre_family(challenge[1].get("genre_folder")))
        self.assertGreater(chal_score, easy_score)

    def test_unknown_artist_pool_still_builds(self):
        """Regression for Phase 1E Issue 1: an all-'Unknown'-artist pool must
        not be treated as one giant artist conflict — a session must still
        be buildable."""
        pool = [track("u1", "Unknown", 128.0, "8A", confidence=0.9),
                track("u2", "Unknown", 130.0, "8A", confidence=0.9),
                track("u3", "unknown", 129.0, "8A", confidence=0.9)]
        result = _build_easy_harmonic(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(result)


class TestGenerateDailySession(unittest.TestCase):

    def test_exactly_five_exercises(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertEqual(session["exercise_count"], 5)
        self.assertEqual(len(session["exercises"]), 5)

    def test_all_five_types_present(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        types = {e["type"] for e in session["exercises"]}
        self.assertEqual(types, set(EXERCISE_TYPES))

    def test_deterministic_same_date_same_pool(self):
        s1 = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        s2 = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertEqual(s1["exercises"], s2["exercises"])

    def test_repeated_calls_deterministic_generated_at_excluded(self):
        s1 = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        s2 = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertNotIn("generated_at", s1["exercises"][0])  # not per-exercise
        self.assertEqual(s1["session_id"], s2["session_id"])
        self.assertEqual(s1["exercises"], s2["exercises"])

    def test_generated_at_does_not_affect_selection(self):
        s1 = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        import time
        time.sleep(0.01)
        s2 = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        self.assertEqual(s1["exercises"], s2["exercises"])

    def test_no_invalid_bpm_in_session(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        for ex in session["exercises"]:
            for role in ("source_track", "target_track"):
                bpm = ex[role]["bpm"]
                self.assertIsNotNone(bpm)
                self.assertGreaterEqual(bpm, 40)
                self.assertLessEqual(bpm, 250)

    def test_no_invalid_camelot_in_session(self):
        import re
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        for ex in session["exercises"]:
            for role in ("source_track", "target_track"):
                cam = ex[role]["camelot"]
                self.assertTrue(re.match(r"^(1[0-2]|[1-9])[AB]$", cam), cam)

    def test_no_damaged_tracks_in_session(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        used_keys = set()
        for ex in session["exercises"]:
            used_keys.add(ex["source_track"]["identity_key"])
            used_keys.add(ex["target_track"]["identity_key"])
        for bad_key in ("bad_missing_meta", "bad_out_of_range_bpm",
                        "bad_malformed_camelot", "bad_nan_bpm", "bad_soft_deleted"):
            self.assertNotIn(bad_key, used_keys)

    def test_easy_harmonic_satisfies_constraints(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        ex = next(e for e in session["exercises"] if e["type"] == "EASY_HARMONIC")
        self.assertEqual(ex["difficulty"], "EASY")
        self.assertLessEqual(ex["metrics"]["bpm_delta"], 5.0)
        self.assertIn(ex["metrics"]["harmonic_relation"], ("same_key", "adjacent_key"))

    def test_bpm_transition_has_intended_difficulty(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        ex = next(e for e in session["exercises"] if e["type"] == "BPM_TRANSITION")
        self.assertGreaterEqual(ex["metrics"]["bpm_delta"], 8.0)
        self.assertLessEqual(ex["metrics"]["bpm_delta"], 15.0)
        joined_instructions = " ".join(ex["instructions"])
        self.assertIn(str(ex["source_track"]["bpm"]), joined_instructions)
        self.assertIn(str(ex["target_track"]["bpm"]), joined_instructions)
        self.assertEqual(ex["difficulty"], "MEDIUM")

    def test_energy_transition_has_meaningful_delta(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        ex = next(e for e in session["exercises"] if e["type"] == "ENERGY_TRANSITION")
        self.assertGreater(abs(ex["metrics"]["energy_delta"]), 0.05)
        self.assertIn(ex["metrics"]["energy_direction"], ("BUILD", "RELEASE"))

    def test_genre_crossover_different_genre_family(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        ex = next(e for e in session["exercises"] if e["type"] == "GENRE_CROSSOVER")
        self.assertNotEqual(_genre_family(ex["source_track"]["genre"]),
                             _genre_family(ex["target_track"]["genre"]))

    def test_challenge_harder_than_easy_harmonic(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        by_type = {e["type"]: e for e in session["exercises"]}
        self.assertGreater(by_type["CHALLENGE"]["metrics"]["difficulty_score"],
                            by_type["EASY_HARMONIC"]["metrics"]["difficulty_score"])
        self.assertEqual(by_type["CHALLENGE"]["difficulty"], "HARD")

    def test_unknown_artists_do_not_create_false_conflicts(self):
        """A pool where the only viable easy-harmonic pair is two
        'Unknown'-artist tracks must still succeed."""
        pool = [track("u1", "Unknown", 128.0, "8A", confidence=0.9, genre_folder="house"),
                track("u2", "Unknown", 130.0, "8A", confidence=0.9, genre_folder="house"),
                track("u3", "unknown", 129.0, "9A", confidence=0.9, genre_folder="techno"),
                track("u4", "UNKNOWN", 138.0, "9A", confidence=0.8, genre_folder="trance"),
                track("u5", "Unknown", 150.0, "2B", confidence=0.3, genre_folder="pop")]
        session = generate_daily_session(date_str="2026-09-15", docs=pool)
        # Whatever exercises could be built, none should have failed purely
        # because two Unknown-artist tracks were (wrongly) treated as the
        # same artist.
        self.assertGreaterEqual(session["exercise_count"], 1)

    def test_dj_mix_length_class_correct_when_present(self):
        pool = [track("a", "X", 128.0, "8A", confidence=0.9, genre_folder="house"),
                track("mix1", "Y", 128.0, "8A", confidence=0.9, genre_folder="house",
                      duration_sec=11561.7)]
        result = _build_easy_harmonic(pool, set(), __import__("random").Random(1))
        self.assertIsNotNone(result)
        source, target, _metrics = result
        from services.recommendation_service import classify_track_length
        for doc in (source, target):
            expected = classify_track_length((doc.get("audio_features") or {}).get("duration_sec"))
            if doc["identity_key"] == "mix1":
                self.assertEqual(expected, "dj_mix")

    def test_no_duplicate_source_target_pairs_across_session(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        used = []
        for ex in session["exercises"]:
            used.append(ex["source_track"]["identity_key"])
            used.append(ex["target_track"]["identity_key"])
        self.assertEqual(len(used), len(set(used)), f"tracks reused across exercises: {used}")

    def test_insufficient_pool_fails_gracefully(self):
        tiny_pool = [track("only", "A", 128.0, "8A")]
        session = generate_daily_session(date_str="2026-09-15", docs=tiny_pool)
        self.assertLess(session["exercise_count"], 5)
        self.assertIsNotNone(session.get("error"))

    def test_missing_optional_metadata_does_not_crash(self):
        pool = MASTER_POOL + [track("no_energy", "Z", 128.0, "8A",
                                     rms_energy=None, spectral_centroid_mean=None,
                                     confidence=None, genre_folder="house")]
        try:
            session = generate_daily_session(date_str="2026-09-15", docs=pool)
        except Exception as e:  # pragma: no cover
            self.fail(f"generate_daily_session raised on missing optional metadata: {e}")
        self.assertIsInstance(session, dict)

    def test_score_and_reason_metadata_present(self):
        session = generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
        for ex in session["exercises"]:
            self.assertIn("similarity_score", ex["metrics"])
            self.assertIsInstance(ex["reason"], str)
            self.assertTrue(len(ex["reason"]) > 0)
            self.assertTrue(len(ex["instructions"]) > 0)
            self.assertTrue(len(ex["success_criteria"]) > 0)

    def test_no_mongodb_writes(self):
        """Service must never write — patch pymongo's Collection.update_one/
        insert_one/delete_one at the class level and confirm zero calls
        across a full session generation."""
        from unittest.mock import patch
        from pymongo.collection import Collection
        with patch.object(Collection, "update_one") as mock_update, \
             patch.object(Collection, "insert_one") as mock_insert, \
             patch.object(Collection, "delete_one") as mock_delete, \
             patch.object(Collection, "update_many") as mock_update_many:
            generate_daily_session(date_str="2026-09-15", docs=MASTER_POOL)
            mock_update.assert_not_called()
            mock_insert.assert_not_called()
            mock_delete.assert_not_called()
            mock_update_many.assert_not_called()


if __name__ == "__main__":
    unittest.main()
