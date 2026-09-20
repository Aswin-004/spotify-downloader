"""
Integration tests for the audio-verification hook in DownloaderService._download_from_youtube:
a candidate whose DOWNLOADED audio is proven wrong (karaoke / other song) must be discarded
and the NEXT-best candidate tried — never kept, and never picked again.

Network, yt-dlp and the AcoustID lookup are all mocked; only the control flow is under test.
"""
import os
import tempfile
import threading
import unittest
from unittest import mock

from services import downloader_service as ds
from services.audio_verifier import VerifyResult


def _service(tmpdir):
    svc = ds.DownloaderService.__new__(ds.DownloaderService)   # skip __init__ (touches the real library dir)
    svc._tls = threading.local()
    svc.download_dir = tmpdir
    return svc


def _cand(url, uploader="Chan"):
    return {"title": f"title-{url}", "url": url, "uploader": uploader, "duration": 244}


class _Harness:
    """Fakes: scorer picks candidates in order (skipping excluded URLs); finalize writes a
    file whose CONTENT names the candidate; verify decides from that content."""

    def __init__(self, tmpdir, order, verdicts):
        self.tmp = tmpdir
        self.order = order                       # candidate URLs, best first
        self.verdicts = verdicts                 # url -> VerifyResult
        self.exclude_history = []
        self.svc = _service(tmpdir)
        self.svc._score_stage_candidates = self._score
        self.svc._finalize_and_download = self._finalize

    def _score(self, query, stage_name, duration_ms=None, spotify_title=None, artist=None, exclude_urls=None):
        excl = set(exclude_urls or ())
        self.exclude_history.append(excl)
        for url in self.order:
            if url not in excl:
                return _cand(url), 0.95, "ok"
        return None, 0.0, "nothing left"

    def _finalize(self, candidate, source_name, spotify_title, duration_ms,
                  progress_callback=None, output_dir=None, output_filename=None):
        name = f"{output_filename or 'song'}.mp3"
        with open(os.path.join(output_dir, name), "wb") as fh:
            fh.write(candidate["url"].encode() + b"\x00" * 2048)
        return name

    def _verify(self, filepath, title, artist, dur=None):
        with open(filepath, "rb") as fh:
            url = fh.read().split(b"\x00")[0].decode()
        return self.verdicts[url]

    def run(self):
        with mock.patch("services.audio_verifier.verify_recording", side_effect=self._verify):
            return self.svc._download_from_youtube(
                "q", "Song - Artist", output_dir=self.tmp, duration_ms=244000,
                spotify_title="Song", artist="Artist",
            )


BAD = VerifyResult("suspect_version", 0.97, "Song (Karaoke Version)", ["Band"], "karaoke")
GOOD = VerifyResult("verified", 0.98, "Song", ["Artist"], "matches")
UNKNOWN = VerifyResult("inconclusive", 0.0, reason="no AcoustID match")


class TestRejectAndRetry(unittest.TestCase):
    def test_bad_first_candidate_is_discarded_and_next_best_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _Harness(tmp, ["u_bad", "u_good"], {"u_bad": BAD, "u_good": GOOD})
            name = h.run()
            self.assertEqual(name, "Song - Artist.mp3")
            with open(os.path.join(tmp, name), "rb") as fh:
                self.assertTrue(fh.read().startswith(b"u_good"), "kept the WRONG candidate's audio")
            self.assertEqual(os.listdir(tmp), ["Song - Artist.mp3"], "rejected file was not deleted")
            self.assertEqual(h.svc._last_audio_verification["status"], "verified")
            # the rejected URL must be excluded when the scorer is called again
            self.assertIn("u_bad", h.exclude_history[-1])

    def test_gives_up_after_three_rejections_and_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            urls = ["u1", "u2", "u3", "u4"]
            h = _Harness(tmp, urls, {u: BAD for u in urls})
            with self.assertRaises(Exception) as ctx:
                h.run()
            self.assertIn("failed audio verification", str(ctx.exception))
            self.assertEqual(os.listdir(tmp), [], "a rejected file was left on disk")

    def test_inconclusive_verdict_keeps_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _Harness(tmp, ["u1"], {"u1": UNKNOWN})
            name = h.run()
            self.assertTrue(os.path.isfile(os.path.join(tmp, name)))
            self.assertEqual(h.svc._last_audio_verification["status"], "inconclusive")

    def test_flag_mode_records_but_never_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _Harness(tmp, ["u_bad"], {"u_bad": BAD})
            with mock.patch.dict(os.environ, {"AUDIO_VERIFY_MODE": "flag"}):
                name = h.run()
            self.assertTrue(os.path.isfile(os.path.join(tmp, name)))
            self.assertEqual(h.svc._last_audio_verification["status"], "suspect_version")

    def test_off_mode_skips_the_check_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _Harness(tmp, ["u_bad"], {"u_bad": BAD})
            with mock.patch.dict(os.environ, {"AUDIO_VERIFY_MODE": "off"}), \
                 mock.patch("services.audio_verifier.verify_recording",
                            return_value=VerifyResult("inconclusive", reason="disabled")):
                name = h.svc._download_from_youtube("q", "Song - Artist", output_dir=tmp,
                                                    duration_ms=244000, spotify_title="Song", artist="Artist")
            self.assertTrue(os.path.isfile(os.path.join(tmp, name)))

    def test_a_crashing_verifier_never_blocks_a_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _Harness(tmp, ["u1"], {})
            with mock.patch("services.audio_verifier.verify_recording", side_effect=RuntimeError("boom")):
                name = h.svc._download_from_youtube("q", "Song - Artist", output_dir=tmp,
                                                    duration_ms=244000, spotify_title="Song", artist="Artist")
            self.assertTrue(os.path.isfile(os.path.join(tmp, name)))


class TestQualityReportField(unittest.TestCase):
    def test_report_carries_the_verdict(self):
        rep = ds._build_quality_report(audio_verification={"status": "verified", "confidence": 0.98})
        self.assertEqual(rep["audio_verification"]["status"], "verified")

    def test_report_field_defaults_to_none(self):
        self.assertIsNone(ds._build_quality_report()["audio_verification"])


class TestScorerExclusion(unittest.TestCase):
    def test_excluded_urls_are_not_scored(self):
        svc = _service("unused")
        entries = {"entries": [
            {"title": "Song", "url": "https://y/1", "webpage_url": "https://y/1", "duration": 244, "uploader": "Artist"},
            {"title": "Song", "url": "https://y/2", "webpage_url": "https://y/2", "duration": 244, "uploader": "Artist - Topic"},
        ]}
        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = entries
        with mock.patch.object(ds.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            cand, score, _ = svc._score_stage_candidates(
                "q", "Stage 1", duration_ms=244000, spotify_title="Song", artist="Artist",
                exclude_urls={"https://y/1", "https://y/2"},
            )
        self.assertIsNone(cand, "every candidate was excluded — none should be scored")


if __name__ == "__main__":
    unittest.main()
