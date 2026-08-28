"""
Focused test for the missing socketTimeoutMS fix in database.py's _get_db().

Background: the MongoClient() call set serverSelectionTimeoutMS and
connectTimeoutMS (both bound how long it takes to *establish* a
connection) but never socketTimeoutMS — the setting that bounds how long
an already-open socket may block on an individual read/write. PyMongo's
own default for socketTimeoutMS is None (unbounded).

This client is a single module-level singleton (`_client`/`_db` in this
file) shared by the entire app, including maintenance_worker.py, whose
_run_loop() runs its 9 registered tasks in one single-threaded
`for task in self._tasks` loop with no per-task timeout. If any single
Mongo query blocks forever on a stalled socket, that whole loop freezes
silently — no exception is ever raised, so nothing is ever logged, and
every other maintenance task (dead_letter_report, stale_staging, etc.)
stops running behind it too.

Observed live: on the real deployment, the `reconcile` maintenance task
(the heaviest task — several full-collection Mongo scans plus per-file
ID3 reads across ~1990 files, against an Atlas M0 free-tier cluster
already flagged elsewhere in this file's own comment as needing generous
connect timeouts for cold-starts) stopped updating its report and every
other periodic maintenance log line went silent for over 20 hours with
zero errors logged anywhere — consistent with exactly this failure mode.

Fix: add socketTimeoutMS=45000 to the one MongoClient() call. This test
mocks pymongo.MongoClient directly (a small, clean surface — no need for
the source-contract-test pattern used elsewhere in this suite for methods
that are hard to isolate) and asserts _get_db() actually passes it.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import database  # noqa: E402


class MongoSocketTimeoutTestCase(unittest.TestCase):
    def setUp(self):
        # Reset the module-level singleton so _get_db() re-creates the client.
        self._orig_client = database._client
        self._orig_db = database._db
        self._orig_initialized = database._initialized
        database._client = None
        database._db = None
        database._initialized = False

    def tearDown(self):
        database._client = self._orig_client
        database._db = self._orig_db
        database._initialized = self._orig_initialized

    @patch("database._ensure_indexes")
    @patch("database.MongoClient")
    def test_get_db_sets_socket_timeout(self, mock_mongo_client, mock_ensure_indexes):
        mock_mongo_client.return_value = MagicMock()

        database._get_db()

        self.assertTrue(mock_mongo_client.called, "MongoClient must be constructed")
        _, kwargs = mock_mongo_client.call_args
        self.assertIn(
            "socketTimeoutMS", kwargs,
            "MongoClient must set socketTimeoutMS — without it, a stalled "
            "socket can block the single-threaded maintenance loop forever",
        )
        self.assertGreater(
            kwargs["socketTimeoutMS"], 0,
            "socketTimeoutMS must be a positive, bounded value, not 0/None/unset",
        )

    @patch("database._ensure_indexes")
    @patch("database.MongoClient")
    def test_connection_timeouts_still_present(self, mock_mongo_client, mock_ensure_indexes):
        """Guard against the fix accidentally dropping the existing,
        already-working connection-establishment timeouts."""
        mock_mongo_client.return_value = MagicMock()

        database._get_db()

        _, kwargs = mock_mongo_client.call_args
        self.assertIn("serverSelectionTimeoutMS", kwargs)
        self.assertIn("connectTimeoutMS", kwargs)

    def test_source_file_compiles(self):
        import py_compile
        import tempfile
        source_path = Path(_BACKEND_DIR) / "database.py"
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True):
            py_compile.compile(str(source_path), doraise=True)


if __name__ == "__main__":
    unittest.main()
