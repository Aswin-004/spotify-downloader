"""
Regression tests for the ROOT CAUSE of the garbage artist tags ("Indian", "Unknown", "Electronic").

services/legacy_identification_service._parse_filename used the parent FOLDER's name as the artist of
any file named just "Song.mp3" — unconditionally. In a genre-organised library the parent folder is
"Indian" / "Electronic" / "Unknown" / "Ingest", so hundreds of files got artist="Indian" and the
Spotify search then asked for `artist:Indian`, returning unrelated tracks with "Indian" in the artist
name (25% of the affected files ended up with a Spotify id for a DIFFERENT song).
"""
import tempfile
import unittest
from pathlib import Path

from services import legacy_identification_service as lid


def parse(folder: str, filename: str):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "Library", folder, filename)
        return lid._parse_filename(p)


class TestParseFilenameNoLongerTrustsGenreFolders(unittest.TestCase):
    def test_genre_crates_and_containers_are_not_artists(self):
        for folder in ("Indian", "Electronic", "Unknown", "Ingest", "Uncategorized", "Punjabi", "Bollywood", "House",
                       "Techno", "Trance", "Hip Hop", "R&B", "Pop", "Latin", "Drum & Bass", "UK Garage", "Dubstep",
                       "Grime", "Tamil", "NeedsReview", "Various Artists", "Library", "Downloads"):
            r = parse(folder, "Kiya Kiya.mp3")
            self.assertEqual(r.artist, "", folder)
            self.assertEqual((r.title, r.confident), ("Kiya Kiya", True), folder)     # the title is still known

    def test_a_real_artist_folder_is_still_used(self):
        for folder in ("Arijit Singh", "Sammy Virji", "Above & Beyond", "Techno Project"):
            self.assertEqual(parse(folder, "Song.mp3").artist, folder, folder)

    def test_artist_dash_title_is_unaffected(self):
        r = parse("Indian", "Arijit Singh - Tum Hi Ho.mp3")
        self.assertEqual((r.artist, r.title), ("Arijit Singh", "Tum Hi Ho"))

    def test_song_dash_edition_is_unaffected(self):
        r = parse("Electronic", "Goodums - Sammy Virji Remix.mp3")
        self.assertEqual((r.artist, r.title, r.edition), ("", "Goodums", "Sammy Virji Remix"))


class TestFolderIsNotAnArtist(unittest.TestCase):
    def test_names(self):
        for name in ("Indian", "  indian ", "UNKNOWN", "Hip Hop", "hip-hop", "R&B", "Drum & Bass", "Ingest", "",
                     "Electronic", "Indian Marathi", "dj music"):
            self.assertTrue(lid._folder_is_not_an_artist(name), repr(name))
        for name in ("Arijit Singh", "Pritam", "Kova", "Pop Smoke", "House of Pain", "Electronic Sheep", "Indian Ocean"):
            self.assertFalse(lid._folder_is_not_an_artist(name), repr(name))


class TestFolderContextHint(unittest.TestCase):
    def ctx(self, folder_name, files=("a.mp3", "b.mp3")):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d, folder_name)
            folder.mkdir()
            for f in files:
                (folder / f).write_bytes(b"x")
            return lid.infer_parent_folder_context(folder)

    def test_a_container_folder_gives_no_artist_hint(self):
        for name in ("Unknown", "Ingest", "Uncategorized", "Pop", "Latin"):
            self.assertEqual(self.ctx(name)["artist_hint"], "", name)

    def test_a_real_artist_folder_is_still_a_loose_hint(self):
        c = self.ctx("Nucleya")
        self.assertEqual((c["artist_hint"], c["folder_type"]), ("Nucleya", "artist"))

    def test_numbered_album_under_a_genre_crate_has_no_artist_hint(self):
        with tempfile.TemporaryDirectory() as d:
            album = Path(d, "Indian", "Some Album")
            album.mkdir(parents=True)
            for f in ("01 - A.mp3", "02 - B.mp3", "03 - C.mp3"):
                (album / f).write_bytes(b"x")
            c = lid.infer_parent_folder_context(album)
            self.assertEqual((c["folder_type"], c["artist_hint"]), ("album", ""))
            album2 = Path(d, "Arijit Singh", "Aashiqui 2")
            album2.mkdir(parents=True)
            for f in ("01 - A.mp3", "02 - B.mp3", "03 - C.mp3"):
                (album2 / f).write_bytes(b"x")
            self.assertEqual(lid.infer_parent_folder_context(album2)["artist_hint"], "Arijit Singh")


if __name__ == "__main__":
    unittest.main()
