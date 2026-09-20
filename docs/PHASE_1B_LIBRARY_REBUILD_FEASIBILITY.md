# Phase 1B — Library Rebuild Feasibility Audit

**Date:** 2026-09-07
**Type:** Read-only forensic audit. No writes of any kind were performed.
**Library root:** `C:\Users\Aswin-pc\Desktop\DJ music` (from `BASE_DOWNLOAD_DIR` in `backend/.env`)

---

## Executive Summary

**The physical library is healthy. The MongoDB index and the audio-analysis layer are what is incomplete.**

The core question — "is the physical library actually bad, or is the Mongo/index/audio-intelligence layer merely incomplete?" — is answered decisively by one measurement:

> **1,904 files carry a BPM and key in their ID3 tags that MongoDB does not have.**

The tags are richer than the database on almost every axis. Physical files have title on **1,953 / 2,002 (97.6%)** and BPM/key on **1,958 / 2,002 (97.8%)**; the live index has title on **419 / 2,309 (18.1%)** and BPM on **409 / 2,309 (17.7%)**. The data was never lost — it was never read back in.

Three further findings decide the recommendation:

1. **Zero Mongo orphans.** All 2,309 live records point at a file that exists on disk. Nothing in the index refers to a vanished file.
2. **The 356 "ambiguous" matches are not ambiguous.** Each is one physical file indexed *twice* — once as `sp:<spotify_id>`, once as `file:<stem>` — because the same path was written with two different separator spellings. All **356 / 356 (100%)** of these pairs share an identical `spotify_id`, so the merge is deterministic, not a judgement call.
3. **Only 28 / 2,002 (1.4%) files are actually damaged**, totalling 62.4 MB, and **23 of those 28 (82.1%)** still carry a Spotify ID in their tags, so they can be re-acquired precisely.

A clean rebuild would re-download 19.49 GB to fix 1.4% of the library, and would permanently lose the **932 files (46.6%) that have no Spotify ID** and therefore no canonical re-acquisition path.

**Recommendation: B — KEEP CURRENT LIBRARY + REANALYZE/REPAIR INTELLIGENCE.**

---

## Physical Library

| Metric | Value |
|---|---|
| Total audio files | **2,002** |
| Total size | **19,485,151,188 bytes (19.49 GB)** |
| Formats | `.mp3` — 2,002 / 2,002 (100%) |

No `.flac`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.opus`, or `.wma` files exist. The library is uniformly MP3.

### Distribution by root

| Root | Files | % |
|---|---|---|
| `Library/` | 1,986 | 99.2% |
| `Same Day Cleaning/` | 10 | 0.5% |
| `Ingest/` | 6 | 0.3% |

`Manual/`, `NeedsReview/`, and `Unknown/` exist as directories but contain no audio files.

### Distribution by crate

| Crate | Files | Crate | Files |
|---|---|---|---|
| Punjabi | 482 | R&B | 46 |
| Bollywood | 421 | Dubstep | 43 |
| Electronic | 245 | UK Garage | 42 |
| House | 202 | Tamil | 15 |
| Latin | 124 | Grime | 10 |
| Trance | 106 | Techno | 6 |
| Pop | 85 | PSY | 5 |
| Drum & Bass | 77 | Ingest/Uncategorized | 5 |
| Hip Hop | 77 | Ingest/Ap Dhillion | 1 |

### Validity

| Status | Count | % |
|---|---|---|
| **VALID** (opens, has decodable audio stream) | **1,974** | **98.6%** |
| **INVALID/CORRUPT** | **28** | **1.4%** |
| **UNKNOWN/UNABLE TO CHECK** | **0** | **0.0%** |

Breakdown of the 28 invalid files:

| Failure | Count | Interpretation |
|---|---|---|
| `HeaderNotFoundError` | 19 | Truncated / partial download — no valid MPEG header |
| No decodable audio stream | 9 | Container opens but reports zero length |

- Zero-byte files: **0**
- Combined size of damaged files: **62.4 MB (0.3% of library by size)**
- Damaged files still exposing a Spotify ID in tags: **23 / 28 (82.1%)**
- Damaged files by crate: Dubstep 10, Hip Hop 4, R&B 4, Latin 2, Pop 2, UK Garage 2, Bollywood 1, House 1, Punjabi 1, Ingest 1

The Dubstep concentration (10 / 43 = 23.3% of that crate) suggests a single failed ingest batch rather than diffuse rot.

### Non-audio files present

| Type | Count | Significance |
|---|---|---|
| `.json` dead-letter manifests | 19 | `Ingest/.retry_queue/dead/` — authoritative Spotify identity, see *Manifest / Historical Data* |
| `.vdjstems` | 13 | **VirtualDJ stem-separation cache — confirms active VirtualDJ use** (relevant to a future Phase 6) |
| `.part` | 1 | `Ingest/Staging/Indian Flair - Augusto Yepes.mp4.part` — one incomplete download |

---

## Metadata Coverage

Read from ID3 tags of all 2,002 physical files. Percentages are of 2,002.

| Tag | Files | % |
|---|---|---|
| BPM (`TBPM`) | 1,958 | **97.8%** |
| Key (`TKEY`) | 1,958 | **97.8%** |
| Title (`TIT2`) | 1,953 | **97.6%** |
| Artist (`TPE1`) | 1,948 | **97.3%** |
| **Title + Artist together** | **1,948** | **97.3%** |
| Album artist (`TPE2`) | 1,948 | 97.3% |
| Genre (`TCON`) | 1,605 | 80.2% |
| `TXXX:gemini_genre` | 1,582 | 79.0% |
| Spotify ID | 1,070 | 53.4% |
| Album (`TALB`) | 925 | 46.2% |
| Track number (`TRCK`) | 777 | 38.8% |
| Year (`TDRC`) | 757 | 37.8% |
| MusicBrainz ID | **0** | **0.0%** |
| AcoustID in tags | **0** | **0.0%** |

Most-common ID3 frames observed: `TXXX` (25,547 instances), `GEOB` (12,191), `COMM` (3,415), `TBPM` (1,958), `TKEY` (1,958), `APIC` artwork (1,939).

MusicBrainz and AcoustID identifiers are absent from tags entirely — fingerprints live only in MongoDB (where coverage is 100%).

---

## Spotify Identity Coverage

**Storage location:** every discovered ID came from a single frame — `TXXX:spotify_id`, holding a bare 22-character base62 string. **1,070 / 1,070 (100%)** of discoveries used this frame. No `spotify:track:` URIs or `open.spotify.com` URLs were found in `COMM` or `WXXX` frames.

| Class | Count | % of 2,002 |
|---|---|---|
| **A — Valid-looking Spotify track identity** | **1,070** | **53.4%** |
| **B — Invalid / malformed** | **0** | **0.0%** |
| **C — Ambiguous** | **0** | **0.0%** |
| **D — Missing** | **932** | **46.6%** |

Unique Spotify IDs across physical files: **1,058** (12 IDs appear on more than one file — see *Duplicate Analysis*).

No Spotify IDs were inferred from filenames. The repository defines no filename→ID format, so per the audit constraints none was assumed.

**The 932 files with no Spotify ID are the single most important constraint on any rebuild** — they have no canonical re-acquisition path.

---

## MongoDB Coverage

`library_index` — **2,480 documents total**.

| Segment | Count | % |
|---|---|---|
| Live (`missing != true`) | 2,309 | 93.1% |
| Soft-deleted (`missing == true`) | 171 | 6.9% |

### Live records (n = 2,309)

| Field | Count | % |
|---|---|---|
| `final_path` present | 2,309 | 100.0% |
| **`final_path` points to a file that exists** | **2,309** | **100.0%** |
| **`final_path` missing from disk** | **0** | **0.0%** |
| `audio_fingerprint` | 2,309 | 100.0% |
| `gemini_genre` (top-level or nested) | 1,635 | 70.8% |
| `spotify_id` | 957 | 41.4% |
| `identity_key` = `sp:…` | 957 | 41.4% |
| `identity_key` = `file:…` | 1,352 | 58.6% |
| `artist` | 421 | 18.2% |
| `title` | 419 | 18.1% |
| `content_hash` | 418 | 18.1% |
| `audio_features.bpm` / `.key` / `.camelot` | 409 | 17.7% |
| `audio_features.analysis_source` | 409 | 17.7% |
| `audio_features.confidence` | **2** | **0.1%** |
| `audio_features.rms_energy` | **2** | **0.1%** |

Distinct normalized `final_path` values among the 2,309 live records: **1,953**. Records whose `final_path` mixes `/` and `\` separators: **356 (15.4%)**.

### Soft-deleted records (n = 171)

| Field | Count | % |
|---|---|---|
| `final_path` still exists on disk | **3** | 1.8% |
| `final_path` gone | 168 | 98.2% |
| `spotify_id` | 67 | 39.2% |
| BPM / key / camelot | 31 | 18.1% |

Soft-deletion has been accurate: 98.2% of soft-deleted records correspond to genuinely absent files.

---

## Physical ↔ Mongo Reconciliation

Matching priority: exact normalized `final_path` → Spotify ID → fingerprint → content hash → strong metadata (normalized artist + title). Windows normalization via `os.path.normcase(os.path.normpath(...))`, handling `/` vs `\`, case, redundant separators, and drive-letter case.

### Every physical file categorised (n = 2,002)

| Category | Count | % |
|---|---|---|
| `MATCHED_CONFIDENTLY` | 1,597 | 79.8% |
| `MATCHED_WITH_STRONG_METADATA` | 0 | 0.0% |
| `AMBIGUOUS` | 356 | 17.8% |
| `UNMATCHED_PHYSICAL_FILE` | 49 | 2.4% |

All 1,597 confident matches resolved via exact normalized `final_path`. No file needed a metadata fallback.

### Every live Mongo record categorised (n = 2,309)

| Category | Count | % |
|---|---|---|
| `MATCHED_PHYSICAL_FILE` | 1,597 | 69.2% |
| `AMBIGUOUS_PHYSICAL_MATCH` | 712 | 30.8% |
| **`MISSING_PHYSICAL_FILE`** | **0** | **0.0%** |

### The ambiguity is mechanical, not genuine

The 356 ambiguous files and 712 ambiguous records are the same defect counted from both sides: **356 files each indexed twice**, as 356 × 2 = 712 records.

Every pair has the shape:

```
ik = sp:2GHKo6nrSjruvBEQbzD7Fw   sid = 2GHKo6nrSjruvBEQbzD7Fw   title = "Tiramisu"
   final_path = ...\DJ music\Library/Hip Hop\Tiramisu - Don Toliver.mp3     <- forward slash

ik = file:Tiramisu - Don Toliver  sid = (none)                    title = None
   final_path = ...\DJ music\Library\Hip Hop\Tiramisu - Don Toliver.mp3     <- backslash
```

- Maximum records on any one path: **2** (never 3+)
- Pairs sharing an identical `spotify_id`: **356 / 356 (100%)**
- Pairs with conflicting Spotify IDs: **0**
- Duplicate `identity_key` values among live records: **0**

The `file:` record is a degenerate shadow of the `sp:` record, created because the same path was written with a different separator spelling and so failed the uniqueness check. **Resolution is deterministic: keep the `sp:` record, drop the shadow.** This is the same mixed-separator defect that Phase 1A's `resolve_identity_key()` had to defend against.

### Effective reconciliation after shadow-merge

| Outcome | Count | % of 2,002 |
|---|---|---|
| Physical files confidently matched | **1,953** | **97.6%** |
| Physical files unmatched | 49 | 2.4% |
| Mongo records with no physical file | **0** | **0.0%** |

`1,953 distinct indexed paths + 49 unindexed files = 2,002` — the reconciliation balances exactly.

---

## Orphan Analysis

| Class | Count | % |
|---|---|---|
| **A. Physical orphan** (file exists, no confident live record) | **49** | 2.4% of files |
| **B. Mongo orphan** (live record, missing file) | **0** | 0.0% of live records |
| **C. Soft-deleted record whose file is present** | **3** | 1.8% of soft-deleted |
| **D. Physical file matching a soft-deleted record** | **3** | 0.1% of files |

The 49 physical orphans sit in `Library/` (44) and `Ingest/` (5). Sampled examples — `Library\Bollywood\Aankhein Khuli.mp3`, `Library\Bollywood\Barbaadiyan - Sachet Tandon.mp3`, `Ingest\Uncategorized\Chalak Chalak - From Devdas.mp3`. Several are the un-suffixed twin of an indexed `_1.mp3` file.

**These are healthy, playable files that were simply never indexed. They are not candidates for re-download.**

Class C (3 records) is a genuine inconsistency worth review later: a file present on disk marked deleted in the index. It is not a data-loss risk.

---

## Duplicate / Collision Analysis

| Class | Groups | Redundant copies | % of files |
|---|---|---|---|
| `EXACT_DUPLICATE` (identical byte size **and** duration) | 10 | 12 | 0.6% |
| `LIKELY_DUPLICATE` (same Spotify ID, different files) | — | 12 | 0.6% |
| `POSSIBLE_DUPLICATE` (same normalized title + artist) | — | 18 | 0.9% |
| Filename-collision-pattern groups | 12 | 12 | 0.6% |
| `UNIQUE` | — | **1,990** | **99.4%** |

Duplication is minimal. Confirmed cross-crate copies include `Bebopper - Ajja.mp3` in both `Electronic/` and `Techno/`, and `Maula Mere Maula.mp3` in both `Bollywood/` and `Trance/`.

### One finding requiring follow-up before any dedup action

Two `EXACT_DUPLICATE` groups are suspicious rather than clearly redundant:

- Four **differently-named** Ajja tracks (`Disco Bunny`, `Ninja Wobble`, `Not What`, `Original Spin`) share an identical byte size *and* identical duration.
- `Library\Hip Hop\BUN MASKA.mp3` and `Library\Hip Hop\PISHA - Calm.mp3` likewise.

Distinct titles with byte-identical size and duration is consistent with **the same audio having been saved under several names** (a download-integrity failure), but size + duration alone is not proof. **Per the audit constraints these are not asserted as duplicates.** Confirming requires a content hash, which only 418 / 2,309 (18.1%) of live records currently carry. This should be verified before any deduplication runs, and is the one place where the physical library may be worse than it appears.

---

## Audio Intelligence Recoverability

How much intelligence exists in the files but is absent from MongoDB. This is the decisive section.

| Signal | Present in file, absent in Mongo |
|---|---|
| **BPM** | **1,904** |
| **Key** | **1,904** |
| **Title** | **1,892** |
| **Artist** | **1,885** |
| **Spotify ID** | **539** |
| Gemini genre | 352 |

Side-by-side coverage:

| Signal | Physical tags | Live Mongo | Gap |
|---|---|---|---|
| BPM | 1,958 (97.8%) | 409 (17.7%) | **+1,549** |
| Key | 1,958 (97.8%) | 409 (17.7%) | **+1,549** |
| Title | 1,953 (97.6%) | 419 (18.1%) | **+1,534** |
| Artist | 1,948 (97.3%) | 421 (18.2%) | **+1,527** |
| Spotify ID | 1,070 (53.4%) | 957 (41.4%) | **+113** |

**A tag re-read alone — no downloading, no audio analysis — would lift BPM/key coverage from 17.7% to ~97.8% and title/artist from ~18% to ~97.3%.**

What tags **cannot** supply, because it was never written to them:

| Signal | Live Mongo coverage | Recovery route |
|---|---|---|
| `rms_energy` | 2 (0.1%) | librosa re-analysis only |
| `spectral_centroid_mean` | 2 (0.1%) | librosa re-analysis only |
| `confidence` (key detection) | 2 (0.1%) | librosa re-analysis only |

These three are exactly the fields Phase 0 identified as blocking the recommendation engine — 30% of the similarity weight is inert without energy and spectral values, and the missing confidence halves the camelot term. **They require re-analysis, not re-download.** The reference run in `backend/reports/audio_feature_backfill_report.json` measured 0.359 s/track, implying roughly **12 minutes** to analyse all 1,974 valid files.

---

## Manifest / Historical Data

| Source | Rows | Unique Spotify IDs | Usable as manifest? |
|---|---|---|---|
| Physical file tags | 2,002 | **1,058** | **Yes — richest source** |
| `library_index` (live) | 2,309 | 957 | Yes |
| `Ingest/.retry_queue/dead/*.json` | 19 | 19 | Yes — authoritative |
| `download_history` | 460 | **0** | **No** |
| `musicbrainz_cache` | — | 0 | No |

**`download_history` cannot contribute to a manifest.** Its 460 documents have no `spotify_id` field at all — the schema is `album, art_embedded, artist, bitrate_achieved, bpm, bpm_analyzed, downloaded_at, duration_match_diff, extra, filename, key, key_confidence, normalization_applied, query_stage_used, source_platform, tagging_report, title_similarity_score, track_title`. Identity there is filename-based, which the audit constraints forbid treating as authoritative.

### Dead-letter queue — 19 genuinely lost tracks

The 19 manifests in `Ingest/.retry_queue/dead/` each carry `spotify_id`, `artist`, `title`, `sha256`, `duration_ms`, and `intended_dest`. All 19 failed at the same `move` stage.

| Metric | Value |
|---|---|
| Manifests | 19 |
| Unique Spotify IDs | 19 |
| With `sha256` recorded | 19 (100%) |
| Staged files still on disk | **0** |
| Already present as a live Mongo record | **13 / 19 (68.4%)** |
| **Genuinely absent — known identity, no file** | **6 / 19 (31.6%)** |

Examples: `1XdUEm2MwB6ysy6E80ltMg` (KR$NA — No Cap), `4I2txOHJ5QRpJvG0nQpBKL` (Badshah — Bajenge), `1m0V5Yf8GgwnYSkGgxrVZY` (YUNG SAMMY — 4 X 4).

**These 6 are the only tracks in the entire library with a known Spotify identity and no corresponding file.** They are precise, safe re-download candidates.

### Can a canonical manifest be reconstructed?

**Partially — for 1,058 of 2,002 files (52.8%).** Combining tag IDs, Mongo IDs, and the dead-letter queue yields a well-formed Spotify manifest for just over half the library. The remaining **932 files (46.6%) have no Spotify identity in any local source** and cannot be placed on a canonical manifest without external lookup. No external APIs were contacted for this audit.

---

## Rebuild Scenarios

### Strategy A — KEEP + REINDEX

Re-read tags from disk, merge the 356 shadow records, index the 49 orphans. No audio analysis, no downloads.

| Outcome | Count |
|---|---|
| Files preserved | **1,974 / 2,002 (98.6%)** |
| Requiring audio analysis | 0 |
| Requiring metadata reconstruction | ~49 (index the orphans) |
| Requiring re-download | **0** |
| Ambiguous | 0 (all 356 resolve deterministically) |

**Gains:** BPM/key 17.7% → ~97.8%; title/artist ~18% → ~97.3%; +539 Spotify IDs; removes 356 duplicate records; index shrinks 2,309 → ~1,953 accurate records.
**Does not fix:** energy, spectral centroid, key confidence — all remain at 0.1%, so the recommendation engine stays blocked.
**Risk:** Low. Read-only against files; the only writes are to `library_index`.

### Strategy B — KEEP + REINDEX + REANALYZE

Strategy A, plus a librosa pass over all valid files.

| Outcome | Count |
|---|---|
| Files preserved | **1,974 / 2,002 (98.6%)** |
| Requiring audio analysis | 1,974 (~12 min at the measured 0.359 s/track) |
| Requiring metadata reconstruction | ~49 |
| Requiring re-download | **28 damaged + 6 dead-letter = 34 (1.7%)** |
| Ambiguous | 12 possible-duplicate files pending content-hash verification |

**Gains:** everything in A, plus energy / spectral / confidence from 0.1% → ~98%, which unblocks the recommendation engine's dead 30% weight and the halved camelot term.
**Risk:** Low–moderate. **Prerequisite: Phase 1A's field-scoped `persist_audio_features()` fix must be in place — it is** — otherwise the re-analysis would destroy the `gemini_*`/`lastfm_*` enrichment on 1,635 records as it ran.

### Strategy C — CONTROLLED CLEAN REBUILD

| Outcome | Count |
|---|---|
| Files preserved | 0 |
| Requiring re-download | **2,002 (100%) — 19.49 GB** |
| Re-acquirable by canonical Spotify ID | 1,058 (52.8%) |
| **Not re-acquirable canonically** | **932 (46.6%)** |
| Ambiguous | 932 — would need metadata search, risking wrong versions/edits |

**Risks:** the 932 ID-less files include DJ edits, bootlegs, and regional Punjabi/Bollywood tracks least likely to be recoverable by search. Phase 0 also recorded that YouTube downloads are IP-blocked from the cloud host, so throughput is unproven at this scale. Re-downloading 19.49 GB to repair 62.4 MB of damage is a ~312:1 cost ratio.

---

## Re-download Estimate

A missing Mongo record does **not** imply a needed re-download — the 49 physical orphans are healthy files. A present Mongo record does **not** imply a healthy file — 28 damaged files are indexed.

| Estimate | Count | % of 2,002 | Composition |
|---|---|---|---|
| **MINIMUM** | **28** | **1.4%** | Files that fail to open or have no decodable stream |
| **LIKELY** | **34** | **1.7%** | The 28 damaged + 6 dead-letter tracks with known ID and no file |
| **MAXIMUM** | **46** | **2.3%** | The 34 + up to 12 suspected placeholder-audio duplicates, *if* content hashing confirms them |

Of the 28 damaged files, **23 (82.1%)** still expose a Spotify ID in their tags, so most of the minimum set can be re-acquired precisely rather than by search.

**Explicitly NOT re-download candidates:**

- the **49** unmatched physical files — healthy, merely unindexed
- the **1,352** `file:`-identity records — indexed, merely under a weak identity scheme
- the **1,904** files whose BPM/key is missing from Mongo — the data is in the tags
- the **1,890** live records lacking title/artist — recoverable from tags

---

## Potential Data Loss

### RECOVERABLE — reconstructible from physical tags alone

| Asset | Count |
|---|---|
| BPM | 1,958 |
| Key | 1,958 |
| Title | 1,953 |
| Artist / album artist | 1,948 |
| Genre (`TCON`) | 1,605 |
| Gemini genre (`TXXX`) | 1,582 |
| Spotify ID | 1,070 |
| Album | 925 |
| Embedded artwork | 1,939 |
| Crate/organisational structure | encoded in 2,002 paths |

### POTENTIALLY RECOVERABLE — recomputable, at a cost

| Asset | Route |
|---|---|
| `rms_energy`, `spectral_centroid_mean`, `confidence` | librosa re-analysis (~12 min) |
| `content_hash` | recomputable from files |
| `audio_fingerprint` (2,309 stored) | recomputable via `fpcalc.exe` |
| Camelot | derivable from key via `tkey_to_camelot()` |

### NOT RECOVERABLE — lost permanently if the library is wiped

| Asset | Count | Why |
|---|---|---|
| **Files with no Spotify ID** | **932 (46.6%)** | No canonical re-acquisition path |
| `download_history` | 460 rows | No `spotify_id`; filename identity is not authoritative |
| `artist_memory` genre learning | 4 docs | User-taught classifications |
| Soft-delete history | 171 records | Records what was already removed and why |
| Manual BPM corrections not mirrored to tags | unknown | Phase 1A established these never reached Mongo before the fix |
| `.vdjstems` VirtualDJ stem caches | 13 | Expensive to regenerate |
| Exact file versions (specific edits/bootlegs) | unknown | Re-download may return a different master |

---

## Risks

| Risk | Severity | Note |
|---|---|---|
| Wiping loses 932 canonically un-reacquirable files | **HIGH** | Decisive argument against Strategy C |
| Re-analysis without Phase 1A's fix destroys enrichment on 1,635 records | **HIGH** | **Mitigated — the fix is in place** |
| 12 suspected placeholder-audio duplicates unconfirmed | **MEDIUM** | Needs content hashing before any dedup; only 18.1% of records carry a hash |
| 356 shadow records inflate every index-derived statistic | **MEDIUM** | Any count computed from `library_index` today over-reports by ~15% |
| Mixed `/` and `\` in `final_path` (356 records) | **MEDIUM** | Root cause of the shadow records; will recur unless writes normalise |
| 3 soft-deleted records whose files exist | **LOW** | Inconsistent state, no data at risk |
| 49 unindexed physical files invisible to the app | **LOW** | Healthy files, simply absent from the index |
| 1 `.part` file and 6 lost dead-letter tracks | **LOW** | Small, precisely identified |

---

## Final Recommendation

### **B. KEEP CURRENT LIBRARY + REANALYZE/REPAIR INTELLIGENCE**

**Why re-downloading is unnecessary.**

The physical library is in good condition and is materially *richer* than the database. **1,974 / 2,002 files (98.6%) are valid and playable.** Their tags already hold BPM and key for **1,958 (97.8%)** and title+artist for **1,948 (97.3%)** — against **17.7%** and **18.1%** respectively in MongoDB. **1,904 files have BPM and key on disk that the database does not know about.** Nothing was lost; it was never read back.

Every live index record points at a file that exists — **0 Mongo orphans**. The apparent 30.8% ambiguity is not ambiguity at all: it is 356 files indexed twice under two separator spellings, and **100% of those pairs agree on `spotify_id`**, making the merge deterministic.

Genuine damage is **28 files (1.4%), 62.4 MB**, plus 6 tracks lost from the dead-letter queue — **34 files (1.7%)** truly warranting re-download, and 23 of the 28 damaged ones still carry a Spotify ID for precise re-acquisition.

A clean rebuild would move **19.49 GB to repair 62.4 MB** — a 312:1 ratio — and would permanently forfeit the **932 files (46.6%) that carry no Spotify ID in any local source**, along with 460 download-history rows, 13 VirtualDJ stem caches, and the learned artist-genre memory.

**Why Strategy B rather than Strategy A.** A tag re-read fixes metadata but leaves `rms_energy`, `spectral_centroid_mean`, and `confidence` at **2 / 2,309 (0.1%)**. Those are precisely the fields Phase 0 found blocking the recommendation engine — without them 30% of the similarity weight scores zero and the camelot term is permanently halved. Only re-analysis fixes that, and it costs roughly **12 minutes** for all 1,974 valid files. Phase 1A's field-scoped `persist_audio_features()` fix is the prerequisite that makes this safe, and it is already in place.

**Answering the Phase 1B question directly: the physical library is not bad. The Mongo index and the audio-analysis layer are incomplete. Repair them; do not rebuild the library.**

### Suggested sequencing (not executed — Phase 1B stops here)

1. Content-hash the 12 suspected placeholder duplicates to confirm or clear them.
2. Merge the 356 `file:`/`sp:` shadow pairs, keeping the `sp:` record.
3. Re-read tags into `library_index` for all 1,974 valid files.
4. Index the 49 physical orphans.
5. Run the librosa re-analysis for energy / spectral / confidence.
6. Re-download the 34 confirmed-missing or damaged tracks, 23 of them by Spotify ID.

---

## Evidence / Commands Used

All operations were read-only. MongoDB was accessed through a **direct `MongoClient`**, deliberately bypassing `backend/database.py` — its `_get_db()` calls `_ensure_indexes()`, which creates indexes and would have constituted a database write.

| Purpose | Method |
|---|---|
| Library root discovery | `grep BASE_DOWNLOAD_DIR backend/config.py backend/.env` |
| Filesystem enumeration | `os.walk` over `C:\Users\Aswin-pc\Desktop\DJ music`, 8 audio extensions |
| Validity check | `mutagen.File(path)` — open + `info.length`; **no `.save()` anywhere** |
| Tag audit | `mutagen.File(easy=True)` and `mutagen.id3.ID3(path)`, read-only |
| Spotify ID discovery | Regex over all ID3 frame text/URLs + `TXXX` values; bare 22-char base62 |
| Mongo audit | `MongoClient(...).library_index.find/count_documents/aggregate` — reads only |
| Path normalisation | `os.path.normcase(os.path.normpath(p))` |
| Manifests | Read `Ingest/.retry_queue/dead/*.json` |
| Audit script | `<scratchpad>/audit_1b.py` — written **outside the repository**, in the session scratchpad |

### Safety verification

| Check | Result |
|---|---|
| Mongo writes | **None** — no `insert`, `update`, `delete`, `upsert`, or `create_index` issued |
| Files modified / moved / renamed / deleted | **None** |
| Tags written | **None** |
| Files downloaded | **None** |
| Backfill / analysis / enrichment jobs run | **None** |
| Application code changed | **None** |
| Configuration changed | **None** |
| Packages installed | **None** |
| Files created in the repository | **This report only** |
