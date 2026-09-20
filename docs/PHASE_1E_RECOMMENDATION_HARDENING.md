# Phase 1E — Recommendation Hardening

## Objective

Fix the four non-blocking issues Phase 1D surfaced (unknown-artist handling,
DJ-mix classification, confidence semantics, Mongo URI fallback), add
focused regression tests for each, and re-run the Phase 1D recommendation
validation to confirm no regression. Nothing else in scope: no DJ Coach, no
Training Engine, no scoring redesign, no bulk re-analysis, no physical file
changes, no downloads.

---

## Issue 1 — Unknown Artist

### Before

`_pick_next()` and `_rank_candidates()` (`backend/services/recommendation_service.py`)
blocked a candidate whenever `artist and artist in recent_artists[-_ARTIST_WINDOW:]`.
This treats the literal string as the artist's identity with no concept of
"no identity." 201 live tracks carry `artist == "Unknown"` and 42 carry an
empty string. Under the old check, any two `"Unknown"`-artist tracks read
as *the same artist*, so playing one blocked the other ~240 for the next
`_ARTIST_WINDOW` (4) steps — for no musical reason.

### Fix

Added `_is_known_artist(artist)` (recommendation-layer only, no stored data
touched): returns `False` for `None`, empty/whitespace-only strings, and
`"unknown"` in any case; `True` for anything else. Both `_pick_next()` and
`_rank_candidates()` now compute `recent_known_artists` — the recent-artist
window filtered through `_is_known_artist` — and only block a candidate
when *both* the candidate's own artist and the matching recent entry are
real artist identities:

```python
recent_known_artists = [a for a in recent_artists_window if _is_known_artist(a)]
...
if not relax and _is_known_artist(artist) and artist in recent_known_artists:
    continue
```

For a real artist name, this is byte-identical to the old behavior (a real
name is never equal to `"Unknown"`/`""`, so filtering those out of the
comparison list changes nothing for it). No Mongo write, no ID3 write —
`artist` values in the database are untouched.

### Tests

`backend/tests/test_recommendation_service.py::TestUnknownArtistHandling`
(8 tests): placeholder classification (`"Unknown"`, `"UNKNOWN"`, `""`,
`"   "`, `None`), real-artist-to-real-artist still blocked, Unknown-to-
Unknown not blocked, Unknown-to-real never matches, different real artists
stay different, 6 consecutive `"Unknown"`-artist tracks can all appear in
one recommendation list, and mixed-case placeholders (`"unknown"`, `"  "`)
don't block each other.

### After

Real-data check (`phase1e_revalidate.py`, read-only, live library):
210 tracks currently classify as unknown/placeholder-artist, 1,876 as
real-artist. An `"Unknown"`-artist seed with `recent_artists` full of
`"Unknown"` entries returned its full requested 5 recommendations with no
error. A real, repeated artist (`"Don Toliver"`, 2+ tracks in the pool) had
its second track correctly excluded from the seed's top-4 recommendations —
existing protection for real artists is unchanged.

---

## Issue 2 — DJ Mix Classification

### Before

`library_index` documents have no field distinguishing a DJ mix/set from an
individual track. All 6 known long files score and sequence exactly like
any other track.

### Detection Rule

A direct read-only query of the real `audio_features.duration_sec`
distribution across all 1,938 live documents that carry a duration found a
completely empty gap: every normal track is under **780.5s (13.0 min)**
except for exactly **6 documents**, all at **1,981.5s (33.0 min) or
longer** — nothing at all falls between those two values. This is not an
invented cutoff: any threshold placed inside that gap classifies the same
6 documents identically.

| Rank | duration_sec | minutes | title |
|---|---|---|---|
| 1 | 11,561.7 | 192.7 | HARDSTYLE 2 - Lou Nour remix |
| 2 | 3,549.8 | 59.2 | ..FEISTY - Oppidan remix |
| 3 | 3,485.9 | 58.1 | Baby again |
| 4 | 3,416.1 | 56.9 | Doctor |
| 5 | 2,099.3 | 35.0 | Selenophilia |
| 6 | 1,981.5 | 33.0 | (untitled — ARRACK-SHANTHI_DEVIL-EXPERIMENTAL_DARKPSY) |
| 7 | 780.5 | 13.0 | Shamanic Tales — first *normal* track, 1,200s below the gap |

`_DJ_MIX_DURATION_THRESHOLD_SEC = 1200.0` (20 minutes) was chosen because
it sits well inside that empty gap (420s of margin below the lowest mix,
780s above the highest normal track) — not because 20 minutes is otherwise
meaningful.

### Classification

Added `classify_track_length(duration_sec) -> "track" | "dj_mix" | "unknown"`
as a pure function in `recommendation_service.py`. No Mongo schema change —
the classification is derived dynamically from the existing
`audio_features.duration_sec` field on every call, matching the "prefer
deriving dynamically" guidance; a persistent field was not needed because
duration_sec is already stored and stable. Wired into `recommend_next()`
only (the forward-looking entry point) as `length_class` per recommendation
and `current_length_class` for the seed track. `find_similar_tracks()` and
`generate_playlist_sequence()` were left untouched — their module docstring
explicitly marks them "ported VERBATIM ... behavior must be unchanged," and
Issue 2 only requires making the distinction *available*, not wiring it
everywhere. DJ mixes are **not** excluded from the candidate pool, scoring,
or ranking — no existing recommendation behavior for them changes.

### Tests

`TestDJMixClassification` (8 tests): short/long/boundary classification
against the real threshold, missing/non-numeric/NaN/negative duration →
`"unknown"`, a `bool` duration is not treated as numeric (Python's
`isinstance(True, int) is True` trap), `recommend_next()` reports
`length_class` per candidate and `current_length_class` for the seed, a DJ
mix is still recommendable (not silently filtered), and an unresolvable
seed reports `current_length_class: "unknown"`.

### After

Real-data check found exactly the same 6 documents (by identity_key and
duration) as the manual audit above, and confirmed a mix still appears in
at least one live recommendation list — i.e., classification is available
without changing which tracks get recommended.

---

## Issue 3 — Confidence Semantics

### Before

`_confidence_factor()`/`_compute_similarity_confidence_aware()` down-weight
the camelot-compatibility term using `audio_features.confidence`, described
only as "key-detection confidence" — ambiguous about *whose* key it is
confident about.

### Analysis

Traced the full provenance in `backend/bpm_key_service.py`:

- `detect_bpm_and_key()` computes `key`, `key_root`, `key_mode`, `camelot`,
  and `confidence` together, in one Krumhansl-Schmuckler correlation run —
  `confidence` is literally the best correlation score (`best_major` /
  `best_minor`) for the key that same function call independently detected.
- Phase 1C's repair policy (`docs/PHASE_1C_REPAIR_MANIFEST.json`,
  `repair_policy.bpm_key_rule`) is: *"embedded ID3 BPM/key are metadata
  truth; librosa results were compared and recorded but never written over
  them."* Concretely, `docs/PHASE_1C_LIBRARY_INTELLIGENCE_REPAIR.md`'s
  audio-analysis step (1C-J) wrote **only** `rms_energy`,
  `spectral_centroid_mean`, `zero_crossing_rate`, `duration_sec`, and
  `confidence` — it deliberately never wrote `camelot`/`key` for tracks
  whose values already came from an ID3 tag.
- Consequence: for the majority of this library, the `camelot` stored on a
  document came from its **ID3 tag**, while `confidence` on the *same
  document* came from a **separate, independent librosa run** whose own
  camelot output was discarded. Phase 1D measured these two independent
  signals agreeing only **68.1%** of the time.
- `_compute_similarity_confidence_aware()` has no way to know, per
  document, whether `camelot` is tag-sourced or detector-sourced — it
  applies the down-weighting uniformly either way. For tracks that *did*
  come through the live pipeline (`analyze_and_tag()` → `persist_audio_features()`
  with a single `detect_bpm_and_key()` result), `camelot` and `confidence`
  *do* share the same detection run and are self-consistent.

### Decision

**No scoring change.** This is a real, bounded approximation, not a
correctness bug: `confidence` never claims to describe anything but the
audio detector's own certainty in the key it independently found, and using
it as a *proxy* for trust in the stored `camelot` is the existing,
already-documented Phase 0 design choice (see the module's ADAPTATIONS
comment). Phase 1D found no correctness violation from it (99% strong
recommendations, ~99% harmonic-compatible transitions in that run; 98.3%
and 98.5% respectively in this phase's re-validation). Building a
provenance-aware alternative would require either a new persistent
per-track field recording camelot's source, or a full re-analysis pass to
make `confidence` and `camelot` co-derived everywhere — both are
architectural changes explicitly out of scope for a hardening pass. Per
the task's own instruction ("if a requested issue cannot be safely fixed
without architectural redesign: document it and leave it unchanged"), this
is documented, not rebuilt.

What *was* done: `_confidence_factor()` and
`_compute_similarity_confidence_aware()`'s docstrings now state explicitly
that `confidence` is the detector's confidence in the key it found, not a
confidence score for the specific `camelot` value stored on the document,
and reference this report section for the full analysis. No Mongo field
was renamed; no migration was needed.

### Tests

`TestConfidenceSemantics` (2 tests): confirms the down-weighting is applied
identically regardless of camelot's (unknowable) provenance — pinning the
current, intentional behavior rather than asserting it was changed — and
confirms a high-confidence match is fully trusted (`camelot_score ==
camelot_score_base`, `confidence_factor == 1.0`), both already covered
indirectly by the existing `TestConfidenceFactor`/`TestRecommendNext`
classes but now made explicit to this semantic question.

### After

Behavior is byte-for-byte unchanged (verified by the full existing
`TestConfidenceFactor`/`TestRecommendNext` suites still passing, plus the
new documentation tests). Only comments/docstrings changed.

---

## Issue 4 — Mongo URI Safety

### Before

`backend/database.py`:

```python
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
```

evaluated at import time, with no `load_dotenv()` call of its own — it
relied on something else (`config.py`'s own `load_dotenv()`, or the calling
process) having already populated the environment. `config.py` already
calls `load_dotenv()` and already fails fast on a missing/localhost
`MONGODB_URI` inside `ProductionConfig.__init__()` — but only for the
Flask app itself, which always imports `config.py`. Any **standalone
script** that imports `database.py` directly — without importing
`config.py`/`app.py` first, and without exporting the variable itself —
got no error: `os.getenv` silently returned the localhost default, and the
script proceeded to query an empty local database as if it were the real
one. This exact trap was hit repeatedly during Phase 1B/1C/1D and worked
around each time by manually exporting `MONGODB_URI` before import — a
workaround, never a fix.

Searched the repository for the same pattern. Found:
- `backend/config.py` — already has its own, stricter, Flask-specific
  fail-fast check; not touched.
- `backend/tests/test_index_recovery.py`, `test_gemini_service.py`,
  `test_strict_matcher.py` — `os.environ.setdefault("MONGODB_URI",
  "mongodb://localhost:27017/test")` before import. This is *explicit*
  test configuration (a real, deliberate value), not the silent trap —
  compatible with the fix by design (see Tests below) and left unchanged.
- `backend/tests/test_full_pipeline.py:35-36` — an identical
  `os.getenv("MONGODB_URI", "mongodb://localhost:27017")` fallback in a
  separate, manual diagnostic script (`python test_full_pipeline.py`, not a
  `unittest.TestCase`, not collected by `unittest discover`, confirmed by
  this phase's test run: 168 tests ran, none from this file). Same footgun
  in spirit, but out of scope for this hardening pass under "do not perform
  broad refactoring" / "fix only clearly related unsafe fallback behavior"
  — noted below under Remaining Known Issues instead of touched.
- `test_maintenance_workflow.py` (repo root) hardcodes
  `MongoClient("mongodb://localhost:27017", ...)` with no env var read at
  all — an intentional local test fixture, not a silent trap. Not in scope.

### Fix

`backend/database.py`:

1. Added its own `load_dotenv()` call (matching `config.py`'s existing
   convention), so this module no longer depends on import order to see
   `backend/.env`.
2. Removed the default: `MONGODB_URI = os.getenv("MONGODB_URI")` — `None`
   if genuinely unset, never a same-shaped-but-wrong local URI.
3. `_get_db()` now raises a new `MongoConfigurationError(RuntimeError)`
   *before* constructing a `MongoClient` if `MONGODB_URI` is falsy:

```python
if not MONGODB_URI:
    raise MongoConfigurationError(
        "MONGODB_URI is not set. Set it in backend/.env or the "
        "process environment before connecting to MongoDB — this "
        "module no longer falls back to mongodb://localhost:27017 "
        "silently. If a local MongoDB is genuinely what you want, "
        "set MONGODB_URI=mongodb://localhost:27017 explicitly."
    )
```

Explicitly configuring `MONGODB_URI=mongodb://localhost:27017` still works
— only the *silent, unrequested* fallback is removed. No credentials are
hardcoded or logged; the existing connection-success log line
(`f"...Connected to MongoDB: {MONGODB_URI}/{MONGODB_DB}"`) is unchanged and
only reached once a URI is confirmed present.

### Tests

New file `backend/tests/test_database_config.py` (8 tests), each reloading
`database.py` under a controlled environment (`importlib.reload` +
`unittest.mock.patch.dict`) with `MongoClient` always mocked — no test ever
opens a real connection:

- explicit environment variable resolves normally
- pre-set `MONGODB_URI` before import (the existing convention in
  `test_index_recovery.py` etc.) still works unchanged
- absent from the process environment but present in `backend/.env` still
  resolves (asserted as "truthy and not localhost," never as the literal
  value, so no credential appears in test output)
- missing URI + no `.env` available → raises `MongoConfigurationError`,
  `MongoClient` is never called
- empty-string URI → treated as missing, same guarantee
- the error message names `MONGODB_URI` (a configuration error, not a
  disguised connection failure)
- explicit `mongodb://localhost:27017` is still honored and reaches
  `MongoClient`
- `database.py` still compiles

One implementation pitfall worth recording: `importlib.reload()`
re-executes `database.py`'s own `from dotenv import load_dotenv` line,
which silently **rebinds** `database.load_dotenv` back to the real
function — so `patch("database.load_dotenv")` made *before* a reload does
not survive it. The "no `.env` available" tests instead patch
`dotenv.load_dotenv` (the source function), which *does* survive because
the re-executed import reads it fresh during the reload.

### After

All 8 new tests pass, plus the pre-existing `test_database_mongo_timeout.py`
(socket-timeout regression test, unaffected — it never touches
`MONGODB_URI` itself) still passes.

---

## Regression Results

Before any Phase 1E change: **142/142** tests passing
(`python -m unittest discover -s tests`, run from `backend/`).

After all four fixes: **168/168** tests passing — the same 142 plus 26 new
(18 in `test_recommendation_service.py` across the three new test classes,
8 in the new `test_database_config.py`). Zero pre-existing tests were
modified in a way that weakens an assertion; the only edits to existing
test code were adding a new optional `duration_sec` parameter to the shared
`track()` fixture builder (default `None`, so every existing call is
unaffected) and importing the newly-tested names.

`python -m py_compile` across every `.py` file under `backend/` — clean,
matching the project's existing CI gate.

---

## Phase 1D Before vs After

Re-validated with a fresh, read-only script
(`phase1e_revalidate.py`) against the live library — a raw, read-only
`MongoClient` fetches `library_index` documents once; every
`recommendation_service` call after that uses its existing `docs=`/`_docs=`
injection parameters exclusively, so `database._get_db()`/`_ensure_indexes()`
(a write) is never invoked. Confirmed target before running: non-localhost
Atlas URI, `spotify_downloader` database, `library_index` collection.

| Metric | Phase 1D | Phase 1E re-run | Verdict |
|---|---|---|---|
| Live docs | 1,955 | 2,125 (library grew via normal use between sessions) | n/a |
| Candidate pool scoreable | 1,919 (98.2%) | 2,086 (98.2%) | same rate, no regression |
| Recommendations sampled | 520 | 600 | — |
| Mean score | 0.8376 | 0.847 | consistent |
| Score range | 0.5318–0.9872 | 0.5504–0.9947 | consistent |
| Strong recommendations | 99.0% | 98.3% | consistent |
| Harmonic-compatible | 99.6% | 98.5% | consistent |
| +40 BPM rejected | yes | yes | unchanged |
| Same-BPM included | yes | yes | unchanged |
| All 24 Camelot values valid | yes | yes | unchanged |
| Energy term swings 1.0→0.0 on far energy | yes | yes | unchanged |
| Spectral term swings 1.0→0.0 on far spectral | yes | yes | unchanged |
| Confidence down-weights camelot (0.9→0.1 conf) | 1.0→0.2 | 1.0→0.2 | unchanged |
| Sequences 5/10/20 | 0 dup, 0 weak, 100% harmonic | 0 dup, 0 weak, 100% harmonic | unchanged |
| Determinism | recommendations identical, only `generated_at` differs | same | unchanged |
| Edge cases | 14 passed, 0 crashes | unknown-seed / invalid-flow / zero-top_n all correct, 0 crashes | unchanged |
| `recommend_next` mean | 22.07 ms | 19.38 ms | no regression |
| `generate_playlist_sequence(20)` | 190.9 ms | 130.6 ms | no regression |
| `find_similar_tracks` | 24.61 ms | 16.64 ms | no regression |

New, hardening-specific checks (no Phase 1D equivalent):

- 210 tracks currently classify as unknown/placeholder-artist (up slightly
  from Phase 1D's 201+42=243 count basis, consistent with library growth);
  an unknown-artist seed with an all-placeholder `recent_artists` window
  returned its full requested recommendation count with no error.
- A real, repeated artist ("Don Toliver") still had its second track
  correctly excluded from the top-4 recommendations.
- All 6 known DJ mixes were found by `classify_track_length()` at the exact
  same identity_keys and durations as the original manual audit; a mix
  still appears in at least one live recommendation list (behavior
  preserved, not filtered).

**No regression found.**

---

## Remaining Known Issues

Unchanged from Phase 1D except where superseded above:

1. ~~201 "Unknown"-artist tracks distort `_ARTIST_WINDOW`~~ — **fixed**
   (Issue 1).
2. ~~6 DJ mixes have no classification~~ — **classification added**
   (Issue 2); the underlying files are still full mixes and Phase 2 should
   decide how to treat `length_class == "dj_mix"` in training/sequencing.
3. Confidence/Camelot provenance mismatch (68.1% agreement) — **documented**
   (Issue 3), not resolved at the data level; would require either a new
   persistent provenance field or a full re-analysis pass, both out of
   scope here.
4. ~~Mongo URI silent localhost fallback~~ — **fixed** (Issue 4) for
   `backend/database.py`, the module every route and script actually uses
   for `library_index`.
5. `backend/tests/test_full_pipeline.py:35-36` has the same
   `os.getenv("MONGODB_URI", "mongodb://localhost:27017")` pattern, in a
   manual, non-automated diagnostic script — left unchanged (see Issue 4
   analysis above).
6. 37 empty-title tracks, 6 recommendation-scoring issues around genre
   (scoring has no genre term by design — a Bollywood → Dubstep transition
   can rank highly on harmonic/tempo grounds alone), and the 10 Spotify-ID
   collision groups noted in Phase 1D remain untouched — none were in this
   phase's scope.

---

## Phase 2 Readiness

**READY FOR PHASE 2**

All four requested issues were fixed or, where a full fix would require
architectural redesign (Issue 3), explicitly documented and left
unchanged per instruction. No regressions: 168/168 tests pass (142
pre-existing + 26 new), `py_compile` is clean, and a fresh read-only
re-validation against the live library shows every Phase 1D metric held or
improved. `recommend_next()` now additionally reports `length_class`/
`current_length_class` for Phase 2's future use, with no change to
existing scoring, ranking, or filtering behavior.

---

## Final Safety Verification

- Source files modified: **2** (`backend/database.py`,
  `backend/services/recommendation_service.py`)
- Tests added/modified: **2 files** — `backend/tests/test_recommendation_service.py`
  modified (+212 lines: 3 new test classes, 24 new tests, one additive
  optional parameter on the shared `track()` fixture), `backend/tests/test_database_config.py`
  created (8 new tests)
- Documentation files created: **1** (this file)
- Mongo documents modified: **0**
- Mongo documents created: **0**
- Mongo documents deleted: **0**
- Physical files modified: **0**
- Physical files moved: **0**
- Physical files renamed: **0**
- Physical files deleted: **0**
- Downloads: **0**
- Database migrations: **0**

Mongo state, confirmed via read-only queries against the live, non-localhost
Atlas cluster: **2,296 total documents / 2,125 live / 171 soft-deleted** —
consistent with normal library growth between sessions (Phase 1D recorded
2,126 total / 1,955 live); no write of any kind was issued by this phase's
work. No unexpected write occurred.

**STOP. Phase 2 (DJ Training Engine) not started, per instruction.**
