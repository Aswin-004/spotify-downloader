# Phase 2 — DJ Training Engine

**Date:** 2026-09-15
**Type:** New deterministic service + one read-only API route. Zero MongoDB writes, zero physical file changes, zero downloads.

## Objective

Transform the existing analyzed library into a daily DJ practice session of
exactly 5 exercises (EASY_HARMONIC, BPM_TRANSITION, ENERGY_TRANSITION,
GENRE_CROSSOVER, CHALLENGE), reusing the Phase 0/1 recommendation engine as
the *only* scoring mechanism. No LLM, no ML, no external API, no VirtualDJ
integration, no learned skill data — those are explicitly out of scope
(see "Phase 2 does NOT include" below).

## Architecture

```
library_index (Mongo)
    -> recommendation_service.get_candidate_pool()      [existing, unchanged]
    -> training_service._is_training_safe()             [NEW — additive safety filter]
    -> recommendation_service._compute_similarity_confidence_aware()  [existing, unchanged — sole scorer]
    -> training_service exercise builders (5)            [NEW — selection rules per type]
    -> training_service.generate_daily_session()         [NEW — orchestration + contract]
    -> GET /api/dj/training/today                        [NEW — thin Flask route]
```

`training_service.py` never reimplements camelot/BPM/energy/spectral
scoring. Every pairwise comparison goes through
`recommendation_service._compute_similarity_confidence_aware()` — the same
function `recommend_next()` uses. This module only adds: an additional
candidate-safety filter, exercise-type selection rules, a transparent
difficulty score, deterministic seeding, and human-readable instructions.

## Files

| File | Status |
|---|---|
| `backend/services/training_service.py` | Created |
| `backend/routes/training.py` | Created |
| `backend/tests/test_training_service.py` | Created |
| `backend/routes/__init__.py` | Modified — export `training_bp` |
| `backend/app.py` | Modified — import + `register_blueprint(training_bp)` |

No other file was touched. `recommendation_service.py`, `bpm_key_service.py`,
the downloader/organizer/matcher/migrator/reconcile scripts, genre taxonomy,
and all physical music files are unchanged.

## Exercise types and selection rules

All five share one candidate pool (`recommendation_service.get_candidate_pool()`
further filtered by `_is_training_safe()`) and one deterministic RNG. They
are built in this fixed order, each excluding every identity_key already
used as a source or target by an earlier exercise in the same session
(Section 4/7's uniqueness requirement):

| Type | Difficulty | Hard constraints | Ranked by |
|---|---|---|---|
| `EASY_HARMONIC` | EASY (fixed) | `bpm_delta <= 5`, harmonic relation `same_key` or `adjacent_key` | similarity_score |
| `BPM_TRANSITION` | MEDIUM (fixed) | `8 <= bpm_delta <= 15`, harmonic relation not `incompatible` | similarity_score |
| `ENERGY_TRANSITION` | MEDIUM (fixed) | `abs(energy_delta) >= 0.05`, harmonic relation not `incompatible`, `bpm_delta <= 25` | similarity_score |
| `GENRE_CROSSOVER` | MEDIUM or HARD (computed) | different genre family; if harmonic relation is `incompatible`, `bpm_delta` must still be `<= 15` (the engine's own hard-reject limit) | similarity_score |
| `CHALLENGE` | HARD (fixed) | `bpm_delta <= 25` (never exceeds the engine's own soft-reject ceiling), `similarity_score >= 0.15` (never a nonsensical pairing) | **difficulty_score** (highest wins) |

`harmonic_relation` is derived from the existing
`camelot_score_base` (1.0/0.75/0.0) the engine already computes:
`same_key` (1.0), `adjacent_key` (0.75), `incompatible` (0.0). "Prefer
high key-confidence" for EASY_HARMONIC is satisfied implicitly: a
low-confidence match is already down-weighted by
`_compute_similarity_confidence_aware()`'s own camelot term, so it ranks
lower automatically — no separate confidence filter was hand-rolled on top
of the engine's own mechanism.

**Selection mechanism (`_find_best_pair`):** the candidate pool is shuffled
once per builder call using the session's seeded RNG. For each shuffled
candidate, in order, the whole pool is scanned for its best-scoring valid
target; the first source with at least one valid target wins, paired with
its single best match. This is O(n) per exercise in the common case
(mirrors how `recommend_next()` itself works — one "current" track scored
against the whole pool), not an O(n²) full pairwise search.

**Artist variety (not a hard rule):** each builder runs two passes — first
requiring that neither side's artist is a *real* artist already used
elsewhere in the session, then (only if that finds nothing) a relaxed pass
without that constraint. This mirrors `recommend_next()`'s own strict/relax
BPM-jump fallback, applied here to artist variety. Placeholder artists
("Unknown" in any case, empty, whitespace) never participate in this check
at all — reuses `recommendation_service._is_known_artist()` unchanged, so
an all-"Unknown" pool behaves exactly as if the check didn't exist (Phase
1E semantics preserved, tested explicitly).

## Difficulty model

Centralized, explainable, deterministic — `services/training_service.py::_difficulty_score()`:

```
score = 0.35 * bpm_component      # min(bpm_delta / 25, 1.0)
      + 0.30 * harmonic_component # 1.0 - camelot_score_base
      + 0.20 * energy_component   # min(|energy_delta| / 0.15, 1.0)
      + 0.10 * genre_component    # 1.0 if different genre family else 0.0
      + 0.05 * confidence_component  # 1.0 - confidence_factor
```

Every exercise exposes `metrics.difficulty_score` regardless of type, so
CHALLENGE (which explicitly maximizes it) is provably harder than the
other four — this is asserted directly in tests, not just claimed.
GENRE_CROSSOVER's EASY/MEDIUM/HARD-eligible label is the only dynamically
computed one: `HARD` at `difficulty_score >= 0.35`, `MEDIUM` below it. The
other four types' difficulty labels are fixed by type per the spec (EASY,
MEDIUM, MEDIUM, HARD) — `difficulty_score` explains *why*, it doesn't
override the type's assigned label except for GENRE_CROSSOVER.

Genre is never a scoring term inside `recommendation_service.py` — it
remains a `_difficulty_score` and selection-filter input only, exactly as
required.

## Genre-family normalization

`_genre_family(genre_folder)` — the only new genre-handling logic, entirely
inside `training_service.py`:

```python
def _genre_family(genre_folder):
    if not genre_folder or not isinstance(genre_folder, str):
        return ""
    return genre_folder.strip().split("/")[-1].strip().lower()
```

`library_index.genre_folder` values are the top-level library folder name,
sometimes prefixed `"Library/"` (e.g. `"Library/Bollywood"`, `"House"`,
`"Same Day Cleaning"`). The leaf segment already *is* this project's genre
grouping — it's what `genre_router.py` routes tracks into — so this just
takes that leaf, case-folded, for equality comparisons in
`GENRE_CROSSOVER`/`CHALLENGE`. The original, unmodified `genre_folder` is
always what's returned in `source_track.genre`/`target_track.genre` — this
helper is comparison-only and never feeds recommendation scoring.

## Deterministic seed strategy

```python
def _daily_seed(date_str, pool):
    identity_keys = sorted(d["identity_key"] for d in pool if d.get("identity_key"))
    fingerprint = sha256("|".join(identity_keys)).hexdigest()
    return int(sha256(f"{date_str}:{fingerprint}").hexdigest()[:16], 16)
```

A single `random.Random(seed)` instance is created once per
`generate_daily_session()` call and threaded through all 5 builders in a
fixed order. No call in this module ever uses the global `random` module or
an unseeded `Random()`. `generated_at` (`datetime.now(timezone.utc)`) is
computed separately, purely for display, and never enters the seed —
verified by a test that sleeps between two calls and asserts the exercises
are still identical. If the library changes (tracks added/removed), the
fingerprint changes and the selection can change — this is the intended
Section 6 "personalization follows the actual library" behavior, not a
determinism violation.

## Candidate filtering

`get_candidate_pool()` (existing, unchanged) already excludes `missing: True`
and requires `bpm`+`camelot` present. `_is_training_safe()` adds the checks
Section 7 explicitly lists that the existing pool does not itself apply:

- BPM must be numeric, non-NaN/inf, and within `40–250` (matches
  `bpm_key_service.detect_bpm_and_key()`'s own sanity bounds)
- Camelot must match `^(1[0-2]|[1-9])[AB]$` (rejects malformed values like `"ZZ"` or `"13A"`)
- `rms_energy`/`spectral_centroid_mean`/`confidence`, when present, must not be NaN/inf

No filename-stem identity is ever used — every exercise field is populated
from `identity_key`/`title`/`artist`/`audio_features`, matching the
project's existing identity convention.

## Exercise / session data contracts

Exactly the shape specified, with additive fields (`difficulty_score`,
`camelot_score_base`-derived `harmonic_relation`, `energy_direction` for
ENERGY_TRANSITION) layered on top for transparency:

```
exercise = {
  exercise_id, type, title, difficulty,
  source_track: { identity_key, title, artist, bpm, key, camelot,
                   key_confidence, energy, genre, length_class },
  target_track: { ...same shape... },
  metrics: { bpm_delta, energy_delta, harmonic_relation, similarity_score,
             difficulty_score, [energy_direction] },
  instructions: [...], success_criteria: [...], reason: "...",
}

session = {
  session_id, date, title, difficulty, exercise_count, exercises,
  library_stats: { docs_considered, candidate_pool, training_pool },
  generated_at, error,
}
```

No track metadata is ever fabricated — every field comes directly from the
matched `library_index` document or is `None`/`""` when genuinely absent.

## API contract

```
GET /api/dj/training/today
GET /api/dj/training/today?date=YYYY-MM-DD    (optional override, for testing/determinism verification)
```

Registered exactly like every other blueprint (`routes/training.py` ->
`training_bp`, exported from `routes/__init__.py`, registered in `app.py`
alongside the other six). The route is a thin wrapper — all logic lives in
`training_service.generate_daily_session()`, matching this project's
existing "routes call straight into services/*.py" convention (see
`routes/analytics.py`/`routes/genre.py`) rather than introducing a
controller/repository layering this codebase doesn't otherwise use.

Response: `200` with the full session JSON, including a non-`null` `error`
string when fewer than 5 exercises could be built (never a `4xx/5xx` for
that expected condition — matches `recommend_next()`'s own convention of
reporting `error` inside a normal `200` payload). A genuine exception
(e.g., a Mongo connectivity failure) returns `500` with `{"error": "..."}`,
matching every other blueprint's existing try/except pattern.

## Failure behavior

`generate_daily_session()` never raises for an insufficient library and
never invents a track. If the training pool has fewer than 2 usable
tracks, it returns immediately with `exercise_count: 0` and a clear
`error`. If some (but not all) of the 5 types can't be built — because
uniqueness constraints exhausted the remaining valid candidates — it
returns the exercises that *could* be built, `exercise_count < 5`, and an
`error` naming exactly which type(s) failed and why. This was verified
directly (`test_insufficient_pool_fails_gracefully`) and observed
overlapping in a small hand-built all-placeholder-artist pool
(`test_unknown_artists_do_not_create_false_conflicts`, where
ENERGY_TRANSITION legitimately can't be built because all 5 tracks share
identical energy — the session still returns the 4 it could build).

## Test coverage

`backend/tests/test_training_service.py` — 43 tests, all passing, covering
every item in the Phase 2 spec's list of 20 plus additional unit-level
coverage of the new helpers (`_genre_family`, `_pair_metrics`,
`_daily_seed`, `_is_training_safe`, each builder in isolation):

| # | Requirement | Test(s) |
|---|---|---|
| 1 | Exactly 5 exercises | `test_exactly_five_exercises` |
| 2 | All 5 types present | `test_all_five_types_present` |
| 3 | Same date+pool -> identical selection | `test_deterministic_same_date_same_pool` |
| 4 | Repeated calls deterministic | `test_repeated_calls_deterministic_generated_at_excluded` |
| 5 | `generated_at` doesn't affect selection | `test_generated_at_does_not_affect_selection` (sleeps between calls) |
| 6 | No invalid BPM | `test_no_invalid_bpm_in_session` |
| 7 | No invalid Camelot | `test_no_invalid_camelot_in_session` |
| 8 | No damaged tracks | `test_no_damaged_tracks_in_session` |
| 9 | EASY_HARMONIC constraints | `test_easy_harmonic_satisfies_constraints` |
| 10 | BPM_TRANSITION difficulty | `test_bpm_transition_has_intended_difficulty` |
| 11 | ENERGY_TRANSITION delta | `test_energy_transition_has_meaningful_delta` |
| 12 | GENRE_CROSSOVER different family | `test_genre_crossover_different_genre_family` |
| 13 | CHALLENGE harder than others | `test_challenge_harder_than_easy_harmonic` (asserts `difficulty_score` strictly greater) |
| 14 | Unknown artists, no false conflicts | `test_unknown_artists_do_not_create_false_conflicts`, `test_unknown_artist_pool_still_builds` |
| 15 | DJ mix `length_class` correct | `test_dj_mix_length_class_correct_when_present` |
| 16 | No duplicate source/target pairs | `test_no_duplicate_source_target_pairs_across_session` |
| 17 | Insufficient pool fails gracefully | `test_insufficient_pool_fails_gracefully` |
| 18 | Missing optional metadata doesn't crash | `test_missing_optional_metadata_does_not_crash` |
| 19 | Score/reason metadata present | `test_score_and_reason_metadata_present` |
| 20 | No MongoDB writes | `test_no_mongodb_writes` (patches `pymongo.collection.Collection.update_one/insert_one/delete_one/update_many`, asserts zero calls) |

Full existing suite: **211/211 pass** (168 pre-Phase-2 + 43 new — zero
existing tests modified or weakened). `py_compile` clean across every
backend `.py` file, including the two modified route-wiring files.

**API-level verification (not an automated test, matching this project's
existing convention that no test imports `app.py` or exercises a Flask
route):** the real `GET /api/dj/training/today` route was exercised
end-to-end via a minimal Flask app + test client, with `database.py`'s
`get_library_index_collection` monkey-patched to a read-only shim serving
already-fetched real documents (the same read-only-shim pattern used for
the official validator in Phase 1D/1E) — confirmed `200`, exactly 5
exercises, all 5 types, and identical responses across two calls at the
same date. The full `app.py` was not imported (heavy, unrelated side
effects: SocketIO, Redis rate limiter, Spotify/downloader services) —
consistent with why no existing test in this codebase does either.

## Real library result (2026-09-15, read-only)

2,171 live docs fetched via a direct, read-only `MongoClient` (never
`database._get_db()`); `docs=` injection used throughout — zero writes.

- `candidate_pool`: 2,096 · `training_pool`: 2,096 (all candidates passed the additional safety filter)
- `exercise_count`: 5, `error`: `None`
- Two calls at the same date produced byte-identical `exercises`; a different date produced a different selection
- Latency: ~0.1–0.14s per call

The 5 generated exercises:

1. **EASY_HARMONIC** (EASY) — *Kadi Te Has Bol* (Atif Aslam, 107 BPM, 7A) → *Kaun Tujhe (Armaan Malik Version)* (108 BPM, 7A). Same key, 1.0 BPM apart, difficulty_score 0.1304.
2. **BPM_TRANSITION** (MEDIUM) — *Namaqua* (Daniel Rateuke, 123 BPM, 6A) → *Laal Pari* (Yo Yo Honey Singh, 115 BPM, 6A). 8.0 BPM gap, same key, difficulty_score 0.2308.
3. **ENERGY_TRANSITION** (MEDIUM) — *Dis Badman* (Sammy Virji, energy 0.2227) → *Baller* (Shubh, energy 0.1552). RELEASE, difficulty_score 0.2149.
4. **GENRE_CROSSOVER** (MEDIUM) — *UNDRGRND DRIFT* (Library/Electronic, 160 BPM, 4A) → *Fallin'* (Library/Drum & Bass, 160 BPM, 4B). Adjacent key, 0 BPM apart, difficulty_score 0.2566.
5. **CHALLENGE** (HARD) — *Widdershins* (Ajja, 74 BPM, 3B) → *Pavitra Dhvani* (Albela, 99 BPM, 7A). 25 BPM gap, incompatible key, genre change (Electronic → Bollywood), difficulty_score **0.8762** — clearly the hardest of the five.

All 10 tracks across the 5 exercises are distinct (no source/target reused).

## Candidate-pool limitations observed

- The training pool (2,096) is smaller than the full live library (2,171)
  purely because of the existing `get_candidate_pool()` requirement
  (bpm+camelot present) — no track was excluded by this phase's own
  additional safety filter in the real run.
- CHALLENGE's selected pair had `similarity_score` 0.162 — just above the
  0.15 floor. This is intentional (a genuinely hard, still-defensible
  pairing), not a sign of a thin pool.

## Extension points for future phases

`training_service.py` defines `_default_skill_profile()` returning
`{skill_profile: None, session_history: [], completed_exercises: [],
user_feedback: []}` — an unused placeholder shape, not consumed anywhere in
Phase 2. No historical skill data is pretended to exist.

## Phase 2 does NOT include

- LLM coaching
- ML personalization
- trend intelligence
- VirtualDJ integration
- session history
- user skill learning
- automatic downloads
- physical library modifications
- a second recommendation/scoring engine (all scoring is delegated to `recommendation_service.py`)

## Final safety verification

- Source files modified: **2** (`backend/app.py`, `backend/routes/__init__.py`) — both additive (new import + one registration line)
- Files created: **3** (`backend/services/training_service.py`, `backend/routes/training.py`, `backend/tests/test_training_service.py`)
- Documentation files created: **1** (this file)
- Tests added: **43**, full suite: **211/211 pass**
- MongoDB writes: **0**
- MongoDB documents created/deleted: **0**
- Physical file changes: **0**
- Physical files moved/renamed/deleted: **0**
- ID3 tags modified: **0**
- Downloads: **0**
- `recommendation_service.py` changes: **0** (imported, never modified)
- `downloader_service.py` / `auto_downloader.py` / `organizer_service.py` / `strict_matcher.py` / `library_migrator.py` / `master_organise.py` / `reconcile_library_state.py` / genre taxonomy: **0 changes**

## Remaining risks

- `GENRE_CROSSOVER`'s MEDIUM/HARD threshold (0.35) and `CHALLENGE`'s
  similarity floor (0.15) were chosen deliberately and documented, but are
  not empirically tuned against a large sample of real sessions across many
  dates — worth revisiting once daily sessions have run for a while.
- No end-to-end automated test exercises the Flask route (consistent with
  this codebase's existing convention of testing services, not routes) —
  the route was verified manually via a test-client script instead. A
  route-level regression could theoretically slip through service-level
  tests alone (e.g. a `jsonify` serialization issue) — none was found in
  manual verification, but it's not continuously guarded.
- The 21 Phase 1E "Group B" tracks (empty-field key/BPM fill, deliberately
  left unwritten per that phase's own scope decision) remain excluded from
  the training pool exactly as they were from the recommendation pool — no
  new exposure, but also no improvement from this phase.

## Phase 3 readiness

**READY FOR PHASE 3** (per this task's scope — Phase 3 is out of scope for
this report to plan in detail). Phase 2's deterministic exercise/session
contracts, extension-point placeholders (`skill_profile`,
`session_history`, `completed_exercises`, `user_feedback`), and the
`GET /api/dj/training/today` endpoint give a stable foundation for whatever
Phase 3 adds next, without requiring changes to `recommendation_service.py`
or the training contract itself.
