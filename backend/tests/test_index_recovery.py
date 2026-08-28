"""
Focused tests for the P0 index-write hardening added to auto_downloader.py.

Scope: services.auto_downloader._write_index_recovery_manifest() and
_process_index_queue() — the two new, independently-callable functions that
make a failed library_index write (download succeeded, index write did not)
observable and recoverable, without overloading the existing move-failure
retry queue (which is proven, in the P0 report, to be structurally unable to
represent this failure shape — it discards any manifest whose staged_path no
longer exists, and that is *always* true for an index-write failure).

Test-scope note (why some cases are source-contract checks, not live calls):
the actual diagnostic log line and the "don't count this as a download
failure" decision live inline inside `_download_single()`, a closure nested
inside `ingest_download()` that captures many outer-scope variables
(saved_ids, failure_counts, target_base, a live Spotify client, etc.).
Exercising it directly would require mocking the entire ingest pipeline
(Spotify OAuth, playlist fetch, yt-dlp, tagging) — far outside "smallest
necessary change" for a P0 fix, and `_download_single` was deliberately left
unrefactored to keep the diff minimal. Where a case concerns that inline
code specifically (C's exact log fields, D's no-false-failure behavior),
this file asserts the property against the actual source text of the
except block instead of re-implementing a parallel pipeline mock.

Cases (per the P0 task spec):
  A. index_track succeeds
  B. index_track fails with a known database exception
  C. failure produces diagnostic logging
  D. download success is not falsely reported as download failure
  E. repeated recovery/index attempt is idempotent
  F. already-indexed track does not create duplicate index state
  G. final file remains untouched if indexing fails

No real downloads, no real MongoDB connection, no production data touched.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")

try:
    import services.auto_downloader as ad
    _IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover - environment-dependent
    ad = None
    _IMPORT_ERROR = _e


@unittest.skipIf(ad is None, f"services.auto_downloader not importable: {_IMPORT_ERROR}")
class IndexRecoveryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._queue_dir = Path(self._tmpdir.name) / ".index_queue"
        # Point the module at an isolated queue dir for every test — never
        # touch the real Ingest/.index_queue/ directory.
        self._patch_queue_dir = patch.object(ad, "INDEX_QUEUE_DIR", self._queue_dir)
        self._patch_queue_dir.start()

    def tearDown(self):
        self._patch_queue_dir.stop()
        self._tmpdir.cleanup()

    def _write_manifest(self, name="123_spotify1.json", **overrides):
        self._queue_dir.mkdir(exist_ok=True)
        data = {
            "identity_key": "sp:spotify1",
            "spotify_id": "spotify1",
            "title": "Some Track",
            "artist": "Some Artist",
            "filename": "Some Track - Some Artist.mp3",
            "final_path": overrides.pop("final_path", str(Path(self._tmpdir.name) / "final.mp3")),
            "genre_folder": "Library/Electronic",
            "genre_confidence": 0.0,
            "duration_ms": 200000,
            "attempt_count": 0,
            "timestamp": "2026-08-24T10:00:00",
        }
        data.update(overrides)
        (self._queue_dir / name).write_text(json.dumps(data, indent=2))
        return data

    def _touch_final_file(self, path=None, content=b"fake mp3 bytes"):
        path = path or (Path(self._tmpdir.name) / "final.mp3")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    # ── A. index_track succeeds ──────────────────────────────────────────
    def test_A_successful_replay_removes_manifest(self):
        self._touch_final_file()
        self._write_manifest()
        with patch("database.index_track", return_value=True) as mock_idx, \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()
        mock_idx.assert_called_once()
        self.assertEqual(list(self._queue_dir.glob("*.json")), [],
                          "manifest must be removed after a successful replay")

    # ── B. index_track fails with a known database exception ────────────
    def test_B_duplicate_key_error_keeps_manifest_and_increments_attempt(self):
        self._touch_final_file()
        self._write_manifest()

        class FakeDuplicateKeyError(Exception):
            pass

        with patch("database.index_track", side_effect=FakeDuplicateKeyError("E11000 duplicate key")), \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()

        remaining = list(self._queue_dir.glob("*.json"))
        self.assertEqual(len(remaining), 1, "manifest must survive a failed replay")
        data = json.loads(remaining[0].read_text())
        self.assertEqual(data["attempt_count"], 1)

    def test_B_server_selection_timeout_also_retried_not_dropped(self):
        self._touch_final_file()
        self._write_manifest()

        class FakeServerSelectionTimeoutError(Exception):
            pass

        with patch("database.index_track", side_effect=FakeServerSelectionTimeoutError("timed out")), \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()

        remaining = list(self._queue_dir.glob("*.json"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(json.loads(remaining[0].read_text())["attempt_count"], 1)

    def test_B_manifest_dead_lettered_after_max_attempts(self):
        self._touch_final_file()
        self._write_manifest(attempt_count=ad.MAX_INDEX_RETRY_ATTEMPTS)
        with patch("database.index_track", side_effect=RuntimeError("still broken")), \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()
        self.assertEqual(list(self._queue_dir.glob("*.json")), [],
                          "exhausted manifest must leave the live queue")
        dead = list((self._queue_dir / "dead").glob("*.json"))
        self.assertEqual(len(dead), 1, "exhausted manifest must land in dead/, not be deleted")

    # ── C. failure produces diagnostic logging ───────────────────────────
    def test_C_manifest_carries_full_diagnostic_context(self):
        """The manifest itself is the persisted diagnostic record for a
        failed write — assert every field STEP 3 requires is present and
        correctly populated (except type/message live in the live log line,
        checked below via source inspection, not in the manifest)."""
        final_path = str(Path(self._tmpdir.name) / "diag.mp3")
        ad._write_index_recovery_manifest(
            identity_key="sp:xyz",
            spotify_id="xyz",
            title="Diag Title",
            artist="Diag Artist",
            filename="diag.mp3",
            final_path=final_path,
            genre_folder="Library/Punjabi",
            genre_confidence=0.0,
            duration_ms=180000,
        )
        files = list(self._queue_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        data = json.loads(files[0].read_text())
        for field in ("identity_key", "spotify_id", "title", "artist", "filename",
                      "final_path", "genre_folder", "genre_confidence",
                      "duration_ms", "attempt_count", "timestamp"):
            self.assertIn(field, data)
        self.assertEqual(data["identity_key"], "sp:xyz")
        self.assertEqual(data["spotify_id"], "xyz")
        self.assertEqual(data["final_path"], final_path)
        self.assertEqual(data["attempt_count"], 0)

    def test_C_live_except_block_logs_required_fields(self):
        """Source-contract check (see module docstring): the actual except
        block inline in _download_single must reference every field STEP 3
        asked for, and must not be the old bare warning line."""
        src = Path(ad.__file__).read_text()
        idx = src.index("except Exception as _idx_err:")
        block = src[idx: idx + 1200]
        self.assertIn("type(_idx_err).__name__", block, "must log exception TYPE")
        self.assertIn("_idx_err}", block, "must log exception message")
        self.assertIn("title=", block)
        self.assertIn("artist=", block)
        self.assertIn("spotify_id=", block)
        self.assertIn("identity_key=", block)
        self.assertIn("final_path=", block)
        self.assertIn("genre_folder=", block)
        self.assertIn("logger.error", block, "a data-integrity failure must not stay at warning level")
        self.assertIn("_write_index_recovery_manifest(", block)
        self.assertNotIn('logger.warning(f"[ingest] library_index write failed: {_idx_err}")', src,
                          "old generic one-line handler must be gone, not just supplemented")

    # ── D. download success is not falsely reported as download failure ──
    def test_D_index_failure_does_not_increment_fail_count_or_record_failure(self):
        """Source-contract check: the except block must not call
        fail_count[0] += 1 or _record_failure(...) — the download itself
        succeeded (file is on disk at final_path); only the index write
        failed, and that must stay a distinct, non-fatal outcome."""
        src = Path(ad.__file__).read_text()
        idx = src.index("except Exception as _idx_err:")
        # Bound the block to just this except clause (next top-level except/def)
        end = src.index("\n                _routing_label", idx)
        block = src[idx:end]
        self.assertNotIn("fail_count[0] += 1", block)
        self.assertNotIn("_record_failure(", block)
        self.assertNotIn('"download_error"', block)

    # ── E. repeated recovery/index attempt is idempotent ─────────────────
    def test_E_second_replay_after_success_is_a_no_op(self):
        self._touch_final_file()
        self._write_manifest()
        with patch("database.index_track", return_value=True) as mock_idx, \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()  # first replay: succeeds, manifest removed
            ad._process_index_queue()  # second replay: nothing left to do
        self.assertEqual(mock_idx.call_count, 1, "index_track must not be called again once resolved")

    def test_E_crash_before_cleanup_is_safe_on_next_replay(self):
        """Simulates a process crash between a successful index_track() call
        and manifest deletion: is_indexed() now reports True even though the
        manifest is still on disk. The next replay must not call index_track
        again — it just cleans up."""
        self._touch_final_file()
        self._write_manifest()
        with patch("database.index_track") as mock_idx, \
             patch("database.is_indexed", return_value=True):
            ad._process_index_queue()
        mock_idx.assert_not_called()
        self.assertEqual(list(self._queue_dir.glob("*.json")), [])

    # ── F. already-indexed track does not create duplicate index state ───
    def test_F_already_indexed_skips_write_entirely(self):
        self._touch_final_file()
        self._write_manifest()
        with patch("database.index_track") as mock_idx, \
             patch("database.is_indexed", return_value=True) as mock_is_indexed:
            ad._process_index_queue()
        mock_is_indexed.assert_called_once_with("sp:spotify1")
        mock_idx.assert_not_called()
        self.assertEqual(list(self._queue_dir.glob("*.json")), [])

    # ── G. final file remains untouched if indexing fails ────────────────
    def test_G_final_file_unchanged_when_indexing_keeps_failing(self):
        final_path = self._touch_final_file(content=b"original bytes, do not touch")
        before_mtime = final_path.stat().st_mtime_ns
        before_bytes = final_path.read_bytes()
        self._write_manifest(final_path=str(final_path))

        with patch("database.index_track", side_effect=RuntimeError("still broken")), \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()

        self.assertTrue(final_path.is_file(), "final file must still exist")
        self.assertEqual(final_path.read_bytes(), before_bytes, "final file content must be untouched")
        self.assertEqual(final_path.stat().st_mtime_ns, before_mtime, "final file mtime must be untouched")

    def test_G_manifest_discarded_without_writing_if_final_file_gone(self):
        """If the final file no longer exists, there is nothing safe to
        index — the manifest is discarded and index_track is never called
        (never invents an index entry for a file that isn't there)."""
        missing_path = str(Path(self._tmpdir.name) / "never_existed.mp3")
        self._write_manifest(final_path=missing_path)
        with patch("database.index_track") as mock_idx, \
             patch("database.is_indexed", return_value=False):
            ad._process_index_queue()
        mock_idx.assert_not_called()
        self.assertEqual(list(self._queue_dir.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
