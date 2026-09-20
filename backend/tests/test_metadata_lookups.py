"""
Tests for the external-metadata lookups that feed genre_evidence:
  * strict_matcher.title_similarity     — "same song?" must not accept a superset title
  * musicbrainz_service.search_recording_tags / lookup_by_search
  * lastfm_service.get_track_top_tags / get_artist_top_tags

The MusicBrainz section includes the regression for a bug that made every search with an
artist silently return nothing: the URL contained raw spaces, http.client rejects those
(InvalidURL), and a blanket `except Exception` turned that into "no genre found".
"""
import http.client
import json
import unittest
import urllib.parse
from unittest import mock

from services import lastfm_service, musicbrainz_service as mb, strict_matcher as sm


class TestTitleSimilarity(unittest.TestCase):
    def test_subset_title_is_not_the_same_song(self):
        self.assertEqual(sm._fuzzy_ratio("takes", "takes me home"), 1.0)     # search relevance: fine
        self.assertLess(sm.title_similarity("Takes", "Takes Me Home"), 0.85)  # identity: different song

    def test_same_song_with_noise_matches(self):
        self.assertGreaterEqual(sm.title_similarity("Levels", "Levels (Original Mix)"), 0.99)
        self.assertGreaterEqual(sm.title_similarity("LEVELS", "levels"), 0.99)
        self.assertGreaterEqual(sm.title_similarity("Song (Radio Edit)", "Song [Extended Mix]"), 0.99)
        self.assertGreaterEqual(sm.title_similarity("Song - Remastered 2011", "Song"), 0.99)

    def test_a_named_remix_is_a_different_recording(self):
        self.assertLess(sm.title_similarity("Song", "Song (Charlotte de Witte Remix)"), 0.85)
        self.assertLess(sm.title_similarity("Song (Original Mix)", "Song (Charlotte de Witte Remix)"), 0.85)

    def test_empty_is_zero(self):
        self.assertEqual(sm.title_similarity("", "x"), 0.0)
        self.assertEqual(sm.title_similarity("x", ""), 0.0)


class TestMusicBrainzGetRetry(unittest.TestCase):
    """MusicBrainz answers 503 intermittently (seen live: first try 503, second try 200)."""

    def setUp(self):
        sleep_patch = mock.patch.object(mb.time, "sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        last_patch = mock.patch.object(mb, "_LAST_REQ", 0.0)
        last_patch.start()
        self.addCleanup(last_patch.stop)

    @staticmethod
    def _ok(body=b'{"recordings": []}'):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        return resp

    @staticmethod
    def _http_error(code):
        import urllib.error
        return urllib.error.HTTPError("http://x", code, "err", {}, None)

    def test_a_single_503_is_retried_after_a_backoff(self):
        with mock.patch.object(mb.urllib.request, "urlopen", side_effect=[self._http_error(503), self._ok()]) as op:
            self.assertEqual(mb._get("http://x"), {"recordings": []})
        self.assertEqual(op.call_count, 2)
        self.assertIn(mock.call(2.0), self.sleep.call_args_list)

    def test_429_is_retried_too(self):
        with mock.patch.object(mb.urllib.request, "urlopen", side_effect=[self._http_error(429), self._ok()]):
            self.assertEqual(mb._get("http://x"), {"recordings": []})

    def test_a_second_503_is_raised_not_looped(self):
        import urllib.error
        with mock.patch.object(mb.urllib.request, "urlopen",
                               side_effect=[self._http_error(503), self._http_error(503)]) as op:
            with self.assertRaises(urllib.error.HTTPError):
                mb._get("http://x")
        self.assertEqual(op.call_count, 2)

    def test_other_http_errors_are_not_retried(self):
        import urllib.error
        with mock.patch.object(mb.urllib.request, "urlopen", side_effect=self._http_error(404)) as op:
            with self.assertRaises(urllib.error.HTTPError):
                mb._get("http://x")
        self.assertEqual(op.call_count, 1)

    def test_a_search_survives_one_503(self):
        good = {"recordings": [rec("Song", "Artist", tags=[{"name": "trance", "count": 3}, {"name": "x", "count": 1}])]}
        body = json.dumps(good).encode()
        with mock.patch.object(mb.urllib.request, "urlopen", side_effect=[self._http_error(503), self._ok(body)]):
            hit = mb.search_recording_tags("Song", "Artist")
        self.assertEqual(hit["tags"][0]["name"], "trance")


class TestSameSongTitle(unittest.TestCase):
    def test_base_title_strips_edition_tags(self):
        self.assertEqual(sm.base_title('Chhote Chhote Peg (From "Yaariyan")'), "chhote chhote peg")
        self.assertEqual(sm.base_title("Khoyo - Kahani Remix"), "khoyo")
        self.assertEqual(sm.base_title("Addicted (feat. TIMID.)"), "addicted")
        self.assertEqual(sm.base_title("(Intro)"), "")
        self.assertEqual(sm.base_title(""), "")

    def test_the_same_song_in_a_different_edition(self):
        for a, b in (("Chhote Chhote Peg", 'Chhote Chhote Peg (From "Yaariyan")'), ("Levels", "Levels (Original Mix)"),
                     ("Khoyo - Kahani Remix", "Khoyo"), ("Addicted (feat. TIMID.)", "Addicted")):
            self.assertTrue(sm.same_song_title(a, b), (a, b))

    def test_different_songs_never_match(self):
        for a, b in (("Aura", 'Aura of Ustaad (From "Ustaad Bhagat Singh")'), ("WOH", "Woh Ladki Jo - Sped Up"),
                     ("Dil Nu", "Dil Pe Zakham Khate Hain"), ("Kiya Kiya", "Candytuft Parsley"),
                     ("Play", "Players"), ("Beba", "Pepas"), ("", "x"), ("(Intro)", "(Intro)")):
            self.assertFalse(sm.same_song_title(a, b), (a, b))

    def test_threshold_is_adjustable(self):
        self.assertFalse(sm.same_song_title("Dil Nu", "Dil Ku"))                # ratio 0.83 < 0.85
        self.assertTrue(sm.same_song_title("Dil Nu", "Dil Ku", threshold=0.8))


def rec(title, artist, score=100, tags=None, rg="rg1"):
    return {"id": "r", "score": score, "title": title, "tags": tags if tags is not None else [],
            "artist-credit": [{"name": artist, "artist": {"name": artist}}],
            "releases": [{"release-group": {"id": rg}}]}


class TestMusicBrainzSearch(unittest.TestCase):
    def _search(self, recordings, title="Song", artist="Artist", rg_tags=None):
        urls = []

        def fake_get(url):
            urls.append(url)
            if "/release-group/" in url:
                return {"tags": rg_tags or []}
            return {"recordings": recordings}

        with mock.patch.object(mb, "_get", side_effect=fake_get):
            return mb.search_recording_tags(title, artist), urls

    def _query(self, url):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]

    def test_url_is_valid_for_http_client_and_query_is_intact(self):
        _, urls = self._search([], "Song Title", "Some Artist")
        parts = urllib.parse.urlsplit(urls[0])
        # This is exactly the check that used to fail with raw spaces in the URL.
        http.client.HTTPConnection("musicbrainz.org")._validate_path(parts.path + "?" + parts.query)
        self.assertEqual(self._query(urls[0]), 'recording:"Song Title" AND artist:"Some Artist"')

    def test_quotes_and_backslashes_cannot_break_out_of_the_phrase(self):
        _, urls = self._search([], 'Say "Hi"', "A\\B")
        self.assertEqual(self._query(urls[0]), 'recording:"Say \\"Hi\\"" AND artist:"A\\\\B"')

    def test_placeholder_artist_is_left_out_of_the_query(self):
        for placeholder in ("Unknown", "electronic", "", "Various Artists"):
            _, urls = self._search([], "Song", placeholder)
            self.assertNotIn("artist:", self._query(urls[0]), placeholder)

    def test_returns_tags_of_the_matching_recording(self):
        tags = [{"name": "techno", "count": 4}, {"name": "dark", "count": 1}]
        hit, _ = self._search([rec("Song", "Artist", tags=tags)])
        self.assertEqual(hit["tags"], tags)
        self.assertEqual((hit["title"], hit["artist"]), ("Song", "Artist"))

    def test_skips_an_unrelated_top_hit_and_takes_the_real_one(self):
        good = rec("Song", "Artist", tags=[{"name": "trance", "count": 3}, {"name": "uplifting", "count": 2}])
        hit, _ = self._search([rec("Song Of The Sea", "Artist", tags=[{"name": "folk", "count": 9}] * 2), good])
        self.assertEqual(hit["tags"][0]["name"], "trance")

    def test_rejects_a_hit_by_a_different_artist(self):
        hit, _ = self._search([rec("Song", "Completely Different Band", tags=[{"name": "metal", "count": 9}] * 2)])
        self.assertEqual(hit["tags"], [])

    def test_rejects_a_low_confidence_hit(self):
        hit, _ = self._search([rec("Song", "Artist", score=60, tags=[{"name": "house", "count": 9}] * 2)])
        self.assertEqual(hit["tags"], [])

    def test_no_match_is_a_definite_empty_answer_but_failure_is_none(self):
        hit, _ = self._search([])
        self.assertEqual((hit["title"], hit["tags"]), ("", []))
        with mock.patch.object(mb, "_get", side_effect=OSError("network down")):
            self.assertIsNone(mb.search_recording_tags("Song", "Artist"))

    def test_credited_name_or_canonical_name_may_match(self):
        r = rec("Song", "Alias Name")
        r["artist-credit"][0]["artist"]["name"] = "Artist"               # canonical name differs from credit
        hit, _ = self._search([r])
        self.assertEqual(hit["artist"], "Alias Name")

    def test_release_group_tags_only_fetched_when_recording_has_few(self):
        _, urls = self._search([rec("Song", "Artist", tags=[{"name": "a", "count": 1}])],
                               rg_tags=[{"name": "techno", "count": 5}])
        self.assertTrue(any("/release-group/" in u for u in urls))
        rich = [{"name": "techno", "count": 3}, {"name": "dark techno", "count": 2}]
        hit, urls = self._search([rec("Song", "Artist", tags=rich)])
        self.assertFalse(any("/release-group/" in u for u in urls))
        self.assertEqual(hit["tags"], rich)

    def test_release_group_tags_are_merged(self):
        hit, _ = self._search([rec("Song", "Artist")], rg_tags=[{"name": "trance", "count": 6}])
        self.assertEqual([t["name"] for t in hit["tags"]], ["trance"])

    def test_empty_title_is_none_without_a_request(self):
        with mock.patch.object(mb, "_get") as get:
            self.assertIsNone(mb.search_recording_tags("", "Artist"))
        get.assert_not_called()


class TestLookupBySearch(unittest.TestCase):
    def test_maps_tags_to_a_folder(self):
        hit = {"title": "Song", "artist": "Artist", "score": 100, "tags": [{"name": "melodic techno", "count": 5}]}
        with mock.patch.object(mb, "search_recording_tags", return_value=hit):
            self.assertEqual(mb.lookup_by_search("Song", "Artist"), "Techno")

    def test_no_match_or_failure_is_empty_string(self):
        with mock.patch.object(mb, "search_recording_tags", return_value={"title": "", "artist": "", "score": 0, "tags": []}):
            self.assertEqual(mb.lookup_by_search("Song", "Artist"), "")
        with mock.patch.object(mb, "search_recording_tags", return_value=None):
            self.assertEqual(mb.lookup_by_search("Song", "Artist"), "")


def response(body, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body
    return r


class TestLastfmTopTags(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(lastfm_service, "_api_key", return_value="key"),
                        mock.patch.object(lastfm_service.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _get(self, body, status=200):
        return mock.patch.object(lastfm_service.requests, "get", return_value=response(body, status))

    def test_track_tags_with_resolved_names(self):
        body = {"toptags": {"tag": [{"name": "Techno", "count": 100}, {"name": "electronic", "count": "70"}],
                            "@attr": {"artist": "Some Artist", "track": "Some Song"}}}
        with self._get(body):
            res = lastfm_service.get_track_top_tags("Some Artist", "Some Song")
        self.assertEqual(res, {"tags": [("techno", 100), ("electronic", 70)], "artist": "Some Artist", "track": "Some Song"})

    def test_single_tag_comes_back_as_a_dict(self):
        with self._get({"toptags": {"tag": {"name": "House", "count": 100}}}):
            res = lastfm_service.get_track_top_tags("A", "T")
        self.assertEqual(res["tags"], [("house", 100)])
        self.assertEqual((res["artist"], res["track"]), ("", ""))

    def test_unknown_track_is_a_definite_empty_answer(self):
        with self._get({"error": 6, "message": "Track not found"}):
            self.assertEqual(lastfm_service.get_track_top_tags("A", "T")["tags"], [])

    def test_other_api_errors_mean_unavailable(self):
        with self._get({"error": 29, "message": "Rate limit exceeded"}):
            self.assertIsNone(lastfm_service.get_track_top_tags("A", "T"))

    def test_server_error_and_transport_failure_mean_unavailable(self):
        with self._get({}, status=503):
            self.assertIsNone(lastfm_service.get_track_top_tags("A", "T"))
        with mock.patch.object(lastfm_service.requests, "get", side_effect=OSError("down")):
            self.assertIsNone(lastfm_service.get_track_top_tags("A", "T"))

    def test_no_api_key_makes_no_request(self):
        with mock.patch.object(lastfm_service, "_api_key", return_value=""), \
             mock.patch.object(lastfm_service.requests, "get") as get:
            self.assertIsNone(lastfm_service.get_track_top_tags("A", "T"))
        get.assert_not_called()

    def test_missing_artist_or_title_makes_no_request(self):
        with mock.patch.object(lastfm_service.requests, "get") as get:
            self.assertIsNone(lastfm_service.get_track_top_tags("", "T"))
            self.assertIsNone(lastfm_service.get_track_top_tags("A", ""))
            self.assertIsNone(lastfm_service.get_artist_top_tags(""))
        get.assert_not_called()

    def test_request_asks_for_the_right_method_and_autocorrect(self):
        with self._get({"toptags": {"tag": []}}) as get:
            lastfm_service.get_track_top_tags("A", "T")
            lastfm_service.get_artist_top_tags("A")
        first, second = (c.kwargs["params"] for c in get.call_args_list)
        self.assertEqual((first["method"], first["artist"], first["track"]), ("track.getTopTags", "A", "T"))
        self.assertEqual(second["method"], "artist.getTopTags")
        self.assertEqual((first["autocorrect"], first["format"], first["api_key"]), (1, "json", "key"))

    def test_artist_tags(self):
        body = {"toptags": {"tag": [{"name": "trance", "count": 100}], "@attr": {"artist": "Some Artist"}}}
        with self._get(body):
            res = lastfm_service.get_artist_top_tags("Some Artist")
        self.assertEqual(res, {"tags": [("trance", 100)], "artist": "Some Artist", "track": ""})


if __name__ == "__main__":
    unittest.main()
