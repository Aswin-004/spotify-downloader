"""
Tests for services/stream_download.py — the selection + audio-verification loop behind the
browser Download button. Everything external (YouTube search, yt-dlp, AcoustID) is injected.
"""
import os
import tempfile
import unittest

from services.audio_verifier import VerifyResult, should_reject
from services.stream_download import (
    AllCandidatesRejected, NoConfidentMatch, SearchFailed, StreamDownloadError,
    download_verified_audio,
)

BAD = VerifyResult("suspect_version", 0.97, "Song (Karaoke Version)", ["Band"], "karaoke")
WRONG_SONG = VerifyResult("mismatch", 0.97, "Other Song", ["Other"], "different song")
GOOD = VerifyResult("verified", 0.98, "Song", ["Artist"], "matches")
UNKNOWN = VerifyResult("inconclusive", 0.0, reason="no AcoustID match")


class Env:
    """Fake world: candidates in ranking order, a verdict per video id, and bookkeeping."""

    def __init__(self, order, verdicts, score=0.95):
        self.order = order
        self.verdicts = verdicts
        self.score = score
        self.queries = []
        self.exclusions = []
        self.tmp_dirs = []
        self.fetch_error = None

    def score_stage(self, query, label, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
        self.queries.append(query)
        excl = set(exclude_urls or ())
        self.exclusions.append(excl)
        for vid in self.order:
            url = f"https://y/{vid}"
            if url not in excl:
                return {"title": vid, "url": url, "uploader": "Chan", "entry": {"id": vid}}, self.score, "ok"
        return None, 0.0, "nothing left"

    def fetch_audio(self, video_id):
        if self.fetch_error:
            raise self.fetch_error
        d = tempfile.mkdtemp()
        self.tmp_dirs.append(d)
        path = os.path.join(d, "track.mp3")
        with open(path, "wb") as fh:
            fh.write(video_id.encode() + b"\x00" + b"\x01" * 512)
        return d, path

    def verify(self, path, title, artist, expected_secs):
        with open(path, "rb") as fh:
            vid = fh.read().split(b"\x00")[0].decode()
        return self.verdicts[vid]

    def run(self, **kw):
        return download_verified_audio(
            title="Song", artist="Artist", duration_ms=244000,
            score_stage=self.score_stage, fetch_audio=self.fetch_audio,
            verify=self.verify, should_reject_fn=should_reject, **kw,
        )

    def assert_no_temp_left(self, tc):
        for d in self.tmp_dirs:
            tc.assertFalse(os.path.exists(d), f"temp dir leaked: {d}")


class TestVerifiedSelection(unittest.TestCase):
    def test_good_first_candidate_is_delivered_untouched(self):
        env = Env(["a"], {"a": GOOD})
        out = env.run()
        self.assertTrue(out.audio_bytes.startswith(b"a\x00"))
        self.assertEqual(out.verdict.status, "verified")
        env.assert_no_temp_left(self)

    def test_karaoke_first_then_real_track_is_delivered(self):
        env = Env(["karaoke", "real"], {"karaoke": BAD, "real": GOOD})
        out = env.run()
        self.assertTrue(out.audio_bytes.startswith(b"real\x00"), "delivered the KARAOKE audio")
        self.assertIn("https://y/karaoke", env.exclusions[-1], "rejected URL was not excluded on retry")
        env.assert_no_temp_left(self)

    def test_wrong_song_verdict_is_also_rejected(self):
        env = Env(["other", "real"], {"other": WRONG_SONG, "real": GOOD})
        self.assertTrue(env.run().audio_bytes.startswith(b"real\x00"))

    def test_inconclusive_is_delivered_not_blocked(self):
        env = Env(["a"], {"a": UNKNOWN})
        out = env.run()
        self.assertEqual(out.verdict.status, "inconclusive")
        env.assert_no_temp_left(self)

    def test_stops_searching_at_the_first_confident_stage(self):
        env = Env(["a"], {"a": GOOD}, score=0.9)
        env.run()
        self.assertEqual(len(env.queries), 1, "searched stage 2 even though stage 1 was confident")

    def test_marginal_score_tries_both_stages(self):
        env = Env(["a"], {"a": GOOD}, score=0.5)
        env.run()
        self.assertEqual(len(env.queries), 2)


class TestFailures(unittest.TestCase):
    def test_all_candidates_rejected_raises_422_and_leaks_nothing(self):
        ids = ["k1", "k2", "k3", "k4"]
        env = Env(ids, {i: BAD for i in ids})
        with self.assertRaises(AllCandidatesRejected) as ctx:
            env.run()
        self.assertEqual(ctx.exception.http_status, 422)
        self.assertIn("karaoke", ctx.exception.reason.lower())
        env.assert_no_temp_left(self)

    def test_running_out_of_candidates_after_a_rejection_is_422_not_404(self):
        env = Env(["only"], {"only": BAD})
        with self.assertRaises(AllCandidatesRejected):
            env.run()

    def test_nothing_clears_the_matcher_is_404(self):
        env = Env([], {})
        with self.assertRaises(NoConfidentMatch) as ctx:
            env.run()
        self.assertEqual(ctx.exception.http_status, 404)

    def test_search_outage_is_502(self):
        def boom(*a, **k):
            raise OSError("network down")
        with self.assertRaises(SearchFailed) as ctx:
            download_verified_audio(title="Song", artist="Artist", duration_ms=1, score_stage=boom,
                                    fetch_audio=lambda v: ("", ""), verify=lambda *a: GOOD,
                                    should_reject_fn=should_reject)
        self.assertEqual(ctx.exception.http_status, 502)

    def test_candidate_without_video_id_is_an_error(self):
        def score(*a, **k):
            return {"title": "x", "url": "u", "entry": {}}, 0.9, "ok"
        with self.assertRaises(StreamDownloadError):
            download_verified_audio(title="Song", artist="Artist", duration_ms=1, score_stage=score,
                                    fetch_audio=lambda v: ("", ""), verify=lambda *a: GOOD,
                                    should_reject_fn=should_reject)

    def test_download_failure_propagates_for_the_route_to_map_to_502(self):
        env = Env(["a"], {"a": GOOD})
        env.fetch_error = RuntimeError("yt-dlp exploded")
        with self.assertRaises(RuntimeError):
            env.run()


if __name__ == "__main__":
    unittest.main()
