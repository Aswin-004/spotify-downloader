"""
Phase 0 — regression suite for services/recommendation_service.py.

Scope: PHASE 0 ONLY. Every candidate below is a hand-built dict passed via
the `docs=`/`_docs=` dependency-injection parameters — no real MongoDB
connection, no live library data. This mirrors the pattern already used in
tests/test_strict_matcher.py (stdlib unittest, no pytest dependency
declared in requirements.txt, but collectible by pytest too if installed).

Run:
    python -m unittest tests.test_recommendation_service -v
    (from the backend/ directory)

Covers the required Phase 0 validation list:
  - same-BPM candidates score better than very-different-BPM candidates
  - compatible Camelot keys (exact + neighbor) score appropriately
  - large BPM jumps are rejected by the hard limit
  - artist-repeat rules are enforced when enough alternatives exist
  - energy progression / pool-relative normalization behaves correctly
  - missing metadata does not crash the engine (candidates are excluded
    and counted, not scored as 0)
  - low key-detection confidence down-weights camelot trust
  - repeated calls with identical input produce identical results
    (determinism)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.recommendation_service import (
    recommend_next,
    find_similar_tracks,
    generate_playlist_sequence,
    get_candidate_pool,
    _is_scoreable,
    _camelot_score,
    _camelot_neighbors,
    _proximity_score,
    _confidence_factor,
    _normalize_energy_pool,
)


def track(identity_key, artist, bpm, camelot, rms_energy=0.15,
          spectral_centroid_mean=2000.0, confidence=0.8, missing=False,
          title=None, genre_folder="house"):
    """Build a library_index-shaped dict, matching the real schema written
    by bpm_key_service.persist_audio_features()."""
    doc = {
        "identity_key": identity_key,
        "title": title or identity_key,
        "artist": artist,
        "genre_folder": genre_folder,
        "audio_features": {
            "bpm": bpm,
            "camelot": camelot,
            "rms_energy": rms_energy,
            "spectral_centroid_mean": spectral_centroid_mean,
            "confidence": confidence,
        },
    }
    if missing:
        doc["missing"] = True
    return doc


# A pool large enough that the strict (non-relaxed) pass alone satisfies
# top_n=5, so artist-window / hard-BPM-limit behavior isn't muddied by the
# small-pool relax fallback (see TestRelaxFallback for that path instead).
BASE_POOL = [
    track("cur", "Artist A", 128.0, "8A"),
    track("same_bpm", "Artist B", 128.0, "8A", rms_energy=0.15),
    track("t2", "Artist C", 127.0, "8A", rms_energy=0.16),
    track("t3", "Artist D", 129.0, "8A", rms_energy=0.14),
    track("t4", "Artist E", 126.5, "9A", rms_energy=0.15),
    track("t5", "Artist F", 129.5, "7A", rms_energy=0.15),
    track("t6", "Artist G", 128.0, "8A", rms_energy=0.15),
    track("diff_bpm", "Artist H", 90.0, "8A"),
    track("neighbor_key", "Artist I", 129.0, "9A"),
    track("bad_key", "Artist J", 128.0, "2B"),
    track("huge_jump", "Artist K", 200.0, "8A"),
    track("low_conf", "Artist L", 128.0, "8A", confidence=0.1),
    track("missing_meta", "Artist M", None, None),
    track("gone", "Artist N", 128.0, "8A", missing=True),
    track("repeat_artist", "Artist A", 127.0, "8A"),
]


class TestCamelotWheel(unittest.TestCase):
    """Compatible Camelot keys score appropriately."""

    def test_exact_match_scores_1(self):
        self.assertEqual(_camelot_score("8A", "8A"), 1.0)

    def test_neighbor_same_side_scores_075(self):
        self.assertEqual(_camelot_score("8A", "9A"), 0.75)
        self.assertEqual(_camelot_score("8A", "7A"), 0.75)

    def test_parallel_opposite_letter_scores_075(self):
        self.assertEqual(_camelot_score("8A", "8B"), 0.75)

    def test_incompatible_key_scores_0(self):
        self.assertEqual(_camelot_score("8A", "2B"), 0.0)

    def test_wraparound_neighbors(self):
        # 1 and 12 are adjacent on the wheel
        self.assertIn("12A", _camelot_neighbors("1A"))
        self.assertIn("1A", _camelot_neighbors("12A"))

    def test_missing_key_scores_0_not_crash(self):
        self.assertEqual(_camelot_score("", "8A"), 0.0)
        self.assertEqual(_camelot_score("8A", ""), 0.0)
        self.assertEqual(_camelot_score(None, "8A"), 0.0)


class TestProximityScore(unittest.TestCase):
    """Same-BPM scores better than very-different-BPM; missing values are safe."""

    def test_identical_values_score_1(self):
        self.assertEqual(_proximity_score(128.0, 128.0, 20.0), 1.0)

    def test_closer_value_scores_higher(self):
        close = _proximity_score(128.0, 127.0, 20.0)
        far = _proximity_score(128.0, 90.0, 20.0)
        self.assertGreater(close, far)

    def test_beyond_tolerance_floors_at_0(self):
        self.assertEqual(_proximity_score(128.0, 500.0, 20.0), 0.0)

    def test_none_values_score_0_not_crash(self):
        self.assertEqual(_proximity_score(None, 128.0, 20.0), 0.0)
        self.assertEqual(_proximity_score(128.0, None, 20.0), 0.0)
        self.assertEqual(_proximity_score(None, None, 20.0), 0.0)

    def test_nan_scores_0_not_crash(self):
        self.assertEqual(_proximity_score(float("nan"), 128.0, 20.0), 0.0)

    def test_non_numeric_scores_0_not_crash(self):
        self.assertEqual(_proximity_score("not a number", 128.0, 20.0), 0.0)


class TestConfidenceFactor(unittest.TestCase):
    """Low key-detection confidence down-weights camelot trust (Phase 0 adaptation)."""

    def test_high_confidence_full_trust(self):
        self.assertEqual(_confidence_factor(0.9), 1.0)

    def test_at_threshold_full_trust(self):
        self.assertEqual(_confidence_factor(0.5), 1.0)

    def test_low_confidence_partial_trust(self):
        self.assertEqual(_confidence_factor(0.1), 0.2)  # 0.1 / 0.5

    def test_zero_confidence_zero_trust(self):
        self.assertEqual(_confidence_factor(0.0), 0.0)

    def test_missing_confidence_neutral(self):
        self.assertEqual(_confidence_factor(None), 0.5)

    def test_nan_confidence_neutral(self):
        self.assertEqual(_confidence_factor(float("nan")), 0.5)

    def test_non_numeric_confidence_neutral(self):
        self.assertEqual(_confidence_factor("high"), 0.5)


class TestEnergyNormalization(unittest.TestCase):
    """Pool-relative min-max normalization behaves correctly and never crashes."""

    def test_normalizes_to_0_1_range(self):
        docs = [
            track("a", "X", 128, "8A", rms_energy=0.10),
            track("b", "Y", 128, "8A", rms_energy=0.20),
            track("c", "Z", 128, "8A", rms_energy=0.30),
        ]
        norm = _normalize_energy_pool(docs)
        self.assertEqual(norm["a"], 0.0)
        self.assertEqual(norm["c"], 1.0)
        self.assertAlmostEqual(norm["b"], 0.5)

    def test_flat_pool_returns_midpoint(self):
        docs = [track("a", "X", 128, "8A", rms_energy=0.15),
                track("b", "Y", 128, "8A", rms_energy=0.15)]
        norm = _normalize_energy_pool(docs)
        self.assertEqual(norm["a"], 0.5)
        self.assertEqual(norm["b"], 0.5)

    def test_missing_rms_excluded_not_crash(self):
        docs = [track("a", "X", 128, "8A", rms_energy=0.15),
                track("b", "Y", 128, "8A", rms_energy=None)]
        norm = _normalize_energy_pool(docs)
        self.assertIn("a", norm)
        self.assertNotIn("b", norm)

    def test_empty_pool_returns_empty_dict(self):
        self.assertEqual(_normalize_energy_pool([]), {})


class TestCandidatePool(unittest.TestCase):
    """Missing metadata / soft-deleted files do not crash the engine — excluded and counted."""

    def test_missing_flag_excluded(self):
        pool = get_candidate_pool(docs=BASE_POOL)
        self.assertNotIn("gone", [d["identity_key"] for d in pool])

    def test_incomplete_metadata_excluded(self):
        pool = get_candidate_pool(docs=BASE_POOL)
        self.assertNotIn("missing_meta", [d["identity_key"] for d in pool])

    def test_is_scoreable_requires_bpm_and_camelot(self):
        self.assertTrue(_is_scoreable(track("x", "A", 128.0, "8A")))
        self.assertFalse(_is_scoreable(track("x", "A", None, "8A")))
        self.assertFalse(_is_scoreable(track("x", "A", 128.0, None)))
        self.assertFalse(_is_scoreable({"identity_key": "x", "audio_features": {}}))

    def test_exclude_identity_keys_removed(self):
        pool = get_candidate_pool(exclude_identity_keys={"same_bpm"}, docs=BASE_POOL)
        self.assertNotIn("same_bpm", [d["identity_key"] for d in pool])


class TestRecommendNext(unittest.TestCase):
    """recommend_next() — the new Phase 0 entry point."""

    def test_determinism(self):
        r1 = recommend_next("cur", top_n=5, docs=BASE_POOL)
        r2 = recommend_next("cur", top_n=5, docs=BASE_POOL)
        self.assertEqual(r1["recommendations"], r2["recommendations"])

    def test_same_bpm_scores_better_than_far_bpm(self):
        result = recommend_next("cur", top_n=10, docs=BASE_POOL)
        by_key = {r["identity_key"]: r for r in result["recommendations"]}
        # diff_bpm (90 vs 128 = 38 delta) exceeds even the soft BPM limit
        # (25) and must not appear at all.
        self.assertNotIn("diff_bpm", by_key)
        self.assertIn("same_bpm", by_key)
        self.assertGreater(by_key["same_bpm"]["bpm_score"], 0.9)

    def test_large_bpm_jump_rejected(self):
        result = recommend_next("cur", top_n=10, docs=BASE_POOL)
        keys = [r["identity_key"] for r in result["recommendations"]]
        self.assertNotIn("huge_jump", keys)  # 72 bpm delta, exceeds hard and soft limits

    def test_neighbor_key_scored_as_compatible(self):
        result = recommend_next("cur", top_n=10, docs=BASE_POOL)
        by_key = {r["identity_key"]: r for r in result["recommendations"]}
        self.assertIn("neighbor_key", by_key)
        self.assertEqual(by_key["neighbor_key"]["camelot_score_base"], 0.75)
        self.assertTrue(by_key["neighbor_key"]["harmonic_compatible"])

    def test_incompatible_key_not_harmonic_compatible(self):
        result = recommend_next("cur", top_n=10, docs=BASE_POOL)
        by_key = {r["identity_key"]: r for r in result["recommendations"]}
        if "bad_key" in by_key:
            self.assertEqual(by_key["bad_key"]["camelot_score_base"], 0.0)
            self.assertFalse(by_key["bad_key"]["harmonic_compatible"])

    def test_artist_repeat_excluded_when_alternatives_exist(self):
        result = recommend_next("cur", top_n=5, docs=BASE_POOL)
        keys = [r["identity_key"] for r in result["recommendations"]]
        self.assertNotIn("repeat_artist", keys)  # same artist as "cur"

    def test_low_confidence_downweights_camelot_vs_high_confidence(self):
        docs = [
            track("cur", "A", 128.0, "8A", confidence=0.9),
            track("high_conf", "B", 128.0, "8A", confidence=0.9),
            track("low_conf", "C", 128.0, "8A", confidence=0.1),
        ]
        result = recommend_next("cur", top_n=2, docs=docs)
        by_key = {r["identity_key"]: r for r in result["recommendations"]}
        self.assertEqual(by_key["high_conf"]["camelot_score_base"], 1.0)
        self.assertEqual(by_key["low_conf"]["camelot_score_base"], 1.0)
        self.assertEqual(by_key["high_conf"]["camelot_score"], 1.0)
        self.assertAlmostEqual(by_key["low_conf"]["camelot_score"], 0.2)
        self.assertGreater(by_key["high_conf"]["score"], by_key["low_conf"]["score"])
        self.assertFalse(by_key["high_conf"]["key_confidence_low"])
        self.assertTrue(by_key["low_conf"]["key_confidence_low"])

    def test_missing_metadata_does_not_crash_and_is_counted(self):
        result = recommend_next("cur", top_n=5, docs=BASE_POOL)
        self.assertIsNone(result["error"])
        keys = [r["identity_key"] for r in result["recommendations"]]
        self.assertNotIn("missing_meta", keys)
        self.assertEqual(result["candidates_excluded_incomplete_metadata"], 1)
        self.assertEqual(result["candidates_excluded_missing_flag"], 1)

    def test_unknown_current_track_handled_not_crash(self):
        result = recommend_next("does-not-exist", docs=BASE_POOL)
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["recommendations"], [])

    def test_current_track_missing_required_fields_handled_not_crash(self):
        docs = BASE_POOL + [track("bad_current", "Z", None, None)]
        result = recommend_next("bad_current", docs=docs)
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["recommendations"], [])

    def test_invalid_flow_raises_value_error(self):
        with self.assertRaises(ValueError):
            recommend_next("cur", flow="not_a_real_flow", docs=BASE_POOL)

    def test_energy_normalized_present_in_0_1_range(self):
        result = recommend_next("cur", top_n=5, docs=BASE_POOL)
        for rec in result["recommendations"]:
            if rec["energy_normalized"] is not None:
                self.assertGreaterEqual(rec["energy_normalized"], 0.0)
                self.assertLessEqual(rec["energy_normalized"], 1.0)

    def test_recently_played_excluded(self):
        result = recommend_next("cur", top_n=10, recently_played=["same_bpm"], docs=BASE_POOL)
        keys = [r["identity_key"] for r in result["recommendations"]]
        self.assertNotIn("same_bpm", keys)
        self.assertEqual(result["candidates_excluded_recently_played"], 1)

    def test_empty_pool_returns_no_recommendations_no_crash(self):
        docs = [track("cur", "A", 128.0, "8A")]
        result = recommend_next("cur", docs=docs)
        self.assertEqual(result["recommendations"], [])
        self.assertIsNone(result["error"])


class TestRelaxFallback(unittest.TestCase):
    """When the strict pass can't fill top_n, the soft/relaxed pass (matching
    the original _pick_next's own hard->soft BPM fallback) fills the rest —
    including waiving the artist-repeat window if that's the only way to
    return results, exactly as the original single-best-pick logic did."""

    def test_relax_surfaces_same_artist_when_pool_too_small(self):
        docs = [
            track("cur", "Artist A", 128.0, "8A"),
            track("only_other", "Artist A", 128.0, "8A"),
        ]
        result = recommend_next("cur", top_n=3, docs=docs)
        keys = [r["identity_key"] for r in result["recommendations"]]
        self.assertIn("only_other", keys)


class TestFindSimilarTracks(unittest.TestCase):
    """Ported verbatim from the recovered original — behavior must be unchanged."""

    def setUp(self):
        self.docs = [
            track("seed", "A", 128.0, "8A"),
            track("s1", "B", 128.0, "8A", rms_energy=0.16),
            track("s2", "C", 129.0, "9A", rms_energy=0.14),
            track("s3", "D", 90.0, "8A"),
            track("s4", "E", 127.0, "8A"),
        ]

    def test_results_sorted_descending(self):
        results = find_similar_tracks("seed", top_k=5, _docs=self.docs)
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_far_bpm_ranked_last(self):
        results = find_similar_tracks("seed", top_k=5, _docs=self.docs)
        self.assertEqual(results[-1]["identity_key"], "s3")

    def test_unknown_identity_key_returns_empty_list_not_crash(self):
        self.assertEqual(find_similar_tracks("nope", _docs=self.docs), [])

    def test_top_k_limits_results(self):
        results = find_similar_tracks("seed", top_k=2, _docs=self.docs)
        self.assertLessEqual(len(results), 2)


class TestGeneratePlaylistSequence(unittest.TestCase):
    """Ported verbatim from the recovered original — behavior must be unchanged."""

    def setUp(self):
        self.docs = [
            track("seed", "A", 128.0, "8A"),
            track("s1", "B", 128.0, "8A", rms_energy=0.16),
            track("s2", "C", 129.0, "9A", rms_energy=0.14),
            track("s3", "D", 90.0, "8A"),
            track("s4", "E", 127.0, "8A"),
        ]

    def test_sequence_starts_with_seed(self):
        seq = generate_playlist_sequence("seed", length=4, _docs=self.docs)
        self.assertEqual(seq["sequence"][0]["identity_key"], "seed")

    def test_sequence_respects_requested_length_ceiling(self):
        seq = generate_playlist_sequence("seed", length=4, _docs=self.docs)
        self.assertLessEqual(len(seq["sequence"]), 4)

    def test_energy_progression_matches_sequence_length(self):
        seq = generate_playlist_sequence("seed", length=4, _docs=self.docs)
        self.assertEqual(len(seq["energy_progression"]), len(seq["sequence"]))

    def test_quality_metrics_present(self):
        seq = generate_playlist_sequence("seed", length=4, _docs=self.docs)
        self.assertIn("avg_transition_score", seq["quality"])
        self.assertIn("weak_transition_pct", seq["quality"])

    def test_unknown_seed_handled_not_crash(self):
        seq = generate_playlist_sequence("nope", _docs=self.docs)
        self.assertIn("error", seq)

    def test_invalid_flow_raises_value_error(self):
        with self.assertRaises(ValueError):
            generate_playlist_sequence("seed", flow="not_a_real_flow", _docs=self.docs)


if __name__ == "__main__":
    unittest.main()
