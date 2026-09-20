"""
Tests for services/genre_evidence.py — the evidence-voting genre classifier.

The decision rule is pure (votes in, Decision out) so its thresholds are pinned with exact
numbers; the signals are driven through an injected fake `fetchers`, so nothing here touches
the network. The scenarios mirror the failures that motivated the module: a Techno DJ's track
filed as House, a Bollywood song remixed as Techno, a single weak source deciding alone, and a
Last.fm autocorrect answering for the wrong track.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services import genre_evidence as ge
from services.genre_evidence import Decision, TagCache, Vote

UNKNOWN_ARTIST = "Zzyzx Qwerty"          # in no override table and no knowledge base

_env_patch = None


def setUpModule():
    """The answer cut-off can come from the environment; these tests pin the built-in default."""
    global _env_patch
    _env_patch = mock.patch.dict("os.environ")
    _env_patch.start()
    os.environ.pop("GENRE_EVIDENCE_MIN_CONFIDENCE", None)


def tearDownModule():
    _env_patch.stop()


class FakeFetchers:
    """Stands in for LiveFetchers; records every call so tests can prove what was (not) asked."""

    def __init__(self, lfm_track=None, lfm_artist=None, mb=None, fp="", llm="", boom=()):
        self.lfm_track, self.lfm_artist, self.mb, self.fp, self.llm = lfm_track, lfm_artist, mb, fp, llm
        self.boom = set(boom)
        self.calls = []

    def _call(self, name, value):
        self.calls.append(name)
        if name in self.boom:
            raise RuntimeError(f"{name} exploded")
        return value

    def lastfm_track_tags(self, artist, title):   return self._call("lfm_track", self.lfm_track)
    def lastfm_artist_tags(self, artist):         return self._call("lfm_artist", self.lfm_artist)
    def musicbrainz_tags(self, artist, title):    return self._call("mb", self.mb)
    def fingerprint_genre(self, path):            return self._call("fp", self.fp)
    def llm_genre(self, path):                    return self._call("llm", self.llm)


def lfm(tags, artist=UNKNOWN_ARTIST, track="Some Song"):
    return {"tags": tags, "artist": artist, "track": track}


class TestTagLabels(unittest.TestCase):
    def test_electronic_subgenres(self):
        self.assertEqual(ge.tag_labels("melodic techno"), {"Techno": 1.0})
        self.assertEqual(ge.tag_labels("Progressive Trance"), {"Trance": 1.0})
        self.assertEqual(ge.tag_labels("psytrance"), {"Trance": 1.0})
        self.assertEqual(ge.tag_labels("deep house"), {"House": 1.0})
        self.assertEqual(ge.tag_labels("2-step"), {"UK Garage": 1.0})
        self.assertEqual(ge.tag_labels("dubstep"), {"Dubstep": 1.0})

    def test_tech_house_is_house_not_techno(self):
        self.assertEqual(ge.tag_labels("tech house"), {"House": 1.0})
        self.assertEqual(ge.tag_labels("tech-house"), {"House": 1.0})

    def test_a_tag_naming_two_genres_supports_both(self):
        self.assertEqual(set(ge.tag_labels("melodic house and techno")), {"House", "Techno"})

    def test_drum_and_bass_spellings(self):
        for tag in ("drum and bass", "Drum & Bass", "drum'n'bass", "drum n bass", "dnb", "D&B", "neurofunk"):
            self.assertEqual(ge.tag_labels(tag), {"Drum & Bass": 1.0}, tag)

    def test_generic_and_junk_tags_say_nothing(self):
        for tag in ("electronic", "edm", "dance", "electro", "ambient", "seen live", "favorites", "female vocalists"):
            self.assertEqual(ge.tag_labels(tag), {}, tag)

    def test_word_boundaries(self):
        self.assertEqual(ge.tag_labels("entrance"), {})                 # not "trance"
        self.assertEqual(ge.tag_labels("technology"), {})               # not "techno"
        self.assertEqual(ge.tag_labels("warehouse rave"), {})           # not "house"

    def test_indian_tags_and_strengths(self):
        self.assertEqual(ge.tag_labels("bollywood"), {"Bollywood": 1.0})
        self.assertEqual(ge.tag_labels("desi"), {"Bollywood": 0.8})
        self.assertEqual(ge.tag_labels("indian"), {"Bollywood": 0.6})
        self.assertEqual(ge.tag_labels("punjabi"), {"Punjabi": 1.0})
        self.assertEqual(ge.tag_labels("bhangra"), {"Punjabi": 1.0})
        self.assertEqual(ge.tag_labels("tamil"), {"Tamil": 1.0})
        self.assertEqual(ge.tag_labels("hip-hop"), {"Hip Hop": 0.9})

    def test_soul_is_a_weak_rnb_hint_because_it_also_tags_indian_film_songs(self):
        self.assertEqual(ge.tag_labels("soul"), {"R&B": 0.4})
        self.assertEqual(ge.tag_labels("rnb"), {"R&B": 0.9})
        self.assertEqual(ge.tag_labels("neo soul"), {"R&B": 0.4})


class TestVotesFromTags(unittest.TestCase):
    def test_unanimous_track_gets_the_full_weight(self):
        votes = ge.votes_from_tags("lastfm_track", [("techno", 100), ("electronic", 80)], 2.0, 60)
        self.assertEqual([(v.label, round(v.weight, 3)) for v in votes], [("Techno", 2.0)])

    def test_weight_is_shared_in_proportion_to_tag_mass(self):
        votes = {v.label: v.weight for v in ge.votes_from_tags("s", [("techno", 100), ("house", 50)], 2.0, 60)}
        self.assertAlmostEqual(votes["Techno"], 2.0 * 100 / 150, places=3)
        self.assertAlmostEqual(votes["House"], 2.0 * 50 / 150, places=3)

    def test_thin_community_agreement_speaks_quietly(self):
        (v,) = ge.votes_from_tags("s", [("techno", 30)], 2.0, 60)
        self.assertAlmostEqual(v.weight, 1.0, places=3)                 # 30/60 reliability
        (v,) = ge.votes_from_tags("mb", [("techno", 1)], 1.5, 3)
        self.assertAlmostEqual(v.weight, 0.5, places=3)                 # one MusicBrainz voter of three

    def test_faint_tags_are_ignored(self):
        votes = ge.votes_from_tags("s", [("techno", 100), ("house", 20)], 2.0, 60)   # 20% < MIN_TAG_REL
        self.assertEqual([v.label for v in votes], ["Techno"])

    def test_nothing_to_vote_on(self):
        self.assertEqual(ge.votes_from_tags("s", [], 2.0, 60), [])
        self.assertEqual(ge.votes_from_tags("s", [("techno", 0)], 2.0, 60), [])
        self.assertEqual(ge.votes_from_tags("s", [("seen live", 90), ("edm", 80)], 2.0, 60), [])

    def test_accepts_lists_from_a_json_cache(self):
        (v,) = ge.votes_from_tags("s", [["techno", 100]], 2.0, 60)
        self.assertEqual(v.label, "Techno")


class TestBpmFactor(unittest.TestCase):
    def test_fits_the_label(self):
        self.assertEqual(ge.bpm_factor("House", 128), 1.0)
        self.assertEqual(ge.bpm_factor("Techno", 140), 1.0)
        self.assertEqual(ge.bpm_factor("Drum & Bass", 174), 1.0)

    def test_half_and_double_time_detection_errors_are_tolerated(self):
        self.assertEqual(ge.bpm_factor("House", 64), 1.0)               # 128 detected at half time
        self.assertEqual(ge.bpm_factor("Drum & Bass", 87), 1.0)
        self.assertEqual(ge.bpm_factor("House", 250), 1.0)              # 125 detected at double time

    def test_implausible_tempo_is_penalised(self):
        self.assertEqual(ge.bpm_factor("House", 174), ge.BPM_FAR_FACTOR)
        self.assertEqual(ge.bpm_factor("Techno", 100), ge.BPM_FAR_FACTOR)
        self.assertEqual(ge.bpm_factor("Techno", 112), ge.BPM_NEAR_FACTOR)   # 6 below the range

    def test_labels_without_a_tempo_range_and_unknown_bpm_are_untouched(self):
        self.assertEqual(ge.bpm_factor("Bollywood", 90), 1.0)
        self.assertEqual(ge.bpm_factor("House", None), 1.0)
        self.assertEqual(ge.bpm_factor("House", 0), 1.0)

    def test_user_measured_range_replaces_the_default(self):
        priors = {"House": [120, 126]}
        self.assertEqual(ge.bpm_factor("House", 122, priors), 1.0)
        self.assertEqual(ge.bpm_factor("House", 130, priors), ge.BPM_NEAR_FACTOR)


class TestDecide(unittest.TestCase):
    def test_no_votes_abstains(self):
        d = ge.decide([])
        self.assertTrue(d.abstain)
        self.assertEqual(d.genre, "")
        self.assertIn("no evidence", d.reason)

    def test_a_curated_override_is_enough_on_its_own(self):
        d = ge.decide([Vote("artist_override", "Techno", ge.W_OVERRIDE)])
        self.assertFalse(d.abstain)
        self.assertEqual((d.genre, d.confidence), ("Techno", 1.0))
        self.assertEqual(d.sources, ["artist_override"])

    def test_knowledge_base_alone_is_enough(self):
        d = ge.decide([Vote("knowledge_base", "Bollywood", ge.W_KNOWLEDGE_BASE * 0.85)])
        self.assertFalse(d.abstain)
        self.assertAlmostEqual(d.confidence, 0.68, places=2)

    def test_a_title_word_alone_never_decides(self):
        d = ge.decide([Vote("title_hint", "Techno", ge.W_TITLE_HINT)])
        self.assertTrue(d.abstain)
        self.assertIn("not enough evidence", d.reason)

    def test_the_weak_llm_guess_never_decides_alone(self):
        self.assertTrue(ge.decide([Vote("llm", "House", ge.W_LLM)]).abstain)

    def test_close_race_is_a_conflict_not_a_coin_flip(self):
        d = ge.decide([Vote("artist_override", "Techno", 2.5), Vote("lastfm_track", "House", 2.0)])
        self.assertTrue(d.abstain)
        self.assertIn("conflicting", d.reason)

    def test_clear_winner_over_weak_dissent_still_answers(self):
        d = ge.decide([Vote("artist_override", "Techno", 2.5), Vote("lastfm_track", "House", 1.0)])
        self.assertEqual(d.genre, "Techno")
        self.assertAlmostEqual(d.confidence, 2.5 / 3.5, places=2)

    def test_agreeing_sources_add_up(self):
        d = ge.decide([Vote("lastfm_track", "Trance", 2.0), Vote("musicbrainz", "Trance", 1.5),
                       Vote("lastfm_artist", "Trance", 1.0)])
        self.assertEqual(d.confidence, 1.0)
        self.assertEqual(d.sources, ["lastfm_artist", "lastfm_track", "musicbrainz"])

    def test_bpm_breaks_a_tie_between_house_and_trance(self):
        votes = [Vote("lastfm_track", "House", 2.0), Vote("musicbrainz", "Trance", 1.9)]
        self.assertTrue(ge.decide(votes).abstain)                       # no tempo: a genuine conflict
        d = ge.decide(votes, bpm=145, bpm_ranges=ge.DEFAULT_BPM_RANGES)
        self.assertEqual(d.genre, "Trance")                             # 145 BPM is not House
        self.assertLess(d.scores["House"], d.scores["Trance"])

    def test_tempo_never_overrules_a_curated_source(self):
        # Skrillex's "Bangarang" is 110 BPM — far from any tidy dubstep range — and must still be Dubstep.
        for source in ge.BPM_EXEMPT_SOURCES:
            d = ge.decide([Vote(source, "Dubstep", 2.5)], bpm=110, bpm_ranges=ge.DEFAULT_BPM_RANGES)
            self.assertEqual((d.genre, d.confidence), ("Dubstep", 1.0), source)
        weak = ge.decide([Vote("lastfm_track", "Dubstep", 2.5)], bpm=110, bpm_ranges=ge.DEFAULT_BPM_RANGES)
        self.assertTrue(weak.abstain)                                    # uncurated evidence IS tempered

    def test_a_curated_vote_keeps_its_weight_when_uncurated_ones_are_tempered(self):
        votes = [Vote("artist_override", "Dubstep", 2.5), Vote("lastfm_artist", "Dubstep", 1.6)]
        d = ge.decide(votes, bpm=110, bpm_ranges=ge.DEFAULT_BPM_RANGES)
        self.assertAlmostEqual(d.scores["Dubstep"], 2.5 + 1.6 * ge.BPM_FAR_FACTOR, places=3)

    def test_decision_explains_itself(self):
        self.assertIn("Techno", ge.decide([Vote("artist_override", "Techno", 2.5)]).explain())
        self.assertIn("abstain", ge.decide([]).explain())

    def test_verified_needs_a_trusted_source_behind_the_winner(self):
        for source in ("artist_override", "knowledge_base", "lastfm_track", "musicbrainz", "fingerprint", "remixer"):
            d = ge.decide([Vote(source, "Techno", 2.5)])
            self.assertTrue(d.verified, source)
        for source in ("lastfm_artist", "script", "title_hint", "llm"):
            d = ge.decide([Vote(source, "Techno", 2.5)])
            self.assertFalse(d.verified, source)                        # answered, but only as an inference

    def test_an_abstention_is_never_verified(self):
        self.assertFalse(ge.decide([]).verified)

    def test_a_trusted_source_on_the_losing_side_does_not_verify_the_winner(self):
        d = ge.decide([Vote("lastfm_artist", "Techno", 2.5), Vote("musicbrainz", "House", 0.3)])
        self.assertEqual(d.genre, "Techno")
        self.assertFalse(d.verified)

    def test_the_cutoff_is_adjustable(self):
        votes = [Vote("lastfm_track", "Techno", 1.6)]                   # confidence 0.64
        self.assertFalse(ge.decide(votes).abstain)
        self.assertTrue(ge.decide(votes, min_confidence=0.9).abstain)
        self.assertFalse(ge.decide(votes, min_confidence=0.3).abstain)

    def test_the_cutoff_can_come_from_the_environment(self):
        votes = [Vote("lastfm_track", "Techno", 1.6)]
        with mock.patch.dict("os.environ", {"GENRE_EVIDENCE_MIN_CONFIDENCE": "0.9"}):
            self.assertTrue(ge.decide(votes).abstain)
            self.assertFalse(ge.decide(votes, min_confidence=0.3).abstain)   # explicit beats env
        with mock.patch.dict("os.environ", {"GENRE_EVIDENCE_MIN_CONFIDENCE": "not a number"}):
            self.assertFalse(ge.decide(votes).abstain)                       # falls back to the default


class TestClassifyTrackOffline(unittest.TestCase):
    def test_known_techno_artist(self):
        d = ge.classify_track("Charlotte de Witte", "Anything")
        self.assertEqual((d.genre, d.abstain), ("Techno", False))

    def test_feature_credit_falls_back_to_the_individual_artist(self):
        d = ge.classify_track(f"Charlotte de Witte & {UNKNOWN_ARTIST}", "Anything")
        self.assertEqual(d.genre, "Techno")

    def test_catch_all_override_is_not_evidence(self):
        d = ge.classify_track("Hardwell", "Anything")                   # overridden to "Electronic"
        self.assertTrue(d.abstain)
        self.assertEqual(d.votes, [])

    def test_unknown_artist_abstains_rather_than_guessing(self):
        d = ge.classify_track(UNKNOWN_ARTIST, "Some Song")
        self.assertTrue(d.abstain)

    def test_remix_takes_the_remixers_genre(self):
        d = ge.classify_track(UNKNOWN_ARTIST, "Some Song (Charlotte de Witte Remix)")
        self.assertIn(("remixer", "Techno"), [(v.source, v.label) for v in d.votes])

    def test_non_latin_script_is_a_hint_but_not_a_verdict(self):
        d = ge.classify_track("अरिजीत सिंह Zzyzx", "Some Song")
        self.assertIn(("script", "Bollywood"), [(v.source, v.label) for v in d.votes])
        self.assertTrue(d.abstain)

    def test_offline_mode_never_touches_a_fetcher(self):
        f = FakeFetchers(lfm_track=lfm([("techno", 100)]))
        ge.classify_track(UNKNOWN_ARTIST, "Some Song", online=False, fetchers=f)
        self.assertEqual(f.calls, [])

    def test_bpm_alone_is_never_a_vote(self):
        d = ge.classify_track(UNKNOWN_ARTIST, "Some Song", bpm=174)
        self.assertEqual(d.votes, [])

    def test_garbage_input_fails_closed(self):
        d = ge.classify_track(None, None)
        self.assertTrue(d.abstain)
        self.assertIsInstance(d, Decision)

    def test_engine_error_becomes_an_abstention(self):
        with mock.patch.object(ge, "collect_votes", side_effect=RuntimeError("boom")):
            d = ge.classify_track("A", "B")
        self.assertTrue(d.abstain)
        self.assertIn("error", d.reason)


class TestClassifyTrackOnline(unittest.TestCase):
    def _classify(self, fetchers, **kw):
        return ge.classify_track(UNKNOWN_ARTIST, "Some Song", online=True, fetchers=fetchers, **kw)

    def test_verified_lastfm_track_tags_can_decide(self):
        d = self._classify(FakeFetchers(lfm_track=lfm([("techno", 100), ("electronic", 80)])))
        self.assertEqual(d.genre, "Techno")
        self.assertAlmostEqual(d.confidence, 0.8, places=2)

    def test_lastfm_answer_with_no_resolved_name_is_discounted_and_not_enough_alone(self):
        d = self._classify(FakeFetchers(lfm_track=lfm([("techno", 100)], artist="", track="")))
        self.assertTrue(d.abstain)

    def test_lastfm_autocorrecting_to_another_track_is_discarded(self):
        d = self._classify(FakeFetchers(lfm_track=lfm([("house", 100)], track="A Totally Different Song")))
        self.assertTrue(d.abstain)
        self.assertEqual(d.votes, [])

    def test_lastfm_autocorrecting_to_another_artist_is_discarded(self):
        d = self._classify(FakeFetchers(lfm_track=lfm([("house", 100)], artist="Someone Else Entirely")))
        self.assertEqual(d.votes, [])

    def test_subset_title_is_not_the_same_song(self):
        d = ge.classify_track(UNKNOWN_ARTIST, "Takes", online=True,
                              fetchers=FakeFetchers(lfm_track=lfm([("house", 100)], track="Takes Me Home")))
        self.assertEqual(d.votes, [])

    def test_lone_musicbrainz_vote_is_too_weak(self):
        mb = {"tags": [{"name": "techno", "count": 1}], "title": "Some Song", "artist": UNKNOWN_ARTIST}
        self.assertTrue(self._classify(FakeFetchers(mb=mb)).abstain)

    def test_sources_agreeing_beat_any_single_one(self):
        f = FakeFetchers(lfm_track=lfm([("trance", 100), ("progressive trance", 60)]),
                         lfm_artist={"tags": [("trance", 100)], "artist": UNKNOWN_ARTIST},
                         mb={"tags": [{"name": "trance", "count": 4}], "title": "Some Song", "artist": UNKNOWN_ARTIST})
        d = self._classify(f)
        self.assertEqual((d.genre, d.confidence), ("Trance", 1.0))
        self.assertEqual(d.sources, ["lastfm_artist", "lastfm_track", "musicbrainz"])

    def test_disagreeing_sources_abstain(self):
        f = FakeFetchers(lfm_track=lfm([("house", 100)]),
                         mb={"tags": [{"name": "techno", "count": 5}], "title": "Some Song", "artist": UNKNOWN_ARTIST})
        d = self._classify(f)
        self.assertTrue(d.abstain)

    def test_artist_level_tags_alone_answer_but_are_never_verified(self):
        # Last.fm has no track tags for most DJ tracks, so artist tags often are all there is.
        # They may answer — but the answer is flagged so callers hold it for review.
        f = FakeFetchers(lfm_artist={"tags": [("techno", 100)], "artist": UNKNOWN_ARTIST})
        d = self._classify(f)
        self.assertEqual(d.genre, "Techno")
        self.assertEqual(d.sources, ["lastfm_artist"])
        self.assertFalse(d.verified)

    def test_a_mixed_catalogue_artist_does_not_answer(self):
        f = FakeFetchers(lfm_artist={"tags": [("techno", 100), ("house", 90), ("trance", 80)], "artist": UNKNOWN_ARTIST})
        self.assertTrue(self._classify(f).abstain)

    def test_track_level_evidence_makes_an_answer_verified(self):
        f = FakeFetchers(lfm_track=lfm([("techno", 100)]), lfm_artist=lfm([("techno", 100)]))
        d = self._classify(f)
        self.assertEqual(d.genre, "Techno")
        self.assertTrue(d.verified)

    def test_a_failing_source_does_not_take_the_others_down(self):
        f = FakeFetchers(lfm_track=lfm([("techno", 100)]), lfm_artist=lfm([("techno", 100)]),
                         boom={"mb"})
        d = self._classify(f)
        self.assertEqual(d.genre, "Techno")

    def test_all_sources_failing_abstains_cleanly(self):
        d = self._classify(FakeFetchers(boom={"lfm_track", "lfm_artist", "mb"}))
        self.assertTrue(d.abstain)

    def test_tempo_breaks_a_genuine_conflict(self):
        f = FakeFetchers(lfm_track=lfm([("house", 100)]),
                         lfm_artist={"tags": [("trance", 100)], "artist": UNKNOWN_ARTIST})
        self.assertTrue(self._classify(f).abstain)                      # House 2.0 vs Trance 1.6
        self.assertEqual(self._classify(f, bpm=146).genre, "Trance")    # 146 BPM is not House

    def test_tempo_contradicting_the_only_candidate_lowers_confidence(self):
        f = FakeFetchers(lfm_track=lfm([("house", 100)]))
        self.assertEqual(self._classify(f).genre, "House")              # 128-ish is fine / unknown tempo
        self.assertTrue(self._classify(f, bpm=174).abstain)             # House at 174 BPM: don't file it

    def test_one_source_split_between_two_genres_is_not_rescued_by_tempo(self):
        f = FakeFetchers(lfm_track=lfm([("house", 100), ("trance", 90)]))
        self.assertTrue(self._classify(f, bpm=146).abstain)             # too little evidence left

    def test_remixer_and_lastfm_agree(self):
        f = FakeFetchers(lfm_track=lfm([("techno", 100)], track="Some Song (Charlotte de Witte Remix)"))
        d = ge.classify_track(UNKNOWN_ARTIST, "Some Song (Charlotte de Witte Remix)", online=True, fetchers=f)
        self.assertEqual(d.genre, "Techno")
        self.assertEqual(set(d.sources), {"remixer", "lastfm_track"})

    def test_fingerprint_and_llm_are_opt_in(self):
        f = FakeFetchers(fp="Techno", llm="House")
        ge.classify_track(UNKNOWN_ARTIST, "S", path="x.mp3", online=True, fetchers=f)
        self.assertEqual([c for c in f.calls if c in ("fp", "llm")], [])
        d = ge.classify_track(UNKNOWN_ARTIST, "S", path="x.mp3", online=True, use_fingerprint=True, fetchers=f)
        self.assertIn(("fingerprint", "Techno"), [(v.source, v.label) for v in d.votes])
        self.assertFalse(d.abstain)                                     # 2.0 weight, alone, is enough

    def test_llm_guess_is_only_ever_a_supporting_vote(self):
        f = FakeFetchers(llm="House")
        d = ge.classify_track(UNKNOWN_ARTIST, "S", path="x.mp3", online=True, use_llm=True, fetchers=f)
        self.assertIn("llm", [v.source for v in d.votes])
        self.assertTrue(d.abstain)


class TestTagCache(unittest.TestCase):
    def test_roundtrip_through_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            c = TagCache(path, flush_every=100)
            c.set("k", {"tags": [["techno", 9]]})
            c.flush()
            self.assertEqual(TagCache(path).get("k"), {"tags": [["techno", 9]]})

    def test_missing_and_expired_entries(self):
        c = TagCache(None, ttl_days=1)
        self.assertIs(c.get("nope"), ge._MISSING)
        c.set("k", 1)
        self.assertEqual(c.get("k"), 1)
        with mock.patch.object(ge.time, "time", return_value=time.time() + 2 * 86400):
            self.assertIs(c.get("k"), ge._MISSING)

    def test_autoflush_and_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            c = TagCache(path, flush_every=2)
            c.set("a", 1)
            self.assertFalse(path.exists())
            c.set("b", 2)                                               # second write triggers the flush
            self.assertTrue(path.exists())
            self.assertEqual([p.name for p in Path(d).iterdir()], ["c.json"])

    def test_unreadable_cache_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(len(TagCache(path)), 0)

    def test_flush_without_a_path_is_a_noop(self):
        c = TagCache(None)
        c.set("k", 1)
        c.flush()


class TestLiveFetchers(unittest.TestCase):
    def setUp(self):
        self.cache = TagCache(None)
        self.f = ge.LiveFetchers(self.cache)

    def test_definite_answers_are_cached(self):
        with mock.patch("services.lastfm_service.get_track_top_tags",
                        return_value={"tags": [("techno", 9)], "artist": "A", "track": "T"}) as api:
            self.f.lastfm_track_tags("A", "T")
            self.f.lastfm_track_tags("a", "t")                           # same key after normalising
        self.assertEqual(api.call_count, 1)

    def test_empty_answers_are_cached_too(self):                        # "Last.fm has nothing" is an answer
        with mock.patch("services.lastfm_service.get_artist_top_tags",
                        return_value={"tags": [], "artist": "", "track": ""}) as api:
            self.f.lastfm_artist_tags("A")
            self.f.lastfm_artist_tags("A")
        self.assertEqual(api.call_count, 1)

    def test_failures_are_never_cached(self):
        with mock.patch("services.musicbrainz_service.search_recording_tags", return_value=None) as api:
            self.f.musicbrainz_tags("A", "T")
            self.f.musicbrainz_tags("A", "T")
        self.assertEqual(api.call_count, 2)

    def test_fingerprint_genre_is_mapped_to_a_folder_label(self):
        with mock.patch("services.musicbrainz_service.lookup_by_fingerprint", return_value="Drum & Bass"):
            self.assertEqual(self.f.fingerprint_genre("x.mp3"), "Drum & Bass")
        with mock.patch("services.musicbrainz_service.lookup_by_fingerprint", return_value=""):
            self.assertEqual(self.f.fingerprint_genre("x.mp3"), "")

    def test_default_fetchers_is_a_single_shared_instance(self):
        with mock.patch.object(ge, "_default_fetchers", None), \
             mock.patch.object(ge, "LiveFetchers", side_effect=lambda: mock.Mock()) as ctor:
            a, b = ge.default_fetchers(), ge.default_fetchers()
        self.assertIs(a, b)
        self.assertEqual(ctor.call_count, 1)


class TestLabelForGenre(unittest.TestCase):
    def test_folders_and_aliases(self):
        for raw, want in {"Afro House": "House", "Library/Techno": "Techno", "Techno": "Techno",
                          "Drum & Bass": "Drum & Bass", "Psytrance": "Trance", "Indian": "Punjabi",
                          "Electronic": "Electronic", "": ""}.items():
            self.assertEqual(ge.label_for_genre(raw), want, raw)


if __name__ == "__main__":
    unittest.main()
