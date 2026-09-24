"""
Tests for library_resort.py — the whole-library "find misfiled tracks and move them" tool.

Decision logic is pure, so it is pinned with exact cases. The scan / apply / undo cycle runs on a
throw-away library of tag-only MP3 stubs with the real collision-safe mover and real ID3 writes;
only the classifier, Spotify and MongoDB are faked. What matters most here is safety: a scan
changes nothing, nothing moves without --yes, nothing is overwritten, and undo puts it all back.
"""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import library_resort as lr
from services.genre_evidence import Decision, TagCache


_patches = []


def setUpModule():
    """The REAL Spotify block file and the REAL hand-placement ledger (reports/*.json) must never leak
    into — or be written by — these tests."""
    from services import artist_recovery as ar
    _tmp = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(_tmp.cleanup)
    for target, name, value in ((ar, "BLOCK_FILE", Path(_tmp.name) / "no_block.json"),
                                (lr, "USER_PLACED_PATH", Path(_tmp.name) / "user_placed.json")):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        _patches.append(patcher)


def tearDownModule():
    for patcher in _patches:
        patcher.stop()


def make_mp3(root: Path, rel: str, title: str, artist: str, bpm: str = "", spotify_id: str = "", genre: str = ""):
    from mutagen.id3 import ID3, TBPM, TCON, TIT2, TPE1, TXXX
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 32)
    t = ID3()
    t.add(TIT2(encoding=3, text=title))
    t.add(TPE1(encoding=3, text=artist))
    if bpm:
        t.add(TBPM(encoding=3, text=bpm))
    if spotify_id:
        t.add(TXXX(encoding=3, desc="SPOTIFY_ID", text=[spotify_id]))
    if genre:
        t.add(TCON(encoding=3, text=[genre]))
    t.save(str(p))
    return p


def tag(path, key):
    from mutagen.id3 import ID3
    f = ID3(str(path)).get(key)
    return str(f.text[0]) if f is not None and getattr(f, "text", None) else ""


def decision(genre, conf=1.0, sources=("artist_override",)):
    return Decision(genre=genre, confidence=conf, abstain=False, reason="voted", sources=list(sources))


ABSTAIN = Decision(reason="no evidence")


class TestDecideAction(unittest.TestCase):
    def go(self, current, dec, bpm=None, **kw):
        return lr.decide_action(current, dec, bpm, **kw)

    def test_no_answer_is_unknown_unless_the_tempo_contradicts_the_folder(self):
        self.assertEqual(self.go("House", ABSTAIN, 126)[0], "unknown")
        self.assertEqual(self.go("House", None, None)[0], "unknown")
        action, proposed, note = self.go("House", ABSTAIN, 174)
        self.assertEqual((action, proposed), ("suspect", ""))
        self.assertIn("174", note)

    def test_tempo_never_flags_a_folder_that_has_no_tempo_range(self):
        self.assertEqual(self.go("Bollywood", ABSTAIN, 60)[0], "unknown")

    def test_same_folder_is_ok(self):
        self.assertEqual(self.go("Techno", decision("Techno"))[0], "ok")

    def test_confident_verified_different_folder_is_a_move(self):
        self.assertEqual(self.go("House", decision("Dubstep", 0.9)), ("move", "Dubstep", ""))

    def test_a_verified_answer_below_the_move_bar_is_only_review(self):
        action, proposed, note = self.go("House", decision("Trance", 0.6))
        self.assertEqual((action, proposed), ("review", "Trance"))
        self.assertIn("below", note)

    def test_artist_level_evidence_alone_is_never_a_move(self):
        dec = decision("Trance", 0.95, sources=("lastfm_artist",))
        action, _, note = self.go("House", dec)
        self.assertEqual(action, "review")
        self.assertIn("artist-level", note)

    def test_weak_answers_are_ignored(self):
        self.assertEqual(self.go("House", decision("Trance", 0.4))[0], "unknown")

    def test_the_indian_boundary_is_taste_so_it_is_never_a_move(self):
        for a, b in (("Bollywood", "Punjabi"), ("Punjabi", "Bollywood"), ("Bollywood", "Tamil"),
                     ("Punjabi", "Indian Hip Hop"), ("Indian Hip Hop", "Bollywood")):     # Punjabi rap vs Indian rap: taste
            action, proposed, note = self.go(a, decision(b, 1.0))
            self.assertEqual((action, proposed), ("review", b))
            self.assertIn("boundary", note)

    def test_pop_rnb_hiphop_latin_overlap_so_moves_between_them_are_taste_too(self):
        for a, b in (("Pop", "R&B"), ("Pop", "Latin"), ("International Hip Hop", "Pop"), ("Latin", "International Hip Hop")):
            action, proposed, note = self.go(a, decision(b, 1.0))
            self.assertEqual((action, proposed), ("review", b), (a, b))
            self.assertIn("boundary", note)
        # crossing OUT of the family (Skrillex in Pop -> Dubstep) is a fact-based correction:
        self.assertEqual(self.go("Pop", decision("Dubstep", 1.0))[0], "move")
        self.assertEqual(self.go("House", decision("Pop", 1.0))[0], "move")
        # ...and a garbage-artist track has no human judgment to protect:
        self.assertEqual(self.go("Pop", decision("R&B", 1.0), artist_was_placeholder=True)[0], "move")

    def test_the_boundary_does_not_protect_a_folder_chosen_by_a_guess(self):
        # Artist tag was "Indian"/"Unknown": the file went to Punjabi by default, not by judgment.
        self.assertEqual(self.go("Punjabi", decision("Bollywood", 1.0), artist_was_placeholder=True),
                         ("move", "Bollywood", ""))
        self.assertEqual(self.go("Punjabi", decision("Bollywood", 1.0), artist_was_placeholder=False)[0], "review")
        # ... but it still needs verified, confident evidence:
        self.assertEqual(self.go("Punjabi", decision("Bollywood", 0.9, sources=("lastfm_artist",)),
                                 artist_was_placeholder=True)[0], "review")

    def test_crossing_into_or_out_of_the_indian_family_is_not_the_boundary(self):
        self.assertEqual(self.go("House", decision("Bollywood", 1.0))[0], "move")
        self.assertEqual(self.go("Punjabi", decision("International Hip Hop", 1.0))[0], "move")
        self.assertEqual(self.go("International Hip Hop", decision("Indian Hip Hop", 1.0))[0], "move")   # crosses families

    def test_a_destination_that_is_not_a_crate_is_not_used(self):
        self.assertEqual(self.go("House", decision("Nonsense", 1.0))[0], "unknown")
        self.assertEqual(self.go("House", decision("Electronic", 1.0))[0], "unknown")

    def test_move_bar_is_adjustable(self):
        dec = decision("Trance", 0.8)
        self.assertEqual(self.go("House", dec, move_confidence=0.9)[0], "review")
        self.assertEqual(self.go("House", dec, move_confidence=0.7)[0], "move")


class TestEffectiveArtist(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(lr.effective_artist("Skrillex", "Someone"), ("Skrillex", "tag"))
        self.assertEqual(lr.effective_artist("Unknown", "Kova, Memento Mori"), ("Kova, Memento Mori", "recovered"))
        self.assertEqual(lr.effective_artist("Electronic", ""), ("", "none"))
        self.assertEqual(lr.effective_artist("", "Unknown"), ("", "none"))


class TestClassifyRows(unittest.TestCase):
    def test_placeholder_rows_are_classified_with_the_recovered_artist(self):
        seen = []

        def classify(artist, title, bpm, path):
            seen.append(artist)
            return ABSTAIN

        rows = [lr.Row(folder="Trance", artist="Unknown", recovered_artist="Kova", title="a"),
                lr.Row(folder="Trance", artist="Skrillex", recovered_artist="", title="b"),
                lr.Row(folder="Trance", artist="Unknown", recovered_artist="", title="c")]
        lr.classify_rows(rows, classify)
        self.assertEqual(seen, ["Kova", "Skrillex", ""])
        self.assertIn("no identifiable artist", rows[2].note)

    def test_a_crashing_classifier_leaves_the_track_alone(self):
        rows = [lr.Row(folder="House", artist="X", title="t")]
        lr.classify_rows(rows, mock.Mock(side_effect=RuntimeError("boom")))
        self.assertEqual(rows[0].action, "unknown")

    def test_decision_details_are_copied_to_the_row(self):
        rows = [lr.Row(folder="House", artist="Skrillex", title="t")]
        lr.classify_rows(rows, lambda *a: decision("Dubstep", 0.9, sources=("artist_override", "lastfm_track")))
        r = rows[0]
        self.assertEqual((r.action, r.proposed, r.verified, r.confidence), ("move", "Dubstep", True, 0.9))
        self.assertEqual(r.sources, ["artist_override", "lastfm_track"])
        self.assertIn("Dubstep", r.why)


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.out = Path(self._t.name, "_reports")
        # every test gets its OWN hand-placement ledger, so one test's decisions never leak into the next
        ledger = mock.patch.object(lr, "USER_PLACED_PATH", Path(self._t.name, "_ledger", "user_placed.json"))
        ledger.start()
        self.addCleanup(ledger.stop)
        self.files = {
            "wrong":    make_mp3(self.root, "Library/House/Skrillex - Bangarang.mp3", "Bangarang", "Skrillex", "110", genre="House"),
            "right":    make_mp3(self.root, "Library/House/Fisher - Losing It.mp3", "Losing It", "Fisher", "125", genre="House"),
            "recover":  make_mp3(self.root, "Library/Trance/Acelerar.mp3", "Acelerar", "Unknown", "145", spotify_id="sid1", genre="Trance"),
            "boundary": make_mp3(self.root, "Library/Bollywood/Dil Chori.mp3", "Dil Chori", "Yo Yo Honey Singh", "100", genre="Bollywood"),
            "unknown":  make_mp3(self.root, "Library/Pop/Mystery.mp3", "Mystery", "Nobody Known", "100", genre="Pop"),
            "electronic": make_mp3(self.root, "Library/Electronic/Loose.mp3", "Loose", "Skrillex", "140"),
            "psy":      make_mp3(self.root, "Library/PSY/Psy One.mp3", "Psy One", "Skrillex", "145"),
        }
        self.decisions = {
            "Bangarang": decision("Dubstep"),
            "Losing It": decision("House", 0.9),
            "Acelerar": decision("Trance", 0.9, sources=("lastfm_artist",)),   # agrees, but artist-level
            "Dil Chori": decision("Punjabi"),
            "Mystery": ABSTAIN,
            "Loose": decision("Dubstep"),
            "Psy One": decision("Dubstep"),
        }
        self.classified_with = []

    def classify(self, artist, title, bpm, path):
        self.classified_with.append((title, artist))
        return self.decisions[title]

    def scan(self, **kw):
        cache = TagCache(None)
        kw.setdefault("fetch", lambda sid: {"artists": ["Kova", "Memento Mori"], "title": "Acelerar", "album": "A"})
        kw.setdefault("playlist_tracks", [])                                # never touch Spotify in tests
        return lr.scan(self.root, recovery_cache=cache, classify=self.classify, pace_s=0, out=lambda *_: None, **kw)


class TestScan(LibraryCase):
    def test_scan_changes_nothing(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*.mp3")}
        self.scan()
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.mp3")})
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*.mp3")), sorted(str(p) for p in before))

    def test_actions(self):
        rows, _ = self.scan()
        by = {r.title: r for r in rows}
        self.assertEqual((by["Bangarang"].action, by["Bangarang"].proposed), ("move", "Dubstep"))
        self.assertEqual(by["Losing It"].action, "ok")
        self.assertEqual(by["Acelerar"].action, "ok")
        self.assertEqual(by["Dil Chori"].action, "review")
        self.assertEqual(by["Mystery"].action, "unknown")

    def test_catch_all_and_hand_curated_folders_are_skipped_by_default(self):
        rows, _ = self.scan()
        titles = {r.title for r in rows}
        self.assertNotIn("Loose", titles)                                   # Library/Electronic
        self.assertNotIn("Psy One", titles)                                 # Library/PSY

    def test_electronic_can_be_included(self):
        rows, _ = self.scan(include_electronic=True)
        self.assertIn("Loose", {r.title for r in rows})
        self.assertNotIn("Psy One", {r.title for r in rows})

    def test_missing_artists_are_recovered_and_used_for_classification(self):
        rows, summary = self.scan()
        r = next(r for r in rows if r.title == "Acelerar")
        self.assertEqual(r.recovered_artist, "Kova, Memento Mori")
        self.assertEqual(r.artist, "Unknown")                                # the tag itself is untouched
        self.assertIn(("Acelerar", "Kova, Memento Mori"), self.classified_with)
        self.assertEqual((summary["placeholder_tracks"], summary["with_spotify_id"], summary["recovered"]), (1, 1, 1))

    def test_no_recover_never_calls_spotify(self):
        fetch = mock.Mock()
        rows, summary = self.scan(recover=False, fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(next(r for r in rows if r.title == "Acelerar").recovered_artist, "")
        self.assertEqual(summary["recovered"], 0)

    def test_rows_are_ordered_moves_first_with_sequential_indexes(self):
        rows, _ = self.scan()
        self.assertEqual([r.idx for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual(rows[0].action, "move")
        self.assertEqual(rows[-1].action, "ok")

    def test_spotify_rate_limit_is_reported_not_fatal(self):
        from services.artist_recovery import RateLimited

        def limited(sid):
            raise RateLimited("429")
        rows, summary = self.scan(fetch=limited)
        self.assertIn("rate limited", summary["stopped"])
        self.assertEqual(len(rows), 5)


class TestIdentityValidation(unittest.TestCase):
    """The Spotify id stored in a file is sometimes WRONG (17% on the real library: the old pipeline
    searched with the placeholder artist "Indian"). A recovered artist is only used when the id's
    title matches the file, and lengths agree; playlist data can identify files that have no id."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.seen = []
        self.lengths = {}
        patcher = mock.patch.object(lr, "file_seconds", side_effect=lambda p: self.lengths.get(Path(p).stem))
        patcher.start()
        self.addCleanup(patcher.stop)

    def lib(self, *specs):
        """spec = (folder, title, artist_tag, spotify_id, file_secs)"""
        for folder, title, artist, sid, secs in specs:
            make_mp3(self.root, f"Library/{folder}/{title}.mp3", title, artist, spotify_id=sid)
            self.lengths[title] = secs

    def scan(self, spotify, playlist=(), **kw):
        def classify(artist, title, bpm, path):
            self.seen.append((title, artist))
            return ABSTAIN
        return lr.scan(self.root, recovery_cache=TagCache(None), fetch=lambda sid: spotify.get(sid),
                       playlist_tracks=list(playlist), classify=classify, pace_s=0, out=lambda *_: None, **kw)

    def row(self, rows, title):
        return next(r for r in rows if r.title == title)

    def test_an_id_pointing_at_another_song_is_not_trusted(self):
        self.lib(("Punjabi", "Kiya Kiya", "Indian", "bad1", 200.0), ("Punjabi", "Tu Hi Hai", "Indian", "good1", 200.0))
        rows, summary = self.scan({
            "bad1": {"artists": ["The Bulbine for Indian"], "title": "Candytuft Parsley", "duration_ms": 200_000},
            "good1": {"artists": ["Arijit Singh"], "title": "Tu Hi Hai", "duration_ms": 201_000},
        })
        bad, good = self.row(rows, "Kiya Kiya"), self.row(rows, "Tu Hi Hai")
        self.assertEqual(bad.recovered_artist, "")
        self.assertIn("different song", bad.id_check)
        self.assertNotIn(("Kiya Kiya", "The Bulbine for Indian"), self.seen)      # junk never reaches the classifier
        self.assertIn(("Kiya Kiya", ""), self.seen)
        self.assertEqual((good.recovered_artist, good.id_check), ("Arijit Singh", "ok"))
        self.assertEqual((summary["ids_ok"], summary["ids_wrong"], summary["recovered"]), (1, 1, 1))

    def test_the_right_title_but_the_wrong_audio_is_not_trusted_either(self):
        self.lib(("Drum & Bass", "Dagger", "Unknown", "sid", 276.0))
        rows, _ = self.scan({"sid": {"artists": ["Slowdive"], "title": "Dagger", "duration_ms": 330_000}})
        r = rows[0]
        self.assertEqual(r.recovered_artist, "")
        self.assertIn("54s shorter", r.id_check)

    def test_playlist_data_counts_as_free_identity(self):
        self.lib(("Trance", "Acelerar", "Unknown", "sid", 245.0))
        playlist = [{"id": "sid", "title": "Acelerar", "artist": "Kova", "duration_ms": 245_000}]
        fetch = mock.Mock()
        rows, summary = lr.scan(self.root, recovery_cache=TagCache(None), fetch=fetch, playlist_tracks=playlist,
                                classify=lambda *a: ABSTAIN, pace_s=0, out=lambda *_: None)
        fetch.assert_not_called()                                                  # the playlist answered
        self.assertEqual(rows[0].recovered_artist, "Kova")
        self.assertEqual(summary["calls"], 0)

    def test_a_file_with_no_id_is_identified_by_title_and_length(self):
        self.lib(("House", "Move", "Electronic", "", 190.0))
        playlist = [{"id": "p1", "title": "Move", "artist": "Adam Port", "duration_ms": 191_000}]
        rows, summary = self.scan({}, playlist)
        r = rows[0]
        self.assertEqual(r.recovered_artist, "Adam Port")
        self.assertIn("playlist", r.id_check)
        self.assertEqual(summary["playlist_matched"], 1)
        self.assertIn(("Move", "Adam Port"), self.seen)

    def test_a_wrong_id_can_still_be_rescued_by_the_playlist(self):
        self.lib(("Punjabi", "Woh Ladki Jo", "Indian", "bad", 180.0))
        playlist = [{"id": "p1", "title": "Woh Ladki Jo", "artist": "Abhijeet", "duration_ms": 180_500}]
        rows, _ = self.scan({"bad": {"artists": ["Junk"], "title": "Something Else", "duration_ms": 1}}, playlist)
        self.assertEqual(rows[0].recovered_artist, "Abhijeet")
        self.assertIn("the id in the file was wrong", rows[0].id_check)

    def test_playlist_matching_needs_the_length_to_agree_and_the_artist_to_be_unambiguous(self):
        self.lib(("House", "Move", "Electronic", "", 190.0), ("House", "Same Title", "Unknown", "", 200.0),
                 ("House", "Way Off", "Unknown", "", 300.0))
        playlist = [
            {"id": "1", "title": "Move", "artist": "Adam Port", "duration_ms": 260_000},           # 70 s off
            {"id": "2", "title": "Same Title", "artist": "Artist A", "duration_ms": 200_000},
            {"id": "3", "title": "Same Title", "artist": "Artist B", "duration_ms": 200_500},      # two different artists
            {"id": "4", "title": "Way Off", "artist": "Nobody", "duration_ms": 100_000},
        ]
        rows, summary = self.scan({}, playlist)
        self.assertEqual({r.title: r.recovered_artist for r in rows}, {"Move": "", "Same Title": "", "Way Off": ""})
        self.assertEqual(summary["playlist_matched"], 0)

    def test_a_placeholder_artist_from_the_playlist_is_not_accepted(self):
        self.lib(("House", "Move", "Electronic", "", 190.0))
        rows, _ = self.scan({}, [{"id": "p", "title": "Move", "artist": "Unknown", "duration_ms": 190_000}])
        self.assertEqual(rows[0].recovered_artist, "")

    def test_a_right_titled_file_far_from_spotifys_length_is_flagged_suspect(self):
        self.lib(("Bollywood", "Chaiyya Chaiyya", "Sukhwinder Singh", "s1", 180.0),
                 ("Bollywood", "Fine Song", "Someone Real", "s2", 200.0),
                 ("Bollywood", "Other Title", "Someone Else", "s3", 100.0))
        playlist = [{"id": "s1", "title": "Chaiyya Chaiyya", "artist": "Sukhwinder Singh", "duration_ms": 221_000},   # 41 s
                    {"id": "s2", "title": "Fine Song", "artist": "Someone Real", "duration_ms": 203_000},              # 3 s
                    {"id": "s3", "title": "Completely Different", "artist": "Someone Else", "duration_ms": 300_000}]   # wrong id
        rows, summary = self.scan({}, playlist)
        chaiyya = self.row(rows, "Chaiyya Chaiyya")
        self.assertEqual(chaiyya.action, "suspect")
        self.assertIn("41s shorter", chaiyya.note)
        self.assertEqual(self.row(rows, "Fine Song").action, "unknown")           # within tolerance: untouched
        self.assertEqual(self.row(rows, "Other Title").action, "unknown")         # the id is for another song: no length claim
        self.assertEqual(summary["length_suspects"], 1)

    def test_a_length_flag_never_overrides_a_move(self):
        make_mp3(self.root, "Library/House/Bangarang.mp3", "Bangarang", "Skrillex", spotify_id="s1")
        self.lengths["Bangarang"] = 100.0
        rows, _ = lr.scan(self.root, recovery_cache=TagCache(None), fetch=lambda s: None, pace_s=0, out=lambda *_: None,
                          playlist_tracks=[{"id": "s1", "title": "Bangarang", "artist": "Skrillex", "duration_ms": 215_000}],
                          classify=lambda *a: decision("Dubstep"))
        self.assertEqual(rows[0].action, "move")
        self.assertIn("shorter", rows[0].note)

    def test_an_unavailable_playlist_does_not_stop_the_scan(self):
        def broken():
            raise RuntimeError("Spotify cooling down")
        lines = []
        self.assertEqual(lr.load_playlist(broken, out=lines.append), [])
        self.assertIn("playlist unavailable", lines[0])
        self.lib(("House", "T", "Someone", "", None))
        rows, _ = lr.scan(self.root, recovery_cache=TagCache(None), fetch=lambda s: None, pace_s=0, out=lambda *_: None,
                          classify=lambda *a: ABSTAIN, use_playlist=False)
        self.assertEqual(len(rows), 1)

    def test_summary_reports_the_identity_check(self):
        self.lib(("Punjabi", "Kiya Kiya", "Indian", "bad1", 200.0))
        rows, summary = self.scan({"bad1": {"artists": ["Junk"], "title": "Candytuft Parsley", "duration_ms": 200_000}})
        text = lr.render_summary(rows, summary)
        self.assertIn("1 point at a DIFFERENT song", text)

    def test_the_id_check_reaches_the_spreadsheet(self):
        self.lib(("Punjabi", "Kiya Kiya", "Indian", "bad1", 200.0))
        rows, summary = self.scan({"bad1": {"artists": ["Junk"], "title": "Candytuft Parsley", "duration_ms": 200_000}})
        rows[0].action = "review"
        _, cp, _ = lr.write_reports(rows, self.root, summary, out_dir=self.root / "_r")
        with open(cp, newline="", encoding="utf-8-sig") as fh:
            self.assertIn("different song", next(csv.DictReader(fh))["id_check"])


class TestHandPlacedProtection(unittest.TestCase):
    """The user's manual sorting outranks any suggestion. A file whose genre tag names a DIFFERENT crate
    than the one it sits in was moved there by a person (measured: 124 files, 72 still tagged
    'Electronic'); a suggestion must never move it back."""

    def run_rows(self, dec, **row):
        rows = [lr.Row(folder="House", artist="Skrillex", title="t", **row)]
        lr.classify_rows(rows, lambda *a: dec)
        return rows[0]

    def test_tag_names_another_crate_so_a_move_is_downgraded_to_review(self):
        r = self.run_rows(decision("Dubstep"), tag_folder="Dubstep", genre_tag="Dubstep")   # tag == suggestion!
        self.assertEqual((r.action, r.proposed), ("review", "Dubstep"))
        self.assertIn("by hand", r.note)

    def test_tag_agreeing_with_the_folder_changes_nothing(self):
        r = self.run_rows(decision("Dubstep"), tag_folder="House", genre_tag="House")
        self.assertEqual(r.action, "move")

    def test_no_tag_changes_nothing(self):
        self.assertEqual(self.run_rows(decision("Dubstep")).action, "move")

    def test_a_mismatch_on_a_non_move_row_is_only_noted(self):
        r = self.run_rows(decision("House"), tag_folder="Electronic", genre_tag="Electronic")
        self.assertEqual(r.action, "ok")
        self.assertIn("moved by hand?", r.note)

    def test_tag_text_maps_to_crates(self):
        for text, want in (("Electronic", "Electronic"), ("Drum and Bass", "Drum & Bass"), ("drum & bass", "Drum & Bass"),
                           ("Hip-Hop", "International Hip Hop"), ("Hip Hop", "International Hip Hop"),   # tags written before the split
                           ("Desi Hip Hop", "Indian Hip Hop"), ("Indian Hip Hop", "Indian Hip Hop"),
                           ("RNB", "R&B"), ("UK Garage", "UK Garage"), ("Psytrance", "Trance"),
                           ("", ""), ("Some Odd Genre", ""), ("  house ", "House")):
            self.assertEqual(lr.tag_folder_of(text), want, text)


class TestIndianVersionGuard(unittest.TestCase):
    def go(self, current, answer, title, **kw):
        return lr.decide_action(current, decision(answer, 1.0), None, title=title, **kw)[:2]

    def test_a_hindi_version_stays_in_the_indian_crates(self):
        self.assertEqual(self.go("Bollywood", "R&B", "Ride It (Kya Yehi Pyaar Hai) - Hindi Version"), ("review", "R&B"))
        self.assertEqual(self.go("Punjabi", "Pop", "Song (Punjabi Remix)"), ("review", "Pop"))

    def test_without_the_marker_it_is_an_ordinary_move(self):
        self.assertEqual(self.go("Bollywood", "R&B", "Ride It"), ("move", "R&B"))

    def test_it_only_applies_inside_the_indian_crates_and_only_when_leaving_them(self):
        self.assertEqual(self.go("House", "Pop", "Song - Hindi Version")[0], "move")
        self.assertEqual(self.go("Pop", "Bollywood", "Song - Hindi Version")[0], "move")
        self.assertEqual(self.go("Bollywood", "Bollywood", "Song - Hindi Version")[0], "ok")


class TestTagWriting(unittest.TestCase):
    def test_the_id3_version_of_a_file_is_preserved(self):
        from mutagen.id3 import ID3, TIT2, TPE1
        with tempfile.TemporaryDirectory() as d:
            for version in (3, 4):
                p = Path(d, f"v{version}.mp3")
                p.write_bytes(b"\x00" * 64)
                t = ID3()
                t.add(TIT2(encoding=3, text="T"))
                t.add(TPE1(encoding=3, text="A"))
                t.save(str(p), v2_version=version)
                lr.write_tags(str(p), genre="House")
                self.assertEqual(ID3(str(p)).version[1], version)
                self.assertEqual(tag(p, "TCON"), "House")
                self.assertEqual(tag(p, "TPE1"), "A")                       # untouched frames stay untouched


class TestGenreSyncAndLeaveAlone(LibraryCase):
    def setUp(self):
        super().setUp()
        # hand-moved: pipeline tagged it "Electronic", the user put it in Techno
        self.hand = make_mp3(self.root, "Library/Techno/Hand Moved.mp3", "Hand Moved", "Anyma", genre="Electronic")
        self.decisions["Hand Moved"] = decision("Techno", 0.9)
        rows, _ = self.scan()
        self.rows = [vars_of(r) for r in rows]
        self.manifest = self.out / "undo.json"

    def apply(self, **kw):
        kw.setdefault("manifest_path", self.manifest)
        return lr.apply_rows(lr.select_rows(self.rows), self.root, index_fn=lambda *a: 1, out=lambda *_: None, **kw)

    def mismatched(self):
        return [r for r in self.rows if r.get("tag_folder") and r["tag_folder"] != r["folder"]]

    def test_the_scan_records_the_genre_tag_and_the_crate_it_names(self):
        r = next(r for r in self.rows if r["title"] == "Hand Moved")
        self.assertEqual((r["genre_tag"], r["tag_folder"], r["folder"]), ("Electronic", "Electronic", "Techno"))
        self.assertIn("moved by hand?", r["note"])

    def test_sync_sets_the_tag_to_the_folder_and_undo_restores_it(self):
        res = self.apply(dry_run=False, sync_genre_rows=self.mismatched())
        self.assertEqual(tag(self.hand, "TCON"), "Techno")
        self.assertGreaterEqual(res["genre_synced"], 1)
        rec = [r for r in json.loads(self.manifest.read_text(encoding="utf-8"))["records"]
               if r["old_path"] == str(self.hand)][0]
        self.assertEqual((rec["old_genre"], rec["old_path"] == rec["new_path"]), ("Electronic", True))
        lr.undo_manifest(json.loads(self.manifest.read_text(encoding="utf-8")), dry_run=False,
                         index_fn=lambda *a: 1, out=lambda *_: None)
        self.assertEqual(tag(self.hand, "TCON"), "Electronic")

    def test_sync_preview_changes_nothing(self):
        self.apply(dry_run=True, sync_genre_rows=self.mismatched())
        self.assertEqual(tag(self.hand, "TCON"), "Electronic")
        self.assertFalse(self.manifest.exists())

    def test_files_whose_tag_already_matches_are_not_rewritten(self):
        before = self.files["right"].read_bytes()
        self.apply(dry_run=False, sync_genre_rows=[r for r in self.rows if r["title"] == "Losing It"])
        self.assertEqual(self.files["right"].read_bytes(), before)

    def test_a_row_being_moved_is_not_tagged_twice(self):
        wrong = next(r for r in self.rows if r["title"] == "Bangarang")
        wrong = dict(wrong, tag_folder="Techno", genre_tag="Techno")            # pretend its tag also mismatches
        res = self.apply(dry_run=False, sync_genre_rows=[wrong])
        moved = self.root / "Library" / "Dubstep" / "Skrillex - Bangarang.mp3"
        self.assertEqual(tag(moved, "TCON"), "Dubstep")                          # the destination's tag, once
        self.assertEqual(res["genre_synced"], 0)

    def test_leave_alone_files_are_neither_moved_nor_tagged(self):
        res = self.apply(dry_run=False, sync_genre_rows=self.mismatched(),
                         leave_alone={"skrillex - bangarang.MP3".upper().replace(".MP3", ".mp3"), "Hand Moved.mp3"})
        self.assertTrue(self.files["wrong"].exists(), "a leave-alone file must not be moved")
        self.assertEqual(tag(self.hand, "TCON"), "Electronic")
        self.assertEqual(res["moved"], [])

    def test_trusted_artist_rows_need_a_full_identity_check(self):
        rows = [{"recovered_artist": "A", "id_check": "ok"},
                {"recovered_artist": "B", "id_check": "matched to your playlist by title + length"},
                {"recovered_artist": "C", "id_check": "ok (title matches; length not checked)"},
                {"recovered_artist": "D", "id_check": "Spotify id in the file points at a different song ('x')"},
                {"recovered_artist": "", "id_check": "ok"}]
        self.assertEqual([r["recovered_artist"] for r in lr.trusted_artist_rows(rows)], ["A", "B"])


class TestLearnFromLibrary(unittest.TestCase):
    """`learn` turns the corrected folders into artist-memory rules, so FUTURE songs by an artist you have
    already sorted land where you put them."""

    def rows(self, artist, crate, n, action="ok", **kw):
        base = dict(folder=crate, action=action, artist_used=artist, tag_folder="")
        base.update(kw)
        return [dict(base) for _ in range(n)]

    def test_an_artist_whose_tracks_sit_in_one_crate_is_a_consensus(self):
        got = lr.library_consensus(self.rows("Skrillex", "Dubstep", 5) + self.rows("Skrillex", "Pop", 1))
        self.assertEqual(got, [{"artist": "Skrillex", "crate": "Dubstep", "tracks": 6, "share": 0.83}])

    def test_thresholds(self):
        self.assertEqual(lr.library_consensus(self.rows("A", "House", 2)), [])                                # too few tracks
        self.assertEqual(lr.library_consensus(self.rows("A", "House", 2) + self.rows("A", "Techno", 1)), [])   # 67% < 80%
        self.assertEqual(len(lr.library_consensus(self.rows("A", "House", 3))), 1)

    def test_unverified_folders_never_teach(self):
        # an artist whose tracks were all AI-guessed into Trance (nobody could verify them) must not become a rule
        self.assertEqual(lr.library_consensus(self.rows("A", "Trance", 9, action="unknown")), [])
        self.assertEqual(lr.library_consensus(self.rows("A", "Trance", 9, action="suspect")), [])
        # ...but the same tracks count once the user has placed them
        self.assertEqual(len(lr.library_consensus(self.rows("A", "Trance", 3, action="unknown", hand_placed=True))), 1)

    def test_contested_and_untrustworthy_rows_do_not_count(self):
        rows = (self.rows("A", "House", 3) + self.rows("A", "Techno", 4, action="move")
                + self.rows("A", "Techno", 4, action="review") + self.rows("A", "Techno", 4, action="ear")
                + self.rows("A", "Techno", 4, action="unknown"))
        self.assertEqual(lr.library_consensus(rows)[0]["crate"], "House")                          # pending moves ignored
        self.assertEqual(lr.library_consensus(self.rows("Unknown", "House", 5) + self.rows("Indian", "Punjabi", 9)), [])
        self.assertEqual(lr.library_consensus(self.rows("A", "Electronic", 5) + self.rows("B", "NeedsReview", 5)), [])

    def test_files_you_placed_by_hand_count_whatever_evidence_said(self):
        rows = self.rows("A", "Techno", 3, action="review", tag_folder="Electronic")
        self.assertEqual(lr.library_consensus(rows)[0]["crate"], "Techno")

    def test_a_credit_counts_for_its_lead_artist_and_names_are_normalised(self):
        rows = (self.rows("Skrillex, Poo Bear", "Dubstep", 2) + self.rows("SKRILLEX", "Dubstep", 1)
                + self.rows("skrillex feat. Diplo", "Dubstep", 1))
        got = lr.library_consensus(rows)
        self.assertEqual((len(got), got[0]["tracks"]), (1, 4))

    def test_every_crate_maps_to_a_real_taxonomy_key(self):
        from services.genre_router import GENRE_TAXONOMY
        for crate, genre in lr.CRATE_TO_GENRE.items():
            self.assertIn(genre, GENRE_TAXONOMY, crate)

    def test_preview_records_nothing(self):
        record, lookup = mock.Mock(), mock.Mock(return_value=None)
        res = lr.learn_from_library(self.rows("A", "House", 4), dry_run=True, record=record, lookup=lookup, out=lambda *_: None)
        record.assert_not_called()
        self.assertEqual((res["considered"], res["recorded"]), (1, 1))

    def test_confidence_scales_with_the_evidence(self):
        record, lookup = mock.Mock(), mock.Mock(return_value=None)
        lr.learn_from_library(self.rows("Few", "House", 4) + self.rows("Many", "Techno", 8), dry_run=False,
                              record=record, lookup=lookup, out=lambda *_: None)
        calls = Counter(c.args[0] for c in record.call_args_list)
        self.assertEqual((calls["Few"], calls["Many"]), (2, 4))                     # 0.5 and 1.0 confidence
        self.assertTrue(all(c.kwargs["source"] == "library_consensus" for c in record.call_args_list))
        self.assertIn(mock.call("Many", "Techno", source="library_consensus"), record.call_args_list)

    def test_it_is_idempotent(self):
        record = mock.Mock()
        known = {"genre": "House", "confidence": 0.5}
        res = lr.learn_from_library(self.rows("A", "House", 4), dry_run=False, record=record,
                                    lookup=mock.Mock(return_value=known), out=lambda *_: None)
        record.assert_not_called()
        self.assertEqual((res["already_known"], res["recorded"]), (1, 0))

    def test_more_evidence_than_before_raises_the_confidence(self):
        record = mock.Mock()
        lr.learn_from_library(self.rows("A", "House", 8), dry_run=False, record=record,
                              lookup=mock.Mock(return_value={"genre": "House", "confidence": 0.5}), out=lambda *_: None)
        self.assertEqual(record.call_count, 4)

    def test_a_changed_mind_is_reported_and_recorded(self):
        record = mock.Mock()
        res = lr.learn_from_library(self.rows("A", "Techno", 4), dry_run=False, record=record,
                                    lookup=mock.Mock(return_value={"genre": "House", "confidence": 1.0}), out=lambda *_: None)
        self.assertEqual((res["changed"], res["recorded"], record.call_count), (1, 1, 2))

    def test_command_line_preview_then_record(self):
        import io, contextlib
        with tempfile.TemporaryDirectory() as d:
            report = Path(d, "r.json")
            report.write_text(json.dumps({"root": d, "rows": self.rows("Skrillex", "Dubstep", 4)}), encoding="utf-8")
            with mock.patch("services.artist_memory_service.record_move") as rec, \
                 mock.patch("services.artist_memory_service.lookup_artist", return_value=None):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(lr.main(["learn", "--report", str(report)]), 0)
                self.assertIn("PREVIEW", buf.getvalue())
                rec.assert_not_called()
                with contextlib.redirect_stdout(io.StringIO()):
                    lr.main(["learn", "--report", str(report), "--yes"])
                self.assertEqual(rec.call_count, 2)


class TestAudioFusion(unittest.TestCase):
    """How the audio model's opinion is fused with what the metadata said. The rule of thumb: audio may
    fill silence and confirm weak evidence, may question a coarse curated table, but never overrules a
    person, and never decides a matter of taste."""

    MODEL = __import__("types").SimpleNamespace(
        thresholds={"Techno": 0.7, "House": 0.7, "Bollywood": 0.7, "Punjabi": 0.7, "Pop": 0.7, "R&B": 0.7, "Trance": 1.01},
        # the stricter bar for MOVING on sound alone: more confident than any mistake made on confirmed tracks
        move_thresholds={"Techno": 0.8, "House": 0.8, "Bollywood": 0.85, "Punjabi": 0.85, "Pop": 0.8, "R&B": 0.8,
                         "Trance": 1.01})

    def row(self, **kw):
        base = dict(path="/lib/x.mp3", folder="Trance", action="unknown", note="", proposed="", sources=[])
        base.update(kw)
        return lr.Row(**base)

    def fuse(self, row, crate, conf):
        stats = lr.apply_audio([row], {row.path: (crate, conf)}, self.MODEL)
        return row, stats

    def test_audio_places_a_track_nothing_else_could(self):
        r, stats = self.fuse(self.row(), "Bollywood", 0.9)
        self.assertEqual((r.action, r.proposed), ("ear", "Bollywood"))
        self.assertIn("by ear", r.note)
        self.assertIn("audio_model", r.sources)
        self.assertEqual(stats["ear"], 1)

    def test_an_unknown_that_sounds_like_its_folder_is_confirmed(self):
        r, stats = self.fuse(self.row(folder="Techno"), "Techno", 0.9)
        self.assertEqual(r.action, "ok")
        self.assertIn("audio agrees", r.note)
        self.assertEqual(stats["confirmed"], 1)

    def test_below_the_measured_bar_it_stays_silent_but_is_recorded(self):
        r, _ = self.fuse(self.row(), "Bollywood", 0.6)
        self.assertEqual((r.action, r.audio_genre, r.audio_conf), ("unknown", "Bollywood", 0.6))

    def test_confident_enough_to_confirm_is_not_confident_enough_to_move(self):
        # 0.75 clears the precision bar (0.70) but not the bar for moving (0.85): measured on the real
        # library, Brazilian / Latin / R&B tracks "sounded like Bollywood" at 55-67% — those must not move.
        r, _ = self.fuse(self.row(folder="Latin"), "Bollywood", 0.75)
        self.assertEqual(r.action, "unknown")
        r, _ = self.fuse(self.row(folder="Bollywood"), "Bollywood", 0.75)              # ...but it may still CONFIRM
        self.assertEqual(r.action, "ok")

    def test_a_curated_move_is_only_questioned_by_audio_that_may_move(self):
        r, _ = self.fuse(self.row(folder="Pop", action="move", proposed="Techno", verified=True), "Bollywood", 0.75)
        self.assertEqual(r.action, "move")

    def test_an_ok_track_is_only_flagged_by_audio_that_may_move(self):
        r, _ = self.fuse(self.row(folder="House", action="ok"), "Bollywood", 0.75)
        self.assertEqual(r.action, "ok")

    def test_a_crate_it_could_not_learn_is_never_acted_on(self):
        r, _ = self.fuse(self.row(folder="House"), "Trance", 0.99)              # threshold 1.01 = never
        self.assertEqual(r.action, "unknown")

    def test_a_taste_boundary_is_never_decided_by_ear(self):
        r, _ = self.fuse(self.row(folder="Punjabi"), "Bollywood", 0.95)
        self.assertEqual((r.action, r.proposed), ("review", "Bollywood"))
        self.assertIn("taste", r.note)

    def test_audio_confirms_a_weak_suggestion(self):
        r, stats = self.fuse(self.row(folder="House", action="review", proposed="Techno",
                                      note="artist-level evidence only"), "Techno", 0.85)
        self.assertEqual((r.action, r.verified), ("move", True))
        self.assertIn("two independent signals", r.note)
        self.assertEqual(stats["upgraded"], 1)

    def test_agreement_below_60_percent_is_not_enough_to_upgrade(self):
        r, _ = self.fuse(self.row(folder="House", action="review", proposed="Techno", note="artist-level evidence only"),
                         "Techno", 0.58)
        self.assertEqual(r.action, "review")

    def test_a_boundary_suggestion_is_not_upgraded(self):
        r, _ = self.fuse(self.row(folder="Punjabi", action="review", proposed="Bollywood",
                                  note="Bollywood/Punjabi/Tamil boundary — a matter of taste, your call"), "Bollywood", 0.9)
        self.assertEqual(r.action, "review")

    def test_audio_disagreeing_with_a_weak_suggestion_changes_nothing(self):
        r, _ = self.fuse(self.row(folder="House", action="review", proposed="Techno"), "Bollywood", 0.9)
        self.assertEqual((r.action, r.proposed), ("review", "Techno"))

    def test_a_curated_move_that_sounds_wrong_is_questioned(self):
        r, stats = self.fuse(self.row(folder="Pop", action="move", proposed="Techno", verified=True), "Bollywood", 0.9)
        self.assertEqual(r.action, "review")
        self.assertIn("sounds like Bollywood", r.note)
        self.assertEqual(stats["downgraded"], 1)

    def test_a_curated_move_that_sounds_right_is_untouched(self):
        r, _ = self.fuse(self.row(folder="Pop", action="move", proposed="Techno", verified=True), "Techno", 0.95)
        self.assertEqual(r.action, "move")

    def test_a_confirmed_track_that_sounds_like_another_crate_is_flagged_not_moved(self):
        r, stats = self.fuse(self.row(folder="House", action="ok"), "Bollywood", 0.9)
        self.assertEqual(r.action, "suspect")
        self.assertIn("folder says House", r.note)
        self.assertEqual(stats["flagged"], 1)
        r, _ = self.fuse(self.row(folder="Pop", action="ok"), "R&B", 0.9)              # taste boundary: leave it
        self.assertEqual(r.action, "ok")

    def test_files_you_placed_by_hand_are_never_touched(self):
        for action in ("unknown", "ok", "review", "suspect"):
            r, _ = self.fuse(self.row(folder="House", action=action, tag_folder="Electronic"), "Bollywood", 0.99)
            self.assertEqual(r.action, action, action)
            self.assertEqual(r.audio_genre, "Bollywood")                                 # still recorded

    def test_a_tempo_only_suspect_is_resolved_but_a_wrong_length_is_not(self):
        r, _ = self.fuse(self.row(folder="Techno", action="suspect", note="97 BPM does not fit Techno"), "Techno", 0.9)
        self.assertEqual(r.action, "ok")
        r, _ = self.fuse(self.row(folder="Techno", action="suspect",
                                  note="file is 41s shorter than Spotify's version (a different edit, or the wrong audio)"),
                         "Techno", 0.9)
        self.assertEqual(r.action, "suspect")

    def test_rows_without_a_prediction_are_skipped(self):
        r = self.row()
        stats = lr.apply_audio([r], {}, self.MODEL)
        self.assertEqual((r.action, stats["scored"]), ("unknown", 0))

    def test_select_rows_includes_ear_rows_only_on_request(self):
        rows = [{"idx": 1, "action": "move", "proposed": "House"}, {"idx": 2, "action": "ear", "proposed": "Techno"}]
        self.assertEqual([r["idx"] for r in lr.select_rows(rows)], [1])
        self.assertEqual([r["idx"] for r in lr.select_rows(rows, include_audio=True)], [1, 2])

    def test_the_spreadsheet_does_not_pre_tick_ear_rows(self):
        with tempfile.TemporaryDirectory() as d:
            row = lr.Row(idx=1, path="x", rel="x", folder="Trance", title="t", action="ear", proposed="Bollywood",
                         audio_genre="Bollywood", audio_conf=0.91)
            _, cp, _ = lr.write_reports([row], Path(d), {}, out_dir=Path(d, "r"))
            with open(cp, newline="", encoding="utf-8-sig") as fh:
                rec = next(csv.DictReader(fh))
            self.assertEqual((rec["apply"], rec["audio_genre"], rec["audio_conf"]), ("", "Bollywood", "0.91"))


class TestRunAudio(unittest.TestCase):
    """End to end with synthetic audio features: train on confirmed rows, then place an unknown."""

    def build(self, dim=12):
        import numpy as np
        rng = np.random.default_rng(0)
        centers = {"Techno": 0.0, "Bollywood": 6.0, "House": 12.0}
        rows, feats = [], {}
        for crate, c in centers.items():
            for i in range(40):
                p = f"/lib/{crate}/{i}.mp3"
                rows.append(lr.Row(path=p, folder=crate, action="ok"))
                feats[p] = list(rng.normal(c, 1.0, dim))
        # an unknown filed under House that SOUNDS like Techno, and one that sounds like House
        rows.append(lr.Row(path="/lib/House/odd.mp3", folder="House", action="unknown"))
        feats["/lib/House/odd.mp3"] = list(rng.normal(0.0, 1.0, dim))
        rows.append(lr.Row(path="/lib/House/fine.mp3", folder="House", action="unknown"))
        feats["/lib/House/fine.mp3"] = list(rng.normal(12.0, 1.0, dim))
        rows.append(lr.Row(path="/lib/House/nofeat.mp3", folder="House", action="unknown"))
        feats["/lib/House/nofeat.mp3"] = None                                        # could not be decoded
        return rows, feats

    def test_unknowns_are_placed_or_confirmed_by_ear(self):
        rows, feats = self.build()
        with tempfile.TemporaryDirectory() as d:
            report, fusion = lr.run_audio(rows, features=feats, out=lambda *_: None, model_path=Path(d, "m.joblib"))
            self.assertTrue(Path(d, "m.joblib").is_file())                           # saved for the ingest pipeline
        by = {Path(r.path).name: r for r in rows}
        self.assertEqual((by["odd.mp3"].action, by["odd.mp3"].proposed), ("ear", "Techno"))
        self.assertEqual(by["fine.mp3"].action, "ok")
        self.assertEqual((by["nofeat.mp3"].action, by["nofeat.mp3"].audio_genre), ("unknown", ""))
        self.assertEqual(fusion["ear"], 1)
        self.assertGreaterEqual(report["per_class"]["Techno"]["precision"], 0.95)

    def test_the_confirmed_tracks_are_scored_out_of_fold_not_by_a_model_that_saw_them(self):
        rows, feats = self.build()
        with tempfile.TemporaryDirectory() as d:
            lr.run_audio(rows, features=feats, out=lambda *_: None, model_path=Path(d, "m.joblib"))
            from services import audio_genre_model as am
            model = am.load(Path(d, "m.joblib"))
        self.assertIn("/lib/Techno/0.mp3", model.oof)                               # every training track has an OOF score
        self.assertNotIn("/lib/House/odd.mp3", model.oof)                           # the unknown was never trained on

    def test_too_little_confirmed_data_skips_the_audio_step_quietly(self):
        rows = [lr.Row(path=f"/lib/House/{i}.mp3", folder="House", action="unknown") for i in range(5)]
        lines = []
        report, fusion = lr.run_audio(rows, features={r.path: [0.0] * 12 for r in rows}, out=lines.append)
        self.assertEqual((report, fusion), (None, {}))
        self.assertTrue(any("audio model skipped" in ln for ln in lines))
        self.assertTrue(all(r.action == "unknown" for r in rows))

    def test_the_report_renders_in_plain_words(self):
        rows, feats = self.build()
        with tempfile.TemporaryDirectory() as d:
            report, fusion = lr.run_audio(rows, features=feats, out=lambda *_: None, model_path=Path(d, "m.joblib"))
        text = lr.render_audio_report(report, fusion)
        for needle in ("Audio model", "out-of-fold accuracy", "Techno", "placed by ear"):
            self.assertIn(needle, text)
        self.assertIn("ear", lr.render_summary(rows, {"audio": report, "audio_fusion": fusion}))


class TestUserPlacedLedger(LibraryCase):
    """Syncing a genre tag to the folder you chose ERASES the evidence that you chose it. The ledger keeps
    that fact, so a suggestion you overrode does not come straight back."""

    def test_is_hand_placed(self):
        self.assertTrue(lr.is_hand_placed({"hand_placed": True, "folder": "House"}))
        self.assertTrue(lr.is_hand_placed({"tag_folder": "Techno", "folder": "House"}))
        self.assertTrue(lr.is_hand_placed(lr.Row(hand_placed=True)))
        self.assertFalse(lr.is_hand_placed({"tag_folder": "House", "folder": "House"}))
        self.assertFalse(lr.is_hand_placed({"folder": "House"}))

    def test_ledger_roundtrip_and_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "sub", "l.json")
            self.assertEqual(lr.load_user_placed(p), {})
            lr.save_user_placed({"a|1": "House", "b|2": "Techno"}, p)
            self.assertEqual(lr.load_user_placed(p), {"a|1": "House", "b|2": "Techno"})
            p.write_text("{ not json", encoding="utf-8")
            self.assertEqual(lr.load_user_placed(p), {})

    def test_the_key_survives_a_move_and_a_tag_edit(self):
        k = lr.placement_key(str(self.files["wrong"]))
        lr.write_tags(str(self.files["wrong"]), genre="Dubstep", artist="A Much Longer Artist Name Than Before")
        self.assertEqual(lr.placement_key(str(self.files["wrong"])), k)

    def test_the_scan_marks_ledger_files_only_while_they_sit_in_the_recorded_folder(self):
        key = lr.placement_key(str(self.files["wrong"]))
        lr.save_user_placed({key: "House"})
        rows, _ = self.scan()
        self.assertTrue(next(r for r in rows if r.title == "Bangarang").hand_placed)
        lr.save_user_placed({key: "Pop"})                                    # you chose Pop, but it now sits in House
        rows, _ = self.scan()
        self.assertFalse(next(r for r in rows if r.title == "Bangarang").hand_placed)

    def test_a_ledger_file_is_never_suggested_a_move(self):
        lr.save_user_placed({lr.placement_key(str(self.files["wrong"])): "House"})
        rows, _ = self.scan()
        r = next(r for r in rows if r.title == "Bangarang")                   # the engine says Dubstep (verified)
        self.assertEqual((r.action, r.proposed), ("review", "Dubstep"))
        self.assertIn("on record", r.note)

    def test_the_suggestion_does_not_come_back_after_the_tag_is_synced(self):
        """THE regression: hand-placed (tag says Dubstep, sits in House) -> sync the tag -> re-scan."""
        from mutagen.id3 import ID3
        t = ID3(str(self.files["wrong"]))
        from mutagen.id3 import TCON
        t["TCON"] = TCON(encoding=3, text=["Dubstep"])
        t.save(str(self.files["wrong"]))                                         # the pipeline once routed it to Dubstep
        rows, summary = self.scan()
        self.assertEqual(next(r for r in rows if r.title == "Bangarang").action, "review")     # protected by the stale tag

        chosen = [vars_of(r) for r in rows]
        lr.apply_rows([], self.root, dry_run=False, sync_genre_rows=[r for r in chosen if r["tag_folder"] and r["tag_folder"] != r["folder"]],
                      index_fn=lambda *a: 1, out=lambda *_: None)
        self.assertEqual(tag(self.files["wrong"], "TCON"), "House")               # ...and now the tag no longer says so

        rows, _ = self.scan()
        r = next(r for r in rows if r.title == "Bangarang")
        self.assertEqual(r.tag_folder, "House")                                   # the evidence is gone from the tag
        self.assertTrue(r.hand_placed)                                            # ...but not from the ledger
        self.assertEqual(r.action, "review")                                      # so the move does NOT return

    def test_a_preview_does_not_touch_the_ledger(self):
        rows, _ = self.scan()
        lr.apply_rows([], self.root, dry_run=True, out=lambda *_: None,
                      sync_genre_rows=[dict(vars_of(r), tag_folder="Techno") for r in rows if r.title == "Losing It"])
        self.assertEqual(lr.load_user_placed(), {})

    def test_learn_counts_ledger_files_as_your_decision(self):
        rows = [dict(folder="Techno", action="review", artist_used="A", tag_folder="", hand_placed=True) for _ in range(3)]
        self.assertEqual(lr.library_consensus(rows)[0]["crate"], "Techno")

    def test_the_audio_model_trains_on_ledger_files(self):
        from services import audio_genre_model as am
        rows = [{"path": "1", "folder": "House", "action": "review", "tag_folder": "", "hand_placed": True}]
        self.assertEqual(am.training_rows(rows)[0][1], "hand")


class TestSpotifyBlocked(unittest.TestCase):
    """A Spotify 429 blocks the whole app for ~23 h. While that lasts the scan must make NO Spotify
    calls, yet still use what is cached — validated by title — instead of discarding it."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        make_mp3(self.root, "Library/Punjabi/Tu Hi Hai.mp3", "Tu Hi Hai", "Indian", spotify_id="good")
        make_mp3(self.root, "Library/Punjabi/Kiya Kiya.mp3", "Kiya Kiya", "Indian", spotify_id="bad")
        make_mp3(self.root, "Library/Punjabi/Never Seen.mp3", "Never Seen", "Indian", spotify_id="uncached")
        self.cache = TagCache(None)
        # older cache entries: no duration_ms
        self.cache.set("good", {"artists": ["Arijit Singh"], "title": "Tu Hi Hai", "album": "A"})
        self.cache.set("bad", {"artists": ["The Bulbine for Indian"], "title": "Candytuft Parsley", "album": "B"})
        self.seen = []
        self.fetch = mock.Mock()
        self.set_limit = mock.patch("services.spotify_service.set_global_rate_limit")

    def scan(self, **kw):
        def classify(artist, title, bpm, path):
            self.seen.append((title, artist))
            return ABSTAIN
        kw.setdefault("playlist_tracks", [])
        with self.set_limit as guard:
            out = lr.scan(self.root, recovery_cache=self.cache, fetch=self.fetch, classify=classify, pace_s=0,
                          out=lambda *_: None, **kw)
        return out, guard

    def test_no_spotify_calls_flag(self):
        (rows, summary), guard = self.scan(spotify_calls=False)
        self.fetch.assert_not_called()
        self.assertIn("switched off", summary["stopped"])
        guard.assert_called()                                   # the app's own in-process guard is armed too

    def test_an_active_block_is_honoured_automatically(self):
        from services import artist_recovery as ar
        with mock.patch.object(ar, "block_seconds_left", return_value=7200):
            (rows, summary), guard = self.scan()
        self.fetch.assert_not_called()
        self.assertIn("rate-limited for another 2.0 h", summary["stopped"])

    def test_cached_records_are_still_used_but_only_when_the_title_matches(self):
        (rows, summary), _ = self.scan(spotify_calls=False)
        by = {r.title: r for r in rows}
        self.assertEqual(by["Tu Hi Hai"].recovered_artist, "Arijit Singh")
        self.assertIn("length not checked", by["Tu Hi Hai"].id_check)
        self.assertEqual(by["Kiya Kiya"].recovered_artist, "")                  # wrong id: still rejected
        self.assertIn("different song", by["Kiya Kiya"].id_check)
        self.assertEqual(by["Never Seen"].recovered_artist, "")                 # nothing cached: unidentified
        self.assertEqual(summary["ids_unchecked_len"], 1)
        self.assertIn(("Tu Hi Hai", "Arijit Singh"), self.seen)

    def test_summary_says_the_length_was_not_checked(self):
        (rows, summary), _ = self.scan(spotify_calls=False)
        self.assertIn("TITLE only", lr.render_summary(rows, summary))

    def test_without_a_block_it_still_fetches(self):
        self.fetch.side_effect = lambda sid: {"artists": ["Someone"], "title": "Never Seen", "duration_ms": 1000}
        (rows, summary), guard = self.scan()
        self.assertEqual(self.fetch.call_count, 3)                              # good & bad lack a length, 'uncached' is new
        guard.assert_not_called()


class TestReports(LibraryCase):
    def test_json_csv_txt(self):
        rows, summary = self.scan()
        jp, cp, tp = lr.write_reports(rows, self.root, summary, out_dir=self.out)
        data = json.loads(jp.read_text(encoding="utf-8"))
        self.assertEqual(len(data["rows"]), len(rows))
        self.assertEqual(data["recovery"]["recovered"], 1)
        with open(cp, newline="", encoding="utf-8-sig") as fh:
            recs = list(csv.DictReader(fh))
        self.assertNotIn("ok", {r["action"] for r in recs})                 # the sheet is the to-do list
        move = next(r for r in recs if r["action"] == "move")
        self.assertEqual((move["apply"], move["current_folder"], move["proposed_folder"]), ("yes", "House", "Dubstep"))
        self.assertEqual(next(r for r in recs if r["action"] == "review")["apply"], "")
        self.assertIn("MOVE", tp.read_text(encoding="utf-8"))

    def test_non_latin_titles_survive_the_spreadsheet(self):
        row = lr.Row(idx=1, path="x", rel="x", folder="House", title="संगीत", action="move", proposed="Trance")
        _, cp, _ = lr.write_reports([row], self.root, {}, out_dir=self.out)
        with open(cp, newline="", encoding="utf-8-sig") as fh:
            self.assertEqual(next(csv.DictReader(fh))["title"], "संगीत")

    def test_summary_mentions_recovery_and_flows(self):
        import re
        rows, summary = self.scan()
        text = lr.render_summary(rows, summary)
        self.assertIn("recovered 1", text)
        self.assertRegex(text, re.compile(r"House\s+->\s+Dubstep\s+1"))


class TestSelection(unittest.TestCase):
    ROWS = [{"idx": 1, "action": "move", "proposed": "Dubstep"}, {"idx": 2, "action": "review", "proposed": "Trance"},
            {"idx": 3, "action": "unknown", "proposed": ""}, {"idx": 4, "action": "move", "proposed": "House"}]

    def idxs(self, **kw):
        return [(r["idx"], r["dest"]) for r in lr.select_rows(self.ROWS, **kw)]

    def test_default_is_the_move_rows_only(self):
        self.assertEqual(self.idxs(), [(1, "Dubstep"), (4, "House")])

    def test_include_review_only_skip(self):
        self.assertEqual(self.idxs(include_review=True), [(1, "Dubstep"), (2, "Trance"), (4, "House")])
        self.assertEqual(self.idxs(only="2"), [(1, "Dubstep"), (2, "Trance"), (4, "House")])
        self.assertEqual(self.idxs(skip="1"), [(4, "House")])
        self.assertEqual(self.idxs(include_review=True, skip="1-2"), [(4, "House")])

    def test_the_spreadsheet_decides_and_can_correct_the_destination(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "s.csv")
            p.write_text("idx,apply,proposed_folder\n1,,Dubstep\n2,YES,Trance\n3,x,Latin\n4,no,House\n99,yes,Pop\n",
                         encoding="utf-8-sig")
            self.assertEqual(self.idxs(csv_path=p), [(2, "Trance"), (3, "Latin")])
            p.write_text("idx,apply,proposed_folder\n3,yes,\n", encoding="utf-8")
            self.assertEqual(self.idxs(csv_path=p), [(3, "")])              # nothing to go on: apply will refuse it

    def test_parse_index_list(self):
        self.assertEqual(lr.parse_index_list("1, 4,7-9"), {1, 4, 7, 8, 9})
        self.assertEqual(lr.parse_index_list(None), set())


class TestApply(LibraryCase):
    def setUp(self):
        super().setUp()
        rows, summary = self.scan()
        self.rows = [vars_of(r) for r in rows]
        self.index_calls = []
        self.manifest = self.out / "undo.json"

    def apply(self, selected=None, **kw):
        selected = selected if selected is not None else lr.select_rows(self.rows)
        kw.setdefault("manifest_path", self.manifest)
        return lr.apply_rows(selected, self.root, index_fn=lambda *a: self.index_calls.append(a) or 1,
                             out=lambda *_: None, **kw)

    def test_preview_changes_nothing_and_writes_no_manifest(self):
        res = self.apply(dry_run=True)
        self.assertTrue(self.files["wrong"].exists())
        self.assertFalse(self.manifest.exists())
        self.assertEqual(self.index_calls, [])
        self.assertEqual(res["moved"][0][1], "dry-run")

    def test_move_updates_location_genre_tag_index_and_manifest(self):
        res = self.apply(dry_run=False)
        new = self.root / "Library" / "Dubstep" / "Skrillex - Bangarang.mp3"
        self.assertTrue(new.is_file())
        self.assertFalse(self.files["wrong"].exists())
        self.assertEqual(tag(new, "TCON"), "Dubstep")                        # tag follows the folder
        self.assertEqual(self.index_calls, [(str(self.files["wrong"]), str(new), "Library/Dubstep")])
        rec = json.loads(self.manifest.read_text(encoding="utf-8"))["records"][0]
        self.assertEqual((rec["old_folder"], rec["new_folder"], rec["old_genre"]), ("House", "Dubstep", "House"))
        self.assertEqual(len(res["moved"]), 1)
        # everything else is untouched
        for key in ("right", "recover", "boundary", "unknown", "electronic", "psy"):
            self.assertTrue(self.files[key].exists(), key)

    def test_an_existing_file_is_never_overwritten(self):
        existing = make_mp3(self.root, "Library/Dubstep/Skrillex - Bangarang.mp3", "Other", "Someone")
        original = existing.read_bytes()
        self.apply(dry_run=False)
        self.assertEqual(existing.read_bytes(), original)
        self.assertEqual(len(list((self.root / "Library" / "Dubstep").glob("*.mp3"))), 2)

    def test_genre_tag_can_be_left_alone(self):
        self.apply(dry_run=False, write_genre_tag=False)
        new = self.root / "Library" / "Dubstep" / "Skrillex - Bangarang.mp3"
        self.assertEqual(tag(new, "TCON"), "House")

    def test_refuses_bad_rows(self):
        good = lr.select_rows(self.rows)[0]
        with tempfile.TemporaryDirectory() as other:
            # A REAL mp3 that even sits in a folder called "House" — but outside the library root.
            outsider = make_mp3(Path(other), "Library/House/Skrillex - Outsider.mp3", "Outsider", "Skrillex")
            res = self.apply([dict(good, path=str(outsider), rel="outsider.mp3")], dry_run=False)
            self.assertEqual(res["moved"], [])
            self.assertIn("outside the library root", res["skipped"][0][1])
            self.assertTrue(outsider.exists())
            self.assertFalse((self.root / "Library" / "Dubstep" / outsider.name).exists())
        cases = {
            "missing": dict(good, path=str(self.root / "Library" / "House" / "gone.mp3")),
            "bad crate": dict(good, dest="Nonsense"),
            "empty crate": dict(good, dest=""),
            "wrong folder": dict(good, folder="Techno"),
            "not mp3": dict(good, path=str(self.files["wrong"].with_suffix(".txt"))),
        }
        for name, row in cases.items():
            res = self.apply([row], dry_run=False)
            self.assertEqual((res["moved"], len(res["skipped"])), ([], 1), name)
        self.assertTrue(self.files["wrong"].exists())

    def test_one_failing_move_does_not_stop_the_rest_and_is_still_undoable(self):
        make_mp3(self.root, "Library/House/Second.mp3", "Second", "Skrillex")
        second = dict(lr.select_rows(self.rows)[0], path=str(self.root / "Library" / "House" / "Second.mp3"),
                      rel="Library/House/Second.mp3")
        calls = []
        real_move = __import__("services.organizer_service", fromlist=["safe_move"]).safe_move

        def flaky(src, dest_dir, **kw):
            calls.append(src.name)
            if src.name.startswith("Skrillex"):
                raise PermissionError("file is open in another program")
            return real_move(src, dest_dir, **kw)

        res = self.apply([lr.select_rows(self.rows)[0], second], dry_run=False, move_fn=flaky)
        self.assertEqual(len(res["moved"]), 1)
        self.assertIn("move failed", res["skipped"][0][1])
        self.assertEqual(len(json.loads(self.manifest.read_text(encoding="utf-8"))["records"]), 1)
        self.assertTrue(self.files["wrong"].exists())

    def test_manifest_is_written_after_every_move_so_a_crash_is_still_undoable(self):
        make_mp3(self.root, "Library/House/Second.mp3", "Second", "Skrillex")
        first = lr.select_rows(self.rows)[0]
        second = dict(first, path=str(self.root / "Library" / "House" / "Second.mp3"), rel="Library/House/Second.mp3")
        real_move = __import__("services.organizer_service", fromlist=["safe_move"]).safe_move

        def interrupted(src, dest_dir, **kw):
            if src.name == "Second.mp3":
                raise KeyboardInterrupt()                # Ctrl-C / power loss mid-run
            return real_move(src, dest_dir, **kw)

        with self.assertRaises(KeyboardInterrupt):
            self.apply([first, second], dry_run=False, move_fn=interrupted)
        records = json.loads(self.manifest.read_text(encoding="utf-8"))["records"]
        self.assertEqual(len(records), 1)
        undo = lr.undo_manifest(json.loads(self.manifest.read_text(encoding="utf-8")), dry_run=False,
                                index_fn=lambda *a: 1, out=lambda *_: None)
        self.assertEqual(undo["restored"], 1)
        self.assertTrue(self.files["wrong"].is_file())

    def test_fix_tags_writes_the_recovered_artist_even_for_tracks_that_stay(self):
        res = self.apply(dry_run=False, fix_tags=True, fix_artist_rows=[r for r in self.rows if r["recovered_artist"]])
        self.assertEqual(tag(self.files["recover"], "TPE1"), "Kova, Memento Mori")
        self.assertEqual(tag(self.files["recover"], "TCON"), "Trance")        # not moved: genre tag untouched
        self.assertEqual(res["tagged"], 1)
        recs = json.loads(self.manifest.read_text(encoding="utf-8"))["records"]
        self.assertIn(("Unknown", str(self.files["recover"])), [(r["old_artist"], r["new_path"]) for r in recs])

    def test_fix_tags_also_writes_the_artist_of_a_track_that_is_moved(self):
        row = dict(lr.select_rows(self.rows)[0], recovered_artist="Skrillex, Poo Bear")
        self.apply([row], dry_run=False, fix_tags=True)
        new = self.root / "Library" / "Dubstep" / "Skrillex - Bangarang.mp3"
        self.assertEqual((tag(new, "TPE1"), tag(new, "TCON")), ("Skrillex, Poo Bear", "Dubstep"))
        rec = json.loads(self.manifest.read_text(encoding="utf-8"))["records"][0]
        self.assertEqual(rec["old_artist"], "Skrillex")
        lr.undo_manifest(json.loads(self.manifest.read_text(encoding="utf-8")), dry_run=False,
                         index_fn=lambda *a: 1, out=lambda *_: None)
        self.assertEqual((tag(self.files["wrong"], "TPE1"), tag(self.files["wrong"], "TCON")), ("Skrillex", "House"))

    def test_artist_tags_are_not_touched_without_fix_tags(self):
        self.apply(dry_run=False)
        self.assertEqual(tag(self.files["recover"], "TPE1"), "Unknown")


class TestUndo(LibraryCase):
    def setUp(self):
        super().setUp()
        rows, _ = self.scan()
        self.rows = [vars_of(r) for r in rows]
        self.index_calls = []
        self.manifest = self.out / "undo.json"
        lr.apply_rows(lr.select_rows(self.rows), self.root, dry_run=False, fix_tags=True,
                      fix_artist_rows=[r for r in self.rows if r["recovered_artist"]],
                      index_fn=lambda *a: 1, manifest_path=self.manifest, out=lambda *_: None)
        self.new = self.root / "Library" / "Dubstep" / "Skrillex - Bangarang.mp3"

    def undo(self, **kw):
        return lr.undo_manifest(json.loads(self.manifest.read_text(encoding="utf-8")),
                                index_fn=lambda *a: self.index_calls.append(a) or 1, out=lambda *_: None, **kw)

    def test_preview_changes_nothing(self):
        res = self.undo(dry_run=True)
        self.assertTrue(self.new.exists())
        self.assertEqual(res["restored"], 2)

    def test_undo_restores_location_and_tags(self):
        res = self.undo(dry_run=False)
        self.assertEqual(res["restored"], 2)
        self.assertTrue(self.files["wrong"].is_file())
        self.assertFalse(self.new.exists())
        self.assertEqual(tag(self.files["wrong"], "TCON"), "House")
        self.assertEqual(tag(self.files["recover"], "TPE1"), "Unknown")
        self.assertEqual(self.index_calls, [(str(self.new), str(self.files["wrong"]), "Library/House")])

    def test_a_tag_that_did_not_exist_is_removed_again(self):
        from mutagen.id3 import ID3
        t = ID3(str(self.files["wrong"]) if self.files["wrong"].exists() else str(self.new))
        t.delall("TCON")
        t.save(str(self.new))
        # re-run the whole cycle from a file that had no genre tag originally
        src = make_mp3(self.root, "Library/House/NoGenre.mp3", "NoGenre", "Skrillex")
        row = dict(lr.select_rows(self.rows)[0], path=str(src), rel="Library/House/NoGenre.mp3")
        m = self.out / "undo2.json"
        lr.apply_rows([row], self.root, dry_run=False, index_fn=lambda *a: 1, manifest_path=m, out=lambda *_: None)
        moved = self.root / "Library" / "Dubstep" / "NoGenre.mp3"
        self.assertEqual(tag(moved, "TCON"), "Dubstep")
        lr.undo_manifest(json.loads(m.read_text(encoding="utf-8")), index_fn=lambda *a: 1, out=lambda *_: None, dry_run=False)
        self.assertTrue(src.is_file())
        from mutagen.id3 import ID3
        self.assertIsNone(ID3(str(src)).get("TCON"), "the frame must be REMOVED, not left empty")

    def test_refuses_to_overwrite_something_now_at_the_original_location(self):
        blocker = make_mp3(self.root, "Library/House/Skrillex - Bangarang.mp3", "Different file", "Else")
        res = self.undo(dry_run=False)
        self.assertEqual(blocker.read_bytes(), blocker.read_bytes())
        self.assertTrue(self.new.exists(), "the moved file must stay put")
        self.assertEqual(tag(blocker, "TIT2"), "Different file")
        self.assertEqual(len(res["skipped"]), 1)

    def test_a_file_that_is_gone_is_skipped(self):
        self.new.unlink()
        res = self.undo(dry_run=False)
        self.assertEqual(len(res["skipped"]), 1)


class TestCommandLine(unittest.TestCase):
    """The real engine, offline (no network, no Spotify), through the actual CLI."""

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lr.main(list(argv))
        return code, buf.getvalue()

    def test_scan_apply_undo_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d, "lib"), Path(d, "reports")
            skr = make_mp3(root, "Library/House/Bangarang.mp3", "Bangarang", "Skrillex", "110", genre="House")
            cdw = make_mp3(root, "Library/Techno/Doppler.mp3", "Doppler", "Charlotte de Witte", "138", genre="Techno")
            hs = make_mp3(root, "Library/Bollywood/Dil Chori.mp3", "Dil Chori", "Yo Yo Honey Singh", "100", genre="Bollywood")
            zz = make_mp3(root, "Library/House/Mystery.mp3", "Mystery", "Zzyzx Qwerty", "126", genre="House")
            before = {p: p.read_bytes() for p in (skr, cdw, hs, zz)}

            with mock.patch.object(lr, "REPORTS_DIR", out), \
                 mock.patch.object(lr, "update_library_index", return_value=1):
                code, text = self.run_cli("scan", "--root", str(root), "--offline", "--no-recover", "--no-playlist")
                self.assertEqual(code, 0)
                self.assertEqual(before, {p: p.read_bytes() for p in before}, "scan must not change any file")
                report = sorted(out.glob("resort_20*.json"))[-1]
                rows = {r["title"]: r for r in json.loads(report.read_text(encoding="utf-8"))["rows"]}
                self.assertEqual((rows["Bangarang"]["action"], rows["Bangarang"]["proposed"]), ("move", "Dubstep"))
                self.assertEqual(rows["Doppler"]["action"], "ok")
                self.assertEqual(rows["Dil Chori"]["action"], "review")             # Punjabi artist, Bollywood folder
                self.assertEqual(rows["Mystery"]["action"], "unknown")

                code, text = self.run_cli("apply", "--report", str(report))          # preview
                self.assertIn("PREVIEW", text)
                self.assertTrue(skr.exists())

                code, text = self.run_cli("apply", "--report", str(report), "--yes")
                moved = root / "Library" / "Dubstep" / "Bangarang.mp3"
                self.assertTrue(moved.is_file())
                self.assertTrue(hs.exists(), "an Indian-boundary track is never moved automatically")
                self.assertEqual(tag(moved, "TCON"), "Dubstep")
                manifest = sorted(out.glob("resort_undo_*.json"))[-1]

                code, text = self.run_cli("undo", "--manifest", str(manifest), "--yes")
                self.assertTrue(skr.is_file())
                self.assertEqual(tag(skr, "TCON"), "House")

    def test_hand_sorting_is_protected_tags_are_synced_and_leave_alone_is_honoured(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d, "lib"), Path(d, "reports")
            # (a) the pipeline tagged it Dubstep, YOU put it in House: a suggestion must not move it back
            a = make_mp3(root, "Library/House/Hand Placed.mp3", "Hand Placed", "Skrillex", genre="Dubstep")
            # (b) a plain misfile (tag agrees with the folder it sits in): a normal MOVE
            b = make_mp3(root, "Library/House/Misfiled.mp3", "Misfiled", "Skrillex", genre="House")
            # (c) moved out of the Electronic catch-all by hand: tag still says Electronic
            c = make_mp3(root, "Library/Techno/Out Of Electronic.mp3", "Out Of Electronic", "Anyma", genre="Electronic")
            # (d) on the leave-alone list (e.g. flagged corrupt): never touched, not even its tag
            dd = make_mp3(root, "Library/Techno/Corrupt.mp3", "Corrupt", "Anyma", genre="Electronic")
            leave = Path(d, "leave.txt")
            leave.write_text("# corrupt files\nCorrupt.mp3\n", encoding="utf-8")
            before_d = dd.read_bytes()

            with mock.patch.object(lr, "REPORTS_DIR", out), mock.patch.object(lr, "update_library_index", return_value=1):
                self.run_cli("scan", "--root", str(root), "--offline", "--no-recover", "--no-playlist")
                report = sorted(out.glob("resort_20*.json"))[-1]
                rows = {r["title"]: r for r in json.loads(report.read_text(encoding="utf-8"))["rows"]}
                self.assertEqual(rows["Hand Placed"]["action"], "review")             # engine says Dubstep; you decided House
                self.assertIn("by hand", rows["Hand Placed"]["note"])
                self.assertEqual((rows["Misfiled"]["action"], rows["Misfiled"]["proposed"]), ("move", "Dubstep"))
                self.assertEqual(rows["Out Of Electronic"]["tag_folder"], "Electronic")

                _, preview = self.run_cli("apply", "--report", str(report), "--sync-genre-tags", "--leave-alone", str(leave))
                self.assertIn("PREVIEW", preview)
                self.assertEqual(tag(c, "TCON"), "Electronic")                          # the preview changed nothing

                self.run_cli("apply", "--report", str(report), "--sync-genre-tags", "--leave-alone", str(leave), "--yes")

            self.assertTrue(a.exists(), "the hand-placed file must stay where you put it")
            self.assertEqual(tag(a, "TCON"), "House")                                   # ...and its stale tag now agrees
            self.assertFalse(b.exists())
            self.assertEqual(tag(root / "Library" / "Dubstep" / "Misfiled.mp3", "TCON"), "Dubstep")
            self.assertEqual(tag(c, "TCON"), "Techno")                                  # synced to the folder you chose
            self.assertEqual(dd.read_bytes(), before_d)                                 # the leave-alone file is byte-identical

    def test_missing_library_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cli("scan", "--root", d)[0], 2)


def vars_of(row):
    from dataclasses import asdict
    return asdict(row)


if __name__ == "__main__":
    unittest.main()
