# Phase 1F — Spotify Identity Corruption Root-Cause Investigation

**Date:** 2026-09-15
**Mode:** Read-only reconnaissance. No files, tags, Mongo documents, or `ingest_tracks.json` entries were modified.
**Scope:** The 10 Spotify-ID collision groups discovered in the post-cleanup audit ([POST_CLEANUP_RECONCILIATION_2026-09-15.md](POST_CLEANUP_RECONCILIATION_2026-09-15.md)), traced to root cause.

---

## 1. Executive Summary

10 groups of physical files (21 files total) share a Spotify ID with at least one other, unrelated physical file. Of these:

- **2 groups (4 files) are genuine same-song duplicates** — two encodes of the same real recording, correctly sharing one ID (Ramba Ho, Crazy For It).
- **2 groups (4 files) are near-duplicates / spelling variants** worth a manual listen but not clearly wrong (Play/Players, Zulfa/Zulfaan).
- **6 groups (13 files) are confirmed different-song collisions** — the shared ID is wrong for at least one file in the group.

The 6 confirmed collisions are **not random** — 5 of them (11 files) share an exact, reproducible mechanical signature: the file's embedded ID3 **title and artist fields are swapped**, with the title field holding a generic remix/edition credit ("Sammy Virji Remix", "Radio Edit", "Hamdi Remix", "Mixed") instead of the real song title. A downstream Spotify-ID backfill process then searched Spotify **using that corrupted, generic title as the query**, with **no confidence or similarity gate**, and wrote back whatever track Spotify's API ranked first. Because multiple unrelated songs collapsed to the same corrupted title string (e.g. two different tracks both remixed by "Sammy Virji" both end up titled literally "Sammy Virji Remix"), the identical wrong Spotify ID was written to all of them.

The 6th collision (Beba / Pepas) does **not** show the swap signature — its ID3 tags are clean and correct on both files — so it is evidence of a second, related but distinct failure of the same unguarded backfill mechanism (or a manual tagging mistake external to this codebase; see §4).

**Good news on blast radius:** for 9 of the 10 groups (18 of 21 files), the corrupted ID lives **only in the on-disk ID3 tag**. The corresponding MongoDB documents were indexed under the `file:<name>` identity scheme with an **empty** `spotify_id` field, so the bad ID has not reached the database, recommendation engine, or DJ Coach. Only one file (`Pepas.mp3`) has the wrong ID inside MongoDB itself.

---

## 2. All 10 Spotify-ID Collision Groups

Full per-file detail (ID3 tags, Mongo document, BPM/key/Camelot/duration, content_hash, file size) was captured to `phase1f_collision_detail.json` in this session's scratchpad directory (not part of the repo). Summary:

| # | Spotify ID | Files | Classification |
|---|---|---|---|
| 1 | `3Zg5ENFvbucf41iPRvPcHn` | Ramba Ho.mp3 (Bollywood) / Ramba Ho.mp3 (Punjabi) | **GENUINE DUPLICATE** — same title/artist/BPM/Camelot (138, 1A); durations differ (163.7s vs 127.6s), likely a trimmed edit of the same recording |
| 2 | `0SLedTMdKihqLsR6CGPAfD` | Goodums - Sammy Virji Remix.mp3 / on & on - Sammy Virji Remix.mp3 | **DIFFERENT-TRACK COLLISION** — title/artist swap signature present |
| 3 | `3taCbWWTilb7eNMsAzOBq4` | Calling Out Your Name - Radio Edit.mp3 / Fight to Love - Radio Edit.mp3 | **DIFFERENT-TRACK COLLISION** — title/artist swap signature present |
| 4 | `0nZUQdt97RJ429I0FuAO2r` | Crazy For It - Rampa.mp3 / Crazy For It.mp3 | **GENUINE DUPLICATE** — same title, same BPM (120), Camelot differs (2B vs 1A — likely a key-detection octave/relative-key discrepancy on one copy), durations within 1s |
| 5 | `5jde2vTSXmrIAKaZYmhqSF` | Cruel Summer (Again) - Tom Enzy Remix.mp3 / Let's Go (feat. Tom Enzy...) [HUGEL Remix].mp3 | **DIFFERENT-TRACK COLLISION** — title/artist swap signature present |
| 6 | `401geNb9B5l7bFzi53Z3oP` | OK OK - Hamdi remix.mp3 / Sweet Shop - Hamdi Remix.mp3 | **DIFFERENT-TRACK COLLISION** — title/artist swap signature present |
| 7 | `5fwSHlTEWpluwOM0Sxnh5k` | Beba.mp3 (Latin) / Pepas.mp3 (Pop) | **DIFFERENT-TRACK COLLISION** — clean tags on both sides, no swap signature (distinct failure mode, see §4) |
| 8 | `6S0By3u06ttb3kU2XEtWnw` | Alone / Oh Will / Somebody Else / Trumpet — all "- Mixed.mp3", artist=ENHYPEN | **DIFFERENT-TRACK COLLISION (4-way)** — title/artist swap signature present; likely the correct owner of this ID is a real Spotify item literally titled "Mixed" by ENHYPEN |
| 9 | `0MbOLfDcGk8ROHJYXJHu5c` | Play.mp3 / Players.mp3 | **AMBIGUOUS / near-duplicate** — different titles, different BPM (120 vs 110) and duration (112s vs 167s); artists differ (Badshah vs "Indian"). Plausibly two different songs by Badshah miscollapsed, or the same base track re-edited — needs a listen, not confidently classifiable from metadata alone |
| 10 | `2nUs7DZ4tV0NOtTTRxyfFj` | Zulfa.mp3 / Zulfaan.mp3 | **AMBIGUOUS / near-duplicate** — same artist field ("Indian"), similar titles, but BPM differs sharply (130 vs 100) and duration differs (146s vs 185s) — plausibly two different songs, not one |

For groups 9 and 10, evidence is genuinely mixed (per the user's explicit instruction: do not infer sameness from Spotify ID alone, and do not force a same/different call without support) — they are reported as ambiguous rather than force-classified.

---

## 3. Evidence For Each Collision

**Group 2 (Goodums / on & on):** File 1 ID3: `title="Sammy Virji Remix" artist="Unknown T"`. File 2 ID3: `title="Sammy Virji Remix" artist="on & on"`. Filenames are "Goodums - Sammy Virji Remix.mp3" and "on & on - Sammy Virji Remix.mp3" — i.e. the part before " - " (the real song name) is missing from TIT2 entirely on file 1, and the part after " - " (an edition credit, not a song title) is in TIT2 on both. Mongo `spotify_id` field is empty for both (identity_key uses the `file:` fallback scheme) — the corruption has not reached the database.

**Group 3 (Calling Out Your Name / Fight to Love):** File 1 ID3: `title="Radio Edit" artist="Calling Out Your Name"`. File 2 ID3: `title="Radio Edit" artist="Fight to Love"`. The real song name ended up in TPE1 (artist field), and the edition credit "Radio Edit" ended up in TIT2. Two unrelated songs, both released as a "Radio Edit," collapsed to an identical corrupted title.

**Group 5 (Cruel Summer / Let's Go):** File 1 ID3: `title="Tom Enzy Remix" artist="Jaden Bojsen"`. File 2 ID3: `title="Let's Go (feat. Tom Enzy, Juany Bravo & Sami Brielle) [HUGEL Remix]" artist="Jaden Bojsen"` — here file 2's title is actually intact and correct, but both carry the same (wrong, or at least non-title) artist value "Jaden Bojsen" and the same spotify_id, and file 1's title has been reduced to just the remix credit.

**Group 6 (OK OK / Sweet Shop):** File 1 ID3: `title="Hamdi remix" artist="OK OK"`. File 2 ID3: `title="Hamdi Remix" artist="Sweet Shop"`. Identical swap pattern to groups 2 and 3.

**Group 8 (ENHYPEN 4-way):** All four files show `title="Mixed" artist="ENHYPEN"`, regardless of their real song name (Alone / Oh Will / Somebody Else / Trumpet, all different BPMs: 136, 137, 139, 139, and different durations: 113s, 85s, 97s, 110s — confirming these are four physically distinct recordings, not four copies of one track). "Mixed" is very plausibly a real Spotify item by ENHYPEN; the backfill process matched all four files to it because all four files' title field literally reads "Mixed."

**Group 7 (Beba / Pepas):** File 1 ID3: `title="Beba" artist="Farruko"` (correct — Beba is a real Farruko/Anuel AA/Prince Royce track). File 2 ID3: `title="Pepas" artist="Unknown"` (correct title, no swap). No shared suffix, no swap signature. Distinguishing detail: Pepas's Mongo document is the **only one of the 21 collision-group files** with a populated `spotify_id` field and an `sp:`-scheme `identity_key` — meaning this file, unlike the other 20, went through a path that wrote the (wrong) ID into Mongo, not just the ID3 tag.

---

## 4. Root Cause

### 4a. Primary mechanism — confirmed, code-substantiated (accounts for 5 of 6 collision groups, 11 files)

Two independent, provable defects chain together:

**Defect A — title/artist field swap for "Song - EditionCredit" filenames.**
`legacy_identification_service.py::_parse_filename()` ([legacy_identification_service.py:246-258](../backend/services/legacy_identification_service.py#L246)) parses a filename with a single `" - "` separator as:

```python
if " - " in stem:
    parts = stem.split(" - ", 1)
    return parts[0].strip(), parts[1].strip()   # returns (artist, title)
```

This assumes the universal convention is `"Artist - Song.mp3"`. It is wrong for the actual convention used on the 5 affected groups: `"Song - EditionCredit.mp3"` (e.g. `"Goodums - Sammy Virji Remix.mp3"`, `"OK OK - Hamdi remix.mp3"`, `"Alone - Mixed.mp3"`). Applied to that convention, the function returns the **song name as "artist"** and the **edition credit as "title"** — exactly the swap observed in every affected file's live ID3 tags. This function itself is read-only (confirmed — see §6) and only runs when `TIT2` is already empty, so it is not necessarily the literal code that wrote these specific tags; it is presented as **the proven, currently-present instance of this exact flawed assumption** in the codebase. Whatever process originally populated these tags (a predecessor script, no longer present verbatim in the repo, or an equivalent inline routine) shared the same "first token = artist" assumption.

**Defect B — zero-validation Spotify-ID backfill.**
`backfill_gemini.py::pass6_backfill_spotify_id()` ([backfill_gemini.py:581-670](../backend/backfill_gemini.py#L581)) runs against every file missing a `TXXX:SPOTIFY_ID` tag:

```python
title  = _read_tag(f, "TIT2") or f.stem
artist = _read_tag(f, "TPE1") or ""
...
q = f"track:{clean}"
if artist:
    q += f" artist:{artist}"
items = sp.search(q=q, type="track", limit=1).get("tracks", {}).get("items", [])
if items:
    track      = items[0]
    spotify_id = track.get("id", "")
...
tags.add(_TXXX(encoding=3, desc="SPOTIFY_ID", text=[spotify_id]))
tags.save(str(f))
```

There is **no scoring, no confidence threshold, no duration check, no title-similarity check** — the top Spotify search result (`items[0]`) is written unconditionally. Contrast this with `strict_matcher.py::select_best_candidate()` (multi-factor scoring, `min_score` gate) and `legacy_identification_service.py::_pick_best()` (`CONF_NEEDS_REVIEW` gate) — both of which have exactly the kind of gate this pass lacks.

**The chain:** Defect A corrupts TIT2 to a short, generic, non-unique phrase like "Sammy Virji Remix" or "Mixed" for files whose real title was lost. Defect B then searches Spotify **for that corrupted phrase verbatim**, accepts whatever ranks first with no sanity check, and writes it back. Because two or more *different* real songs independently collapse to the identical corrupted title (both remixed by "Sammy Virji," both edited as "Radio Edit," etc.), the identical Spotify search runs for each of them, returns the identical top result, and stamps the identical wrong ID onto every file sharing that corrupted title. This is not a coincidence-dependent theory — it is a deterministic, reproducible mechanism fully supported by the observed ID3 tag contents.

### 4b. Secondary/distinct case — Beba / Pepas (1 group, 2 files)

Both files' ID3 tags are clean (no swap). Two explanations remain plausible and cannot be fully distinguished from read-only forensics alone:

1. **The same unguarded-backfill defect (4b), independently misfiring on a clean but under-specified query** — Pepas's ID3 artist is literally `"Unknown"`, so pass6's query becomes `track:Pepas artist:Unknown`, which could rank a wrong track first with no artist signal to anchor it; a `track:Beba artist:Farruko` query for the other file should be more constrained, so this alone doesn't cleanly explain the identical result on both sides — but Spotify's search relevance ranking is not deterministic evidence either way from outside the API.
2. **A manual tagging error, external to this codebase.** `sync_tags_to_mongo.py`'s own docstring documents a real manual workflow used on this library: *"After using Mp3tag (or any external tagger) to write TXXX:SPOTIFY_ID to Library files, run this to sync the tag back into the MongoDB library_index."* Mp3tag is a third-party GUI tagger; a common user error in it is multi-selecting several files and pasting/filling one field's value across the entire selection. This would produce exactly what's observed here — two files with otherwise-correct, unrelated metadata that nonetheless carry an identical, unrelated Spotify ID — without needing any code defect at all.

This is flagged as **unresolved** rather than force-classified to either explanation, per the audit's explicit instruction not to infer causes beyond what the evidence supports.

### 4c. Ruled out: `strict_matcher.py`

The prior audit's own hypothesis named `strict_matcher.py`'s remix/edit fallback as a suspect. Having read the file in full: **it is not a plausible mechanism for this bug.** `strict_matcher.py::select_best_candidate()` picks which *YouTube video* to download for a Spotify track whose ID, title, and artist are **already known and passed in as ground truth** by the caller — it never originates or assigns a Spotify ID itself; it only validates a YouTube candidate against a Spotify identity that was decided before this module ever runs. For it to cause two songs to share one Spotify ID, some other code would need to first pass it the wrong Spotify identity — and that other code (whatever selects which Spotify track to search YouTube for) was not found to have such a defect. The actual defect is upstream and downstream of `strict_matcher.py`, not inside it.

---

## 5. Exact Code Path

```
Legacy / untagged file on disk (filename: "Song - EditionCredit.mp3")
        │
        ▼
[Defect A]  legacy_identification_service.py :: _parse_filename()  (line 246)
            "Song - EditionCredit" split on " - " → returned as (artist=Song, title=EditionCredit)
            — confirmed READ-ONLY, does not itself write tags (see §6)
        │
        ▼  (same flawed assumption present in whatever process originally wrote the live ID3 tags —
        │   the tags on disk today already show this exact swap, so the corruption predates this audit)
        ▼
TIT2 = "EditionCredit" (generic, non-unique)   TPE1 = "Song" or unrelated value
        │
        ▼
[Defect B]  backfill_gemini.py :: pass6_backfill_spotify_id()  (line 581)
            title = TIT2 ("EditionCredit")  →  Spotify search  →  items[0], no gate
        │
        ▼
tags.add(TXXX(desc="SPOTIFY_ID", text=[wrong_id])); tags.save()   ← WRITE POINT (in file's ID3 tag)
        │
        ▼
(only for Pepas.mp3, not the other 20 files) sync_tags_to_mongo.py reads the tag back, matches
Mongo doc by filename only, and $set-overwrites doc.spotify_id with no validation (line 76-96)
        │
        ▼
MongoDB library_index.spotify_id / identity_key  ← WRITE POINT (Mongo, only reached for 1/21 files)
```

The correctly-designed tagging path — `tagger_service.py` lines 585-588 — is not implicated; it writes whatever `track_id` its caller passes it, and does not itself search or select a Spotify ID.

---

## 6. Is the Bug Active?

| Component | Status | Evidence |
|---|---|---|
| `legacy_identification_service.py::_parse_filename()` (Defect A signature) | **ACTIVE** | Present unchanged in the current codebase; runs whenever `TIT2` is empty and a `" - "` filename is parsed. `identify_file()` overall is confirmed read-only (docstring line 630, and `run_batch()`/`write_identification_report()` traced end-to-end — no tag-write or Mongo-write call found anywhere in this file), so this specific defect currently only affects generated *reports*, not live files — until something wires its `AUTO_ACCEPT`/`ACCEPT_WARN` output to an actual writer. |
| `backfill_gemini.py::pass6_backfill_spotify_id()` (Defect B) | **ACTIVE** | Present unchanged, unguarded, reachable via the Maintenance page's "Backfill Gemini" action (per commit `55c1d1d`, "Maintenance page — reorganise, repair index, backfill Gemini"). Running it again today, on any file missing a Spotify ID, reproduces this bug exactly as analyzed — no code changes have added a gate since. |
| `sync_tags_to_mongo.py` (Mongo propagation) | **ACTIVE** | Present unchanged; a manual script (`python sync_tags_to_mongo.py`), not scheduled. Blindly trusts ID3 over Mongo with no similarity check. |
| Beba/Pepas mechanism (manual Mp3tag or unguarded-query variant) | **HISTORICAL, cause UNKNOWN** | Confirmed from commit history that Mp3tag was used on this library (commit `b498e6d`, 2026-06-02) as an established workflow; cannot confirm from current repo state alone which specific incident produced this pair. |

**Overall classification: ACTIVE.** The two code-level defects (A + B) that fully explain 5 of 6 confirmed collisions are both still present and both still reachable in the current codebase (`feature/ai-agents` branch, commit `aa171e1`).

---

## 7. Blast Radius

**Confirmed, observed damage (as of this audit):**
- 21 physical files carry a spotify_id in their ID3 tag that is wrong for at least one member of their collision group.
- 1 of those 21 files (`Pepas.mp3`) has the wrong ID **inside MongoDB** (`spotify_id` field + `sp:`-scheme `identity_key`).
- The other 20 files are indexed under `identity_key = file:<name>` with an **empty** Mongo `spotify_id` field — the corrupted tag has not reached the database for these.

**Confirmed NOT currently affected (verified in Phase 4 of the prior audit and unchanged since):**
- Recommendation engine (`get_candidate_pool`, `recommend_next`) — reads Mongo `audio_features`/`identity_key` only, never reads ID3 tags directly, so the 20 tag-only corruptions are invisible to it.
- DJ Coach (`generate_daily_coaching_session`) — same, Mongo-only.
- Deduplication as currently exercised in this library (Mongo `content_hash`/`identity_key` uniqueness) — unaffected for the same reason.

**Real, demonstrated latent risk (not yet triggered, but proven reachable):**
- `dedup_service.py::duplicate_identity_key(spotify_id, title, artist, duration_ms)` returns `f"sp:{spotify_id}"` whenever a non-empty spotify_id is supplied — and `dedup_service.py::score_from_tags()` (line 167-194) sources that spotify_id **directly from the file's `TXXX:SPOTIFY_ID` ID3 tag**, not from Mongo. If any future dedup/reconcile pass runs `score_from_tags()`/`duplicate_identity_key()` over these files, it would compute the *same* dedup key for two genuinely different songs (e.g. both "Alone - Mixed.mp3" and "Trumpet - Mixed.mp3" would collapse to `sp:6S0By3u06ttb3kU2XEtWnw`) and treat them as duplicates of each other.
- `dedup_service.py::canonical_score()` gives **+10 points** for "spotify_id present," meaning a corrupted-but-tagged file would be scored as more "canonical" than a correctly-tagged file with no Spotify ID at all — an automated cleanup could keep the *wrong* file and delete the *right* one.
- `sync_tags_to_mongo.py`, if run again, would propagate any of the remaining 20 tag-only corruptions into MongoDB exactly as it already did for Pepas — turning a currently-dormant tag-level problem into a database-level one, affecting genre routing, recommendations, and DJ Coach for those tracks.

This is precisely why the earlier post-cleanup audit's caution against any fuzzy-evidence auto-deletion was correct, and why this phase's "no automatic repair" instruction is the right call: **the corruption is currently contained, but any of several existing, unmodified scripts would spread it into the database if run.**

---

## 8. Downloader/Tagger Implications

- Wrong title/artist: confirmed on all 5 mechanically-explained groups (11 files) — the live ID3 `TIT2`/`TPE1` values are themselves wrong (swapped), independent of the spotify_id question.
- Wrong artwork/genre/BPM/key: not observed to be corrupted by this specific bug — `audio_features` (BPM/key/Camelot) in Mongo come from `bpm_key_service.py`'s own librosa analysis of the audio, not from the Spotify ID, so they remain independently correct regardless of the mistagged ID.
- Wrong deduplication / ingest suppression: not yet triggered (see §7), but mechanically enabled the moment `dedup_service.py` or `sync_tags_to_mongo.py` runs against these files.
- Wrong library identity / recommendations: not currently affected, for the reasons in §7.

---

## 9. `ingest_tracks.json` Findings

`ingest_tracks.json` stores `{"track_ids": [...], "last_checked": ...}` — a **flat set** of Spotify IDs already downloaded (`backend/services/auto_downloader.py:227-251`). It records only *whether* an ID has been downloaded, never *which physical file* or *which source playlist track* it corresponds to. Scanned read-only for this phase:

- No duplicate entries possible by construction (it's a Python `set`).
- It has **no reverse mapping** from ID → filename/track, so it structurally cannot detect or prevent two different physical files from later being tagged with the same ID — that class of bug is entirely outside its purview by design, not a gap introduced by this incident.
- None of the 10 collision-group Spotify IDs were cross-checked against this file specifically in this phase (out of scope — they are legacy-library IDs, not ingest-pipeline IDs; none of the 21 collision files show recent `indexed_at` timestamps consistent with the current auto-downloader pipeline).

---

## 10. Mongo Identity Model Findings

`database.py` index definitions (`_ensure_indexes()`, lines 172-186):

```python
db.library_index.create_index([("identity_key", 1)], unique=True, name="idx_identity_key")
db.library_index.create_index([("spotify_id", 1)], sparse=True, name="idx_lib_spotify_id")
db.library_index.create_index([("content_hash", 1)], sparse=True, name="idx_lib_content_hash")
```

**`spotify_id` has no uniqueness constraint** — only `identity_key` does. This means MongoDB's schema **structurally permits** multiple documents to share one `spotify_id` (confirmed: 2 such groups already exist in Mongo today — Dil Nu/Dil Pe Zakham Khate Hain and Let's Nacho, both previously identified as PROBABLE duplicates in the prior audit). This is "expected" at the schema-enforcement level (the database will not reject it) but is an **integrity violation at the data-modeling level** — the field's entire purpose is to be a 1:1 external identity — so the schema is not itself defective, but it is not weight-bearing here since it never enforced anything.

---

## 11. Relationship to the 8 Content-Hash Duplicates

**No overlap.** Cross-checked every `identity_key` in the 8 confirmed content-hash duplicate groups (Winny, Chaleya, Zaalima, Baddadan, DON'T KILL MY VIBE, Free Your Mind, Tripasia, Club Chennai) against every `identity_key`/path in the 10 Spotify-ID collision groups — zero shared identity_keys or files. The two problems are independent: content-hash duplicates are pairs of *correctly*-identified copies of the same song; the Spotify-ID collisions are *incorrectly*-identified pairs of different songs. They do not share a root cause or a remediation path.

(Incidental finding, not part of this ask: 3 of the 8 content-hash duplicate groups — Free Your Mind, Tripasia, Club Chennai — are also exactly the 3 "Mongo live document with no physical file" cases from the prior audit's reconciliation §4/§11. Their physical twin under a different folder/suffix is the file that still exists. This is the same "collision-suffix sibling" pattern Phase 1E addressed, not a new issue, and is unrelated to the Spotify-ID mistagging investigated here.)

---

## 12. Remaining 3+3 Path Mismatches

**3 live Mongo docs without a physical file** (Free Your Mind - Prospa, Tripasia - Cloonee, Club Chennai - aboywithabag): confirmed to be **the same collision-suffix sibling pattern** Phase 1E previously repaired — each has a duplicate physical copy under a different genre-folder path or a `_1` filename suffix, and the "canonical" Mongo doc's `final_path` points at the copy that no longer exists. This is a known, previously-characterized pattern, not a new failure mode.

**3 soft-deleted Mongo docs whose physical file still exists** (Laundey Crazy - pho, Bhussi - KSHMR, Narmahat Freestyle - Karma): **not** the same pattern — these have `missing: True` in Mongo while their file is present on disk today, which is the opposite direction (a stale "file not found" flag rather than a stale path pointing at a since-moved duplicate). Most consistent with a transient scan glitch (e.g. a maintenance sweep running while the file was briefly inaccessible) rather than a naming collision. Flagged as a distinct, smaller issue for a future phase — not investigated further here as it is out of this phase's Spotify-ID scope.

---

## 13. Same Day Cleaning

Unchanged from the prior audit: 9 tracks remain under `genre_folder: "Same Day Cleaning"` (an EP title, not a taxonomy genre). Re-confirmed this phase: it is a **folder-routing bug**, not a Spotify-ID mistagging issue — none of the 9 Same Day Cleaning tracks appear in any of the 10 collision groups, and their `spotify_id` fields (where present) are clean. Unrelated to this investigation; still unfixed, still out of scope for a "do not fix" phase.

---

## 14. Current System Safety

Re-verified this phase, read-only, direct `MongoClient` only (never `database._get_db()`):

- Recommendation engine: healthy (unchanged from prior audit — not re-exercised this phase since no code affecting it changed).
- DJ Training Engine / DJ Coach: healthy (unchanged from prior audit).
- No deleted/corrupt tracks leak into recommendations or coaching (unchanged).
- **Mongo document counts confirmed unchanged across this entire investigation: 2,282 total / 2,150 live**, both at the point measured in this phase's final script run — identical to the counts recorded at the end of the prior post-cleanup audit. No writes occurred.

---

## 15. Safe Remediation Proposal (design only — NOT implemented this phase)

1. **Add a confidence/similarity gate to `backfill_gemini.py::pass6_backfill_spotify_id()`**, mirroring `legacy_identification_service.py::_score_candidate()` or `strict_matcher.py::score_candidate()` — require a minimum title+artist similarity between the query and the Spotify result before writing `TXXX:SPOTIFY_ID`, and skip (don't guess) when `items[0]` is a weak match.
2. **Fix `_parse_filename()`'s single fixed convention.** Detect which side of `" - "` is more title-like vs. more credit-like (e.g. a known remix/edit token set on one side — the same `VERSION_TOKENS`/`_REMIX_TOKEN_SET` already defined in this codebase) before assuming `(artist, title)` order; or require both an ID3 read AND a filename-parse to agree before trusting either as ground truth for a write.
3. **Make `sync_tags_to_mongo.py` require agreement, not just presence**, before overwriting Mongo's `spotify_id` — e.g. refuse to overwrite when the existing Mongo `title`/`artist` doesn't reasonably match the Spotify track the new ID resolves to, and always log a diff report for human review before any `$set`.
4. **Re-scan the full library for the swap signature** (TIT2 containing only a known remix/edit token, e.g. matches `VERSION_TOKENS`/`_REMIX_TOKEN_SET` exactly) to find any further affected files beyond these 10 groups that happen not to collide with another file (i.e., mistagged but not yet detected because no sibling shares the same wrong ID).
5. For the 6 confirmed different-track groups: **clear the wrong `TXXX:SPOTIFY_ID` tag and re-derive title/artist from the filename correctly** (song name before " - ", credit after), then re-run legitimate identification (AcoustID/ISRC fingerprinting via `pass7`, which IS validated, in preference to title-search).
6. For Beba/Pepas specifically: manually verify against the actual audio (or AcoustID fingerprint) before any tag change, since neither automated theory in §4b is conclusively proven.

---

## 16. Tests Required Before Remediation Ships

- Unit test for `pass6_backfill_spotify_id()` (or its replacement) asserting it **rejects** a low-similarity top search result rather than writing it — regression test using exactly this Beba/Pepas or Sammy-Virji-Remix scenario as fixture input.
- Unit test for `_parse_filename()` covering both `"Artist - Song.mp3"` and `"Song - EditionCredit.mp3"` conventions, asserting it no longer silently mis-assigns edition credits as titles.
- Regression test for `sync_tags_to_mongo.py` asserting it refuses (or at minimum flags) an update where the incoming spotify_id's actual title/artist diverges materially from the existing Mongo document's title/artist.
- A one-off (not unit-test) verification pass across all 21 collision-group files after any fix, confirming corrected tags before any redownload/re-tag is executed.

---

## 17. What Must NOT Be Changed (this phase)

Per explicit instruction, none of the following were modified and must not be modified until a remediation phase is explicitly approved: MongoDB documents, physical files, ID3 tags, `ingest_tracks.json`, `recommendation_service.py`, `training_service.py`, DJ Coach, `strict_matcher.py`, or any downloader code. The 10 collision groups, the 8 content-hash duplicates, the 3+3 path mismatches, and Same Day Cleaning all remain exactly as found.

---

## 18. Recommended Next Phase

**Phase 1G — Spotify Identity Remediation (requires explicit approval before any write):**
1. Implement the confidence gate in `pass6_backfill_spotify_id()` and the filename-convention fix in `_parse_filename()` (items 1-2 in §15) — these are the two defects with the clearest fix and the most direct evidence.
2. Add the required tests (§16) before touching any live file.
3. With backup + dry-run + user confirmation (per this project's established pattern): clear the 6 confirmed-wrong `TXXX:SPOTIFY_ID` tags, re-derive correct title/artist, and re-identify via AcoustID/ISRC (the validated `pass7` path) rather than title search.
4. Investigate Beba/Pepas specifically (manual listen or fingerprint) before deciding a fix.
5. Only after 1-4: harden `sync_tags_to_mongo.py` (item 3, §15) so this class of error cannot reach MongoDB again even if a future Mp3tag session reintroduces it at the file level.
