# Phase 3 — Daily DJ Coach

**Date:** 2026-09-15
**Type:** New orchestration service + one read-only API route + one frontend page. Zero MongoDB writes, zero physical file changes, zero downloads, zero `start.bat` changes.

## Objective

Phase 2 answers *"what should I practice?"* (5 deterministic exercises).
Phase 3 answers *"how should I practice today's session?"* — it wraps
Phase 2's output with coaching structure: stage ordering/labels, a session
objective, a title, an overall difficulty summary, an estimated duration,
and per-exercise coaching instructions/success criteria. No LLM, no ML, no
external API, no new scoring engine.

## Architecture

```
library_index (Mongo)
    -> recommendation_service.get_candidate_pool() / _compute_similarity_confidence_aware()  [Phase 0/1, unchanged]
    -> training_service.generate_daily_session()                                             [Phase 2, unchanged, reused as-is]
    -> dj_coach_service.generate_daily_coaching_session()                                     [NEW — orchestration only]
    -> GET /api/dj/coach/today                                                                [NEW — thin Flask route]
    -> frontend /dj page                                                                      [NEW — React page]
```

`dj_coach_service.py` calls `training_service.generate_daily_session()`
exactly once and never recomputes track selection, similarity, or
difficulty scoring — it only reads the finished `exercises` list and adds
a `stage` label plus coaching-specific `instructions`/`success_criteria`
per exercise, then derives the session-level `title`/`objective`/
`difficulty`/`estimated_duration_minutes`/`coach_summary` from that same
list. `recommendation_service.py` and `training_service.py` are imported,
never modified.

## Phase 2 relationship

`GET /api/dj/training/today` is completely untouched and still returns
Phase 2's own exercise shape (verified live — see Real Library Result
below). `GET /api/dj/coach/today` is a new, separate endpoint; nothing was
removed or renamed. The five exercise types, their selection rules, and
their difficulty scores are 100% Phase 2's — Phase 3 never redefines them.

## Files

| File | Status |
|---|---|
| `backend/services/dj_coach_service.py` | Created |
| `backend/routes/dj_coach.py` | Created |
| `backend/tests/test_dj_coach_service.py` | Created |
| `frontend-react/src/pages/DjCoach.jsx` | Created |
| `backend/routes/__init__.py` | Modified — export `dj_coach_bp` |
| `backend/app.py` | Modified — import + `register_blueprint(dj_coach_bp)` |
| `frontend-react/src/App.jsx` | Modified — add `/dj` route |
| `frontend-react/src/components/Sidebar.jsx` | Modified — add "DJ Coach" nav item |
| `frontend-react/src/services/api.js` | Modified — add `getDjCoachToday()` |
| `start.bat` | **Untouched** (see Startup Integration below) |

No other file was touched. `recommendation_service.py`, `training_service.py`,
`bpm_key_service.py`, the downloader/organizer/matcher/migrator/reconcile
scripts, and genre taxonomy are all unchanged.

## Coach responsibilities

`dj_coach_service.py`'s orchestration layer, in the order it runs:

1. Call `training_service.generate_daily_session(date_str, docs)` — gets the 5 (or fewer, on graceful failure) Phase 2 exercises, unchanged.
2. Attach a coaching **stage** label per exercise (Section 3 below) — a presentation concept only, the underlying `type` is untouched.
3. Replace each exercise's Phase 2 `instructions`/`success_criteria` with Phase 3's own coaching-specific, per-type templates (Sections 8/9), interpolated with that exercise's *actual* tracks/metrics — never generic filler.
4. Compute a session-wide **objective** (focus, description, skills) purely from the actual 5 exercises' difficulty scores (Section 4).
5. Compute a session **title** from that same objective's focus, via a fixed template lookup (Section 5).
6. Compute overall **difficulty** (score, label, progression) from the mean of the 5 exercises' own `difficulty_score` values (Section 6) — no second difficulty model.
7. Compute **estimated duration** by summing fixed, configurable per-type minutes (Section 7).
8. Compose a deterministic **coach_summary** paragraph from the above (Section 10).

## Five exercises — stage mapping

The Phase 2 exercise **type** and its selection logic are exactly as Phase 2
built them. Phase 3 only attaches a coaching **stage** label:

| Order | Type (Phase 2, unchanged) | Stage (Phase 3, new) | Difficulty (Phase 2, unchanged) |
|---|---|---|---|
| 1 | `EASY_HARMONIC` | `WARM_UP` | EASY |
| 2 | `BPM_TRANSITION` | `TECHNIQUE` | MEDIUM |
| 3 | `ENERGY_TRANSITION` | `ENERGY_CONTROL` | MEDIUM |
| 4 | `GENRE_CROSSOVER` | `CROSSOVER` | MEDIUM or HARD (Phase 2's own dynamic call) |
| 5 | `CHALLENGE` | `CHALLENGE` | HARD |

No exercise type was added, removed, or reordered — `training_service.py`
always builds them in exactly this sequence, and Phase 3 relies on that
fixed order rather than re-sorting.

## Objective generation

`_focus_for_exercises()`: the day's focus is whichever of the **four
non-CHALLENGE** exercises has the highest `difficulty_score`. CHALLENGE is
deliberately excluded from this comparison — `training_service.py`'s own
selection rule *maximizes* CHALLENGE's difficulty_score by construction
(proven in Phase 2's own tests), so including it would make the focus (and
therefore the title) the same "controlled difficult transitions" every
single day, defeating the purpose of a day-to-day-varying objective. Ties
keep the first exercise in fixed type order (deterministic).

```python
SKILL_BY_TYPE = {
    "EASY_HARMONIC": "harmonic mixing",
    "BPM_TRANSITION": "BPM control",
    "ENERGY_TRANSITION": "energy management",
    "GENRE_CROSSOVER": "genre crossover",
    "CHALLENGE": "controlled difficult transitions",
}
```

`objective.skills` is simply `[SKILL_BY_TYPE[e.type] for e in exercises]` in
stage order — directly derived, never invented. `objective.description` is
a template sentence naming the actual stage sequence and the actual
hardest exercise's actual source/target tracks and difficulty score — no
motivational filler, no LLM.

**Design note on the sixth vocabulary term:** the Phase 3 spec's focus
vocabulary lists six terms (`harmonic mixing`, `BPM control`, `energy
management`, `genre crossover`, `transition confidence`, `controlled
difficult transitions`) against five exercise types. `transition
confidence` is used as the session-wide umbrella phrase inside
`objective.description` (every exercise, not one specific type, builds
it) rather than as a seventh `focus` bucket — documented here rather than
silently dropped.

## Session title

```python
TITLE_BY_FOCUS = {
    "harmonic mixing": "Smooth Transition Lab",
    "BPM control": "Tempo Control Session",
    "energy management": "Energy Flow Practice",
    "genre crossover": "Genre Bridge Session",
    "controlled difficult transitions": "Transition Challenge",
}
```

`title = TITLE_BY_FOCUS[objective.focus]` — a pure lookup, deterministic,
matches the Phase 3 spec's own five example titles exactly. No LLM.

## Difficulty

```python
scores = [e.metrics.difficulty_score for e in exercises]   # Phase 2's own values, unchanged
score = round(mean(scores), 4)
label = "BEGINNER" if score < 0.25 else "INTERMEDIATE" if score < 0.50 else "ADVANCED"
progression = "EASY_TO_HARD" if scores[0] == min(scores) and scores[-1] == max(scores) else "MIXED"
```

No second, opaque difficulty model — this is a pure aggregation of Phase
2's already-tested `difficulty_score` values. `progression` is computed
honestly rather than asserted: it checks that the *first* exercise
(EASY_HARMONIC) is the session's easiest and the *last* (CHALLENGE) is the
hardest — the designed shape, and one Phase 2's own tests already prove
holds for CHALLENGE vs. EASY_HARMONIC — falling back to `"MIXED"` if that
ever isn't true, rather than always claiming `EASY_TO_HARD` unconditionally.
The `_BEGINNER_MAX`/`_INTERMEDIATE_MAX` thresholds (0.25 / 0.50) are
centralized module constants, documented in `dj_coach_service.py` itself.

## Estimated duration

```python
DURATION_MINUTES_BY_TYPE = {
    "EASY_HARMONIC": 8, "BPM_TRANSITION": 10, "ENERGY_TRANSITION": 10,
    "GENRE_CROSSOVER": 12, "CHALLENGE": 15,
}
```

Summed over the *actual* exercises present (55 minutes when all 5 succeed;
correctly less if Phase 2 could only build fewer). These are estimates —
`dj_coach_service.py` never claims to know how long a DJ actually
practiced, and no field in the API response implies otherwise.

## Coaching instructions and success criteria

Per-type templates (`_easy_harmonic_coaching`, `_bpm_transition_coaching`,
`_energy_transition_coaching`, `_genre_crossover_coaching`,
`_challenge_coaching` in `dj_coach_service.py`), each interpolated with
that exercise's real track titles, BPMs, camelot keys, genres, and energy
values — never generic motivational text. Success criteria are explicitly
**practice targets**, not automated-detection claims: `coach_summary` and
`future_feedback_supported: false` both state plainly that this system
cannot yet determine whether a transition was actually executed
successfully. A dedicated test (`test_no_automated_success_claim_language`)
asserts none of the criteria text claims automated detection.

## Personalization boundary

No skill profile, ratings, session history, adaptive learning, or user
performance database — none of that exists in this phase. "Personalization"
here means exactly what Phase 2 already means: the user's actual library,
actual BPM/Camelot/energy/genre, and actual recommendation scores. Stable
identifiers are preserved unchanged for a future Phase 5 to key off of:
`session_id` (`"session-{date}"`, shared with Phase 2's own session_id for
the same date), `exercise_id`, `source_track.identity_key`,
`target_track.identity_key`, `type`, `difficulty` — verified directly by
`test_session_id_and_stable_identifiers_present`.

## Determinism

`dj_coach_service.py` introduces **no randomness of its own** — every
value it computes (stage, focus, title, difficulty, duration, coaching
text) is a pure function of `training_service.generate_daily_session()`'s
already-deterministic output. Verified: same date + same library snapshot
produces identical `exercises`/`title`/`objective`/`difficulty` across two
calls (`test_deterministic_same_date_same_pool`), `generated_at` changes
between calls but selection does not
(`test_generated_at_does_not_affect_selection`, which sleeps between
calls), and the real, running HTTP endpoint was called twice back-to-back
with byte-identical results (see Real Library Result below).

## API

```
GET /api/dj/coach/today
GET /api/dj/coach/today?date=YYYY-MM-DD   (optional override, same convention as Phase 2)
```

Registered exactly like Phase 2's `training_bp` — a new one-file blueprint
(`routes/dj_coach.py` -> `dj_coach_bp`), exported from `routes/__init__.py`,
registered in `app.py` alongside the other seven. The route is a thin
wrapper; all logic is in `dj_coach_service.generate_daily_coaching_session()`.

**Response contract** (verified against the real, running server):

```json
{
  "session_id": "session-2026-09-15",
  "date": "2026-09-15",
  "title": "Genre Bridge Session",
  "objective": {
    "title": "Bridge Genres Confidently",
    "focus": "genre crossover",
    "description": "Today's session moves through Warm Up, Technique, Energy Control, Crossover, Challenge, ...",
    "skills": ["harmonic mixing", "BPM control", "energy management", "genre crossover", "controlled difficult transitions"]
  },
  "difficulty": { "score": 0.3418, "label": "INTERMEDIATE", "progression": "EASY_TO_HARD" },
  "estimated_duration_minutes": 55,
  "exercises": [
    { "exercise_id": "...", "type": "EASY_HARMONIC", "stage": "WARM_UP", "difficulty": "EASY",
      "source_track": {...}, "target_track": {...}, "metrics": {...},
      "instructions": [...], "success_criteria": [...], "reason": "..." },
    "... 4 more, in stage order ..."
  ],
  "coach_summary": "A 5-exercise session running Warm Up -> Technique -> Energy Control -> Crossover -> Challenge, ...",
  "future_feedback_supported": false,
  "generated_at": "2026-09-15T...",
  "error": null
}
```

`GET /api/dj/training/today` (Phase 2) is unmodified and was re-verified
live returning `exercise_count: 5, error: None` after Phase 3's changes.

## Frontend

**Route:** `/dj` (`frontend-react/src/App.jsx`), added inside the existing
`<Layout>` route group alongside every other page — no new layout, no
special-cased routing.

**Page:** `frontend-react/src/pages/DjCoach.jsx`. Fetches
`GET /api/dj/coach/today` on mount via a new `api.getDjCoachToday()`
method (`services/api.js`), with loading/error/empty states matching this
project's existing page conventions (shimmer skeletons, `Card`/`Badge`
from `components/ui`). Reuses existing, purpose-built display components
rather than reinventing them: `BPMDisplay` (BPM + key + clickable Camelot
wheel), `GenreBadge`, `ConfidenceBar` (repurposed for `key_confidence`).
Displays: session title, objective description/focus/skills, overall
difficulty badge + score + progression, estimated duration, and all 5
exercises — each showing its stage, difficulty, source/target track
(title, artist, BPM, Camelot, key confidence, energy, genre, DJ-mix flag
via `length_class`), the transition metrics, instructions, and success
criteria. No live VirtualDJ interface, no fake real-time data, no fake
feedback UI — exactly as scoped.

**Navigation:** added a "DJ Coach" entry to `Sidebar.jsx`'s existing
`navItems` array (a `Headphones` icon), matching the existing nav pattern
exactly — no new UI framework or pattern introduced.

## Startup integration — `start.bat`

**`start.bat` was not modified**, and reconnaissance proved this is
correct, not merely convenient:

- Step 1 (`npm run build`) runs a full Vite production build of the
  *entire* `frontend-react/src` tree into `frontend-react/dist/` — the new
  `/dj` route and `DjCoach.jsx` page are automatically included the moment
  they exist in `src/`, with no build-config change needed. Verified: `npm
  run build` completed successfully or (`✓ built in 4.88s`) with the new page included.
- Step 3 (`python app.py`, preferring `.venv\Scripts\python.exe` if
  present) starts Flask, which already serves the SPA via `app.py`'s
  existing catch-all route (`/<path:filename>` -> serves the built static
  file if it exists, else falls back to `dist/index.html` — see
  `app.py:389-396`). Client-side routes like `/dj` therefore work
  automatically: the browser requests `/dj`, Flask serves `index.html`
  (since no literal file `dist/dj` exists), and React Router resolves
  `/dj` to `DjCoach.jsx` client-side. No new Flask static route was needed.
- The new `/api/dj/coach/today` endpoint is just another blueprint
  registered on the same Flask app `start.bat` already starts — no
  separate process, port, or server of any kind.

No `start_dj_coach.bat`, no `run_coach.py`, no separate server was created.

## Start.bat end-to-end verification

`start.bat` itself is an interactive batch script (it launches a blocking
dev server and ends with `pause`), so it was not run as a literal
double-click in this non-interactive environment. Its three steps were
verified individually, in the same order and using the same commands
`start.bat` itself runs:

| Step | Verified | Result |
|---|---|---|
| 1. `npm run build` | Yes — ran the literal command | Success, `dist/` rebuilt including the new page |
| 2. Redis + Celery (optional) | Inspected, not started | `start.bat` already treats these as optional and skips gracefully if `redis-server`/`redis-cli` aren't on PATH — pre-existing behavior, unrelated to Phase 3; not re-verified live in this pass |
| 3. `python app.py` (via `.venv\Scripts\python.exe`, matching `start.bat`'s own interpreter preference) | Yes — ran the literal command | Flask started successfully: `Running on http://127.0.0.1:5000` |
| Application loads | Yes | `GET /` -> `200`; `GET /dj` -> `200` (SPA fallback serving `index.html`) |
| DJ Coach endpoint available | Yes | `GET /api/dj/coach/today` -> `200`, exactly 5 exercises, deterministic across two calls |
| DJ Coach frontend page available | Yes (route-level) | `/dj` resolves through Flask's existing SPA fallback; the page's own React rendering was not screenshotted in this pass (no browser session available in this environment), but the identical data contract it consumes was confirmed correct end-to-end via the live API |
| No additional DJ Coach process required | Yes | One Flask process (Werkzeug's debug reloader spawns one child, standard Flask behavior, not a Phase 3 addition) served both `/api/dj/training/today` and `/api/dj/coach/today` |

**A pre-existing, unrelated issue was observed and left untouched**: the
Telegram bot integration failed to authenticate (`InvalidToken`) during
this startup — an existing configuration issue in `.env`, not caused by or
related to Phase 3, and not something this phase's scope covers fixing.
The Flask HTTP server and both DJ endpoints were unaffected by it.

**Conclusion: double-clicking `start.bat` is sufficient.** No manual step,
no second terminal, and no additional command are needed to make DJ Coach
available.

## Manual API test (live server, real library)

```
GET http://127.0.0.1:5000/api/dj/coach/today
```

- **HTTP 200**, valid JSON
- Exactly 5 exercises, correct type order, correct stage order
- Called twice back-to-back: `exercises`, `title`, `objective`, and
  `difficulty` were **byte-identical**; only `generated_at` differed
- All 10 source/target tracks across the 5 exercises were **distinct**
  (0 duplicates)

## Real library validation

Current live database state, discovered at verification time (not
hard-coded from the Phase 2 baseline, which was 2,164/2,090 — the library
grew slightly from normal use between sessions):

- **Total documents:** 2,303
- **Live documents:** 2,171
- **BPM + Camelot scoreable:** 2,096

The 5 generated exercises for 2026-09-15 (identical across repeated calls):

1. **WARM_UP / EASY_HARMONIC** (EASY) — *Kadi Te Has Bol* (Atif Aslam, 107 BPM, 7A) → *Kaun Tujhe (Armaan Malik Version)* (Indian, 108 BPM, 7A). Same key, 1 BPM apart. difficulty_score 0.1304.
2. **TECHNIQUE / BPM_TRANSITION** (MEDIUM) — *Namaqua* (Daniel Rateuke, 123 BPM, 6A) → *Laal Pari (From "Housefull 5")* (Yo Yo Honey Singh, 115 BPM, 6A). Same key, 8 BPM gap. difficulty_score 0.2308.
3. **ENERGY_CONTROL / ENERGY_TRANSITION** (MEDIUM) — *Dis Badman* (Sammy Virji, energy 0.2227) → *Baller* (Shubh, energy 0.1552). RELEASE. difficulty_score 0.2149.
4. **CROSSOVER / GENRE_CROSSOVER** (MEDIUM) — *UNDRGRND DRIFT* (Library/Electronic, 160 BPM, 4A) → *Fallin'* (Library/Drum & Bass, 160 BPM, 4B). Adjacent key, 0 BPM apart. difficulty_score 0.2566.
5. **CHALLENGE / CHALLENGE** (HARD) — *Widdershins* (Ajja, 74 BPM, 3B) → *Pavitra Dhvani* (Albela, 99 BPM, 7A). 25 BPM gap (exactly at the engine's soft-reject ceiling, never exceeding it), incompatible key, genre change (Electronic → Bollywood). difficulty_score **0.8762**.

**Musical sanity check (Section 20 requirement):** every transition was
manually inspected. None looked nonsensical. The CHALLENGE pairing (25 BPM
gap + incompatible key + genre change) is the *intended* shape of that
exercise type, not a bug — `training_service.py`'s `_build_challenge()`
never exceeds `_BPM_JUMP_SOFT` (25) and requires `similarity_score >= 0.15`
specifically to prevent an "intentionally bad" pairing; 25 BPM is the
ceiling, not an overshoot. Overall difficulty progression was confirmed
genuinely `EASY_TO_HARD` (0.1304 → 0.8762, monotonic-enough by the honest
check above) — no artificial inflation.

**Objective for this session:** focus = "genre crossover" (GENRE_CROSSOVER's
0.2566 was the highest difficulty_score among the four non-CHALLENGE
exercises) → title "Genre Bridge Session", objective title "Bridge Genres
Confidently". Difficulty: score 0.3418, label INTERMEDIATE, progression
EASY_TO_HARD. Estimated duration: 55 minutes (all 5 types present).

## Tests

`backend/tests/test_dj_coach_service.py` — **25 tests, all passing**,
covering every item in Phase 3's spec (exactly 5 exercises, all 5 types,
correct order, correct stage labels, determinism across same-date calls,
different-date behavior, `generated_at` independence, deterministic
title, objective reflects actual exercises, difficulty derived from actual
scores, correct difficulty label, correct estimated duration, instructions
and success criteria present on every exercise, no automated-success-claim
language, unknown-artist handling preserved, DJ-mix `length_class`
preserved, insufficient-pool graceful failure, zero MongoDB writes, zero
filesystem/download side effects, stable identifiers present).

Written **before** `dj_coach_service.py` existed and confirmed to fail
with `ModuleNotFoundError` first (TDD, feature-level granularity, same
approach as Phase 2), then the service was implemented to make all 25 pass
on the first attempt.

**Full suite: 236/236 pass** (211 pre-Phase-3 + 25 new — zero existing
tests modified or weakened). `py_compile` clean across every backend `.py`
file, including all four modified/created route-wiring files.

## Safety

- Source files modified: **2** (`backend/app.py`, `backend/routes/__init__.py`) — additive only (import + one registration line each)
- Frontend files modified: **3** (`App.jsx`, `Sidebar.jsx`, `api.js`) — additive only (one route, one nav item, one API method)
- Files created: **4** (`dj_coach_service.py`, `routes/dj_coach.py`, `test_dj_coach_service.py`, `pages/DjCoach.jsx`)
- `start.bat`: **untouched**
- Documentation created: **1** (this file)
- MongoDB writes: **0**
- Physical file changes: **0**
- Downloads: **0**
- ID3 tag modifications: **0**
- `recommendation_service.py` changes: **0**
- `training_service.py` changes: **0**
- `downloader_service.py` / `auto_downloader.py` / `organizer_service.py` / `strict_matcher.py` / `library_migrator.py` / `master_organise.py` / `reconcile_library_state.py` / genre taxonomy: **0 changes**

Mongo document counts before and after the entire Phase 3 session (server
start, repeated live API calls, shutdown) were identical (2,303 total /
2,171 live / 2,096 scoreable both times) — confirmed via direct read-only
queries, not inferred.

## Regressions

- `recommendation_service.py`: 0 changes (imported transitively, unmodified)
- `GET /api/dj/training/today` (Phase 2 API): confirmed still returns `200`, `exercise_count: 5`, `error: null` on the live server after all Phase 3 changes

## Remaining limitations

- The DJ Coach page's own React rendering (as opposed to the API contract
  it consumes, which was fully verified) was not visually screenshotted in
  this environment — no interactive browser session was available during
  this verification pass. The data shape it renders was confirmed correct
  end-to-end via the live API.
- Redis/Celery were not started or verified live in this pass (`start.bat`
  already treats them as optional and this behavior predates Phase 3);
  Phase 3 does not depend on either.
- The pre-existing Telegram bot `InvalidToken` error surfaced during
  startup is unrelated to Phase 3 and was left untouched, per scope.
- `objective.focus`'s "exclude CHALLENGE from the max" rule is a
  documented design decision, not something the Phase 3 spec stated
  explicitly — flagged here for visibility rather than left implicit.

## Phase 4 readiness

**READY FOR PHASE 4.** Phase 3's stable identifiers
(`session_id`/`exercise_id`/`identity_key`/`type`/`difficulty`), the
`future_feedback_supported: false` flag, and the untouched Phase 2
contract underneath give a clean foundation for whatever Phase 4 adds,
without requiring changes to `recommendation_service.py`,
`training_service.py`, or the Phase 3 coaching contract itself.
