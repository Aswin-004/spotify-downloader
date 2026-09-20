"""
Phase 1E Issue 4 — MongoDB URI configuration safety.

backend/database.py used to read MONGODB_URI via a bare
os.getenv("MONGODB_URI", "mongodb://localhost:27017") at import time, with
no .env loading of its own — it relied on *something else* (config.py's own
load_dotenv(), or the calling process) having already populated the
environment. Any standalone script that imported database.py directly
without doing that first got no error: it silently pointed at an empty
local MongoDB instead of the real one. This was hit repeatedly during
Phase 1B/1C/1D library-repair work (see docs/PHASE_1E_RECOMMENDATION_HARDENING.md
Issue 4) and worked around each time by manually exporting the env var
before import — a workaround, not a fix.

Fix under test (database.py): the module now calls load_dotenv() itself,
keeps NO default for MONGODB_URI (None if genuinely absent), and _get_db()
raises MongoConfigurationError before ever constructing a MongoClient if
the value is missing/empty. Explicitly configuring
MONGODB_URI=mongodb://localhost:27017 still works — only the silent,
unrequested fallback is removed.

Every test reloads database.py under a controlled environment and NEVER
performs a real MongoDB connection (MongoClient is always mocked). setUp/
tearDown snapshot and restore both os.environ and the module's live
connection state so later test files in the same discovery run see the
same database.py they would have without this file ever running.
"""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import database  # noqa: E402


def _reload_with_env(env, clear=False, suppress_dotenv=False):
    """Reload database.py once under a patched os.environ and return it.

    The patch is only active for the duration of the reload — MONGODB_URI
    is a plain string resolved at import time, so the module's attribute
    keeps whatever value was resolved during that reload even after the
    patch is torn down when this function returns.

    suppress_dotenv=True additionally makes load_dotenv() a no-op during
    the reload, to simulate "no backend/.env available" even though this
    repo's real backend/.env exists with a real credential. This patches
    dotenv.load_dotenv (the source function), not database.load_dotenv:
    importlib.reload() re-executes database.py's own
    `from dotenv import load_dotenv` line, which silently REBINDS
    database.load_dotenv back to the real function — a patch made on the
    database-module name does not survive a reload of that module.
    Patching the dotenv package's own attribute does survive, because the
    re-executed import statement reads it fresh at reload time.
    """
    if suppress_dotenv:
        with patch("dotenv.load_dotenv", lambda *a, **k: False):
            with patch.dict(os.environ, env, clear=clear):
                return importlib.reload(database)
    with patch.dict(os.environ, env, clear=clear):
        return importlib.reload(database)


class MongoUriConfigurationTestCase(unittest.TestCase):

    def setUp(self):
        self._orig_environ = dict(os.environ)
        self._orig_client = database._client
        self._orig_db = database._db
        self._orig_initialized = database._initialized

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_environ)
        importlib.reload(database)  # restore MONGODB_URI/MONGODB_DB from the real env
        database._client = self._orig_client
        database._db = self._orig_db
        database._initialized = self._orig_initialized

    # -- explicit environment variable -----------------------------------

    def test_explicit_environment_uri_works(self):
        mod = _reload_with_env({
            "MONGODB_URI": "mongodb+srv://user:pass@test-cluster.example/test",
            "MONGODB_DB": "spotify_downloader",
        })
        self.assertEqual(
            mod.MONGODB_URI, "mongodb+srv://user:pass@test-cluster.example/test")
        self.assertEqual(mod.MONGODB_DB, "spotify_downloader")

    def test_existing_valid_configuration_remains_compatible(self):
        """The pattern already used by test_index_recovery.py,
        test_gemini_service.py, and test_strict_matcher.py — pre-setting
        MONGODB_URI before importing database.py — must keep working
        unchanged."""
        mod = _reload_with_env({"MONGODB_URI": "mongodb://localhost:27017/test"})
        self.assertEqual(mod.MONGODB_URI, "mongodb://localhost:27017/test")

    # -- .env-file configuration -------------------------------------------

    def test_dotenv_configuration_works(self):
        """With MONGODB_URI absent from the pre-existing process
        environment, database.py's own load_dotenv() call must still
        resolve it from backend/.env, matching config.py's existing
        convention. The real value is never asserted or printed here —
        only that *some* non-localhost value was found — so no credential
        from backend/.env ever appears in test output."""
        env = {k: v for k, v in os.environ.items() if k != "MONGODB_URI"}
        mod = _reload_with_env(env, clear=True)
        self.assertTrue(
            mod.MONGODB_URI,
            "backend/.env exists in this repo with a real MONGODB_URI — "
            "database.py's own load_dotenv() call must find it even when "
            "the process environment doesn't already have the variable set",
        )
        self.assertNotEqual(mod.MONGODB_URI, "mongodb://localhost:27017")

    # -- missing configuration must fail loudly, never localhost ---------

    def test_missing_uri_fails_clearly_not_localhost(self):
        env = {k: v for k, v in os.environ.items() if k != "MONGODB_URI"}
        # Also hide any real backend/.env value so this test exercises a
        # genuinely unconfigured environment, not "found via .env".
        mod = _reload_with_env(env, clear=True, suppress_dotenv=True)
        self.assertIsNone(mod.MONGODB_URI)
        mod._client = None
        mod._db = None
        mod._initialized = False
        with patch.object(mod, "MongoClient") as mock_client:
            with self.assertRaises(mod.MongoConfigurationError):
                mod._get_db()
            mock_client.assert_not_called()

    def test_empty_string_uri_treated_as_missing(self):
        mod = _reload_with_env({"MONGODB_URI": ""}, suppress_dotenv=True)
        mod._client = None
        mod._db = None
        mod._initialized = False
        with patch.object(mod, "MongoClient") as mock_client:
            with self.assertRaises(mod.MongoConfigurationError):
                mod._get_db()
            mock_client.assert_not_called()

    def test_missing_uri_error_message_does_not_mention_localhost_as_target(self):
        """The failure must read as a configuration error, not a connection
        error — the historical bug was that a missing URI produced no error
        at all, just a silent, misleading connection attempt."""
        env = {k: v for k, v in os.environ.items() if k != "MONGODB_URI"}
        mod = _reload_with_env(env, clear=True, suppress_dotenv=True)
        mod._client = None
        mod._db = None
        mod._initialized = False
        with patch.object(mod, "MongoClient"):
            with self.assertRaises(mod.MongoConfigurationError) as ctx:
                mod._get_db()
        self.assertIn("MONGODB_URI", str(ctx.exception))

    # -- explicit localhost is a deliberate choice, still honored ---------

    def test_explicit_localhost_is_still_honored(self):
        mod = _reload_with_env({"MONGODB_URI": "mongodb://localhost:27017", "MONGODB_DB": "test"})
        mod._client = None
        mod._db = None
        mod._initialized = False
        with patch.object(mod, "MongoClient") as mock_client, \
             patch.object(mod, "_ensure_indexes"):
            mock_client.return_value = MagicMock()
            mod._get_db()
            mock_client.assert_called_once()
            args, _kwargs = mock_client.call_args
            self.assertEqual(args[0], "mongodb://localhost:27017")

    def test_source_file_compiles(self):
        import py_compile
        py_compile.compile(str(Path(_BACKEND_DIR) / "database.py"), doraise=True)


if __name__ == "__main__":
    unittest.main()
