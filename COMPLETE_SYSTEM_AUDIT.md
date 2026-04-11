# COMPLETE SPOTIFY META DOWNLOADER SYSTEM AUDIT

## 1. DOWNLOAD FLOW (CRITICAL)

### Entry Point

**API Endpoint**: `POST /api/download`  
**Location**: [app.py](app.py#L380-L410)

```python
@app.route('/api/download', methods=['POST'])
def download_track():
    data = request.get_json()
    url = data.get("url")
    # Spawn background task
    socketio.start_background_task(target=_download_background, url=url)
    return jsonify({"status": "started"}), 202
```

### Full Download Flow: Spotify → YouTube → yt-dlp → MP3 Save

**Step-by-step execution** happens in `_download_background()` → `downloader_service.download_track()`:

#### Phase 1: URL Detection & Metadata Fetch
- Extract Spotify URL type (track/album/playlist) via `extract_spotify_id()`
- Call **SpotifyService** to fetch metadata:
  - Single track: `spotify_service.get_track_metadata(url)` → returns `{title, artist, album, duration_ms, album_art_url, release_date}`
  - Album: `spotify_service.get_album_tracks(url)` → returns track list
  - Playlist: `spotify_service.get_playlist_tracks(url)` → requires user OAuth

#### Phase 2: Duplicate Detection

**Location**: [downloader_service.py](downloader_service.py#L450-L475)

**Two-level duplicate check** (happens BEFORE download attempt):

1. **Exact filename match**: Check if `{clean_name}.mp3` exists in output_dir with file size > 1000 bytes
   - If found: SKIP, return `{"status": "success", "message": "Already exists"}`

2. **Normalized duplicate check**: Normalize both candidate + existing filenames (lowercase, collapse spaces)
   - Compare using `normalize(filename)` function
   - If match found AND file size > 1000 bytes: SKIP

**Key behavior**: Duplicates skip the ENTIRE pipeline (no re-download, no re-organization).

#### Phase 3: 4-Stage YouTube Search + SoundCloud Fallback

**Location**: [downloader_service.py](downloader_service.py#L1300-L1400)

```
Stage 1: ytsearch10:"ARTIST - TITLE Official Audio"
Stage 2: ytsearch5:"ARTIST - TITLE Audio"
Stage 3: ytsearch5:"ARTIST TITLE youtube music"
Stage 4: ytsearch3:"TITLE ARTIST" (last resort)
Stage 5: scsearch3:"ARTIST - TITLE" (SoundCloud)
```

**For each stage**:
1. Extract search results via yt-dlp (without downloading)
2. Apply **STRICT candidate matching** ([strict_matcher.py](strict_matcher.py#L50-L150)):
   - **Pre-filter blacklist** (Step 1): Check if candidate contains blacklisted keywords that DON'T appear in Spotify title
     - Blacklisted: "cover", "karaoke", "nightcore", "sped up", "reverb", "slowed", "remix", "mashup", "parody"
   - **Title scoring** (50% weight): Fuzzy match between YouTube title and Spotify title (via thefuzz token_set_ratio)
   - **Artist scoring** (30% weight): Max of (artist-in-title match, channel-uploader match)
   - **Duration scoring** (20% weight): Tiered penalty system:
     - ±2 sec: 1.0 (perfect)
     - ±5 sec: 0.8 (good)
     - ±10 sec: 0.5 (acceptable)
     - ±30 sec: 0.2 (poor)
     - >30 sec: 0.0 (REJECT)
   - **Bonuses**:
     - Official channel in channel name: +0.15
     - VEVO channel: +0.20
     - "Official" in title: +0.05
     - Verified channel badge: +0.30
   - **Heavy penalty** for duration >±2 sec: -0.50
   - **Final score**: `0.5*title + 0.3*artist + 0.2*duration + bonuses + penalty`
   - Accept candidates with score ≥ 0.50
3. Sort by score, select best
4. **Final hard duration check**: Reject if |duration_diff| > 30 seconds
5. On success for a stage: **STOP** (don't try other stages)
6. On failure: Move to next stage

**Max retries per stage**: 2 (with 1-second delay between retries)

#### Phase 4: Audio Download & Post-Processing

**Format Selection** (duration-aware):
- **>10 min track**: `bestaudio[ext=flac]/bestaudio/best` (prefer lossless)
- **<1 min track**: `bestaudio[ext=m4a]/bestaudio/best` (prefer compact)
- **1-10 min track**: `bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best` (full chain)

**yt-dlp postprocessor**:
- Extract audio → convert to MP3 @ **320 kbps**
- Store format in `self._last_format_downloaded` for quality report

**Post-processing pipeline** (errors here DON'T trigger re-download):
1. **Silence trimming**: FFmpeg silenceremove filter (start + end)
2. **Loudness normalization**: EBU R128 loudnorm filter (I=-14, TP=-1, LRA=11)
3. **Album art embedding**: Download highest-res Spotify image (640×640), embed as APIC frame
4. **File size validation**: If file > expected * 3 times → DELETE and fail (wrong track)

---

## 2. FAILURE HANDLING

### Return Structure of `download_track()`

**Location**: [downloader_service.py](downloader_service.py#L450-L900)

#### Success Case:
```python
{
    "status": "success",
    "filename": "Song Name.mp3",
    "filepath": "/full/path/to/Song Name.mp3",
    "message": "Successfully downloaded: Song Name.mp3",
    "match_quality": "exact|approx|fallback",
    "quality_report": {
        "bitrate_achieved": "320kbps",
        "format_downloaded": "mp3|m4a|flac",
        "source_platform": "youtube|soundcloud",
        "search_stage_used": 1-5,
        "duration_match_diff": 1.5,  # seconds
        "title_similarity": 0.95,
        "channel_verified": True/False,
        "blacklist_filtered": 0,  # count of blacklisted candidates filtered
        "normalization_applied": True/False,
        "silence_trimmed": True/False,
        "art_embedded": True/False,
    },
    "tagging_report": { ... },  # if _TAGGER_AVAILABLE
    "organized": True/False,
    "organize_result": { ... },
    "warning": "Downloaded but tagging failed: error_msg"  # optional
}
```

#### Fallback Case:
```python
{
    "status": "fallback",
    "message": "Auto-download failed. Please click 'Open YouTube' to find and download manually.",
    "manual_url": "https://www.youtube.com/results?search_query=ARTIST+TITLE",
    "title": "Song Title",
    "artist": "Artist Name"
}
```

**Fallback is triggered when**:
- All 5 download stages fail (no acceptable YouTube/SoundCloud candidate found, or yt-dlp extraction fails)
- Exception occurs during download attempt
- File size validation fails (file too large)

**Fallback does NOT add to "failed list"** — user receives YouTube search link and can download manually.

### Actual Failure Scenarios

1. **Stage 1-4 all fail** (no acceptable match ≥0.50):
   - Status: `fallback` (not counted as "failed")
   - User gets YouTube search URL
   - No notification by default unless explicitly calling notify_download_failure()

2. **yt-dlp crash** (both primary + fallback attempts):
   - Status: `fallback`
   - Emits `download_error` Socket.IO event
   - Notification sent (if notifications_service available)

3. **File validation fails** (size too large):
   - File is **DELETED**
   - Status: `fallback`
   - Notification sent

4. **Post-processing fails** (tagging, organization):
   - **File is NOT deleted**
   - Status: `success` (file exists = success)
   - Warning added to response
   - Errors logged but non-fatal

### Failed Download Tracking (Auto-Downloader)

**Location**: [auto_downloader.py](auto_downloader.py#L125-L160)

**Permanent skip logic**:
```python
_record_failure(track_id, title, artist, failure_counts)
if failure_counts[track_id] >= MAX_FAIL_ATTEMPTS (3):
    # PERMANENTLY SKIP this track_id
    # Prevent re-download if playlist is re-synced
```

**Persistent storage**: `ingest_failures.json` on disk
```json
{
    "track_id_1": 3,  // 3 failed attempts → skipped
    "track_id_2": 1,  // 1 failed attempt → will retry
}
```

### File Existence Check Before Retry

**Current behavior**: Duplicate prevention happens BEFORE download attempt:
- If file exists (size > 1000 bytes): Skip immediately, return success
- Does NOT check if file exists during retry logic
- No built-in retry mechanism (Celery has autoretry, but not for manual downloads)

---

## 3. ORGANIZER SYSTEM

### classify_folder() Function

**Location**: [organizer_service.py](organizer_service.py#L80-L135)

**Current behavior**: GENRE-FIRST classification with NO "dj_hybrid" mode support despite parameter

```python
def classify_folder(artist: str, genre: str, title: str, bpm=None) -> Tuple[str, Optional[str]]:
    """
    Returns (main_folder, subfolder_or_none)
    Genre is PRIMARY, artist mapping is SECONDARY, ALWAYS classifies (never skips).
    """
```

**Classification pipeline**:

1. **Genre-first checks** (PRIMARY):
   ```
   if "house" OR "afro" OR "deep house" OR "tech house" in genre:
       → ("House", "Others")
   
   if "garage" in genre:
       → ("UKG", "Others")
   
   if "techno" in genre:
       → ("Techno", None)
   
   if "dnb" OR "drum and bass" in genre:
       → ("DnB", None)
   
   if "dubstep" in genre:
       → ("Dubstep", None)
   
   if "hindi" OR "bollywood" in genre:
       → ("Bollywood", artist_clean)
   ```

2. **Title-based remix detection** (FALLBACK):
   ```
   if "remix" OR "edit" OR "vip" in title.lower():
       → ("House", "Others")
   ```

3. **Artist mapping** (SECONDARY):
   ```python
   ARTIST_MAP = {
       "ap dhillon": "Punjabi",
       "diljit": "Punjabi",
       "hugel": "House",
       "black coffee": "House",
       "sammy virji": "UKG",
       "fred again": "House",
   }
   ```
   If artist matches → `(genre_category, artist_clean)`

4. **Smart default** (NO FAIL):
   ```
   if no match found:
       → ("Bollywood", artist_clean)  // Default for unknown tracks
   ```

### organize_file() Function

**Location**: [organizer_service.py](organizer_service.py#L190-L280)

```python
def organize_file(filename: str, mode: str = "dj_hybrid", 
                  file_dir: Optional[str] = None, 
                  spotify_genre: str = "") -> Dict:
```

**Current behavior**:
```
1. Find file:
   - If file_dir provided: check file_dir first, fallback to BASE_DOWNLOAD_DIR root
   - Else: check BASE_DOWNLOAD_DIR root
   
2. Read ID3 tags:
   - Artist (TPE1 frame)
   - Genre (TCON frame)
   - If genre is "Unknown"/"Other" AND spotify_genre provided: use spotify_genre as fallback
   
3. FORCE dj_hybrid classification:
   - Calls classify_folder(artist, genre, filename, None)
   - Returns (main_folder, subfolder) tuple
   - Builds folder path: "main_folder/subfolder" OR just "main_folder"
   
4. Clean folder names:
   - Remove unsafe chars: < > : " / \ | ? *
   - Collapse spaces
   - Strip padding
   
5. Resolve destination:
   - Create destination folder (mkdir -p)
   - Handle collisions: if file exists, append _1, _2, ... _N
   
6. Move file:
   - shutil.move(old_path, new_path)
   - File is MOVED (not copied)
   
7. Update MongoDB:
   - Set relative_path, folder, organized=True, organize_mode
   - Non-fatal if MongoDB fails
   
8. Return dict:
   {
       "moved": True/False,
       "old_path": string,
       "new_path": string,
       "folder": "House/Others",
       "artist": "extracted artist",
       "genre": "mapped genre",
       "error": "error_message"  # optional
   }
```

### Mode Parameter Behavior

**Current behavior**: The `mode` parameter is **IGNORED**. All code paths force `mode = "dj_hybrid"`.

```python
# This is done explicitly in organize_file():
mode = "dj_hybrid"  # FORCE — ignore user input
```

Even though function signature accepts `mode: str`, it's overwritten immediately. All 3 public functions force dj_hybrid:
- `organize_file()`
- `organize_recent()`
- `organize_library()`

---

## 4. FILE STORAGE STRUCTURE

### Base Directory Path

```python
# From config.py
BASE_DOWNLOAD_DIR = os.getenv("BASE_DOWNLOAD_DIR", 
                              os.path.join(os.path.dirname(__file__), "downloads"))

# Default: <project_root>/backend/downloads/
```

### Current Downloaded Files Location

Manual downloads: `BASE_DOWNLOAD_DIR/Manual/`  
Auto-downloader (ingest): `BASE_DOWNLOAD_DIR/Ingest/{subfolder}/`  
Playlists: `BASE_DOWNLOAD_DIR/Playlists/{playlist_name}/`

### Example Real File Paths AFTER Organization

Assuming folder structure `{BASE_DOWNLOAD_DIR}/` with downloaded files in root:

**Before organization**:
```
downloads/
  ├── Song A.mp3
  ├── Song B.mp3
  └── Song C.mp3
```

**After organize_file() called** (via auto_downloader or explicit call):
```
downloads/
  ├── House/
  │   ├── Others/
  │   │   ├── Song A.mp3  (was "Song A.mp3", genre=house)
  │   │   └── Song B_1.mp3  (collision: same artist/genre as A)
  ├── UKG/
  │   └── Sammy Virji/
  │       └── Song C.mp3  (artist=sammy virji)
  ├── Bollywood/
  │   └── Artist X/
  │       └── Song D.mp3  (default for unknown)
```

**Classification example for real songs**:

1. **"Percolator" by Adana Twins** (genre: House):
   - classify_folder("Adana Twins", "House", ...) → ("House", "Others")
   - Path: `downloads/House/Others/Percolator.mp3`

2. **"Flowers" by Sammy Virji** (genre: UKG):
   - classify_folder("Sammy Virji", "UKG", ...) → ("UKG", "Sammy Virji")
   - Path: `downloads/UKG/Sammy Virji/Flowers.mp3`

3. **"Besharam Rang" by Shreya Ghoshal** (genre: Bollywood/Hindi):
   - classify_folder("Shreya Ghoshal", "Bollywood", ...) → ("Bollywood", "Shreya Ghoshal")
   - Path: `downloads/Bollywood/Shreya Ghoshal/Besharam Rang.mp3`

### Move vs Copy

**Current behavior**: Files are **MOVED** (shutil.move), not copied.
- Original location after organization: file is gone
- If organization fails: file remains at source

---

## 5. SOCKET.IO SETUP

### Initialization

**Location**: [app.py](app.py#L155-L175)

```python
import eventlet
eventlet.monkey_patch()  # Called at module import time

socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173"
    ],
    ping_timeout=300,
    ping_interval=10,
    max_http_buffer_size=1e8,
    logger=False,
    engineio_logger=False,
)
```

### Async Mode: eventlet

**Current mode**: `async_mode="eventlet"`

**Monkey-patching**:
```python
import eventlet
eventlet.monkey_patch()  # At app.py top level
```

This patches:
- Threading (uses greenlets)
- Sockets
- Subprocess calls

**Key consequence**: Cannot use `_socketio.sleep()` from ThreadPoolExecutor workers — causes hub corruption and disconnections. Code uses `time.sleep()` instead.

**NOT using**:
- gevent (not installed)
- threading (explicitly NOT used despite stdlib available)

### Server Startup

**Location**: [app.py](app.py#L900+)

```python
# NOT using: app.run(debug=True)
# NOT using: socketio.run(app, debug=True)

# Instead, in scripts/start.ps1:
python app.py  # Runs the app directly

# To start server from Python:
socketio.run(app, host="0.0.0.0", port=5000)
```

**Monkey-patching does NOT interfere** with startup because it's done early.

### Emitted Events (Backend → Frontend)

**Real-time events emitted via Socket.IO**:

```python
# Status updates (every 5 seconds)
emit("status_update", {
    "download": {"status": "...", "progress": 0-100, "current": "..."},
    "auto": AUTO_STATUS dict,
    "history": last 50 history entries
})

# Quality reports (per track)
emit("quality_report", {
    "bitrate_achieved": "320kbps",
    "format_downloaded": "mp3|m4a|flac",
    "source_platform": "youtube|soundcloud",
    "search_stage_used": 1-5,
    "duration_match_diff": seconds,
    "title_similarity": 0.0-1.0,
    "channel_verified": True/False,
    "blacklist_filtered": count,
    "art_embedded": True/False,
    "normalization_applied": True/False,
    "silence_trimmed": True/False,
})

# Tagging completion
emit("tagging_complete", {
    "filename": "...",
    "title": "...",
    "artist": "...",
    "report": {...}  # tagging_report
})

# Auto-downloader status
emit("auto_status_update", AUTO_STATUS)

# Queue status (for Celery)
emit("queue_status", download_queue_status)

# Celery task events
emit("task_started", {"task_id": "...", "title": "...", "artist": "..."})
emit("task_retrying", {"task_id": "...", "attempt": 2, "error": "..."})
emit("task_failed", {"task_id": "...", "error": "..."})

# File lists
emit("files_list", [...])

# Keepalive
emit("pong_keepalive", {"status": "alive"})
```

### Connection Handlers

```python
@socketio.on('connect')
def handle_connect():
    emit_status()  # Send current state
    emit("files_list", load_existing_files())
    emit("queue_status", download_queue_status)

@socketio.on('request_status')
def handle_request_status():
    emit_status()

@socketio.on('ping_keepalive')  # DISCONNECT FIX
def handle_keepalive():
    emit('pong_keepalive', {'status': 'alive'})
```

---

## 6. CELERY INTEGRATION

### Conditional Availability

**Location**: [app.py](app.py#L40-L55)

```python
_celery_available = False
_celery_app = None

try:
    from celery_app import is_redis_available
    if is_redis_available():
        from tasks import download_track_task, sync_playlist_task, retry_failed_task
        from celery_app import celery_app as _celery_app
        _celery_available = True
        logger.info("Celery + Redis detected — task queue enabled")
    else:
        logger.info("Redis not reachable — falling back to threading")
except ImportError:
    logger.info("Celery not installed — falling back to threading")
except Exception as _celery_err:
    logger.warning(f"Celery init error: {_celery_err} — falling back to threading")
```

**If Celery is unavailable**: System falls back to synchronous threading via `socketio.start_background_task()`

### Tasks Using Celery

**Location**: [tasks.py](tasks.py#L35-L150)

#### Task 1: download_track_task
```python
@celery_app.task(
    bind=True,
    name="tasks.download_track_task",
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=60,          # 60 seconds before first retry
    retry_backoff_max=300,     # Max 5 minutes between retries
    acks_late=True,            # Acknowledge after completion
)
def download_track_task(self, track_metadata: dict, output_path: str = None):
```

**Arguments**:
- `track_metadata`: `{title, artist, album, duration_ms, album_art_url}`
- `output_path`: Optional output directory override

**What it does**:
1. Calls `DownloaderService.download_track()` (existing logic, NO changes)
2. Builds progress callback that emits Socket.IO events
3. Emits `quality_report` event on completion
4. On failure: autoretry (3 max with backoff)

**Retry behavior**:
- Exponential backoff: 60s → 120s → 240s (max 5min between attempts)
- Emits `task_retrying` event on each retry
- Emits `task_failed` event after max retries exhausted

#### Task 2: sync_playlist_task
```python
@celery_app.task(
    bind=True,
    name="tasks.sync_playlist_task",
    acks_late=True,
)
def sync_playlist_task(self, playlist_id: str):
```

**What it does**:
1. Fetch playlist tracks via SpotifyService
2. Create `Playlists/{playlist_name}/` folder
3. Spawn one `download_track_task.delay()` per track (parallel via Celery workers)
4. Emit `playlist_sync_progress` events

**No retries** specific to this task (child tasks have their own retry logic).

### Socket.IO ↔ Celery Integration

**Socket.IO emission from Celery workers**:

**Location**: [tasks.py](tasks.py#L62-L90)

```python
def _emit_socketio_event(event: str, data: dict):
    """Best-effort Socket.IO emit from within a Celery worker."""
    
    # Strategy 1: Direct module-level socketio reference (set by app.py)
    try:
        from services.downloader_service import _socketio
        if _socketio is not None:
            _socketio.emit(event, data)
            return
    except Exception:
        pass

    # Fallback: Redis pub/sub bridge
    try:
        import json
        import redis
        from celery_app import REDIS_URL
        r = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=2)
        payload = json.dumps({"event": event, "data": data})
        r.publish("socketio_bridge", payload)
    except Exception:
        pass  # Silently degrade
```

**Current architecture**: 
- If worker runs in same process as Flask (during tests): Direct Socket.IO emission works
- If worker runs in separate process: Events go through Redis pub/sub (Flask subscribes and re-emits)

### How Download Jobs Are Triggered

**Manual download** (user clicks download):
```
POST /api/download → _download_background() 
  ├─ If Celery available:
  │   └─ [NOT YET IMPLEMENTED] Could use download_track_task.delay()
  └─ Else: socketio.start_background_task(_download_background())
```

**Current**: Manual downloads use **threading** (socketio.start_background_task), NOT Celery tasks.

**Auto-downloader ingest**:
```
ingest_download() (in auto_downloader.py)
  ├─ ThreadPoolExecutor with MAX_WORKERS=2 (DISCONNECT FIX: limit to prevent Socket.IO flooding)
  ├─ For each new track:
  │   └─ downloader_service.download_track()  (synchronous in worker thread)
```

**Playlist download** (user downloads album/playlist):
```
POST /api/download → _download_background()
  ├─ Detect playlist type
  └─ For each track in playlist:
      └─ downloader_service.download_track()  (synchronous, not Celery)
```

---

## 7. METADATA AVAILABILITY

### What Metadata is ALWAYS Available

For each downloaded track in the system:

```
✓ artist       : Retrieved from Spotify API (or ID3 tag if organizing)
✓ title        : Retrieved from Spotify API (or filename if organizing later)
✓ duration_ms  : Retrieved from Spotify API (Spotify tracks have duration)
```

### What is OFTEN Missing

```
○ genre        : ID3 TCON tag is frequently empty or generic
                 If missing: organizer uses Spotify metadata fallback (spotify_genre param)
                 If Spotify also missing: defaults to "Other"

○ bpm          : Only added IF bpm_key_service is:
                 1. Installed (librosa + soundfile)
                 2. Available after download
                 NOT from Spotify directly (Spotify doesn't provide BPM in public API)

○ album        : Sometimes provided by Spotify API
                 Not always required for download

○ playlist_name: Only available if downloading playlist (not single track)

○ album_art_url: Retrieved from Spotify image (highest-res 640×640)
                 Not guaranteed if track has no image

○ release_date : Retrieved from Spotify (sometimes missing for live/unreleased)
```

### Data Flow

```
Spotify API
    ↓
SpotifyService.get_track_metadata()
    ↓
{title, artist, album, duration_ms, album_art_url, release_date}
    ↓
DownloaderService.download_track()
    ↓
Post-processing (Tagging Service reads ID3 tags)
    ↓
Organizer reads ID3 tags for classification
    ↓
If genre missing in ID3: use spotify_genre fallback
    ↓
BPM/key analysis (if available, happens AFTER tagging)
    ↓
MongoDB download_history record contains all metadata
```

---

## 8. SAMPLE TRACE (VERY IMPORTANT)

### Real Example: Spotify → YouTube → File

**INPUT**:
```
Spotify URL: https://open.spotify.com/track/3tIDRQKyws0wqkPKAYwCKs
Artist: Sammy Virji
Title: "Flowers (Extended Mix)"
Album: Unknown
Duration: 420000 ms (7 minutes)
Album Art: https://i.scdn.co/image/ab67616d0000b273...
```

**STEP 1: Extract Spotify metadata**
```
SpotifyService.get_track_metadata(url):
  → {
      "title": "Flowers (Extended Mix)",
      "artist": "Sammy Virji",
      "album": "Unknown",
      "duration_ms": 420000,
      "album_art_url": "https://i.scdn.co/image/..."
    }
```

**STEP 2: Duplicate detection**
```
download_track("Flowers (Extended Mix)", "Sammy Virji", ...)
    output_dir = "/downloads/Manual"
    expected_path = "/downloads/Manual/Flowers (Extended Mix).mp3"
    
File check: file NOT found → proceed with download
```

**STEP 3: YouTube search (4-stage)**

Stage 1: Query `"ytsearch10:Sammy Virji - Flowers (Extended Mix) Official Audio"`
```
Results:
  #1: "Sammy Virji - Flowers (Extended Mix) [Official Audio]"
      Duration: 420s
      Uploader: Sammy Virji
      Verified: ✓
      URL: https://youtube.com/watch?v=xxxxx

Score calculation:
  title_similarity = 0.95 (clean: "flowers extended mix" vs "flowers extended mix official audio")
  artist_score = 1.0 (channel uploader matches)
  duration_score = 1.0 (exact ±2s match)
  verified_bonus = +0.30
  official_bonus = +0.05
  tight_penalty = 0 (duration is perfect)
  
  final = 0.5 * 0.95 + 0.3 * 1.0 + 0.2 * 1.0 + 0.30 + 0.05
        = 0.475 + 0.3 + 0.2 + 0.35
        = 1.325 (clamped to 1.0)
  
  → ACCEPTED (score 1.0 > 0.50 threshold)
```

**STEP 4: Download via yt-dlp**
```
_try_download_with_query(
    query="https://youtube.com/watch?v=xxxxx",
    source_name="Stage 1 (Official)",
    duration_ms=420000
)

yt-dlp format: bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best
              (standard track = 1-10 min, so full chain)

Post-processor: FFmpegExtractAudio → mp3 320k

Result: /downloads/Manual/Flowers (Extended Mix).mp3
        File size: 12.8 MB
        Format downloaded: m4a (converted to mp3)
        Duration: 420s
```

**STEP 5: Post-processing**
```
Silence trimming: skipped (no leading/trailing silence detected)
Loudness normalization: applied (EBU R128)
Album art embedding: applied (640×640 JPEG embedded as APIC frame)
```

**STEP 6: Build quality report**
```
{
    "bitrate_achieved": "320kbps",
    "format_downloaded": "m4a",
    "source_platform": "youtube",
    "search_stage_used": 1,
    "duration_match_diff": 0.0,
    "title_similarity": 0.95,
    "channel_verified": True,
    "blacklist_filtered": 0,
    "art_embedded": True,
    "normalization_applied": True,
    "silence_trimmed": False,
}
```

**STEP 7: Tagging (if available)**
```
_tag_file("/downloads/Manual/Flowers (Extended Mix).mp3", 
          spotify_meta={...})

Writes ID3 tags:
  TPE1 (Artist): "Sammy Virji"
  TIT2 (Title): "Flowers (Extended Mix)"
  TALB (Album): "Unknown"
  TCON (Genre): Empty → defaults to "Unknown"
  TDRC (Date): "2022"
```

**STEP 8: Organization**
```
organize_file("Flowers (Extended Mix).mp3", mode="dj_hybrid")

Read ID3 tags:
  artist: "Sammy Virji"
  genre: "Unknown"

Apply spotify_genre fallback: None (not provided)

classify_folder("Sammy Virji", "Unknown", ...) → ("Bollywood", "Sammy Virji")

BUT WAIT — ARTIST_MAP check first:
  "sammy virji" in ARTIST_MAP → maps to "UKG"
  → return ("UKG", "Sammy Virji")

Create folders:
  /downloads/UKG/Sammy Virji/

Move file:
  /downloads/Manual/Flowers (Extended Mix).mp3 
  → /downloads/UKG/Sammy Virji/Flowers (Extended Mix).mp3

Update MongoDB:
  download_history document:
    filename: "Flowers (Extended Mix).mp3"
    relative_path: "UKG/Sammy Virji/Flowers (Extended Mix).mp3"
    folder: "UKG/Sammy Virji"
    organized: True
    organize_mode: "dj_hybrid"
```

**OUTPUT**:
```json
{
  "status": "success",
  "filename": "Flowers (Extended Mix).mp3",
  "filepath": "/downloads/UKG/Sammy Virji/Flowers (Extended Mix).mp3",
  "message": "Successfully downloaded: Flowers (Extended Mix).mp3",
  "match_quality": "exact",
  "quality_report": { ... },
  "organized": True,
  "organize_result": {
    "moved": True,
    "old_path": "/downloads/Manual/Flowers (Extended Mix).mp3",
    "new_path": "/downloads/UKG/Sammy Virji/Flowers (Extended Mix).mp3",
    "folder": "UKG/Sammy Virji",
    "artist": "Sammy Virji",
    "genre": "Unknown"
  }
}
```

---

## 9. KNOWN ISSUES (FROM CODE)

### 1. Duplicate Risk: Normalized vs Exact

**Issue**: Duplicate detection uses TWO different comparison methods:
- Exact: `filename == existing_filename`
- Normalized: `normalize(filename) == normalize(existing)`

**Risk**: If track "Song.mp3" exists but user requests "SONG.mp3" (different case), both checks pass AND file is skipped, but normalized check cannot distinguish between legitimately different files.

**Location**: [downloader_service.py](downloader_service.py#L450-L475)

---

### 2. Organization Classification Fallback is Too Broad

**Issue**: Default classification always falls back to `("Bollywood", artist_name)` regardless of actual track type:

```python
# Smart defaults (NO FAIL)
# If genre unknown + artist not in ARTIST_MAP → ALWAYS returns Bollywood
return ("Bollywood", artist_clean)
```

**Risk**: Electronic/House/Rock tracks without genre tags and without artist mapping all end up in Bollywood folder.

**Location**: [organizer_service.py](organizer_service.py#L135-L140)

---

### 3. Mode Parameter is Silently Ignored

**Issue**: `organize_file(mode="artist")` or `organize_file(mode="genre")` both silently force `mode="dj_hybrid"`:

```python
def organize_file(filename: str, mode: str = "dj_hybrid", ...):
    # FORCE dj_hybrid mode — ignore mode parameter
    mode = "dj_hybrid"
```

**Risk**: Frontend or API consumers expecting mode switching will see no effect and no warning.

**Location**: [organizer_service.py](organizer_service.py#L210)

---

### 4. Verified Channel Bonus is Extreme

**Bonus value**: `+0.30` (+30 on 0-100 scale) for verified channel badge

**Risk**: Can override poor title/duration matching:
```
title_score = 0.4 (poor match)
artist_score = 0.3 (weak)
duration_score = 0.2 (acceptable)
verified_bonus = +0.30 (extreme)
final = 0.5 * 0.4 + 0.3 * 0.3 + 0.2 * 0.2 + 0.30 = 0.41 (still below 0.50)
```

But with VEVO + Official channel:
```
official_bonus (in channel) = +0.15
official_bonus (in title) = +0.05
verified_bonus = +0.30
final = 0.5*0.4 + 0.3*0.3 + 0.2*0.2 + 0.50 = 0.71 (ACCEPTED despite poor title match)
```

**Location**: [strict_matcher.py](strict_matcher.py#L200-L215)

---

### 5. Socket.IO Disconnect Risk with ThreadPoolExecutor

**Issue**: Auto-downloader uses `ThreadPoolExecutor` with `MAX_WORKERS=2`, but calls `_socketio.sleep()` from worker threads:

```python
# DISCONNECT FIX comment in auto_downloader.py states issue is KNOWN:
# "Calling _socketio.sleep() from ThreadPoolExecutor worker threads corrupts 
#  the eventlet hub and directly causes WebSocket disconnections"
```

**Current mitigation**: Code uses `time.sleep()` instead, but Socket.IO event rate-limiting still happens in emit:

```python
_last_emit_times = {}
if now - last < 0.3:
    return  # Skip emit if < 300ms since last emit
```

**Residual risk**: If 2 workers emit simultaneously, race condition on `_last_emit_times` dict (uses threading.Lock but still subject to missed updates if interleaved).

**Location**: [auto_downloader.py](auto_downloader.py#L75-L95)

---

### 6. Celery Tasks Never Actually Used for Manual Downloads

**Issue**: `download_track_task` is defined with full retry logic, BUT manual downloads via `/api/download` use blocking `socketio.start_background_task()`:

```python
# In app.py:
socketio.start_background_task(target=_download_background, url=url)
# This is BLOCKING (waits for download to complete before returning to eventlet hub)

# NOT:
download_track_task.delay(track_metadata, output_path)
```

**Risk**: Manual downloads don't benefit from Celery's:
- Distributed workers
- Automatic retries with backoff
- Task monitoring
- Failure persistence

**Location**: [app.py](app.py#L375-L410)

---

### 7. yt-dlp Format Selection Doesn't Account for Available Codecs

**Issue**: Format string prioritizes lossless (FLAC) and M4A, but doesn't check if source actually has these:

```python
if duration_sec < 60:
    format = 'bestaudio[ext=m4a]/bestaudio/best'
else:
    format = 'bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best'
```

**Risk**: If YouTube only has MP3 audio available, fallback chain works, but:
- Extra processing (decode MP3 → re-encode to MP3 @ 320kbps)
- No advantage to lossless preference if source was already MP3

**Location**: [downloader_service.py](downloader_service.py#L260-L275)

---

### 8. File Size Validation Uses Inaccurate Expected Size Calculation

**Issue**: Expected file size based on bitrate × duration assumes linear relationship:

```python
expected_bytes = (duration_ms / 1000.0) * (320_000 / 8)
# 320_000 bits/sec ÷ 8 = 40,000 bytes/sec

if actual_bytes > expected_bytes * 3:
    # REJECT file
```

**Risk**: 
- VBR (variable bitrate) MP3s can exceed this by up to 40% without being wrong
- Threshold of 3× is loose (allows 3x size, but rejects for audio quality variations)
- Example: 5-minute track @ 320kbps constant would be ~120MB expected, but actual VBR might be 150MB → ACCEPTED. But 180MB might be rejected as "wrong track" when it's actually just higher quality.

**Location**: [downloader_service.py](downloader_service.py#L535-L545)

---

### 9. Auto-Downloader Doesn't Check File Existence Before Recording Success

**Issue**: `ingest_download()` calls `downloader_service.download_track()` and assumes success if status="success", but doesn't verify MP3 file actually exists post-tagging/organization:

```python
result = downloader_service.download_track(title, artist, ...)
if result["status"] == "success":
    # Record as success
    saved_ids.add(track_id)
    # BUT: file might have been moved/deleted by organizer error recovery
```

**Risk**: If organizer crashes mid-move, file might be in limbo, but track is marked as "already downloaded" in ingest_tracks.json.

**Location**: [auto_downloader.py](auto_downloader.py#L300-L350)

---

### 10. Rate-Limit Backoff Doesn't Account for Concurrent Requests

**Issue**: Spotify API rate-limit detection blocks globally:

```python
if time.time() < api_usage["rate_limited_until"]:
    wait_left = int(api_usage["rate_limited_until"] - time.time())
    raise ValueError(f"Retry in {wait_left}s")
```

**Risk**: If one thread triggers 429, ALL threads/workers are blocked for cooldown duration (up to 600+ seconds). No per-user or per-track backoff.

**Location**: [spotify_service.py](spotify_service.py#L80-L100)

---

**END OF AUDIT**
