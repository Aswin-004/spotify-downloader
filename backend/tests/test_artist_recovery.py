"""
Tests for services/artist_recovery.py — recovering the real artist of tracks whose artist tag is
missing, "Unknown", or a genre word ("Electronic"). On the real library that was 14% of all tracks.

The recovery talks to Spotify, where a 429 blocks the whole app for ~22 h, so most of these tests
pin the safety behaviour: pacing, the per-run cap, stopping at the first rate limit, and never
asking twice for the same track.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import artist_recovery as ar
from services.genre_evidence import TagCache

_block_patch = None


def setUpModule():
    """Anything here that trips the 'remember the Spotify block' path must write to a throw-away
    file — never to the real reports/spotify_block.json that protects the live app."""
    global _block_patch
    _tmp = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(_tmp.cleanup)
    _block_patch = mock.patch.object(ar, "BLOCK_FILE", Path(_tmp.name) / "no_block.json")
    _block_patch.start()


def tearDownModule():
    _block_patch.stop()


def found(*artists, title="T", album="A"):
    return {"artists": list(artists), "title": title, "album": album}


class TestPlaceholderArtists(unittest.TestCase):
    def test_placeholders(self):
        for name in ("Unknown", "  UNKNOWN ", "unknown artist", "", None, "Electronic", "Dance", "House",
                     "Hip Hop", "hip-hop", "R&B", "Various Artists", "N/A", "Bollywood", "Drum & Bass"):
            self.assertTrue(ar.is_placeholder_artist(name), repr(name))

    def test_real_artists_are_not_placeholders(self):
        for name in ("Kova", "Pop Smoke", "House of Pain", "Techno Project", "Skrillex", "Electronic Sheep",
                     "Above & Beyond"):
            self.assertFalse(ar.is_placeholder_artist(name), name)

    def test_join(self):
        self.assertEqual(ar.join_artists(["Kova", "", "Memento Mori"]), "Kova, Memento Mori")
        self.assertEqual(ar.join_artists([]), "")


class TestRecoverArtists(unittest.TestCase):
    def setUp(self):
        self.cache = TagCache(None)
        self.sleeps = []
        self.calls = []

    def _fetch(self, table):
        def fetch(sid):
            self.calls.append(sid)
            value = table[sid]
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    def _run(self, ids, table, **kw):
        return ar.recover_artists(ids, self._fetch(table), self.cache, sleep=self.sleeps.append,
                                  out=lambda *_: None, **kw)

    def test_looks_up_each_distinct_id_once_and_ignores_blanks(self):
        res = self._run(["a", "b", "a", "", None, " b "], {"a": found("Kova"), "b": found("X", "Y")})
        self.assertEqual(self.calls, ["a", "b"])
        self.assertEqual(res.found["a"]["artists"], ["Kova"])
        self.assertEqual(res.found["b"]["artists"], ["X", "Y"])
        self.assertEqual(res.calls, 2)

    def test_second_run_costs_no_calls(self):
        table = {"a": found("Kova"), "b": {}}
        self._run(["a", "b"], table)
        self.calls.clear()
        res = self._run(["a", "b"], table)
        self.assertEqual(self.calls, [])
        self.assertEqual((res.calls, res.from_cache, len(res.found), res.unknown), (0, 2, 1, 1))

    def test_calls_are_paced(self):
        self._run(["a", "b", "c"], {k: found("X") for k in "abc"}, pace_s=1.5)
        self.assertEqual(self.sleeps, [1.5, 1.5])                        # between calls, not after the last

    def test_a_track_spotify_does_not_know_is_cached_but_a_failure_is_not(self):
        res = self._run(["gone", "flaky"], {"gone": {}, "flaky": None})
        self.assertEqual((res.unknown, res.failed), (1, 1))
        self.assertEqual(self.cache.get("gone", None), {})               # never asked again
        self.assertIsNone(self.cache.get("flaky", None))                 # retried next run

    def test_the_first_rate_limit_stops_the_run_and_keeps_progress(self):
        table = {"a": found("Kova"), "b": ar.RateLimited("429, 80000s"), "c": found("Never")}
        res = self._run(["a", "b", "c"], table)
        self.assertEqual(self.calls, ["a", "b"])                         # 'c' is never requested
        self.assertIn("rate limited", res.stopped)
        self.assertEqual(list(res.found), ["a"])
        self.assertEqual(self.cache.get("a", None)["artists"], ["Kova"])
        self.assertIsNone(self.cache.get("c", None))

    def test_call_cap(self):
        res = self._run(list("abcde"), {k: found("X") for k in "abcde"}, max_calls=2)
        self.assertEqual(res.calls, 2)
        self.assertIn("call cap", res.stopped)
        self.assertEqual(len(res.found), 2)

    def test_cache_is_flushed_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            cache = TagCache(Path(d, "c.json"), ttl_days=10, flush_every=1000)
            ar.recover_artists(["a"], lambda s: found("Kova"), cache, sleep=lambda *_: None, out=lambda *_: None)
            self.assertEqual(TagCache(Path(d, "c.json"), ttl_days=10).get("a", None)["artists"], ["Kova"])


class TestValidateRecovery(unittest.TestCase):
    """On the real library 17% of Spotify ids in the files pointed at a DIFFERENT song."""

    def test_an_id_that_points_at_another_song_is_rejected(self):
        for file_title, spotify_title in (("Kiya Kiya", "Candytuft Parsley"), ("Dil Nu", "Dil Pe Zakham Khate Hain"),
                                          ("WOH", "Woh Ladki Jo - Sped Up"), ("Play", "Players"),
                                          ("Aura", 'Aura of Ustaad (From "Ustaad Bhagat Singh")')):
            ok, why = ar.validate_recovery(file_title, {"title": spotify_title, "artists": ["X"]})
            self.assertFalse(ok, file_title)
            self.assertIn("different song", why)

    def test_the_same_song_with_edition_tags_is_accepted(self):
        for file_title, spotify_title in (("Chhote Chhote Peg", 'Chhote Chhote Peg (From "Yaariyan")'),
                                          ("Levels", "Levels (Original Mix)"), ("Khoyo - Kahani Remix", "Khoyo - Kahani Remix")):
            self.assertEqual(ar.validate_recovery(file_title, {"title": spotify_title, "artists": ["X"]}), (True, ""))

    def test_length_is_checked_only_when_both_are_known(self):
        hit = {"title": "Dagger", "artists": ["Slowdive"], "duration_ms": 330_000}
        self.assertEqual(ar.validate_recovery("Dagger", hit, 335.0), (True, ""))
        self.assertEqual(ar.validate_recovery("Dagger", hit, None), (True, ""))
        self.assertEqual(ar.validate_recovery("Dagger", dict(hit, duration_ms=None), 276.0), (True, ""))
        ok, why = ar.validate_recovery("Dagger", hit, 276.0)                  # a different "Dagger"
        self.assertFalse(ok)
        self.assertIn("54s shorter", why)
        self.assertIn("longer", ar.validate_recovery("Dagger", hit, 400.0)[1])

    def test_the_length_tolerance_is_adjustable(self):
        hit = {"title": "T", "artists": ["X"], "duration_ms": 200_000}
        self.assertFalse(ar.validate_recovery("T", hit, 225.0)[0])
        self.assertTrue(ar.validate_recovery("T", hit, 225.0, max_length_delta=30)[0])


class TestKnownAndDuration(unittest.TestCase):
    def setUp(self):
        self.cache = TagCache(None)
        self.calls = []

    def run_(self, ids, fetch=None, **kw):
        def default(sid):
            self.calls.append(sid)
            return found("Fetched", title="T") | {"duration_ms": 1000}
        return ar.recover_artists(ids, fetch or default, self.cache, sleep=lambda *_: None,
                                  out=lambda *_: None, **kw)

    def test_known_from_playlist(self):
        k = ar.known_from_playlist([{"id": "a", "artist": "Kova", "title": "T", "duration_ms": 5}, {"id": "", "artist": "x"},
                                    {"id": "b", "artist": "", "title": "U"}])
        self.assertEqual(k["a"], {"artists": ["Kova"], "title": "T", "duration_ms": 5})
        self.assertEqual(k["b"]["artists"], [])
        self.assertNotIn("", k)

    def test_free_playlist_data_replaces_a_call(self):
        res = self.run_(["a"], known={"a": {"artists": ["Kova"], "title": "T", "duration_ms": 200_000}}, want_duration=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(res.found["a"], {"artists": ["Kova"], "title": "T", "duration_ms": 200_000})
        self.assertEqual(self.cache.get("a", None)["duration_ms"], 200_000)          # kept for next time

    def test_an_old_cache_entry_without_a_length_is_completed_from_the_playlist_keeping_all_artists(self):
        self.cache.set("a", found("Kova", "Memento Mori", title="T"))
        res = self.run_(["a"], known={"a": {"artists": ["Kova"], "title": "T", "duration_ms": 1234}}, want_duration=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(res.found["a"]["artists"], ["Kova", "Memento Mori"])         # the fuller credit list wins
        self.assertEqual(res.found["a"]["duration_ms"], 1234)

    def test_an_old_cache_entry_without_a_length_is_refetched_when_nothing_else_has_it(self):
        self.cache.set("a", found("Kova", title="T"))
        res = self.run_(["a"], want_duration=True)
        self.assertEqual(self.calls, ["a"])
        self.assertEqual(res.found["a"]["duration_ms"], 1000)

    def test_without_want_duration_an_old_entry_is_left_alone(self):
        self.cache.set("a", found("Kova", title="T"))
        res = self.run_(["a"])
        self.assertEqual(self.calls, [])
        self.assertNotIn("duration_ms", res.found["a"])

    def test_known_without_a_length_cannot_complete_a_want_duration_record(self):
        res = self.run_(["a"], known={"a": {"artists": ["Kova"], "title": "T", "duration_ms": None}}, want_duration=True)
        self.assertEqual(self.calls, ["a"])
        self.assertEqual(res.found["a"]["artists"], ["Fetched"])

    def test_a_track_spotify_does_not_have_stays_unknown(self):
        self.cache.set("gone", {})
        res = self.run_(["gone"], want_duration=True, known={})
        self.assertEqual((self.calls, res.unknown), ([], 1))


class TestPartialFallback(unittest.TestCase):
    """When Spotify is unavailable, a cached record WITHOUT a length is still returned (marked
    partial) so the caller can validate it by title — instead of the artist being thrown away."""

    def setUp(self):
        self.cache = TagCache(None)
        self.cache.set("old", found("Kova", title="T"))                       # written before lengths were kept
        self.cache.set("complete", {"artists": ["X"], "title": "T", "duration_ms": 1000})

    def run_(self, ids, fetch, **kw):
        return ar.recover_artists(ids, fetch, self.cache, want_duration=True, sleep=lambda *_: None,
                                  out=lambda *_: None, **kw)

    def test_a_rate_limit_leaves_old_records_usable_as_partial(self):
        def limited(sid):
            raise ar.RateLimited("429")
        res = self.run_(["old", "complete", "never_cached"], limited)
        self.assertEqual(sorted(res.found), ["complete", "old"])
        self.assertEqual(res.partial, {"old"})
        self.assertIn("rate limited", res.stopped)

    def test_the_call_cap_does_the_same(self):
        res = self.run_(["old"], lambda s: found("Never"), max_calls=0)
        self.assertEqual(res.found["old"]["artists"], ["Kova"])
        self.assertEqual(res.partial, {"old"})

    def test_a_record_that_gets_completed_is_not_partial(self):
        res = self.run_(["old"], lambda s: found("Kova", title="T") | {"duration_ms": 5})
        self.assertEqual(res.partial, set())
        self.assertEqual(res.found["old"]["duration_ms"], 5)

    def test_without_want_duration_nothing_is_partial(self):
        res = ar.recover_artists(["old"], lambda s: None, self.cache, sleep=lambda *_: None, out=lambda *_: None)
        self.assertEqual((sorted(res.found), res.partial), (["old"], set()))


class TestSpotifyBlockFile(unittest.TestCase):
    """The app's cooldown flag lives in process memory, so a NEW process would call Spotify again.
    The block is therefore also written to disk."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.path = Path(self._t.name, "sub", "block.json")

    def test_roundtrip(self):
        self.assertEqual(ar.block_seconds_left(self.path), 0)                 # no file: no block
        ar.record_block(3600, self.path)
        self.assertTrue(3590 <= ar.block_seconds_left(self.path) <= 3600)

    def test_an_expired_or_corrupt_file_means_no_block(self):
        ar.record_block(-10, self.path)
        self.assertEqual(ar.block_seconds_left(self.path), 0)
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(ar.block_seconds_left(self.path), 0)

    def test_default_path_is_the_module_setting(self):
        with mock.patch.object(ar, "BLOCK_FILE", self.path):
            ar.record_block(600)
            self.assertGreater(ar.block_seconds_left(), 0)

    def test_seconds_are_read_from_the_apps_messages(self):
        self.assertEqual(ar._seconds_from_message("Spotify rate limited. Blocked for 82373s."), 82373)
        self.assertEqual(ar._seconds_from_message("Spotify API cooling down. Retry in 500s."), 500)
        self.assertEqual(ar._seconds_from_message("something else"), 0)

    def test_a_429_seen_by_the_fetcher_is_remembered_for_the_next_process(self):
        svc = mock.Mock()

        def blocked(fn, *a, **k):
            raise ValueError("Spotify rate limited. Blocked for 82373s.")
        svc._call_with_backoff = blocked
        with mock.patch("services.spotify_service.get_spotify_service", return_value=svc), \
             mock.patch.object(ar, "BLOCK_FILE", self.path):
            fetch = ar.spotify_fetcher()
            with self.assertRaises(ar.RateLimited):
                fetch("x")
            self.assertTrue(82000 <= ar.block_seconds_left() <= 82373)

    def test_a_message_without_seconds_records_nothing(self):
        svc = mock.Mock()

        def blocked(fn, *a, **k):
            raise ValueError("something odd")
        svc._call_with_backoff = blocked
        with mock.patch("services.spotify_service.get_spotify_service", return_value=svc), \
             mock.patch.object(ar, "BLOCK_FILE", self.path):
            with self.assertRaises(ar.RateLimited):
                ar.spotify_fetcher()("x")
            self.assertFalse(self.path.exists())


class TestSpotifyFetcher(unittest.TestCase):
    def _fetcher(self, track_impl):
        svc = mock.Mock()
        svc._call_with_backoff = lambda fn, *a, **k: fn(*a, **k)
        svc.sp.track = track_impl
        patcher = mock.patch("services.spotify_service.get_spotify_service", return_value=svc)
        patcher.start()
        self.addCleanup(patcher.stop)
        return ar.spotify_fetcher()

    def test_maps_a_track_to_artists_title_album(self):
        f = self._fetcher(lambda sid: {"name": "Acelerar", "artists": [{"name": "Kova"}, {"name": "Memento Mori"}],
                                       "album": {"name": "Acelerar EP"}, "duration_ms": 245000})
        self.assertEqual(f("x"), {"artists": ["Kova", "Memento Mori"], "title": "Acelerar", "album": "Acelerar EP",
                                  "duration_ms": 245000})

    def test_cooldown_or_429_becomes_ratelimited(self):
        def boom(sid):
            raise ValueError("Spotify rate limited. Blocked for 80000s.")
        with self.assertRaises(ar.RateLimited):
            self._fetcher(boom)("x")

    def test_unknown_track_is_a_definite_empty_answer(self):
        class NotFound(Exception):
            http_status = 404
        def boom(sid):
            raise NotFound()
        self.assertEqual(self._fetcher(boom)("x"), {})

    def test_other_errors_are_transient(self):
        class Forbidden(Exception):
            http_status = 403
        def boom(sid):
            raise Forbidden()
        self.assertIsNone(self._fetcher(boom)("x"))


if __name__ == "__main__":
    unittest.main()
