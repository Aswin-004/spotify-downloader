"""
Tests for services/audio_genre_model.py (confirmed-track model) and the shape of
services/audio_genre_features.py. The model tests use SYNTHETIC feature clusters — three well
separated "genres" plus a deliberately confusable pair — so what is being tested is the logic
(labels, honesty checks, thresholds, refusal to act when unsure), not scikit-learn.
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from services import audio_genre_features as ag
from services import audio_genre_model as am


def synthetic(n_per=40, dim=12, seed=0, overlap=("C", "D")):
    """Clusters A, B (well separated) and C, D (heavily overlapping) -> (rows, features-by-path)."""
    rng = np.random.default_rng(seed)
    centers = {"A": 0.0, "B": 6.0, "C": 12.0, "D": 12.6}
    rows, feats = [], {}
    for label, c in centers.items():
        for i in range(n_per):
            path = f"/lib/{label}/{i}.mp3"
            feats[path] = list(rng.normal(c, 1.0, dim))
            rows.append({"path": path, "folder": label, "action": "ok", "tag_folder": ""})
    return rows, feats


class TestTrainingRows(unittest.TestCase):
    def test_labels_come_from_confirmed_tracks_only(self):
        rows = [
            {"path": "1", "folder": "House", "action": "ok", "tag_folder": ""},                 # evidence agrees
            {"path": "2", "folder": "Techno", "action": "unknown", "tag_folder": ""},            # nobody knows: NOT a label
            {"path": "3", "folder": "Techno", "action": "move", "tag_folder": ""},               # evidence disagrees: NOT a label
            {"path": "4", "folder": "House", "action": "unknown", "tag_folder": "Electronic"},   # moved by hand: the user's label
            {"path": "5", "folder": "Electronic", "action": "ok", "tag_folder": ""},             # catch-all is never a label
            {"path": "6", "folder": "Pop", "action": "review", "tag_folder": ""},
        ]
        got = {r["path"]: kind for r, kind in am.training_rows(rows)}
        self.assertEqual(got, {"1": "ok", "4": "hand"})

    def test_a_hand_moved_file_is_labelled_by_the_folder_even_if_evidence_disagreed(self):
        rows = [{"path": "1", "folder": "House", "action": "review", "tag_folder": "Pop"}]
        self.assertEqual(am.training_rows(rows)[0][1], "hand")

    def test_files_without_features_are_skipped(self):
        rows, feats = synthetic(n_per=3)
        feats["/lib/A/0.mp3"] = None
        ts = am.build_training_set(rows, lambda p: feats[p])
        self.assertEqual(len(ts.y), len(rows) - 1)
        self.assertNotIn("/lib/A/0.mp3", ts.paths)

    def test_rare_crates_are_dropped(self):
        rows, feats = synthetic(n_per=30)
        rows += [{"path": f"/lib/Tamil/{i}.mp3", "folder": "Tamil", "action": "ok", "tag_folder": ""} for i in range(4)]
        feats.update({f"/lib/Tamil/{i}.mp3": [0.0] * 12 for i in range(4)})
        ts = am._drop_rare(am.build_training_set(rows, lambda p: feats[p]), am.MIN_PER_CLASS)
        self.assertNotIn("Tamil", set(ts.y.tolist()))


class TestTrainingAndThresholds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, feats = synthetic()
        cls.ts = am.build_training_set(rows, lambda p: feats[p])
        cls.model, cls.oof = am.train(cls.ts, [f"f{i}" for i in range(12)], target_precision=0.9, folds=3)

    def test_separable_crates_are_learned_with_high_out_of_fold_accuracy(self):
        rep = self.model.report["per_class"]
        for crate in ("A", "B"):
            self.assertGreaterEqual(rep[crate]["precision"], 0.95, crate)
            self.assertGreaterEqual(rep[crate]["recall"], 0.95, crate)

    def test_it_acts_on_most_of_the_clear_crates_but_only_a_fraction_of_the_confusable_ones(self):
        rep = self.model.report["per_class"]
        for crate in ("A", "B"):
            self.assertGreaterEqual(rep[crate]["acted"], 0.9 * rep[crate]["support"], crate)
        for crate in ("C", "D"):                                        # it cannot tell C from D: mostly silent
            self.assertLess(rep[crate]["acted"], 0.6 * rep[crate]["support"], crate)

    def test_it_only_acts_where_it_measured_the_precision(self):
        rep = self.model.report["per_class"]
        for crate, r in rep.items():
            if r["acted"]:
                self.assertGreaterEqual(r["acted_precision"], 0.9, crate)

    def test_out_of_fold_probabilities_are_honest_rows_not_scored_by_a_model_that_saw_them(self):
        # the OOF matrix must NOT be the resubstitution fit: a memorising model would score ~1.0 on the
        # confusable pair, but out-of-fold it cannot
        pred = np.asarray(self.model.classes)[self.oof.argmax(axis=1)]
        cd = np.isin(self.ts.y, ["C", "D"])
        self.assertLess(float((pred[cd] == self.ts.y[cd]).mean()), 0.85)

    def test_report_names_the_chosen_model_and_lists_every_candidate(self):
        rep = self.model.report
        self.assertIn(rep["chosen"], {"logistic", "forest", "boosting"})
        self.assertEqual(set(rep["candidates"]), {"logistic", "forest", "boosting"})
        self.assertEqual(rep["n_train"], len(self.ts.y))

    def test_predict_and_acts(self):
        rng = np.random.default_rng(5)
        a, b = rng.normal(0.0, 1.0, (1, 12)), rng.normal(6.0, 1.0, (1, 12))
        (pa, ca), (pb, cb) = am.predict(self.model, np.vstack([a, b]))
        self.assertEqual((pa, pb), ("A", "B"))
        self.assertTrue(am.acts(self.model, pa, ca))
        self.assertFalse(am.acts(self.model, "C", 0.99) and self.model.thresholds["C"] > 1.0)
        self.assertEqual(am.predict(self.model, np.zeros((0, 12))), [])

    def test_a_low_confidence_never_acts(self):
        self.assertFalse(am.acts(self.model, "A", 0.30))

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = am.save(self.model, Path(d, "m.joblib"))
            loaded = am.load(p)
            X = np.random.default_rng(1).normal(6.0, 1.0, (3, 12))
            self.assertEqual(am.predict(loaded, X), am.predict(self.model, X))
            self.assertIsNone(am.load(Path(d, "missing.joblib")))
            Path(d, "bad.joblib").write_bytes(b"not a model")
            self.assertIsNone(am.load(Path(d, "bad.joblib")))


class TestMoveThresholds(unittest.TestCase):
    def test_the_bar_is_just_above_the_most_confident_mistake(self):
        classes = ["A", "B"]
        # rows: true labels and probabilities [pA, pB]
        y = np.asarray(["A", "A", "A", "B", "B"], dtype=object)
        proba = np.asarray([[0.9, 0.1], [0.6, 0.4], [0.3, 0.7],     # 3rd A track predicted B at 0.70: a mistake
                            [0.2, 0.8], [0.1, 0.9]])
        th = am.choose_move_thresholds(proba, y, classes, margin=0.02)
        self.assertAlmostEqual(th["B"], 0.72)                        # must beat the 0.70 mistake
        self.assertEqual(th["A"], am.MIN_CONFIDENCE)                 # A was never predicted wrongly

    def test_a_crate_it_was_wrong_about_at_near_certainty_may_never_move(self):
        y = np.asarray(["A", "B"], dtype=object)
        proba = np.asarray([[0.01, 0.99], [0.0, 1.0]])               # predicted B at 0.99 for a true A
        self.assertEqual(am.choose_move_thresholds(proba, y, ["A", "B"])["B"], 1.01)

    def test_acts_to_move_needs_both_bars(self):
        import types
        m = types.SimpleNamespace(thresholds={"A": 0.6, "B": 0.6}, move_thresholds={"A": 0.9, "B": 1.01})
        self.assertFalse(am.acts_to_move(m, "A", 0.8))              # confident enough to confirm, not to move
        self.assertTrue(am.acts_to_move(m, "A", 0.95))
        self.assertFalse(am.acts_to_move(m, "B", 0.999))            # 'never'
        self.assertFalse(am.acts_to_move(m, "Z", 0.99))             # unknown crate

    def test_training_reports_the_move_bars(self):
        rows, feats = synthetic()
        model, _ = am.train(am.build_training_set(rows, lambda p: feats[p]), [f"f{i}" for i in range(12)], folds=3)
        mv = model.report["move_thresholds"]
        self.assertEqual(set(mv), {"A", "B", "C", "D"})
        # the confusable pair has made confident mistakes, so its bar is higher than for the clear crates
        self.assertGreater(min(mv["C"] or 2, mv["D"] or 2), max(mv["A"] or 0, mv["B"] or 0))
        self.assertEqual(model.move_thresholds["A"] <= 1.0, mv["A"] is not None)


class TestHandPlacedTest(unittest.TestCase):
    def test_hand_moved_tracks_are_scored_by_a_model_that_never_saw_them(self):
        rows, feats = synthetic(n_per=30)
        # 12 A/B tracks the user placed by hand (tag names another crate)
        for r in rows:
            if r["folder"] in ("A", "B") and int(r["path"].split("/")[-1].split(".")[0]) < 6:
                r["tag_folder"] = "C" if r["folder"] == "A" else "D"
        ts = am.build_training_set(rows, lambda p: feats[p])
        model, _ = am.train(ts, [f"f{i}" for i in range(12)], folds=3)
        hp = model.report["hand_placed_test"]
        self.assertEqual(hp["n"], 12)
        self.assertGreaterEqual(hp["accuracy_all"], 0.9)                 # separable, so learnable from the 'ok' tracks

    def test_no_hand_placed_tracks_means_no_such_test(self):
        rows, feats = synthetic()
        model, _ = am.train(am.build_training_set(rows, lambda p: feats[p]), [f"f{i}" for i in range(12)], folds=3)
        self.assertIsNone(model.report["hand_placed_test"])

    def test_too_little_data_is_refused(self):
        rows, feats = synthetic(n_per=5)
        with self.assertRaises(ValueError):
            am.train(am.build_training_set(rows, lambda p: feats[p]), ["f"] * 12)


class TestFeatureShape(unittest.TestCase):
    def test_names_are_unique_and_the_count_matches_the_extractor(self):
        self.assertEqual(len(ag.FEATURE_NAMES), len(set(ag.FEATURE_NAMES)))
        self.assertEqual(len(ag.FEATURE_NAMES), 2 * ag.N_MFCC + 12 + 7 + 12 + len(ag.BANDS) + len(ag.TEMPO_BANDS) + 10)

    def test_tempo_folding(self):
        self.assertEqual(ag._fold_tempo(87), 87.0)
        self.assertEqual(ag._fold_tempo(174), 87.0)                      # double-time and half-time agree
        self.assertEqual(ag._fold_tempo(43.5), 87.0)
        self.assertEqual(ag._fold_tempo(0), 0.0)
        self.assertTrue(80 <= ag._fold_tempo(133) < 160)

    def test_slice_comes_from_the_middle_and_short_tracks_start_at_zero(self):
        self.assertEqual(ag._slice_offset(30), 0.0)
        self.assertEqual(ag._slice_offset(45), 0.0)
        self.assertAlmostEqual(ag._slice_offset(200), 70.0)               # 35 % in, intro skipped
        self.assertAlmostEqual(ag._slice_offset(50), 5.0)                 # never runs past the end

    def test_cache_key_survives_a_move_but_not_an_edit_of_the_audio(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d, "x", "song.mp3")
            a.parent.mkdir()
            a.write_bytes(b"1234")
            k1 = ag.file_key(str(a))
            b = Path(d, "y", "song.mp3")
            b.parent.mkdir()
            a.rename(b)
            self.assertEqual(ag.file_key(str(b)), k1)
            b.write_bytes(b"12345")
            self.assertNotEqual(ag.file_key(str(b)), k1)

    def test_cache_key_survives_tag_edits(self):
        """Rewriting the genre / artist tag (what the resort tool does) must not invalidate the analysis."""
        from mutagen.id3 import ID3, TCON, TIT2, TPE1
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "song.mp3")
            p.write_bytes(b"\xff\xfb\x90\x00" * 500)                       # stand-in for audio frames
            t = ID3()
            t.add(TIT2(encoding=3, text="T"))
            t.save(str(p))
            k0 = ag.file_key(str(p))
            t = ID3(str(p))
            t.add(TPE1(encoding=3, text="A Much Longer Artist Name, Another Artist, And A Third"))
            t.add(TCON(encoding=3, text="Drum and Bass"))
            t.save(str(p))
            size_after = p.stat().st_size
            self.assertEqual(ag.file_key(str(p)), k0)
            self.assertNotIn(f"|{size_after}", k0)                          # the key is not the raw file size
            self.assertNotEqual(ag.legacy_key(str(p)), k0)

    def test_id3v2_size(self):
        from mutagen.id3 import ID3, TIT2
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "a.mp3")
            p.write_bytes(b"\xff\xfb" * 100)
            self.assertEqual(ag._id3v2_size(str(p)), 0)                     # no tag
            t = ID3()
            t.add(TIT2(encoding=3, text="x" * 300))
            t.save(str(p))
            n = ag._id3v2_size(str(p))
            self.assertGreater(n, 300)
            self.assertEqual(p.read_bytes()[n:n + 2], b"\xff\xfb")          # audio starts exactly where the tag ends
            self.assertEqual(ag._id3v2_size(str(Path(d, "missing.mp3"))), 0)

    def test_a_cache_written_with_the_old_key_is_migrated_not_discarded(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            song = Path(d, "song.mp3")
            song.write_bytes(b"\xff\xfb" * 100)
            cache_path = Path(d, "c.json")
            cache_path.write_text(json.dumps({"version": ag.FEATURE_VERSION, "features": {ag.legacy_key(str(song)): [1.0, 2.0]}}),
                                  encoding="utf-8")                          # no key_format => the old layout
            c = ag.FeatureCache(cache_path)
            self.assertEqual(c.data, {})
            self.assertEqual(c.lookup(str(song)), (True, [1.0, 2.0]))
            self.assertIn(ag.file_key(str(song)), c.data)                    # promoted to the new key
            c.save()
            self.assertEqual(ag.FeatureCache(cache_path).data, {ag.file_key(str(song)): [1.0, 2.0]})

    def test_extract_many_only_analyses_what_the_cache_lacks(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.mp3"), Path(d, "b.mp3")
            a.write_bytes(b"\xff\xfb" * 50)
            b.write_bytes(b"\xff\xfb" * 60)
            cache = ag.FeatureCache(Path(d, "c.json"))
            cache.data[ag.file_key(str(a))] = [9.0]
            with mock.patch.object(ag, "extract_features", return_value=[1.0]) as ex:
                out = ag.extract_many([str(a), str(b)], cache, n_jobs=1)
            self.assertEqual(ex.call_count, 1)
            self.assertEqual(out, {str(a): [9.0], str(b): [1.0]})
            self.assertTrue(Path(d, "c.json").is_file())

    def test_cache_roundtrip_and_version_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            c = ag.FeatureCache(Path(d, "c.json"))
            c.data["k"] = [1.0, 2.0]
            c.data["bad"] = None
            c.save()
            self.assertEqual(ag.FeatureCache(Path(d, "c.json")).data, {"k": [1.0, 2.0], "bad": None})
            import json
            blob = json.loads(Path(d, "c.json").read_text(encoding="utf-8"))
            blob["version"] = ag.FEATURE_VERSION - 1
            Path(d, "c.json").write_text(json.dumps(blob), encoding="utf-8")
            self.assertEqual(ag.FeatureCache(Path(d, "c.json")).data, {})

    def test_undecodable_and_silent_files_yield_none(self):
        with tempfile.TemporaryDirectory() as d:
            junk = Path(d, "junk.mp3")
            junk.write_bytes(b"not audio at all" * 100)
            self.assertIsNone(ag.extract_features(str(junk)))
            self.assertIsNone(ag.extract_features(str(Path(d, "missing.mp3"))))

    def test_a_real_signal_gives_a_full_finite_vector(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as d:
            sr = 22050
            t = np.arange(sr * 12) / sr
            beat = (np.sin(2 * np.pi * 60 * t) * (np.mod(t, 0.5) < 0.1)) + 0.3 * np.sin(2 * np.pi * 440 * t)
            p = Path(d, "tone.wav")
            sf.write(str(p), beat.astype("float32"), sr)
            v = ag.extract_features(str(p))
            self.assertIsNotNone(v)
            self.assertEqual(len(v), len(ag.FEATURE_NAMES))
            self.assertTrue(np.isfinite(v).all())
            tempo = v[ag.FEATURE_NAMES.index("tempo_folded")]
            self.assertTrue(80 <= tempo < 160)
            self.assertAlmostEqual(sum(v[ag.FEATURE_NAMES.index(f"tg{i}")] for i in range(len(ag.TEMPO_BANDS))), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
