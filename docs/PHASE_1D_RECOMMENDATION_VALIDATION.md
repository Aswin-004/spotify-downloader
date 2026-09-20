# Phase 1D — Recommendation Engine Validation

**Date:** 2026-09-07
**Type:** Read-only validation. Zero database writes, zero file changes, zero source changes.
**Target verified before querying:** `cluster0.f4cod.mongodb.net` / `spotify_downloader` — **not** `localhost:27017`.

---

## Executive Summary

**The repaired intelligence is real, it is being used, and the engine produces usable DJ recommendations.**

The decisive evidence is that the three signals Phase 1C restored are now *demonstrably influencing scores*, not merely present:

| Controlled test | Result |
|---|---|
| Identical vs very different energy | 1.0 → 0.8 (energy term 1.0 → 0.0) |
| Identical vs very different spectral centroid | 1.0 → 0.9 (spectral term 1.0 → 0.0) |
| High vs low key confidence | 1.0 → 0.68 (camelot down-weighted ×0.2) |

Before Phase 1C those three terms were inert on 99.9% of the library, capping `recommend_next()` at ~0.50. The observed score range is now **0.532 – 0.987, mean 0.838**.

| Headline | Value |
|---|---|
| Candidate pool | **1,919 / 1,955 live (98.2%)** |
| Recommendations scored | 520 across 52 real sources |
| Strong / weak | **515 (99.0%) / 5 (1.0%)** |
| Harmonically compatible | 518 / 520 (99.6%) |
| Sequence quality (20 tracks) | 100% harmonic, 0 weak, mean BPM jump 1.05 |
| Crashes across 14 edge cases | **0** |
| `recommend_next()` latency | 22 ms mean over a 1,919-track pool |
| Mongo writes | **0** |

**Verdict: READY WITH MINOR ISSUES.** No blocker for Phase 2. The issues found are data-quality and tuning observations, not engine defects.

---

## Pre-Flight

### Engine implementation (documented, unchanged)

| Element | Location / value |
|---|---|
| Candidate pool | `get_candidate_pool()` — requires `bpm` **and** `camelot`; excludes `missing: true` |
| Similarity | `_compute_similarity()` — camelot **0.40** / bpm **0.30** / energy **0.20** / spectral **0.10** |
| Confidence-aware | `_compute_similarity_confidence_aware()` — used **only** by `recommend_next()` |
| Tolerances | BPM 20.0 · energy 0.15 RMS · spectral 1500.0 Hz |
| BPM jump | hard **15.0**, soft **25.0** (strict `>`, so exactly 15 and exactly 25 pass) |
| Artist window | `_ARTIST_WINDOW = 4` |
| Weak transition | score < **0.40**, or BPM jump > **12.0**, or camelot base = 0.0 |
| Key confidence | `_KEY_CONFIDENCE_THRESHOLD = 0.5`; missing → factor **0.5** |
| Flow modes | `warmup`, `peak`, `cooldown`, `mixed`; `RMS_MID_MAX = 0.18` |
| Genre | **No genre term exists in scoring** — genre is carried for display only |

**No constant, weight, or threshold was modified.**

### Test results

| Suite | Tests | Pass | Fail | Error |
|---|---|---|---|---|
| `tests.test_recommendation_service` | 56 | 56 | 0 | 0 |

---

## Test Results

### Official validator — `backend/validate_recommendation_service.py`

**Write audit before execution:** no `update/insert/delete/replace/bulk_write/$set` calls exist. However, it imports `get_library_index_collection` from `database.py`, whose `_get_db()` calls **`_ensure_indexes()` — a `createIndexes` write** — and which falls back to `localhost:27017` when `MONGODB_URI` is absent (the Phase 1C trap).

It was therefore **not run as-is**. It was executed through a read-only shim that pre-registers a `database` module exposing only a direct read-only collection, so `_get_db()` is never reached. The real validator code path ran unmodified.

Result: completed successfully, reporting `considered=1919`, `excluded_missing=171`, `excluded_incomplete=36`, and ending `Done. No writes were made to the database.`

---

## Real Library Coverage

| Metric | Value |
|---|---|
| Total documents | 2,126 |
| Live (`missing != true`) | 1,955 |
| Soft-deleted | 171 |
| **Scoreable (bpm + camelot)** | **1,919 (98.2% of live)** |

### Genre distribution of the scoreable pool

| Genre | Tracks | Genre | Tracks |
|---|---|---|---|
| Punjabi | 481 | Hip Hop | 73 |
| Bollywood | 385 | R&B | 43 |
| Electronic | 243 | UK Garage | 39 |
| House | 202 | Dubstep | 33 |
| Latin | 120 | Tamil | 15 |
| Trance | 101 | Same Day Cleaning | 10 |
| Pop | 83 | | |
| Drum & Bass | 76 | | |

**Techno is absent from the scoreable pool.** Phase 1B counted 6–8 Techno tracks; the byte-duplicate audit showed at least one (`Bebopper - Ajja.mp3`) is a cross-crate copy also filed under Electronic. The validation sample therefore covers **12 of the 13 requested genres**; 52 sources were tested (4 per genre).

---

## Score Distribution

520 recommendations over 52 real sources (top 10 each).

| Statistic | Score | Adjusted score |
|---|---|---|
| Min | 0.5318 | 0.5318 |
| p25 | 0.7960 | 0.8080 |
| Median | 0.8532 | 0.8646 |
| Mean | **0.8376** | 0.8480 |
| p75 | 0.8942 | 0.9028 |
| p90 | 0.9354 | 0.9420 |
| Max | **0.9872** | 0.9872 |
| Std dev | 0.0832 | — |
| Distinct values | 468 / 520 | — |

### Strength classification

| Label | Count | % |
|---|---|---|
| Strong | 515 | 99.0% |
| Weak | 5 | 1.0% |
| Harmonically compatible | 518 | 99.6% |
| Full key confidence | 327 | 62.9% |
| Low key confidence (down-weighted) | 193 | 37.1% |

### Comparison with pre-Phase-1C behaviour

Phase 0 established that with energy and spectral inert and confidence absent, `recommend_next()` could not exceed **≈0.50** — below the 0.40 weak threshold for most pairs, so nearly every recommendation self-flagged weak.

| | Phase 0 (predicted) | Phase 1D (measured) |
|---|---|---|
| Max achievable score | ≈0.50 | **0.9872** |
| Mean score | — | **0.8376** |
| Weak-transition rate | near-universal | **1.0%** |
| Contributing terms | 2 of 4 | **4 of 4** |

468 distinct values across 520 results confirms the scores carry real discriminating information rather than clustering on a few plateaus.

---

## Real Recommendation Examples

25 source → top-candidate pairs were captured; 6 representative ones follow. Reasons are derived strictly from the returned score components.

**1. Exact Camelot, close BPM, low spectral similarity**
`Fred again.. — Winny` (140 BPM, 3B, House) → `Deborah de Luca — Nero` (136 BPM, 3B)
score **0.7279** · camelot 1.0 · bpm 0.8 (Δ4.0) · energy 0.0 · spectral 0.8794 · **STRONG**
Exact Camelot match carried it; the energy term contributed nothing (RMS delta ≥ 0.15).

**2. Near-perfect BPM and energy alignment**
`HUGEL — I'm Moving To The Sun` (125 BPM, 3A) → `Indian — Magic Coke Studio Bharat` (126 BPM, 3A)
score **0.9103** · camelot 1.0 · bpm 0.95 (Δ1.0) · energy 0.9898 · spectral 0.2735 · **STRONG**

**3. Adjacent Camelot**
`(It Goes Like) Nanana — Edit` (130 BPM, 5A) → `Electronic — Fancy $hit` (129 BPM, 4A)
score **0.8589** · camelot base 0.75 · bpm 0.95 · energy 0.9768 · spectral 0.7852 · **STRONG**

**4. Confidence down-weighting visibly applied**
`Fred again.. — ..FEISTY` (102 BPM, 2A) → `Indian — 8 ASLE` (100 BPM, 2A)
score **0.7993** · camelot **0.592** (base 1.0, factor **0.592**) · bpm 0.9 · energy 0.9909 · spectral 0.9432
An exact Camelot match discounted because key-detection confidence was below 0.5.

**5. Perfect BPM, discounted key**
`Truth x Lies — Heard About Me` (123 BPM, 4A) → `Reznik — Cloudy Eyes` (123 BPM, 4A)
score **0.7408** · camelot 0.526 (base 1.0, factor 0.526) · bpm **1.0** (Δ0.0) · energy 0.7727 · spectral 0.759

**6. Highest-quality observed pairing**
`James Hype — 7 Seconds` (129 BPM, 3A) → `Hamdi — Sammy Virji Remix` (130 BPM, 3A, Dubstep)
score **0.9664** · camelot 1.0 · bpm 0.95 · energy 0.9768 · spectral 0.8603 · **STRONG**

Note example 6 crosses Electronic → Dubstep. **This is correct behaviour, not a bug:** the scoring function has no genre term, so crates never constrain recommendations.

---

## BPM Validation

| Statistic | Value |
|---|---|
| n | 1,919 |
| Min / Max | **70.0 / 178.0** |
| Mean / Median | 119.98 / 123.0 |
| p25 / p75 / p90 | 99.0 / 136.0 / 145.0 |
| Std dev | 23.08 |
| Distinct values | 88 |
| **Outside 60–200 BPM** | **0** |
| Zero or negative | **0** |
| Non-integer | **0** |
| Non-numeric | **0** |

All BPM values sit inside the usable DJ range. No decimal-parsing artefacts survived Phase 1C, consistent with its report of 0 rejected values.

### Measured BPM-delta behaviour (source 128 BPM, 8A)

| Δ BPM | Returned | Score | Weak? |
|---|---|---|---|
| 0 | yes | 1.000 | no |
| +2 | yes | 0.970 | no |
| +5 | yes | 0.925 | no |
| +10 | yes | 0.850 | no |
| +15 | yes | 0.775 | **yes** |
| +25 | yes | 0.700 | **yes** |
| **+40** | **rejected** | — | — |

Monotonic decay, weak flag engaging above the 12.0 BPM threshold, and hard rejection beyond the soft limit. Exactly 15 and exactly 25 pass because both comparisons are strict `>` — correct per the implementation.

---

## Camelot Validation

| Check | Result |
|---|---|
| Distinct Camelot values | 24 |
| **All values structurally valid** | **Yes** — every value ∈ {1A…12A, 1B…12B} |
| Malformed / unexpected values | **0** |
| Invalid Camelot crashes scoring | **No** — `"99Z"` scores 0.0 and is handled |

Most common: 4A (164), 9A (159), 1A (154), 6A (152), 2A (143), 8A (143), 5A (138), 11A (104). The minor-key skew matches the library's 580:276 minor:major split recorded in Phase 0.

### Compatibility behaviour (source 8A, 128 BPM)

| Relationship | Camelot base | Score | Weak? |
|---|---|---|---|
| Same key (8A) | 1.00 | 1.000 | no |
| Adjacent (9A) | 0.75 | 0.900 | no |
| Relative major (8B) | 0.75 | 0.900 | no |
| Incompatible (2B) | 0.00 | 0.600 | **yes** |

All 1,919 scoreable tracks carry a valid Camelot representation.

---

## Energy Validation

| Statistic | `rms_energy` |
|---|---|
| n | 1,935 |
| Min / Max | 0.0423 / 0.4886 |
| Mean / Median | 0.1599 / — |
| Std dev | **0.0576** |
| **Distinct values** | **1,904 of 1,935 (98.4%)** |
| NaN / inf / zero / non-numeric | **0** |

**The values genuinely vary** — 1,904 distinct readings, not a constant stamped across the library. Contribution confirmed directly: identical-energy candidate scored 1.0 (energy term 1.0), very-different-energy candidate scored 0.8 (energy term 0.0) — exactly the 0.20 weight.

---

## Spectral Validation

| Statistic | `spectral_centroid_mean` | `zero_crossing_rate` |
|---|---|---|
| n | 1,935 | 1,935 |
| Min / Max | 809.27 / 5038.17 Hz | 0.0156 / 0.4062 |
| Mean | 2394.02 Hz | 0.0933 |
| Std dev | **513.71** | 0.0341 |
| Distinct values | **1,908 (98.6%)** | 1,894 (97.9%) |
| Anomalies | **0** | **0** |

Contribution confirmed: identical spectral candidate 1.0 (term 1.0) vs very different 0.9 (term 0.0) — exactly the 0.10 weight.

---

## Confidence Validation

| Statistic | `confidence` |
|---|---|
| n | 1,935 |
| Min / Max | 0.212 / 0.972 |
| Mean | 0.6206 |
| Std dev | 0.1580 |
| Distinct values | 609 |
| **Below 0.5 threshold** | **471 / 1,935 (24.3%)** |

Contribution confirmed: confidence 0.9 → factor 1.0, score 1.000; confidence 0.1 → factor 0.2, camelot term cut 1.0 → 0.2, score 0.680.

A missing confidence yields factor 1.0 in practice when the *other* side supplies one — the rule takes the minimum of available values and only falls back to 0.5 when neither track has any.

**Caveat carried forward from Phase 1C:** these confidence values describe *librosa's own* key detection, while the stored Camelot is tag-derived. The two agreed on only 68.1% of tracks. Confidence is therefore a well-founded signal for roughly two-thirds of the library and an approximation for the rest.

---

## Candidate Pool Quality

| Metric | Count | % of live |
|---|---|---|
| Live documents | 1,955 | 100% |
| **In candidate pool** | **1,919** | **98.2%** |
| Excluded | 36 | 1.8% |

### Exclusion reasons

| Reason | Count |
|---|---|
| No `audio_features` sub-document | 19 |
| Missing `bpm` | 17 |
| Missing `camelot` | 17 |

(The 17s overlap — the same documents lack both.) These 36 correspond to the 20 analysis failures plus a handful of untagged files, i.e. the known damaged set. **The engine is not unnecessarily excluding healthy tracks.**

One observation: **16 excluded documents do have `rms_energy`** — they were analysed successfully but still lack BPM or Camelot, so `_is_scoreable()` rejects them. That is the intended conservative behaviour, not a defect.

---

## Duplicate / Identity Safety

| Check | Result |
|---|---|
| Source returned as its own candidate | **0** |
| Duplicate `identity_key` within one result set | **0** |
| Same physical file recommended twice | **0** |
| Shadow (double-indexed) paths remaining | **0** |
| Spotify IDs shared by more than one live document | 3 |

Verified across 40 real sources returning 10 candidates each.

The 3 shared Spotify IDs are survivors of the 10 collision groups Phase 1C recorded (the rest resolved to a single live document after shadow removal). **They caused no incorrect recommendation behaviour**, because the engine keys entirely on `identity_key`, never on `spotify_id`. Left unresolved as instructed.

Byte-identical duplicate copies (6 groups from Phase 1C) remain in the library and can both surface as candidates — they are distinct `identity_key`s at distinct paths. Not deduplicated in this phase.

---

## Artist Repetition

| Test | Result |
|---|---|
| Artist tested | `Fred again..` |
| Tracks by that artist in library | 22 |
| Same-artist tracks in top 10 | **0** |
| `_ARTIST_WINDOW` | 4 (unchanged) |

Documented behaviour: `recommend_next()` defaults `recent_artists` to `[source track's artist]`, so the source's own artist is blocked in the primary ranking pass despite 22 available tracks.

**One nuance worth recording:** in the controlled test where only two candidates existed (one same-artist, one not), *both* were returned — ordered `other_artist` first, then `same_artist`. This is the relaxation fallback: when the strict pass yields fewer than `top_n`, a relaxed pass re-admits blocked candidates rather than returning a short list. Preference is preserved through ordering. Correct, but worth knowing before building on it.

---

## Sequence Generation

Seeded from a real House track, `flow="mixed"`.

| Length | Achieved | Time | Avg transition | Harmonic | Weak | Mean BPM jump | Max jump | Dup tracks | Repeat artists |
|---|---|---|---|---|---|---|---|---|---|
| 5 | **5** | 36 ms | 0.8671 | **100%** | **0** | 2.75 | 4.0 | **0** | **0** |
| 10 | **10** | 97 ms | 0.9082 | **100%** | **0** | 1.89 | 6.0 | **0** | **0** |
| 20 | **20** | 186 ms | 0.9198 | **100%** | **0** | 1.05 | 6.0 | **0** | **0** |

Every requested length was achieved in full, with no duplicate tracks, no repeated artists, 100% harmonic compatibility, and zero weak transitions. Maximum BPM jump of 6.0 sits well inside the 15.0 hard limit.

### Flow modes

| Flow | Achieved | Avg transition | Weak |
|---|---|---|---|
| warmup | 10 | 0.8862 | 0 |
| peak | 10 | 0.8659 | 0 |
| cooldown | 10 | 0.8941 | 0 |
| mixed | 10 | 0.9082 | 0 |

All four produce complete, coherent sequences.

---

## recommend_next()

Exercised from 52 real sources across 12 genres (target was 20).

| Metric | Value |
|---|---|
| Sources tested | 52 |
| Recommendations produced | 520 |
| Sources returning an error | 0 |
| Candidates considered per call | 1,918–1,919 |
| Excluded (soft-deleted) | 171 |
| Excluded (incomplete metadata) | 36 |
| **Determinism** | **Confirmed** |

Every returned record carries the full explanatory payload — `camelot_score`, `camelot_score_base`, `confidence_factor`, `bpm_score`, `energy_score`, `spectral_score`, `bpm_jump`, `harmonic_compatible`, `key_confidence_low`, `is_weak_transition`, `energy_normalized`. **This is sufficient to drive a DJ Coach explanation layer without any LLM**, which was the Phase 0 design requirement.

### Determinism — a correction to my own first measurement

An initial strict dict-equality check reported `determinism: False`. That was **my test being wrong, not the engine**. The only differing key is `generated_at`, an ISO timestamp included by design. Re-verified:

- `recommendations` payload identical across **5 consecutive runs**
- `generate_playlist_sequence()` produced identical `sequence` and `transitions` across runs

The engine is deterministic.

---

## Edge Cases

14 cases, **zero crashes, zero writes, zero silent corruption**.

| Case | Behaviour |
|---|---|
| Non-existent source | Clean error string, no exception |
| Source missing BPM | `"current track is missing bpm and/or camelot"` |
| Source missing Camelot | Same clean error |
| Empty candidate pool | Empty `recommendations`, `error: None` |
| Empty docs list | Clean "not found" error |
| Candidate missing energy | Scored and returned (energy term 0.0) |
| Candidate missing confidence | Handled; factor 1.0 from the other side |
| Extreme low BPM (5) | Scored 1.0, no crash |
| Extreme high BPM (400) | Scored 1.0, no crash |
| Invalid Camelot (`"99Z"`) | Camelot term 0.0, no crash |
| Only same-artist candidates | 1 returned via relaxation |
| Invalid flow value | `ValueError` — **intentional**, documented |
| Invalid `top_n` (0) | `ValueError` — **intentional**, documented |
| Determinism | Confirmed |

The two `ValueError`s are deliberate input validation, not failures.

Note: extreme BPM values of 5 and 400 are *scored* rather than rejected — the engine has no absolute BPM sanity gate, relying on `bpm_key_service` (40–250) at write time. Not reachable from current data (measured range 70–178), but worth knowing.

---

## Performance

Measured against the real 1,919-track pool.

| Operation | Mean | Median | Max |
|---|---|---|---|
| `get_candidate_pool()` | 3.13 ms | — | — |
| `recommend_next(top_n=5)` | **22.07 ms** | 22.13 ms | 29.00 ms |
| `find_similar_tracks(top_k=10)` | 24.61 ms | — | 27.37 ms |
| `generate_playlist_sequence(20)` | **190.9 ms** | — | — |

Independently corroborated by the official validator (21.00 ms mean / 182.1 ms sequence).

Comfortably interactive. A live copilot could call `recommend_next()` on every track change with no perceptible delay. Note all timings assume documents are pre-fetched; the Mongo round-trip is separate.

---

## Problems Found

**None blocking.** All items below are data-quality or tuning observations.

| # | Issue | Severity | Detail |
|---|---|---|---|
| 1 | **201 tracks have artist `"Unknown"`** | MEDIUM | Plus 42 empty artist, 37 empty title. Degrades `_ARTIST_WINDOW` protection — all 201 are treated as one artist — and weakens any future explanation text. |
| 2 | **6 tracks are DJ mixes, not tracks** | MEDIUM | `duration_sec` up to **11,562 s (3.2 h)**. They are fully scoreable and will be recommended as if they were single tracks. |
| 3 | Confidence semantics mismatch | MEDIUM | Stored `confidence` describes librosa's key detection; stored Camelot is tag-derived. Only 68.1% agreement (Phase 1C). Affects 24.3% of tracks whose confidence is below 0.5. |
| 4 | Techno absent from scoreable pool | LOW | 12 of 13 requested genres validated. |
| 5 | No genre term in scoring | LOW | By design, but means Bollywood → Dubstep transitions can rank highly on harmonic/tempo grounds alone. |
| 6 | 5 tracks under 60 s | LOW | Likely clips or fragments; scoreable. |
| 7 | No absolute BPM sanity gate in engine | LOW | BPM 5 and 400 score normally. Not reachable from current data. |
| 8 | 3 Spotify IDs on multiple live docs | LOW | No effect on recommendations — engine keys on `identity_key`. |
| 9 | `validate_recommendation_service.py` triggers `_ensure_indexes()` | LOW | Its docstring claims read-only; true for queries, but the import path issues `createIndexes` and can target `localhost`. Needed a shim to run safely. |

---

## Recommendations

1. **Backfill the 201 `Unknown` artists** from tags or Spotify ID before the Training Engine relies on artist-repeat protection.
2. **Flag long-form content** — add a duration guard (e.g. > 900 s) so DJ mixes are not recommended as tracks.
3. **Decide the confidence policy** — either re-derive Camelot from librosa's key where confidence is high, or record `key_source` so the mismatch is explicit.
4. **Consider a genre-affinity term** if cross-genre transitions prove undesirable in practice. This is a scoring change and was deliberately not made here.
5. **Fix `validate_recommendation_service.py`'s import path** to accept an injected collection.
6. Leave the 47 review items and 34 reacquisition candidates as they are — none affects the engine.

---

## Phase 2 Readiness

### **READY WITH MINOR ISSUES**

| Criterion | Status |
|---|---|
| Scoring works | ✅ range 0.532–0.987, mean 0.838 |
| No crashes | ✅ 0 across 14 edge cases |
| Repaired intelligence actually used | ✅ all 4 scoring terms contribute |
| Scores have useful variation | ✅ 468 distinct values / 520, σ = 0.083 |
| BPM works | ✅ monotonic decay, thresholds correct, 0 invalid values |
| Camelot works | ✅ 24 valid values, compatibility tiers correct |
| Energy contributes | ✅ 1.0 → 0.0 term swing, 1,904 distinct values |
| Spectral contributes | ✅ 1.0 → 0.0 term swing, 1,908 distinct values |
| Confidence contributes | ✅ factor 1.0 → 0.2 measured |
| Candidate filtering safe | ✅ 98.2% retained; 36 excluded are the known damaged set |
| No duplicate/shadow recommendations | ✅ 0 shadows, 0 self-returns, 0 repeated files |
| Sequence generation stable | ✅ 5/10/20 full, 100% harmonic, 0 weak |
| `recommend_next()` usable | ✅ deterministic, explainable, 22 ms |
| No critical identity/data blocker | ✅ none found |

Every READY criterion is met. The qualifier is for the non-blocking data-quality items — chiefly the 201 `Unknown` artists and the 6 DJ-mix-length tracks — which should be logged and addressed, but do not prevent building the Training Engine on this engine.

---

## Final Output

| Measure | Result |
|---|---|
| Recommendation unit tests | **56 / 56 pass** |
| Validation checks executed | 14 edge cases + 12 controlled transition tests + official validator |
| Real-library sources tested | **52** (12 genres) |
| Recommendations scored | **520** |
| Average score | **0.8376** |
| Score range | **0.5318 – 0.9872** |
| Strong recommendations | **515 (99.0%)** |
| Weak recommendations | **5 (1.0%)** |
| Candidate pool size | **1,919 (98.2% of live)** |
| Sequences | 5/10/20 all achieved; 100% harmonic; 0 weak; 0 duplicates |
| Edge cases | 14 tested, **0 crashes** |
| Performance | pool 3 ms · `recommend_next` 22 ms · sequence 191 ms |
| Issues found | **9** (0 high, 3 medium, 6 low) |
| **Issues blocking Phase 2** | **0** |
| **Decision** | **READY WITH MINOR ISSUES** |

### Safety confirmation

| Check | Count |
|---|---|
| **Mongo writes** | **0** |
| **Physical files changed** | **0** |
| **Downloads** | **0** |
| **Application source changes** | **0** |
| **Report files created** | **1** |

MongoDB was accessed exclusively through a direct read-only `MongoClient`, with the target host asserted as non-localhost before any query. `database._get_db()` was never invoked, so `_ensure_indexes()` never ran.

Phase 1D stops here. Phase 2 was not started.
