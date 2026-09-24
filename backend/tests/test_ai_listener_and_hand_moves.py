"""
Groq listening (services/ai_listener.py), learning from hand moves (services/hand_moves.py) and how the ingest
loop / downloader use them. No network: Whisper and the chat model are injected fakes; the "library" is a temp
folder of small files carrying real ID3 tags.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mutagen.id3 import ID3, TPE1, TXXX

from services import ai_listener as al
from services import auto_downloader as ad
from services import hand_moves as hm


def _song(folder: Path, name: str, artist: str = "Some Artist", filed: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"\x00" * 4096)
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    if filed:
        tags.add(TXXX(encoding=3, desc=hm.TAG, text=[filed]))
    tags.save(str(p))
    return p


class SungWordsAndScript(unittest.TestCase):
    def test_whisper_filler_is_not_singing(self):
        for text in ("Music", " موسیقی", "Thanks for watching!", "¡Suscríbete al canal!", "🎵", "Outro Music"):
            self.assertLess(al.sung_words(text), al.MIN_SUNG_WORDS, text)

    def test_real_lyrics_count(self):
        self.assertGreaterEqual(al.sung_words("तुम उड़े जा रहे ये आसमा में खिड़कियों से देख"), al.MIN_SUNG_WORDS)
        self.assertGreaterEqual(al.sung_words("I'm not looking for your eyes tonight"), al.MIN_SUNG_WORDS)

    def test_script_language(self):
        self.assertEqual(al.script_language("तेरा"), "Hindi")
        self.assertEqual(al.script_language("Qasme", "ਦਿਲਜੀਤ"), "Punjabi")
        self.assertEqual(al.script_language("Qasme", "rohh"), "")


class Listen(unittest.TestCase):
    def setUp(self):
        al._cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "a.mp3")
        Path(self.path).write_bytes(b"x" * 5000)

    def tearDown(self):
        al._cache.clear()
        self.tmp.cleanup()

    def _listen(self, answers, **kw):
        calls = iter(answers)
        with mock.patch.object(al, "_duration", return_value=200.0):
            return al.listen(self.path, transcribe=lambda audio: next(calls), clip=lambda p, s: b"a", **kw)

    def test_stops_at_first_sung_clip(self):
        heard = self._listen([("Urdu", "پیشے دیکھو راہ تیری ہاتھ جو رہی نہ میری")])
        self.assertTrue(heard["has_vocals"])
        self.assertEqual((heard["language"], heard["clips"]), ("Urdu", 1))

    def test_language_kept_when_whisper_will_not_transcribe(self):
        heard = self._listen([("Urdu", "موسیقی"), ("Urdu", "موسیقی")])
        self.assertFalse(heard["has_vocals"])
        self.assertEqual(heard["language"], "Urdu")

    def test_failure_returns_empty(self):
        def boom(audio):
            raise RuntimeError("no key")
        with mock.patch.object(al, "_duration", return_value=200.0):
            self.assertEqual(al.listen(self.path, transcribe=boom, clip=lambda p, s: b"a"), {})


class IsInstrumental(unittest.TestCase):
    def _run(self, heard):
        with mock.patch.object(al, "listen", return_value=heard):
            return al.is_instrumental("x.mp3")

    def test_karaoke_upload(self):          # measured on "Make Some Noise For The Desi Boyz"
        self.assertTrue(self._run({"has_vocals": False, "clips": 3, "languages": ["English"] * 3}))

    def test_indian_voice_without_transcript_is_not_instrumental(self):   # measured on "Qasme"
        self.assertFalse(self._run({"has_vocals": False, "clips": 3, "languages": ["Urdu"] * 3}))
        self.assertFalse(self._run({"has_vocals": False, "clips": 3, "languages": ["English", "Panjabi", "English"]}))

    def test_sung_or_unknown_is_not_instrumental(self):
        self.assertFalse(self._run({"has_vocals": True, "clips": 1, "languages": ["Hindi"]}))
        self.assertFalse(self._run({}))                                     # Groq unavailable
        self.assertFalse(self._run({"has_vocals": False, "clips": 1, "languages": ["English"]}))


class Classify(unittest.TestCase):
    heard = {"language": "Urdu", "lyrics": "", "has_vocals": False, "languages": ["Urdu"]}

    def test_picks_one_of_the_crates(self):
        prompts = []
        chat = lambda prompt: prompts.append(prompt) or '{"crate": "indie", "confidence": 0.9, "reason": "desi indie"}'
        out = al.classify("", "Qasme", "rohh", heard=self.heard, chat=chat)
        self.assertEqual((out["crate"], out["confidence"]), ("Indie", 0.9))
        self.assertIn("Voice heard in Urdu", prompts[0])
        self.assertIn("- Indie:", prompts[0])

    def test_answer_outside_the_crates_is_ignored(self):
        chat = lambda prompt: '{"crate": "Afrobeats", "confidence": 0.9}'
        self.assertEqual(al.classify("", "X", "Y", heard=self.heard, chat=chat), {})

    def test_indian_crate_for_non_indian_singing_is_demoted(self):
        heard = {"language": "English", "lyrics": "la la", "has_vocals": True, "languages": ["English"]}
        chat = lambda prompt: '{"crate": "Indian Hip Hop", "confidence": 0.9}'
        self.assertLess(al.classify("", "Schizophrenik In Panik", "ILYAA", heard=heard, chat=chat)["confidence"], 0.4)
        # ... but not when the title or artist is written in an Indian script
        self.assertEqual(al.classify("", "तेरा", "X", heard=heard, chat=chat)["confidence"], 0.9)

    def test_garbage_answer_is_ignored(self):
        self.assertEqual(al.classify("", "X", "Y", heard=self.heard, chat=lambda p: "not json"), {})


class IngestAiRoute(unittest.TestCase):
    def test_confident_crate_is_used(self):
        fake = lambda *a, **k: {"crate": "Indie", "confidence": 0.8}
        self.assertEqual(ad._ai_route("rohh", "Qasme", "f.mp3", classify=fake), ("Library/Indie", "ai:groq"))

    def test_unsure_electronic_or_nothing_stays_in_the_catch_all(self):
        for answer in ({"crate": "Indie", "confidence": 0.2}, {"crate": "Electronic", "confidence": 0.9}, {}):
            self.assertEqual(ad._ai_route("a", "t", "f", classify=lambda *a, **k: answer), ("", ""), answer)

    def test_floor_is_configurable(self):
        fake = lambda *a, **k: {"crate": "House", "confidence": 0.5}
        with mock.patch.dict(os.environ, {"AI_MIN_CONFIDENCE": "0.6"}):
            self.assertEqual(ad._ai_route("a", "t", "f", classify=fake), ("", ""))

    def test_fallback_order_is_evidence_then_groq(self):
        src = Path(ad.__file__).read_text(encoding="utf-8")
        fb = src[src.index("def _fallback_genre"):]
        fb = fb[:fb.index("# Yield to manual downloads")]
        self.assertLess(fb.index("_evidence_route("), fb.index("_ai_route("))


class ExpectVocals(unittest.TestCase):
    def test_known_indian_crate_expects_singing(self):
        with mock.patch.object(ad, "_known_crate", return_value="Bollywood"):
            self.assertTrue(ad._expects_indic_vocals("Vishal-Shekhar", "Make Some Noise For The Desi Boyz"))

    def test_instrumental_titles_and_other_crates_do_not(self):
        with mock.patch.object(ad, "_known_crate", return_value="Bollywood"):
            self.assertFalse(ad._expects_indic_vocals("A", "Song (Instrumental)"))
        with mock.patch.object(ad, "_known_crate", return_value="Techno"):
            self.assertFalse(ad._expects_indic_vocals("Anyma", "Voices in My Head"))
        with mock.patch.object(ad, "_known_crate", return_value=""):
            self.assertFalse(ad._expects_indic_vocals("rohh", "Qasme"))

    def test_genre_playlist_crate_counts(self):
        with mock.patch.object(ad, "_known_crate", return_value=""):
            self.assertTrue(ad._expects_indic_vocals("x", "y", forced_crate="Punjabi"))


class DownloaderVocalCheck(unittest.TestCase):
    def setUp(self):
        from services.downloader_service import DownloaderService
        self.svc = DownloaderService.__new__(DownloaderService)
        import threading
        self.svc._tls = threading.local()
        self.tmp = tempfile.TemporaryDirectory()
        self.f = os.path.join(self.tmp.name, "song.mp3")
        Path(self.f).write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_instrumental_is_rejected_then_accepted_after_two(self):
        from services.downloader_service import VerificationRejected
        self.svc._vocal_checks_left = 2
        with mock.patch("services.ai_listener.is_instrumental", return_value=True):
            for _ in range(2):
                Path(self.f).write_bytes(b"x")
                with self.assertRaises(VerificationRejected) as ctx:
                    self.svc._reject_if_instrumental(self.f, {"url": "u", "title": "karaoke"})
                self.assertIn("no singing", ctx.exception.result.reason)
                self.assertFalse(os.path.exists(self.f))
        self.assertEqual(self.svc._vocal_checks_left, 0)

    def test_sung_download_is_kept(self):
        self.svc._vocal_checks_left = 2
        with mock.patch("services.ai_listener.is_instrumental", return_value=False):
            self.svc._reject_if_instrumental(self.f, {"url": "u"})
        self.assertTrue(os.path.exists(self.f))

    def test_download_track_arms_the_check_only_when_asked(self):
        src = Path(__import__("services.downloader_service", fromlist=["x"]).__file__).read_text(encoding="utf-8")
        self.assertIn("self._vocal_checks_left = 2 if expect_vocals else 0", src)
        self.assertIn("if self._vocal_checks_left > 0:", src)


class HandMoves(unittest.TestCase):
    def setUp(self):
        hm.reset_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Library"
        self.learned = []

    def tearDown(self):
        hm.reset_cache()
        self.tmp.cleanup()

    def _scan(self):
        return hm.scan(self.root, learn_fn=lambda p, old, new: self.learned.append((p.name, old, new))
                       or {"file": p.name, "from": old, "to": new, "artist_learned": ""},
                       log_dir=Path(self.tmp.name))

    def test_first_pass_is_a_baseline(self):
        f = _song(self.root / "Bollywood", "a.mp3")
        stats = self._scan()
        self.assertEqual((stats["baselined"], self.learned), (1, []))
        self.assertEqual(hm.read_stamp(f), "Bollywood")

    def test_a_file_in_another_crate_than_it_was_filed_in_is_a_hand_move(self):
        _song(self.root / "Indie", "Qasme - rohh.mp3", artist="rohh", filed="Electronic")
        _song(self.root / "House", "b.mp3", filed="House")
        self._scan()
        self.assertEqual(self.learned, [("Qasme - rohh.mp3", "Electronic", "Indie")])

    def test_unchanged_files_are_not_read_twice(self):
        _song(self.root / "House", "b.mp3", filed="House")
        self.assertEqual(self._scan()["checked"], 1)
        self.assertEqual(self._scan()["checked"], 0)

    def test_skip_folders_and_nested_files_are_ignored(self):
        _song(self.root / "PSY", "x.mp3", filed="Trance")
        _song(self.root / "House" / "Sub", "y.mp3", filed="Techno")
        self._scan()
        self.assertEqual(self.learned, [])

    def test_learn_records_the_artist_syncs_the_tag_and_restamps(self):
        f = _song(self.root / "Indie", "Qasme - rohh.mp3", artist="rohh, Someone", filed="Electronic")
        recorded, tagged, indexed = [], [], []
        placed = Path(self.tmp.name) / "placed.json"
        with mock.patch("library_resort.placement_key", return_value="k1"):
            out = hm._learn(f, "Electronic", "Indie", record_move=lambda *a, **k: recorded.append((a, k)),
                            index_fn=lambda *a: indexed.append(a) or 1,
                            tag_fn=lambda p, genre=None: tagged.append(genre), placed_path=placed)
        self.assertEqual(out["artist_learned"], "rohh")
        self.assertEqual(recorded[0][0], ("rohh", "Indie"))
        self.assertEqual(recorded[0][1]["min_confidence"], hm.HAND_MOVE_CONFIDENCE)
        self.assertEqual(tagged, ["Indie"])
        self.assertEqual(json.loads(placed.read_text(encoding="utf-8")), {"k1": "Indie"})
        self.assertTrue(indexed[0][0].endswith(os.path.join("Electronic", "Qasme - rohh.mp3")))
        self.assertEqual(hm.read_stamp(f), "Indie")

    def test_placeholder_artist_is_not_learned(self):
        f = _song(self.root / "Punjabi", "s.mp3", artist="Indian", filed="Bollywood")
        recorded = []
        with mock.patch("library_resort.placement_key", return_value="k"):
            out = hm._learn(f, "Bollywood", "Punjabi", record_move=lambda *a, **k: recorded.append(a),
                            index_fn=lambda *a: 0, tag_fn=lambda p, genre=None: None,
                            placed_path=Path(self.tmp.name) / "p.json")
        self.assertEqual((recorded, out["artist_learned"]), ([], ""))

    def test_app_movers_stamp_their_moves(self):
        backend = Path(ad.__file__).resolve().parent.parent
        for rel in ("services/auto_downloader.py", "services/maintenance_worker.py", "services/organizer_service.py",
                    "library_resort.py", "backfill_ai.py", "master_organise.py"):
            self.assertIn("hand_moves import stamp", (backend / rel).read_text(encoding="utf-8"), rel)

    def test_watcher_learns_every_cycle_even_during_a_spotify_cooldown(self):
        src = Path(ad.__file__).read_text(encoding="utf-8")
        loop = src[src.index("\ndef playlist_monitor"):src.index("\ndef manual_refresh")]
        self.assertLess(loop.index("_learn_hand_moves()"), loop.index("is_rate_limited()"))


class ArtistMemoryFloor(unittest.TestCase):
    def _col(self, existing=None):
        col = mock.MagicMock()
        col.find_one.return_value = existing
        return col

    def test_new_hand_move_is_confident_enough_to_route(self):
        from services import artist_memory_service as am
        col = self._col()
        with mock.patch.object(am, "_get_col", return_value=col):
            am.record_move("rohh", "Indie", source="hand_move", min_confidence=0.75)
        self.assertEqual(col.insert_one.call_args[0][0]["confidence"], 0.75)

    def test_a_move_to_a_different_genre_restarts_the_count(self):
        from services import artist_memory_service as am
        col = self._col({"genre": "Bollywood", "move_count": 4, "aliases": ["rohh"]})
        with mock.patch.object(am, "_get_col", return_value=col):
            am.record_move("rohh", "Indie", source="hand_move", min_confidence=0.75)
        fields = col.update_one.call_args[0][1]["$set"]
        self.assertEqual((fields["genre"], fields["move_count"], fields["confidence"]), ("Indie", 1, 0.75))

    def test_old_callers_unchanged(self):
        from services import artist_memory_service as am
        col = self._col({"genre": "House", "move_count": 1, "aliases": ["x"]})
        with mock.patch.object(am, "_get_col", return_value=col):
            am.record_move("x", "House")
        self.assertEqual(col.update_one.call_args[0][1]["$set"]["confidence"], 0.5)


class IndieCrate(unittest.TestCase):
    def test_indie_is_a_real_crate_everywhere(self):
        from services.genre_router import GENRE_TAXONOMY, normalize_genre
        from services import genre_playlists
        import library_resort as lr
        self.assertEqual(GENRE_TAXONOMY["Indie"][1], "Indie")
        self.assertEqual(GENRE_TAXONOMY[normalize_genre("indie pop")][1], "Indie")
        self.assertIn("Indie", genre_playlists.valid_crates())
        self.assertEqual(lr.FOLDER_TO_TCON["Indie"], "Indie")
        self.assertIn("Indie", al.CRATES)

    def test_users_indie_artists(self):
        from services.genre_router import _get_artist_override
        for artist in ("Anuv Jain", "rohh", "Asim Azhar"):
            self.assertEqual(_get_artist_override(artist), "Indie", artist)


if __name__ == "__main__":
    unittest.main()
