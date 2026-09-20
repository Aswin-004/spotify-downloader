# Phase 4 — Trend Intelligence (Design Only)

**Date:** 2026-09-18
**Mode:** Design only. No code, Mongo writes, file changes, or downloads performed. This document is the only artifact produced.
**Depends on (read first):** [PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md](PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md), [PHASE_1G_FINAL_POST_REMEDIATION_AUDIT.md](PHASE_1G_FINAL_POST_REMEDIATION_AUDIT.md), [PHASE_3_1_ACTIONABLE_MIX_PLAN.md](PHASE_3_1_ACTIONABLE_MIX_PLAN.md)
**Status:** Phase 3.1 is frozen. This document does not modify it.

---

## 1. Current architecture

```
LOCAL LIBRARY (library_index, Mongo)
    |
TRACK INTELLIGENCE (bpm_key_service, genre_router, audio_features)
    |
DETERMINISTIC RECOMMENDATION (recommendation_service.py)
    |
TRAINING ENGINE (training_service.py — 5 daily exercises, seeded by date)
    |
DAILY DJ COACH (dj_coach_service.py — stage/objective/instructions wrapper)
    |
ACTIONABLE MIX PLAN (Phase 3.1 — build_mix_plan(), structured deck-by-deck steps)
```

Concretely, as read from the repository:

- **`recommendation_service.py`** — pure deterministic scoring over `library_index` documents. `get_candidate_pool()` / `find_similar_tracks()` / `recommend_next()` / `generate_playlist_sequence()`. Weighted similarity (`_SIM_WEIGHTS = {camelot: 0.40, bpm: 0.30, energy: 0.20, spectral: 0.10}`) plus a confidence-aware variant (`_compute_similarity_confidence_aware`). Identifies tracks exclusively by `identity_key`. No network calls, no external data, no ML.
- **`training_service.py`** — `generate_daily_session(date_str, docs)` builds exactly 5 exercises (EASY_HARMONIC, BPM_TRANSITION, ENERGY_TRANSITION, GENRE_CROSSOVER, CHALLENGE) from `recommendation_service`'s pool, seeded by `_daily_seed(date_str, pool)` (sha256 of date + sorted identity-key fingerprint — never wall-clock, never external data). `_format_track()` emits only fields already in `library_index` (`bpm`, `camelot`, `key_confidence`, `energy`, `genre`, `length_class`).
- **`dj_coach_service.py`** — orchestration wrapper (`generate_daily_coaching_session`) adding `stage`/`objective`/`difficulty`/`duration`/`instructions`/`success_criteria`/`mix_plan` (Phase 3.1) per exercise. Never re-queries Mongo directly, never recomputes scoring.
- **`routes/`** — one blueprint per concern, each a thin pass-through to its service (`training.py` → `training_service`, `dj_coach.py` → `dj_coach_service`, `analytics.py` → `analytics_service`, `genre.py`, `notifications.py`, `settings.py`, `system.py`, `library.py`). All registered in `routes/__init__.py`.
- **`database.py`** — single Mongo singleton (`_get_db()`), collections: `library_index` (canonical library, unique index on `identity_key`, sparse index on `spotify_id` — **not unique**, see §9), `artist_memory`, `musicbrainz_cache` (30-day TTL via `expireAfterSeconds`), `tagging_failures`, `download_history`, `custom_folder_mappings`.
- **Existing external-API integrations** (the precedent this design follows): `spotify_service.py` (OAuth/Client-Credentials, request throttle, 429 backoff + cooldown, `metadata_cache` JSON-file cache), `lastfm_service.py` (API-key-gated, in-memory 30-day TTL cache, 4 req/s throttle, graceful `{}`/`""` on any failure), `musicbrainz_service.py` (no-key search mode + optional AcoustID fingerprint mode, 1 req/s throttle).
- **Existing enrichment pattern**: `lastfm_service.enrich_from_lastfm()` already fetches a `listeners` count per track (currently used only as a mismatch-rejection guard, not surfaced anywhere) — closest existing precedent to a "popularity" signal.
- **Existing background-refresh pattern**: `maintenance_worker.py` — a daemon thread running a fixed task list, each task with its own cooldown and exponential backoff on failure, env-gated (`MAINTENANCE_ENABLED`), started once at app boot via `start_maintenance()`.
- **Existing analytics surface**: `analytics_service.py` / `routes/analytics.py` — read-only stat endpoints, direct Mongo aggregation, no caching layer of its own.
- **Frontend**: `frontend-react/src/pages/DjCoach.jsx` — `ExerciseCard` renders `TrackSide` (source/target) then `MixPlanSection` (Phase 3.1's primary, single workflow). `frontend-react/src/services/api.js` — one method per endpoint, `apiFetch()` + `handleResponse()`, `VITE_API_BASE_URL` prefix for prod.
- **Identity** (the load-bearing fact for this whole design, see §9): `services/dedup_service.py::duplicate_identity_key()` defines the canonical scheme — `sp:{spotify_id}` if a Spotify ID is present, else `ch:{sha256(normalized_title|normalized_artist|duration_bucket)[:16]}`. [PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md](PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md) documents 10 real collision groups where this Spotify ID was wrong or shared by unrelated tracks — proof that `spotify_id` is not a safe global key on its own.

## 2. Problem

The DJ Coach currently reasons only about the local library's own audio-feature metadata (BPM, Camelot, energy, genre). It has no sense of which of two harmonically-compatible tracks is a widely-recognized current record versus a deep cut, whether a track is rising, or whether it currently shows up on any external chart. A DJ practicing a transition has no way to know, from inside the app, whether either track has any current relevance outside the local library.

## 3. Goals

1. Attach optional, clearly-sourced **external context** ("this track is currently popular on Spotify, last checked 3 days ago") to tracks already selected by the existing recommendation/training engines.
2. Make that context **explain**, not **decide** — it never changes which tracks get paired or how exercises are built.
3. Keep the **core app fully functional with zero external network access** — Phase 4 must be strictly additive and non-blocking.
4. Establish a **conservative, explicit identity-resolution layer** between external tracks and local `identity_key`s that never guesses past the point of confidence, informed directly by the Phase 1F/1G Spotify-ID collision history.
5. Reuse this project's own established patterns (rate limiting, TTL caching, graceful degradation, background-worker refresh) rather than inventing new ones.

## 4. Non-goals (this phase)

- No change to `recommendation_service.py`'s scoring, weights, or candidate selection.
- No change to `training_service.py`'s exercise-building or seeding.
- No change to `dj_coach_service.py`'s existing `mix_plan` construction (Phase 3.1 stays frozen).
- No ML, no LLM, no VirtualDJ integration.
- No automatic repair of local identity data from external signals.
- No implementation — this phase produces a document only.

## 5. Definition of trend

For a personal DJ-practice tool, "trend" is not a marketing/chart-vanity concept — it should answer one question for the DJ: **"is this track something a crowd is likely to already know, or likely to be hearing as new/rising, right now?"** That reframes the candidate signals below: raw all-time popularity, current momentum, and DJ-specific chart presence are three genuinely different things, and the design keeps them labeled separately rather than folding them into one number (see §12).

## 6. Candidate trend signals

| Signal | Classification | Basis |
|---|---|---|
| **Track popularity (Spotify `popularity` 0–100)** | **AVAILABLE NOW** | Returned on `GET /v1/tracks/{id}`, the same endpoint `spotify_service.get_track_metadata()` already calls under the app's existing Client-Credentials auth. Confirmed live against Spotify's current API reference (2026-09-18): field exists, 0–100, "may lag actual popularity by a few days... not updated in real time." `diagnose_audio_features()` (already in this codebase) independently confirms `GET /v1/tracks` is unaffected by the Nov-2024 app-token restriction that broke `audio-features`. Zero new auth, zero new secret. |
| **Recency (release date proximity)** | **AVAILABLE NOW** | `release_date` is already fetched by `spotify_service.get_playlist_tracks_by_id()` and used as a MusicBrainz-miss fallback (see comment at `spotify_service.py:414-418`). A "how new is this" computation needs no external call at all once a track has a `release_date`. |
| **Community listener count (Last.fm `listeners`)** | **AVAILABLE NOW (weak signal)** | Already fetched by `lastfm_service.enrich_from_lastfm()` and currently discarded after use as a mismatch guard. All-time cumulative, not time-windowed — a popularity floor, not a momentum signal. |
| **Momentum / recent growth** | **POSSIBLE WITH LEGITIMATE API** (self-built) | No source in scope exposes "velocity" directly and reliably for free. Buildable in-house by sampling Spotify `popularity` (and/or Last.fm `listeners`) on a fixed cadence and diffing our own historical snapshots (§11-§14) — i.e. momentum becomes a **derived** signal from data this design already collects, not a new external source. |
| **Chart / playlist presence (Spotify editorial charts, Featured Playlists, Categories)** | **NOT RELIABLY AVAILABLE** | Spotify deprecated most browse/discovery endpoints (Featured Playlists, Categories, Recommendations, Related Artists) for apps approved after Nov 2024 — the same API-access wave that broke `audio-features` (per this project's own `diagnose_audio_features` finding). Needs a live verification spike before any implementation relies on it (see §24). |
| **Artist momentum** | **POSSIBLE WITH LEGITIMATE API** (derived) | Same mechanism as track momentum, aggregated by artist, once track-level snapshots exist. No local `artist_id` (Spotify) is currently stored anywhere — would need fuzzy artist search, the same class of risk as `backfill_fix_artist_tags.py::_search_spotify` (unscored, name-only search). Treat as a later increment, not MVP. |
| **Genre momentum** | **POSSIBLE WITH LEGITIMATE API** (derived, local-only) | Aggregate already-collected track-level trend data by the library's own `genre_folder` taxonomy. No new external call. |
| **DJ-specific relevance (Beatport-style charts)** | **NOT RELIABLY AVAILABLE** | Beatport does not offer self-serve public API access (partner/label agreements only, per current knowledge — not independently verified live in this session, see §24). Scraping Beatport or 1001Tracklists is explicitly out of scope (spec forbids scraping restricted/private data) and those charts are not "private," but they are not an open API either — do not build against an unsupported access path. |
| **Video view count (YouTube)** | **POSSIBLE WITH LEGITIMATE API** | YouTube Data API v3 has a free-tier, quota-documented, official `videos.list` endpoint with `statistics.viewCount`. The blocker is identity, not access: this project already downloads via yt-dlp (not the official Data API) and stores no canonical YouTube video ID per track, so a track would need a fresh, unscored video search — same fuzzy-match risk class as the Spotify-ID backfill bug in Phase 1F. Treat as FUTURE, not MVP. |
| **Commercial trend platforms (Chartmetric, Songstats)** | **POSSIBLE WITH LEGITIMATE API** (paid) | Purpose-built for exactly this ("is this track trending, cross-platform, DJ-relevant") but require a paid subscription. Good FUTURE candidate if the user wants to budget for it; not assumed or required for Phase 4 MVP. |
| **SoundCloud plays** | **NOT RELIABLY AVAILABLE** | No `soundcloud_service.py` or structured SoundCloud API integration exists in this codebase (confirmed by search) — SoundCloud downloads here go through the generic multi-source yt-dlp path. SoundCloud's official public API has been effectively closed to new third-party registrations for an extended period (not independently re-verified live this session — flag in §24). |
| **ListenBrainz sitewide/time-windowed listen stats** | **POSSIBLE WITH LEGITIMATE API** | Free, open, MusicBrainz-affiliated, no API key required for public read stats, and — unlike Spotify `popularity` — genuinely time-windowed (e.g. "top recordings this week") rather than a single opaque score. Matches this project's existing MBID-oriented enrichment (`musicbrainz_service.py`) well. Not independently re-verified live this session (endpoint shape may have changed since training data) — flag as a verification-spike item in §24, not assumed working. |

## 7. Legitimate external sources

### 7.1 Spotify Web API (recommended MVP source)

- **Available data:** `popularity` (0–100, track-level), `release_date`, artist `popularity` (if artist lookup added later).
- **Track identifiers:** Spotify track ID.
- **Artist identifiers:** Spotify artist ID (not currently stored locally).
- **Authentication:** already configured (`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`, Client-Credentials flow, `spotify_service.py`). No new secret.
- **Rate limits:** governed by Spotify's dynamic per-app limit; this project already has a working throttle + 429 backoff + global cooldown (`spotify_service._call_with_backoff`, `API_THROTTLE = 0.35s`). Reuse directly — do not build a second limiter.
- **Cost:** free (standard Developer tier, already in use).
- **Freshness:** Spotify's own docs state `popularity` "may lag actual popularity by a few days... not updated in real time." A daily-to-weekly refresh cadence is a good fit, not a bottleneck.
- **Reliability:** high — same endpoint (`GET /v1/tracks`) already relied on elsewhere in this codebase and confirmed unaffected by the Nov-2024 API restriction that broke `audio-features`.
- **Caching:** must be cached (see §13) — do not call live on every DJ Coach page load.
- **Commercial-use considerations:** Spotify's developer policy (visible on the current API reference page) explicitly states content may not be used to train ML/AI models and downloadable/redistributable use is restricted. This project already stores Spotify metadata locally (via `metadata_cache`, `library_index`) for its own internal, personal, non-redistributive use — a cached numeric popularity score used the same way is consistent with that existing pattern, not a new category of risk. Re-confirm current Developer Terms before shipping (see §24) — terms can change independent of this document.
- **Local persistence:** permitted for internal app functionality (matches existing practice).

### 7.2 Last.fm API (already integrated, extend rather than re-integrate)

- **Available data:** `listeners` (already fetched, currently discarded after its guard use), `playcount` (present in the same `track.getInfo` response, not currently extracted), `chart.getTopTracks` / `tag.getTopTracks` (unused, scoped "currently popular" lists, not time-windowed momentum).
- **Track identifiers:** MBID (preferred) or artist+title (fuzzy).
- **Authentication:** already configured (`LASTFM_API_KEY`).
- **Rate limits:** Last.fm's published limit is ~5 req/s; this project's existing `_ENRICH_INTERVAL = 0.25s` (4 req/s) already respects it.
- **Cost:** free.
- **Freshness:** cumulative counters, not real-time; no defined update cadence from Last.fm — treat as a weak, slow-changing background signal, not a momentum source on its own.
- **Reliability:** already production-tested in this codebase (30-day in-memory TTL cache, existing guard logic against wrong-artist mismatches at `<30` listeners).
- **Caching:** existing in-memory cache is per-process only (lost on restart) — Phase 4's own persistent Mongo cache (§13) should be the source of truth if this signal is reused, not the existing in-memory one.
- **Commercial-use considerations:** Last.fm's API terms permit this kind of internal display use; no redistribution planned.
- **Local persistence:** already permitted and practiced.

### 7.3 ListenBrainz (candidate second/future source)

- **Available data:** open, MusicBrainz-affiliated listen statistics, time-windowed (per public documentation as of training data — **not independently re-verified live this session**).
- **Track identifiers:** MBID.
- **Authentication:** none required for public read stats.
- **Rate limits:** informal/generous, no key needed.
- **Cost:** free, non-profit/open project.
- **Freshness:** genuinely time-windowed (weekly/monthly stats), the closest fit in this list to real "momentum" without building it ourselves.
- **Reliability:** unverified in this session — requires a short verification spike (§24) before it can move from "candidate" to "planned."
- **Caching / persistence:** same pattern as Spotify/Last.fm.

### 7.4 Explicitly ruled out or deferred

| Source | Why |
|---|---|
| Beatport | No self-serve public API (partner-only, unverified live). Do not scrape. |
| 1001Tracklists | No public API; scraping would be the only path — out of scope. |
| SoundCloud official API | No integration exists in this codebase; third-party registration has been effectively closed for an extended period (unverified live this session). |
| YouTube Data API | Real API exists, but this project has no stored canonical YouTube video ID per track — identity risk, not access risk. FUTURE. |
| Chartmetric / Songstats | Real, purpose-built APIs — but paid. FUTURE, budget-gated, not assumed. |

## 8. Source comparison (summary)

| Source | Access | Cost | Identity anchor | Time-windowed? | MVP fit |
|---|---|---|---|---|---|
| Spotify `popularity` | Existing auth | Free | `spotify_id` (unreliable alone, see §9) | No (single rolling score) | **Yes — primary** |
| Last.fm `listeners`/`playcount` | Existing auth | Free | MBID or artist+title | No | Secondary, reuse existing integration |
| ListenBrainz stats | Open, no key | Free | MBID | Yes | Candidate — needs verification spike |
| YouTube views | New key | Free tier | none stored locally | No | Future |
| Chartmetric/Songstats | Paid | $$ | multi-platform | Yes | Future, budget-gated |
| Beatport / 1001Tracklists | None (self-serve) | N/A | N/A | N/A | Not viable |

## 9. Local identity mapping

**Hard rule carried over from Phase 1F/1G:** `spotify_id` is not a safe unique key on its own. `database.py`'s own schema agrees — `library_index`'s index on `spotify_id` is `sparse`, **not** `unique` (only `identity_key` is unique). Phase 1F found 10 real collision groups where one Spotify ID was shared by unrelated physical tracks, traced to an unguarded, zero-confidence-check backfill (`backfill_gemini.py::pass6_backfill_spotify_id`). Trend ingestion must not repeat that mistake in reverse — attaching external trend data to the wrong local track based on a blind ID match would reintroduce the exact same failure class in a new subsystem.

```
EXTERNAL TRACK (Spotify search result, Last.fm track.getInfo result, ...)
      |
IDENTITY RESOLUTION  (never automatic on low confidence)
      |
LOCAL TRACK (identity_key: "sp:..." | "ch:..." | legacy "file:...")
      |
TREND SIGNAL  (attached only when resolution status = MATCHED)
```

Resolution logic, using data already in `library_index` — no new field required on that collection:

1. **Exact local identity (`MATCHED`)** — the external source returns a Spotify track ID, and exactly one `library_index` document has that `spotify_id` **and** its `identity_key` is `sp:{that id}` (i.e. the local record's own canonical identity agrees, not just an incidentally-stored field). This double-check is deliberate: it rejects the Phase 1F failure mode, where a `spotify_id` field could be populated on a record whose `identity_key` was actually derived via `ch:`/`file:` fallback (meaning the ID was written after the fact, unverified) or where `spotify_id` is present but shared by more than one record.
2. **Missing identity (`UNMATCHED`)** — no local record matches (external track legitimately not in the library, or the local record only has a `ch:`/`file:` identity and no reliable Spotify ID to compare). No signal is stored against any local track; the fetched data may still be cached for its own sake (§11) in case the identity resolves later.
3. **Multiple candidates (`AMBIGUOUS`)** — more than one local record's `identity_key` maps to the same external reference (this is exactly the shape of the Phase 1F collision groups). Do not attach to any of them. Record all candidate `identity_key`s for visibility/debugging only.
4. **Conflicting identity (`CONFLICT`)** — a local record has a `spotify_id` field, but the title/artist Spotify itself returns for that ID is materially different from the local record's own title/artist (the Beba/Pepas pattern — clean tags, wrong shared ID). Do not attach. This state exists specifically to catch drift that the simpler `MATCHED` check might otherwise accept.
5. **External track not present locally** — same as `UNMATCHED`; the external catalog is much larger than this library by definition, and that is expected, not an error.
6. **External identity conflicting with local metadata** — same handling as `CONFLICT`.

**Trend ingestion must never write to `library_index`, never modify `spotify_id`, `identity_key`, or any ID3/Spotify identity field, and never attempt to "fix" an `AMBIGUOUS`/`CONFLICT` record.** Those states are informational only in this phase. (A future phase could feed `CONFLICT` records into the existing Phase 1-style remediation workflow as a *lead*, but that is explicitly deferred — §26.)

## 10. Ambiguity / conflict handling (state table)

| State | Meaning | Trend signal attached? | Stored for visibility? |
|---|---|---|---|
| `MATCHED` | Exactly one local record, identity self-consistent | Yes | — |
| `AMBIGUOUS` | ≥2 local records map to the same external reference | No | Yes, with candidate list |
| `UNMATCHED` | No local record found | No | Optional (raw signal only, unattached) |
| `CONFLICT` | Local record's own metadata disagrees with the external source's metadata for the same ID | No | Yes, with conflict reason |

## 11. Trend data model

Two collections, deliberately separated so an identity mistake can never silently corrupt raw source data, and so "what did the source say" is always distinguishable from "what did we conclude":

**Raw + derived signal (no identity_key here at all — decoupled from local library on purpose):**

```jsonc
// trend_signals — one document per (source, external_ref)
{
  "source": "spotify",                 // "spotify" | "lastfm" | "listenbrainz" | ...
  "external_ref": {
    "type": "spotify_track_id",
    "value": "4iV5W9uYEdYUVa79Axb7Rh"
  },
  "artist_name": "...",                // as returned by source — matching aid only, not canonical
  "track_title": "...",                // as returned by source — matching aid only, not canonical
  "raw": {                             // only the subset of the source payload actually used
    "popularity": 63
  },
  "derived": {
    "popularity": 63,                  // normalized 0-100 where applicable
    "observed_at": "2026-09-18T12:00:00Z"
  },
  "fetch_status": "ok",                // "ok" | "not_found" | "error"
  "fetched_at": "2026-09-18T12:00:00Z",
  "expires_at": "2026-09-25T12:00:00Z" // TTL index, same pattern as musicbrainz_cache
}
```

**Identity resolution (the only place `identity_key` appears — the deliberate join point from §9):**

```jsonc
// trend_identity_links — one document per (source, external_ref)
{
  "source": "spotify",
  "external_ref": { "type": "spotify_track_id", "value": "4iV5W9uYEdYUVa79Axb7Rh" },
  "identity_key": "sp:4iV5W9uYEdYUVa79Axb7Rh",  // present only when status = MATCHED
  "status": "MATCHED",                           // MATCHED | AMBIGUOUS | UNMATCHED | CONFLICT
  "candidates": [],                              // populated only for AMBIGUOUS
  "conflict_reason": null,                        // populated only for CONFLICT
  "resolution_method": "spotify_id_self_consistent",
  "resolved_at": "2026-09-18T12:00:00Z"
}
```

Momentum, if ever built (§6), is a **derived** field computed by comparing two or more `trend_signals` documents for the same `external_ref` over time (or a small `trend_signals_history` append-only collection, capped/TTL'd) — never a new external call in itself.

## 12. Trend score decision

**Decision: do not build a composite `trend_score` in this phase.** The candidate inputs (Spotify `popularity`, Last.fm `listeners`) measure different things (recent-weighted streaming popularity vs. cumulative scrobble count) and folding them into one opaque number would recreate exactly the kind of unexplainable score this project has otherwise avoided (`recommendation_service.py`'s own similarity score is deliberately weighted and documented field-by-field, not a black box). Surface each source's own number, labeled by source, instead of a synthetic blend.

If a composite score is proposed later, it must satisfy all of:
- **Inputs:** explicit, named list (e.g. `[spotify_popularity, lastfm_listeners_normalized]`).
- **Normalization:** documented, deterministic (e.g. min-max against the same candidate pool, mirroring `recommendation_service._normalize_energy_pool`'s existing precedent).
- **Formula:** fixed weights, checked into code and this doc, no hidden tuning.
- **Freshness handling:** a stale or missing input must degrade the score transparently (e.g. drop that term, don't silently zero it).
- **Source weighting:** justified in writing, not just chosen by feel.
- **Explainability:** the API response must be able to show "why" (which inputs, what raw values) alongside the number — never just the number.
- **No ML**, per the phase-wide constraint.

## 13. Cache strategy

Modeled directly on `musicbrainz_cache`'s existing pattern in `database.py` (`cached_at` field + `expireAfterSeconds` TTL index) rather than `lastfm_service`'s in-memory dict (which does not survive a process restart and is unsuitable for a signal meant to inform Coach sessions across restarts):

- `trend_signals` documents carry `expires_at`; a Mongo TTL index (`expireAfterSeconds: 0` on `expires_at`, i.e. "expire at the stored time" — the same idiom `musicbrainz_cache` already nearly achieves with a fixed 30-day window) expires stale rows automatically.
- Reads are **always cache-only**. `GET /api/trend/track/<identity_key>` (§17) never makes a live external call inline with a request — it reads `trend_identity_links` → `trend_signals` and returns what's there (possibly nothing). This is the mechanism that guarantees external-API latency/outages can never slow down or block a DJ Coach page load.
- Writes (live fetches) happen only from the background refresh worker (§14), the same separation `maintenance_worker.py` already establishes between "read fast path" and "slow background repair path."

## 14. Refresh strategy

Reuse `maintenance_worker.py`'s existing `_Task` pattern (name, fn, cooldown, exponential backoff on failure) rather than inventing a second scheduler. Two reasonable implementation shapes, both consistent with the existing codebase — left as an open question for the implementation phase, not decided here:

- **(a)** Add a new task to the existing `maintenance_worker.py` task list (least new code, but couples trend refresh's failure modes to library-repair tasks).
- **(b)** A small, separate daemon thread copying the same `_Task` dataclass shape (more isolation — a trend-source outage can never affect fingerprinting/reconcile tasks or vice versa).

Recommended: **(b)**, gated by its own `TREND_INTELLIGENCE_ENABLED` env flag (default off until validated), independent of `MAINTENANCE_ENABLED`. Refresh cadence: a stale-then-refresh model — Spotify `popularity` itself only updates "every few days" per Spotify's own docs, so refreshing already-cached, still-fresh rows more than roughly weekly would be wasted quota. Suggested defaults (to be tuned during implementation, not fixed here): refresh a given `(source, external_ref)` if `now - fetched_at > 7 days`; hard cache TTL (row deleted if untouched) `30 days`, matching `musicbrainz_cache`'s existing precedent exactly.

## 15. Offline / failure behavior

Non-negotiable, per the phase-wide constraint and mirroring `lastfm_service`'s existing graceful-degradation pattern (`{}`/`""` on any failure, never an exception surfaced to callers):

- **Internet unavailable / API unavailable / timeout:** background task's own try/except catches it, logs, applies its own backoff (`maintenance_worker`-style), and does nothing else. No caller-visible error.
- **API key missing:** `is_available()`-style check (same shape as `lastfm_service.is_available()` / `musicbrainz_service.is_available()`) short-circuits before any network call; `/api/trend/status` reports the source as disabled, not erroring.
- **Source returns no result:** stored as `fetch_status: "not_found"`, not retried on every cycle (respects the cooldown).
- **Trend data stale:** served anyway, with its own `observed_at`/`fetched_at` timestamps in the response so the frontend can show "last checked N days ago" — staleness is disclosed, never hidden, matching the MixPlan's own `limitations` disclosure pattern from Phase 3.1.
- **Trend data entirely absent for a track:** `GET /api/trend/track/<identity_key>` returns an explicit empty/`null` shape, not a 404 or error — the DJ Coach UI must treat "no trend data" as a normal, expected, majority-case state (most of a personal library will have thin or no external footprint).
- Library, recommendations, training, DJ Coach, and MixPlan generation **must never import a live network call from `trend_service.py` into their own request path** — they may read cached trend context (via the same cache-only accessor `/api/trend/track` uses) wrapped in try/except, exactly as `dj_coach_service.py` would call it (§20).

## 16. MongoDB proposal (not created — design only)

| Collection | Purpose | Key index | Notes |
|---|---|---|---|
| `trend_signals` | Raw + derived per-source signal | `(source, external_ref.value)` unique compound; `expires_at` TTL | No `identity_key` field — decoupled from local identity by design (§11) |
| `trend_identity_links` | External→local resolution outcome | `(source, external_ref.value)` unique compound; `identity_key` sparse (only set when `MATCHED`) | Never written to by anything except the trend resolution step; never read by `library_index`-writing code |
| `trend_signals_history` *(optional, only if momentum is built later)* | Append-only snapshots for delta computation | `(source, external_ref.value, observed_at)`; TTL on `observed_at` (e.g. 90 days) | Deferred — only needed once §6's momentum signal is actually pursued |

Naming follows the project's existing `idx_<short_name>` index-naming convention (e.g. `idx_trend_sig_ref`, `idx_trend_sig_ttl`, `idx_trend_link_ref`) and the existing `_ensure_indexes()` idempotent-creation pattern in `database.py`. No collection, index, or document would be created by this phase.

## 17. API proposal (design only — nothing implemented)

**`GET /api/trend/track/<identity_key>`**
- **Purpose:** cache-only read of whatever trend context exists for one local track.
- **Request:** path param `identity_key`.
- **Response:** `{"identity_key": "...", "signals": [{"source": "spotify", "popularity": 63, "observed_at": "...", "fetched_at": "..."}], "stale": false}` or `{"identity_key": "...", "signals": []}` when nothing is attached.
- **Validation:** `identity_key` must be non-empty; no existence check against `library_index` required (an unknown key simply returns empty `signals`).
- **Cache behavior:** always cache-only, per §13/§15 — never triggers a live fetch.
- **Failure behavior:** Mongo unavailable → 200 with empty `signals` and a `"degraded": true` flag, not a 500 (matches "core app must keep working").
- **Write/Read:** read-only.

**`GET /api/trend/status`**
- **Purpose:** operational visibility — which sources are enabled, have keys, when they last succeeded, whether they're currently rate-limited. Modeled on `spotify_service.get_api_usage()` / `analytics_service`'s cache-analytics shape.
- **Response:** `{"sources": {"spotify": {"enabled": true, "has_api_key": true, "last_success_at": "...", "cache_size": 412, "rate_limited": false}, "lastfm": {...}}}`.
- **Write/Read:** read-only.

A manual `POST /api/trend/refresh` (operator-triggered re-fetch, bypassing the background cadence) is **not** proposed as required for MVP — flagged as optional, only if useful during implementation/testing, per the instruction to propose endpoints only when necessary.

## 18. Frontend proposal (minimal — no redesign)

- **`/dj` (DjCoach.jsx):** a small, secondary, optional line under each `TrackSide` (source/target) in `ExerciseCard` — rendered only when `trend_context` for that track is non-empty (graceful no-op otherwise, per §15). Example content: *"Trending: 63/100 popularity (Spotify) · checked 3d ago."* Deliberately **not** placed inside `MixPlanSection` — the MixPlan stays the single primary, actionable workflow per Phase 3.1's own frozen decision; trend context is explanatory color, not an instruction step. Every shown signal must answer "why is this useful to the DJ" (§ goal): framed as recognizability context ("a crowd may already know this one" vs. "this is a deeper cut"), never a bare vanity number.
- **`/analytics`:** optional, later — a small "Trend" tile/section surfacing local-library-only aggregates (e.g. how many tracks have any external trend data attached, top-N by cached popularity) reusing the existing `analytics_service` read-only pattern. Not required for MVP; the DJ Coach integration is the higher-value target.
- No new page, no navigation change, no redesign of existing components.

## 19. Recommendation-engine interaction

```
CURRENT TRACK
    |
EXISTING RECOMMENDATION ENGINE (recommendation_service.py — UNCHANGED)
    |
COMPATIBLE CANDIDATES (ranked, exactly as today)
    |
TREND CONTEXT ATTACHED (read-only, post-ranking, best-effort)
    |
DJ COACH
```

`recommendation_service.py` is not imported by, and does not import, `trend_service.py`. Trend context is attached strictly **after** `training_service.generate_daily_session()` has already picked the day's 5 exercises and **after** `dj_coach_service.build_mix_plan()` has already built the MixPlan — a pure decoration step over already-final `source_track`/`target_track` identity keys. If a future phase wants trend-aware *ranking*, it must be a new, explicitly-opt-in code path (e.g. a separate `recommend_next(..., trend_aware=True)` variant or a distinct scoring weight added under its own flag) — never a silent change to the existing deterministic weights. That is FUTURE WORK (§26), not part of this design.

## 20. DJ Coach interaction

`dj_coach_service.generate_daily_coaching_session()` would, after building each exercise (including its already-frozen `mix_plan`, untouched), make one additional, best-effort, try/except-wrapped, cache-only call per track:

```python
exercise["trend_context"] = {
    "source_track": get_trend_context(source["identity_key"]),  # None-safe, cache-only
    "target_track": get_trend_context(target["identity_key"]),
}
```

- **Purely additive** to the API contract — every existing field (`instructions`, `success_criteria`, `mix_plan`, etc.) is untouched, so all 303 existing backend tests remain valid without modification.
- If `trend_service` raises, is disabled, or Mongo is unavailable, `trend_context` becomes `{"source_track": null, "target_track": null}` — session generation must never fail or degrade because of this step.
- The MixPlan (`exercise["mix_plan"]`) remains the primary instruction set, exactly as Phase 3.1 established; `trend_context` is a sibling key, not nested inside `mix_plan`, so Phase 3.1's own tested shape is never touched.

## 21. Testing strategy (design only — not implemented)

| Scenario | Proposed test |
|---|---|
| Source unavailable / network down | `test_trend_service_source_unavailable` — background fetch catches, logs, no exception propagates |
| API timeout | `test_trend_service_api_timeout` |
| Missing API key | `test_trend_service_missing_api_key` — `is_available()` returns False, no call attempted |
| Stale data served | `test_trend_service_stale_data_disclosed` — response includes accurate `fetched_at`/staleness flag |
| Cached data | `test_trend_service_cache_hit_no_network_call` |
| Offline operation | `test_trend_track_endpoint_works_with_mongo_down` (degraded flag, 200 not 500) |
| Exact identity match | `test_identity_matched_self_consistent` |
| Ambiguous identity | `test_identity_ambiguous_multiple_candidates` |
| Conflicting identity | `test_identity_conflict_metadata_mismatch` |
| Spotify-ID collision (regression) | `test_identity_rejects_known_phase1f_collisions` — reuse the real collision fixtures already captured in `tests/test_spotify_identity_remediation.py` as negative cases |
| Unmatched external track | `test_identity_unmatched_not_in_library` |
| Deterministic derived signal | `test_derived_popularity_field_deterministic_for_same_raw_input` |
| Recommendation independence | `test_recommendation_service_output_identical_with_trend_data_present` — byte-for-byte same ranked order with trend collections populated vs. empty |
| DJ Coach compatibility | `test_dj_coach_mix_plan_unchanged_when_trend_context_added` — Phase 3.1 fixtures still pass verbatim; `trend_context` is present but additive only |

## 22. Migration strategy

Purely additive, no backfill required for MVP:

1. New collections/indexes created idempotently at startup via the existing `_ensure_indexes()` pattern (same function `database.py` already uses) — no migration script, no existing collection touched.
2. `trend_context` on the DJ Coach API response starts appearing only once the feature flag (`TREND_INTELLIGENCE_ENABLED`) is on; with it off, the response is byte-identical to today's.
3. Data populates lazily — first background refresh cycle after the flag is enabled, not a bulk one-time import of the whole library against external APIs (that would burn API quota for tracks that may never appear in a Coach session soon).
4. Rollback is trivial: flip the flag off (or drop the two new collections) — nothing else in the app references them.

## 23. Risks

- **Identity mis-attachment recurrence.** The single biggest risk given this project's own history — attaching trend data to the wrong `identity_key` is the same failure shape as Phase 1F, just in a new subsystem. Mitigated by the conservative MATCHED-only-when-self-consistent rule (§9) and by never writing back to `library_index`.
- **Spotify Developer Terms drift.** Terms can change independent of this document; re-confirm before implementation (§24).
- **Over-trust by the user.** A displayed "popularity" number could be read as more authoritative than it is (lags days, US/global-skewed, doesn't reflect DJ/club relevance specifically). Mitigated by labeling every number with its source and by deliberately not building an opaque composite score (§12).
- **External outage blocking the app.** Mitigated structurally — reads are cache-only (§13), writes are background-only (§14), every integration point is try/except-wrapped (§15, §20).
- **Rate-limit exhaustion from over-eager refresh.** Mitigated by TTL + stale-threshold cadence (§14) reusing the existing throttle/backoff each source already has.
- **Scope creep toward trend-aware ranking.** Explicitly fenced off in §19/§26 — must remain a separate, opt-in future decision, not something that creeps in during "just attaching context."
- **Cost creep** if a paid source (Chartmetric/Songstats) is added later without an explicit budget decision.

## 24. Open questions

1. Live-verify current Spotify Developer Terms language on caching/displaying `popularity` before implementation (last confirmed in-session only against the API *reference* page, not the full legal Terms of Service).
2. Live-verify ListenBrainz's current public stats endpoint shape/availability — not independently re-checked in this session.
3. Live-verify whether Spotify's Featured Playlists/Categories/Recommendations endpoints are truly inaccessible for this app's registration tier, or only for apps approved after a certain date — the exact cutoff/grandfathering rules were not re-verified live.
4. Is a DJ-specific chart signal (Beatport-class) important enough to the user to justify pursuing a paid partner/API relationship, or is general popularity context suf- ficient?
5. Should `trend_context` be shown for both source and target track, or only the target (the track being mixed *in*)?
6. Should genre-momentum / artist-momentum ever be pursued, given both require either aggregation-only local computation (cheap) or new fuzzy-identity work (risky, same class as §9)?
7. What staleness threshold should actually gate a re-fetch — 7 days suggested here, not validated against real usage patterns yet.
8. Should `CONFLICT` records ever feed into a *human-reviewed* Phase-1-style remediation queue later? (Explicitly not automatic, but flagged as a plausible, separate future use of data this phase would already be collecting.)

## 25. Implementation order (if/when this phase is authorized)

1. Mongo schema + indexes only (`trend_signals`, `trend_identity_links`) — no data yet, behind `TREND_INTELLIGENCE_ENABLED=0` default.
2. `trend_service.py` — Spotify `popularity` only (reuses existing auth/throttle), single source, no ListenBrainz/Last.fm yet.
3. Identity resolution logic + the four-state machine, tested against the real Phase 1F collision fixtures as regression cases before anything else ships.
4. `GET /api/trend/track/<identity_key>` and `GET /api/trend/status`, cache-only, behind the flag.
5. `dj_coach_service.py` additive `trend_context` attachment (try/except-wrapped) + minimal `DjCoach.jsx` secondary display.
6. Background refresh worker (§14 option (b)), TTL cache expiry, full offline/failure-path hardening and tests.
7. Second source (Last.fm `listeners`/`playcount` reuse, or ListenBrainz pending §24 verification) — only once the first source has run stably.
8. Revisit `trend_score` composite (§12) only if real usage shows a genuine need — not scheduled by default.
9. `/analytics` surfacing — optional, after DJ Coach integration proves useful in practice.

## 26. Explicitly deferred functionality

- Trend-aware recommendation ranking/scoring (any change to `recommendation_service.py`'s weights or selection logic).
- ML-based trend prediction or forecasting.
- VirtualDJ / Live Copilot trend overlays.
- Beatport or other paid/partner-only DJ-chart integration.
- Artist-level identity resolution (storing/matching Spotify artist IDs).
- `trend_score` composite scoring (§12) — until an explicit, later decision.
- Any write-back or auto-repair of local identity (`spotify_id`, `identity_key`, ID3 tags) from trend/identity-resolution findings.
- Multi-source conflict-resolution voting logic (what to do when Spotify and Last.fm disagree) — only one source is proposed for MVP.
- Historical trend charts/graphs in the UI.
- Notifications or alerts about trending tracks.
- Feeding `CONFLICT` identity states into the Phase-1-style remediation workflow (noted as a plausible future use in §24, not designed here).

---

## Final Safety

**FILES CREATED:** `docs/PHASE_4_TREND_INTELLIGENCE_DESIGN.md`

**FILES MODIFIED:** 0

**CODE CHANGES:** 0

**MONGO WRITES:** 0

**PHYSICAL FILE CHANGES:** 0

**SPOTIFY CHANGES:** 0

**DOWNLOADS:** 0

**OPEN QUESTIONS:** see §24 (8 items — Spotify Terms re-verification, ListenBrainz endpoint re-verification, Spotify browse-endpoint access-tier re-verification, Beatport/paid-source priority, source/target display scope, artist/genre-momentum priority, staleness threshold tuning, CONFLICT→remediation feed-in).

STOP.
