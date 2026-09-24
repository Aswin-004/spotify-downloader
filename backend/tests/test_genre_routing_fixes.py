"""
Regression tests for the genre-routing / karaoke-leak fixes found on 2026-09-19.

Root causes pinned here:
  1. techno artists were overridden to the generic "Electronic" catch-all at conf 1.0
  2. SPOTIFY_GENRE_MAP sent "melodic techno" to "Afro House" (=> the House crate)
  3. Spotify stopped returning artist `genres`; the router failed silently and
     kept spending an API call per artist
  4. the catch-all reclassifier's title-search step could route on a stranger's
     genre from a title collision
  5. API keys read at import time instead of call time
"""
import os
import unittest
from unittest import mock

from config import config
from services import genre_router
from services.genre_evidence import Decision
from services.genre_router import _library_path, normalize_genre, normalize_artist_key

# What the evidence vote returns when it cannot place a track.
ABSTAIN = Decision(reason="test abstain")


class TestArtistOverridesAreSpecific(unittest.TestCase):
    def _folder(self, artist):
        return _library_path(config.ARTIST_GENRE_OVERRIDE[normalize_artist_key(artist)])

    def test_techno_artists_land_in_techno_not_electronic(self):
        for artist in ("Charlotte de Witte", "Amelie Lens", "Adam Beyer", "Chris Liebing", "Anyma"):
            self.assertEqual(self._folder(artist), "Library/Techno", artist)

    def test_trance_artists_land_in_trance(self):
        for artist in ("Markus Schulz", "1200 Micrograms"):
            self.assertEqual(self._folder(artist), "Library/Trance", artist)

    def test_melodic_house_artist_lands_in_house(self):
        self.assertEqual(self._folder("Ben Böhmer"), "Library/House")


class TestGenreMapMelodicTechno(unittest.TestCase):
    def test_melodic_techno_maps_to_techno_not_house(self):
        self.assertEqual(normalize_genre("melodic techno"), "Techno")
        self.assertEqual(_library_path(normalize_genre("melodic techno")), "Library/Techno")

    def test_plain_house_and_trance_unaffected(self):
        self.assertEqual(_library_path(normalize_genre("progressive house")), "Library/House")
        self.assertEqual(_library_path(normalize_genre("uplifting trance")), "Library/Trance")
        self.assertEqual(_library_path(normalize_genre("hard techno")), "Library/Techno")


class TestSpotifyGenresRemoved(unittest.TestCase):
    """Spotify artist objects no longer include 'genres' — detect it, warn once, stop calling."""

    def setUp(self):
        genre_router._spotify_genres_available = True
        genre_router._genre_cache.clear()
        genre_router._confidence_cache.clear()
        genre_router._source_cache.clear()

    def tearDown(self):
        self.setUp()

    def _unknown_artist_resolve(self, sp, artist_id, name="Totally Unknown Artist XYZ"):
        # Bypass every non-Spotify tier so the Spotify fetch (step 5) is reached.
        with mock.patch.object(genre_router, "_get_artist_override", return_value=""), \
             mock.patch("services.artist_memory_service.lookup_artist", return_value=None), \
             mock.patch("services.artist_knowledge_service.lookup_artist_knowledge", return_value=None), \
             mock.patch("database.get_custom_folder_mappings_collection", side_effect=RuntimeError("no db")):
            return genre_router._resolve_core(artist_id, name, sp)

    def test_missing_genres_key_disables_further_spotify_calls(self):
        sp = mock.Mock()
        sp.artist.return_value = {"id": "a1", "name": "X", "images": []}   # NO "genres" key
        folder, conf, source, _ = self._unknown_artist_resolve(sp, "a1")
        self.assertTrue(folder.startswith("NeedsReview/"))
        self.assertEqual(conf, 0.0)
        self.assertFalse(genre_router.spotify_genres_available())
        self.assertEqual(sp.artist.call_count, 1)

        # A second, different artist must NOT trigger another API call.
        self._unknown_artist_resolve(sp, "a2", name="Another Unknown Artist")
        self.assertEqual(sp.artist.call_count, 1)

    def test_present_but_empty_genres_keeps_asking(self):
        sp = mock.Mock()
        sp.artist.return_value = {"id": "a1", "genres": []}   # key present, artist just untagged
        self._unknown_artist_resolve(sp, "a1")
        self.assertTrue(genre_router.spotify_genres_available())
        self._unknown_artist_resolve(sp, "a2", name="Another Unknown Artist")
        self.assertEqual(sp.artist.call_count, 2)

    def test_real_genres_still_route(self):
        sp = mock.Mock()
        sp.artist.return_value = {"id": "a1", "genres": ["hard techno", "techno"]}
        folder, conf, source, _ = self._unknown_artist_resolve(sp, "a1")
        self.assertEqual(folder, "Library/Techno")
        self.assertEqual(source, "spotify_genre")


class TestLazyApiKeys(unittest.TestCase):
    def test_lastfm_key_honours_env_set_after_import(self):
        from services import lastfm_service
        with mock.patch.dict(os.environ, {"LASTFM_API_KEY": "late-key"}):
            self.assertEqual(lastfm_service._api_key(), "late-key")

    def test_acoustid_key_honours_env_set_after_import(self):
        from services import musicbrainz_service
        with mock.patch.dict(os.environ, {"ACOUSTID_API_KEY": "late-acoustid"}):
            self.assertEqual(musicbrainz_service._acoustid_key(), "late-acoustid")


class TestUriRedaction(unittest.TestCase):
    def test_password_is_masked(self):
        from database import _redact_uri
        out = _redact_uri("mongodb+srv://admin:hunter2@cluster0.example.mongodb.net/db?x=1")
        self.assertNotIn("hunter2", out)
        self.assertNotIn("admin", out)
        self.assertIn("cluster0.example.mongodb.net", out)

    def test_uri_without_credentials_unchanged(self):
        from database import _redact_uri
        self.assertEqual(_redact_uri("mongodb://localhost:27017"), "mongodb://localhost:27017")


class TestReclassifierTitleSearchIdentityGuard(unittest.TestCase):
    """A title-only Spotify hit must match the file's title AND length; the tag is never rewritten."""

    def _run(self, *, hit_title, hit_ms, file_secs, dry_run=True):
        from services import catchall_reclassifier as cr

        hit = {"name": hit_title, "duration_ms": hit_ms,
               "artists": [{"name": "Some Stranger", "id": "sid"}]}
        sp = mock.Mock()
        sp.search.return_value = {"tracks": {"items": [hit]}}
        fake_id3 = mock.MagicMock()
        fake_id3.get.side_effect = lambda k, d="": {"TPE1": "Nico Moreno", "TIT2": "Takes"}.get(k, d)

        with mock.patch("mutagen.id3.ID3", return_value=fake_id3), \
             mock.patch("mutagen.mp3.MP3", return_value=mock.Mock(info=mock.Mock(length=file_secs))), \
             mock.patch("services.spotify_service.get_spotify_service", return_value=mock.Mock(sp=sp)), \
             mock.patch("services.genre_router.spotify_genres_available", return_value=True), \
             mock.patch("services.genre_router.resolve_genre_folder_with_confidence",
                        # Only the STRANGER's artist id resolves to a genre; the file's own
                        # artist (step 2, no id) stays unresolved so step 3 is what's exercised.
                        side_effect=lambda aid, name, _sp: (
                            ("Library/Pop", 1.0, "artist_override") if aid == "sid"
                            else ("NeedsReview/x", 0.0, "uncategorized"))), \
             mock.patch.object(cr, "_with_timeout", side_effect=lambda fn, seconds=8: fn()), \
             mock.patch("services.genre_evidence.classify_track", return_value=ABSTAIN):
            return cr.classify_and_route_catchall_track(
                "C:/nonexistent/Takes - Nico Moreno.mp3", dry_run=dry_run, skip_ai_step=True,
            ), fake_id3

    def test_title_collision_with_different_length_is_ignored(self):
        # "Takes" by a stranger, but 40s longer than our file -> a different recording.
        out, _ = self._run(hit_title="Takes", hit_ms=250_000, file_secs=210.0)
        self.assertFalse(out["moved"])

    def test_different_title_is_ignored(self):
        out, _ = self._run(hit_title="Take Me Home", hit_ms=210_000, file_secs=210.0)
        self.assertFalse(out["moved"])

    def test_same_title_and_length_is_reported_but_marked_unverified(self):
        out, _ = self._run(hit_title="Takes", hit_ms=210_500, file_secs=210.0)
        self.assertTrue(out["moved"])
        self.assertTrue(out["ai_guess"], "title-search matches must be flagged UNVERIFIED")

    def test_artist_tag_is_never_rewritten(self):
        out, fake_id3 = self._run(hit_title="Takes", hit_ms=210_500, file_secs=210.0, dry_run=False)
        fake_id3.save.assert_not_called()


class TestMaintenanceWorkerTitleSearchGuard(unittest.TestCase):
    """Source-contract tests (same approach as test_retag_catchall_rate_limit.py): the hourly
    maintenance job moves files unattended, so its title-search step must carry the identity
    guard and must never rewrite a file's artist tag."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "services" / "maintenance_worker.py").read_text(encoding="utf-8")
        start = src.index("# ── 3. Spotify title-only search")
        end = src.index("# ── 4. Evidence vote", start)
        cls.step3 = src[start:end]
        cls.step4_to_7 = src[end:src.index("dest_dir = Path(config.BASE_DOWNLOAD_DIR) / genre_path", end)]
        cls.whole = src

    def test_requires_near_exact_title_and_same_length(self):
        self.assertIn("_sim < 0.90", self.step3)
        self.assertIn("<= 3.0", self.step3)

    def test_never_rewrites_the_artist_tag(self):
        self.assertNotIn('_tags["TPE1"]', self.step3)
        self.assertNotIn("_tags.save", self.step3)

    def test_spotify_steps_skip_when_genres_are_unavailable(self):
        self.assertIn("_spotify_genres_available()", self.step3)
        self.assertGreaterEqual(self.whole.count("_spotify_genres_available()"), 2)

    def test_uses_the_evidence_vote_not_single_source_lookups(self):
        self.assertIn("genre_evidence", self.step4_to_7)
        self.assertNotIn("lookup_genre", self.step4_to_7)
        self.assertNotIn("lookup_by_search", self.step4_to_7)
        self.assertNotIn("lookup_by_fingerprint", self.step4_to_7)

    def test_unattended_job_only_moves_verified_evidence(self):
        step4 = self.step4_to_7[:self.step4_to_7.index("# ── 7.")]
        self.assertIn("_decision.verified", step4)
        self.assertIn("not _decision.abstain", step4)

    def test_unattended_job_uses_groq_listening_never_a_text_guess(self):
        # 2026-09-24: the user wants Groq to decide unknown songs. It now LISTENS (ai_listener: language,
        # lyrics, vocals) instead of guessing from the title, and can be switched off with AI_CATCHALL_MOVES.
        step7 = self.step4_to_7[self.step4_to_7.index("# ── 7."):]
        gate = 'if not genre_path and os.getenv("AI_CATCHALL_MOVES", "true").strip().lower() not in ("0", "false", "no", "off"):'
        self.assertIn(gate, step7)
        self.assertLess(step7.index(gate), step7.index("_ai_catchall_route("))
        self.assertNotIn("identify_audio(", step7)


class TestReclassifierEvidenceStep(unittest.TestCase):
    """Step 4 of the catch-all reclassifier now asks the evidence vote instead of trusting the
    first genre Last.fm / MusicBrainz / AcoustID returned."""

    def _run(self, decision_or_calls, *, tags=None, acoustid=True, dry_run=True, skip_ai=True):
        """decision_or_calls: one Decision (returned every call) or a list (one per call)."""
        from services import catchall_reclassifier as cr

        tags = {"TPE1": "Zzyzx Qwerty", "TIT2": "Some Song", **(tags or {})}
        fake_id3 = mock.MagicMock()
        fake_id3.get.side_effect = lambda k, d="": tags.get(k, d)
        side_effect = decision_or_calls if isinstance(decision_or_calls, list) else None
        with mock.patch("mutagen.id3.ID3", return_value=fake_id3), \
             mock.patch("mutagen.mp3.MP3", side_effect=OSError), \
             mock.patch("services.genre_router.spotify_genres_available", return_value=False), \
             mock.patch.object(cr, "_with_timeout", side_effect=lambda fn, seconds=8: fn()), \
             mock.patch.object(cr, "_acoustid_configured", return_value=acoustid), \
             mock.patch("services.genre_evidence.classify_track",
                        side_effect=side_effect, return_value=None if side_effect else decision_or_calls) as classify:
            out = cr.classify_and_route_catchall_track("C:/nonexistent/Song.mp3", dry_run=dry_run, skip_ai_step=skip_ai)
        return out, classify

    def test_a_confident_vote_routes_the_track_and_is_not_flagged_unverified(self):
        from services.genre_evidence import Decision
        d = Decision(genre="Techno", confidence=0.83, abstain=False, reason="voted", sources=["lastfm_track", "musicbrainz"],
                     scores={"Techno": 2.0})
        out, _ = self._run(d)
        self.assertEqual(out["new_folder"], "Library/Techno")
        self.assertEqual(out["source"], "evidence:lastfm_track+musicbrainz")
        self.assertFalse(out["ai_guess"])
        self.assertEqual(out["confidence"], 0.83)
        self.assertIn("Techno", out["evidence"])

    def test_a_vote_resting_on_artist_tags_alone_is_flagged_unverified_and_can_be_held(self):
        d = Decision(genre="Trance", confidence=0.64, abstain=False, reason="voted", sources=["lastfm_artist"])
        self.assertFalse(d.verified)
        out, _ = self._run(d)
        self.assertEqual(out["new_folder"], "Library/Trance")
        self.assertTrue(out["ai_guess"], "artist-only evidence must be reported as UNVERIFIED")
        # With hold_ai_matches (the batch tool's default) it is reported, not executed.
        from services import catchall_reclassifier as cr
        with mock.patch("mutagen.id3.ID3", return_value=mock.MagicMock(get=lambda k, d="": {"TPE1": "Zzyzx", "TIT2": "S"}.get(k, d))), \
             mock.patch("services.genre_router.spotify_genres_available", return_value=False), \
             mock.patch.object(cr, "_with_timeout", side_effect=lambda fn, seconds=8: fn()), \
             mock.patch.object(cr, "_acoustid_configured", return_value=False), \
             mock.patch("services.genre_evidence.classify_track", return_value=d), \
             mock.patch.object(cr, "safe_move") as move:
            held = cr.classify_and_route_catchall_track("C:/nonexistent/S.mp3", hold_ai_matches=True, skip_ai_step=True)
        move.assert_not_called()
        self.assertTrue(held["moved"])                                   # "would move" ...
        self.assertIn("held for manual review", held["reason"])          # ... but held

    def test_a_vote_backed_by_track_level_evidence_is_not_held(self):
        d = Decision(genre="Trance", confidence=0.9, abstain=False, reason="voted", sources=["lastfm_track", "lastfm_artist"])
        self.assertTrue(d.verified)
        out, _ = self._run(d)
        self.assertFalse(out["ai_guess"])

    def test_an_abstention_leaves_the_track_where_it_is(self):
        from services.genre_evidence import Decision
        out, _ = self._run(Decision(reason="conflicting evidence: House 2.00 vs Trance 1.90"))
        self.assertFalse(out["moved"])
        self.assertIsNone(out["new_folder"])
        self.assertIn("conflicting", out["evidence"])

    def test_a_catch_all_verdict_is_never_a_destination(self):
        from services.genre_evidence import Decision
        out, _ = self._run(Decision(genre="Electronic", confidence=0.9, abstain=False, sources=["x"]))
        self.assertIsNone(out["new_folder"])

    def test_tempo_from_the_tbpm_tag_is_passed_to_the_vote(self):
        from services.genre_evidence import Decision
        _, classify = self._run(Decision(), tags={"TBPM": "146"})
        self.assertEqual(classify.call_args.kwargs["bpm"], 146.0)
        _, classify = self._run(Decision(), tags={"TBPM": "not a number"})
        self.assertIsNone(classify.call_args.kwargs["bpm"])

    def test_fingerprint_is_only_spent_when_the_cheap_signals_abstain(self):
        from services.genre_evidence import Decision
        answer = Decision(genre="Trance", confidence=0.9, abstain=False, sources=["fingerprint"])
        out, classify = self._run([Decision(reason="no evidence"), answer])
        self.assertEqual([c.kwargs["use_fingerprint"] for c in classify.call_args_list], [False, True])
        self.assertEqual(out["new_folder"], "Library/Trance")
        # A first-pass answer never triggers the second (slow) pass.
        _, classify = self._run([answer])
        self.assertEqual(classify.call_count, 1)

    def test_no_fingerprint_pass_without_an_acoustid_key(self):
        from services.genre_evidence import Decision
        _, classify = self._run(Decision(reason="no evidence"), acoustid=False)
        self.assertEqual(classify.call_count, 1)

    def test_an_override_short_circuits_before_the_vote(self):
        _, classify = self._run(None, tags={"TPE1": "Charlotte de Witte"})
        classify.assert_not_called()

    def test_an_engine_crash_is_contained(self):
        out, _ = self._run([RuntimeError("boom")])
        self.assertFalse(out["moved"])
        self.assertIn("Could not classify", out["reason"])


if __name__ == "__main__":
    unittest.main()
