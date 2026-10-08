"""Lag fixes: /api/artwork caching + any-description covers, /api/files folder-signature cache, analytics TTL cache."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask
from mutagen.id3 import ID3, APIC

from routes import library as lib


def _client():
    app = Flask(__name__)
    app.register_blueprint(lib.library_bp)
    return app.test_client()


class TestArtwork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self.tmp.name)
        patcher = mock.patch.object(lib, "BASE_DOWNLOAD_DIR", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _mp3(self, name, pic_desc=None):
        p = os.path.join(self.base, name)
        open(p, "wb").close()
        tags = ID3()
        if pic_desc is not None:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc=pic_desc, data=b"\xff\xd8jpeg"))
        tags.save(p)
        return name

    def test_missing_art_is_404_but_browser_may_cache_it(self):
        r = _client().get("/api/artwork", query_string={"path": self._mp3("a.mp3")})
        self.assertEqual(r.status_code, 404)
        self.assertIn("max-age=3600", r.headers.get("Cache-Control", ""))

    def test_cover_is_found_whatever_its_description(self):
        for i, desc in enumerate(("", "Cover", "front", "Cover (front)")):
            r = _client().get("/api/artwork", query_string={"path": self._mp3(f"b{i}.mp3", desc)})
            self.assertEqual(r.status_code, 200, desc)
            self.assertEqual(r.data, b"\xff\xd8jpeg")

    def test_path_outside_the_library_is_refused_even_when_the_file_exists(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = os.path.join(outside.name, "secret.mp3")
        open(target, "wb").close()
        tags = ID3()
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"SECRET"))
        tags.save(target)
        r = _client().get("/api/artwork", query_string={"path": target})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b"SECRET", r.data)

    def test_unreadable_file_is_not_cached_as_having_no_art(self):
        open(os.path.join(self.base, "locked.mp3"), "wb").close()
        with mock.patch("mutagen.id3.ID3", side_effect=PermissionError("locked")):
            r = _client().get("/api/artwork", query_string={"path": "locked.mp3"})
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("max-age", r.headers.get("Cache-Control", ""))

    def test_file_with_no_tag_header_is_cacheable_no_art(self):
        with open(os.path.join(self.base, "plain.mp3"), "wb") as f:
            f.write(b"not an mp3 at all")
        r = _client().get("/api/artwork", query_string={"path": "plain.mp3"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("max-age=3600", r.headers.get("Cache-Control", ""))


class TestFilesCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        os.makedirs(os.path.join(self.base, "House"))
        for patch in (mock.patch.object(lib, "BASE_DOWNLOAD_DIR", self.base),
                      mock.patch.dict(lib._FILES_CACHE, {"sig": None, "at": 0.0, "files": []})):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def _touch(self, rel):
        p = os.path.join(self.base, rel)
        open(p, "wb").close()
        os.utime(os.path.dirname(p), None)       # make the folder mtime move even on coarse clocks
        return p

    def test_second_call_does_not_rescan_when_nothing_changed(self):
        self._touch("House/a.mp3")
        with mock.patch.object(lib, "_scan_existing_files", wraps=lib._scan_existing_files) as scan:
            lib._load_existing_files()
            lib._load_existing_files()
        self.assertEqual(scan.call_count, 1)

    def test_a_new_file_shows_up_immediately(self):
        self._touch("House/a.mp3")
        self.assertEqual(len(lib._load_existing_files()), 1)
        p = self._touch("House/b.mp3")
        os.utime(os.path.dirname(p), (1_900_000_000, 1_900_000_000))   # force a different folder mtime
        self.assertEqual(len(lib._load_existing_files()), 2)


class TestOverviewCache(unittest.TestCase):
    def test_overview_is_computed_once_within_the_ttl(self):
        from services import analytics_service as an
        with mock.patch.dict(an._OVERVIEW_CACHE, {"at": 0.0, "value": None}), \
             mock.patch.object(an, "_compute_overview_stats", return_value={"total_downloads": 5}) as comp:
            an.get_overview_stats()
            self.assertEqual(an.get_overview_stats()["total_downloads"], 5)
        self.assertEqual(comp.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
