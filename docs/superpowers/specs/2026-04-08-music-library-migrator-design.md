# Music Library Migrator — Design Spec
**Date:** 2026-04-08  
**Status:** Approved  

---

## Problem

The music library is structured as 50+ artist folders, each containing individual songs. This is hard to navigate and mixes multiple languages and genres with no consistent grouping. The goal is to reorganise it into four flat language/genre buckets: Punjabi, English, Hindi, House.

---

## Approach

Option A — single service file + thin CLI runner + Telegram command.

`services/library_migrator.py` contains all logic. `run_migrate.py` is the CLI entry point. The Telegram `/organize` command calls the same service function in a background daemon thread, identical to how Spotify downloads are dispatched in the existing bot. The service has no Flask dependency.

---

## Files

### New files
| File | Purpose |
|---|---|
| `backend/services/library_migrator.py` | All migration logic |
| `backend/config/artist_categories.json` | Artist→category mapping (pre-seeded) |
| `backend/run_migrate.py` | Thin CLI runner |

### Modified files
| File | Change |
|---|---|
| `backend/telegram_bot.py` | Add `/organize` command handler, register in `_run_bot()` |

No Flask imports in the service. MongoDB updates are optional — silently skipped if unavailable.

---

## Destination Structure

```
<dest_root>/
├── Punjabi/     ← flat, no subfolders
├── English/
├── Hindi/
└── House/
```

Supported audio extensions: `.mp3`, `.flac`, `.wav`, `.m4a`, `.aac`

---

## Artist Categorisation

### Pre-seeded mappings

| Artist | Category |
|---|---|
| Diljit Dosanjh | Punjabi |
| Badshah | Punjabi |
| AP Dhillon | Punjabi |
| Jazzy B | Punjabi |
| Yo Yo Honey Singh | Punjabi |
| Gurinder Gill | Punjabi |
| Imran Khan | Punjabi |
| Jaz Dhami | Punjabi |
| Shashwat Sachdev | Punjabi |
| Bad Bunny | English |
| Drake | English |
| JVKE | English |
| Leo Grewal | English |
| Meet Bros | English |
| Sweetaj Brar | English |
| Amit Trivedi | Hindi |
| Anirudh Ravichander | Hindi |
| Himesh Reshammiya | Hindi |
| Pritam | Hindi |
| Raja Baath | Hindi |
| This is sammy Virji | Hindi |
| Mika Singh | Hindi |
| Electronic House 2025 | House |
| Indo House & Techno | House |
| House Music 2026 'ॐ' Party House Mix | House |
| drum and bass | House |

### Unresolved (null in config)
Anuv Jain, Cheema Y, Gminxr, hugel, Ingest, Manual, Mau P, NIJJAR, Nimino, odia, This Is James Hype, Unknown

### Config format (`artist_categories.json`)
```json
{
  "categories": ["Punjabi", "English", "Hindi", "House"],
  "mappings": {
    "Diljit Dosanjh": "Punjabi",
    "hugel": null
  }
}
```

`null` = unresolved. Any artist folder on disk missing from `mappings` entirely is also treated as unresolved.

---

## Interactive Fallback

At runtime, unresolved artists are handled differently depending on context:

**Interactive (TTY detected — `run_migrate.py`):**
- Prompt user for each unresolved artist before migration starts:
  ```
  Artist: "hugel"
  Assign to: [1] Punjabi  [2] English  [3] Hindi  [4] House  [5] Skip
  >
  ```
- Answer saved back to `artist_categories.json` immediately
- Next run is fully automated with no prompts

**Non-interactive (piped, cron, Telegram `/organize`):**
- Unresolved artists are skipped silently
- Listed in final report
- Their source folders are left untouched

---

## File Operations (Copy → Verify → Delete)

Per-file pipeline:

1. **Copy** `source/ArtistFolder/song.ext` → `dest/Category/song.ext`
2. **Verify** MD5 hash of source == destination. If mismatch: delete bad copy, log error, continue.
3. **Delete source** only after verification passes
4. **Collision handling** — if `dest/Category/song.ext` already exists, append `_1`, `_2`, etc.
5. **Undo log** written per run to `logs/migrate_undo_YYYYMMDD_HHMMSS.json`:
   ```json
   [
     {"from": "dest/Punjabi/song.mp3", "to": "source/Diljit Dosanjh/song.mp3"}
   ]
   ```

**Post-processing:**
- Empty source artist folders → deleted automatically
- Non-empty source folders (non-audio files remain) → left untouched, flagged in report

---

## CLI Interface

```bash
# Interactive with dry-run prompt
python run_migrate.py

# Explicit paths (defaults: source=BASE_DOWNLOAD_DIR, dest=BASE_DOWNLOAD_DIR + "_organised")
python run_migrate.py --source /path/to/music --dest /path/to/organised

# Preview only (no file operations)
python run_migrate.py --dry-run

# Undo a previous run
python run_migrate.py --undo logs/migrate_undo_20260408_143022.json
```

`--dry-run` prints a full preview table of what would move where. No files are touched.

---

## Telegram `/organize` Command

- Auth-checked via `_auth_check`
- Rate-limited via `_rate_limiter`
- Wrapped in `@handle_command_error`
- Runs migration in a daemon thread (same pattern as `_run_spotify_download`)
- Always runs in non-interactive mode
- Sends three messages:
  1. Start confirmation with source/dest paths
  2. Progress update every 25 files, or on completion if total < 25 (`"⏳ 50/450 files processed..."`)
  3. Final report on completion

---

## Final Report Format

```
MUSIC LIBRARY MIGRATION REPORT
================================
Punjabi  : 120 files  (2.1 GB)
English  :  85 files  (1.8 GB)
Hindi    : 145 files  (3.2 GB)
House    : 100 files  (1.4 GB)
--------------------------------
Total    : 450 files  (8.5 GB)
Errors   : 0
Skipped artists (unresolved): hugel, NIJJAR
Undo log : logs/migrate_undo_20260408_143022.json
Duration : 2m 34s
```

Same format printed to terminal and sent as Telegram message (Telegram version uses HTML formatting).

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| MD5 mismatch after copy | Delete bad copy, log error, skip file, continue |
| Source file not readable | Log error, skip, continue |
| Destination write permission error | Log error, skip, continue |
| Duplicate filename at destination | Append `_1`, `_2`, etc. |
| Unresolved artist (non-interactive) | Skip folder, report at end |
| Unknown artist (not in config, interactive) | Prompt user, save answer |
| MongoDB unavailable | Skip DB update silently, log warning |

---

## Optional Enhancements (Post-MVP)

These are explicitly out of scope for the initial implementation but worth building next:

1. **`.m3u` playlist generation** — write one playlist file per category after migration completes
2. **ID3 tag fallback** — if an artist folder name isn't in the config, try reading the `TPE1` tag to guess the language/category before prompting
3. **Web UI progress** — emit Socket.IO events from the migrator so the React frontend can show a live progress bar
4. **Scheduled migration** — cron-style auto-run via the existing Telegram `/schedule` or a new `--watch` flag that monitors `BASE_DOWNLOAD_DIR` for new artist folders
5. **Multi-language fuzzy matching** — handle folder name variations like "diljit", "Diljit dosanjh", "DILJIT DOSANJH" mapping to the same config entry

---

## Success Criteria

1. All audio files in resolved artist folders moved to correct category
2. No file lost — MD5 verified before source deletion
3. File count in destination equals file count in source (for resolved artists)
4. Unresolved artists left untouched and listed in report
5. Undo log reverses migration completely
6. `artist_categories.json` updated with any new interactive answers
7. `/organize` Telegram command triggers the same pipeline non-interactively
