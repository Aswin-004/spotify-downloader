"""
Tests for services/recycle.py — recoverable removal (Recycle Bin / quarantine).

The Windows test really sends a throw-away file from a temp dir to the Recycle Bin: it is the
only way to know the ctypes shell call works on this machine, and it is the exact code path the
cleanup tool relies on. It touches nothing but a file the test itself creates.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import recycle


class TestQuarantine(unittest.TestCase):
    def test_moves_file_keeping_relative_path_and_never_deletes(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root, "Library", "House", "song.mp3")
            f.parent.mkdir(parents=True)
            f.write_bytes(b"data")
            dest = recycle.quarantine(str(f), root, stamp="T1")
            self.assertFalse(f.exists())
            self.assertTrue(Path(dest).is_file())
            self.assertEqual(Path(dest).read_bytes(), b"data")
            self.assertEqual(Path(dest), Path(root, "_TO_DELETE", "T1", "Library", "House", "song.mp3").resolve())

    def test_never_overwrites_an_earlier_quarantined_file(self):
        with tempfile.TemporaryDirectory() as root:
            dests = []
            for content in (b"first", b"second"):
                f = Path(root, "a", "song.mp3")
                f.parent.mkdir(exist_ok=True)
                f.write_bytes(content)
                dests.append(recycle.quarantine(str(f), root, stamp="SAME"))
            self.assertNotEqual(dests[0], dests[1])
            self.assertEqual(Path(dests[0]).read_bytes(), b"first")
            self.assertEqual(Path(dests[1]).read_bytes(), b"second")

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(recycle.quarantine(os.path.join(root, "nope.mp3"), root))


class TestRemoveFile(unittest.TestCase):
    def test_quarantine_mode_never_touches_the_recycle_bin(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(recycle, "send_to_recycle_bin") as sb:
            f = Path(root, "x.mp3"); f.write_bytes(b"1")
            out = recycle.remove_file(str(f), root, mode="quarantine")
            sb.assert_not_called()
            self.assertTrue(Path(out).is_file())

    def test_recycle_mode_falls_back_to_quarantine_when_recycle_fails(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(recycle, "send_to_recycle_bin", return_value=False):
            f = Path(root, "x.mp3"); f.write_bytes(b"1")
            out = recycle.remove_file(str(f), root, mode="recycle")
            self.assertNotEqual(out, "recycle-bin")
            self.assertTrue(Path(out).is_file(), "file was lost instead of quarantined")

    def test_recycle_mode_reports_recycle_bin_on_success(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(recycle, "send_to_recycle_bin", return_value=True):
            f = Path(root, "x.mp3"); f.write_bytes(b"1")
            self.assertEqual(recycle.remove_file(str(f), root, mode="recycle"), "recycle-bin")

    def test_there_is_no_permanent_delete_api(self):
        for name in dir(recycle):
            self.assertNotIn("permanent", name.lower())
        src = Path(recycle.__file__).read_text(encoding="utf-8")
        self.assertNotIn("os.remove", src)
        self.assertNotIn("os.unlink", src)


@unittest.skipUnless(sys.platform == "win32", "Recycle Bin is Windows-only")
class TestRealRecycleBin(unittest.TestCase):
    def test_file_really_goes_to_the_recycle_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "recycle_bin_selftest_delete_me.mp3")
            f.write_bytes(b"\x00" * 1024)
            self.assertTrue(recycle.send_to_recycle_bin(str(f)))
            self.assertFalse(f.exists())

    def test_missing_file_is_false_not_an_exception(self):
        self.assertFalse(recycle.send_to_recycle_bin(r"C:\definitely\not\here.mp3"))


if __name__ == "__main__":
    unittest.main()
