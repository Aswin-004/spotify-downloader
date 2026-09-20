"""
Tests for the playlist-snapshot side of services/metadata_cache.py, and for the rate-limited path in
SpotifyService.get_playlist_tracks_by_id that is supposed to fall back to a STALE snapshot.

Bug pinned here: that fallback used the TTL-checked getter (30 min), so during a Spotify rate-limit
block (~23 h) — exactly when a fallback is needed — it found nothing and raised.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services import metadata_cache as mc

TRACKS = [{"id": "a", "title": "T", "artist": "Kova", "duration_ms": 1000}]


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        d = Path(self._t.name)
        for name, value in (("_CACHE_DIR", str(d)), ("_TRACK_CACHE_FILE", str(d / "t.json")),
                            ("_PLAYLIST_CACHE_FILE", str(d / "p.json"))):
            patcher = mock.patch.object(mc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cache = mc.MetadataCache()


class TestPlaylistSnapshotStaleness(SnapshotCase):
    def age(self, seconds):
        self.cache._playlists["p"]["fetched_at"] = time.time() - seconds

    def test_a_fresh_snapshot_is_returned(self):
        self.cache.set_playlist_snapshot("p", TRACKS)
        self.assertEqual(self.cache.get_playlist_snapshot("p"), TRACKS)

    def test_an_expired_snapshot_is_hidden_by_default(self):
        self.cache.set_playlist_snapshot("p", TRACKS)
        self.age(mc.PLAYLIST_SNAPSHOT_TTL + 60)
        self.assertIsNone(self.cache.get_playlist_snapshot("p"))                 # unchanged behaviour

    def test_allow_stale_returns_it_however_old(self):
        self.cache.set_playlist_snapshot("p", TRACKS)
        self.age(60 * 60 * 24 * 30)
        self.assertEqual(self.cache.get_playlist_snapshot("p", allow_stale=True), TRACKS)

    def test_allow_stale_still_returns_none_when_there_is_no_snapshot(self):
        self.assertIsNone(self.cache.get_playlist_snapshot("never", allow_stale=True))

    def test_snapshot_survives_a_restart(self):
        self.cache.set_playlist_snapshot("p", TRACKS)
        self.age(99999)
        self.cache._save_json(mc._PLAYLIST_CACHE_FILE, self.cache._playlists)
        self.assertEqual(mc.MetadataCache().get_playlist_snapshot("p", allow_stale=True), TRACKS)


class TestRateLimitedPlaylistRead(SnapshotCase):
    """SpotifyService.get_playlist_tracks_by_id while Spotify has us blocked."""

    def service(self):
        from services import spotify_service as ss
        svc = ss.SpotifyService.__new__(ss.SpotifyService)          # no credentials / network needed
        return ss, svc

    def test_rate_limited_and_expired_snapshot_is_served_stale_instead_of_raising(self):
        ss, svc = self.service()
        self.cache.set_playlist_snapshot("p", TRACKS)
        self.cache._playlists["p"]["fetched_at"] = time.time() - 60 * 60 * 20     # 20 h old
        with mock.patch.object(ss, "get_cache", return_value=self.cache), \
             mock.patch.object(ss, "is_rate_limited", return_value=True):
            self.assertEqual(svc.get_playlist_tracks_by_id("p"), TRACKS)

    def test_rate_limited_with_no_snapshot_still_raises_cleanly(self):
        ss, svc = self.service()
        with mock.patch.object(ss, "get_cache", return_value=self.cache), \
             mock.patch.object(ss, "is_rate_limited", return_value=True):
            with self.assertRaises(ValueError):
                svc.get_playlist_tracks_by_id("nothing-cached")

    def test_not_rate_limited_an_expired_snapshot_is_refetched_not_served(self):
        ss, svc = self.service()
        self.cache.set_playlist_snapshot("p", TRACKS)
        self.cache._playlists["p"]["fetched_at"] = time.time() - 60 * 60 * 20
        svc.sp = mock.Mock()
        svc._get_user_sp = lambda: None
        svc._call_with_backoff = mock.Mock(return_value={"items": [], "next": None})
        with mock.patch.object(ss, "get_cache", return_value=self.cache), \
             mock.patch.object(ss, "is_rate_limited", return_value=False):
            self.assertEqual(svc.get_playlist_tracks_by_id("p"), [])                # a real (empty) refetch
        svc._call_with_backoff.assert_called()


if __name__ == "__main__":
    unittest.main()
