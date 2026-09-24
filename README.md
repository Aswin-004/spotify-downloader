# SpotifyDL — DJ Music Downloader & Organiser

Downloads tracks from Spotify, enriches them with ID3 metadata, and automatically
organises them into genre-based folders. Built for DJs who want a clean, sorted local library.

---

## Features

- **Auto-sync** — monitors a Spotify playlist and downloads new tracks automatically
- **Smart genre routing** — your artist lists and what the app learned from your own moves → verified evidence (Last.fm / MusicBrainz / iTunes) → Groq *listens* to the song (language, lyrics) and picks a folder → the catch-all (see `docs/AUTOMATION.md`)
- **Custom folder mapping** — point the app at your existing DJ folder structure
- **Album artwork** — embeds Spotify cover art as ID3 APIC frames
- **Celery + Redis** — optional async task queue with automatic fallback to threading
- **Discord notifications** (optional) — alerts on download completion and storage warnings

---

## Setup (Windows)

**Full step-by-step guide: [docs/SETUP.md](docs/SETUP.md)** (free accounts to create, what to paste where).

1. Create a Spotify developer app (Redirect URI `http://127.0.0.1:8888/callback`), a free MongoDB Atlas
   database and a free Groq key; optionally Last.fm and AcoustID keys.
2. Double-click **`setup.bat`**: installs Python/Node packages (and offers to install Python/Node themselves),
   builds the web app, creates `backend/.env` for you to fill in, logs in to Spotify once and checks everything.
3. Double-click **`start.bat`** and open **http://localhost:5000**.

Check your setup any time: `..\.venv\Scripts\python.exe check_setup.py` from the `backend` folder.

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | installed by setup.bat if missing |
| Node.js | 18+ | builds the web app; installed by setup.bat if missing |
| MongoDB | Atlas M0 (free) or local 6+ | |
| ffmpeg | — | bundled (`imageio-ffmpeg`), nothing to install |
| fpcalc | optional | AcoustID fingerprinter: put `fpcalc.exe` in `backend/` |
| Redis | optional | faster task queue; the app uses threads without it |

---

## API Keys

| Key | Where to get | |
|-----|-------------|---|
| `SPOTIFY_CLIENT_ID` / `SECRET` | [developer.spotify.com](https://developer.spotify.com/dashboard) | required |
| `MONGODB_URI` | [mongodb.com/atlas](https://www.mongodb.com/cloud/atlas/register) | required |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) | required: listens to unknown songs and picks their genre |
| `LASTFM_API_KEY` | [last.fm/api](https://www.last.fm/api/account/create) | recommended |
| `ACOUSTID_API_KEY` | [acoustid.org](https://acoustid.org/new-application) | recommended: rejects wrong-song / karaoke downloads |
| `DISCORD_WEBHOOK_URL` | Discord → Server Settings → Integrations → Webhooks | optional |

All settings live in `backend/.env` (template: `backend/.env.example`). It is git-ignored: never commit or share it.

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
└── Library/
    ├── House/  Techno/  Trance/  Drum & Bass/  Dubstep/  UK Garage/
    ├── Bollywood/  Punjabi/  Tamil/  Indie/  Indian Hip Hop/
    ├── International Hip Hop/  R&B/  Pop/  Latin/
    └── Electronic/        ← catch-all: nothing could tell the genre yet (re-checked hourly)
```

Move a song to another folder yourself and the app learns that artist's folder (`docs/AUTOMATION.md`).

---

## Pages

| Page | Purpose |
|------|---------|
| **Download** | Paste Spotify URL → download track, album, or playlist |
| **History** | Log of all downloads with status and error details |
| **Files** | Browse downloaded MP3s by genre folder |
| **Library** | Search library with album art thumbnails |
| **Analytics** | Stats, top artists, genre distribution |
| **Review** | Retry catch-all tracks |
| **Maintenance** | Reorganise library, repair index, backfill AI genre tags |
| **Settings** | Custom folder mappings, API keys |
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
