"""
Focused tests for the mixed-path-separator fix in auto_downloader.py's
_download_single().

Background: every genre-routing branch except the plain Electronic
catch-all sets `final_folder` from a genre_router.py helper
(_library_path / map_genre_string / the Spotify-confidence path / the
Gemini fallback), all of which return "Library/..." forward-slash paths
by convention (confirmed in services/genre_router.py: _library_path()
docstring examples, map_genre_string() docstring, resolve_genre_folder_
with_confidence() docstring). `final_folder = os.path.join(BASE_DOWNLOAD_DIR,
folder_structure)` does NOT normalize the slash embedded inside
folder_structure on Windows, so final_folder ends up mixed-separator,
e.g. "C:\\Users\\...\\DJ music\\Library/Bollywood".

This is confirmed live: on 2026-08-25, `repair_index.py --dry-run` on the
real machine reported 9 tracks downloaded *that same evening* as
"orphaned" (no matching library_index entry found on disk), even though
each one had a genuine index write. Root cause: the mixed-separator
string stored as `final_path` never exact-string-matches the
all-backslash paths pathlib/os.walk produce when scanning the real
filesystem in reconcile_library_state.py / repair_index.py — those tools
compare paths as strings, not by resolving them. Windows itself tolerates
'/' in paths, so the actual download/move/index-write all succeed; only
downstream tooling that does exact string comparison is fooled.

Fix: normalize `final_folder` once, right after the routing if/else
converges and before it's used for os.makedirs/the move/the
library_index write — a single insertion point that covers every
routing branch instead of patching each one individually.

This file uses source-contract tests (inspecting the actual source text)
rather than driving _download_single end-to-end: that function is a
long, deeply-nested closure inside ingest_download() that downloads real
audio, hits real APIs, and moves real files — restructuring it into an
independently-callable/mockable unit is out of scope for this fix,
matching the same documented approach used for maintenance_worker.py's
retag_catchall fix in tests/test_retag_catchall_rate_limit.py.
"""
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_SOURCE_PATH = Path(_BACKEND_DIR) / "services" / "auto_downloader.py"


class DownloadPathNormalizationTestCase(unittest.TestCase):
    def setUp(self):
        self.source = _SOURCE_PATH.read_text(encoding="utf-8")
        # Isolate just the _download_single() closure body, so a match
        # elsewhere in the file can't produce a false pass.
        start = self.source.index("def _download_single")
        end = self.source.index("\ndef playlist_monitor")
        self.assertGreater(end, start, "could not isolate _download_single body")
        self.method_src = self.source[start:end]

    def test_normpath_applied_to_final_folder(self):
        self.assertIn(
            "final_folder = os.path.normpath(final_folder)", self.method_src,
            "final_folder must be normalized after genre routing decides it",
        )

    def test_normpath_precedes_makedirs(self):
        idx_norm = self.method_src.index("final_folder = os.path.normpath(final_folder)")
        idx_mkdir = self.method_src.index("os.makedirs(final_folder")
        self.assertLess(
            idx_norm, idx_mkdir,
            "normpath must run before the folder is created on disk",
        )

    def test_normpath_precedes_library_index_write(self):
        idx_norm = self.method_src.index("final_folder = os.path.normpath(final_folder)")
        idx_write = self.method_src.index("_idx(\n")
        self.assertLess(
            idx_norm, idx_write,
            "normpath must run before final_path is written to library_index "
            "— writing the mixed-separator string is the actual root cause",
        )

    def test_normpath_after_every_routing_branch(self):
        """The normpath call must come after ALL branches that can set
        final_folder, not just some of them — otherwise it only fixes a
        subset of routing paths."""
        branch_markers = [
            'final_folder = os.path.join(target_base, force_folder)',           # manual override
            '_mem_lib_path = _lib_p(_mem_genre)',                                # artist_memory
            'final_folder = os.path.join(BASE_DOWNLOAD_DIR, mapped)',           # MB genre
            'final_folder = os.path.join(BASE_DOWNLOAD_DIR, gemini_path)',      # Gemini fallback (both call sites)
            'final_folder = os.path.join(BASE_DOWNLOAD_DIR, folder_structure)', # Spotify confidence
            'final_folder = os.path.join(BASE_DOWNLOAD_DIR, "Library", "Electronic")',  # catch-all (both call sites)
        ]
        idx_norm = self.method_src.index("final_folder = os.path.normpath(final_folder)")
        for marker in branch_markers:
            self.assertIn(marker, self.method_src, f"expected routing branch not found: {marker!r}")
            last_occurrence = self.method_src.rindex(marker)
            self.assertLess(
                last_occurrence, idx_norm,
                f"normpath must come after every assignment, but a later "
                f"occurrence of {marker!r} was found after it",
            )

    def test_source_file_compiles(self):
        import py_compile
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True):
            py_compile.compile(str(_SOURCE_PATH), doraise=True)


if __name__ == "__main__":
    unittest.main()
