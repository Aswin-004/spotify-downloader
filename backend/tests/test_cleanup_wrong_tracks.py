"""
Tests for cleanup_wrong_tracks.py — the tool that finds wrong music files, removes them
RECOVERABLY on the user's confirmation, and re-queues them for the next ingest cycle.

Everything runs against a throw-away library in a temp dir with tag-only fake MP3s. The Recycle
Bin, Mongo, Spotify and AcoustID are all replaced by fakes; the end-to-end CLI test uses
--quarantine so it can never touch the real Recycle Bin.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cleanup_wrong_tracks as cwt
from services.audio_verifier import VerifyResult


def make_mp3(root: Path, rel: str, title: str, artist: str, spotify_id: str = "") -> Path:
    from mutagen.id3 import ID3, TIT2, TPE1, TXXX
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 32)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    if spotify_id:
        tags.add(TXXX(encoding=3, desc="SPOTIFY_ID", text=spotify_id))
    tags.save(str(path))
    return path


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.desi = make_mp3(self.root, "Library/Bollywood/Make Some Noise For The Desi Boyz - Pritam.mp3",
                             "Make Some Noise For The Desi Boyz", "Pritam", "sp_desi")
        self.karaoke = make_mp3(self.root, "Library/Bollywood/Kesariya (Karaoke Version) - Band.mp3",
                                "Kesariya (Karaoke Version)", "Band", "sp_kes")
        self.normal = make_mp3(self.root, "Library/Techno/Overdrive - Charlotte de Witte.mp3",
                               "Overdrive", "Charlotte de Witte", "sp_od")
        self.discovery = make_mp3(self.root, "Library/House/Discovery - Daft Punk.mp3", "Discovery", "Daft Punk")

    def scan(self, **kw):
        return cwt.scan_library(self.root, **kw)

    def by_name(self, entries):
        return {e["filename"]: e for e in entries}


class TestScan(LibraryCase):
    def test_a_named_file_is_marked_for_removal_as_user_confidence(self):
        got = self.by_name(self.scan(names=["Make Some Noise For The Desi Boyz"]))
        e = got[self.desi.name]
        self.assertEqual(e["confidence"], "user")
        self.assertTrue(e["delete"])
        self.assertEqual(e["spotify_id"], "sp_desi")

    def test_names_are_case_insensitive_and_match_tags_too(self):
        got = self.by_name(self.scan(names=["make some noise for the desi boyz"]))
        self.assertIn(self.desi.name, got)

    def test_keyword_only_is_listed_but_not_selected_by_default(self):
        e = self.by_name(self.scan())[self.karaoke.name]
        self.assertEqual(e["confidence"], "medium")
        self.assertFalse(e["delete"], "a keyword match alone must never be auto-selected for removal")

    def test_normal_files_are_left_alone(self):
        got = self.by_name(self.scan())
        self.assertNotIn(self.normal.name, got)
        self.assertNotIn(self.discovery.name, got, "'Discovery' must not trip a cover/karaoke rule")

    def test_scan_never_modifies_anything(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob("*.mp3")}
        self.scan(names=["Desi Boyz"])
        after = {str(p): p.read_bytes() for p in self.root.rglob("*.mp3")}
        self.assertEqual(before, after)

    def test_audio_proof_is_high_confidence_and_selected(self):
        bad = VerifyResult("suspect_version", 0.97, "Overdrive (Karaoke)", ["Band"], "identified as a karaoke recording")

        def fake_verify(path, title, artist, *a, **k):
            return bad if "Overdrive" in path else VerifyResult("inconclusive", reason="no match")

        with mock.patch("services.audio_verifier.verify_recording", side_effect=fake_verify):
            e = self.by_name(self.scan(verify_audio=True))[self.normal.name]
        self.assertEqual(e["confidence"], "high")
        self.assertTrue(e["delete"])
        self.assertIn("audio fingerprint", e["reasons"][0])

    def test_audio_results_are_cached_so_a_rescan_does_not_refingerprint(self):
        cache = self.root / "cache.json"
        with mock.patch("services.audio_verifier.verify_recording",
                        return_value=VerifyResult("verified", 0.99)) as v:
            self.scan(verify_audio=True, cache_path=cache)
            first = v.call_count
            self.scan(verify_audio=True, cache_path=cache)
        self.assertGreater(first, 0)
        self.assertEqual(v.call_count, first, "second scan re-fingerprinted cached files")

    def test_inconclusive_audio_never_flags_a_file(self):
        with mock.patch("services.audio_verifier.verify_recording",
                        return_value=VerifyResult("inconclusive", reason="no AcoustID match")):
            self.assertNotIn(self.normal.name, self.by_name(self.scan(verify_audio=True)))

    def test_quarantine_folder_is_never_scanned(self):
        make_mp3(self.root, "Library/_TO_DELETE/20260101/x (Karaoke).mp3", "x (Karaoke)", "y")
        self.assertNotIn("x (Karaoke).mp3", self.by_name(self.scan()))


class TestReportAndSelection(LibraryCase):
    def test_indexes_are_assigned_and_report_round_trips(self):
        entries = self.scan(names=["Desi Boyz"])
        self.assertEqual([e["idx"] for e in entries], list(range(1, len(entries) + 1)))
        with tempfile.TemporaryDirectory() as out:
            jp, tp = cwt.write_report(entries, self.root, Path(out))
            loaded = cwt.load_report(jp)
            self.assertEqual(loaded["entries"], entries)
            self.assertIn("REMOVE", tp.read_text(encoding="utf-8"))

    def test_index_list_parsing(self):
        self.assertEqual(cwt.parse_index_list("1,4,7-9"), {1, 4, 7, 8, 9})
        self.assertEqual(cwt.parse_index_list(""), set())
        self.assertEqual(cwt.parse_index_list(None), set())

    def test_default_selection_is_only_marked_entries(self):
        entries = self.scan(names=["Desi Boyz"])
        chosen = cwt.select_entries(entries)
        self.assertEqual([e["filename"] for e in chosen], [self.desi.name])

    def test_only_adds_review_entries_and_skip_removes(self):
        entries = self.scan(names=["Desi Boyz"])
        idx = self.by_name(entries)
        with_karaoke = cwt.select_entries(entries, only=str(idx[self.karaoke.name]["idx"]))
        self.assertEqual({e["filename"] for e in with_karaoke}, {self.desi.name, self.karaoke.name})
        skipped = cwt.select_entries(entries, skip=str(idx[self.desi.name]["idx"]))
        self.assertEqual(skipped, [])


class Fakes:
    def __init__(self, remove_ok=True):
        self.removed, self.index, self.queued = [], [], []
        self.remove_ok = remove_ok

    def remove(self, path, root, mode):
        if not self.remove_ok:
            return None
        self.removed.append((path, mode))
        os.remove(path)          # the fake stands in for the Recycle Bin, inside a temp dir
        return "recycle-bin"

    def index_remove(self, path):
        self.index.append(path)
        return 1

    def requeue(self, entries, reason):
        self.queued.extend(entries)
        return {"queued": len(entries)}


class TestApply(LibraryCase):
    def _apply(self, entries, fakes, **kw):
        return cwt.apply_report(entries, self.root, remove_fn=fakes.remove, index_remove_fn=fakes.index_remove,
                                requeue_fn=fakes.requeue, out=lambda s: None, **kw)

    def test_dry_run_changes_nothing_and_calls_no_hooks(self):
        f = Fakes()
        entries = cwt.select_entries(self.scan(names=["Desi Boyz"]))
        res = self._apply(entries, f, dry_run=True)
        self.assertTrue(self.desi.exists())
        self.assertEqual((f.removed, f.index, f.queued), ([], [], []))
        self.assertTrue(res["dry_run"])

    def test_apply_removes_drops_index_record_and_requeues_with_spotify_id(self):
        f = Fakes()
        entries = cwt.select_entries(self.scan(names=["Desi Boyz"]))
        res = self._apply(entries, f, dry_run=False)
        self.assertFalse(self.desi.exists())
        self.assertTrue(self.normal.exists(), "an unrelated file was touched")
        self.assertEqual(f.index, [str(self.desi)])
        self.assertEqual(f.queued, [{"spotify_id": "sp_desi", "title": "Make Some Noise For The Desi Boyz",
                                     "artist": "Pritam"}])
        self.assertEqual(res["index_removed"], 1)

    def test_requeue_falls_back_to_filename_when_tags_are_missing(self):
        untagged = self.root / "Library" / "Pop" / "Some Song - Some Artist.mp3"
        untagged.parent.mkdir(parents=True)
        untagged.write_bytes(b"\x00" * 32)
        f = Fakes()
        entries = self.scan(names=["Some Song"])
        self._apply(cwt.select_entries(entries), f, dry_run=False)
        self.assertEqual(f.queued, [{"spotify_id": "", "title": "Some Song", "artist": "Some Artist"}])

    def test_a_failed_removal_is_reported_and_not_requeued(self):
        f = Fakes(remove_ok=False)
        entries = cwt.select_entries(self.scan(names=["Desi Boyz"]))
        res = self._apply(entries, f, dry_run=False)
        self.assertTrue(self.desi.exists())
        self.assertEqual(f.queued, [], "re-queued a track whose wrong file is still on disk")
        self.assertEqual(len(res["skipped"]), 1)

    def test_paths_outside_the_library_root_are_refused(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = make_mp3(Path(elsewhere), "precious.mp3", "Precious", "Me")
            f = Fakes()
            entry = {"idx": 1, "path": str(outside), "rel_path": "precious.mp3", "title": "Precious", "artist": "Me",
                     "spotify_id": "", "delete": True}
            res = self._apply([entry], f, dry_run=False)
            self.assertTrue(outside.exists(), "removed a file OUTSIDE the library root")
            self.assertIn("outside", res["skipped"][0][1])

    def test_non_mp3_is_refused(self):
        txt = self.root / "Library" / "notes.txt"
        txt.write_text("keep me")
        f = Fakes()
        entry = {"idx": 1, "path": str(txt), "rel_path": "Library/notes.txt", "title": "", "artist": "",
                 "spotify_id": "", "delete": True}
        self._apply([entry], f, dry_run=False)
        self.assertTrue(txt.exists())

    def test_already_removed_file_is_skipped_quietly(self):
        f = Fakes()
        entries = cwt.select_entries(self.scan(names=["Desi Boyz"]))
        os.remove(self.desi)
        res = self._apply(entries, f, dry_run=False)
        self.assertEqual(f.queued, [])
        self.assertIn("not found", res["skipped"][0][1])

    def test_index_or_requeue_failure_does_not_undo_the_removal(self):
        f = Fakes()
        f.index_remove = mock.Mock(side_effect=RuntimeError("mongo down"))
        f.requeue = mock.Mock(side_effect=RuntimeError("queue broken"))
        entries = cwt.select_entries(self.scan(names=["Desi Boyz"]))
        res = self._apply(entries, f, dry_run=False)
        self.assertFalse(self.desi.exists())
        self.assertIn("error", res["requeue"])


class TestEndToEndCli(LibraryCase):
    def test_scan_preview_then_apply_with_quarantine(self):
        with tempfile.TemporaryDirectory() as reports, \
             mock.patch.object(cwt, "REPORTS_DIR", Path(reports)):
            self.assertEqual(cwt.main(["scan", "--root", str(self.root), "--names", "Desi Boyz"]), 0)
            report = next(Path(reports).glob("wrong_tracks_*.json"))

            # PREVIEW (no --yes): nothing may change
            self.assertEqual(cwt.main(["apply", "--report", str(report)]), 0)
            self.assertTrue(self.desi.exists())

            # APPLY with --quarantine: recoverable move, never the real Recycle Bin
            with mock.patch("services.requeue_service.requeue_tracks", return_value={"queued": 1}) as rq, \
                 mock.patch.object(cwt, "remove_from_library_index", return_value=1):
                self.assertEqual(cwt.main(["apply", "--report", str(report), "--yes", "--quarantine"]), 0)
            self.assertFalse(self.desi.exists())
            moved = list((self.root / "_TO_DELETE").rglob(self.desi.name))
            self.assertEqual(len(moved), 1, "the file was destroyed instead of quarantined")
            self.assertTrue(self.normal.exists())
            rq.assert_called_once()
            self.assertEqual(rq.call_args[0][0][0]["spotify_id"], "sp_desi")


if __name__ == "__main__":
    unittest.main()
