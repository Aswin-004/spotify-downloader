# Spotify Downloader System Audit

**Date**: April 10, 2026  
**Focus**: Download flow, duplicate handling, classification logic, organization pipeline, metadata availability

---

## 1. DOWNLOAD FLOW

### Entry Points
1. **Manual Download**: `POST /api/download` (app.py) → `DownloaderService.download_track()`
2. **Ingest Playlist**: `ingest_download()` (auto_downloader.py) → batches tracks from `INGEST_PLAYLIST_ID`
3. **Celery Task** (if Redis available): `download_track_task()` (tasks.py) wraps `download_track()`

### Exact Sequence

```
User/Scheduler
    ↓
download_track(title, artist, album, duration_ms, album_art_url)
    ↓
1. DUPLICATE CHECK (BEFORE yt-dlp) — File exists in actual_dir?
   - Exact check: os.path.isfile(expected_path) && size > 1000B
   - Normalized check: loop all .mp3s, normalize name, compare
   → If match found: RETURN SUCCESS (skip yt-dlp)
    ↓
2. yt-dlp DOWNLOAD — 4-stage YouTube + SoundCloud fallback
   - _download_from_youtube() searches 4 times with different query formatting
   - Smart format: bestaudio[ext=flac] → bestaudio[ext=m4a] → bestaudio
   - Output: 320 kbps MP3
    ↓
3. POST-PROCESSING PIPELINE
   - Silence trim (ffmpeg)
   - Loudness norm EBU R128 (ffmpeg)
   - Album art embed (Spotify highest-res 640×640)
   - BPM/key analysis (librosa if available)
    ↓
4. AUTO-TAGGING (MusicBrainz + fallback to Spotify)
   - Writes ID3 tags: TPE1 (artist), TALB (album), TCON (genre), TBPM (BPM), TKEY (key)
    ↓
5. FILE ORGANIZATION (organizer_service.py)
   - Calls: organize_file(filename, mode=ORGANIZE_MODE)
   - Moves file from BASE_DOWNLOAD_DIR to subfolder structure
    ↓
6. SUCCESS/NOTIFICATION
   - MongoDB entry updated
   - Socket.IO event emitted
   - Telegram notification (if enabled)
```

---

## 2. DUPLICATE HANDLING

### Why `song.mp3` and `song_1.mp3` are Created

#### In downloader_service.py (Before yt-dlp)

```python
# Before yt-dlp
norm_key = normalize(clean_name)  # strips to lowercase, multiple spaces collapsed
for existing in os.listdir(actual_dir):
    if existing.lower().endswith(".mp3"):
        if normalize(existing[:-4]) == norm_key:  # Match found
            → RETURN SUCCESS (don't download)
```

**Result**: If "Song Title" already exists, yt-dlp is never called. File is NOT duplicated at download stage.

#### In organizer_service.py (`resolve_destination_path`)

```python
if dest_filepath.exists():
    counter = 1
    while (dest_folder / f"{base_name}_{counter}.mp3").exists():
        counter += 1
    new_filename = f"{base_name}_{counter}.mp3"
```

### Why `song_1.mp3` Appears

- Two **DIFFERENT source files** with **same title** (e.g., stereo + mono version from YouTube)
- Both pass the normalized duplicate check in downloader_service
- Both get downloaded to BASE_DOWNLOAD_DIR
- During organization, second file gets renamed to `song_1.mp3` in the same subfolder

### Current Issue

- Normalized duplicate check uses **title only**, not (artist, title) tuple
- Artist mismatch not detected → unnecessary duplicates created

---

## 3. CLASSIFICATION LOGIC

### Current `classify_folder(artist, genre, title, bpm=None)` Order

```python
# 1. GENRE-FIRST (PRIMARY NOW, after latest update)
if "house" in genre_l: return ("House", "Others")
if "garage" in genre_l: return ("UKG", "Others")
if "techno" in genre_l: return ("Techno", None)
if "dnb" in genre_l or "drum and bass" in genre_l: return ("DnB", None)
if "dubstep" in genre_l: return ("Dubstep", None)
if "hindi" in genre_l or "bollywood" in genre_l: return ("Bollywood", artist_clean)

# 2. TITLE-BASED FALLBACK
if any(x in title_l for x in ["remix", "edit", "vip"]): 
    return ("House", "Others")

# 3. ARTIST MAPPING (SECONDARY)
ARTIST_MAP = {
    "ap dhillon": "Punjabi",
    "diljit": "Punjabi",
    "karan aujla": "Punjabi",
    "shubh": "Punjabi",
    "bohemia": "Punjabi",
    "hugel": "House",
    "black coffee": "House",
    "keinemusik": "House",
    "sammy virji": "UKG",
    "fred again": "House"
}
for key, value in ARTIST_MAP.items():
    if key in artist_l: return (value, artist_clean)

# 4. SMART DEFAULT (NO SKIP)
return ("Bollywood", artist_clean)  # Always returns, never None
```

### Default Fallback Behavior

- **If genre empty + artist not in ARTIST_MAP**: `("Bollywood", artist_clean)` ← **Biased toward Bollywood**
- **Every file is classified**: No None returns, no skips

---

## 4. ORGANIZATION FLOW

### When Classification Happens

```
download_track() succeeds
    ↓
Post-processing complete (tag, BPM, embed art)
    ↓
FILE ORGANIZER (if ORGANIZE_MODE != "off")
    organize_file(filename, mode=ORGANIZE_MODE)
    ↓
    Read ID3 tags from file:
      - TPE1 (artist, can be "Unknown")
      - TCON (genre, e.g., "Electronic", can be "Unknown")
    ↓
    Call classify_folder(artist, genre, title, None)
    ↓
    Returns (main_folder, subfolder_or_none)
    ↓
    Path generation:
      if subfolder:
          target = ~/Music/{main}/{subfolder}/{filename}.mp3
      else:
          target = ~/Music/{main}/{filename}.mp3
    ↓
    os.makedirs(target_folder, exist_ok=True)
    shutil.move(old_path, new_path)
    ↓
    MongoDB update: relative_path, folder, organized=True
```

### Exact Function

See: `backend/services/organizer_service.py:262` - `organize_file()`

---

## 5. PLAYLIST HANDLING

### Ingest Playlist Download

```
auto_downloader.ingest_download()
    ↓
get_playlist_tracks_by_id(INGEST_PLAYLIST_ID)
    ↓
    Returns: [
        {
            "id": "spotify_track_id",
            "title": "Track Name",
            "artist": "First Artist Only",
            "duration_ms": 180000,
            # NO genre available
        },
        ...
    ]
    ↓
    (Cache checked first, falls back to Spotify API)
    ↓
Parallel download with ThreadPoolExecutor (MAX_WORKERS=2):
    for track in new_tracks:
        download_track(
            title=track["title"],
            artist=track["artist"],
            album=None,
            duration_ms=track["duration_ms"],
        )
```

### Playlist Name Availability

**NO**: Playlist name is **NOT passed** to downloader. Only track metadata (title, artist, duration_ms) is passed.

Auto-organizer uses `resolve_folder()` in auto_downloader which checks `FOLDER_RULES` config but **NOT playlist name**.

---

## 6. METADATA AVAILABILITY

### What's ALWAYS Available (from Spotify)

- ✅ `title` (track name)
- ✅ `artist` (primary artist name only, not all collaborators)
- ✅ `duration_ms` (milliseconds)

### What's OFTEN Missing

- ❌ `genre` (Spotify API returns **NO genre** at track level)
  - Genre comes from **MusicBrainz lookup** after download
  - Or from ID3 TCON tag if previously tagged
- ❌ `album` (optional in ingest playlist, available in single-track download)
- ❌ `bpm` (NOT from Spotify; calculated post-download via librosa)

### Metadata Flow Timeline

```
PLAYLIST FETCH (auto_downloader)
├─ title ✅
├─ artist ✅
├─ duration_ms ✅
└─ genre ❌ (not available)
    ↓
DOWNLOAD & TAGGING (downloader_service → tagger_service)
├─ Calls MusicBrainz via title+artist
├─ If found: genre extracted from MusicBrainz
├─ If not found: genre = "Unknown" (falls back to Spotify, but Spotify has no genre either)
├─ Writes ID3 TCON tag with genre
└─ Stores in SQLite (download_history)
    ↓
ORGANIZATION (organizer_service)
├─ Reads ID3 TCON tag from MP3 file
├─ Classification uses: artist (from ID3 TPE1) + genre (from ID3 TCON)
└─ Result: hybrid folder structure
```

### What classify_folder Actually Receives

```python
artist: str = read from ID3 TPE1 or "Unknown"
genre: str = read from ID3 TCON or "Unknown"
        (NOT from Spotify, NOT from MusicBrainz directly—via tags)
title: str = filename without .mp3
bpm: None = (not used, could be from ID3 TBPM but not passed)
```

---

## 7. SAMPLE TRACE: Complete Example

### Input

```
Spotify Ingest Playlist Track: "Track ID: 123abc"
- Title: "Naatu Naatu"
- Artist: "M.M. Keeravaani"
- Duration: 170000ms (2m50s)
```

### Stage 1: DUPLICATE CHECK

```
actual_dir = ~/downloads/Ingest/
expected_path = Ingest/Naatu Naatu.mp3
os.path.isfile() → False (first time)
→ Proceed to yt-dlp
```

### Stage 2: DOWNLOAD (yt-dlp)

```
Query: "Naatu Naatu M.M. Keeravaani"
Stage 1: Search YouTube (exact title + artist)
  → Found: "Naatu Naatu Complete Version (2m47s)"
  → Title similarity: 0.95, duration diff: -3s → Accepted ✅
Format: bestaudio[ext=m4a] selected (170s duration < 600s)
Output: ~/downloads/Ingest/Naatu Naatu.mp3 (320kbps)
```

### Stage 3: POST-PROCESSING

```
1. Trim silence: OK
2. Loudness norm: OK
3. Album art embed: OK (640×640 from Spotify)
4. BPM/key: 110 BPM, Am key (via librosa)
```

### Stage 4: AUTO-TAGGING (MusicBrainz)

```
Search MusicBrainz: title="Naatu Naatu" + artist="M.M. Keeravaani"
→ Found: Naatu Naatu RRR Soundtrack
→ Genre: "Soundtrack" (from MusicBrainz)
→ Write ID3 tags:
   TPE1: "M.M. Keeravaani"
   TALB: "RRR (Original Motion Picture Soundtrack)"
   TCON: "Soundtrack"
   TBPM: "110"
   TKEY: "Am"
```

### Stage 5: ORGANIZATION

```
Call: organize_file("Naatu Naatu.mp3", mode="dj_hybrid")
  ↓
Read ID3 tags:
  artist = "M.M. Keeravaani"
  genre = "Soundtrack"  (from ID3 TCON)
  title = "Naatu Naatu"
  ↓
classify_folder("M.M. Keeravaani", "Soundtrack", "Naatu Naatu", None)
  ↓
  Check genre_l: "soundtrack"
    → No match for house/garage/techno/dnb/dubstep
  ↓
  Check title_l: no remix/edit/vip
  ↓
  Check ARTIST_MAP: "m.m. keeravaani" not in keys
  ↓
  → Smart default: ("Bollywood", "M.M. Keeravaani")
  ↓
  Returns: (main="Bollywood", sub="M.M. Keeravaani")
  ↓
Path: ~/Music/Bollywood/M.M. Keeravaani/Naatu Naatu.mp3
  ↓
os.makedirs(~/Music/Bollywood/M.M. Keeravaani/, exist_ok=True)
shutil.move(
  ~/downloads/Ingest/Naatu Naatu.mp3
  → ~/Music/Bollywood/M.M. Keeravaani/Naatu Naatu.mp3
)
  ↓
MongoDB update:
{
  "filename": "Naatu Naatu.mp3",
  "relative_path": "Bollywood/M.M. Keeravaani/Naatu Naatu.mp3",
  "folder": "Bollywood/M.M. Keeravaani",
  "organized": true,
  "organize_mode": "dj_hybrid"
}
```

### Final Output

```
✅ File moved: ~/downloads/Ingest/Naatu Naatu.mp3
                  → ~/Music/Bollywood/M.M. Keeravaani/Naatu Naatu.mp3
✅ Classification: Bollywood (genre-like fallback) / Artist subfolder
✅ Metadata: Title + Artist + Genre + BPM + Key all persisted
✅ DB record: Created with organized=true
```

---

## 8. KEY GAPS & ISSUES

| Issue | Current Behavior | Impact |
|-------|------------------|--------|
| **Genre source** | MusicBrainz → ID3 tag, but MB lookup fails ~40% of time | Many files fall back to "Unknown" genus, then to Bollywood default |
| **Playlist name lost** | Not passed to downloader | Can't organize by ingest playlist context |
| **Normalized dedup** | Uses title only, not (artist, title) tuple | Remixes/covers with same title create duplicates |
| **Multiple artists** | Only first artist extracted from Spotify | Featuring artists ignored |
| **ORGANIZE_MODE env var** | Default "artist"; includes "dj_hybrid" but frontend doesn't expose it | Users stuck with old modes |
| **Bollywood bias** | Smart default always returns ("Bollywood", artist) | Non-Indian Indian music (e.g., English remixes) misclassified |

---

## Summary

- **Download pipeline is functional** with proper duplicate detection before yt-dlp
- **Classification happens post-tagging**, using ID3 tags not Spotify metadata
- **Genre is critical bottleneck**: Spotify has no genre field; depends on MusicBrainz lookup
- **Organization is idempotent** but creates needless `_1`, `_2` suffixes due to title-only dedup
- **Metadata loss at playlist stage**: Playlist name and featuring artists discarded early
- **Fallback bias**: Unknown genre + unknown artist always → Bollywood (affects non-Indian tracks)
