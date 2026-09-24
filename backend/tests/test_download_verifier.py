"""
Tests for services/download_verifier.py — a download recorded as done but with no file gets retried, and
nothing else does.

The two things that must NEVER happen are re-downloading a song the user moved or deliberately deleted, and
a mass re-download caused by a mistake (drive not connected). Both have tests of their own.
"""
import os
import tempfile
import unittest

from services import download_verifier as dv


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = os.path.join(self.tmp.name, "DJ music")
        os.makedirs(os.path.join(self.lib, "Library", "House"))
        os.makedirs(os.path.join(self.lib, "Library", "Punjabi"))
        self.path = os.path.join(self.tmp.name, "verify.json")
        self.requeued = []
        self.t0 = 1_000_000.0

    def make(self, folder, name):
        with open(os.path.join(self.lib, "Library", folder, name), "wb") as fh:
            fh.write(b"x")

    def requeue(self, entries, reason=""):
        self.requeued.extend(entries)
        return {"queued": len(entries)}

    def record(self, tid="t1", title="Song", artist="Artist", filename="Song - Artist.mp3"):
        dv.record_done(tid, title, artist, filename, now=self.t0, path=self.path)

    def run_pass(self, dt=dv.MIN_AGE_SECONDS + 1, **kw):
        return dv.process_pending(self.lib, self.requeue, now=self.t0 + dt, path=self.path, **kw)


class TestTiming(Base):
    def test_not_checked_before_it_is_old_enough(self):
        self.record()
        s = self.run_pass(dt=60)
        self.assertEqual((s["ok"], s["requeued"], s["waiting"]), ([], [], 1))
        self.assertEqual(self.requeued, [])

    def test_entry_survives_a_restart(self):
        self.record()
        self.assertEqual(self.run_pass(dt=10)["waiting"], 1)        # a fresh process reads the same file
        self.assertEqual(self.run_pass(dt=10)["waiting"], 1)


class TestFileIsFound(Base):
    def test_present_file_is_fine_and_forgotten(self):
        self.make("House", "Song - Artist.mp3")
        self.record()
        s = self.run_pass()
        self.assertEqual(s["ok"], ["t1"])
        self.assertEqual(self.requeued, [])
        self.assertEqual(self.run_pass()["waiting"], 0)              # no longer pending

    def test_a_song_the_user_moved_to_another_folder_is_still_found(self):
        self.record()
        self.make("Punjabi", "Song - Artist.mp3")                   # moved by hand, different crate
        self.assertEqual(self.run_pass()["ok"], ["t1"])
        self.assertEqual(self.requeued, [])

    def test_collision_suffix_and_case_do_not_matter(self):
        self.make("House", "SONG - artist_2.MP3")
        self.record()
        self.assertEqual(self.run_pass()["ok"], ["t1"])


class TestMissingFile(Base):
    def test_missing_file_is_requeued_with_the_spotify_id(self):
        self.make("House", "Something Else.mp3")
        self.record()
        s = self.run_pass()
        self.assertEqual(s["requeued"], ["t1"])
        self.assertEqual(self.requeued, [{"spotify_id": "t1", "title": "Song", "artist": "Artist"}])

    def test_retried_at_most_twice_then_reported(self):
        self.make("House", "Something Else.mp3")
        for _ in range(dv.MAX_ATTEMPTS):
            self.record()
            self.assertEqual(self.run_pass()["requeued"], ["t1"])
        self.record()                                                # the third download also vanished
        s = self.run_pass()
        self.assertEqual((s["requeued"], s["gave_up"]), ([], ["t1"]))
        self.assertEqual(len(self.requeued), dv.MAX_ATTEMPTS)
        self.assertEqual([g["id"] for g in dv.gave_up(self.path)], ["t1"])

    def test_success_resets_the_retry_count(self):
        self.make("House", "Something Else.mp3")
        self.record()
        self.run_pass()                                              # attempt 1
        self.make("House", "Song - Artist.mp3")                     # the retry worked
        self.record()
        self.assertEqual(self.run_pass()["ok"], ["t1"])
        os.remove(os.path.join(self.lib, "Library", "House", "Song - Artist.mp3"))
        self.record()
        self.assertEqual(self.run_pass()["requeued"], ["t1"])        # counted from zero again, not given up

    def test_a_failing_requeue_loses_nothing(self):
        self.make("House", "Something Else.mp3")
        self.record()

        def boom(entries, reason=""):
            raise RuntimeError("database down")

        s = dv.process_pending(self.lib, boom, now=self.t0 + dv.MIN_AGE_SECONDS + 1, path=self.path)
        self.assertEqual(s["requeued"], [])
        self.assertEqual(self.run_pass()["requeued"], ["t1"])        # still pending, retried next pass


class TestSafetyLimits(Base):
    def test_an_empty_library_requeues_nothing(self):
        """Drive not connected / wrong path: every file looks missing, and that must not become a mass re-download."""
        for i in range(5):
            self.record(tid=f"t{i}", filename=f"S{i}.mp3")
        s = self.run_pass()                                          # the library has no mp3 at all
        self.assertEqual((s["requeued"], self.requeued), ([], []))
        self.assertEqual(s["waiting"], 5)                            # kept, looked at again later

    def test_one_pass_requeues_at_most_the_cap(self):
        self.make("House", "Unrelated.mp3")
        for i in range(dv.MAX_REQUEUE_PER_PASS + 5):
            self.record(tid=f"t{i}", filename=f"S{i}.mp3")
        s = self.run_pass()
        self.assertEqual(len(s["requeued"]), dv.MAX_REQUEUE_PER_PASS)
        again = self.run_pass()                                      # the rest are looked at on the next pass
        self.assertEqual(len(again["requeued"]), 5)

    def test_recording_the_same_song_twice_keeps_one_entry(self):
        self.record()
        self.record()
        self.make("House", "Unrelated.mp3")
        self.assertEqual(len(self.run_pass()["requeued"]), 1)

    def test_blank_id_or_filename_is_ignored(self):
        dv.record_done("", "T", "A", "x.mp3", now=self.t0, path=self.path)
        dv.record_done("t1", "T", "A", "", now=self.t0, path=self.path)
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
