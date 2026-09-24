"""
Tests for the two hip hop crates: Indian Hip Hop (rappers from India) and International Hip Hop (everyone else).

Hip hop tags are the same words for Seedhe Maut and Kendrick Lamar, so tags alone cannot pick the crate. The rule
under test: generic hip hop evidence is counted for the INDIAN crate when the artist shows any Indian signal (a
curated entry, Indian tags, Indian script), and stays international otherwise. Weak signals and words in a title are
deliberately not enough to reclassify an artist, and a Punjabi-language rapper — a genuine taste boundary — must
never be pushed into the international crate.
"""
import os
import unittest
from unittest import mock

from services import artist_recovery
from services import auto_downloader as ad
from services import genre_evidence as ge
from services import genre_router as gr
from services.genre_evidence import Vote
from tests.test_genre_evidence import UNKNOWN_ARTIST, FakeFetchers, lfm

INTL, IND = ge.HIP_HOP_INTERNATIONAL, ge.HIP_HOP_INDIAN


def setUpModule():
    global _env
    _env = mock.patch.dict("os.environ")
    _env.start()
    os.environ.pop("GENRE_EVIDENCE_MIN_CONFIDENCE", None)


def tearDownModule():
    _env.stop()


def labels(votes):
    return [v.label for v in votes]


class TestSplitRule(unittest.TestCase):
    def test_votes_without_generic_hip_hop_are_untouched(self):
        votes = [Vote("lastfm_track", "House", 2.0), Vote("script", "Bollywood", 0.8)]
        self.assertEqual(ge.split_hip_hop(votes), votes)

    def test_without_an_indian_signal_it_stays_international(self):
        votes = [Vote("lastfm_artist", INTL, 1.6), Vote("lastfm_track", INTL, 2.0)]
        self.assertEqual(labels(ge.split_hip_hop(votes)), [INTL, INTL])

    def test_an_indian_tag_moves_the_generic_hip_hop_votes_to_the_indian_crate(self):
        votes = [Vote("lastfm_artist", INTL, 1.0), Vote("lastfm_artist", "Bollywood", 0.6)]
        out = ge.split_hip_hop(votes)
        self.assertEqual(labels(out), [IND, "Bollywood"])
        self.assertEqual(out[0].weight, 1.0)                     # only the label changes, never the weight

    def test_a_curated_indian_entry_counts(self):
        out = ge.split_hip_hop([Vote("lastfm_track", INTL, 2.0), Vote("artist_override", "Punjabi", 2.5)])
        self.assertEqual(labels(out)[0], IND)

    def test_indian_script_counts(self):
        out = ge.split_hip_hop([Vote("lastfm_track", INTL, 2.0), Vote("script", "Bollywood", 0.8)])
        self.assertEqual(labels(out)[0], IND)

    def test_a_word_in_the_title_or_a_remixers_crate_is_not_the_artists_origin(self):
        for source in ("title_hint", "remixer"):
            out = ge.split_hip_hop([Vote("lastfm_track", INTL, 2.0), Vote(source, "Bollywood", 1.5)])
            self.assertEqual(labels(out)[0], INTL, source)

    def test_a_faint_indian_signal_is_not_enough(self):
        out = ge.split_hip_hop([Vote("lastfm_artist", INTL, 2.0), Vote("lastfm_artist", "Bollywood", 0.1)])
        self.assertEqual(labels(out)[0], INTL)

    def test_the_input_list_is_not_mutated(self):
        votes = [Vote("lastfm_track", INTL, 2.0), Vote("script", "Bollywood", 0.8)]
        ge.split_hip_hop(votes)
        self.assertEqual(labels(votes), [INTL, "Bollywood"])


class TestIsIndianArtist(unittest.TestCase):
    def test_indian_tags_mean_indian(self):
        f = FakeFetchers(lfm_artist=lfm([("dhh", 100), ("hip-hop", 80), ("india", 50)]))
        self.assertTrue(ge.is_indian_artist(UNKNOWN_ARTIST, "Some Song", fetchers=f))

    def test_plain_hip_hop_tags_mean_international(self):
        f = FakeFetchers(lfm_artist=lfm([("hip-hop", 100), ("rap", 90), ("trap", 70)]))
        self.assertFalse(ge.is_indian_artist(UNKNOWN_ARTIST, "Some Song", fetchers=f))

    def test_curated_lists_answer_even_when_last_fm_knows_nothing(self):
        f = FakeFetchers()
        self.assertTrue(ge.is_indian_artist("Divine", "Some Song", fetchers=f))          # override table: Indian Hip Hop
        self.assertFalse(ge.is_indian_artist("Kendrick Lamar", "Some Song", fetchers=f))  # override table: hip hop

    def test_no_data_and_broken_sources_are_never_indian(self):
        self.assertFalse(ge.is_indian_artist(UNKNOWN_ARTIST, "Some Song", fetchers=FakeFetchers()))
        boom = FakeFetchers(boom=("lfm_track", "lfm_artist", "mb"))
        self.assertFalse(ge.is_indian_artist(UNKNOWN_ARTIST, "Some Song", fetchers=boom))


class TestDecisionsEndToEnd(unittest.TestCase):
    def decide(self, **kw):
        return ge.classify_track(UNKNOWN_ARTIST, "Some Song", online=True, use_musicbrainz=False,
                                 fetchers=FakeFetchers(**kw))

    def test_an_indian_rapper_lands_in_indian_hip_hop_and_it_is_verified(self):
        d = self.decide(lfm_track=lfm([("dhh", 100), ("hip-hop", 100)]), lfm_artist=lfm([("dhh", 90), ("india", 40)]))
        self.assertEqual((d.abstain, d.genre, d.verified), (False, IND, True))

    def test_an_international_rapper_lands_in_international_hip_hop(self):
        d = self.decide(lfm_track=lfm([("hip-hop", 100), ("rap", 90)]), lfm_artist=lfm([("hip-hop", 90), ("trap", 60)]))
        self.assertEqual((d.abstain, d.genre, d.verified), (False, INTL, True))

    def test_a_punjabi_language_rapper_is_never_called_international(self):
        """Punjabi vs Indian Hip Hop is a taste boundary: the engine may abstain, but must not export him."""
        d = self.decide(lfm_track=lfm([("punjabi", 100), ("hip-hop", 100)]))
        self.assertNotEqual(d.genre, INTL)

    def test_curated_indian_rappers_route_to_their_crate_without_any_network(self):
        d = ge.classify_track("Seedhe Maut", "Some Song", online=False)
        self.assertEqual((d.genre, d.verified), (IND, True))
        d = ge.classify_track("Kendrick Lamar", "Some Song", online=False)
        self.assertEqual((d.genre, d.verified), (INTL, True))


class TestUserDecisions(unittest.TestCase):
    """Choices the user made about specific artists, so a later edit cannot silently undo them."""

    def test_nucleya_is_filed_with_the_bass_artists_not_with_the_rappers(self):
        for offline_only in (True,):
            d = ge.classify_track("Nucleya", "Some Song", online=not offline_only)
            self.assertEqual((d.genre, d.verified), ("Dubstep", True))
        self.assertEqual(ge.classify_track("Skrillex", "Some Song", online=False).genre, "Dubstep")   # same crate

    def test_talha_anjum_is_indian_hip_hop_and_desi_hip_hop_is_the_same_crate(self):
        d = ge.classify_track("Talha Anjum", "Some Song", online=False)
        self.assertEqual((d.genre, d.verified), (IND, True))
        self.assertEqual(gr._library_path("Desi Hip Hop"), gr._library_path("Indian Hip Hop"))


class TestFoldersAndNames(unittest.TestCase):
    def test_the_taxonomy_has_both_crates_and_no_plain_hip_hop_folder(self):
        self.assertEqual(gr._library_path("Hip Hop"), "Library/International Hip Hop")
        self.assertEqual(gr._library_path("Indian Hip Hop"), "Library/Indian Hip Hop")
        self.assertEqual(gr._library_path("Desi Hip Hop"), "Library/Indian Hip Hop")
        self.assertNotIn("Hip Hop", {v[1] for v in gr.GENRE_TAXONOMY.values()})

    def test_indian_hip_hop_words_from_any_source_reach_the_indian_crate(self):
        for word in ("desi hip hop", "Indian Hip-Hop", "indian hip hop", "dhh", "hindi rap", "Desi Hip-Hop"):
            self.assertEqual(gr._library_path(gr.normalize_genre(word)), "Library/Indian Hip Hop", word)
        self.assertEqual(gr.map_genre_string("desi hip hop"), "Library/Indian Hip Hop")
        self.assertEqual(gr.normalize_genre("hip hop"), "Hip Hop")            # generic = the international crate

    def test_a_song_in_a_hip_hop_folder_is_never_mistaken_for_an_artist(self):
        from services.legacy_identification_service import _folder_is_not_an_artist
        for name in ("Indian Hip Hop", "International Hip Hop", "indian hip hop", "Hip Hop"):
            self.assertTrue(artist_recovery.is_placeholder_artist(name), name)
            self.assertTrue(_folder_is_not_an_artist(name), name)


class TestIngestStep(unittest.TestCase):
    def test_the_download_step_asks_about_origin_only_when_hip_hop_was_decided_by_something_else(self):
        self.assertEqual(ad._hip_hop_folder("A", "T", is_indian=lambda a, t: True), "Library/Indian Hip Hop")
        self.assertEqual(ad._hip_hop_folder("A", "T", is_indian=lambda a, t: False), "Library/International Hip Hop")

    def test_a_failing_origin_check_falls_back_to_international(self):
        def boom(a, t):
            raise RuntimeError("Last.fm down")
        self.assertEqual(ad._hip_hop_folder("A", "T", is_indian=boom), "Library/International Hip Hop")

    def test_the_split_never_overrides_a_folder_the_user_chose(self):
        src = open(ad.__file__, encoding="utf-8").read()
        block = src[src.index("# Hip hop has two crates."):src.index("final_folder = os.path.normpath(final_folder)")]
        self.assertIn("not force_folder and not _forced_folder", block)
        self.assertIn('endswith("/International Hip Hop")', block)


if __name__ == "__main__":
    unittest.main()
