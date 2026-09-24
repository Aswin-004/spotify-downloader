"""
Tests for services/playlist_delta.py — reading a playlist with one small request unless it changed.

The whole point is the request COUNT (a full read of the 2,070-song ingest playlist is 21 requests;
polling that every minute is what gets the app locked out by Spotify), so the fake client records every
call and the tests assert on them.
"""
import os
import tempfile
import unittest

from services import playlist_delta as pd

PID = "playlist1"


def _t(i):
    return {"id": f"id{i}", "title": f"Song {i}", "artist": "A", "duration_ms": 200000}


class FakeClient:
    def __init__(self, n=2070, snapshot="s1"):
        self.ids = list(range(n))
        self.snapshot = snapshot
        self.calls = []

    def get_playlist_meta(self, playlist_id):
        self.calls.append("meta")
        return {"snapshot_id": self.snapshot, "total": len(self.ids)}

    def get_playlist_window(self, playlist_id, offset, limit):
        self.calls.append(("window", offset, limit))
        return [_t(i) for i in self.ids[offset:offset + limit]]

    def get_playlist_tracks_by_id(self, playlist_id, force_refresh=False):
        self.calls.append("full")
        return [_t(i) for i in self.ids]

    # helpers to change the playlist the way Spotify would
    def append(self, *new, snap=None):
        self.ids.extend(new)
        self.snapshot = snap or self.snapshot + "+"

    def insert_top(self, *new, snap=None):
        self.ids[0:0] = list(new)
        self.snapshot = snap or self.snapshot + "+"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state.json")
        self.now = 1_000_000.0

    def read(self, client, **kw):
        return pd.read_playlist(client, PID, pd.load_state(self.path).get(PID), now=kw.pop("now", self.now), **kw)

    def commit(self, r, now=None):
        pd.commit(PID, r, now=self.now if now is None else now, path=self.path)


class TestDecisions(Base):
    def test_first_read_is_a_full_read(self):
        c = FakeClient()
        r = self.read(c)
        self.assertEqual(r.mode, "full")
        self.assertEqual(len(r.tracks), 2070)
        self.assertEqual(c.calls, ["meta", "full"])

    def test_unchanged_playlist_costs_exactly_one_request(self):
        c = FakeClient()
        self.commit(self.read(c))
        c.calls.clear()
        r = self.read(c)
        self.assertEqual(r.mode, "skip")
        self.assertEqual(r.tracks, [])
        self.assertEqual(c.calls, ["meta"])

    def test_one_song_appended_reads_the_top_and_the_end_only(self):
        c = FakeClient()
        self.commit(self.read(c))
        c.calls.clear()
        c.append(5000)
        r = self.read(c)
        self.assertEqual(r.mode, "incremental")
        self.assertIn("id5000", {t["id"] for t in r.tracks})
        self.assertEqual(c.calls[0], "meta")
        self.assertEqual(sorted(c.calls[1:]), sorted([("window", 0, 50), ("window", 2071 - 51, 51)]))
        self.assertLess(len(r.tracks), 150)                      # nowhere near the 2,070-song full read

    def test_song_added_at_the_TOP_is_found_too(self):
        c = FakeClient()
        self.commit(self.read(c))
        c.insert_top(7777)
        r = self.read(c)
        self.assertEqual(r.mode, "incremental")
        self.assertIn("id7777", {t["id"] for t in r.tracks})

    def test_small_playlist_is_read_in_one_window(self):
        c = FakeClient(n=30)
        self.commit(self.read(c))
        c.calls.clear()
        c.append(99)
        r = self.read(c)
        self.assertEqual(c.calls, ["meta", ("window", 0, 31)])
        self.assertIn("id99", {t["id"] for t in r.tracks})

    def test_a_big_batch_falls_back_to_a_full_read(self):
        c = FakeClient()
        self.commit(self.read(c))
        c.calls.clear()
        c.append(*range(10_000, 10_000 + pd.MAX_INCREMENTAL_GROWTH + 1))
        r = self.read(c)
        self.assertEqual(r.mode, "full")
        self.assertEqual(c.calls, ["meta", "full"])

    def test_removing_songs_still_reads_safely(self):
        c = FakeClient()
        self.commit(self.read(c))
        del c.ids[100:110]
        c.snapshot = "changed"
        r = self.read(c)
        self.assertEqual(r.mode, "incremental")
        self.assertEqual(r.total, 2060)

    def test_full_read_at_least_every_24_hours_even_if_unchanged(self):
        c = FakeClient()
        self.commit(self.read(c), now=self.now)
        c.calls.clear()
        r = self.read(c, now=self.now + pd.FULL_RESYNC_SECONDS + 1)
        self.assertEqual(r.mode, "full")

    def test_force_full(self):
        c = FakeClient()
        self.commit(self.read(c))
        self.assertEqual(self.read(c, force_full=True).mode, "full")

    def test_missing_snapshot_id_never_skips(self):
        c = FakeClient()
        c.snapshot = ""
        self.commit(self.read(c))
        self.assertEqual(self.read(c).mode, "incremental")

    def test_short_read_at_end_of_playlist_stops_paging(self):
        c = FakeClient(n=10)
        self.assertEqual(len(pd._read_window(c, PID, 0, 250)), 10)
        self.assertEqual(c.calls, [("window", 0, 100)])


class TestStateIsOnlyAdvancedByCommit(Base):
    def test_uncommitted_read_is_read_again(self):
        """A crash between 'read the new songs' and 'downloaded them' must not turn into 'nothing changed'."""
        c = FakeClient()
        self.commit(self.read(c))
        c.append(4242)
        first = self.read(c)                                     # ... the app dies here, never commits ...
        self.assertIn("id4242", {t["id"] for t in first.tracks})
        again = self.read(c)
        self.assertEqual(again.mode, "incremental")
        self.assertIn("id4242", {t["id"] for t in again.tracks})

    def test_after_commit_the_same_stamp_is_a_skip(self):
        c = FakeClient()
        self.commit(self.read(c))
        c.append(4242)
        self.commit(self.read(c))
        self.assertEqual(self.read(c).mode, "skip")

    def test_incremental_commit_does_not_count_as_a_full_read(self):
        c = FakeClient()
        self.commit(self.read(c), now=100.0)
        c.append(1)
        self.commit(self.read(c, now=200.0), now=200.0)
        self.assertEqual(pd.load_state(self.path)[PID]["last_full_at"], 100.0)

    def test_playlists_do_not_share_state(self):
        c = FakeClient()
        pd.commit("other", self.read(c), now=self.now, path=self.path)
        self.assertEqual(self.read(c).mode, "full")              # PID has no state of its own


class TestPlanWindows(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(pd.plan_windows(0, 0), [])
        self.assertEqual(pd.plan_windows(30, 1), [(0, 30)])
        self.assertEqual(pd.plan_windows(60, 5), [(0, 60)])                     # touching windows merge
        self.assertEqual(pd.plan_windows(100, 0), [(0, 100)])
        self.assertEqual(pd.plan_windows(150, 0), [(0, 50), (100, 50)])
        self.assertEqual(pd.plan_windows(2070, 3), [(0, 50), (2017, 53)])


class TestStateFile(unittest.TestCase):
    def test_missing_or_corrupt_file_means_no_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            self.assertEqual(pd.load_state(p), {})
            with open(p, "w") as fh:
                fh.write("{not json")
            self.assertEqual(pd.load_state(p), {})


if __name__ == "__main__":
    unittest.main()
