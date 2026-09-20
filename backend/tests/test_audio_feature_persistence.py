"""
Regression suite for the two audio-feature persistence bugs found in Phase 0.

Scope: these two bugs ONLY. No backfill, no live MongoDB, no recommendation
logic. Every collection below is an in-memory fake implementing just the
subset of the pymongo API that bpm_key_service uses, so the dotted-path
$set semantics are exercised for real rather than mocked away.

Bug 1 - bpm_key_service.persist_audio_features() replaced the entire
        audio_features sub-document, deleting lastfm_*/gemini_* enrichment
        written by other services. Reachable in normal ingest: auto_downloader
        enriches a track and *then* re-persists BPM for DnB/Techno half-time
        correction (services/auto_downloader.py:1072).

Bug 2 - routes/library.py passed resolved.stem as identity_key, but
        library_index.identity_key is "sp:<spotify_id>". The lookup never
        matched, so manual BPM/key corrections reached the ID3 tags and
        silently never reached MongoDB.

Run:
    python -m unittest tests.test_audio_feature_persistence -v
    (from the backend/ directory)
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bpm_key_service import (
    persist_audio_features,
    resolve_identity_key,
    _norm_path,
    ANALYSIS_VERSION,
)


# ---------------------------------------------------------------------------
# Minimal in-memory stand-in for a pymongo collection
# ---------------------------------------------------------------------------

def _matches(doc, query):
    for field, cond in query.items():
        val = doc.get(field)
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


def _apply_dotted_set(doc, updates):
    """Apply {"a.b": v} the way MongoDB's $set does - create intermediate
    documents, replace only the addressed leaf, leave siblings untouched."""
    for path, value in updates.items():
        target = doc
        parts = path.split(".")
        for part in parts[:-1]:
            nxt = target.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                target[part] = nxt
            target = nxt
        target[parts[-1]] = value


class FakeCollection:
    def __init__(self, docs=None):
        # deep copy: audio_features is nested, and a shallow copy would let one
        # test's write leak into the shared ENRICHED fixture used by the next
        self.docs = [copy.deepcopy(d) for d in (docs or [])]
        self.update_calls = []

    def count_documents(self, query, limit=None):
        n = sum(1 for d in self.docs if _matches(d, query))
        return min(n, limit) if limit else n

    def find(self, query, projection=None):
        return [dict(d) for d in self.docs if _matches(d, query)]

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def update_one(self, query, update):
        self.update_calls.append((query, update))
        for d in self.docs:
            if _matches(d, query):
                if "$set" in update:
                    _apply_dotted_set(d, update["$set"])
                return
        raise AssertionError("update_one matched no document: %r" % (query,))

    def get(self, identity_key):
        return next(d for d in self.docs if d.get("identity_key") == identity_key)


ENRICHED = {
    "identity_key": "sp:abc123",
    "title": "Barbaadiyan",
    "artist": "Sachet Tandon",
    "final_path": r"C:\Users\x\DJ music\Library/Bollywood\Barbaadiyan_1.mp3",
    "filename": "Barbaadiyan.mp3",
    "audio_features": {
        "bpm": 129,
        "key": "G#m",
        "camelot": "1A",
        "confidence": 0.71,
        "rms_energy": 0.152,
        "spectral_centroid_mean": 2100.0,
        "analysis_source": "librosa",
        "analysis_version": "librosa-1.0",
        # written by OTHER services - must survive a BPM/key write
        "lastfm_genre": "bollywood",
        "lastfm_mood": "uplifting",
        "gemini_genre": "Bollywood",
        "gemini_subgenre": "Bhangra",
        "gemini_mood": "uplifting, energetic",
        "gemini_energy": 0.75,
    },
}


class TestEnrichmentSurvives(unittest.TestCase):
    """Bug 1 - the whole-subdocument overwrite."""

    def setUp(self):
        self.col = FakeCollection([ENRICHED])

    def test_lastfm_and_gemini_enrichment_survive_bpm_write(self):
        ok = persist_audio_features(
            "sp:abc123",
            {"bpm": 140, "key": "F min", "analyzed": True},
            _col=self.col,
        )
        self.assertTrue(ok)
        af = self.col.get("sp:abc123")["audio_features"]
        self.assertEqual(af["lastfm_genre"], "bollywood")
        self.assertEqual(af["lastfm_mood"], "uplifting")
        self.assertEqual(af["gemini_genre"], "Bollywood")
        self.assertEqual(af["gemini_subgenre"], "Bhangra")
        self.assertEqual(af["gemini_mood"], "uplifting, energetic")
        self.assertEqual(af["gemini_energy"], 0.75)

    def test_dnb_repersist_after_enrichment_is_non_destructive(self):
        """Reproduces the exact auto_downloader ordering: enrich, then
        re-persist BPM with a genre hint. Previously wiped the enrichment."""
        persist_audio_features(
            "sp:abc123",
            {"bpm": 174, "key": "F min", "camelot": "4A", "confidence": 0.66,
             "rms_energy": 0.2, "analyzed": True},
            _col=self.col,
        )
        af = self.col.get("sp:abc123")["audio_features"]
        self.assertEqual(af["bpm"], 174)
        self.assertEqual(af["camelot"], "4A")
        self.assertEqual(af["gemini_genre"], "Bollywood")
        self.assertEqual(af["lastfm_genre"], "bollywood")

    def test_partial_write_does_not_null_existing_fields(self):
        """A manual BPM-only correction must not blank camelot/confidence/
        energy, which the old code explicitly set to None."""
        persist_audio_features(
            "sp:abc123",
            {"bpm": 128, "key": None, "analyzed": True, "manual": True},
            _col=self.col,
        )
        af = self.col.get("sp:abc123")["audio_features"]
        self.assertEqual(af["bpm"], 128)
        self.assertEqual(af["camelot"], "1A")
        self.assertEqual(af["confidence"], 0.71)
        self.assertEqual(af["rms_energy"], 0.152)
        self.assertEqual(af["spectral_centroid_mean"], 2100.0)
        self.assertEqual(af["key"], "G#m")  # None in result => left untouched

    def test_update_never_replaces_whole_subdocument(self):
        """Guards the root cause directly: no $set key may be the bare
        'audio_features' path."""
        persist_audio_features(
            "sp:abc123", {"bpm": 130, "analyzed": True}, _col=self.col
        )
        _query, update = self.col.update_calls[-1]
        self.assertIn("$set", update)
        for key in update["$set"]:
            self.assertNotEqual(key, "audio_features")
            self.assertTrue(key.startswith("audio_features."))

    def test_provenance_always_written(self):
        persist_audio_features(
            "sp:abc123", {"bpm": 130, "analyzed": True}, _col=self.col
        )
        af = self.col.get("sp:abc123")["audio_features"]
        self.assertEqual(af["analysis_source"], "librosa")
        self.assertEqual(af["analysis_version"], ANALYSIS_VERSION)
        self.assertIn("analysis_timestamp", af)

    def test_optional_features_written_when_present(self):
        persist_audio_features(
            "sp:abc123",
            {"bpm": 130, "analyzed": True, "duration_sec": 210.5,
             "zero_crossing_rate": 0.08},
            _col=self.col,
        )
        af = self.col.get("sp:abc123")["audio_features"]
        self.assertEqual(af["duration_sec"], 210.5)
        self.assertEqual(af["zero_crossing_rate"], 0.08)

    def test_creates_subdocument_when_absent(self):
        col = FakeCollection([{"identity_key": "sp:new", "final_path": "/x/y.mp3"}])
        self.assertTrue(persist_audio_features(
            "sp:new", {"bpm": 120, "camelot": "8A", "analyzed": True}, _col=col
        ))
        self.assertEqual(col.get("sp:new")["audio_features"]["bpm"], 120)

    def test_unknown_identity_key_returns_false(self):
        self.assertFalse(persist_audio_features(
            "sp:does-not-exist", {"bpm": 130, "analyzed": True}, _col=self.col
        ))
        self.assertEqual(self.col.update_calls, [])

    def test_not_analyzed_returns_false(self):
        self.assertFalse(persist_audio_features(
            "sp:abc123", {"bpm": 130, "analyzed": False}, _col=self.col
        ))
        self.assertEqual(self.col.update_calls, [])

    def test_empty_identity_key_returns_false(self):
        self.assertFalse(persist_audio_features(
            "", {"bpm": 130, "analyzed": True}, _col=self.col
        ))
        self.assertEqual(self.col.update_calls, [])


class TestResolveIdentityKey(unittest.TestCase):
    """Bug 2 - identity_key resolution."""

    def setUp(self):
        self.col = FakeCollection([ENRICHED])

    def test_filename_stem_is_not_an_identity_key(self):
        """The old code's assumption, stated as a test."""
        self.assertNotEqual(ENRICHED["identity_key"], "Barbaadiyan_1")
        self.assertTrue(ENRICHED["identity_key"].startswith("sp:"))

    def test_mixed_separator_final_path_resolves(self):
        """Live rows mix separators; exact string match alone fails."""
        got = resolve_identity_key(
            final_path=r"C:\Users\x\DJ music\Library\Bollywood\Barbaadiyan_1.mp3",
            _col=self.col,
        )
        self.assertEqual(got, "sp:abc123")

    def test_exact_final_path_resolves(self):
        self.assertEqual(
            resolve_identity_key(final_path=ENRICHED["final_path"], _col=self.col),
            "sp:abc123",
        )

    def test_filename_fallback_handles_collision_suffix(self):
        """final_path ends _1.mp3 while filename has no suffix - resolving by
        the un-suffixed filename must still find the row."""
        col = FakeCollection([ENRICHED])
        self.assertEqual(
            resolve_identity_key(filename="Barbaadiyan.mp3", _col=col),
            "sp:abc123",
        )

    def test_soft_deleted_rows_are_ignored(self):
        col = FakeCollection([dict(ENRICHED, missing=True)])
        self.assertEqual(
            resolve_identity_key(final_path=ENRICHED["final_path"], _col=col), ""
        )

    def test_ambiguous_basename_refuses_to_guess(self):
        col = FakeCollection([
            {"identity_key": "sp:one", "final_path": r"C:\a\Track.mp3"},
            {"identity_key": "sp:two", "final_path": r"C:\b\Track.mp3"},
        ])
        self.assertEqual(
            resolve_identity_key(final_path=r"C:\somewhere\else\Track.mp3", _col=col), ""
        )

    def test_ambiguous_filename_refuses_to_guess(self):
        col = FakeCollection([
            {"identity_key": "sp:one", "final_path": r"C:\a\X_1.mp3", "filename": "X.mp3"},
            {"identity_key": "sp:two", "final_path": r"C:\b\X_2.mp3", "filename": "X.mp3"},
        ])
        self.assertEqual(resolve_identity_key(filename="X.mp3", _col=col), "")

    def test_unknown_path_returns_empty(self):
        self.assertEqual(
            resolve_identity_key(final_path=r"C:\nope\missing.mp3",
                                 filename="missing.mp3", _col=self.col), ""
        )

    def test_no_arguments_returns_empty(self):
        self.assertEqual(resolve_identity_key(_col=self.col), "")

    def test_norm_path_collapses_mixed_separators(self):
        a = _norm_path(r"C:\x\DJ music\Library/Bollywood\t.mp3")
        b = _norm_path(r"C:\x\DJ music\Library\Bollywood\t.mp3")
        self.assertEqual(a, b)


class TestManualCorrectionComposition(unittest.TestCase):
    """The route's resolve->persist composition (routes/library.py
    update_track_bpm), exercised without Flask or the filesystem."""

    def _correct(self, col, path, name, bpm, key=None):
        ik = resolve_identity_key(final_path=path, filename=name, _col=col)
        persisted = bool(ik) and persist_audio_features(
            ik, {"bpm": bpm, "key": key, "analyzed": True, "manual": True}, _col=col
        )
        return ik, persisted

    def test_correction_reaches_the_right_document(self):
        col = FakeCollection([
            ENRICHED,
            {"identity_key": "sp:other", "final_path": r"C:\z\Other.mp3",
             "filename": "Other.mp3", "audio_features": {"bpm": 90}},
        ])
        ik, persisted = self._correct(
            col, r"C:\Users\x\DJ music\Library\Bollywood\Barbaadiyan_1.mp3",
            "Barbaadiyan_1.mp3", 132,
        )
        self.assertEqual(ik, "sp:abc123")
        self.assertTrue(persisted)
        self.assertEqual(col.get("sp:abc123")["audio_features"]["bpm"], 132)
        # the unrelated track must be untouched
        self.assertEqual(col.get("sp:other")["audio_features"]["bpm"], 90)
        # and enrichment on the corrected track survives
        self.assertEqual(
            col.get("sp:abc123")["audio_features"]["gemini_genre"], "Bollywood"
        )

    def test_unresolvable_file_reports_not_persisted(self):
        """The silent-success case: no document, so persisted must be False."""
        col = FakeCollection([ENRICHED])
        ik, persisted = self._correct(
            col, r"C:\unindexed\Brand New.mp3", "Brand New.mp3", 128
        )
        self.assertEqual(ik, "")
        self.assertFalse(persisted)
        self.assertEqual(col.update_calls, [])

    def test_route_imports_the_resolver(self):
        """Guards the wiring: the route must not go back to resolved.stem."""
        src = (
            Path(__file__).resolve().parent.parent / "routes" / "library.py"
        ).read_text(encoding="utf-8")
        self.assertIn("resolve_identity_key", src)
        self.assertNotIn("resolved.stem,", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
