"""
Tests for services/genre_playlists.py — "a song added to my 'DJ - House' playlist is filed in House".

The rules under test are the ones that make that promise safe: a bad config can never invent a folder,
the genre playlist wins over the plain ingest playlist, and nothing is mutated behind the caller's back.
"""
import os
import tempfile
import unittest

from services import genre_playlists as gp

ID_HOUSE = "3IYpUC5bEQZ2dyCh7YW9VQ"
ID_PUNJABI = "526KoGPgom0SS3jykMBPCE"


def _t(i, title="Song"):
    return {"id": i, "title": title, "artist": "A", "artist_id": "x", "duration_ms": 1000}


class TestPlaylistIds(unittest.TestCase):
    def test_accepts_the_shapes_people_paste(self):
        self.assertEqual(gp.extract_playlist_id(ID_HOUSE), ID_HOUSE)
        self.assertEqual(gp.extract_playlist_id(f"https://open.spotify.com/playlist/{ID_HOUSE}?pi=5_vn4Pe7S1Gjn"), ID_HOUSE)
        self.assertEqual(gp.extract_playlist_id(f"https://open.spotify.com/playlist/{ID_HOUSE}/"), ID_HOUSE)
        self.assertEqual(gp.extract_playlist_id(f"spotify:playlist:{ID_HOUSE}"), ID_HOUSE)
        self.assertEqual(gp.extract_playlist_id(f"  {ID_HOUSE}  "), ID_HOUSE)

    def test_rejects_things_that_are_not_playlists(self):
        for bad in ("", "hello", "https://open.spotify.com/track/abc", "https://example.com/x?y=1", None):
            self.assertEqual(gp.extract_playlist_id(bad), "", bad)


class TestCrates(unittest.TestCase):
    def test_real_crates_are_valid_and_the_catch_all_is_not(self):
        crates = gp.valid_crates()
        for c in ("House", "UK Garage", "Drum & Bass", "Punjabi", "Bollywood", "Indian Hip Hop", "International Hip Hop"):
            self.assertIn(c, crates)
        self.assertNotIn("Hip Hop", crates)             # split into the Indian and international crates
        self.assertNotIn("Electronic", crates)          # the catch-all is where UNKNOWN songs go, not a genre to choose

    def test_both_hip_hop_crates_can_be_chosen_by_the_names_people_type(self):
        self.assertEqual(gp.resolve_crate("indian hip hop"), "Indian Hip Hop")
        self.assertEqual(gp.resolve_crate("Desi Hip Hop"), "Indian Hip Hop")
        self.assertEqual(gp.resolve_crate("international hip hop"), "International Hip Hop")
        self.assertEqual(gp.resolve_crate("hip hop"), "International Hip Hop")     # plain hip hop = everyone outside India
        self.assertEqual(gp.crate_folder("Indian Hip Hop"), "Library/Indian Hip Hop")

    def test_names_people_type_resolve_to_the_crate_label(self):
        self.assertEqual(gp.resolve_crate("house"), "House")
        self.assertEqual(gp.resolve_crate("  HOUSE "), "House")
        self.assertEqual(gp.resolve_crate("Tech House"), "House")       # the config maps tech house into House
        self.assertEqual(gp.resolve_crate("uk garage"), "UK Garage")
        self.assertEqual(gp.resolve_crate("Drum and Bass"), "Drum & Bass")

    def test_unknown_or_catch_all_names_do_not_resolve(self):
        for bad in ("", "banana-core", "electronic", None):
            self.assertEqual(gp.resolve_crate(bad), "", bad)

    def test_crate_folder_never_invents_a_folder(self):
        self.assertEqual(gp.crate_folder("House"), "Library/House")
        self.assertEqual(gp.crate_folder("Nope"), "")
        self.assertEqual(gp.crate_folder(""), "")
        self.assertEqual(gp.crate_folder("Electronic"), "")


class TestConfigFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "genre_playlists.json")

    def test_missing_file_is_no_playlists(self):
        self.assertEqual(gp.load(self.path), [])

    def test_add_load_remove_round_trip(self):
        e = gp.add(f"https://open.spotify.com/playlist/{ID_HOUSE}?pi=zzz", "house", name="DJ - House", path=self.path)
        self.assertEqual((e["id"], e["crate"], e["name"]), (ID_HOUSE, "House", "DJ - House"))
        self.assertEqual([x["id"] for x in gp.load(self.path)], [ID_HOUSE])
        self.assertTrue(gp.remove(ID_HOUSE, path=self.path))
        self.assertEqual(gp.load(self.path), [])
        self.assertFalse(gp.remove(ID_HOUSE, path=self.path))

    def test_adding_the_same_playlist_again_re_points_it(self):
        gp.add(ID_HOUSE, "house", path=self.path)
        gp.add(ID_HOUSE, "techno", path=self.path)
        entries = gp.load(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["crate"], "Techno")

    def test_bad_input_is_refused_with_a_reason(self):
        with self.assertRaisesRegex(ValueError, "playlist"):
            gp.add("not a link", "house", path=self.path)
        with self.assertRaisesRegex(ValueError, "not one of your crates"):
            gp.add(ID_HOUSE, "electronic", path=self.path)
        self.assertEqual(gp.load(self.path), [])

    def test_a_hand_edited_bad_crate_is_ignored_not_trusted(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"playlists": [{"id": "%s", "crate": "../../Windows"}, {"id": "%s", "crate": "House"}]}' % (ID_HOUSE, ID_PUNJABI))
        self.assertEqual([e["id"] for e in gp.load(self.path)], [ID_PUNJABI])

    def test_corrupt_file_is_no_playlists(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{oops")
        self.assertEqual(gp.load(self.path), [])


class TestMerge(unittest.TestCase):
    HOUSE = {"id": ID_HOUSE, "crate": "House"}
    PUNJABI = {"id": ID_PUNJABI, "crate": "Punjabi"}

    def test_a_genre_playlist_song_carries_its_crate(self):
        out = gp.merge_tracks([], [(self.HOUSE, [_t("a")])])
        self.assertEqual((out[0]["forced_genre"], out[0]["source_playlist"]), ("House", ID_HOUSE))

    def test_genre_playlist_wins_over_the_plain_ingest_playlist(self):
        out = gp.merge_tracks([_t("a"), _t("b")], [(self.HOUSE, [_t("b")])])
        by_id = {t["id"]: t for t in out}
        self.assertNotIn("forced_genre", by_id["a"])
        self.assertEqual(by_id["b"]["forced_genre"], "House")
        self.assertEqual([t["id"] for t in out], ["a", "b"])           # no duplicate, ingest order kept

    def test_genre_only_songs_are_appended(self):
        out = gp.merge_tracks([_t("a")], [(self.HOUSE, [_t("b")])])
        self.assertEqual([t["id"] for t in out], ["a", "b"])

    def test_two_genre_playlists_first_one_wins(self):
        out = gp.merge_tracks([], [(self.HOUSE, [_t("a")]), (self.PUNJABI, [_t("a")])])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["forced_genre"], "House")

    def test_inputs_are_not_mutated(self):
        main, genre = [_t("a")], [_t("a")]
        gp.merge_tracks(main, [(self.HOUSE, genre)])
        self.assertNotIn("forced_genre", main[0])
        self.assertNotIn("forced_genre", genre[0])

    def test_songs_without_an_id_are_skipped(self):
        self.assertEqual(gp.merge_tracks([{"title": "x"}], [(self.HOUSE, [{"title": "y"}])]), [])


if __name__ == "__main__":
    unittest.main()
