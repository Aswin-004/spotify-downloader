# Post-Cleanup Reconciliation & Integrity Audit — 2026-09-15

**Type:** Read-only audit. Zero MongoDB writes, zero physical file changes, zero downloads, zero `ingest_tracks.json` changes.
**Target confirmed before querying:** non-localhost Atlas cluster / `spotify_downloader` / `library_index`.

---

## 1. Executive Summary

The cleanup executed earlier today is **fully verified and clean**: all 22
physical files are gone, all 21 Mongo documents are gone, `ingest_tracks.json`
correctly reflects the 13 released Spotify IDs, and Dis Badman/Gasolina each
now have exactly one physical file and one Mongo record. The recommendation
engine and DJ Coach both continue to work correctly against the post-cleanup
library — 236/236 existing tests pass, a live recommendation sample (10
calls) had zero errors, and today's DJ Coach session generates exactly 5
exercises deterministically with none of the deleted tracks appearing.

Two things surfaced during this audit that were **not part of the original
cleanup scope** and are reported, not fixed, per this task's read-only
mandate:

1. **A Spotify-ID mistagging bug**: at least 7 groups of physically
   different songs share one embedded Spotify ID (e.g. "Beba" and "Pepas" —
   two distinct real songs — carry the identical ID). This is the same
   underlying defect that produced the ENHYPEN false positive in the
   previous audit; it now has a name and a clear pattern (concentrated in
   remix/edit-titled tracks).
2. My own duplicate-similarity heuristic from the previous pass was **too
   loose** — manual review found it had also mis-classified several
   remix/edit pairs as "likely duplicates" when they are actually the same
   mistagging bug. Corrected below.

---

## 2. Physical Library Statistics (current, discovered fresh)

| Metric | Value |
|---|---|
| Total files (any type) under DJ music root | 2,189 |
| MP3 files | **2,157** |
| `.part` (incomplete download) files | **0** |
| Other files (`.json`, `.vdjstems` — VirtualDJ sidecar files, not audio) | 32 |
| Unreadable / corrupt MP3s | **0** |
| Zero-duration-but-mutagen-readable MP3s | 9 (same class as the Phase 1E finding — mutagen VBR/Xing header quirk, not actual corruption; not re-decoded in this pass since this audit is read-only and that class was already confirmed playable via librosa in the prior scan) |
| Playable MP3s | 2,157 |
| Total physical library size | 21,335,015,617 bytes ≈ **19.87 GB** |

**Corruption cleanup is confirmed at the physical level**: 0 unreadable
files and 0 `.part` files remain — both were non-zero before the cleanup.

## 3. MongoDB Statistics (current, discovered fresh)

| Metric | Value |
|---|---|
| Total documents | **2,282** |
| Live documents (`missing` ≠ `True`) | **2,150** |
| Soft-deleted documents | **132** |
| Live documents with a physical file present | 2,147 |
| Live documents without a physical file | **3** |
| Soft-deleted documents whose physical file still exists | **3** |
| BPM + Camelot scoreable (live) | **2,094** |
| Fully analyzed (bpm+camelot+duration+confidence, live) | 2,085 |
| Missing title | 53 |
| Missing artist | 58 |
| Missing genre_folder | 0 |
| Missing spotify_id | 511 |
| Duplicate `identity_key` values | **0** (the unique index holds) |
| Duplicate `spotify_id` values (Mongo) | **2** groups |
| Duplicate `content_hash` values | **8** groups |

**Historical comparison (labeled explicitly as historical, not current):**
Phase 3's baseline was 2,303 total / 2,171 live / 2,096 scoreable. The drop
to 2,282 / 2,150 / 2,094 is **exactly** accounted for by the cleanup (-21
Mongo docs, -21 live docs — 20 corrupt + Dis Badman's redundant copy +
Gasolina's redundant copy = 22 physical deletions but only 21 Mongo deletions
since the `.part` file never had a Mongo doc). No unexplained drift.

## 4. Physical ↔ Mongo Mismatches

| Category | Count |
|---|---|
| A. Physical file → Mongo record exists | 2,147 |
| B. Physical file → **no** Mongo record | **10** |
| C. Mongo live record → physical file missing | **3** |
| D. Mongo soft-deleted record → physical file exists | **3** |

**Category B detail** (10 unmatched physical files):

| File | Classification |
|---|---|
| `Ingest/Uncategorized/Bandhu 2.0 (From Cocktail 2) - Pritam.mp3` | Staging area — not yet ingested, expected |
| `Ingest/Uncategorized/Cloudy Eyes (Dance Tonight) - Reznik.mp3.trimmed.mp3` | Intentionally excluded by `config.py`'s `EXCLUDED_PATTERNS` (`.trimmed.mp3`) — by design |
| `Ingest/Uncategorized/Crazy For It - Rampa.mp3.trimmed.mp3` | Same — intentionally excluded, by design |
| `Ingest/Uncategorized/Selenophilia - Oblium.mp3` | Staging area — not yet ingested, expected |
| `Library/Hip Hop/Thot Shit - Megan Thee Stallion.mp3` | **Genuine orphan** — has valid ID3 title/artist, sitting in a real Library folder, but no library_index doc and no spotify_id. Needs investigation. |
| `Library/Trance/03_-_Yoshua_Em_-_Cuentame_Tus_Penas_KLICKAUD.mp3` | ID3 read fails ("doesn't start with an ID3 tag") — no ID3v2 header at all |
| `Library/Trance/ARRACK-SHANTHI_DEVIL-EXPERIMENTAL_DARKPSY_KLICKAUD.mp3` | Same — no ID3v2 header |
| `Library/Trance/Demoniac_Insomniac_-_Nataraja_170_Bpm_KLICKAUD.mp3` | Same — no ID3v2 header |
| `Library/Trance/Natalia_Lafourcade_Ft_Julieta_Venegas_-_Hu_Hu_Hu_..._KLICKAUD.mp3` | Same — no ID3v2 header |
| `Library/Trance/Shamanic_Ritual_Unmasterd_experimental_track_wav_file_KLICKAUD.mp3` | Same — no ID3v2 header |

The 4 "KLICKAUD" files are a distinct pattern worth flagging: they're
playable (mutagen's generic `File()` reads them fine — confirmed in Phase
1E's earlier scan) but carry no ID3v2 header at all, so the indexer likely
never had title/artist to work with. Not touched.

**Category C detail** (3 live docs, physical file missing): `Free Your
Mind - Prospa.mp3` (Library/Electronic), `Tripasia - Cloonee.mp3`
(Library/House), `Club Chennai - aboywithabag.mp3` (Library/Tamil) — **all
three are the "un-analyzed twin" side of a confirmed content_hash
duplicate pair** (see Section 6) whose *other* copy (in a different
folder) is what's actually missing here — wait, re-verified: these three
paths are the doc's own `final_path`, and the file is missing there
specifically; each has a sibling doc with the *same* content_hash pointing
at a different, existing path. This is the same "collision-suffix
sibling" pattern Phase 1E fixed 39 instances of — these 3 are leftover/new
instances of it, not touched in this audit.

**Category D detail** (3 soft-deleted docs, physical file exists):
`Laundey Crazy - pho.mp3` (Library/House), `Bhussi - KSHMR.mp3`
(Library/Electronic), `Narmahat Freestyle - Karma.mp3` (Library/Punjabi) —
same pattern as Category C, inverted. These are exactly the shape of issue
Phase 1E's "Group P" fix resolved for 39 other tracks; these 3 appear to
be additional, not-yet-resolved instances.

## 5. Spotify ID Integrity

| Metric | Value |
|---|---|
| Spotify IDs found in physical file ID3 tags | 1,239 |
| Spotify IDs in Mongo (live docs) | 1,637 |
| Spotify IDs in `ingest_tracks.json` | 1,980 |
| IDs in physical files **and** Mongo but **not** in ingest history | 428 |
| IDs in ingest history but **not** represented physically | 1,173 |
| Duplicate Spotify IDs across physical files (2+ different files, same ID) | **10 groups** |
| Duplicate Spotify IDs across Mongo records | 2 groups |

The 1,173 "in history but not physical" figure is expected and not
alarming on its own — `ingest_tracks.json` accumulates every track ID
ever seen across the playlist's history, including tracks later removed
from the source playlist, skipped, or permanently failed; it is not a
1:1 map to "should exist physically."

### The 13 released Spotify IDs — current state

All 13 confirmed **fully released**, consistent with the cleanup's intent:

| Spotify ID | In `ingest_tracks.json` | In Mongo (live) | In physical files |
|---|---|---|---|
| All 13 (`6DbxG9um…`, `6tifCCTI…`, `5UZIVxzI…`, `6NRvZuFX…`, `6AbVJjzv…`, `0edONLak…`, `4I6hQfzr…`, `79aGuVHo…`, `6JyuJFed…`, `1RJZLVGp…`, `2rzBvHM9…`, `4EWCNWgD…`, `5fohLPNq…`) | **False** | **False** | **False** |

**Whether the next ingest cycle can discover them again:** the local
precondition is satisfied — none of the 13 IDs remain in `ingest_tracks.json`,
so `auto_downloader.py`'s dedup check (`new_tracks = [t for t in tracks if
t["id"] not in saved_ids]`) will treat them as new. Whether they are
*actually* re-fetched depends on whether they are still present in the
currently-configured Spotify ingest playlist — verifying that requires a
live Spotify API call, which is outside this audit's read-only local scope.
**Not verified; flagged as a follow-up if you want certainty.**

### The 6 corrupt files without a Spotify ID

Confirmed: none of these 6 (`Try It Out (with Alvin Risk) - Neon Mix`,
`White Lines - Deco (BE)`, `Fire Fire`, `Tunnel Vision - Two by Four`,
`Fón Póca (feat. Travy) - MUCKANIKS Remix`, `No Bad Vibes`) have any
Spotify ID trail in Mongo, `ingest_tracks.json`, or (now) physically —
they were `file:`-identity tracks with no Spotify catalog match. **No
automatic re-fetch mechanism exists for them.** They would need a manual
re-download if wanted back.

## 6. Duplicate Classification (evidence hierarchy)

Per instruction, fuzzy title/artist matching alone is **never** treated as
proof. Evidence tiers below match the requested hierarchy.

### CONFIRMED (strong evidence — identical `content_hash`)

`content_hash` is a metadata hash (`sha256(normalized_title + normalized_artist
+ duration_bucket)[:16]`) — not a byte-level audio hash, but matching it
requires title, artist, *and* duration (5-second buckets) to all agree,
which is strong evidence. **8 groups:**

| Song | Copy 1 | Copy 2 | BPM readings |
|---|---|---|---|
| Winny (Fred again..) | `sp:439OOP15…` | `sp:4qhrMsB4…` | 140 / 93 (ratio 1.505 ≈ 3:2 — another octave-error case, same pattern as Dis Badman) |
| Chaleya (From "Jawan") | `file:Chaleya` (Bollywood) | `file:Chaleya (From Jawan)` (Tamil) | 96 / 95 |
| Zaalima (Arijit Singh) | `sp:1J9vyEntJ7…` | `sp:4MU4Kfkd9E…` | 90 / 89 |
| Baddadan (Chase & Status) | `file:Baddadan…` | `sp:2ZWmmrWUgD…` | 172 / 174 |
| DON'T KILL MY VIBE (Kendrick Lamar) | `file:…Adrian Fyrla` | `file:DON'T KILL MY VIBE` | 117 / 118 |
| Free Your Mind (Prospa) | `sp:39hHUnYBmy…` (Electronic) | `sp:6TWbY1dq8e…` (UK Garage) | 129 / *null (never analyzed)* |
| Tripasia (Cloonee) | `sp:4NqaNXn9bw…` (House) | `sp:365aB2RegI…` (UK Garage) | 129 / *null* |
| Club Chennai (aboywithabag) | `sp:1Tw1ikzex7…` | `sp:6dBlyH4rSW…` (`_1` collision-suffix filename) | 129 / *null* |

### PROBABLE (medium evidence — same Spotify ID, core title matches after manual review)

**Important correction to the previous pass's method:** raw string-similarity
on filenames is unreliable when both titles share a remix/edit suffix (e.g.
"X - Radio Edit" vs "Y - Radio Edit" scores artificially high). Every group
below was manually re-checked against its *core* title, not the raw score:

| Song | Evidence | Note |
|---|---|---|
| Ramba Ho | Same Spotify ID, identical title, Bollywood + Punjabi copies | High confidence |
| Crazy For It (Rampa) | Same Spotify ID, core title matches | High confidence |
| Let's Nacho | Same Spotify ID, identical title (one has placeholder artist "Indian") | High confidence |
| Play / Players | Same Spotify ID, titles differ by one word | Lower confidence — could be truncation or could be two different songs |
| Zulfa / Zulfaan | Same Spotify ID, spelling variant | Lower confidence — same caveat |

### FALSE POSITIVE — confirmed mistagging bug, NOT duplicates

**This is the same defect class that produced the ENHYPEN false positive.**
Manual review (not the flawed automated similarity score) found the
*embedded Spotify ID is simply wrong* on at least one side of each pair —
the songs are genuinely different:

| Group | Files | Verdict |
|---|---|---|
| ENHYPEN "Mixed" | Alone / Oh Will / Somebody Else / Trumpet | **Confirmed again** — all 4 share one Spotify ID (`6S0By3u06t…`) despite being 4 different songs |
| Goodums / on & on | Both "Sammy Virji Remix" | Different core titles, same ID |
| Calling Out Your Name / Fight to Love | Both "Radio Edit" | Different core titles, same ID |
| Cruel Summer (Again) [Tom Enzy Remix] / Let's Go…[HUGEL Remix] | — | Different core titles, same ID |
| OK OK - Hamdi remix / Sweet Shop - Hamdi Remix | — | Different core titles, same ID |
| Beba / Pepas | — | Two different, well-known real reggaeton songs, same ID |
| Dil Nu / Dil Pe Zakham Khate Hain | Mongo-level | Different artists, different titles, same ID (the previous pass's "probable" classification for this one was **wrong** — corrected here) |

**Root-cause hypothesis (not investigated further — flagged for a future
pass):** the pattern concentrates in remix/edit-titled tracks, suggesting
the Spotify-matching step, when it can't find an exact match for a
remix/edit version, may be falling back to the nearest/most-recent
candidate and reusing *its* ID incorrectly. This is a tagging-pipeline bug,
not a duplicate-library problem, and touches `strict_matcher.py`/Spotify
search logic — **not modified in this audit**, per scope.

### AMBIGUOUS (weak evidence only — left untouched)

The remaining ~10 groups from the previous pass's fuzzy title/artist match
(`duplicate_groups.json`) that don't have content_hash or clean same-ID
evidence remain in the weak-evidence bucket and were not re-examined
individually in this pass — no deletion is recommended for any of them.

## 7. Corruption Status

| Check | Result |
|---|---|
| Deleted corrupt MP3s still present physically | **0 / 19** |
| Orphan `.part` still present | **0 / 1** |
| Corresponding live Mongo records still present | **0 / 21** |
| 13 released Spotify IDs cleared from `ingest_tracks.json` | **13 / 13 confirmed absent** |
| 6 no-Spotify-ID corrupt files — automatic re-fetch identity | **None exists** (confirmed, see Section 5) |

**Cleanup is fully verified — nothing left behind.**

## 8. Dis Badman Verification

| Check | Result |
|---|---|
| Physical copies remaining | **1** (`Library/UK Garage/Dis Badman - Sammy Virji.mp3`) |
| Mongo records remaining | **1** (`sp:5SYwlompkdiAsMzOc3TEKq`) |
| Path correct | Yes |
| Genre routing correct | Yes (`Library/UK Garage`) |
| BPM | **132** (confirmed, with `bpm_corrected_reason` field documenting the fix) |
| Spotify ID present | Yes (`5SYwlompkdiAsMzOc3TEKq`) |
| Second copy remains | **No** |

Fully verified, no issues.

## 9. Gasolina Verification

| Check | Result |
|---|---|
| Physical copies remaining | **1** (`Library/Latin/Gasolina - Daddy Yankee.mp3`) |
| Mongo records remaining | **1** (`sp:5YoITs1m0q8UOQ4AW7N5ga`) |
| Duplicate Mongo identity gone | Yes |
| Spotify ID consistent | Yes |
| Path consistent | Yes |
| Metadata consistent | Yes (Gemini enrichment, fingerprint, all intact on the surviving doc) |

Fully verified, no issues.

## 10. Same Day Cleaning

**Still exists, still a genre-routing bug** — untouched, as instructed.

- **Current track count: 9** (down from 10 — Dis Badman's copy there was removed during cleanup, but that was a duplicate-resolution side effect, not a fix to this issue)
- Every remaining track's `genre_folder` is still the literal string `"Same Day Cleaning"` (the Sammy Virji EP's own ID3 album tag, used as a routing folder)
- **Scope of the issue: folder placement only.** It does not corrupt `library_index` data integrity — every doc has valid `title`/`artist`/`identity_key`/`audio_features`, just an incorrect `genre_folder` value.
- **Effect on recommendation/training/DJ Coach:** minimal but real. `recommendation_service.py` never uses genre in scoring (confirmed unchanged, Section 11). `training_service.py`'s `_genre_family()` treats `"Same Day Cleaning"` as its own distinct genre family for `GENRE_CROSSOVER` purposes — meaning a Sammy Virji track from this folder paired with any other UK Garage track would incorrectly register as a "genre crossover" exercise, when musically it's the same genre. Not observed in today's actual generated session (Section 12), but is a latent, real inaccuracy.

## 11. Recommendation Engine Health

| Metric | Value |
|---|---|
| Candidate pool size (current) | **2,094** |
| Invalid BPM in pool | **0** |
| Invalid Camelot in pool | **0** |
| Missing required fields in pool | **0** |
| Existing test suite | **236/236 pass** |
| Live sample `recommend_next()` calls (10, real tracks incl. Same Day Cleaning tracks) | **10/10 succeeded, 0 errors** |
| Regression vs. pre-cleanup | **None observed** |

`recommendation_service.py` was not modified — confirmed via `git status`
(no changes) and via the test suite still passing unchanged.

## 12. DJ Coach Health

| Check | Result |
|---|---|
| Exercise count | **5** |
| All 5 types present | Yes — `EASY_HARMONIC, BPM_TRANSITION, ENERGY_TRANSITION, GENRE_CROSSOVER, CHALLENGE` |
| Stage order correct | Yes — `WARM_UP, TECHNIQUE, ENERGY_CONTROL, CROSSOVER, CHALLENGE` |
| Real library tracks | Yes |
| Deleted/corrupt tracks appearing in session | **0** |
| Duplicate source/target pairs | **0** |
| Deterministic (2 calls, same date) | Yes — exercises and title byte-identical across both calls |
| Title today | "Genre Bridge Session" |
| Difficulty | score 0.3003, INTERMEDIATE, EASY_TO_HARD |
| Estimated duration | 55 minutes |

No regression. (Today's specific title/difficulty differ slightly from the
Phase 3 report's example run — expected, since the deterministic seed is
derived from the candidate pool's fingerprint, which legitimately changed
after removing 21 tracks.)

## 13. Remaining Unresolved Items

1. **Spotify-ID mistagging bug** (Section 6) — at least 7 groups of
   genuinely different songs share one Spotify ID. Root cause not
   investigated; likely in the Spotify-matching fallback logic for
   remix/edit titles.
2. **3 "collision-suffix sibling" pairs** (Sections 4C/4D) — the same
   class of issue Phase 1E fixed 39 instances of; these 3 weren't part of
   that fix.
3. **1 genuine orphan** — `Thot Shit - Megan Thee Stallion.mp3`, a real
   Library file with valid tags but no library_index record at all.
4. **4 "KLICKAUD" files with no ID3v2 header** — playable but untagged;
   likely need a different tagging path to ever get indexed.
5. **Same Day Cleaning** — 9 tracks still mis-routed; a real but low-severity
   genre-folder-placement issue, not a data-integrity issue.
6. **9 zero-duration-but-playable MP3s** — same benign mutagen/VBR-header
   quirk identified in Phase 1E; not re-verified in this pass.
7. **~10 weak-evidence "ambiguous" duplicate groups** from the prior pass —
   left untouched, no new evidence gathered on them in this audit.
8. **5 "probable" duplicate groups** (Section 6) and **8 confirmed
   content_hash duplicate groups** — none deleted, awaiting your decision.

## 14. Recommended Next Actions (not performed)

In rough priority order:

1. **Investigate the Spotify-ID mistagging bug's root cause** in the
   matching/tagging pipeline (likely `strict_matcher.py`'s remix/edit
   fallback behavior) — this is actively producing wrong metadata on new
   downloads, not just a historical artifact.
2. Resolve the 8 **confirmed** (content_hash) duplicate groups — highest
   confidence, lowest risk of the remaining duplicate work.
3. Resolve the 3 collision-suffix sibling pairs (Category C/D) using the
   same path-repoint approach Phase 1E already validated for 39 other
   tracks.
4. Investigate the single genuine orphan (`Thot Shit`) and the 4
   "KLICKAUD" untagged files to get them into `library_index`.
5. Decide on the 5 "probable" (same-Spotify-ID, title-confirmed) groups.
6. Fix the `Same Day Cleaning` genre routing (reroute 9 tracks to `Library/UK
   Garage`, matching Dis Badman's now-correct placement) — cosmetic/low
   urgency.
7. Leave the ~10 weak-evidence ambiguous groups alone unless you want to
   review them individually.

**None of these were performed in this task**, per its read-only, audit-only
scope.

---

## 15. Safety Verification

| | Count |
|---|---|
| MongoDB writes | **0** |
| Mongo documents created | **0** |
| Mongo documents deleted | **0** |
| Physical files modified | **0** |
| Physical files deleted | **0** |
| Physical files moved | **0** |
| Physical files renamed | **0** |
| ID3 modifications | **0** |
| Downloads | **0** |
| `ingest_tracks.json` modifications | **0** (confirmed: file's last-modified timestamp predates this audit, matching exactly when the cleanup itself ran) |
| `recommendation_service.py` changes | **0** |
| `training_service.py` changes | **0** |
| `dj_coach_service.py` changes | **0** |

Mongo document counts were read at the start and end of this audit:
**2,282 total / 2,150 live, both times** — zero drift.
