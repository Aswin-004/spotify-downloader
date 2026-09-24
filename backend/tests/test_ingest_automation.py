"""
Tests for how the ingest loop (services/auto_downloader.py) uses the new automation pieces:
the cheap playlist read, genre playlists, verified-evidence routing for unknown artists, the poll floor,
and the "advance the watch state only after a clean cycle" rule.

_download_single is a long closure that downloads real audio, so — like tests/test_download_path_normalization.py —
the ordering/wiring rules are checked as source contracts, and every decision that CAN be a plain function
(_read_playlists, _commit_reads, _evidence_route, effective_poll_interval) is tested by calling it.
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from services import auto_downloader as ad
from services import genre_playlists as gp
from services import playlist_delta as pd

_SRC = Path(ad.__file__).read_text(encoding="utf-8")
_INGEST = _SRC[_SRC.index("\ndef ingest_download"):_SRC.index("\ndef playlist_monitor")]
_MONITOR = _SRC[_SRC.index("\ndef playlist_monitor"):_SRC.index("\nif __name__")]

MAIN, HOUSE = "MAINPLAYLIST00000000AA", "HOUSEPLAYLIST0000000AA"     # real playlist ids are 22 characters


def _t(i, title="Song"):
    return {"id": i, "title": title, "artist": "A", "artist_id": "x", "duration_ms": 1000}


class FakeSpotify:
    """Two playlists; every request is recorded."""

    def __init__(self, lists, snapshots=None):
        self.lists = lists                                    # {playlist_id: [ids]}
        self.snap = snapshots or {p: "s1" for p in lists}
        self.calls = []

    def _ids(self, pid):
        if pid not in self.lists:
            raise RuntimeError("403 Forbidden")
        return self.lists[pid]

    def get_playlist_meta(self, pid):
        self.calls.append(("meta", pid))
        return {"snapshot_id": self.snap[pid], "total": len(self._ids(pid))}

    def get_playlist_window(self, pid, offset, limit):
        self.calls.append(("window", pid, offset, limit))
        return [_t(i) for i in self._ids(pid)[offset:offset + limit]]

    def get_playlist_tracks_by_id(self, pid, force_refresh=False):
        self.calls.append(("full", pid))
        return [_t(i) for i in self._ids(pid)]


class WatchBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name in ((pd, "STATE_FILE"), (gp, "STORE_FILE")):
            p = mock.patch.object(target, name, os.path.join(self.tmp.name, name.lower() + ".json"))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ad, "INGEST_PLAYLIST_ID", MAIN)
        p.start()
        self.addCleanup(p.stop)


class TestReadPlaylists(WatchBase):
    def test_genre_playlist_songs_carry_their_crate_and_win_over_the_ingest_playlist(self):
        gp.add(HOUSE, "house")
        sp = FakeSpotify({MAIN: ["a", "b"], HOUSE: ["b", "c"]})
        tracks, reads = ad._read_playlists(sp, full_scan=False)
        by = {t["id"]: t for t in tracks}
        self.assertEqual(sorted(by), ["a", "b", "c"])
        self.assertNotIn("forced_genre", by["a"])
        self.assertEqual(by["b"]["forced_genre"], "House")
        self.assertEqual(by["c"]["forced_genre"], "House")
        self.assertEqual([pid for pid, _ in reads], [MAIN, HOUSE])

    def test_an_unreadable_genre_playlist_is_skipped_but_the_ingest_playlist_still_works(self):
        gp.add(HOUSE, "house")
        sp = FakeSpotify({MAIN: ["a"]})                        # HOUSE answers 403
        tracks, reads = ad._read_playlists(sp, full_scan=False)
        self.assertEqual([t["id"] for t in tracks], ["a"])
        self.assertEqual([pid for pid, _ in reads], [MAIN])   # only what was actually read gets committed later

    def test_an_unreadable_ingest_playlist_is_an_error_for_the_caller(self):
        sp = FakeSpotify({})
        with self.assertRaises(Exception):
            ad._read_playlists(sp, full_scan=False)

    def test_unchanged_playlists_cost_one_request_each(self):
        gp.add(HOUSE, "house")
        sp = FakeSpotify({MAIN: list(map(str, range(300))), HOUSE: ["x"]})
        _t1, reads = ad._read_playlists(sp, full_scan=False)
        ad._commit_reads(reads)
        sp.calls.clear()
        tracks, reads = ad._read_playlists(sp, full_scan=False)
        self.assertEqual(tracks, [])
        self.assertEqual(sp.calls, [("meta", MAIN), ("meta", HOUSE)])
        self.assertTrue(all(r.mode == "skip" for _, r in reads))

    def test_full_scan_reads_everything_even_when_unchanged(self):
        sp = FakeSpotify({MAIN: ["a", "b"]})
        ad._commit_reads(ad._read_playlists(sp, full_scan=False)[1])
        sp.calls.clear()
        tracks, _ = ad._read_playlists(sp, full_scan=True)
        self.assertEqual(len(tracks), 2)
        self.assertIn(("full", MAIN), sp.calls)

    def test_commit_failure_never_breaks_the_cycle(self):
        with mock.patch.object(pd, "commit", side_effect=OSError("disk full")):
            ad._commit_reads([(MAIN, pd.PlaylistRead("full", [], 0, "s"))])      # must not raise


class TestPollFloor(unittest.TestCase):
    def test_short_or_broken_intervals_are_raised_to_the_floor(self):
        self.assertEqual(ad.effective_poll_interval(60), ad.MIN_POLL_SECONDS)
        self.assertEqual(ad.effective_poll_interval(0), ad.MIN_POLL_SECONDS)
        self.assertEqual(ad.effective_poll_interval("junk"), 600)
        self.assertEqual(ad.effective_poll_interval(3600), 3600)

    def test_floor_is_ten_minutes(self):
        self.assertEqual(ad.MIN_POLL_SECONDS, 600)

    def test_the_live_setting_is_honoured(self):
        with mock.patch.object(ad.config, "CHECK_INTERVAL", 1800):
            self.assertEqual(ad.effective_poll_interval(), 1800)
        with mock.patch.object(ad.config, "CHECK_INTERVAL", 60):
            self.assertEqual(ad.effective_poll_interval(), ad.MIN_POLL_SECONDS)


class TestEvidenceRoute(unittest.TestCase):
    @staticmethod
    def dec(genre="House", abstain=False, verified=True, sources=("lastfm_track", "itunes")):
        return SimpleNamespace(genre=genre, abstain=abstain, verified=verified, sources=list(sources),
                               explain=lambda: "explained")

    def route(self, decision, **kw):
        seen = {}

        def classify(artist, title, **k):
            seen.update(artist=artist, title=title, **k)
            if isinstance(decision, Exception):
                raise decision
            return decision

        out = ad._evidence_route("Some DJ", "Some Track", "/x.mp3", bpm=126, duration_ms=200000, classify=classify, **kw)
        return out, seen

    def test_verified_evidence_names_the_crate_and_says_where_it_came_from(self):
        (path, method), seen = self.route(self.dec())
        self.assertEqual(path, "Library/House")
        self.assertEqual(method, "evidence:lastfm_track+itunes")
        self.assertEqual((seen["bpm"], seen["duration_s"], seen["online"], seen["use_itunes"]), (126, 200.0, True, True))

    def test_unverified_evidence_is_not_enough(self):
        self.assertEqual(self.route(self.dec(verified=False))[0], ("", ""))

    def test_an_abstaining_engine_is_not_enough(self):
        self.assertEqual(self.route(self.dec(abstain=True))[0], ("", ""))

    def test_the_catch_all_is_not_an_answer(self):
        self.assertEqual(self.route(self.dec(genre="Electronic"))[0], ("", ""))
        self.assertEqual(self.route(self.dec(genre=""))[0], ("", ""))

    def test_a_crash_in_the_engine_is_swallowed(self):
        self.assertEqual(self.route(RuntimeError("Last.fm down"))[0], ("", ""))

    def test_it_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"INGEST_EVIDENCE_ROUTING": "false"}):
            self.assertEqual(self.route(self.dec())[0], ("", ""))


class TestWiringContracts(unittest.TestCase):
    """Order and presence rules that keep the pieces honest (see the module docstring)."""

    def test_a_normal_cycle_does_not_re_read_the_whole_playlist(self):
        self.assertNotIn("get_playlist_tracks_by_id(INGEST_PLAYLIST_ID, force_refresh=True)", _INGEST)
        self.assertIn("_read_playlists(sp_service, full_scan=", _INGEST)

    def test_the_watch_state_advances_only_when_the_cycle_was_clean(self):
        self.assertEqual(_INGEST.count("_commit_reads(_reads)"), 2)       # nothing-to-do branch + after a clean run
        i_clean = _INGEST.rindex("_commit_reads(_reads)")
        self.assertLess(_INGEST.rindex("if fail_count[0] == 0:"), i_clean)
        # a user-requested stop returns BEFORE the commit: songs not handled must stay 'changed'
        stop = _INGEST.index("Stop requested — cancelling remaining tracks")
        self.assertLess(stop, i_clean)
        self.assertNotIn("_commit_reads", _INGEST[stop:_INGEST.index("return", stop)])

    def test_verification_runs_before_the_playlist_is_read_and_is_recorded_on_success(self):
        self.assertLess(_INGEST.index("download_verifier.process_pending"), _INGEST.index("_read_playlists(sp_service"))
        self.assertLess(_INGEST.index('_requeue_done(tid, title, artist, "downloaded")'),
                        _INGEST.index("_dv.record_done(tid, title, artist"))

    def test_a_genre_playlist_outranks_artist_memory_and_the_router(self):
        forced = _INGEST.index("elif _forced_folder:")
        self.assertLess(forced, _INGEST.index("_mem_lookup(artist)"))
        self.assertLess(forced, _INGEST.index("resolve_genre_folder_with_confidence(\n"))
        self.assertLess(_INGEST.index("if force_folder:\n                    # Manual override always wins"), forced)

    def test_evidence_is_tried_before_gemini_at_both_fallback_points(self):
        self.assertEqual(_INGEST.count("_fallback_genre(staged_filepath, _bpm_hint)"), 2)
        self.assertNotIn("= _gemini_genre_fallback(staged_filepath)", _INGEST)

    def test_how_a_song_was_filed_is_written_to_its_tags(self):
        self.assertIn('desc="routing_source", text=[_route_tag]', _INGEST)

    def test_monitor_uses_the_floor_not_the_raw_setting(self):
        self.assertIn("time.sleep(effective_poll_interval())", _MONITOR)
        self.assertNotIn("time.sleep(CHECK_INTERVAL)", _MONITOR)

    def test_sync_now_does_a_full_read(self):
        manual = _SRC[_SRC.index("\ndef manual_refresh"):_SRC.index("\nif __name__")]
        self.assertIn("full_scan=True", manual)


if __name__ == "__main__":
    unittest.main()
