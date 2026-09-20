"""
Tests for services/requeue_service.py — the mechanism that makes a track download AGAIN on the
next ingest cycle after its wrong file was removed.

Without it, deleting a file changes nothing: the track id stays in ingest_tracks.json, so the next
cycle skips it as "already done"; the 3-strike failure counter can block it permanently; and a
track that was never in the ingest playlist is never revisited at all.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import requeue_service as rq


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        p = mock.patch.object(rq, "REQUEUE_FILE", os.path.join(self.tmpdir.name, "ingest_requeue.json"))
        p.start()
        self.addCleanup(p.stop)


def _track(tid, name="Song", artist="Artist", ms=244000):
    return {"id": tid, "name": name, "duration_ms": ms,
            "artists": [{"name": artist, "id": "art1"}],
            "album": {"images": [{"url": "http://img"}], "release_date": "2011-11-11"}}


class FakeSpotify:
    """Minimal SpotifyService stand-in."""

    def __init__(self, tracks=None, search_items=None):
        self.tracks = tracks or {}
        self.search_items = search_items or []
        self.sp = mock.Mock()
        self.sp.track.side_effect = lambda sid: self.tracks[sid]
        self.sp.search.side_effect = lambda **kw: {"tracks": {"items": self.search_items}}

    def _call_with_backoff(self, fn, *a, **k):
        return fn(*a, **k)


class TestQueueStorage(_Base):
    def test_add_and_load(self):
        self.assertEqual(rq.add_to_requeue([{"spotify_id": "s1", "title": "A", "artist": "X"}], "wrong version"), 1)
        q = rq.load_requeue()
        self.assertEqual(q[0]["spotify_id"], "s1")
        self.assertEqual(q[0]["reason"], "wrong version")

    def test_duplicates_ignored_by_id_and_by_title_artist(self):
        rq.add_to_requeue([{"spotify_id": "s1", "title": "A", "artist": "X"}])
        self.assertEqual(rq.add_to_requeue([{"spotify_id": "s1", "title": "A", "artist": "X"}]), 0)
        rq.add_to_requeue([{"title": "Song  One", "artist": "The Band"}])
        self.assertEqual(rq.add_to_requeue([{"title": "song one", "artist": "the band"}]), 0)

    def test_remove_by_spotify_id_or_title_artist_key(self):
        rq.add_to_requeue([{"spotify_id": "s1", "title": "A", "artist": "X"},
                           {"title": "Only Title", "artist": "Y"}])
        self.assertEqual(rq.remove_from_requeue(["s1"]), 1)
        self.assertEqual(rq.remove_from_requeue(rq.key_for_track("", "Only Title", "Y")), 1)
        self.assertEqual(rq.load_requeue(), [])

    def test_missing_file_is_an_empty_queue(self):
        self.assertEqual(rq.load_requeue(), [])

    def test_corrupt_file_never_raises(self):
        with open(rq.REQUEUE_FILE, "w") as fh:
            fh.write("{not json")
        self.assertEqual(rq.load_requeue(), [])
        self.assertEqual(rq.add_to_requeue([{"spotify_id": "s9", "title": "T", "artist": "A"}]), 1)


class TestRequeueTracks(_Base):
    def test_forgets_history_resets_only_those_failure_counters_and_queues(self):
        counts = {"s1": 3, "s2": 3, "other": 2}
        saved = {}
        with mock.patch("services.auto_downloader.remove_tracks_from_history",
                        return_value={"removed": 2, "remaining": 10}) as rm, \
             mock.patch("services.auto_downloader._load_failure_counts", return_value=counts), \
             mock.patch("services.auto_downloader._save_failure_counts",
                        side_effect=lambda c: saved.update(c)):
            out = rq.requeue_tracks([{"spotify_id": "s1", "title": "A", "artist": "X"},
                                     {"spotify_id": "s2", "title": "B", "artist": "Y"}], "wrong version")
        rm.assert_called_once_with(["s1", "s2"])
        self.assertEqual(out, {"queued": 2, "history_removed": 2, "failures_reset": 2})
        self.assertEqual(saved, {"other": 2}, "an unrelated track's failure counter was touched")
        self.assertEqual(len(rq.load_requeue()), 2)

    def test_entries_without_a_spotify_id_still_get_queued(self):
        with mock.patch("services.auto_downloader.remove_tracks_from_history") as rm, \
             mock.patch("services.auto_downloader._load_failure_counts", return_value={}):
            out = rq.requeue_tracks([{"title": "No Id Song", "artist": "Someone"}])
        rm.assert_not_called()
        self.assertEqual(out["queued"], 1)


class TestMergeIntoIngestCycle(_Base):
    def _merge(self, sp, new_tracks=None, failures=None, rate_limited=False):
        with mock.patch("services.spotify_service.is_rate_limited", return_value=rate_limited):
            return rq.merge_requeued_tracks(new_tracks or [], sp, failures or {}, 3)

    def test_queued_track_is_added_to_the_cycle_with_playlist_shape(self):
        rq.add_to_requeue([{"spotify_id": "s1", "title": "Song", "artist": "Artist"}])
        out = self._merge(FakeSpotify({"s1": _track("s1")}))
        self.assertEqual([t["id"] for t in out], ["s1"])
        self.assertEqual(set(out[0]), {"id", "title", "artist", "artist_id", "duration_ms",
                                       "album_art_url", "release_date"})
        self.assertEqual(out[0]["duration_ms"], 244000)

    def test_no_duplicate_when_the_track_is_already_in_the_cycle(self):
        rq.add_to_requeue([{"spotify_id": "s1", "title": "Song", "artist": "Artist"}])
        out = self._merge(FakeSpotify({"s1": _track("s1")}), new_tracks=[{"id": "s1", "title": "Song"}])
        self.assertEqual(len(out), 1)

    def test_permanently_failed_track_is_not_retried(self):
        rq.add_to_requeue([{"spotify_id": "s1", "title": "Song", "artist": "Artist"}])
        self.assertEqual(self._merge(FakeSpotify({"s1": _track("s1")}), failures={"s1": 3}), [])

    def test_rate_limit_leaves_the_entry_queued_for_next_cycle(self):
        rq.add_to_requeue([{"spotify_id": "s1", "title": "Song", "artist": "Artist"}])
        sp = FakeSpotify({"s1": _track("s1")})
        self.assertEqual(self._merge(sp, rate_limited=True), [])
        sp.sp.track.assert_not_called()
        self.assertEqual(len(rq.load_requeue()), 1)
        self.assertEqual(rq.load_requeue()[0]["attempts"], 0, "a rate limit must not count as a failed lookup")

    def test_title_only_entry_resolves_by_strict_search(self):
        rq.add_to_requeue([{"title": "Make Some Noise For The Desi Boyz", "artist": "Pritam"}])
        sp = FakeSpotify(search_items=[
            _track("wrong", "Make Some Noise (Karaoke)", "Some Band"),
            _track("right", "Make Some Noise For The Desi Boyz", "Pritam"),
        ])
        out = self._merge(sp)
        self.assertEqual([t["id"] for t in out], ["right"])

    def test_title_only_entry_with_no_confident_match_is_counted_and_eventually_dropped(self):
        rq.add_to_requeue([{"title": "Obscure Song", "artist": "Nobody"}])
        sp = FakeSpotify(search_items=[_track("x", "Totally Different", "Other Artist")])
        for expected_attempts in range(1, rq.MAX_RESOLVE_ATTEMPTS):
            self.assertEqual(self._merge(sp), [])
            self.assertEqual(rq.load_requeue()[0]["attempts"], expected_attempts)
        self.assertEqual(self._merge(sp), [])
        self.assertEqual(rq.load_requeue(), [], "an unresolvable entry stayed in the queue forever")

    def test_empty_queue_returns_input_unchanged(self):
        tracks = [{"id": "a"}]
        self.assertEqual(self._merge(FakeSpotify(), new_tracks=tracks), tracks)


class TestIngestIsWiredToTheQueue(unittest.TestCase):
    """Source-contract test (same approach as test_index_recovery.py): ingest_download is a huge
    unrefactored function, so assert the hooks exist and sit in the right order."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / "services" / "auto_downloader.py").read_text(encoding="utf-8")

    def test_queue_is_merged_before_the_nothing_new_early_return(self):
        merge = self.src.index("merge_requeued_tracks(")
        early = self.src.index("if not new_tracks:", merge - 2000)
        self.assertLess(merge, early, "queued tracks would be ignored whenever the playlist has nothing new")

    def test_queue_is_merged_after_the_history_and_failure_filters(self):
        hist = self.src.index("new_tracks = [t for t in tracks if t[\"id\"] not in saved_ids]")
        fail = self.src.index("failure_counts.get(t[\"id\"], 0) < MAX_FAIL_ATTEMPTS")
        merge = self.src.index("merge_requeued_tracks(")
        self.assertLess(hist, merge)
        self.assertLess(fail, merge)

    def test_entries_leave_the_queue_on_success_and_on_duplicate_skip(self):
        self.assertEqual(len(re.findall(r"_requeue_done\(tid, title, artist,", self.src)), 2)


if __name__ == "__main__":
    unittest.main()
