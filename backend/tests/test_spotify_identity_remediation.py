"""
Phase 1G — regression suite for the Spotify-ID mistagging fix
(docs/PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md / PHASE_1G dry-run).

Scope: MATCHING/PARSING/COMPATIBILITY LOGIC ONLY. No real network calls,
no real Mongo, no real files — every candidate/doc below is a hand-built
dict, matching the convention already used in tests/test_strict_matcher.py.

Covers, per the Phase 1G task spec's required-tests list (A-I):
  A. pass6 low-similarity rejection
  B. pass6 strong-match acceptance
  C. filename "Artist - Song.mp3"
  D. filename "Song - Radio Edit.mp3"
  E. filename "Song - Hamdi Remix.mp3"
  F. ambiguous filename -> uncertain result
  G. sync_tags_to_mongo conflict -> BLOCK
  H. sync_tags_to_mongo valid update -> allow
  I. Spotify-ID collision regression (real Phase 1F fixture)

Run:
    python -m unittest tests.test_spotify_identity_remediation -v
    (from the backend/ directory)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backfill_ai
from services.legacy_identification_service import (
    _parse_filename,
    CONF_ACCEPT_WARN,
)
from sync_tags_to_mongo import _identity_compatible


def sp_candidate(id_, title, artist, album="", duration_ms=0, popularity=50):
    """Build an already-extracted Spotify candidate dict (matches
    legacy_identification_service._extract_candidate()'s output shape)."""
    return {
        "id": id_, "title": title, "artist": artist, "album": album,
        "duration_ms": duration_ms, "art_url": "", "popularity": popularity,
    }


# ═══════════════════════════════════════════════════════════════════
# A + B: backfill_ai._select_verified_spotify_match() confidence gate
# ═══════════════════════════════════════════════════════════════════

class TestPass6ConfidenceGate(unittest.TestCase):

    def test_A_low_similarity_rejected(self):
        """Query differs materially from every candidate -> reject, no ID written."""
        candidates = [
            sp_candidate("WRONGID1", "Some Completely Different Song", "Unrelated Artist XYZ",
                         duration_ms=95000),
            sp_candidate("WRONGID2", "Another Unrelated Track", "Also Not It", duration_ms=310000),
        ]
        spotify_id, confidence, reason = backfill_ai._select_verified_spotify_match(
            "Doctor", "Sammy Virji", 152000, candidates,
        )
        self.assertEqual(spotify_id, "")
        self.assertLess(confidence, CONF_ACCEPT_WARN)

    def test_A_no_candidates_rejected(self):
        spotify_id, confidence, reason = backfill_ai._select_verified_spotify_match(
            "Doctor", "Sammy Virji", 152000, [],
        )
        self.assertEqual(spotify_id, "")
        self.assertEqual(confidence, 0.0)

    def test_B_strong_match_accepted(self):
        """Correct title + artist + close duration -> ID is accepted."""
        candidates = [
            sp_candidate("WRONGID", "Totally Different Song", "Nobody Relevant", duration_ms=200000),
            sp_candidate("GOODID", "Doctor", "Sammy Virji", duration_ms=152500, popularity=70),
        ]
        spotify_id, confidence, reason = backfill_ai._select_verified_spotify_match(
            "Doctor", "Sammy Virji", 152000, candidates,
        )
        self.assertEqual(spotify_id, "GOODID")
        self.assertGreaterEqual(confidence, CONF_ACCEPT_WARN)


# ═══════════════════════════════════════════════════════════════════
# C-F: legacy_identification_service._parse_filename() convention fix
# ═══════════════════════════════════════════════════════════════════

class TestParseFilenameConvention(unittest.TestCase):

    def test_C_artist_song_convention_unchanged(self):
        parsed = _parse_filename(Path("C:/Library/Pop/Calvin Harris - Summer.mp3"))
        self.assertTrue(parsed.confident)
        self.assertEqual(parsed.artist, "Calvin Harris")
        self.assertEqual(parsed.title, "Summer")
        self.assertEqual(parsed.edition, "")

    def test_D_song_radio_edit_convention(self):
        parsed = _parse_filename(Path("C:/Library/House/Calling Out Your Name - Radio Edit.mp3"))
        self.assertTrue(parsed.confident)
        self.assertEqual(parsed.title, "Calling Out Your Name")
        self.assertEqual(parsed.edition, "Radio Edit")
        # The real song name must NOT be misassigned as the artist.
        self.assertNotEqual(parsed.artist, "Radio Edit")

    def test_E_song_named_remix_convention(self):
        parsed = _parse_filename(Path("C:/Library/House/OK OK - Hamdi remix.mp3"))
        self.assertTrue(parsed.confident)
        self.assertEqual(parsed.title, "OK OK")
        self.assertEqual(parsed.edition, "Hamdi remix")

    def test_E_mixed_suffix_convention(self):
        """Real Phase 1F case: 'Mixed' isn't in the literal remix token set
        ('mix' is, 'mixed' isn't) but must still be recognized as an edition
        credit, not a song title fragment or artist name."""
        parsed = _parse_filename(Path("C:/Library/Pop/Alone - Mixed.mp3"))
        self.assertTrue(parsed.confident)
        self.assertEqual(parsed.title, "Alone")
        self.assertEqual(parsed.edition, "Mixed")

    def test_F_ambiguous_filename_returns_uncertain(self):
        """Both sides of the split read as edition/version phrases -- the
        parser cannot safely tell which side is the real title."""
        parsed = _parse_filename(Path("C:/Library/House/Extended - Radio Edit.mp3"))
        self.assertFalse(parsed.confident)

    def test_no_separator_in_a_genre_folder_has_no_artist(self):
        # This test used to assert artist == "Latin" ("parent-folder fallback, unchanged behavior").
        # That WAS the bug behind hundreds of files tagged artist="Indian"/"Unknown"/"Electronic":
        # a genre crate's name says nothing about who made the track, and the Spotify search that
        # followed asked for `artist:Latin`. See tests/test_legacy_artist_from_folder.py.
        parsed = _parse_filename(Path("C:/Library/Latin/Beba.mp3"))
        self.assertTrue(parsed.confident)
        self.assertEqual(parsed.title, "Beba")
        self.assertEqual(parsed.artist, "")

    def test_no_separator_in_a_real_artist_folder_still_uses_the_folder(self):
        parsed = _parse_filename(Path("C:/Library/Nucleya/Beba.mp3"))
        self.assertEqual((parsed.artist, parsed.title, parsed.confident), ("Nucleya", "Beba", True))


# ═══════════════════════════════════════════════════════════════════
# G + H: sync_tags_to_mongo._identity_compatible()
# ═══════════════════════════════════════════════════════════════════

class TestSyncTagsIdentityCompatibility(unittest.TestCase):

    def test_G_material_disagreement_blocked(self):
        compatible, reason = _identity_compatible(
            existing_title="Beba", existing_artist="Farruko",
            candidate_title="Pepas", candidate_artist="Farruko",
        )
        self.assertFalse(compatible)

    def test_H_matching_metadata_allowed(self):
        compatible, reason = _identity_compatible(
            existing_title="Dis Badman", existing_artist="Sammy Virji",
            candidate_title="Dis Badman", candidate_artist="Sammy Virji",
        )
        self.assertTrue(compatible)

    def test_no_prior_identity_allowed(self):
        """Nothing on record yet to conflict with -> let the tag through."""
        compatible, reason = _identity_compatible(
            existing_title="", existing_artist="",
            candidate_title="Anything", candidate_artist="Anyone",
        )
        self.assertTrue(compatible)

    def test_close_title_variant_allowed(self):
        compatible, reason = _identity_compatible(
            existing_title="Chaleya (From \"Jawan\")", existing_artist="Anirudh Ravichander",
            candidate_title="Chaleya", candidate_artist="Anirudh Ravichander",
        )
        self.assertTrue(compatible)


# ═══════════════════════════════════════════════════════════════════
# I: Spotify-ID collision regression (real Phase 1F fixture)
# ═══════════════════════════════════════════════════════════════════

class TestCollisionRegression(unittest.TestCase):
    """Real Phase 1F case: 'Goodums - Sammy Virji Remix.mp3' (Unknown T) and
    'on & on - Sammy Virji Remix.mp3' both had TIT2 corrupted to the generic
    edition credit 'Sammy Virji Remix' by the old _parse_filename swap bug,
    and the old unguarded pass6_backfill_spotify_id() then matched both to
    the same wrong spotify_id 0SLedTMdKihqLsR6CGPAfD with no gate at all.
    """

    def test_old_corrupted_query_now_rejected_by_gate(self):
        # Even feeding the OLD corrupted title verbatim (as the pre-fix file
        # on disk still has it until Stage B repairs it), a weak/generic
        # candidate must not clear the stricter auto-write bar.
        candidates = [
            sp_candidate("0SLedTMdKihqLsR6CGPAfD", "Sammy Virji Remix", "Sammy Virji",
                         duration_ms=200000, popularity=55),
        ]
        spotify_id, confidence, reason = backfill_ai._select_verified_spotify_match(
            "Sammy Virji Remix", "Unknown T", 182730, candidates,
        )
        self.assertEqual(spotify_id, "")
        self.assertLess(confidence, CONF_ACCEPT_WARN)

    def test_fixed_parse_no_longer_corrupts_title(self):
        parsed = _parse_filename(Path("C:/Library/Grime/Goodums - Sammy Virji Remix.mp3"))
        self.assertTrue(parsed.confident)
        self.assertEqual(parsed.title, "Goodums")
        self.assertNotEqual(parsed.title, "Sammy Virji Remix")

    def test_fixed_parse_plus_gate_accepts_genuine_match(self):
        parsed = _parse_filename(Path("C:/Library/Grime/Goodums - Sammy Virji Remix.mp3"))
        candidates = [
            sp_candidate("0SLedTMdKihqLsR6CGPAfD", "Sammy Virji Remix", "Sammy Virji", duration_ms=200000),
            sp_candidate("REALGOODUMSID", "Goodums", "Unknown T", duration_ms=182500, popularity=60),
        ]
        spotify_id, confidence, reason = backfill_ai._select_verified_spotify_match(
            parsed.title, "Unknown T", 182730, candidates,
        )
        self.assertEqual(spotify_id, "REALGOODUMSID")


if __name__ == "__main__":
    unittest.main()
