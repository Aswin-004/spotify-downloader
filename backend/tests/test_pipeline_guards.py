"""pipeline_guards: ingest lock, tiered duration validation, on-disk Spotify identity index. No network, temp dirs only."""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import pipeline_guards as pg  # noqa: E402


class TestIngestLock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "ingest.lock")

    def test_first_caller_owns_second_is_refused_and_lock_is_released(self):
        with pg.ingest_lock(self.path) as first:
            self.assertTrue(first)
            self.assertTrue(os.path.exists(self.path))
            with pg.ingest_lock(self.path) as second:      # same process, second run
                self.assertFalse(second)
        self.assertFalse(os.path.exists(self.path))
        with pg.ingest_lock(self.path) as again:
            self.assertTrue(again)

    def test_lock_is_released_when_the_run_crashes(self):
        with self.assertRaises(RuntimeError):
            with pg.ingest_lock(self.path) as got:
                self.assertTrue(got)
                raise RuntimeError("boom")
        with pg.ingest_lock(self.path) as again:
            self.assertTrue(again)

    def test_another_live_process_holds_the_lock(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with open(self.path, "w") as fh:
                fh.write(str(child.pid))
            with pg.ingest_lock(self.path) as got:
                self.assertFalse(got)
            self.assertIsNone(child.poll(), "checking the lock must never kill the other process")
        finally:
            child.kill()
            child.wait()

    def test_lock_of_a_dead_process_is_taken_over(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        with open(self.path, "w") as fh:
            fh.write(str(child.pid))
        with pg.ingest_lock(self.path) as got:
            self.assertTrue(got)

    def test_very_old_lock_is_taken_over_even_if_pid_looks_alive(self):
        with open(self.path, "w") as fh:
            fh.write(str(os.getpid()))
        old = time.time() - pg.STALE_LOCK_SECONDS - 60
        os.utime(self.path, (old, old))
        with pg.ingest_lock(self.path) as got:
            self.assertTrue(got)

    def test_pid_alive_never_terminates_the_process(self):
        self.assertTrue(pg._pid_alive(os.getpid()))
        self.assertFalse(pg._pid_alive(0))


class TestDurationTiers(unittest.TestCase):
    def test_tolerances_scale_with_song_length(self):
        self.assertEqual(pg.duration_tolerances(180), (8.0, 30.0))
        accept, reject = pg.duration_tolerances(600)     # 10-minute DJ track
        self.assertAlmostEqual(accept, 18.0)
        self.assertAlmostEqual(reject, 90.0)

    def _verdict(self, local, official):
        p = os.path.join(tempfile.mkdtemp(), "x.mp3")
        with open(p, "wb") as fh:
            fh.write(b"\0" * 200_000)
        from unittest import mock
        with mock.patch("mutagen.mp3.MP3") as m:
            m.return_value.info.length = local
            return pg.validate_download(p, official)

    def test_within_eight_seconds_is_accepted(self):
        v = self._verdict(186.0, 180.0)
        self.assertTrue(v.ok and not v.flagged)

    def test_between_eight_and_thirty_is_kept_but_flagged(self):
        v = self._verdict(200.0, 180.0)
        self.assertTrue(v.ok and v.flagged)

    def test_beyond_thirty_seconds_is_rejected_both_ways(self):
        self.assertFalse(self._verdict(140.0, 180.0).ok)       # truncated / clip
        self.assertFalse(self._verdict(250.0, 180.0).ok)       # wrong or extended version

    def test_a_52_minute_file_for_a_3_minute_song_is_rejected(self):
        self.assertFalse(self._verdict(3166.0, 200.0).ok)

    def test_no_official_duration_only_runs_file_checks(self):
        v = self._verdict(200.0, None)
        self.assertTrue(v.ok and not v.flagged)

    def test_tiny_or_missing_or_unreadable_files_are_rejected(self):
        self.assertFalse(pg.validate_download(os.path.join(tempfile.mkdtemp(), "nope.mp3"), 180).ok)
        p = os.path.join(tempfile.mkdtemp(), "tiny.mp3")
        open(p, "wb").write(b"x" * 500)
        self.assertFalse(pg.validate_download(p, 180).ok)
        p2 = os.path.join(tempfile.mkdtemp(), "junk.mp3")
        open(p2, "wb").write(b"not audio at all " * 20000)
        v = pg.validate_download(p2, 180)
        self.assertFalse(v.ok)
        self.assertIn("readable", v.reason)


class TestDiskIdentityIndex(unittest.TestCase):
    def _lib(self):
        root = tempfile.mkdtemp()
        for rel in ("Library/House/a.mp3", "Library/Bollywood/b.mp3", "Ingest/Staging/c.mp3", "Library/Punjabi/d.mp3"):
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(b"x")
        return root

    def test_finds_a_song_by_spotify_id_across_crates(self):
        ids = {"a.mp3": "SP_A", "b.mp3": "SP_B", "c.mp3": "SP_C", "d.mp3": ""}
        idx = pg.DiskIdentityIndex(tag_reader=lambda p: ids[os.path.basename(p)])
        root = self._lib()
        self.assertEqual(idx.scan(root), 2)                  # staging skipped, untagged skipped
        self.assertTrue(idx.find("SP_A").endswith("a.mp3"))
        self.assertTrue(idx.find("SP_B").endswith("b.mp3"))
        self.assertEqual(idx.find("SP_C"), "")               # Staging is never "the library"
        self.assertEqual(idx.find("nope"), "")
        self.assertEqual(idx.find(""), "")

    def test_a_deleted_file_is_no_longer_a_duplicate(self):
        idx = pg.DiskIdentityIndex(tag_reader=lambda p: "SP_A" if p.endswith("a.mp3") else "")
        root = self._lib()
        idx.scan(root)
        os.remove(os.path.join(root, "Library", "House", "a.mp3"))
        self.assertEqual(idx.find("SP_A"), "")
        idx.scan(root)
        self.assertEqual(idx.find("SP_A"), "")

    def test_rescan_reuses_cached_tags_for_unchanged_files(self):
        calls = []
        idx = pg.DiskIdentityIndex(tag_reader=lambda p: calls.append(p) or "X" + os.path.basename(p))
        root = self._lib()
        idx.scan(root)
        first = len(calls)
        idx.scan(root)
        self.assertEqual(len(calls), first)


if __name__ == "__main__":
    unittest.main()
