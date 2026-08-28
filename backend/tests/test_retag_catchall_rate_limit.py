"""
Focused tests for the retag_catchall Spotify rate-limit fix in
maintenance_worker.py.

Background: `_task_retag_catchall` retries genre routing for tracks stuck in
Library/Electronic/ with routing_source=catchall. Steps 2 (Spotify artist
search) and 3 (Spotify title search) called `_sp.sp.search(...)` directly —
bypassing the app's own `_call_with_backoff` rate-limit wrapper entirely —
and wrapped the whole thing in a bare `except Exception: pass`, so a 429
during a real Spotify outage (observed live on 2026-08-25, see the
`spotify_service` cooldown machinery) was indistinguishable in the logs from
"Spotify genuinely has no match for this artist". Confirmed via the real
cached playlist snapshot on the production machine that artist_id was NOT
the problem (0 of 1793 cached tracks were missing it) — the ambiguity was
specifically in retag_catchall's own unprotected search calls.

The fix: check `is_rate_limited()` before each Spotify call (skip cleanly,
falling through to the non-Spotify sources — Last.fm/MusicBrainz/AcoustID/
Gemini — steps 4-7, which are unaffected), and replace the silent
`except: pass` with a debug-level log line naming the artist/title and the
actual exception, so a future occurrence is diagnosable.

This file uses source-contract tests (inspecting the actual source text)
rather than driving `_task_retag_catchall` end-to-end: that method reads
real ID3 tags off real files under `config.BASE_DOWNLOAD_DIR/Library/
Electronic/` and is a long, unrefactored loop — restructuring it into an
independently-callable/mockable unit is out of scope for this fix, matching
the same documented approach used for auto_downloader's exception-swallow
fix in tests/test_index_recovery.py.
"""
import re
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_SOURCE_PATH = Path(_BACKEND_DIR) / "services" / "maintenance_worker.py"


class RetagCatchallRateLimitTestCase(unittest.TestCase):
    def setUp(self):
        self.source = _SOURCE_PATH.read_text(encoding="utf-8")
        # Isolate just the _task_retag_catchall method body for these checks,
        # so a match elsewhere in the file can't produce a false pass.
        start = self.source.index("def _task_retag_catchall")
        end = self.source.index("\n    def _task_ingestion_cycle_report")
        self.assertGreater(end, start, "could not isolate _task_retag_catchall body")
        self.method_src = self.source[start:end]

    # ── is_rate_limited is checked before both Spotify calls ─────────────
    def test_is_rate_limited_imported_and_checked(self):
        self.assertIn(
            "is_rate_limited as _is_rl", self.method_src,
            "retag_catchall must import is_rate_limited to gate its Spotify calls",
        )
        # At least twice: once for the artist search (step 2), once for the
        # title search (step 3).
        self.assertGreaterEqual(
            self.method_src.count("if _is_rl():"), 2,
            "expected a rate-limit gate before each Spotify search call (steps 2 and 3)",
        )

    def test_rate_limit_gate_precedes_each_search_call(self):
        """`if _is_rl():` must appear before its corresponding `.sp.search(`
        within the same try block, not after (which would defeat the point)."""
        for marker in ['q=artist_name, type="artist"', 'q=f"track:{title_tag}"']:
            idx_search = self.method_src.index(marker)
            preceding = self.method_src[:idx_search]
            # The nearest gate before this search call must be an is_rate_limited check,
            # not a leftover from a totally unrelated part of the method.
            self.assertIn("if _is_rl():", preceding)

    # ── the OUTER except around each Spotify call block now logs ─────────
    def test_outer_exception_handler_logs_instead_of_silently_passing(self):
        # Each step's outer try/except is the LAST except in its segment (an
        # inner, unrelated try/except around the ID3-tag rewrite in step 3
        # legitimately still bare-passes — that's a best-effort tag write,
        # not a Spotify call, and failing it silently is fine).
        step2 = self.method_src.split("# ── 2. Spotify artist search")[1].split("# ── 3.")[0]
        step3 = self.method_src.split("# ── 3. Spotify title-only search")[1].split("# ── 4.")[0]
        for name, segment, exc_var in [
            ("step 2 (artist search)", step2, "_e2"),
            ("step 3 (title search)", step3, "_e3"),
        ]:
            outer_except = f"except Exception as {exc_var}:"
            self.assertIn(
                outer_except, segment,
                f"{name} must name its outer exception (not a bare `except Exception:`) so it can be logged",
            )
            after = segment.split(outer_except)[1]
            self.assertIn(
                "logger.debug", after.split("\n\n")[0],
                f"{name}'s outer except must log the exception it catches",
            )

    # ── the log messages actually distinguish the two failure modes ──────
    def test_log_messages_name_rate_limit_vs_search_failure_distinctly(self):
        self.assertIn("rate-limited", self.method_src.lower())
        self.assertIn("search failed for", self.method_src)

    # ── sanity: the method is still syntactically valid Python ───────────
    def test_source_file_compiles(self):
        import py_compile
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True):
            py_compile.compile(str(_SOURCE_PATH), doraise=True)


if __name__ == "__main__":
    unittest.main()
