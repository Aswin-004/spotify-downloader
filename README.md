# SpotifyDL — DJ Music Downloader & Organiser

Downloads tracks from Spotify, enriches them with ID3 metadata, and automatically
organises them into genre-based folders. Built for DJs who want a clean, sorted local library.

---

## Features

- **Auto-sync** — monitors a Spotify playlist and downloads new tracks automatically
- **Smart genre routing** — 7-step chain (artist override → Spotify → Last.fm → MusicBrainz → AcoustID → Gemini) routes each track to the right folder
- **Custom folder mapping** — point the app at your existing DJ folder structure
- **Album artwork** — embeds Spotify cover art as ID3 APIC frames
- **Celery + Redis** — optional async task queue with automatic fallback to threading
- **Telegram notifications** — alerts on download completion and storage warnings

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.10+ | |
| Node.js | 18+ | For frontend build |
| MongoDB | 6+ | Local or Atlas |
| Redis | 7+ | Optional — for Celery |
| fpcalc | latest | AcoustID fingerprinter — [download](https://acoustid.org/chromaprint) |

---

## Setup

**1. Clone and install Python dependencies**
```bash
cd backend
pip install -r requirements.txt
```

**2. Install Node dependencies and build the frontend**
```bash
cd frontend-react
npm install
npm run build
```

**3. Configure environment**
```bash
cd backend
copy .env.example .env   # Windows
# Edit .env with your API keys (see below)
```

**4. Run the app**

Double-click `start.bat` — it builds the frontend, starts Redis + Celery (if available), then launches the backend.

Open **http://localhost:5000** in your browser.

---

## API Keys

| Key | Where to get | Free tier |
|-----|-------------|-----------|
| `SPOTIFY_CLIENT_ID` / `SECRET` | [developer.spotify.com](https://developer.spotify.com/dashboard) | Yes |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) | 15 calls/day |
| `LASTFM_API_KEY` | [last.fm/api](https://www.last.fm/api/account/create) | Yes |
| `ACOUSTID_API_KEY` | [acoustid.org](https://acoustid.org/api-key) | Yes |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram | Optional |

All keys go in `backend/.env` — copy `backend/.env.example` as a template.

> **Security:** Never commit `backend/.env` to git. It is already in `.gitignore`.
> Regenerate all keys before sharing the repo publicly.

---

## Configuration

```env
# Where your music lives — genre subfolders are created automatically
BASE_DOWNLOAD_DIR=C:\Users\You\DJ Music

# Spotify playlist to auto-sync from
INGEST_PLAYLIST_ID=your_playlist_id

# How often to check for new tracks (milliseconds)
CHECK_INTERVAL=500
```

---

## Custom Folder Mapping (for existing libraries)

If you already have an organised folder structure:

1. Open **Settings** in the sidebar
2. Click **Scan My Music Folder** — the app lists your existing subfolders
3. Map each folder to a genre label (e.g. `"Drum and Bass"`)
4. Save — future downloads for that genre route into your folder

---

## Folder Structure

```
BASE_DOWNLOAD_DIR/
├── House/
├── Techno/
├── Drum and Bass/
├── Hip Hop/
│   └── track.mp3
├── Library/
│   └── Electronic/        ← catch-all (genre not detected)
└── NeedsReview/           ← low-confidence classifications
```

---

## Pages

| Page | Purpose |
|------|---------|
| **Download** | Paste Spotify URL → download track, album, or playlist |
| **History** | Log of all downloads with status and error details |
| **Files** | Browse downloaded MP3s by genre folder |
| **Library** | Search library with album art thumbnails |
| **Analytics** | Stats, top artists, genre distribution |
| **Review** | Retry catch-all tracks when Gemini quota resets |
| **Maintenance** | Reorganise library, repair index, backfill Gemini tags |
| **Settings** | Custom folder mappings, Telegram notifications |
| **Guide** | In-app getting-started guide |

---

## Architecture

```
Frontend (React + Vite)  ──HTTP──▶  Flask backend
                         ◀─Socket.IO─  (real-time events)

Flask backend
  ├── Auto-downloader thread (polls Spotify playlist)
  ├── Maintenance worker thread (background retag/repair)
  ├── Celery worker (optional, requires Redis)
  └── MongoDB (track index, genre cache, history)
```

---

## License

MIT
