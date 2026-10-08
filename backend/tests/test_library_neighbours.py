"""
services/library_neighbours.py — route a song by where the same artist's other songs already live.
No network, no real library: a temp folder with fake files and an injected tag reader.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("NEIGHBOUR_ROUTING", "true")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import library_neighbours as ln  # noqa: E402
from services.auto_downloader import decide_route  # noqa: E402


def _library(layout):
    """layout: {crate: [artist, ...]} -> (TemporaryDirectory, {path: artist})."""
    tmp = tempfile.TemporaryDirectory()
    artists = {}
    for crate, names in layout.items():
        os.makedirs(os.path.join(tmp.name, crate))
        for i, a in enumerate(names):
            p = os.path.join(tmp.name, crate, f"song{i}.mp3")
            open(p, "wb").write(b"x")
            artists[p] = a
    return tmp, artists


class TestIndexAndVote(unittest.TestCase):
    def _index(self, layout):
        tmp, artists = _library(layout)
        self.addCleanup(tmp.cleanup)
        return ln.build_index(tmp.name, read_artist=lambda p: artists[p], cache_file=None)

    def test_three_songs_in_one_crate_decide(self):
        idx = self._index({"House": ["Kyle Walker"] * 3})
        self.assertEqual(ln.vote("Kyle Walker", idx), ("House", 1.0, 3))

    def test_two_songs_are_not_enough(self):
        idx = self._index({"House": ["Kyle Walker"] * 2})
        self.assertIsNone(ln.vote("Kyle Walker", idx))

    def test_a_split_artist_abstains(self):
        idx = self._index({"Bollywood": ["Sidhu"] * 3, "Punjabi": ["Sidhu"] * 3})
        self.assertIsNone(ln.vote("Sidhu", idx))          # 50/50: a taste call, never automatic

    def test_one_stray_song_does_not_outvote(self):
        idx = self._index({"House": ["Dj Z"] * 9, "Pop": ["Dj Z"]})
        self.assertEqual(ln.vote("Dj Z", idx)[0], "House")

    def test_catchall_and_review_folders_never_vote(self):
        idx = self._index({"Electronic": ["Ghost"] * 5, "NeedsReview": ["Ghost"] * 5, "_TO_DELETE": ["Ghost"] * 5})
        self.assertIsNone(ln.vote("Ghost", idx))

    def test_collaboration_counts_for_each_artist(self):
        idx = self._index({"UK Garage": ["Sammy Virji & Flowdan"] * 2 + ["Sammy Virji"]})
        self.assertEqual(ln.vote("Sammy Virji", idx)[0], "UK Garage")
        self.assertEqual(ln.vote("Someone feat. Sammy Virji", idx)[0], "UK Garage")

    def test_placeholder_artists_are_ignored(self):
        idx = self._index({"House": ["Unknown", "", "Electronic", "Unknown"]})
        self.assertEqual(idx, {})

    def test_unreadable_library_gives_empty_index(self):
        self.assertEqual(ln.build_index(os.path.join(tempfile.gettempdir(), "no-such-library-xyz"), cache_file=None), {})

    def test_cache_skips_rereading_unchanged_files(self):
        tmp, artists = _library({"House": ["Kyle Walker"] * 3})
        self.addCleanup(tmp.cleanup)
        cache = os.path.join(tmp.name, "..", os.path.basename(tmp.name) + "_cache.json")
        self.addCleanup(lambda: os.path.exists(cache) and os.remove(cache))
        reader = mock.Mock(side_effect=lambda p: artists[p])
        ln.build_index(tmp.name, read_artist=reader, cache_file=cache)
        self.assertEqual(reader.call_count, 3)
        ln.build_index(tmp.name, read_artist=reader, cache_file=cache)
        self.assertEqual(reader.call_count, 3)            # second run: nothing re-read


class TestLibraryDir(unittest.TestCase):
    def test_points_at_the_configured_library(self):
        from config import config
        self.assertEqual(ln._library_dir(), os.path.join(config.BASE_DOWNLOAD_DIR, "Library"))


class TestNeighbourCrate(unittest.TestCase):
    def test_switch_off(self):
        with mock.patch.dict(os.environ, {"NEIGHBOUR_ROUTING": "false"}):
            self.assertEqual(ln.neighbour_crate("A", index={"a": {"House": 9}}), "")

    def test_answers_from_index(self):
        with mock.patch.dict(os.environ, {"NEIGHBOUR_ROUTING": "true"}):
            self.assertEqual(ln.neighbour_crate("A", index={"a": {"House": 9}}), "House")

    def test_never_raises(self):
        with mock.patch.dict(os.environ, {"NEIGHBOUR_ROUTING": "true"}), \
             mock.patch.object(ln, "_index", side_effect=RuntimeError("disk gone")):
            self.assertEqual(ln.neighbour_crate("A"), "")


def _never(*_a, **_k):
    raise AssertionError("this step must not run")


def _route(**over):
    kw = dict(artist="A", title="T", artist_id="id", sp=None, filepath="x.mp3",
              memory_lookup=lambda a: {}, router=lambda **k: ("NeedsReview/A", 0.2, "uncategorized"),
              evidence=lambda *a: ("", ""), ai=lambda *a: ("", ""), neighbours=lambda a: "")
    kw.update(over)
    return decide_route(**kw)


class TestInDecideRoute(unittest.TestCase):
    def test_neighbours_beat_evidence_and_ai(self):
        r = _route(neighbours=lambda a: "House", evidence=_never, ai=_never)
        self.assertEqual((r.path, r.source, r.tag, r.catchall), ("Library/House", "neighbours", "neighbours", False))

    def test_your_lists_still_win(self):
        r = _route(router=lambda **k: ("Library/Bollywood", 1.0, "artist_override"), neighbours=_never)
        self.assertEqual(r.path, "Library/Bollywood")

    def test_no_neighbours_falls_through_to_evidence(self):
        r = _route(evidence=lambda *a: ("Library/Techno", "evidence:lastfm"))
        self.assertEqual((r.path, r.source), ("Library/Techno", "evidence"))

    def test_lookup_crash_is_contained(self):
        def boom(a):
            raise RuntimeError("x")
        r = _route(neighbours=boom, evidence=lambda *a: ("Library/Techno", "evidence:lastfm"))
        self.assertEqual(r.path, "Library/Techno")

    def test_trance_neighbours_still_become_techno(self):
        r = _route(neighbours=lambda a: "Trance")
        self.assertEqual(r.path, "Library/Techno")


if __name__ == "__main__":
    unittest.main()
