# Spotify Meta Downloader

A full-stack web application that downloads music from Spotify playlists using metadata matching. Features automatic playlist syncing, real-time WebSocket updates, intelligent audio matching with fuzzy scoring, and a dark-themed dashboard UI.

## Features

- **Manual Download** — Paste any Spotify track, album, or playlist URL to download
- **Auto Playlist Sync** — Monitors your Spotify playlist and downloads new tracks automatically
- **Ingest Playlist** — Sync a second public playlist for discovery downloads
- **Fuzzy Audio Matching** — Multi-stage search (YouTube → SoundCloud) with duration-validated fuzzy scoring
- **Smart Folder Routing** — Routes downloads by artist rules, language detection (Devanagari → Bollywood), and source type
- **Real-time Dashboard** — WebSocket-powered progress updates, queue status, and download history
- **Metadata Caching** — Thread-safe JSON cache reduces Spotify API calls by 90%+ (track TTL: 7 days, playlist TTL: 30 min)
- **Rate Limit Protection** — Automatic backoff with Retry-After extraction and proactive cooldown
- **Duplicate Detection** — File registry + normalized name matching prevents re-downloads

## Architecture

```
├── backend/
│   ├── app.py                 # Flask entry point, routes, WebSocket
│   ├── config.py              # Centralized configuration (reads from .env)
│   ├── spotify_service.py     # Spotify API: auth, metadata, playlists
│   ├── downloader_service.py  # yt-dlp download engine with fuzzy matching
│   ├── auto_downloader.py     # Playlist sync daemon, ingest, OAuth setup
│   ├── metadata_cache.py      # Thread-safe JSON metadata cache
│   ├── utils.py               # URL parsing, search queries, logging
│   └── requirements.txt       # Pinned Python dependencies
├── frontend/
│   ├── index.html             # Dashboard HTML (sidebar + tabbed content)
│   ├── js/app.js              # WebSocket client, UI logic
│   └── css/styles.css         # Dark theme dashboard styles
├── scripts/
│   ├── setup.bat              # Windows CMD setup script
│   ├── setup.ps1              # PowerShell setup script
│   └── install-ffmpeg.bat     # FFmpeg installation helper
├── docs/
│   └── QUICKSTART.md          # Step-by-step Windows setup guide
├── .env.example               # Environment variable template
└── .gitignore
```

## Prerequisites

- **Python 3.10+**
- **FFmpeg** — Required for audio conversion (bundled with spotdl or installed separately)
- **Spotify Developer Account** — Free at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/spotify-meta-downloader.git
cd spotify-meta-downloader
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r backend/requirements.txt
```

Or use the setup scripts:

```powershell
# PowerShell
.\scripts\setup.ps1

# CMD
scripts\setup.bat
```

### 2. Configure environment

```bash
cp .env.example backend/.env
```

Edit `backend/.env` with your credentials:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
BASE_DOWNLOAD_DIR=C:\Users\YourName\Music
PLAYLIST_ID=your_spotify_playlist_id
```

See [.env.example](.env.example) for all available options.

### 3. Authorize Spotify (one-time)

Required for playlist access via OAuth:

```bash
cd backend
python auto_downloader.py
```

This opens a browser for Spotify authorization. Add `http://127.0.0.1:8888/callback` as a Redirect URI in your [Spotify Dashboard](https://developer.spotify.com/dashboard) first.

### 4. Run

```bash
cd backend
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## How It Works

### Download Pipeline

1. **Metadata Fetch** — Spotify API (cache-first) retrieves track title, artist, duration
2. **Search** — Builds YouTube search query, fetches up to 10 candidates
3. **Fuzzy Scoring** — Each candidate scored: title similarity (50%) + artist similarity (30%) + duration match (20%)
4. **Duration Validation** — ±15s tolerance window, hard reject outside 0.3x–3x
5. **Download** — Best candidate downloaded via yt-dlp, converted to MP3 via FFmpeg
6. **Fallback** — 3 stages (YouTube filtered → YouTube unfiltered → SoundCloud), 2 retries each

### Auto-Sync

The playlist monitor runs in a background thread, checking for new tracks at a configurable interval (default: 500s). Uses snapshot-based delta detection to minimize API calls. Downloads run in parallel with dynamic worker count based on CPU cores.

## Configuration

All configuration is via environment variables in `backend/.env`:

| Variable | Description | Default |
|---|---|---|
| `SPOTIFY_CLIENT_ID` | Spotify app client ID | *required* |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret | *required* |
| `BASE_DOWNLOAD_DIR` | Root directory for all downloads | `backend/downloads` |
| `PLAYLIST_ID` | Main playlist to auto-sync | *empty* |
| `INGEST_PLAYLIST_ID` | Secondary playlist (optional) | *empty* |
| `REDIRECT_URI` | OAuth callback URL | `http://127.0.0.1:8888/callback` |
| `CHECK_INTERVAL` | Auto-sync interval in seconds | `500` |
| `SECRET_KEY` | Flask session secret | auto-generated |
| `FLASK_ENV` | `development` or `production` | `production` |

### Folder Rules

Artist-based routing is configured in `backend/config.py` via `FOLDER_RULES`:

```python
FOLDER_RULES = {
    "sammy virji": "Sammy Virji",
    "fred again": "Fred Again",
}
```

Tracks matching an artist pattern route to `Auto Downloads/{subfolder}/`. Titles with Devanagari script automatically route to `Bollywood/`.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/track` | Fetch metadata for a Spotify URL |
| POST | `/api/download` | Start download (returns 202, updates via WebSocket) |
| GET | `/api/files` | List all downloaded MP3 files |
| GET | `/api/auto-status` | Auto-downloader status |
| GET | `/api/queue-status` | Download queue status |
| GET | `/api/api-usage` | Spotify API usage stats |
| GET | `/api/history` | Download history |
| POST | `/api/refresh-playlist` | Force playlist refresh |
| GET | `/api/health` | Health check |

## Tech Stack

- **Backend:** Flask 3.0, Flask-SocketIO, Python 3.10+
- **Frontend:** Vanilla HTML/CSS/JS, Socket.IO
- **Download:** yt-dlp + FFmpeg
- **Metadata:** Spotify Web API (Spotipy)
- **Transport:** WebSocket (polling fallback)

## License

MIT
