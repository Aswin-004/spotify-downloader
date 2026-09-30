"""Batch-organise must be asked for on purpose; deletes must be recoverable. Flask test client, temp dirs, no network."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask  # noqa: E402

from routes import library as lib  # noqa: E402


def _client():
    app = Flask(__name__)
    app.register_blueprint(lib.library_bp)
    return app.test_client()


class TestOrganizeNeedsExplicitConfirmation(unittest.TestCase):
    def test_no_body_is_refused_and_moves_nothing(self):
        with mock.patch("services.organizer_service.organize_library") as org:
            r = _client().post("/api/library/organize", json={})
        self.assertEqual(r.status_code, 400)
        org.assert_not_called()

    def test_a_mode_alone_is_not_enough(self):
        with mock.patch("services.organizer_service.organize_library") as org:
            r = _client().post("/api/library/organize", json={"mode": "artist"})
        self.assertEqual(r.status_code, 400)
        org.assert_not_called()

    def test_organize_recent_is_guarded_too(self):
        with mock.patch("services.organizer_service.organize_recent") as org:
            r = _client().post("/api/library/organize-recent", json={"mode": "artist"})
        self.assertEqual(r.status_code, 400)
        org.assert_not_called()

    def test_explicit_confirmation_and_mode_still_works(self):
        with mock.patch("services.organizer_service.organize_library", return_value={"moved": 0}) as org:
            r = _client().post("/api/library/organize", json={"mode": "genre", "confirm": "MOVE-MY-LIBRARY"})
        self.assertEqual(r.status_code, 200)
        org.assert_called_once_with(mode="genre")

    def test_confirmed_but_no_mode_is_rejected_not_defaulted_to_artist(self):
        with mock.patch("services.organizer_service.organize_library") as org:
            r = _client().post("/api/library/organize", json={"confirm": "MOVE-MY-LIBRARY"})
        self.assertEqual(r.status_code, 400)
        org.assert_not_called()


class TestDeletesAreRecoverable(unittest.TestCase):
    def test_duplicate_delete_goes_through_recycle_not_unlink(self):
        base = tempfile.mkdtemp()
        dup_dir = os.path.join(base, "NeedsReview", "Duplicates")
        os.makedirs(dup_dir)
        target = os.path.join(dup_dir, "song.mp3")
        open(target, "wb").write(b"x")
        with mock.patch.object(lib, "BASE_DOWNLOAD_DIR", base), \
                mock.patch("services.recycle.remove_file", return_value="recycle-bin") as rm:
            r = _client().post("/api/duplicates/delete", json={"filename": "song.mp3"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["recoverable"], "recycle-bin")
        rm.assert_called_once()
        self.assertTrue(os.path.exists(target), "the route must not unlink the file itself")

    def test_duplicate_delete_reports_failure_if_it_cannot_be_recovered(self):
        base = tempfile.mkdtemp()
        dup_dir = os.path.join(base, "NeedsReview", "Duplicates")
        os.makedirs(dup_dir)
        open(os.path.join(dup_dir, "song.mp3"), "wb").write(b"x")
        with mock.patch.object(lib, "BASE_DOWNLOAD_DIR", base), \
                mock.patch("services.recycle.remove_file", return_value=None):
            r = _client().post("/api/duplicates/delete", json={"filename": "song.mp3"})
        self.assertEqual(r.status_code, 500)

    def test_downloader_delete_is_recoverable(self):
        from services import downloader_service as ds
        base = tempfile.mkdtemp()
        open(os.path.join(base, "a.mp3"), "wb").write(b"x")
        svc = ds.DownloaderService.__new__(ds.DownloaderService)
        svc.download_dir = base
        with mock.patch("services.recycle.remove_file", return_value="recycle-bin") as rm:
            out = svc.delete_download("a.mp3")
        self.assertTrue(out["success"])
        rm.assert_called_once()
        self.assertTrue(os.path.exists(os.path.join(base, "a.mp3")))


if __name__ == "__main__":
    unittest.main()
