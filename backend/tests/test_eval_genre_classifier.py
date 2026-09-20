"""
Tests for eval_genre_classifier.py — the harness that measures a genre classifier against the
user's own folders. The metric code is pure, so it is tested with hand-built predictions whose
right answers are known; collection and the CLI run against a throw-away tag-only library.
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import eval_genre_classifier as ev
from eval_genre_classifier import Prediction, Sample, evaluate, label_of


def sample(label, name, bpm=None, artist="A"):
    return Sample(path=f"/lib/{label}/{name}.mp3", rel=f"Library/{label}/{name}.mp3", label=label,
                  artist=artist, title=name, bpm=bpm)


def make_mp3(root: Path, rel: str, title: str, artist: str, bpm: str = ""):
    from mutagen.id3 import ID3, TBPM, TIT2, TPE1
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 32)
    t = ID3()
    t.add(TIT2(encoding=3, text=title))
    t.add(TPE1(encoding=3, text=artist))
    if bpm:
        t.add(TBPM(encoding=3, text=bpm))
    t.save(str(p))
    return p


class TestLabelOf(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(label_of("Library/House"), "House")
        self.assertEqual(label_of("Library\\Techno"), "Techno")
        self.assertEqual(label_of("Library/House/Artist"), "House")
        self.assertEqual(label_of("NeedsReview/Someone"), "NeedsReview")
        self.assertEqual(label_of("Trance"), "Trance")
        self.assertEqual(label_of(""), "")


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        # 4 House, 4 Techno. Classifier: 3 House right, 1 House called Techno (conf .9),
        # 2 Techno right, 1 Techno called House (conf .9), 1 Techno abstains, 1 House says "Electronic".
        self.samples = ([sample("House", f"h{i}") for i in range(4)] +
                        [sample("Techno", f"t{i}") for i in range(4)])
        P = Prediction
        self.preds = {
            "/lib/House/h0.mp3": P("House", 0.95), "/lib/House/h1.mp3": P("House", 0.85),
            "/lib/House/h2.mp3": P("House", 0.60), "/lib/House/h3.mp3": P("Techno", 0.90),
            "/lib/Techno/t0.mp3": P("Techno", 0.95), "/lib/Techno/t1.mp3": P("Techno", 0.75),
            "/lib/Techno/t2.mp3": P("House", 0.90), "/lib/Techno/t3.mp3": None,
        }
        self.m = evaluate(self.samples, self.preds)

    def test_coverage_and_precision(self):
        self.assertEqual(self.m["total"], 8)
        self.assertEqual(self.m["answered"], 7)
        self.assertAlmostEqual(self.m["coverage"], 7 / 8, places=3)
        self.assertAlmostEqual(self.m["precision"], 5 / 7, places=3)

    def test_catch_all_prediction_counts_as_abstaining(self):
        samples = [sample("House", "x")]
        for abstain in ("Electronic", "Library/Electronic", "NeedsReview/Foo", ""):
            m = evaluate(samples, {samples[0].path: Prediction(abstain, 0.99)})
            self.assertEqual(m["answered"], 0, abstain)

    def test_precision_at_thresholds_rises_as_the_bar_rises(self):
        by_t = {t["threshold"]: t for t in self.m["by_threshold"]}
        self.assertEqual(by_t[0.5]["answered"], 7)
        self.assertEqual(by_t[0.9]["answered"], 4)             # .95 .90 .95 .90
        self.assertAlmostEqual(by_t[0.9]["precision"], 2 / 4, places=3)
        self.assertLess(by_t[0.9]["coverage"], by_t[0.5]["coverage"])

    def test_per_folder_precision_recall_f1(self):
        house, techno = self.m["per_folder"]["House"], self.m["per_folder"]["Techno"]
        self.assertEqual(house["support"], 4)
        self.assertEqual(house["predicted"], 4)                # h0 h1 h2 + techno t2 called House
        self.assertEqual(house["correct"], 3)
        self.assertAlmostEqual(house["precision"], 3 / 4, places=3)
        self.assertAlmostEqual(house["recall"], 3 / 4, places=3)
        self.assertAlmostEqual(techno["recall"], 2 / 4, places=3)   # t3 abstained = a miss
        self.assertAlmostEqual(techno["precision"], 2 / 3, places=3)

    def test_confusion_and_confident_disagreements(self):
        conf = {(c["true"], c["predicted"]): c["count"] for c in self.m["confusion"]}
        self.assertEqual(conf[("House", "Techno")], 1)
        self.assertEqual(conf[("Techno", "House")], 1)
        paths = {d["path"] for d in self.m["disagreements"]}
        self.assertEqual(paths, {"/lib/House/h3.mp3", "/lib/Techno/t2.mp3"})
        self.assertEqual(self.m["disagreements"][0]["confidence"], 0.9)

    def test_verified_and_unverified_answers_are_scored_separately(self):
        P = Prediction
        samples = [sample("Techno", f"t{i}") for i in range(4)]
        preds = {
            "/lib/Techno/t0.mp3": P("Techno", 0.9, verified=True),       # verified, right
            "/lib/Techno/t1.mp3": P("House", 0.9, verified=True),        # verified, wrong
            "/lib/Techno/t2.mp3": P("Techno", 0.7, verified=False),      # unverified, right
            "/lib/Techno/t3.mp3": P("Techno", 0.7, verified=False),      # unverified, right
        }
        trust = evaluate(samples, preds)["by_trust"]
        self.assertEqual((trust["verified"]["answered"], trust["verified"]["precision"]), (2, 0.5))
        self.assertEqual((trust["unverified"]["answered"], trust["unverified"]["precision"]), (2, 1.0))
        self.assertAlmostEqual(trust["verified"]["coverage"] + trust["unverified"]["coverage"], 1.0)

    def test_predictions_are_verified_unless_stated_otherwise(self):
        self.assertTrue(Prediction("House", 0.9).verified)

    def test_a_crashing_classifier_abstains(self):
        boom = mock.Mock(side_effect=RuntimeError("x"))
        preds = ev.run_classifier(self.samples, boom)
        self.assertTrue(all(v is None for v in preds.values()))

    def test_empty_input_does_not_divide_by_zero(self):
        m = evaluate([], {})
        self.assertEqual((m["total"], m["coverage"], m["precision"]), (0, 0.0, 0.0))


class TestBpm(unittest.TestCase):
    def test_percentiles(self):
        vals = [float(v) for v in range(100, 201, 10)]        # 100..200
        self.assertEqual(ev.percentile(vals, 50), 150.0)
        self.assertEqual(ev.percentile(vals, 0), 100.0)
        self.assertEqual(ev.percentile(vals, 100), 200.0)
        self.assertEqual(ev.percentile([], 50), 0.0)

    def test_report_and_priors_need_enough_data(self):
        s = [sample("Techno", f"t{i}", bpm=130 + i) for i in range(20)] + [sample("House", "h", bpm=124)]
        rep = ev.bpm_report(s)
        self.assertEqual(rep["Techno"]["with_bpm"], 20)
        self.assertTrue(130 <= rep["Techno"]["median"] <= 150)
        priors = ev.suggest_bpm_priors(rep, min_tracks=15)
        self.assertIn("Techno", priors)
        self.assertNotIn("House", priors, "one BPM value must not become a 'range'")
        lo, hi = priors["Techno"]
        self.assertLess(lo, rep["Techno"]["p10"])
        self.assertGreater(hi, rep["Techno"]["p90"])


class TestCollect(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        for i in range(5):
            make_mp3(self.root, f"Library/House/h{i}.mp3", f"h{i}", "AH", "124")
        for i in range(3):
            make_mp3(self.root, f"Library/Techno/t{i}.mp3", f"t{i}", "AT", "138.5")
        make_mp3(self.root, "Library/Electronic/e.mp3", "e", "AE")
        make_mp3(self.root, "Library/NeedsReview/Artist/n.mp3", "n", "AN")
        make_mp3(self.root, "Library/loose.mp3", "loose", "AL")

    def test_labels_come_from_folders_and_catch_alls_are_excluded(self):
        s = ev.collect_samples(self.root)
        self.assertEqual({x.label for x in s}, {"House", "Techno"})
        self.assertEqual(len(s), 8)
        self.assertFalse(any("loose" in x.path for x in s))

    def test_bpm_and_tags_are_read(self):
        s = {x.title: x for x in ev.collect_samples(self.root)}
        self.assertEqual(s["h0"].bpm, 124.0)
        self.assertEqual(s["t0"].bpm, 138.5)
        self.assertEqual(s["t0"].artist, "AT")

    def test_per_folder_sampling_is_capped_and_reproducible(self):
        a = ev.collect_samples(self.root, per_folder=2, seed=7)
        b = ev.collect_samples(self.root, per_folder=2, seed=7)
        self.assertEqual([x.path for x in a], [x.path for x in b])
        self.assertEqual(sum(1 for x in a if x.label == "House"), 2)
        self.assertEqual(sum(1 for x in a if x.label == "Techno"), 2)


class TestStaticClassifier(unittest.TestCase):
    def test_overrides_answer_and_unknown_artists_abstain(self):
        predict = ev.make_static_classifier()
        p = predict(sample("Techno", "x", artist="Charlotte de Witte"))
        self.assertEqual((p.genre, p.source), ("Techno", "artist_override"))
        self.assertIsNone(predict(sample("House", "y", artist="Zzz Totally Unknown Artist 9")))
        # Hardwell is overridden to the catch-all -> "Electronic" -> counts as abstaining downstream.
        hw = predict(sample("House", "z", artist="Hardwell"))
        self.assertEqual(label_of(hw.genre), "Electronic")


class TestCli(unittest.TestCase):
    def test_static_run_writes_reports_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as out:
            root = Path(lib)
            for i in range(3):
                make_mp3(root, f"Library/Techno/t{i}.mp3", f"t{i}", "Charlotte de Witte", "138")
            make_mp3(root, "Library/House/h0.mp3", "h0", "Charlotte de Witte", "124")   # misfiled on purpose
            before = {str(p): p.read_bytes() for p in root.rglob("*.mp3")}
            with mock.patch.object(ev, "REPORTS_DIR", Path(out)):
                self.assertEqual(ev.main(["--root", str(root), "--classifier", "static"]), 0)
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob("*.mp3")})
            js = next(Path(out).glob("genre_eval_static_*.json"))
            data = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(data["metrics"]["total"], 4)
            rows = list(csv.DictReader(open(next(Path(out).glob("*_disagreements.csv")), encoding="utf-8")))
            self.assertEqual(len(rows), 1, "the misfiled House track should be the one confident disagreement")
            self.assertEqual(rows[0]["folder"], "House")
            self.assertEqual(rows[0]["predicted"], "Techno")

    def test_bpm_priors_file_is_written(self):
        with tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as out:
            root = Path(lib)
            for i in range(16):
                make_mp3(root, f"Library/Techno/t{i}.mp3", f"t{i}", "X", str(130 + i))
            dest = Path(out) / "priors.json"
            self.assertEqual(ev.main(["--root", str(root), "--bpm-report", "--write-bpm-priors", str(dest)]), 0)
            self.assertIn("Techno", json.loads(dest.read_text(encoding="utf-8")))

    def test_missing_folder_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as lib:
            self.assertEqual(ev.main(["--root", lib]), 2)


if __name__ == "__main__":
    unittest.main()
