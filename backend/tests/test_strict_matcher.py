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

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
            call_log.append(source_name)
            cand, score, reason = stage_results.get(source_name, (None, 0.0, "no results"))
            return cand, score, reason

        self.svc._score_stage_candidates = fake_score

        result = self.svc._download_from_youtube(
            search_query="x", output_filename="out.mp3",
            duration_ms=200_000, spotify_title="Strong Confident Match", artist="Someone",
        )

        # Stage 2 hit the confident-accept bar (0.75) — it must win, and the
        # loop must stop there (stages 3/4/5 never queried). The Topic and YT Music stages sit between 1 and 2.
        self.assertEqual(result, "FAKE_DOWNLOADED::Strong Confident Match")
        self.assertEqual(call_log, ["Stage 1 (Official)", "Stage 6 (Topic)", "Stage 7 (YT Music)", "Stage 2 (Audio)"])
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

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
            return stage_results[source_name]

        self.svc._score_stage_candidates = fake_score

        result = self.svc._download_from_youtube(
            search_query="x", output_filename="out.mp3",
            duration_ms=200_000, spotify_title="Best Marginal", artist="Someone",
        )

        self.assertEqual(result, "FAKE_DOWNLOADED::Best Marginal")
        self.assertEqual(len(self.finalize_calls), 1)
        self.assertEqual(self.finalize_calls[0][0]["title"], "Best Marginal")

    def test_confident_topic_hit_stops_the_search_before_the_audio_stage(self):
        stage_results = {"Stage 6 (Topic)": (candidate("Right Length Album Upload", 200), 0.9, "confident")}
        call_log = []

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
            call_log.append(source_name)
            return stage_results.get(source_name, (None, 0.0, "no results"))

        self.svc._score_stage_candidates = fake_score
        self.svc._verify_download = lambda *a, **k: None
        result = self.svc._download_from_youtube(
            search_query="x", output_filename="out.mp3",
            duration_ms=200_000, spotify_title="Right Length Album Upload", artist="Someone",
        )
        self.assertEqual(result, "FAKE_DOWNLOADED::Right Length Album Upload")
        self.assertEqual(call_log, ["Stage 1 (Official)", "Stage 6 (Topic)"])

    def test_wrong_length_download_is_discarded_and_next_candidate_is_tried(self):
        from services.downloader_service import VerificationRejected

        class _R:
            reason = "57s shorter than Spotify's 337s"

        bad, good = candidate("Short Film Edit", 280), candidate("Full Album Version", 337)
        bad["url"], good["url"] = "https://y/bad", "https://y/good"

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
            return (good if "https://y/bad" in (exclude_urls or set()) else bad), 0.9, "confident"

        def fake_verify(filename, actual_dir, spotify_title, artist, duration_ms, cand):
            if cand["url"] == "https://y/bad":
                raise VerificationRejected(cand["url"], _R())

        self.svc._score_stage_candidates = fake_score
        self.svc._verify_download = fake_verify
        result = self.svc._download_from_youtube(
            search_query="x", output_filename="out.mp3",
            duration_ms=337_000, spotify_title="Ek Ladki", artist="Someone",
        )
        self.assertEqual(result, "FAKE_DOWNLOADED::Full Album Version")

    def test_no_acceptable_candidate_across_all_stages_raises(self):
        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
            return None, 0.0, "nothing usable"

        self.svc._score_stage_candidates = fake_score

        with self.assertRaises(Exception):
            self.svc._download_from_youtube(
                search_query="x", output_filename="out.mp3",
                duration_ms=200_000, spotify_title="Anything", artist="Someone",
            )
        self.assertEqual(self.finalize_calls, [])


class TestYouTubeMusicStage(unittest.TestCase):
    def test_search_url_targets_the_songs_tab_and_quotes_the_query(self):
        from services.downloader_service import _ytmusic_songs_search_url, _YTMUSIC_SEARCH_PREFIX, _YTMUSIC_SONGS_FILTER
        url = _ytmusic_songs_search_url("I VM", "Games & Co")
        self.assertTrue(url.startswith(_YTMUSIC_SEARCH_PREFIX))
        self.assertIn("q=I%20VM%20Games%20%26%20Co", url)
        self.assertTrue(url.endswith("sp=" + _YTMUSIC_SONGS_FILTER))

    def test_flat_hits_are_enriched_with_length_artist_and_plain_youtube_url(self):
        from unittest import mock
        svc = DownloaderService()
        metas = {
            "aaa": {"duration": 283, "artist": "Sukhwinder Singh", "track": "Nach Le Nach Le", "title": "x"},
            "bbb": None,                                # unreadable -> dropped, never guessed
            "ccc": {"duration": 0, "artist": "Nobody"},  # no length -> dropped
        }

        class FakeYDL:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                vid = url.rsplit("=", 1)[1]
                if metas[vid] is None:
                    raise RuntimeError("unavailable")
                return metas[vid]

        flat = [{"id": "aaa", "title": "Nach Le Nach Le"}, {"id": "bbb", "title": "b"}, {"id": "ccc", "title": "c"}, None, {"title": "no id"}]
        with mock.patch("services.downloader_service.yt_dlp.YoutubeDL", FakeYDL):
            out = svc._enrich_ytmusic_entries(flat, "Stage 7")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["duration"], 283)
        self.assertEqual(out[0]["uploader"], "Sukhwinder Singh")
        self.assertEqual(out[0]["webpage_url"], "https://www.youtube.com/watch?v=aaa")

    def test_enriched_hit_with_exact_length_scores_as_a_confident_match(self):
        score, rejections = score_candidate("Nach Le Nach Le", 283, "Nach Le Nach Le", "Sukhwinder Singh", 283,
                                            uploader="Sukhwinder Singh")
        self.assertEqual(rejections, [])
        self.assertGreaterEqual(score, 0.75)


class TestLengthRejectionsDoNotUseTheFingerprintBudget(unittest.TestCase):
    """Review 2026-10-08: three wrong-length downloads used to abort a song before later stages ran."""

    def setUp(self):
        self.svc = DownloaderService()
        self.svc._finalize_and_download = lambda c, *a, **k: "FAKE::" + c["title"]

    def test_four_wrong_length_candidates_then_a_good_one_still_succeeds(self):
        from services.downloader_service import VerificationRejected

        class _Len:
            reason = "57s shorter than Spotify's"
            is_length = True

        urls = ["https://y/short%d" % i for i in range(4)]
        cands = {}
        for i, u in enumerate(urls):
            c = candidate("Short %d" % i, 280)
            c["url"] = u
            cands[u] = c
        good = candidate("Full Version", 337)
        good["url"] = "https://y/good"

        def fake_score(query, source_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
            for u in urls:
                if u not in (exclude_urls or set()):
                    return cands[u], 0.9, "confident"
            return good, 0.9, "confident"

        def fake_verify(filename, actual_dir, spotify_title, artist, duration_ms, cand):
            if cand["url"] in cands:
                raise VerificationRejected(cand["url"], _Len())

        self.svc._score_stage_candidates = fake_score
        self.svc._verify_download = fake_verify
        out = self.svc._download_from_youtube(search_query="x", output_filename="o.mp3", duration_ms=337_000,
                                              spotify_title="Ek Ladki", artist="Someone")
        self.assertEqual(out, "FAKE::Full Version")

    def test_real_verify_download_removes_the_short_file_and_raises(self):
        import tempfile
        from unittest import mock
        from services.downloader_service import VerificationRejected
        from services.pipeline_guards import Verdict
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.mp3")
            with open(p, "wb") as f:
                f.write(b"fake")
            bad = Verdict(False, False, "57s shorter than Spotify's 337s", 280.0, -57.0)
            with mock.patch("services.pipeline_guards.validate_download", return_value=bad):
                with self.assertRaises(VerificationRejected) as cm:
                    self.svc._verify_download("x.mp3", d, "Ek Ladki", "Someone", 337_000, {"title": "t", "url": "u"})
            self.assertTrue(getattr(cm.exception.result, "is_length", False))
            self.assertFalse(os.path.exists(p))

    def test_non_length_verdict_does_not_reject_on_length(self):
        import tempfile
        from unittest import mock
        from services.pipeline_guards import Verdict
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.mp3")
            with open(p, "wb") as f:
                f.write(b"fake")
            unreadable = Verdict(False, False, "not a readable mp3 (X)")      # diff_s == 0: not a length problem
            from services.audio_verifier import VerifyResult
            with mock.patch("services.pipeline_guards.validate_download", return_value=unreadable), \
                 mock.patch("services.audio_verifier.verify_recording", return_value=VerifyResult("inconclusive")) as fp:
                self.svc._verify_download("x.mp3", d, "T", "A", 200_000, {"title": "t", "url": "u"})   # must not raise
            fp.assert_called_once()                                           # it moved on to the fingerprint step
            self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestKaraokeChannelAndVariantGaps(unittest.TestCase):
    """Regression tests for the karaoke/instrumental leaks found on 2026-09-19.

    The old filter looked at the VIDEO TITLE only, with \bword\b matching, so:
      - a karaoke re-recording titled exactly like the original (artist + title
        copied verbatim, right duration) on a karaoke CHANNEL scored a perfect 1.0;
      - "Instrumentals" (plural) and misspellings like "Karoke" slipped through.
    """

    SP_TITLE = "Make Some Noise For The Desi Boyz"
    ARTIST = "Pritam"
    DUR = 244

    def test_unlabelled_karaoke_on_karaoke_channel_is_rejected(self):
        score, rejections = score_candidate(
            "Pritam - Make Some Noise For The Desi Boyz", 245, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Bollywood Karaoke Studio",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("channel" in r.lower() for r in rejections), rejections)

    def test_backing_track_channel_is_rejected(self):
        score, _ = score_candidate(
            "Make Some Noise For The Desi Boyz (Pritam)", 244, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Hindi Backing Tracks",
        )
        self.assertEqual(score, 0.0)

    def test_plural_instrumentals_rejected(self):
        score, rejections = score_candidate(
            "Make Some Noise For The Desi Boyz - Instrumentals", 244, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Pritam",
        )
        self.assertEqual(score, 0.0)
        self.assertTrue(any("forbidden keyword" in r for r in rejections), rejections)

    def test_misspelled_karoke_rejected(self):
        score, _ = score_candidate(
            "Make Some Noise For The Desi Boyz Karoke", 244, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Pritam",
        )
        self.assertEqual(score, 0.0)

    def test_without_vocals_and_vocals_removed_rejected(self):
        for suffix in ("(Without Vocals)", "(Vocals Removed)", "[Off Vocal]", "(Sing Along)"):
            score, _ = score_candidate(
                f"Make Some Noise For The Desi Boyz {suffix}", 244, self.SP_TITLE, self.ARTIST,
                self.DUR, uploader="Pritam",
            )
            self.assertEqual(score, 0.0, suffix)

    def test_official_topic_channel_still_accepted(self):
        score, rejections = score_candidate(
            "Make Some Noise For The Desi Boyz", 244, self.SP_TITLE, self.ARTIST,
            self.DUR, uploader="Pritam - Topic",
        )
        self.assertEqual(rejections, [])
        self.assertGreaterEqual(score, 0.90)

    def test_topic_channel_bonus_breaks_ties_in_favour_of_studio_audio(self):
        # Same title/duration; only the uploader differs. Use a duration off by a few
        # seconds so the raw score sits below the 1.0 clamp and the bonus is visible.
        common = dict(actual_duration_sec=249, spotify_title=self.SP_TITLE, artist=self.ARTIST,
                      expected_duration_sec=self.DUR)
        topic, _ = score_candidate("Make Some Noise For The Desi Boyz", uploader="Pritam - Topic", **common)
        plain, _ = score_candidate("Make Some Noise For The Desi Boyz", uploader="Pritam Fan Uploads", **common)
        self.assertGreater(topic, plain)

    def test_legit_track_that_is_itself_an_instrumental_is_still_allowed(self):
        # If the Spotify title asks for "Instrumentals", the exemption still applies.
        score, rejections = score_candidate(
            "Skyline (Instrumentals)", 200, "Skyline (Instrumentals)", "Some Artist",
            200, uploader="Some Artist",
        )
        self.assertNotIn("forbidden keyword", " ".join(rejections))

    def test_word_boundaries_still_avoid_false_positives(self):
        # "Discovery" / "Undercover" must not trip the 'cover' keyword or channel rule.
        score, rejections = score_candidate(
            "Discovery", 200, "Discovery", "Daft Punk", 200, uploader="Discovery Music Group",
        )
        self.assertEqual(rejections, [])
        self.assertGreater(score, 0.5)


class TestDurationLimitMatchesValidator(unittest.TestCase):
    """The search stage must never pick a candidate the post-download validator will refuse."""

    def test_limit_follows_validator_for_short_songs(self):
        from services.strict_matcher import hard_duration_limit
        from services.pipeline_guards import duration_tolerances
        self.assertEqual(hard_duration_limit(146), 30)                    # 30 s, not the old flat 60 s
        self.assertEqual(hard_duration_limit(146), duration_tolerances(146)[1])

    def test_limit_never_exceeds_old_flat_ceiling(self):
        from services.strict_matcher import hard_duration_limit
        self.assertEqual(hard_duration_limit(900), HARD_DURATION_LIMIT_SEC)       # 15% of 900 s = 135 s -> capped at 60

    def test_unknown_expected_duration_keeps_flat_ceiling(self):
        from services.strict_matcher import hard_duration_limit
        self.assertEqual(hard_duration_limit(None), HARD_DURATION_LIMIT_SEC)

    def test_candidate_31s_off_a_146s_song_is_rejected_before_download(self):
        # Real case from 2026-10-07 ("With You - Chloe"): passed the old 60 s gate, failed the validator.
        score, rejections = score_candidate("Chloe - With You", 177, "With You", "Chloe", 146, uploader="Chloe")
        self.assertEqual(score, 0.0)
        self.assertIn("too far", " ".join(rejections))

    def test_candidate_within_limit_still_accepted(self):
        score, rejections = score_candidate("Chloe - With You", 148, "With You", "Chloe", 146, uploader="Chloe")
        self.assertEqual(rejections, [])
        self.assertGreater(score, 0.5)
