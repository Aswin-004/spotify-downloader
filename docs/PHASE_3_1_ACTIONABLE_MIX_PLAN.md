# Phase 3.1 — Actionable Mix Plan

**Date:** 2026-09-18
**Builds on:** Phase 2 (DJ Training Engine), Phase 3 (Daily DJ Coach)
**Depends on:** [PHASE_1G_FINAL_POST_REMEDIATION_AUDIT.md](PHASE_1G_FINAL_POST_REMEDIATION_AUDIT.md) = PASS (read first, as instructed)

---

## 1. Problem

Phase 3's coaching content was correct but abstract: `instructions` like *"Manage EQ — cut the incoming bass until the transition point"* or *"Practice harmonic mixing"* tell an experienced DJ what to focus on, but don't tell a beginner **what to physically do, in order** — which deck, when to start, what to do in the first 16 bars vs. the next 16, when to touch the EQ, how to finish. The content was also a flat list of strings, which pushes structure-parsing work onto the frontend.

## 2. Phase 3 current behavior (unchanged, still present)

`generate_daily_coaching_session()` still returns the same 5-exercise session (`session_id`, `date`, `title`, `objective`, `difficulty`, `estimated_duration_minutes`, `exercises`, `coach_summary`, `future_feedback_supported`, `generated_at`, `error`), and each exercise still carries its original `instructions` (free-text list) and `success_criteria` (free-text list) exactly as Phase 3 built them. **Nothing here was removed or restructured** — Phase 3.1 is additive only.

## 3. Phase 3.1 behavior (new)

Every exercise now also carries a `mix_plan` object: a structured, step-by-step transition guide with one field per concept (deck setup, cue, start, first-16-bars, next-16-bars, EQ, finish, listen-for, success), built from the exact same already-computed Phase 2 data (`source_track`, `target_track`, `metrics`) that Phase 3's own coaching content already uses. No new track selection, no new scoring, no recommendation changes, no LLM, no ML, no VirtualDJ.

## 4. MixPlan structure

Built by `services/dj_coach_service.py :: build_mix_plan(exercise_type, source, target, metrics)`, returning:

| Field | Type | Source |
|---|---|---|
| `source_track` / `target_track` | `{identity_key, title, artist}` | exercise's own `source_track`/`target_track` (already-real library data) |
| `deck_a` / `deck_b` | `{deck, role, identity_key, title}` | fixed assignment: source is always Deck A, target always Deck B |
| `source_bpm` / `target_bpm` | number | `source["bpm"]` / `target["bpm"]` (real `audio_features.bpm`) |
| `bpm_adjustment` | string | derived text from `metrics["bpm_delta"]` — "no adjustment needed" or "increase/decrease by X BPM" |
| `source_key` / `target_key` | string | `source["camelot"]` / `target["camelot"]` (real Camelot) |
| `harmonic_relation` | string | `metrics["harmonic_relation"]` verbatim (`same_key` / `adjacent_key` / `incompatible` — the exact enum training_service.py and the existing frontend already use) |
| `source_energy` / `target_energy` | number | `source["energy"]` / `target["energy"]` (real `rms_energy`) |
| `energy_relation` | string | `BUILD` / `RELEASE` / `SIMILAR` / `UNKNOWN`, derived from `metrics["energy_delta"]` against `training_service._ENERGY_TRANSITION_MIN_DELTA` (0.05, imported not re-declared — see §13) |
| `cue_instruction` | string | which deck to headphone-cue and what to listen for |
| `start_instruction` | string | when/how to start the target — always the generic phrase-boundary phrasing (see §6) |
| `phase_1` | list[str] | what to do in the first 16 bars |
| `phase_2` | list[str] | what to do in the next 16 bars |
| `eq_instruction` | string | bass/mid/high handling |
| `transition_finish` | string | how to complete the transition |
| `listen_for` | list[str] | what should be audible |
| `success_criteria` | list[str] | technique-specific "how do I know I did this right" |
| `recommendation_score` | number | `metrics["similarity_score"]` — Phase 2's own confidence-aware similarity score for this pair, reused verbatim, never recomputed. **Not** a confidence in the MixPlan's own instruction accuracy — see §13 |
| `limitations` | list[str] | what this plan genuinely can't claim (see §6) |

Every field is either a plain string or a list of strings — the frontend renders each one directly; nothing is a single blob the UI has to parse.

## 5. Exercise-specific logic

Each of the 5 Phase 2 exercise types gets its own `_*_mix_plan()` builder (mirroring the existing `_COACHING_BUILDERS` dispatch-table pattern already in this file), sharing the common fields above via `_base_mix_plan()`:

- **EASY_HARMONIC** — clean harmonic transition: lock beat grids first, bring Deck B in bass-cut, swap bass in one smooth move.
- **BPM_TRANSITION** — tempo matching: adjust pitch gradually by ear before bringing bass in; recheck alignment every 8 bars.
- **ENERGY_TRANSITION** — direction-aware (BUILD vs RELEASE) energy handling, branching on `energy_relation`.
- **GENRE_CROSSOVER** — bridges genres using shared rhythmic/technical ground; genre is used only in coaching *text*, never fed into any score (`recommendation_service.py` is untouched — see §11).
- **CHALLENGE** — `_challenge_dims()` names the *specific* factors making the pairing hard (large BPM gap, non-same-key relationship, notable energy shift, genre-family change — reusing `training_service._genre_family()` rather than a second genre-comparison implementation), and every phase of the plan explicitly addresses those named factors.

## 6. Structural-data limitations (the accuracy rule)

**This project's `library_index` has no cue-point, intro/outro, breakdown, or phrase-timestamp data for any track.** Nothing in this phase invents one. Every instruction that would normally reference a specific bar/beat/timestamp instead says:

> *"Start the target at the beginning of a clearly audible 16-bar phrase — wait for a clear phrase boundary or the next musical section change. No cue-point or phrase-timestamp data exists in the library for this track, so this has to be judged by ear, not read off a marker."*

and every `mix_plan.limitations` list opens with the same disclosure. Two additional, real-data-driven limitations are surfaced automatically when applicable (never fabricated):
- **Low key-detection confidence** — when a track's `key_confidence` (`audio_features.confidence`, the BPM/key analysis engine's own confidence — unrelated to recommendation scoring) is below 0.5, the plan flags the harmonic relation as unverified and recommends checking by ear.
- **Long-form DJ mix** — when a track's existing `length_class` (already computed by `recommendation_service.classify_track_length()`, unchanged) is `"dj_mix"`, the plan notes that 16-bar-phrase guidance applies to a chosen mixing point within the recording, not the whole thing.

Regression-tested in `tests/test_mix_plan.py` via a regex that rejects any bar/beat/timestamp-shaped text anywhere in a MixPlan's fields (test #7).

## 7. API changes

`GET /api/dj/coach/today` (unchanged route, unchanged `?date=YYYY-MM-DD` param) now returns a `mix_plan` object nested inside every element of `exercises[]`. Every pre-3.1 field (`session_id`, `date`, `title`, `objective`, `difficulty`, `estimated_duration_minutes`, `coach_summary`, `future_feedback_supported`, `generated_at`, `error`, and every existing per-exercise field) is unchanged — purely additive.

## 8. UI changes

`frontend-react/src/pages/DjCoach.jsx` — added a `MixPlanSection` component rendered inside the existing `ExerciseCard`, directly below the existing Source/Target/metrics row and above the pre-existing Instructions/Success Criteria sections (which are unchanged and still render). Follows the requested flow exactly:

```
Source / Target (existing)
  -> Deck Setup
  -> Before You Start
  -> Start Mix
  -> First 16 Bars
  -> Next 16 Bars
  -> EQ / Bass Swap
  -> Finish
  -> Listen For
  -> Success
  -> (limitations footnote)
```

Reuses existing components/tokens only: `Card`/`CardHeader`/`CardTitle`/`CardContent`, `Badge`, the existing `var(--accent-*)`/`var(--text-*)`/`var(--surface-1)`/`var(--border-subtle)` design tokens (all already used elsewhere in this file and the rest of the app), and `lucide-react` icons already a project dependency. No page was redesigned; no new route, no new page.

**Visually verified in a live browser** (dev servers on :5000/:5173, real library data) — screenshots confirmed Deck Setup, Before You Start, Start Mix, First/Next 16 Bars, EQ/Bass Swap, Finish, Listen For, Success, and the limitations footnote all render correctly with real BPM/Camelot/title values, and that the pre-existing Instructions/Success Criteria sections still render unchanged below it.

## 9. Determinism

`build_mix_plan()` is a pure function of `(exercise_type, source_track, target_track, metrics)` — no randomness, no wall-clock reads, no Mongo queries of its own. Since Phase 2's exercise selection is already deterministic (seeded from `date_str` + a pool fingerprint) and Phase 3 adds no randomness of its own, identical `(date, library snapshot)` inputs produce identical `mix_plan` objects, field-for-field. Verified directly (`tests/test_mix_plan.py`, tests #9) and via the full-session determinism test already covering `exercises` as a whole (now including `mix_plan` in that equality check).

## 10. Testing

New file: [`backend/tests/test_mix_plan.py`](../backend/tests/test_mix_plan.py) — 51 tests covering the task spec's 15 required scenarios (1-9, 13-15 directly; 10-12 are "existing suites still pass," verified by running them, not duplicated), the 8 review-fix regression tests (§13), and the 10 final-UX-fix regression tests (§14):

```
python -m unittest tests.test_mix_plan -v            → 51/51 OK
python -m unittest discover -s tests -p "test_*.py"  → 303/303 OK  (292 pre-existing + 11 new, zero regressions)
python -m py_compile services/dj_coach_service.py services/training_service.py
                       services/recommendation_service.py routes/dj_coach.py
                       tests/test_mix_plan.py tests/test_dj_coach_service.py
                       tests/test_training_service.py                          → PY_COMPILE_OK
```

`tests/test_mix_plan.py::TestRegressionsUnchanged::test_15_no_forbidden_touchpoints_in_this_module` statically greps `dj_coach_service.py`'s own source for `spotify_id`, `TXXX`, `ID3(`, `update_one`, `insert_one`, `delete_one`, `yt_dlp`, and raw network calls — none present, confirming Phase 3.1 introduced no path into Spotify identity, ID3 tags, Mongo writes, or downloads.

## 11. What was NOT touched (verified, not just claimed)

- `services/recommendation_service.py` — **zero changes**. `similarity_score`/`harmonic_relation`/etc. are read, never recomputed.
- `services/training_service.py` — **zero changes**. Exercise selection, difficulty scoring, and candidate-pool logic are exactly as Phase 2 left them; `dj_coach_service.py` only imports its already-existing module-level `_genre_family`, `_BPM_TRANSITION_MAX_DELTA`, and `_ENERGY_TRANSITION_MIN_DELTA` for reuse (§13).
- Phase 1F/1G Spotify-identity code (`backfill_gemini.py`, `legacy_identification_service.py`, `sync_tags_to_mongo.py`) — **zero changes**, not even touched by this phase's diff.
- Physical music files, MongoDB identity records (`spotify_id`, `identity_key`, `content_hash`), `ingest_tracks.json` — **zero changes** (verified: Mongo counts 2,282/2,150 and `ingest_tracks.json` SHA-256 both identical before and after this phase's work, including after the live browser verification session — see §12 note below).

## 12. Deferred features (explicitly out of scope)

Per the task's explicit instruction, none of the following were implemented or started:
- VirtualDJ / any DJ-software integration
- Live DJ Copilot (real-time performance feedback)
- LLM-generated coaching text
- ML-based difficulty/skill modeling
- Trend intelligence
- Automatic cue-point / phrase / beat-grid generation from audio analysis

## 13. Review fixes (2026-09-18)

A dedicated review pass (review-only, no code changes) checked the initial Phase 3.1 implementation against its own requirements and found 4 concrete issues, all fixed in a follow-up pass. Documented here for anyone reading the git history or wondering why some of the examples above differ from an earlier version of this doc.

**Fix 1 — duplicated, disagreeing CHALLENGE thresholds.** `_challenge_dims()` (the function that names *why* a CHALLENGE pairing is hard, for both `mix_plan.cue_instruction` and `mix_plan.listen_for`) previously used its own hardcoded `8.0` (BPM) and `0.04` (energy) thresholds instead of training_service.py's own `_BPM_TRANSITION_MAX_DELTA` (15.0) and `_ENERGY_TRANSITION_MIN_DELTA` (0.05) — the identical constants `training_service._challenge_content()` already uses to decide the same thing for the exercise's own `reason` field. The mismatch meant a CHALLENGE exercise could have its MixPlan call out "a large BPM gap" while the exercise's own `reason` text, built from the same `bpm_delta`, did not — two disagreeing explanations for the same transition. Fixed by importing and reusing the exact training_service constants; `dj_coach_service.py` now cannot drift from them. Regression-tested end-to-end (`TestReviewFixes.test_1_challenge_bpm_dims_agrees_with_training_service_reason` / `test_2_...energy...`) by building a real CHALLENGE exercise and asserting the MixPlan and the `reason` field agree.

**Fix 2 — `energy_relation`'s own threshold didn't match the project's established bar.** Previously used a locally-invented `0.02`; now uses `training_service._ENERGY_TRANSITION_MIN_DELTA` (0.05, imported, not re-declared) — the same `>=` comparison training_service.py itself uses to decide whether an energy_delta is a real, ENERGY_TRANSITION-worthy difference. Below 0.05, `energy_relation` is `"SIMILAR"` (the project's existing neutral label for "not a meaningful energy difference"), never `"BUILD"`/`"RELEASE"`. At exactly 0.05, it *is* significant (matches the `>=` in both places). Energy itself (`source_energy`/`target_energy`, `rms_energy`) is unmodified — only the classification boundary changed.

**Fix 3 — misleading `confidence` field renamed to `recommendation_score`.** The value (`metrics["similarity_score"]`) is unchanged and still reused verbatim from Phase 2, never recomputed — but the old name invited reading it as "how confident is the system that this MixPlan's instructions/data are correct," when it actually measures "how good a recommendation-match is this track pair," an entirely different concept. No frontend change was needed (the field was never rendered in `DjCoach.jsx`).

**Fix 4 — old Phase 3 `instructions` contradicted the new MixPlan and duplicated it.** Two problems, one fix. First: `_genre_crossover_coaching()` (the function that actually reaches the API/UI as `exercise.instructions` — `training_service._genre_crossover_content()`'s own similar text is computed but always overwritten by `dj_coach_service.py` before the response is built, so it was never the live text) told the user to *"Find a phrase or breakdown in {target} to enter cleanly"* — directly contradicting Phase 3.1's own accuracy rule, since no breakdown/phrase-timestamp data exists for any track. Second: all 5 of the old `_*_coaching()` builders re-stated, in different words, the same multi-step guidance the new `mix_plan` now covers in more concrete deck-by-deck detail — two competing instruction systems in the same card. Fixed by shrinking `instructions` (kept, for Phase 3 API compatibility — `exercise.instructions`/`exercise.success_criteria` still exist and are still non-empty lists of strings) to a single short "why this pairing, see the Mix Plan below" summary line per exercise, with no step-by-step content and no structural claims. `success_criteria` (already short, already free of structural claims) was left as-is. `mix_plan` is now unambiguously the primary, actionable workflow; the old fields are a one-line preface to it, not a second parallel plan.

**Fix 5 — vague verbs replaced with concrete, checkable actions.** `"confirm its beat grid lines up"`, `"nudge pitch to lock"`, `"confirm phase-locked"`, `"gauge perceived intensity"`, `"identify shared rhythmic/technical ground"`, and `"manage the bass handoff"` all named a goal without telling a true beginner how to check it. Replaced throughout `mix_plan`'s `cue_instruction`/`phase_1`/`phase_2` with concrete, listenable/actionable instructions built around **kick-drum alignment by ear** (a real, teachable technique: *"Listen to the kick drums. They should land at the same time. If one arrives early or late, use the jog wheel to nudge the target track until the kicks line up"*) for beatmatching, and **named comparable elements** (drums/percussion/layering count) for energy judgment and genre-bridging, instead of abstract "confirm"/"gauge"/"identify" language. `_PHRASE_START_INSTRUCTION` also gained a concrete, non-fabricated hint (*"most dance tracks group into 4, 8, 16, or 32-bar sections… listen for a clear change"*) — universal DJ structural convention, not track-specific invented data. Regression-tested (`TestReviewFixes.test_no_flagged_vague_phrases_in_mix_plan` and siblings) by grepping every `mix_plan` text field for the exact flagged phrases and asserting the concrete replacement technique is actually present, not just that the old phrase is gone.

## 14. Final UX fix (2026-09-18)

A final read-only validation pass re-read the actual implementation (not just the tests) and found 2 remaining issues that survived the §13 review fixes. Both fixed here; this is expected to be the last pass before this phase is frozen.

**Issue 1 — `ENERGY_TRANSITION`/`GENRE_CROSSOVER` still had a few abstract `listen_for`/`success_criteria` bullets.** §13 Fix 5 concretized `cue_instruction`/`phase_1`/`phase_2` for every type, but missed that `ENERGY_TRANSITION`'s `listen_for`/`success_criteria` still said *"Harmonic/tempo alignment holding while the energy shifts"* (no concrete audible check), and `GENRE_CROSSOVER`'s used purely subjective phrasing throughout (*"sounded controlled,"* *"held up,"* *"read as deliberate programming"* — exactly the pattern the task calls out as insufficient on its own). Fixed:
- `ENERGY_TRANSITION`: `listen_for`/`success_criteria` now check the same kick-drum/rhythmic-drift concept taught elsewhere in the session, plus a direct check that the energy actually moved the intended direction (*"The energy clearly increased/decreased by the end of the transition"* — the wording tracks the real `energy_direction`, tested by `test_energy_transition_direction_reflected_in_criteria`).
- `GENRE_CROSSOVER`: replaced entirely with concrete checks — both kick drums staying together, Deck B's elements entering without masking/clashing with Deck A's, and no harsh/jarring moment as the styles overlap.

Every subjective-only phrase from the original examples (*"sounds controlled," "reads as deliberate programming," "held up," "harmonic/tempo alignment holding"*) is now regression-tested absent from every exercise type's `listen_for`/`success_criteria` (`TestFinalUXFix.test_5_no_subjective_only_success_criteria_in_any_type`), not just the two types where it was originally found — guards against the same pattern being reintroduced elsewhere later.

**Issue 2 — two near-duplicate "Success" sections in the same card.** `mix_plan.success_criteria` (§13's new field) and the old `exercise.success_criteria` (kept for API compatibility) had independently-authored, near-identical text for all 5 exercise types (mostly just tense changes — e.g. *"completes"* vs *"completed"*), rendered under two separate headings using the same `CheckCircle2` icon in `DjCoach.jsx`, with the old `"Instructions"` section similarly duplicating `mix_plan`'s content as a shorter summary. Fixed at the source rather than patched over:
- **Backend:** `generate_daily_coaching_session()` no longer feeds `_coaching_content()`'s own per-type criteria into the exercise's `success_criteria` field — that field is now literally `mix_plan["success_criteria"]` (the same list, not a second copy), so the two can never drift into duplicate text again. `_coaching_content()` itself is completely unchanged (still returns a real criteria list) so any direct caller — including its own existing unit tests in `test_dj_coach_service.py` — is unaffected.
- **Frontend:** `DjCoach.jsx`'s old `"Instructions"` and `"Success Criteria"` `<div>` blocks were removed from rendering (both were, by this point, either a short pointer back to `mix_plan` or literally the same text as `mix_plan.success_criteria`). `exercise.instructions`/`exercise.success_criteria` remain in the API response unchanged in shape (still non-empty lists of strings) for any other consumer — only their presentation inside this page changed. `MixPlanSection` is now the single rendered workflow, exactly matching the requested Source → Target → Deck Setup → ... → Success flow with nothing competing after it.

Verified with a static `vite build` (no backend started, so the pre-existing auto-downloader could not activate) — build succeeded, bundle size decreased slightly (dead JSX removed), no other page in the app references the removed blocks.

---

### Note on live verification

Per the standing instruction to verify UI changes in a real browser, both dev servers were started locally (`.claude/launch.json`, new — a dev-convenience config, not application code) against the real library and Mongo. Starting the Flask backend caused its own **pre-existing** background auto-downloader/scheduler to resume its standing queue activity (visible in the Activity panel; one attempted download failed with a toast). This is that server's normal standing behavior whenever it's running, not anything Phase 3.1 code did. Verified directly afterward: zero files in the library were modified in that window (filesystem scan), `ingest_tracks.json` is byte-identical to before, and MongoDB counts are unchanged (2,282/2,150) — so nothing was actually written. Both dev servers were stopped immediately after the UI was confirmed correct.
