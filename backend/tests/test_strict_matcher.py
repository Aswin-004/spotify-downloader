"""
Phase 2 matcher hardening — focused regression suite for services/strict_matcher.py
and the search-stage-selection logic in services/downloader_service.py.

Scope, per the Phase 2 task spec:
  - MATCHING LOGIC ONLY. No real downloads, no network calls, no real
    YouTube/Spotify data. Every candidate below is a hand-built dict.
  - stdlib unittest (no pytest dependency declared in requirements.txt),
    but collectible by pytest too if it happens to be installed.

Run:
    python -m unittest tests.test_strict_matcher -v
    (from the backend/ directory)

Case numbering below matches the task's TESTS section (cases 1-14).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.strict_matcher import (
    score_candidate,
    select_best_candidate,
    version_mismatch,
    duration_score,
    HARD_DURATION_LIMIT_SEC,
    ARTIST_GATE_MIN_SCORE,
)


def candidate(title, duration, uploader="", verified=False):
    """Build a raw search-result-shaped dict, as select_best_candidate expects."""
    return {
        "title": title,
        "duration": duration,
        "url": f"https://youtube.com/watch?v={abs(hash(title)) % 10_000}",
        "uploader": uploader,
        "channel_is_verified": verified,
    }


# ═══════════════════════════════════════════════════════════════════
# Cases 1-13: services/strict_matcher.py matching logic
# ═══════════════════════════════════════════════════════════════════

class TestArtistValidation(unittest.TestCase):
    """Case 1, 2, 9: artist gate — accept correct, reject wrong, allow featured."""

    def test_case1_exact_title_exact_artist_matching_duration_accepts(self):
        score, rejections = score_candidate(
            yt_title="Blinding Lights",
            actual_duration_sec=200,
            spotify_title="Blinding Lights",
            artist="The Weeknd",
            expected_duration_sec=200,
            uploader="The Weeknd",
        )
        self.assertEqual(rejections, [])
        self.assertGreaterEqual(score, 0.40)

    def test_case2_exact_title_wrong_artist_rejects(self):
        # Realistic case: same generic title, different artist, duration is
        # NOT a coincidental near-perfect match (the common real-world shape —
        # two different recordings of a same-titled song rarely also share a
        # sub-2-second duration).
        score, rejections = score_candidate(
            yt_title="Criminal",
            actual_duration_sec=230,
            spotify_title="Criminal",
            artist="The Weeknd",
            expected_duration_sec=200,               # 30s off — clears none of the tight tiers
            uploader="Ariana Grande - Topic",         # a real, specific, different artist
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("Artist mismatch" in r for r in rejections), rejections)

    def test_case2b_documented_residual_edge_case(self):
        """
        NOT one of the 14 required cases — documents a known, accepted
        remaining limitation (see Phase 2 report §7). If a wrong-artist
        candidate shares BOTH a near-exact title (>=0.90) AND a near-perfect
        duration (diff <= 2s) with the request, the artist gate's exemption
        can still let it through. This is the narrowed (not eliminated)
        version of the pre-Phase-2 vulnerability — closing it completely
        would require an artist-name-presence check (e.g. fingerprinting),
        which is out of scope for this phase. Asserted here so any future
        change to this behavior shows up as a deliberate diff, not a
        silent regression.
        """
        score, rejections = score_candidate(
            yt_title="Criminal",
            actual_duration_sec=200,   # coincidentally exact
            spotify_title="Criminal",  # coincidentally exact title
            artist="The Weeknd",
            expected_duration_sec=200,
            uploader="Totally Different Artist",
        )
        self.assertGreater(score, 0.0)  # documented as-is, not asserted as desirable

    def test_case9_featured_artist_accepts(self):
        score, rejections = score_candidate(
            yt_title="Post Malone, Swae Lee - Sunflower (Spider-Man: Into the Spider-Verse)",
            actual_duration_sec=158,
            spotify_title="Sunflower - Spider-Man: Into the Spider-Verse",
            artist="Post Malone, Swae Lee",
            expected_duration_sec=158,
            uploader="Post Malone",
        )
        self.assertEqual(rejections, [])
        self.assertGreaterEqual(score, 0.40)

    def test_artist_gate_min_score_boundary_unchanged(self):
        # Not a required case — regression anchor: ARTIST_GATE_MIN_SCORE
        # itself was not touched this phase (only the exemption's duration
        # bar was), so 0.35 must still be the boundary.
        self.assertEqual(ARTIST_GATE_MIN_SCORE, 0.35)


class TestVersionValidation(unittest.TestCase):
    """Cases 4-8, 11, 12: remix/edit/live/acoustic/karaoke/cover handling."""

    SP_TITLE = "Blinding Lights"
    ARTIST = "The Weeknd"
    DUR = 200

    def test_case4_remix_rejected(self):
        score, rejections = score_candidate(
            "Blinding Lights (Astral Remix)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Astral",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("Version mismatch" in r for r in rejections), rejections)

    def test_case5_radio_edit_rejected(self):
        score, rejections = score_candidate(
            "Blinding Lights (Radio Edit)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="The Weeknd",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("Version mismatch" in r for r in rejections), rejections)

    def test_case6_extended_mix_rejected(self):
        score, rejections = score_candidate(
            "Blinding Lights (Extended Mix)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="The Weeknd",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("Version mismatch" in r for r in rejections), rejections)

    def test_case7_live_rejected(self):
        score, rejections = score_candidate(
            "Blinding Lights (Live at Wembley)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="The Weeknd",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("Version mismatch" in r for r in rejections), rejections)

    def test_case8a_acoustic_rejected_when_not_requested(self):
        score, rejections = score_candidate(
            "Blinding Lights (Acoustic Version)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="The Weeknd",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("Version mismatch" in r for r in rejections), rejections)

    def test_case8b_acoustic_accepted_when_explicitly_requested(self):
        score, rejections = score_candidate(
            "Blinding Lights (Acoustic)", self.DUR, "Blinding Lights - Acoustic",
            self.ARTIST, self.DUR, uploader="The Weeknd",
        )
        self.assertNotIn("Version mismatch", " ".join(rejections))
        self.assertGreaterEqual(score, 0.40)

    def test_case11a_karaoke_rejected_when_not_requested(self):
        score, rejections = score_candidate(
            "Blinding Lights (Karaoke Version)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Karaoke Channel",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("forbidden keyword" in r for r in rejections), rejections)

    def test_case11b_instrumental_rejected_when_not_requested(self):
        score, rejections = score_candidate(
            "Blinding Lights (Instrumental)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="The Weeknd",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("forbidden keyword" in r for r in rejections), rejections)

    def test_case11c_karaoke_allowed_when_explicitly_requested(self):
        score, rejections = score_candidate(
            "Blinding Lights (Karaoke)", self.DUR, "Blinding Lights - Karaoke",
            self.ARTIST, self.DUR, uploader="The Weeknd",
        )
        self.assertNotIn("forbidden keyword", " ".join(rejections))

    def test_case12_cover_rejected_when_not_requested(self):
        score, rejections = score_candidate(
            "Blinding Lights (Cover by Jane Doe)", self.DUR, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Jane Doe",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("forbidden keyword" in r for r in rejections), rejections)

    def test_version_mismatch_direct_symmetric_exemption(self):
        # candidate carries a version word the query didn't ask for -> flagged
        self.assertEqual(version_mismatch("Song (Remix)", "Song"), frozenset({"remix"}))
        # candidate and query both carry it -> not flagged
        self.assertIsNone(version_mismatch("Song (Remix)", "Song - Remix"))
        # candidate carries no version word at all -> not flagged
        self.assertIsNone(version_mismatch("Song (Official Audio)", "Song"))
        # "instrumental" deliberately excluded from VERSION_TOKENS (handled
        # earlier by REJECT_KEYWORDS instead) — must not double-fire here
        self.assertIsNone(version_mismatch("Song (Instrumental)", "Song"))


class TestSearchStageSelection_MatcherLevel(unittest.TestCase):
    """Case 13: correct candidate wins among similar-titled options."""

    def test_case13_similar_titles_correct_selection(self):
        candidates = [
            candidate("Hotline Bling", 267, uploader="Drake"),        # correct
            candidate("Hotline", 90, uploader="Random Ringtones"),    # wrong song, partial title overlap
        ]
        best, score, reason = select_best_candidate(
            candidates, spotify_title="Hotline Bling", artist="Drake",
            expected_duration_sec=267, min_score=0.40,
        )
        self.assertIsNotNone(best)
        self.assertEqual(best["title"], "Hotline Bling")


class TestMusicVideoVsAudio(unittest.TestCase):
    """Case 10: video vs audio-tagged uploads should be treated equivalently."""

    def test_case10_video_and_audio_tags_score_equivalently(self):
        video_score, video_rej = score_candidate(
            "Blinding Lights (Official Music Video)", 200, "Blinding Lights",
            "The Weeknd", 200, uploader="The Weeknd",
        )
        audio_score, audio_rej = score_candidate(
            "Blinding Lights (Official Audio)", 200, "Blinding Lights",
            "The Weeknd", 200, uploader="The Weeknd",
        )
        self.assertEqual(video_rej, [])
        self.assertEqual(audio_rej, [])
        self.assertGreaterEqual(video_score, 0.40)
        self.assertGreaterEqual(audio_score, 0.40)
        self.assertAlmostEqual(video_score, audio_score, delta=0.05)


class TestSameArtistWrongTitle(unittest.TestCase):
    """Case 3."""

    def test_case3_same_artist_wrong_title_rejects(self):
        score, rejections = score_candidate(
            "Save Your Tears", 215, "Blinding Lights", "The Weeknd",
            200, uploader="The Weeknd",
        )
        self.assertLess(score, 0.40)


class TestDurationValidation(unittest.TestCase):
    """Case: duration handling — HARD_DURATION_LIMIT_SEC tightening (90 -> 60)."""

    def test_hard_duration_limit_now_60s_not_90s(self):
        self.assertEqual(HARD_DURATION_LIMIT_SEC, 60)

    def test_duration_score_tier_boundaries_unchanged(self):
        # Regression anchor — Phase 2 reuses these tiers, does not redefine them.
        self.assertEqual(duration_score(200, 200), 1.0)
        self.assertEqual(duration_score(204, 200), 0.8)
        self.assertEqual(duration_score(209, 200), 0.5)
        self.assertEqual(duration_score(225, 200), 0.2)
        self.assertEqual(duration_score(255, 200), 0.1)
        self.assertEqual(duration_score(261, 200), 0.0)

    def test_confirmed_vulnerability_closed_70s_off_no_longer_passes_hard_gate(self):
        """
        Pre-Phase-2: a candidate 70s off (within the old 90s hard ceiling)
        with a strong title+artist match could still be accepted, because
        duration_score() already zeroes the duration term at diff > 60s —
        the hard gate was 30s looser than that. Confirms it's now closed.
        """
        score, rejections = score_candidate(
            yt_title="Blinding Lights", actual_duration_sec=270,
            spotify_title="Blinding Lights", artist="The Weeknd",
            expected_duration_sec=200,  # 70s off
            uploader="The Weeknd",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("too far from expected" in r for r in rejections), rejections)


class TestSelectBestCandidateContract(unittest.TestCase):
    """select_best_candidate's widened (candidate, score, reason) return."""

    def test_returns_3tuple_with_none_candidate(self):
        result = select_best_candidate([], "x", "y", 200)
        self.assertEqual(len(result), 3)
        candidate_, score, reason = result
        self.assertIsNone(candidate_)
        self.assertEqual(score, 0.0)

    def test_returns_3tuple_with_accepted_candidate(self):
        cands = [candidate("Blinding Lights", 200, uploader="The Weeknd")]
        result = select_best_candidate(
            cands, "Blinding Lights", "The Weeknd", 200, min_score=0.40,
        )
        self.assertEqual(len(result), 3)
        candidate_, score, reason = result
        self.assertIsNotNone(candidate_)
        self.assertGreaterEqual(score, 0.40)


# ═══════════════════════════════════════════════════════════════════
# Case 14: search-stage selection in downloader_service.py
# ═══════════════════════════════════════════════════════════════════
#
# Requires importing DownloaderService, which pulls in config.py (needs
# FLASK_ENV=development to skip its production env-var gate) and pymongo
# (declared in requirements.txt; lazy-connects, no real Mongo needed for
# import). Guarded so the rest of this suite still runs standalone if
# this heavier import chain isn't available in a given environment.

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")

_DOWNLOADER_IMPORT_ERROR = None
try:
    from services.downloader_service import DownloaderService
except Exception as _e:  # pragma: no cover - environment dependent
    DownloaderService = None
    _DOWNLOADER_IMPORT_ERROR = _e


@unittest.skipIf(DownloaderService is None, f"downloader_service import unavailable: {_DOWNLOADER_IMPORT_ERROR}")
class TestSearchStageSelection_DownloaderLevel(unittest.TestCase):
    """
    Case 14: a later stage's materially-better candidate must win over an
    earlier stage's marginal one. No real search/download — _score_stage_
    candidates and _finalize_and_download are monkeypatched.
    """

    def setUp(self):
        self.svc = DownloaderService()
        self.finalize_calls = []

        def fake_finalize(candidate_, source_name, spotify_title, duration_ms,
                           progress_callback=None, output_dir=None, output_filename=None):
            self.finalize_calls.append((candidate_, source_name))
            return f"FAKE_DOWNLOADED::{candidate_['title']}"

        self.svc._finalize_and_download = fake_finalize

    def test_case14_later_stage_beats_earlier_marginal_candidate(self):
        stage_results = {
            "Stage 1 (Official)": (candidate("Weak Marginal Match", 200), 0.45, "marginal"),
            "Stage 2 (Audio)":     (candidate("Strong Confident Match", 200), 0.85, "confident"),
        }
        call_log = []

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None):
            call_log.append(source_name)
            cand, score, reason = stage_results.get(source_name, (None, 0.0, "no results"))
            return cand, score, reason

        self.svc._score_stage_candidates = fake_score

        result = self.svc._download_from_youtube(
            search_query="x", output_filename="out.mp3",
            duration_ms=200_000, spotify_title="Strong Confident Match", artist="Someone",
        )

        # Stage 2 hit the confident-accept bar (0.75) — it must win, and the
        # loop must stop there (stages 3/4/5 never queried).
        self.assertEqual(result, "FAKE_DOWNLOADED::Strong Confident Match")
        self.assertEqual(call_log, ["Stage 1 (Official)", "Stage 2 (Audio)"])
        self.assertEqual(len(self.finalize_calls), 1)
        self.assertEqual(self.finalize_calls[0][0]["title"], "Strong Confident Match")

    def test_case14b_best_of_multiple_marginal_candidates_wins_after_exhaustion(self):
        # No stage ever reaches the 0.75 confident bar — every stage is
        # queried, and the best (not the first, not the last) marginal
        # candidate is the one downloaded at the end.
        stage_results = {
            "Stage 1 (Official)": (candidate("First Marginal", 200), 0.42, "marginal"),
            "Stage 2 (Audio)":     (candidate("Weaker Marginal", 200), 0.41, "marginal, lower than held"),
            "Stage 3 (YT Music)":  (candidate("Best Marginal", 200), 0.60, "marginal, but the best seen"),
            "Stage 4 (Generic)":   (None, 0.0, "nothing usable"),
            "Stage 5 (SC)":        (None, 0.0, "nothing usable"),
        }

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None):
            return stage_results[source_name]

        self.svc._score_stage_candidates = fake_score

        result = self.svc._download_from_youtube(
            search_query="x", output_filename="out.mp3",
            duration_ms=200_000, spotify_title="Best Marginal", artist="Someone",
        )

        self.assertEqual(result, "FAKE_DOWNLOADED::Best Marginal")
        self.assertEqual(len(self.finalize_calls), 1)
        self.assertEqual(self.finalize_calls[0][0]["title"], "Best Marginal")

    def test_no_acceptable_candidate_across_all_stages_raises(self):
        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None):
            return None, 0.0, "nothing usable"

        self.svc._score_stage_candidates = fake_score

        with self.assertRaises(Exception):
            self.svc._download_from_youtube(
                search_query="x", output_filename="out.mp3",
                duration_ms=200_000, spotify_title="Anything", artist="Someone",
            )
        self.assertEqual(self.finalize_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
