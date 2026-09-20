"""
Tests for services/audio_verifier.py — the AcoustID-based "is this file really the
requested recording?" check that catches karaoke / instrumental / wrong-song audio
which title+duration matching cannot see.

The decision logic (`evaluate_lookup`) is pure, so most tests feed it canned AcoustID
`results` payloads. The I/O wrapper (`verify_recording`) is tested with the
fingerprinter and the network mocked out.
"""
import os
import tempfile
import unittest
from unittest import mock

from services import audio_verifier as av
from services.audio_verifier import (
    VerifyResult, evaluate_lookup, should_reject, verify_recording, _is_comparable, _split_artists,
)

TITLE = "Make Some Noise For The Desi Boyz"
ARTIST = "Pritam"


def _res(score, title, artists, extra_recordings=None):
    recs = [{"id": "rec1", "title": title, "artists": [{"name": a} for a in artists]}]
    recs.extend(extra_recordings or [])
    return {"id": "acoustid-1", "score": score, "recordings": recs}


class TestVerified(unittest.TestCase):
    def test_exact_recording_is_verified(self):
        r = evaluate_lookup([_res(0.98, TITLE, ["Pritam", "KK"])], TITLE, ARTIST)
        self.assertEqual(r.status, "verified")
        self.assertGreaterEqual(r.confidence, 0.9)

    def test_spotify_style_suffix_and_multi_artist_still_verifies(self):
        r = evaluate_lookup([_res(0.96, TITLE, ["KK", "Bob"])],
                            'Make Some Noise For The Desi Boyz (From "Desi Boyz")', "Pritam, KK, Bob")
        self.assertEqual(r.status, "verified")

    def test_clean_recording_wins_over_a_variant_recording_of_the_same_audio(self):
        # AcoustID often links one fingerprint to several recordings (album version,
        # radio edit, ...). A clean match anywhere means the audio is the real song.
        r = evaluate_lookup([_res(0.97, TITLE + " (Live)", ["Pritam"],
                                  extra_recordings=[{"id": "r2", "title": TITLE, "artists": [{"name": "Pritam"}]}])],
                            TITLE, ARTIST)
        self.assertEqual(r.status, "verified")

    def test_karaoke_is_fine_when_the_request_itself_is_karaoke(self):
        r = evaluate_lookup([_res(0.97, TITLE + " (Karaoke Version)", ["Pritam"])],
                            TITLE + " (Karaoke Version)", ARTIST)
        self.assertEqual(r.status, "verified")

    def test_score_between_min_and_strong_is_not_verified(self):
        # 0.85 could be an official instrumental of the same master — cannot confirm.
        r = evaluate_lookup([_res(0.85, TITLE, ["Pritam"])], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")


class TestSuspectVersion(unittest.TestCase):
    def test_karaoke_recording_title(self):
        r = evaluate_lookup([_res(0.95, TITLE + " (Karaoke Version)", ["Some Band"])], TITLE, ARTIST)
        self.assertEqual(r.status, "suspect_version")
        self.assertIn("karaoke", r.reason.lower())

    def test_karaoke_act_as_recording_artist(self):
        r = evaluate_lookup([_res(0.95, TITLE, ["Bollywood Karaoke Band"])], TITLE, ARTIST)
        self.assertEqual(r.status, "suspect_version")

    def test_instrumentals_plural(self):
        r = evaluate_lookup([_res(0.95, TITLE + " (Instrumentals)", ["Pritam"])], TITLE, ARTIST)
        self.assertEqual(r.status, "suspect_version")

    def test_live_version_when_not_requested(self):
        r = evaluate_lookup([_res(0.95, TITLE + " (Live)", ["Pritam"])], TITLE, ARTIST)
        self.assertEqual(r.status, "suspect_version")

    def test_low_score_variant_is_not_acted_on(self):
        r = evaluate_lookup([_res(0.82, TITLE + " (Karaoke Version)", ["Some Band"])], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")


class TestMismatch(unittest.TestCase):
    def test_confidently_a_different_song(self):
        r = evaluate_lookup([_res(0.97, "Blinding Lights", ["The Weeknd"])], TITLE, ARTIST)
        self.assertEqual(r.status, "mismatch")

    def test_never_mismatch_for_non_latin_match(self):
        # A correct fingerprint matched against a Devanagari MusicBrainz entry looks like
        # a "totally different song" to a Latin comparison — must stay inconclusive.
        r = evaluate_lookup([_res(0.97, "मेक सम नॉइज़ फॉर द देसी बॉयज़", ["प्रीतम"])], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")

    def test_partial_similarity_is_inconclusive(self):
        r = evaluate_lookup([_res(0.95, "Make Some Noise", ["Beastie Boys"])], TITLE, ARTIST)
        self.assertNotEqual(r.status, "verified")
        self.assertFalse(r.is_bad, r)

    def test_low_score_different_song_is_not_a_mismatch(self):
        r = evaluate_lookup([_res(0.83, "Blinding Lights", ["The Weeknd"])], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")


class TestNoEvidence(unittest.TestCase):
    def test_no_results_is_inconclusive_not_bad(self):
        r = evaluate_lookup([], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")
        self.assertFalse(r.is_bad)

    def test_result_without_recordings(self):
        r = evaluate_lookup([{"id": "x", "score": 0.99}], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")

    def test_below_min_score(self):
        r = evaluate_lookup([_res(0.5, TITLE, ["Pritam"])], TITLE, ARTIST)
        self.assertEqual(r.status, "inconclusive")


class TestPolicy(unittest.TestCase):
    def test_only_enforce_mode_rejects_only_bad_verdicts(self):
        bad = VerifyResult("suspect_version", 0.95)
        good = VerifyResult("verified", 0.97)
        unknown = VerifyResult("inconclusive")
        self.assertTrue(should_reject(bad, "enforce"))
        self.assertFalse(should_reject(bad, "flag"))
        self.assertFalse(should_reject(bad, "off"))
        self.assertFalse(should_reject(good, "enforce"))
        self.assertFalse(should_reject(unknown, "enforce"))

    def test_mode_parsing(self):
        with mock.patch.dict(os.environ, {"AUDIO_VERIFY_MODE": "FLAG"}):
            self.assertEqual(av.get_mode(), "flag")
        with mock.patch.dict(os.environ, {"AUDIO_VERIFY_MODE": "nonsense"}):
            self.assertEqual(av.get_mode(), "enforce")
        with mock.patch.dict(os.environ, {"AUDIO_VERIFY_MODE": ""}):
            self.assertEqual(av.get_mode(), "enforce")


class TestHelpers(unittest.TestCase):
    def test_script_comparability(self):
        self.assertTrue(_is_comparable("Blinding Lights"))
        self.assertTrue(_is_comparable("Tiësto"))          # accented Latin is still comparable
        self.assertTrue(_is_comparable("10xx"))
        self.assertFalse(_is_comparable("प्रीतम"))
        self.assertFalse(_is_comparable("周杰伦"))

    def test_artist_splitting(self):
        self.assertEqual(_split_artists("Pritam, KK, Bob"), ["pritam", "kk", "bob"])
        self.assertEqual(_split_artists("Diplo & Marshmello"), ["diplo", "marshmello"])
        self.assertEqual(_split_artists("Artist feat. Someone"), ["artist", "someone"])


class TestVerifyRecordingIO(unittest.TestCase):
    """The wrapper must NEVER raise and must degrade to `inconclusive` on any failure."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        self.tmp.write(b"\x00" * 2048)
        self.tmp.close()
        self.addCleanup(lambda: os.path.exists(self.tmp.name) and os.remove(self.tmp.name))

    def _run(self, *, key="k", fp=("FP", 244.0), lookup=None, lookup_exc=None, mode="enforce"):
        lookup_patch = (mock.patch.object(av, "_acoustid_lookup_recordings", side_effect=lookup_exc)
                        if lookup_exc else
                        mock.patch.object(av, "_acoustid_lookup_recordings", return_value=lookup or []))
        with mock.patch.dict(os.environ, {"AUDIO_VERIFY_MODE": mode}), \
             mock.patch("services.musicbrainz_service._acoustid_key", return_value=key), \
             mock.patch("services.fingerprint_service._run_fpcalc", return_value=fp), \
             lookup_patch:
            return verify_recording(self.tmp.name, TITLE, ARTIST, 244)

    def test_verified_end_to_end(self):
        r = self._run(lookup=[_res(0.98, TITLE, ["Pritam"])])
        self.assertEqual(r.status, "verified")

    def test_karaoke_end_to_end(self):
        r = self._run(lookup=[_res(0.96, TITLE + " (Karaoke)", ["Band"])])
        self.assertEqual(r.status, "suspect_version")

    def test_mode_off_short_circuits(self):
        self.assertEqual(self._run(mode="off", lookup=[_res(0.98, "x", ["y"])]).status, "inconclusive")

    def test_missing_key(self):
        self.assertEqual(self._run(key="").status, "inconclusive")

    def test_fingerprint_failure(self):
        self.assertEqual(self._run(fp=("", 0.0)).status, "inconclusive")

    def test_network_failure_never_raises(self):
        r = self._run(lookup_exc=OSError("timeout"))
        self.assertEqual(r.status, "inconclusive")
        self.assertIn("lookup failed", r.reason)

    def test_missing_file(self):
        with mock.patch("services.musicbrainz_service._acoustid_key", return_value="k"):
            self.assertEqual(verify_recording("C:/does/not/exist.mp3", TITLE, ARTIST).status, "inconclusive")


if __name__ == "__main__":
    unittest.main()
