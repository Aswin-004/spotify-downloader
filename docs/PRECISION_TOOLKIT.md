# Precision toolkit — right song, right folder

Written 2026-09-20 after "only karaoke gets downloaded" and "techno/trance keeps landing in House".
Run everything below from `backend/` with the project venv active (`..\.venv\Scripts\activate`),
because the global Python 3.14 lacks packages the app uses.

## What was going wrong

| Symptom | Root cause | Fix |
|---|---|---|
| Karaoke / instrumental / cover downloaded | Top search hit was trusted; the filter looked at the video title only; nothing checked the audio | Channel filter (karaoke, Sing King, KaraFun, Ameritz…), and **every download is fingerprinted (AcoustID) and rejected if it is provably a different song** — then the next candidate is tried |
| Techno/Trance in House | Artist overrides sent techno acts to the catch-all; a wrong mapping sent "melodic techno" to the House crate; Spotify stopped returning artist genres; a text-only Groq guess leans "House" | Corrected overrides and mapping, Spotify genre steps skip themselves when unavailable, and a **voting engine** replaces "first source wins" |
| MusicBrainz genre step never worked | The search URL contained raw spaces, `http.client` rejects that, and a blanket `except` hid it | Properly encoded query + an identity check on the hit + one retry on 503 |

## What the real library turned out to look like (measured 2026-09-20, read-only)

* **566 of ~2,000 tracks had no usable artist tag**: 257 say "Indian", 205 "Unknown", 82 "Electronic",
  the rest blank. Nothing artist-based (curated tables, Last.fm artist tags) can work on those, so
  they were filed by a guess. 263 of the Punjabi folder's 506 tracks are these unidentified "Indian"
  songs (the taxonomy sends the generic "Indian" label to the Punjabi crate); 105 of Trance's ~107
  and 79 of House's 260 are artist-less too. **501 of the 566 still carry a Spotify track id**, so the
  real artist can be looked up.
* **17 % of the Spotify ids stored in those files point at a different song** (87 of 500 checked:
  "Kiya Kiya" → "Candytuft Parsley" by "The Bulbine for Indian"). The old pipeline searched Spotify
  with the artist set to the placeholder "Indian" and got unrelated tracks back — that is where the
  "Indian" artists came from. So a recovered artist is trusted **only if** Spotify's title matches the
  file's title and the lengths agree (±20 s); files that fail keep "unknown". Your ingest playlist
  (already configured as `INGEST_PLAYLIST_ID`) is read for free identity data — it identifies files
  that have no id at all by title + length — and every file whose Spotify record is the same song
  but whose length differs by more than 30 s is flagged **suspect** (a different edit, or the wrong
  audio such as a karaoke cut).
* Where the artist IS known, the folders are mostly right: the curated tables agree with your sorting
  96.5 % of the time. The genuine mix-ups are a handful of electronic acts (Skrillex in House, Pop and
  UK Garage), plus the Bollywood/Punjabi boundary (Punjabi singers on Hindi film songs), which is a
  matter of taste and is therefore never auto-moved.
* BPM in the library is coarse and sometimes off by a factor (many 80/92/96/99 values), so tempo is
  only used to temper weak evidence — never to overrule a curated artist.

## Whole-library re-sort (`library_resort.py`) — the fix for what is already misfiled

```bash
python library_resort.py scan                       # READ-ONLY. Recovers artists, classifies every track, writes a report + spreadsheet
```
```bash
python library_resort.py apply --report reports/resort_<time>.json                       # preview of the MOVE rows
```
```bash
python library_resort.py apply --report reports/resort_<time>.json --yes                 # move them (writes an undo manifest)
```
```bash
python library_resort.py apply --report reports/resort_<time>.json --fix-tags --yes      # ...and write the recovered artist names into the files
```
```bash
python library_resort.py undo --manifest reports/resort_undo_<time>.json --yes           # put everything back, tags included
```
Each track lands in one bucket: **ok** (left alone) · **MOVE** (confident and backed by a curated table or
track-level evidence) · **review** (weaker, artist-tags-only, or a taste call: across
Bollywood/Punjabi/Tamil, or across Pop/R&B/Hip Hop/Latin, for a track whose artist you did tag) ·
**suspect** (tempo or length doesn't fit, nothing better known) · **unknown**. Tempo only tempers weak
evidence; it never overrules a curated artist (Skrillex's "Bangarang" is 110 BPM). Open the `.csv` in Excel, tick or un-tick the `apply` column (you may even type a different
`proposed_folder`), then `apply --csv <that file> --yes`. The Spotify lookups are paced at 1/s, capped
(`--max-spotify-calls`, default 300) and cached, and stop at the first rate limit; re-run to continue.
Moved files also get their genre tag (TCON) updated, because other tools read it and a stale tag would
send the file back.

**Spotify limits.** A Spotify 429 blocks this app's whole Spotify access for ~23 h (it happened on
2026-09-02 and again on 2026-09-20 after ~600 lookups in an hour). The scan records the block in
`reports/spotify_block.json` and, while it lasts, makes **no** Spotify calls: it reads the cached
artist records and the cached playlist instead, and matches those by title only (length can't be
checked). Force that behaviour any time with `--no-spotify-calls`. Re-scan right before `apply` if you
have been moving files by hand — `apply` skips any file that is no longer where the report says.

## Commands

**1. Remove wrong files and get them re-downloaded next cycle** — nothing is deleted until you say so,
and removal goes to the Recycle Bin (restorable).
```bash
python cleanup_wrong_tracks.py scan --names "Make Some Noise For The Desi Boyz"   # add --verify-audio to fingerprint everything (slow)
python cleanup_wrong_tracks.py apply --report reports/wrong_tracks_<timestamp>.json           # preview only
python cleanup_wrong_tracks.py apply --report reports/wrong_tracks_<timestamp>.json --yes     # do it
```
Removed tracks are dropped from the library index and put on the re-queue list, so the **next ingest
cycle downloads them again** (backend must be running with `INGEST_PLAYLIST_ID` set; about every
500 s). Use `--only 3,7` / `--skip 5` to adjust the list, `--quarantine` to move to `_TO_DELETE`
instead of the Recycle Bin.

**2. Measure the genre classifier against your own folders (read-only)**
```bash
python eval_genre_classifier.py --classifier static                                  # offline, instant
python eval_genre_classifier.py --bpm-report --write-bpm-priors reports/bpm_priors.json
python eval_genre_classifier.py --classifier evidence --online --per-folder 60 --bpm-priors reports/bpm_priors.json
```
Your existing folders are the ground truth (`Electronic`/`NeedsReview` excluded). Read the output as:
* **By trust — verified**: what would be moved unattended. Its precision is the number that matters;
  if it is not ≥ ~95 %, raise `GENRE_EVIDENCE_MIN_CONFIDENCE` until it is.
* **By trust — unverified**: only ever *offered* for review.
* **Confident disagreements** (`…_disagreements.csv`): the classifier is sure and your folder says
  otherwise. Either it is wrong or **the file is misfiled** — this list is your Techno-in-House finder.
* Network answers are cached in `reports/genre_evidence_cache.json` (30 days), so re-runs are free.

**3. Re-sort the catch-all folder** — dry-run first; nothing moves without `--execute`.
```bash
python batch_reclassify_electronic.py --skip-ai --limit 50        # dry-run, no Groq
python batch_reclassify_electronic.py --skip-ai --execute         # moves VERIFIED matches only
```
Unverified matches (artist-level tags only, title search, Groq guess) are listed and held; add
`--include-ai-guesses` only after reading that list. Do not `--execute` from an old dry-run's output.

## Settings (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `AUDIO_VERIFY_MODE` | `enforce` | `enforce` reject provably-wrong audio · `flag` record only · `off` |
| `GENRE_EVIDENCE_MIN_CONFIDENCE` | `0.60` | How sure the voting engine must be before it answers |
| `ALLOW_UNVERIFIED_AI_MOVES` | `false` | The hourly job never moves a file on a Groq text guess unless `true` |
| `LASTFM_API_KEY`, `ACOUSTID_API_KEY` | — | Needed for the online signals / fingerprint verification |

## Limits worth knowing

* **Audio verification is positive-evidence only.** If AcoustID does not know a recording, or the
  title is non-Latin, the file is accepted (inconclusive), never rejected. Karaoke on a channel not
  named like karaoke is caught only when AcoustID recognises the real song.
* **Last.fm has no track-level tags for most DJ tracks** (measured: none for tracks with hundreds of
  thousands of listeners). Artist tags fill the gap but cannot verify a single track, which is why
  such answers are held for review rather than moved.
* Genre is still decided by what the sources say; it cannot hear the music. Curated overrides
  (`config.ARTIST_GENRE_OVERRIDE`) remain the most reliable signal — the disagreements CSV shows
  which artists deserve an entry.
* The Bollywood folder has not been audited; the disagreements CSV covers it once the evaluation is run.

---

# Second pass (2026-09-20, later): fixing the library for good, and keeping it fixed

## The root cause of the garbage artist tags — fixed in the pipeline

`services/legacy_identification_service._parse_filename` gave any file named just `Song.mp3` the
**name of its parent folder as the artist**, unconditionally. In a genre-organised library that folder is
`Indian`, `Electronic`, `Unknown`, `Ingest`… so hundreds of files were tagged artist = "Indian", and the
Spotify search that followed asked for `artist:Indian`, returning unrelated tracks whose artist name
contains "Indian" (that is why a quarter of those files carry the Spotify id of a *different song*).
A genre crate or container folder is now never treated as an artist (`_folder_is_not_an_artist`), in the
filename parser and in the folder-context hints. An existing test had pinned the bug ("artist == Latin,
unchanged behavior") and was corrected.

## What was applied to the real library

* 101 verified moves (60 of them Punjabi → Bollywood songs that only sat in Punjabi because their artist
  tag said "Indian"), 340 artist tags rewritten with the recovered, identity-checked artist, and 124
  genre tags synced to the folder you had put the file in by hand. Everything is in
  `reports/resort_undo_<time>.json` and reversible with `library_resort.py undo`.
* A second round (after the iTunes pass) moved 15 more (Ajja's psytrance → Trance, Wax Motif → House,
  Talha Anjum → Hip Hop, *Muqabala Muqabala* → Bollywood …), and `learn` recorded **91 artists** (≥3
  confirmed tracks, ≥80 % in one crate) as routing rules for future downloads. Final state: 1,180 of 2,024
  tracks independently confirmed in their folder (58 %); 161 review / 94 suspect / 584 nobody could verify.
* **Your hand-sorting is protected**, by the genre tag *and* by a ledger (`reports/user_placed.json`):
  syncing a tag erases the tag evidence, so every synced file is also recorded there. A file whose genre
  tag names a different crate than its folder
  was moved by a person; no suggestion may move it back. `apply --sync-genre-tags` then sets the stale tag
  to your folder so no other tool sends it back either.
* `apply --leave-alone reports/leave_alone.txt` never touches the listed files (corrupt ones from the audit).
* Tag writes keep each file's own ID3 version and change only the requested frames.

## Judging by sound, and its honest limits (`scan --audio`)

A model learns what *your* crates sound like from the tracks whose genre is already confirmed (evidence
agrees with the folder, or you placed them by hand) and is scored on tracks it never saw (K-fold, plus a
test on your hand-placed tracks). Measured on this library it is **weak**: ~61 % out-of-fold accuracy over
13 crates. It is reliable for Bollywood (and somewhat Punjabi) and useless for telling House from Techno
from Trance from UK Garage. So it is used only to **confirm** unknowns, to **corroborate** a weak
suggestion, and to **question** a curated table — never to move a track on its own unless it is more
confident than it has ever been while wrong (`ear` bucket, only with `apply --include-audio`). The
per-crate bars are printed in the scan summary. Analysis is cached and survives tag edits.

## A track-level genre source (`scan --itunes`)

Last.fm has almost no track tags for DJ tracks; iTunes' free search API returns a genre, artist, title and
length per track. An answer counts only if title, artist **and length** match; generic genres ("Dance",
"Electronic", "Worldwide") are ignored; one matching release is a weak vote and two agreeing releases a
full one. Alone it reaches 64 % confidence (→ *review*); with another signal it becomes a move. The scan
asks iTunes only about tracks nothing else settled (3 s per call, every answer cached).

---

# Keeping new songs landing correctly

What already protects **new** downloads (built and tested):

1. **Right song** — every download is fingerprinted (AcoustID) and rejected if it is provably another
   song / a karaoke or cover version; the search rejects karaoke channels. (`AUDIO_VERIFY_MODE=enforce`)
2. **No more garbage artists** — the root-cause fix above.
3. **Unattended sorting is evidence-based** — the hourly catch-all job uses the voting engine (curated
   tables, Last.fm, iTunes, MusicBrainz, remixer/script/title hints, BPM) and moves a file only on
   *verified* evidence; it no longer acts on a Groq text guess (`ALLOW_UNVERIFIED_AI_MOVES=false`).
   Whatever it cannot place stays in Electronic instead of landing in a wrong crate.
4. **Spotify budget** — a 429 blocks the app ~23 h; the block is remembered on disk and the tools stop
   calling Spotify while it lasts.

What YOU do to keep it that way (a few minutes each time):

| When | Command | Why |
|---|---|---|
| After adding a batch of songs (or weekly) | `python library_resort.py scan --itunes` | Finds anything misfiled or unidentified; nothing moves |
| Review `reports/resort_<time>.csv` | tick / un-tick the `apply` column | Your taste decides Bollywood/Punjabi, Pop/R&B… |
| Then | `apply --report … --csv … --fix-tags --sync-genre-tags --leave-alone reports/leave_alone.txt --yes` | Moves, repairs artist tags, syncs genre tags — undoable |
| After you hand-sort anything | `scan`, then `learn --report … --yes` | Every artist with ≥3 tracks and ≥80 % in one crate becomes a **routing rule**: their future songs go where you put them |
| When a new artist keeps landing wrong | add them to `config.ARTIST_GENRE_OVERRIDE` (the unknown / review lists show who) | The most reliable signal there is |

Not built (ideas, in order of value): a Discogs token for finer electronic sub-genres (Tech House vs Techno
vs Trance); a pre-trained audio embedding model to lift the audio model above ~61 %; running `scan` on a
schedule and emailing the review list; feeding the audio model into the ingest as a vote.
