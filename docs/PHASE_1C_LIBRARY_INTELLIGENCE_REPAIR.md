# Phase 1C — Library Intelligence Repair

**Date:** 2026-09-07
**Scope:** MongoDB intelligence layer only. The physical library was never modified.
**Outcome:** Complete. Recommendation-engine scoreable pool **410 → 1,919 (17.7% → 98.2%)**, with **zero field values lost**.

---

## Pre-Repair State

Measured from the pre-repair backup using the same non-empty predicate as every "after" figure.

| Metric | Value |
|---|---|
| Physical MP3 files | 2,003 (1,975 valid · 28 damaged) |
| `library_index` documents | 2,481 |
| Live documents (`missing != true`) | 2,310 |
| Soft-deleted | 171 |
| Shadow (double-indexed) records | 356 |
| Physical orphans | 48 |
| Mongo orphans | 0 |
| **Engine-scoreable (bpm + camelot)** | **410 / 2,310 (17.7%)** |

The physical library was healthy; the index and analysis layers were not — the conclusion Phase 1B reached and this phase acted on.

---

## Backup

Taken before any write, and verified by reloading and asserting document count and `_id` presence.

| Field | Value |
|---|---|
| Path | `<session scratchpad>/library_index_backup_20260907T092152Z.json` |
| Timestamp (UTC) | 2026-09-07T09:21:52Z |
| Database / collection | `spotify_downloader` / `library_index` |
| Documents | **2,481 (the entire collection, not just affected docs)** |
| Size | 10,391,555 bytes |
| SHA-256 | `ff56f228b634ae36e5920246cee7f37f1cb3557196c8356baf1580342fc99bba` |
| Format | `bson.json_util.dumps` — lossless BSON, `_id` preserved as `$oid` |

Every field of every document is preserved, including all fields not named in the phase specification. The backup has not been modified or overwritten.

---

## Repair Policy

| Rule | Applied |
|---|---|
| Identity | `sp:<spotify_id>`, else `ch:<content_hash>` — the project's own convention (`services/dedup_service.py:71`). No new scheme invented. |
| Write mode | Field-scoped `$set` only. **No whole-document or whole-subdocument write anywhere.** |
| Metadata | Fill **only** when the Mongo field is null/absent/empty. Existing values never overwritten. |
| Protected | `gemini_*`, `lastfm_*`, `audio_fingerprint`, `acoustid_id`, `fingerprint_source`, `genre_folder`, `content_hash` |
| BPM / key | Embedded ID3 values are metadata truth. Fresh librosa results were compared and recorded, **never written over them**. |
| Analysis | librosa wrote **only** `rms_energy`, `spectral_centroid_mean`, `zero_crossing_rate`, `duration_sec`, `confidence`. |
| Spotify ID | **Not written** for files whose tag ID collides across multiple physical files. |
| Physical files | Never modified, moved, renamed, retagged, or deleted. |

The dry run and the commit produced **identical** counts (2,338 changes / 59 skipped / 0 errors), so nothing drifted between planning and execution.

---

## Shadow Record Resolution

| Metric | Value |
|---|---|
| Pairs resolved | **356 / 356 (100%)** |
| Conflicts | **0** |
| Fields salvaged from shadows before deletion | 161 across 54 documents |
| Documents removed | **356** |
| Shadow records remaining | **0** |

Each pair was one physical file indexed twice — a canonical `sp:<spotify_id>` record and a `file:<stem>` shadow — created because the same path was written with mixed `/` and `\` separators. Every pair was verified at delete time for identical Spotify ID, identical normalized path, and unchanged identity. Fields present on the shadow but absent on the canonical were salvaged first.

**996 live `file:` records remain and were deliberately left untouched** — they are legitimately indexed files that simply have no Spotify ID, not shadows. Only proven pairs were removed.

---

## Physical Orphan Recovery

| Outcome | Count |
|---|---|
| Indexed | **1** |
| Review — identity held by a soft-deleted record | **39** |
| Review — no Spotify ID and incomplete tags | **8** |
| **Total** | **48** |

**The 39 were not auto-indexed, and this is deliberate.** Each is an un-suffixed file (`Aankhein Khuli.mp3`) whose canonical identity is held by a *soft-deleted* record pointing at a `_1.mp3` variant. All 39 of those variants were verified **gone from disk**, so the surviving file is almost certainly the right one — but resolving this means reversing a soft-delete, which is a user decision, not an automatic repair. Inserting a new document would also have violated the unique `identity_key` index.

The 8 others are `.trimmed.mp3` files and KLICKAUD free-download trance tracks with no title/artist tags. Indexing them would have required guessing identity.

---

## Metadata Recovery

**1,927 documents received 17,974 field writes**, all recovered from embedded ID3 tags.

| Field | Documents |
|---|---|
| `album_artist` | 1,909 |
| `audio_features.duration_sec` | 1,925 |
| `audio_features.key_raw_tag` | 1,919 |
| `audio_features.bpm` / `.key` / `.camelot` | 1,509 each |
| `title` | 1,497 |
| `content_hash` | 1,493 |
| `artist` | 1,490 |
| `album` | 911 |
| `track_number` | 766 |
| `year` | 747 |
| `spotify_id` | 479 |
| `gemini_genre` | 311 |

`content_hash` was computed with the project's own `dedup_service.content_hash(title, artist, duration_ms)` — a **metadata** hash, not a file-byte hash. Populating it with SHA-256 would have silently corrupted the field's meaning.

---

## BPM / Key / Camelot Recovery

| Metric | Value |
|---|---|
| BPM values recovered | 1,509 |
| Key values recovered | 1,509 |
| Camelot generated (via existing `tkey_to_camelot()`) | 1,509 |
| Raw tag spelling preserved in `audio_features.key_raw_tag` | 1,919 |
| BPM values rejected as malformed | **0** |
| Keys that failed to parse | **0** |

BPM was parsed defensively and only accepted in the 40–250 range. Camelot was generated only from a successfully parsed key — never invented.

The original tag spelling is preserved in `key_raw_tag` alongside Mongo's own `key`, so no raw information was destroyed by normalization.

---

## Spotify Identity Recovery

| Metric | Value |
|---|---|
| `spotify_id` values written | **479** |
| Coverage | 958 → **1,437 / 1,955 (41.5% → 73.5%)** |
| Writes **blocked** by collision guard | **20** |
| Collision groups detected | **10** (22 physical files) |
| Spotify IDs invented or inferred | **0** |

**The collision guard was necessary, not precautionary.** Ten Spotify IDs appear on multiple, genuinely different physical files — one ID (`6S0By3u06ttb3kU2XEtWnw`) is tagged onto four different Pop tracks; another (`5fwSHlTEWpluwOM0Sxnh5k`) is on both `Beba.mp3` and `Pepas.mp3`. These are tagging errors, so `TXXX:spotify_id` is not trustworthy for those files. Writing them would have corrupted canonical identity. All are recorded for review; no physical file was touched and no record deleted.

---

## Content Hash Verification

The 10 suspicious size+duration groups Phase 1B flagged were SHA-256 hashed read-only.

| Classification | Groups |
|---|---|
| `TRUE_BYTE_DUPLICATE` | **6** |
| `DIFFERENT_FILE` | **4** |
| `UNKNOWN` | 0 |

**This corrects Phase 1B.** I had flagged four differently-named Ajja tracks and a `BUN MASKA`/`PISHA` pair as *possibly* the same audio saved under different names. Hashing proves they are `DIFFERENT_FILE` — that suspicion was wrong, and no download-integrity problem exists there.

The 6 genuine byte-duplicates are cross-crate copies: `Bebopper - Ajja.mp3` in both Electronic and Techno, and five KLICKAUD tracks duplicated across PSY and Trance. **Nothing was deduplicated** — this phase was verification only.

---

## Audio Analysis

| Metric | Value |
|---|---|
| Targets | 1,953 |
| **Persisted** | **1,933 (99.0%)** |
| Failed | **20 (1.0%)** — all previously-known damaged files |
| Elapsed | 1,635 s (27.2 min), 0.84 s/track |

Each of the 1,933 received all five analytical fields: `rms_energy`, `spectral_centroid_mean`, `zero_crossing_rate`, `duration_sec`, `confidence`.

### Fresh detector vs embedded tags (recorded, never written)

| Comparison | Agree | Differ | Agreement |
|---|---|---|---|
| BPM | 843 | 1,074 | 44.0% |
| Camelot | 1,306 | 611 | 68.1% |

**This is a caveat worth stating plainly.** Tag-derived BPM/key were kept as truth per policy, but the freshly computed `confidence` describes *librosa's* key detection, which agrees with the stored Camelot only 68% of the time. Confidence is therefore a good signal for roughly two-thirds of tracks and an approximate one for the rest. The BPM divergence is largely explained by the half/double-time genre-hint correction in `detect_bpm_and_key()`, which used `genre_folder` on this run and may not have been applied when the tags were first written. Reconciling the two is follow-up work, not a Phase 1C regression.

---

## Damaged Track Manifest

`docs/PHASE_1C_DAMAGED_TRACKS.json` and `docs/PHASE_1C_REACQUISITION_MANIFEST.json`.

| Category | Count |
|---|---|
| Damaged physical files | **28** (62.4 MB) |
| → `REACQUIRE_BY_SPOTIFY_ID` | **23** |
| → `REACQUIRE_BY_METADATA_SEARCH` | **4** |
| → `REVIEW_MANUALLY` | **1** |
| Dead-letter tracks, already present | 13 |
| Dead-letter tracks, genuinely missing | **6** |
| **Total re-acquisition candidates** | **34** |

Failure reasons: 19 `HeaderNotFoundError` (truncated downloads), 9 no decodable audio stream. Concentrated in Dubstep (10 of 43 in that crate).

**Nothing was downloaded.** Every entry carries `"download_authorised": false`. The 4 metadata-search and 1 manual-review entries were deliberately not resolved by search.

---

## Post-Repair State

| Metric | Before | After |
|---|---|---|
| Physical files | 2,003 | **2,003 (unchanged)** |
| `library_index` documents | 2,481 | 2,126 |
| Live documents | 2,310 | 1,955 |
| Soft-deleted | 171 | 171 |
| **Shadow records** | 356 | **0** |
| **Mongo orphans** | 0 | **0** |
| Physical orphans | 48 | 48 (1 indexed, 47 in review) |

---

## Before vs After Metrics

Live documents. Both columns use an identical non-empty predicate.

| Signal | Before | After | Δ |
|---|---|---|---|
| **BPM** | 410 (17.7%) | **1,919 (98.2%)** | +1,509 |
| **Key** | 410 (17.7%) | **1,919 (98.2%)** | +1,509 |
| **Camelot** | 410 (17.7%) | **1,919 (98.2%)** | +1,509 |
| **RMS energy** | 2 (0.1%) | **1,935 (99.0%)** | +1,933 |
| **Spectral centroid** | 2 (0.1%) | **1,935 (99.0%)** | +1,933 |
| **Zero-crossing rate** | 2 (0.1%) | **1,935 (99.0%)** | +1,933 |
| **Key confidence** | 2 (0.1%) | **1,935 (99.0%)** | +1,933 |
| Duration | 2 (0.1%) | 1,935 (99.0%) | +1,933 |
| **Title** | 420 (18.2%) | **1,918 (98.1%)** | +1,498 |
| **Artist** | 422 (18.3%) | **1,913 (97.9%)** | +1,491 |
| Album | 0 (0.0%) | 911 (46.6%) | +911 |
| Spotify ID | 958 (41.5%) | 1,437 (73.5%) | +479 |
| Content hash | 419 (18.1%) | 1,912 (97.8%) | +1,493 |
| Fingerprint | 2,310 (100%) | 1,955 (100%) | held |
| Gemini genre | 1,635 (70.8%) | 1,893 (96.8%) | +258 |
| Last.fm genre | 7 (0.3%) | 7 (0.4%) | held |
| **ENGINE scoreable** | **410 (17.7%)** | **1,919 (98.2%)** | **+1,509** |
| **ENGINE fully usable** | 410 (17.7%) | **1,910 (97.7%)** | +1,500 |

> **Correction to an earlier figure.** An interim run of this table reported Last.fm dropping 401 → 7, which looked like data loss. It was a measurement error on my side: the "before" count used `$ne: null`, which includes the **empty-string markers** `backfill_lastfm.py` writes to mean "attempted, no data" (`backfill_lastfm.py:140`). Only 7 documents ever held a real Last.fm genre, and all 7 survive. Every "before" number in this table is now recomputed from the backup with the identical predicate used for "after".

The three fields Phase 0 identified as blocking the recommendation engine — energy, spectral centroid, and key confidence — went from **0.1% to 99.0%**. The engine's dead 30% scoring weight and permanently-halved Camelot term are both resolved.

---

## Conflicts / Review Queue

**47 items requiring a human decision. None were auto-resolved.**

| Item | Count | Note |
|---|---|---|
| Orphans whose identity is on a soft-deleted record | 39 | `_1.mp3` twin confirmed gone from disk |
| Orphans with no Spotify ID and incomplete tags | 8 | `.trimmed.mp3` and KLICKAUD tracks |
| Spotify ID collisions | 10 groups (22 files) | Tagging errors; guard blocked 20 writes |
| ID3 vs Mongo artist disagreement | 31 | Genuine semantic conflict, e.g. tag `Mithoon` vs Mongo `Ritviz` |
| ID3 vs Mongo title disagreement | 17 | Genuine semantic conflict |
| ID3 vs Mongo key disagreement | 408 | **Notation-only** — all 22 sampled resolve to identical Camelot (`F maj` vs `F`, `A min` vs `Am`). Cosmetic. |
| Byte-identical duplicate groups | 6 | Verified, not deduplicated |

No existing Mongo value was overwritten in any of these cases.

---

## Errors

| Stage | Errors |
|---|---|
| Shadow resolution | **0** |
| Orphan indexing | **0** |
| Metadata fill | **0** |
| Audio analysis | **20** — all previously-catalogued damaged files |

### One process failure worth recording

The first two analysis launches ran for ~25 minutes and wrote nothing. Root cause: `persist_audio_features()` goes through `database.py`, which resolves `MONGODB_URI` via `os.getenv` and **falls back to `mongodb://localhost:27017`**. The standalone script had parsed `.env` for its own client but never exported it, so every write failed against localhost after a 10 s connection timeout. Fixed by exporting `MONGODB_URI`/`MONGODB_DB` before import, plus a fail-fast assertion. **No partial or corrupt writes resulted** — every failed attempt returned `False` and wrote nothing. This is a latent trap for any future standalone script in this repo.

---

## Validation Results

| Check | Result |
|---|---|
| `tests.test_audio_feature_persistence` | pass |
| `tests.test_recommendation_service` | pass |
| `tests.test_strict_matcher` | pass |
| `tests.test_library_migrator`, `tests.test_index_recovery` | pass |
| **Total** | **118 tests, OK** |
| `py_compile` on all touched modules | OK |
| Application code changed | **None** |

### Data-integrity audit — backup vs. live, every field of every document

| Check | Result |
|---|---|
| Documents removed | 356 — **all confirmed shadow pairs**, zero unexpected |
| **Field values lost on surviving documents** | **NONE (0)** |
| Field values changed on surviving documents | `audio_features.analysis_timestamp` on 408 — expected provenance update |
| `gemini_*` / `lastfm_*` / fingerprints / Spotify IDs / `genre_folder` lost | **0** |

This is the strongest available evidence that Phase 1A's field-scoped `persist_audio_features()` held: 1,933 analysis writes landed on documents carrying enrichment, and not one enrichment value was destroyed.

---

## Remaining Work

1. **47 review-queue items** — 39 soft-delete revivals, 8 untagged files.
2. **34 re-acquisition candidates** — 29 by Spotify ID, 4 by metadata search, 1 manual. Not authorised.
3. **10 Spotify ID collision groups** — tagging errors needing a decision.
4. **6 byte-identical duplicate groups** — verified, awaiting a dedup decision.
5. **48 artist/title conflicts** between tags and Mongo.
6. **BPM/Camelot reconciliation** — 44%/68% agreement between tags and fresh detection.
7. **518 live documents (26.5%) still have no Spotify ID** — unchanged; no local source has one.
8. CI still does not run the test suite.

---

## Rollback Procedure

The backup contains every field of all 2,481 pre-repair documents with `_id` preserved, so a full restore is genuinely possible.

```bash
# Full restore of library_index to its pre-Phase-1C state.
# Replace <BACKUP> with the path in the Backup section above.
python - <<'PY'
import os
from bson import json_util
from pymongo import MongoClient
os.environ.setdefault("MONGODB_URI", "<your MONGODB_URI from backend/.env>")
docs = json_util.loads(open(r"<BACKUP>", encoding="utf-8").read())
assert len(docs) == 2481, f"expected 2481 docs, got {len(docs)}"
col = MongoClient(os.environ["MONGODB_URI"])["spotify_downloader"]["library_index"]
col.delete_many({})          # collection is fully represented in the backup
col.insert_many(docs)        # _id values are preserved, so identity is restored exactly
print("restored", col.count_documents({}))
PY
```

Verify the SHA-256 in the Backup section before restoring.

**Physical rollback requires no operation** — no audio file was modified, moved, renamed, retagged, or deleted at any point in this phase.

Partial rollback is also possible: `PHASE_1C_REPAIR_MANIFEST.json` records every change with `_id`, `identity_key`, fields changed, reason, and source.

---

## Final Status

### Exact counts

| Measure | Count |
|---|---|
| Mongo documents **modified** | **1,927** (metadata fill; 54 also received shadow salvage) |
| Mongo documents **modified by analysis** | **1,933** |
| Mongo documents **created** | **1** |
| Mongo documents **removed** | **356** (all verified shadow pairs) |
| Mongo documents **skipped** | **59** |
| Items **requiring manual review** | **47** |
| Total field writes (metadata) | 17,974 |
| Total field writes (analysis) | 9,665 (5 × 1,933) |
| **Physical files touched** | **0** |
| **Physical files deleted / moved / renamed** | **0** |
| Analysis success / failure | **1,933 / 20** |
| Files downloaded | **0** |
| Application source files changed | **0** |

### Success criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Recoverable embedded metadata reflected in Mongo | ✅ 17,974 field writes |
| 2 | 356 shadow records resolved | ✅ 356 → 0, zero conflicts |
| 3 | 49 orphans indexed or placed in review | ✅ 1 indexed, 47 in review |
| 4 | Existing enrichment survives | ✅ **0 values lost**, verified against backup |
| 5 | BPM/key/Camelot restored | ✅ 17.7% → 98.2% |
| 6 | RMS/spectral/confidence restored | ✅ 0.1% → 99.0% |
| 7 | Content hashes verify suspicious duplicates | ✅ 6 true, 4 disproved |
| 8 | No physical files deleted/moved/renamed | ✅ 0 |
| 9 | No Spotify IDs invented | ✅ 0; 20 writes blocked by guard |
| 10 | No whole-subdocument overwrites | ✅ field-scoped `$set` only |
| 11 | All affected documents backed up | ✅ 2,481 docs, SHA-256 recorded |
| 12 | Post-repair reconciliation clean | ✅ 0 shadows, 0 Mongo orphans |
| 13 | Existing tests pass | ✅ 118/118 |
| 14 | Repair report + reacquisition manifest exist | ✅ 4 documents |

### Recommendation

**Phase 1C is complete and verified. The library intelligence layer is repaired.**

The recommendation engine now has **1,919 scoreable tracks (98.2%)**, up from 410 (17.7%), and — critically — the energy, spectral, and confidence signals that were sitting at 0.1% are now at 99.0%. The engine's dead 30% scoring weight and permanently-halved Camelot term, identified in Phase 0 as the blockers to DJ Coach, are resolved.

**Recommended next step:** re-run `backend/validate_recommendation_service.py` against the repaired data to confirm real-world scoring behaviour before any Phase 2 work. The 47 review items and 34 re-acquisition candidates are independent and can be handled separately; none blocks the recommendation engine.

Phase 1C stops here. No DJ Coach, training engine, trend layer, VirtualDJ, ML, or LLM work was started.
