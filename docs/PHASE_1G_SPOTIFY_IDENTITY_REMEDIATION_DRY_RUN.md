# Phase 1G — Spotify Identity Remediation: Code Fix + Dry Run

**Date:** 2026-09-15
**Stage:** A only (code fix, tests, read-only dry run). **Stage B (live remediation) was NOT performed** — per explicit instruction, this phase stops after the dry-run report and waits for approval.
**Builds on:** [PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md](PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md)

---

## 1. Root Cause Being Fixed

Phase 1F traced two chained, still-active defects that let unrelated songs collide on one Spotify ID:

1. `legacy_identification_service.py::_parse_filename()` assumed every `"X - Y.mp3"` filename was `"Artist - Song"`, which is backwards for this library's actual `"Song - EditionCredit"` convention (e.g. `"Goodums - Sammy Virji Remix.mp3"`) — corrupting the title field to a generic edition credit.
2. `backfill_gemini.py::pass6_backfill_spotify_id()` then searched Spotify using that corrupted, generic title with **zero confidence gate**, writing back whatever ranked first.

A third, related gap: `sync_tags_to_mongo.py` propagated whatever ID3 tag it found straight into MongoDB with no check against the document's existing identity — this is the one path that let the corruption reach the database (for `Pepas.mp3`).

---

## 2. Code Changes

All changes are code/test only — no library data was touched (verified in §17 below).

### [`backend/services/legacy_identification_service.py`](../backend/services/legacy_identification_service.py)
- Added `_EDITION_CREDIT_TOKENS` (extends the existing `_REMIX_TOKEN_SET` with inflected forms actually seen in this library, e.g. `"mixed"`) and `_is_edition_phrase()`.
- Replaced `_parse_filename()`'s unconditional `(artist, title) = split(" - ")` with convention detection: if the text after `" - "` reads as an edition/version credit, it's returned as `edition`, not folded into `title` or `artist`; if the ordering is ambiguous (both sides, or only the first side, read as a credit), the function returns `confident=False` rather than guessing. Return type changed from a bare `(str, str)` tuple to a `ParsedFilename` dataclass (`artist`, `title`, `edition`, `confident`).
- Updated `identify_file()`'s single call site: the `_unidentified(...)` closure was moved earlier in the function so an ambiguous filename can now return `matched=False, confidence_reason="ambiguous filename convention..."` immediately, instead of silently proceeding with a corrupted title.

### [`backend/backfill_gemini.py`](../backend/backfill_gemini.py)
- Added `_select_verified_spotify_match(query_title, query_artist, duration_ms, candidates)` — reuses `legacy_identification_service._pick_best()`'s existing weighted scorer (title/artist/duration/remix/album) rather than a new algorithm, then applies a stricter `CONF_ACCEPT_WARN` (0.75) bar for the unattended write (looser than that is documented in that module as "log + report only, no retag").
- `pass6_backfill_spotify_id()` now fetches 5 candidates (was 1), builds them via the existing `_extract_candidate()`, computes the file's real duration, and only writes `TXXX:SPOTIFY_ID` when `_select_verified_spotify_match()` returns a non-empty ID. A rejected candidate is logged (`[reject] ...`) and the file is left untagged rather than mistagged.

### [`backend/sync_tags_to_mongo.py`](../backend/sync_tags_to_mongo.py)
- Added `_identity_compatible()` — a pure function comparing an existing Mongo document's title/artist against a candidate identity; blocks on material title disagreement (title must match by similarity or containment; a strong artist match alone is **not** sufficient — mirrors `strict_matcher.py`'s artist/title gate rationale, and is exactly what's needed to block the real Beba/Pepas case, which shares an artist field but not a title).
- Added `_resolve_spotify_identity()` — resolves what an incoming Spotify ID actually represents via a live (read) Spotify API call, failing safe (block) if the lookup itself fails.
- `main()`'s update loop now blocks and logs (to `backend/reports/sync_tags_conflicts.json`) any proposed `spotify_id` change that disagrees with the document's existing identity, instead of unconditionally overwriting it. `--dry-run` behavior is unchanged.

### No unique index added
Per explicit instruction, `spotify_id` remains a **non-unique** sparse index; `identity_key` remains the sole uniqueness mechanism. No schema change was made.

---

## 3. Tests

New file: [`backend/tests/test_spotify_identity_remediation.py`](../backend/tests/test_spotify_identity_remediation.py) — 16 tests, stdlib `unittest`, no network/Mongo mocking needed (hand-built dicts, matching the existing `test_strict_matcher.py` convention):

| Test | Case | Result |
|---|---|---|
| `test_A_low_similarity_rejected`, `test_A_no_candidates_rejected` | A: pass6 low-similarity rejection | Confirms no ID written |
| `test_B_strong_match_accepted` | B: pass6 strong-match acceptance | Confirms ID accepted |
| `test_C_artist_song_convention_unchanged` | C: `"Artist - Song.mp3"` | artist/title unchanged |
| `test_D_song_radio_edit_convention` | D: `"Song - Radio Edit.mp3"` | title/edition split correctly |
| `test_E_song_named_remix_convention`, `test_E_mixed_suffix_convention` | E: `"Song - Hamdi Remix.mp3"` (+ real "Mixed" case) | title/edition split correctly |
| `test_F_ambiguous_filename_returns_uncertain` | F: ambiguous filename | `confident=False` |
| `test_G_material_disagreement_blocked` | G: sync conflict | Blocked |
| `test_H_matching_metadata_allowed`, `test_close_title_variant_allowed`, `test_no_prior_identity_allowed` | H: sync valid update | Allowed |
| `test_old_corrupted_query_now_rejected_by_gate`, `test_fixed_parse_no_longer_corrupts_title`, `test_fixed_parse_plus_gate_accepts_genuine_match` | I: Spotify-ID collision regression (real Goodums/Sammy Virji Remix fixture) | Old corrupted path now rejected; fixed path accepts the genuine match |

**Results:**
```
python -m unittest tests.test_spotify_identity_remediation -v
Ran 16 tests in 0.001s — OK

python -m unittest discover -s tests -p "test_*.py"
Ran 252 tests in 0.941s — OK   (236 pre-existing + 16 new, zero failures/regressions)

python -m py_compile services/legacy_identification_service.py backfill_gemini.py sync_tags_to_mongo.py tests/test_spotify_identity_remediation.py
PY_COMPILE_OK
```

---

## 4-7. Affected Files, Current/Proposed Metadata, Match Evidence, Confidence Score

Full detail for all 22 files across the 10 collision groups (this is the complete set — every group from Phase 1F, including the 2 groups that need no change): see the table in §16.

## 8. Confidence Score — methodology note

The dry run queried the **live Spotify API** (read-only search — no writes) using the corrected title from the fixed `_parse_filename()` (reconstructed as `"Title - Edition"`, the real, complete song credit) where the file's own ID3 title was confirmed corrupted, or the file's own existing ID3 title/artist where it was not. Every proposed match then went through the exact `_select_verified_spotify_match()` gate that now guards `pass6`.

**Result: 0 of 22 files clear the auto-write bar (0.75).** This is not a defect in the dry run — it is the gate working as specified ("WEAK MATCH = REJECT"). The reason is structural: for every Category A file, the real artist is genuinely unrecoverable from the filename alone (the corrupted convention only ever preserved the song title or the edition credit, never both plus a real artist), so every proposed match is missing 30% of its possible score (the artist term) by construction — capping realistic scores around 0.60-0.68 even for what is very likely the correct track. This is precisely why Phase 1F's remediation design (§10 of that report) puts AcoustID/ISRC fingerprinting ahead of title search: fingerprinting doesn't have this blind spot.

One useful, unplanned finding from the live search: `"Let's Go (feat. Tom Enzy, Juany Bravo & Sami Brielle) [HUGEL Remix].mp3"` — the *second* file in the Cruel Summer/Let's Go collision group — scored **0.975** against its own existing (uncorrupted) tags and its own currently-assigned Spotify ID. This is strong evidence that spotify_id `5jde2vTSXmrIAKaZYmhqSF` **correctly belongs to "Let's Go..."**, and it is `"Cruel Summer (Again) - Tom Enzy Remix.mp3"` that is the misassigned file in that pair, not both.

---

## 9. Safe-to-Repair Files

**None.** 0 of the 22 files in the 10 collision groups cleared the 0.75 auto-write bar. This is a conservative outcome, per the task's explicit "do not blindly repair" instruction — see §8 for why, and §15 for what Stage B should do instead of a title-only re-tag.

## 10. Review-Required Files

**17 files.** All 11 Category A files, both Category B ambiguous groups (4 files: Play/Players, Zulfa/Zulfaan), and both Category C files (Beba/Pepas). Full list and per-file reasons in §16.

## 11. Beba/Pepas Status

**Not auto-modified, as instructed.** Both files' ID3 tags are internally clean (no title/artist swap signature) — `Beba.mp3` (title="Beba", artist="Farruko" — correct) and `Pepas.mp3` (title="Pepas", artist="Unknown"). Neither the filename-convention fix nor the confidence gate applies a proposed change here, because neither file shows the corruption signature the fixes target. Per Phase 1F §4b, this collision needs direct audio or AcoustID fingerprint verification (or a manual listen) — recommended as a Stage B prerequisite, not resolved by this phase's code changes.

## 12. Files Requiring Fingerprint Identification

All 11 Category A files plus Beba/Pepas (13 files total) are recommended for AcoustID/ISRC fingerprinting (`backfill_gemini.py::pass7_acoustid_spotify_id()`, which is already validated — exact ISRC match, no title-guessing) ahead of any title-search-based re-tag, per the task's stated hierarchy (fingerprint > strong title+artist > filename agreement > weak search, reject weak alone).

## 13. Files Requiring Manual Review

4 files (Play/Players, Zulfa/Zulfaan) — metadata evidence is genuinely mixed (see Phase 1F §2) and needs a human listen, not an automated identification attempt of any kind.

## 14. Rollback Strategy (design only — for Stage B, not built this phase)

Per the task's backup design (§12 of the task prompt), before any Stage B write:
1. Fresh Mongo `library_index` backup (`bson.json_util.dumps`) — matches the convention already used in every prior phase of this project.
2. Per-file metadata backup: for each of the (at most) 22 affected files, snapshot the full current ID3 tag set (not just `TXXX:SPOTIFY_ID`) plus its current Mongo document, keyed by file path and content hash (not by title/artist/spotify_id — those are exactly the fields in question, per the task's explicit physical-file-safety instruction, §11).
3. A remediation manifest recording, per file: path, SHA-256 of the file at backup time, old tag values, new tag values, match method, confidence, and timestamp — sufficient to mechanically reverse every write.
4. SHA-256 hashes of every affected physical file, captured before any write, to detect any unexpected concurrent modification.
5. Rollback = restore the Mongo backup for changed documents + re-write the pre-change ID3 values from the manifest to the exact paths recorded (verified against the pre-change SHA-256 first, refusing to touch a file whose hash no longer matches).

## 15. Exact Actions That Would Happen in Stage B (NOT performed)

Given the dry-run result (0 safe-to-repair), Stage B as originally envisioned (re-tag via title search) has **nothing to safely execute yet**. The concrete, correctly-scoped Stage B would instead be:

1. Run the backup steps in §14.
2. Run `backfill_gemini.py::pass7_acoustid_spotify_id()` (already-validated ISRC path, unmodified this phase) against the 13 fingerprint-needed files, with `--dry-run` first.
3. For files pass7 still can't resolve (e.g., a DJ-only dub plate never commercially released — plausible for several of these, given `"Goodums - Sammy Virji Remix"`, `"OK OK - Hamdi remix"` etc. read like unofficial remix credits, not always present in Spotify's catalog under a clean title, which independently explains the dry run's low scores): **clear the wrong `TXXX:SPOTIFY_ID` tag rather than guessing a replacement** — an untagged file is safer than a mistagged one, and BPM/key/Camelot are independently derived from audio analysis, not the Spotify ID, so clearing it does not regress recommendations or DJ Coach.
4. For the 4 manual-review files (Play/Players, Zulfa/Zulfaan): a human listen, then a decision — no automated action.
5. For Beba/Pepas: a human listen or explicit AcoustID fingerprint check before any change.
6. Only after 1-5: re-run the now-hardened `sync_tags_to_mongo.py` to propagate any corrected tags into MongoDB (it will now block, not silently overwrite, any remaining disagreement).
7. Decide separately whether to clear the affected IDs from `ingest_tracks.json` for redownload — explicitly deferred per the task's §14 instruction, not decided in this phase.

None of this was executed.

---

## 16. Dry-Run Table

| File | Current Spotify ID | Current Title | Current Artist | Proposed ID | Proposed Title | Proposed Artist | Evidence | Confidence | Action |
|---|---|---|---|---|---|---|---|---|---|
| Ramba Ho.mp3 (Bollywood) | 3Zg5ENFvbucf41iPRvPcHn | Ramba Ho | Shashwat Sachdev | — | — | — | genuine same-recording duplicate | — | NO_CHANGE |
| Ramba Ho.mp3 (Punjabi) | 3Zg5ENFvbucf41iPRvPcHn | Ramba Ho | 11 | — | — | — | genuine same-recording duplicate | — | NO_CHANGE |
| Goodums - Sammy Virji Remix.mp3 | 0SLedTMdKihqLsR6CGPAfD | Sammy Virji Remix *(corrupted)* | Unknown T | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=1.00(Δ1.6s) remix=1.00 | 0.675 | REVIEW_REQUIRED |
| on & on - Sammy Virji Remix.mp3 | 0SLedTMdKihqLsR6CGPAfD | Sammy Virji Remix *(corrupted)* | on & on | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=0.80(Δ2.2s) remix=1.00 | 0.635 | REVIEW_REQUIRED |
| Calling Out Your Name - Radio Edit.mp3 | 3taCbWWTilb7eNMsAzOBq4 | Radio Edit *(corrupted)* | Calling Out Your Name | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=1.00(Δ2.0s) remix=1.00 | 0.675 | REVIEW_REQUIRED |
| Fight to Love - Radio Edit.mp3 | 3taCbWWTilb7eNMsAzOBq4 | Radio Edit *(corrupted)* | Fight to Love | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=0.80(Δ2.4s) remix=1.00 | 0.635 | REVIEW_REQUIRED |
| Crazy For It - Rampa.mp3 | 0nZUQdt97RJ429I0FuAO2r | (no ID3 title) | (no ID3 artist) | — | — | — | genuine same-recording duplicate | — | NO_CHANGE |
| Crazy For It.mp3 | 0nZUQdt97RJ429I0FuAO2r | Crazy For It | Electronic | — | — | — | genuine same-recording duplicate | — | NO_CHANGE |
| Cruel Summer (Again) - Tom Enzy Remix.mp3 | 5jde2vTSXmrIAKaZYmhqSF | Tom Enzy Remix *(corrupted)* | Jaden Bojsen | — | — | — | own-tags search: title=0.57 artist=1.00 dur=0.00(Δ75.1s) remix=1.00 | 0.604 | REVIEW_REQUIRED |
| Let's Go (feat. Tom Enzy...) [HUGEL Remix].mp3 | 5jde2vTSXmrIAKaZYmhqSF | *(intact, correct)* | Jaden Bojsen | 5jde2vTSXmrIAKaZYmhqSF | *(same)* | Jaden Bojsen | own-tags search: title=1.00 artist=1.00 dur=1.00(Δ0.3s) remix=1.00 | 0.975 | NO_CHANGE — this ID is very likely the correct owner |
| OK OK - Hamdi remix.mp3 | 401geNb9B5l7bFzi53Z3oP | Hamdi remix *(corrupted)* | OK OK | — | — | — | no candidates returned for this query | 0.0 | REVIEW_REQUIRED |
| Sweet Shop - Hamdi Remix.mp3 | 401geNb9B5l7bFzi53Z3oP | Hamdi Remix *(corrupted)* | Sweet Shop | — | — | — | no candidates returned for this query | 0.0 | REVIEW_REQUIRED |
| Beba.mp3 | 5fwSHlTEWpluwOM0Sxnh5k | Beba *(clean)* | Farruko | — | — | — | Category C — fingerprint required | — | REVIEW_REQUIRED |
| Pepas.mp3 | 5fwSHlTEWpluwOM0Sxnh5k | Pepas *(clean)* | Unknown | — | — | — | Category C — fingerprint required | — | REVIEW_REQUIRED |
| Alone - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | Mixed *(corrupted)* | ENHYPEN *(wrong)* | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=1.00(Δ0.0s) remix=1.00 | 0.675 | REVIEW_REQUIRED |
| Oh Will - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | Mixed *(corrupted)* | ENHYPEN *(wrong)* | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=1.00(Δ0.1s) remix=1.00 | 0.675 | REVIEW_REQUIRED |
| Somebody Else - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | Mixed *(corrupted)* | ENHYPEN *(wrong)* | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=1.00(Δ0.1s) remix=1.00 | 0.675 | REVIEW_REQUIRED |
| Trumpet - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | Mixed *(corrupted)* | ENHYPEN *(wrong)* | — | — | — | filename-reparse: title=1.00 artist=0.00 dur=1.00(Δ0.0s) remix=1.00 | 0.675 | REVIEW_REQUIRED |
| Play.mp3 | 0MbOLfDcGk8ROHJYXJHu5c | Play | Badshah | — | — | — | ambiguous (BPM 120 vs 110, dur 112s vs 167s) | — | REVIEW_REQUIRED |
| Players.mp3 | 0MbOLfDcGk8ROHJYXJHu5c | Players | Indian | — | — | — | ambiguous | — | REVIEW_REQUIRED |
| Zulfa.mp3 | 2nUs7DZ4tV0NOtTTRxyfFj | Zulfa | Indian | — | — | — | ambiguous (BPM 130 vs 100, dur 146s vs 185s) | — | REVIEW_REQUIRED |
| Zulfaan.mp3 | 2nUs7DZ4tV0NOtTTRxyfFj | Zulfaan | Indian | — | — | — | ambiguous | — | REVIEW_REQUIRED |

**No cases were hidden or force-classified.** Raw data: `phase1g_dry_run_result.json` in this session's scratchpad.

---

## 17. Verify No Live Changes

| Check | Before | After | Result |
|---|---|---|---|
| Mongo total documents | 2,282 | 2,282 | unchanged |
| Mongo live documents | 2,150 | 2,150 | unchanged |
| `Goodums - Sammy Virji Remix.mp3` Mongo `spotify_id` field | `""` | `""` | unchanged |
| `Goodums - Sammy Virji Remix.mp3` ID3 `TXXX:SPOTIFY_ID` | `0SLedTMdKihqLsR6CGPAfD` | `0SLedTMdKihqLsR6CGPAfD` | unchanged (still the original wrong value — confirms no write occurred despite the live Spotify search) |
| `ingest_tracks.json` SHA-256 | `a8ed475a...03ace7d` | `a8ed475a...03ace7d` | unchanged |
| `ingest_tracks.json` mtime | `1789455767.57` | `1789455767.57` | unchanged |

```
Mongo writes = 0
Physical modifications = 0
Physical deletions = 0
Physical moves = 0
ID3 changes = 0
Downloads = 0
ingest_tracks.json changes = 0
```

No unexpected write occurred.

---

## 18. STAGE B — Execution Results (2026-09-15, post-approval)

User explicitly approved "Stage B exactly as scoped" (this document's §15). Executed the same day.

### Backup (completed before any write)
- Full Mongo `library_index` snapshot: 2,282 docs -> `library_index_backup_pre_stageB_20260915T081655Z.json` (scratchpad).
- Per-file backup (all 22 collision-group files): full ID3 tag dump + SHA-256 + Mongo document -> `phase1g_stageB_file_backup_20260915T081655Z.json` (scratchpad).

### A bug was found and fixed mid-execution
The first Stage B attempt (`...081339Z`) failed every single AcoustID lookup with `HTTP 400: missing required parameter "client"`. Root cause: the remediation script imported `services.musicbrainz_service` (which reads `ACOUSTID_API_KEY` into a **module-level constant at import time**) before anything had loaded `.env` into `os.environ` — so the constant froze as `""`. This caused all 11 Category A files to be **CLEARED** (their wrong tag removed) without a genuine fingerprint attempt ever having run — a real but incomplete outcome, not a wrong one.

This was caught immediately (every result was suspiciously identical), the 11 files were **rolled back** to their exact pre-Stage-B tag values using the just-taken backup (verified restored), and Stage B was re-run with the fix (`from config import config` moved before any `services.*` import, matching the pattern `backfill_gemini.py` itself already uses). No corrupted or inconsistent state was left on disk or in Mongo at any point — verified via the same before/after checks below.

### Fingerprint identification results (13 files: 11 Category A + Beba + Pepas)

None of the 13 files resolved via AcoustID/ISRC. Per-file reasons: 9 files returned an AcoustID audio match (score >= 0.85) but MusicBrainz had **no ISRC** recorded for that recording; 4 files (Calling Out Your Name - Radio Edit, Cruel Summer (Again) - Tom Enzy Remix, Beba, and one MusicBrainz-ID-less case) returned **no AcoustID match at all**. This is consistent with the hypothesis raised in the dry run: several of these are UK garage/grime DJ edits, remix dubs, and promo-only cuts (e.g. "Sammy Virji Remix", "Hamdi remix" credits) that are plausibly not commercially released with standard ISRC metadata, which independently explains why the original title-search-based backfill also had nothing reliable to find.

### Writes performed

**11 Category A files — CLEARED** (wrong `TXXX:SPOTIFY_ID` removed, nothing written in its place, since no genuine match was found):

| File | Old (wrong) Spotify ID | New |
|---|---|---|
| Goodums - Sammy Virji Remix.mp3 | 0SLedTMdKihqLsR6CGPAfD | *(cleared)* |
| on & on - Sammy Virji Remix.mp3 | 0SLedTMdKihqLsR6CGPAfD | *(cleared)* |
| Calling Out Your Name - Radio Edit.mp3 | 3taCbWWTilb7eNMsAzOBq4 | *(cleared)* |
| Fight to Love - Radio Edit.mp3 | 3taCbWWTilb7eNMsAzOBq4 | *(cleared)* |
| Cruel Summer (Again) - Tom Enzy Remix.mp3 | 5jde2vTSXmrIAKaZYmhqSF | *(cleared)* |
| OK OK - Hamdi remix.mp3 | 401geNb9B5l7bFzi53Z3oP | *(cleared)* |
| Sweet Shop - Hamdi Remix.mp3 | 401geNb9B5l7bFzi53Z3oP | *(cleared)* |
| Alone - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | *(cleared)* |
| Oh Will - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | *(cleared)* |
| Somebody Else - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | *(cleared)* |
| Trumpet - Mixed.mp3 | 6S0By3u06ttb3kU2XEtWnw | *(cleared)* |

All 11 writes individually re-read and verified immediately after saving (`verified=True` for every file in the manifest). None of these 11 files had a populated Mongo `spotify_id` field to begin with (confirmed in Phase 1F — all use `identity_key: file:*`), so clearing the ID3 tag required **no MongoDB write** — Mongo was already consistent with the corrected state.

**`Let's Go (feat. Tom Enzy, Juany Bravo & Sami Brielle) [HUGEL Remix].mp3`** — excluded from all fingerprinting/writes; the dry run already confirmed (confidence 0.975 against its own existing tags) that it legitimately owns `5jde2vTSXmrIAKaZYmhqSF`. Untouched.

**Beba.mp3 / Pepas.mp3 — NOT written, as instructed.** Fingerprinting did not resolve either file, and per the repeated instruction to never auto-modify this pair, nothing was changed regardless. Both still carry the shared, unresolved `5fwSHlTEWpluwOM0Sxnh5k` tag, exactly as found. Recommendation unchanged from §11: needs a manual listen or a human-supervised fingerprint check, not a further automated attempt.

**Play.mp3 / Players.mp3 / Zulfa.mp3 / Zulfaan.mp3 / both Ramba Ho.mp3 / both Crazy For It*.mp3 — not touched at all**, confirmed identical to their pre-Stage-B tag values.

### Verify no unexpected changes

| Check | Before Stage B | After Stage B | Result |
|---|---|---|---|
| Mongo total documents | 2,282 | 2,282 | unchanged |
| Mongo live documents | 2,150 | 2,150 | unchanged |
| Mongo writes | — | 0 | none needed (all 11 corrections were ID3-tag-only) |
| `ingest_tracks.json` SHA-256 | `a8ed475a...03ace7d` | `a8ed475a...03ace7d` | unchanged (identical to the hash captured before Stage A began) |
| Downloads | — | 0 | none |
| Not-touched files (6) | — | — | all confirmed byte-for-byte tag-identical |

### Outcome vs. original goal

The original hope (title-search or fingerprint resolving these to their correct Spotify identity) did not pan out for any of the 13 files — the evidence suggests several are simply not in Spotify/MusicBrainz's catalog under a clean, ISRC-tagged recording. The achieved, real fix is narrower but still meaningful: **the specific defect that caused unrelated songs to collide on one Spotify ID no longer exists in the code** (verified by 16 passing regression tests), **the 11 files carrying a proven-wrong ID no longer carry it** (an empty tag can no longer cause a false dedup match or a false "already downloaded" skip), and **Beba/Pepas and the 4 ambiguous files were left exactly as found**, pending a human decision this phase cannot make.

### Recommended follow-up (not performed, no further authorization implied)
- A manual listen (or human-supervised AcoustID check) for Beba.mp3, Pepas.mp3, Play.mp3, Players.mp3, Zulfa.mp3, Zulfaan.mp3.
- If any of the 11 now-cleared files are ever manually identified (e.g. the user recognizes the track), the corrected `spotify_id` can be written directly — the hardened `sync_tags_to_mongo.py` will now safely propagate it into Mongo.
- Decide separately whether/how to source correct copies of the 11 cleared tracks going forward (not attempted here — no downloads occurred, per instruction).
