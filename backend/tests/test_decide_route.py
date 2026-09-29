"""
decide_route() — the single ingest routing decision (services/auto_downloader.py).

Order under test: your lists (artist memory, router overrides/knowledge base) -> verified evidence vote ->
Groq listening -> Electronic catch-all. All collaborators are injected: no network, no Mongo, no files.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.auto_downloader import decide_route  # noqa: E402


def _never(*_a, **_k):
    raise AssertionError("this step must not run")


def _run(**over):
    kw = dict(artist="A", title="T", artist_id="id", sp=None, filepath="x.mp3",
              memory_lookup=lambda a: {}, router=lambda **k: ("NeedsReview/A", 0.2, "uncategorized"),
              evidence=lambda *a: ("", ""), ai=lambda *a: ("", ""))
    kw.update(over)
    return decide_route(**kw)


class TestDecideRouteOrder(unittest.TestCase):
    def test_artist_memory_wins_over_everything(self):
        r = _run(memory_lookup=lambda a: {"genre": "Bollywood", "confidence": 0.9},
                 router=_never, evidence=_never, ai=_never)
        self.assertEqual((r.path, r.source, r.catchall), ("Library/Bollywood", "artist_memory", False))

    def test_weak_memory_is_ignored(self):
        r = _run(memory_lookup=lambda a: {"genre": "Techno", "confidence": 0.3},
                 router=lambda **k: ("Library/Punjabi", 0.95, "artist_override"), evidence=_never, ai=_never)
        self.assertEqual(r.path, "Library/Punjabi")

    def test_your_override_list_beats_the_evidence_vote(self):
        # The regression: a MusicBrainz "techno" tag used to outrank the user's own artist list.
        r = _run(router=lambda **k: ("Library/Bollywood", 1.0, "artist_override"),
                 evidence=lambda *a: ("Library/Techno", "evidence:musicbrainz"), ai=_never)
        self.assertEqual((r.path, r.source, r.tag), ("Library/Bollywood", "artist_override", ""))

    def test_low_router_confidence_falls_to_evidence(self):
        r = _run(evidence=lambda *a: ("Library/House", "evidence:lastfm_track+itunes"), ai=_never)
        self.assertEqual((r.path, r.source, r.tag), ("Library/House", "evidence", "evidence:lastfm_track+itunes"))
        self.assertFalse(r.catchall)

    def test_no_evidence_falls_to_groq(self):
        r = _run(ai=lambda *a: ("Library/Punjabi", "ai:groq"))
        self.assertEqual((r.path, r.source, r.tag), ("Library/Punjabi", "ai", "ai:groq"))

    def test_nothing_knows_the_song_goes_to_catchall(self):
        r = _run()
        self.assertEqual((r.path, r.source, r.catchall, r.tag), ("Library/Electronic", "catchall", True, ""))

    def test_router_crash_is_contained_and_continues(self):
        def boom(**k):
            raise RuntimeError("mongo down")
        r = _run(router=boom, evidence=lambda *a: ("Library/Trance", "evidence:x"))
        self.assertEqual(r.path, "Library/Trance")

    def test_memory_crash_is_contained_and_continues(self):
        def boom(a):
            raise RuntimeError("mongo down")
        r = _run(memory_lookup=boom, router=lambda **k: ("Library/House", 0.7, "spotify_genre"), evidence=_never)
        self.assertEqual(r.path, "Library/House")


if __name__ == "__main__":
    unittest.main()
