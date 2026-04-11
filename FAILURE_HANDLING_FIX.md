# Failure Handling Fix: Separation of Download from Post-Processing

## Root Cause
Files were successfully downloaded but marked as FAILED due to errors in post-processing stages (tagging, organization, or notifications). This caused the system to retry downloads unnecessarily, creating duplicates.

## Architecture Changes

### 1. DOWNLOAD STAGE (Only failure here causes fallback/retry)
- **Wrapper**: Inner try-catch
- **Responsibility**: yt-dlp download only
- **Failure Result**: Returns `{"status": "fallback"}` → supports manual YouTube download
- **Code Location**: Lines 560-600 in downloader_service.py

```python
download_success = False

try:
    filename = self._download_from_youtube(...)
    download_success = True
except Exception as download_error:
    # Log error and return fallback (manual YouTube)
    return {"status": "fallback", "manual_url": ...}
```

### 2. POST-PROCESSING STAGES (Errors captured but never cause failure status)
- **Stages**:
  1. FFmpeg post-processing (silence trim, loudness norm, album art embed)
  2. Tagging (MusicBrainz/Spotify ID3 tags)
  3. BPM/Key analysis (librosa)
  4. Organization (folder structure)

- **Error Handling**: Individual try-catches per stage
- **Failure Result**: Captured in `tagging_error` and `organize_error` strings
- **Code Location**: Lines 600-720 in downloader_service.py

```python
tagging_error = None
organize_error = None

try:
    # Tagging logic
except Exception as tag_err:
    tagging_error = str(tag_err)  # Captured, not fatal
    logger.warning(f"[tagger] Tagging failed: {tag_err}")
```

### 3. SUCCESS RESPONSE LOGIC (CRITICAL CHANGE)
File downloaded = **ALWAYS SUCCESS**, even if post-processing fails

```python
# File exists = SUCCESS status (no retries)
result = {
    "status": "success",
    "filename": filename,
    "filepath": filepath,
    "warning": "Downloaded but tagging failed: ..." if tagging_error else None,
}
return result
```

**Key**: No post-processing error causes return of `{"status": "failed"}` or `{"status": "fallback"}`

---

## API Response Changes

### Before (WRONG - caused retries)
```json
{
    "status": "fallback",
    "error": "[Tagging failed] MusicBrainz lookup error",
    "manual_url": "..."
}
```
→ System retries, creates duplicate

### After (CORRECT - file is success)
```json
{
    "status": "success",
    "filename": "song.mp3",
    "filepath": "/path/to/song.mp3",
    "warning": "Downloaded but tagging failed: MusicBrainz timeout"
}
```
→ System marks as done, no retry

---

## Changes to auto_downloader.py

**Before**: Any `result["status"] != "success"` → records failure → retries

**After**: 
```python
if result["status"] == "success":
    # Count as success (even if warning present)
    success_count[0] += 1
else:
    # Check if file actually exists before recording failure
    if not (os.path.isfile(file_path) and os.path.getsize(file_path) > 1000):
        # True failure: no file and no success status
        fail_count[0] += 1
        record_failure(track_id)
    else:
        # File exists despite fallback status (e.g., manual download)
        skip_count[0] += 1  # Don't retry
```

---

## Idempotency Guarantee

✅ **No re-download of same songs**
- Duplicate check runs BEFORE download attempt
- Uses (artist, title) tuple if available

✅ **No duplicate files (_1, _2 suffixes)**
- Download succeeds once per track
- Organization collision handling is separate from download retry logic

✅ **Failed list only contains true failures**
- Only recorded when file doesn't exist AND download returned fallback/error
- File existence check prevents false positives

✅ **Post-processing failures non-blocking**
- Tagging fails → warning field, not failed status
- Organization fails → warning field, not failed status
- Notification fails → logged, not fatal

---

## Files Modified

1. **backend/services/downloader_service.py**
   - Lines 560-770: Restructured download_track()
   - Separated STAGE 1 (download) from STAGE 2 (post-processing)
   - Added `download_success` flag
   - Added `tagging_error` and `organize_error` tracking

2. **backend/services/auto_downloader.py**
   - Lines 388-420: Enhanced failure detection
   - Added file existence check before recording failure
   - Improved skip vs fail vs success categorization

---

## Testing Checklist

- [ ] Download successful track → status="success"
- [ ] Download with tagging failure → status="success" + warning
- [ ] Downloaded file with tagging failure, re-run → skipped (not retried)
- [ ] True yt-dlp failure → status="fallback" (manual YouTube)
- [ ] No duplicate _1, _2 files created
- [ ] Organization errors don't trigger retries
- [ ] Failure list only contains true yt-dlp failures
