"""
Tests for the iTunes track-level genre signal: services/itunes_service.py, its use as a vote in
services/genre_evidence.py, and the scan's second pass that asks iTunes only about tracks nothing else
could settle. iTunes answers are noisy ("Dance", "Electronic" everywhere; Hamdi "Palm Trees" -> "Latin"),
so most of what is pinned here is what it must NOT be trusted for: other artists' tracks, wrong lengths,
generic genres, and a single release deciding alone.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import genre_evidence as ge
from services import itunes_service as it

UNKNOWN_ARTIST = "Zzyzx Qwerty"


def cand(artist, title, genre, secs):
    return {"artist": artist, "title": title, "genre": genre, "secs": secs}


class FakeResponse:
    def __init__(self, results, status=200):
        self.status_code = status
        self._results = results

    def json(self):
        return {"results": self._results}


def raw(artist, title, genre, ms, kind="song"):
    return {"artistName": artist, "trackName": title, "primaryGenreName": genre, "trackTimeMillis": ms, "kind": kind}


class TestFetchCandidates(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(it, "_last_call", 0.0)
        p.start()
        self.addCleanup(p.stop)
        self.sleeps = []

    def fetch(self, response, term=("Artist", "Title")):
        return it.fetch_candidates(*term, get=lambda **kw: response, sleep=self.sleeps.append)

    def test_results_are_parsed(self):
        out = self.fetch(FakeResponse([raw("Pritam & Arijit Singh", "Channa Mereya", "Bollywood", 289000)]))
        self.assertEqual(out, [{"artist": "Pritam & Arijit Singh", "title": "Channa Mereya", "secs": 289.0, "genre": "Bollywood"}])

    def test_no_results_is_a_definite_empty_answer(self):
        self.assertEqual(self.fetch(FakeResponse([])), [])

    def test_a_failure_is_none_so_it_is_never_cached(self):
        self.assertIsNone(self.fetch(FakeResponse([], status=429)))
        self.assertIsNone(self.fetch(FakeResponse([], status=503)))
        self.assertIsNone(it.fetch_candidates("A", "T", get=mock.Mock(side_effect=OSError("down")), sleep=self.sleeps.append))

    def test_non_song_results_are_skipped(self):
        out = self.fetch(FakeResponse([raw("A", "T", "Pop", 1000, kind="music-video"), raw("A", "T", "Pop", 1000)]))
        self.assertEqual(len(out), 1)

    def test_an_empty_query_makes_no_request(self):
        get = mock.Mock()
        self.assertIsNone(it.fetch_candidates("", "  ", get=get, sleep=self.sleeps.append))
        get.assert_not_called()

    def test_requests_are_spaced_out(self):
        with mock.patch.object(it.time, "time", side_effect=[100.0, 100.0, 100.5, 100.5]):
            it.fetch_candidates("A", "T", get=lambda **kw: FakeResponse([]), sleep=self.sleeps.append)   # first: no wait
            it.fetch_candidates("A", "T", get=lambda **kw: FakeResponse([]), sleep=self.sleeps.append)   # 0.5 s later
        self.assertEqual(len(self.sleeps), 1)                     # the first request needs no wait ...
        self.assertAlmostEqual(self.sleeps[0], it.MIN_INTERVAL - 0.5, places=2)   # ... the second waits out the rest

    def test_the_query_and_limit_are_sent(self):
        seen = {}
        it.fetch_candidates("Skrillex", "Bangarang", get=lambda **kw: seen.update(kw) or FakeResponse([]), sleep=lambda *_: None)
        self.assertEqual(seen["params"], {"term": "Skrillex Bangarang", "entity": "song", "limit": it.MAX_RESULTS})


class TestMatchingGenres(unittest.TestCase):
    C = [cand("Pritam & Arijit Singh", "Channa Mereya", "Bollywood", 289.0),
         cand("Pritam & Arijit Singh", "Channa Mereya (From \"Ae Dil Hai Mushkil\")", "Bollywood", 290.0),
         cand("Norman Dück", "Bangarang (Skrillex Dubstep Remix)", "Electronic", 221.0)]

    def test_releases_of_the_same_song_that_agree_count_separately(self):
        self.assertEqual(it.matching_genres(self.C, "Pritam", "Channa Mereya", 289.0), [["Bollywood", 2]])

    def test_another_artists_track_with_the_same_title_is_excluded(self):
        self.assertEqual(it.matching_genres(self.C, "Skrillex", "Bangarang", 221.0), [])

    def test_a_different_length_means_a_different_recording(self):
        self.assertEqual(it.matching_genres(self.C, "Pritam", "Channa Mereya", 240.0), [])
        self.assertEqual(it.matching_genres(self.C, "Pritam", "Channa Mereya", 292.0), [["Bollywood", 2]])   # within 6 s

    def test_without_a_known_length_the_length_is_not_used(self):
        self.assertEqual(it.matching_genres(self.C, "Pritam", "Channa Mereya", None), [["Bollywood", 2]])

    def test_a_different_song_never_matches(self):
        self.assertEqual(it.matching_genres([cand("Pritam", "Channa", "Bollywood", 289.0)], "Pritam", "Channa Mereya", 289.0), [])

    def test_results_without_a_genre_are_skipped_and_no_artist_skips_the_artist_check(self):
        self.assertEqual(it.matching_genres([cand("Anyone", "T", "", 100.0)], "", "T"), [])
        self.assertEqual(it.matching_genres([cand("Anyone", "T", "House", 100.0)], "", "T"), [["House", 1]])

    def test_genres_are_ranked_by_count(self):
        c = [cand("A", "T", "Dance", 200.0), cand("A", "T", "Techno", 200.0), cand("A", "T", "Techno", 201.0)]
        self.assertEqual(it.matching_genres(c, "A", "T", 200.0), [["Techno", 2], ["Dance", 1]])


class FakeFetchers:
    def __init__(self, itunes=None):
        self.itunes, self.calls = itunes, []

    def itunes_candidates(self, artist, title):
        self.calls.append((artist, title))
        return self.itunes

    def lastfm_track_tags(self, *a):  return None
    def lastfm_artist_tags(self, *a): return None
    def musicbrainz_tags(self, *a):   return None


def classify(f, duration_s=None, **kw):
    return ge.classify_track(UNKNOWN_ARTIST, "Some Song", online=True, use_itunes=True, duration_s=duration_s, fetchers=f, **kw)


class TestItunesVote(unittest.TestCase):
    def two(self, genre="Techno", secs=200.0):
        return [cand(UNKNOWN_ARTIST, "Some Song", genre, secs), cand(UNKNOWN_ARTIST, "Some Song", genre, secs + 1)]

    def test_two_agreeing_releases_decide_and_the_answer_is_verified(self):
        d = classify(FakeFetchers(self.two()), duration_s=200.0)
        self.assertEqual((d.genre, d.abstain, d.verified), ("Techno", False, True))
        self.assertEqual(d.sources, ["itunes"])
        self.assertAlmostEqual(d.confidence, 0.64, places=2)

    def test_one_release_is_only_a_weak_vote(self):
        d = classify(FakeFetchers(self.two()[:1]), duration_s=200.0)
        self.assertTrue(d.abstain)                                        # 0.8 < the minimum evidence

    def test_generic_genres_say_nothing(self):
        for genre in ("Dance", "Electronic", "Worldwide", "Alternative", "Soundtrack"):
            self.assertEqual(classify(FakeFetchers(self.two(genre)), duration_s=200.0).votes, [], genre)

    def test_specific_genres_map_to_crates(self):
        for genre, crate in (("House", "House"), ("Garage", "UK Garage"), ("Bollywood", "Bollywood"), ("Punjabi", "Punjabi"),
                             ("Hip-Hop/Rap", "Hip Hop"), ("R&B/Soul", "R&B"), ("Drum & Bass", "Drum & Bass"),
                             ("Latin Urban", "Latin"), ("Urbano latino", "Latin")):
            d = classify(FakeFetchers(self.two(genre)), duration_s=200.0)
            self.assertEqual([v.label for v in d.votes], [crate], genre)

    def test_indian_pop_supports_both_but_leans_bollywood(self):
        d = classify(FakeFetchers(self.two("Indian Pop")), duration_s=200.0)
        weights = {v.label: v.weight for v in d.votes}
        self.assertEqual(set(weights), {"Bollywood", "Pop"})
        self.assertGreater(weights["Bollywood"], weights["Pop"])

    def test_a_result_of_the_wrong_length_is_ignored(self):
        self.assertEqual(classify(FakeFetchers(self.two(secs=300.0)), duration_s=200.0).votes, [])

    def test_another_artists_result_is_ignored(self):
        other = [cand("Somebody Else Entirely", "Some Song", "Techno", 200.0)] * 2
        self.assertEqual(classify(FakeFetchers(other), duration_s=200.0).votes, [])

    def test_it_is_off_unless_asked_for(self):
        f = FakeFetchers(self.two())
        ge.classify_track(UNKNOWN_ARTIST, "Some Song", online=True, fetchers=f)
        self.assertEqual(f.calls, [])

    def test_unavailable_or_empty_answers_and_older_fetchers_are_harmless(self):
        self.assertEqual(classify(FakeFetchers(None), duration_s=1.0).votes, [])
        self.assertEqual(classify(FakeFetchers([]), duration_s=1.0).votes, [])
        legacy = mock.Mock(spec=["lastfm_track_tags", "lastfm_artist_tags", "musicbrainz_tags"])
        legacy.lastfm_track_tags.return_value = legacy.lastfm_artist_tags.return_value = legacy.musicbrainz_tags.return_value = None
        self.assertTrue(classify(legacy).abstain)

    def test_itunes_agreeing_with_a_curated_artist_table_is_stronger_than_either(self):
        d = ge.classify_track("Charlotte de Witte", "Doppler", online=True, use_itunes=True, duration_s=434.0,
                              fetchers=FakeFetchers([cand("Charlotte de Witte", "Doppler", "Techno", 434.0),
                                                     cand("Charlotte de Witte", "Doppler", "Techno", 433.0)]))
        self.assertEqual((d.genre, d.confidence), ("Techno", 1.0))
        self.assertEqual(sorted(d.sources), ["artist_override", "itunes"])

    def test_a_bare_garage_tag_is_uk_garage_but_garage_rock_is_not(self):
        self.assertEqual(ge.tag_labels("garage"), {"UK Garage": 0.7})
        self.assertEqual(ge.tag_labels("garage rock"), {})


class TestLiveFetcherCaching(unittest.TestCase):
    def test_answers_are_cached_and_failures_are_not(self):
        f = ge.LiveFetchers(ge.TagCache(None))
        with mock.patch("services.itunes_service.fetch_candidates", return_value=[cand("A", "T", "House", 1.0)]) as api:
            f.itunes_candidates("A", "T")
            f.itunes_candidates("a", "t")
        self.assertEqual(api.call_count, 1)
        with mock.patch("services.itunes_service.fetch_candidates", return_value=None) as api:
            f.itunes_candidates("B", "U")
            f.itunes_candidates("B", "U")
        self.assertEqual(api.call_count, 2)
        with mock.patch("services.itunes_service.fetch_candidates", return_value=[]) as api:       # "nothing found" IS an answer
            f.itunes_candidates("C", "V")
            f.itunes_candidates("C", "V")
        self.assertEqual(api.call_count, 1)


class TestScanSecondPass(unittest.TestCase):
    """library_resort.scan(itunes=True) asks iTunes ONLY about tracks nothing else settled."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        from tests.test_library_resort import make_mp3
        make_mp3(self.root, "Library/House/Settled.mp3", "Settled", "Fisher")
        make_mp3(self.root, "Library/House/Mystery One.mp3", "Mystery One", "Nobody Known")
        make_mp3(self.root, "Library/Pop/Mystery Two.mp3", "Mystery Two", "Nobody Else")
        self.calls = []

    def scan(self, **kw):
        import library_resort as lr
        from services.genre_evidence import Decision

        def fake(artist, title, **k):
            self.calls.append((title, k.get("use_itunes", False), k.get("duration_s")))
            if title == "Settled":
                return Decision(genre="House", confidence=0.9, abstain=False, reason="voted", sources=["artist_override"])
            if k.get("use_itunes") and title == "Mystery One":
                return Decision(genre="Techno", confidence=0.64, abstain=False, reason="voted", sources=["itunes"])
            return Decision(reason="no evidence")

        with mock.patch("services.genre_evidence.classify_track", side_effect=fake), \
             mock.patch("services.genre_evidence.default_fetchers", return_value=mock.Mock()):
            return lr.scan(self.root, recover=False, playlist_tracks=[], spotify_calls=False, out=lambda *_: None, **kw)

    def test_only_unresolved_tracks_are_sent_to_itunes(self):
        rows, _ = self.scan(itunes=True)
        first = {t for t, it_flag, _ in self.calls if not it_flag}
        second = {t for t, it_flag, _ in self.calls if it_flag}
        self.assertEqual(first, {"Settled", "Mystery One", "Mystery Two"})
        self.assertEqual(second, {"Mystery One", "Mystery Two"})              # 'Settled' is never re-queried
        by = {r.title: r for r in rows}
        # iTunes alone tops out at 64% confidence (weight 1.6 of 2.5): trusted, but below the 75% move bar,
        # so it is offered for review — it becomes a MOVE only when another signal agrees.
        self.assertEqual((by["Mystery One"].action, by["Mystery One"].proposed, by["Mystery One"].verified), ("review", "Techno", True))
        self.assertIn("below the 75% move bar", by["Mystery One"].note)
        self.assertEqual(by["Mystery Two"].action, "unknown")
        self.assertEqual(by["Settled"].action, "ok")

    def test_the_files_length_is_passed_so_iTunes_answers_can_be_identity_checked(self):
        with mock.patch("library_resort.file_seconds", return_value=201.5):
            self.scan(itunes=True)
        self.assertTrue(all(d == 201.5 for t, flag, d in self.calls if flag))

    def test_off_by_default(self):
        self.scan()
        self.assertFalse(any(flag for _, flag, _ in self.calls))

    def test_an_injected_classifier_is_left_alone(self):
        import library_resort as lr
        rows, _ = lr.scan(self.root, recover=False, playlist_tracks=[], spotify_calls=False, out=lambda *_: None,
                          classify=lambda *a: __import__("services.genre_evidence", fromlist=["Decision"]).Decision(reason="x"),
                          itunes=True)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
