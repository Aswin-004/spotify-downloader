# Spotify Meta Downloader

A full-stack music automation platform that converts Spotify links into high-quality MP3 downloads with intelligent source matching, real-time monitoring, MusicBrainz metadata tagging, and a Telegram bot controller.

> Built with Flask, React, Celery, Redis, MongoDB, and Docker.

---

## Features

### Core Download Engine
- Paste any Spotify **track, album, or playlist** link to download
- High-quality **MP3 conversion at 192 kbps** via FFmpeg
- **Multi-stage YouTube search** with 3-level fallback (official audio → audio → general)
- **Scoring-based matching engine** — title similarity, artist verification, duration tolerance (30s ceiling), official track boost
- Keyword exemption system to filter remixes/edits correctly
- Per-track deduplication guard to prevent duplicate downloads

### Auto Playlist Sync
- Background daemon monitors a configured Spotify playlist
- Detects newly added tracks and queues downloads automatically
- Configurable polling interval (default 500s)
- Pause/resume support via Telegram

### Ingest System (No OAuth Required)
- Dedicated public "Ingest Playlist" — copy songs in, app auto-downloads
- Works with any Spotify playlist, no user login required

### File Organization
- Three organization modes:
  - **Artist** — `downloads/Artist/Song.mp3`
  - **Genre** — `downloads/Genre/Song.mp3`
  - **Genre → Artist** — `downloads/Genre/Artist/Song.mp3`
- ID3 tag reading (Mutagen) for artist/genre extraction
- Genre mapping to predefined categories
- Collision handling (`_1`, `_2` suffixes)
- Batch organization from the Library UI

### MusicBrainz Metadata Tagging
- Acoustic fingerprinting via MusicBrainz API
- Automatic ID3 tag enrichment (title, artist, album, year, genre)
- 30-day MongoDB cache with TTL auto-expiry
- Fallback to Spotify metadata when MusicBrainz lookup fails
- Failure tracking with retry from Analytics dashboard

### Analytics Dashboard
- Overview cards: total downloads, success rate, unique artists
- Downloads per day (line chart, configurable range)
- Top artists (bar chart)
- Source platform breakdown (pie chart)
- Tagging source metrics
- Recent downloads table
- Failed downloads table with one-click retry

### Real-Time Dashboard
- Live download progress per track
- Active queue display
- Activity feed with timestamps
- WebSocket updates via Socket.IO

### Telegram Bot Controller
- Full remote control via Telegram commands:

| Command | Description |
|---|---|
| `/start` | Show welcome message |
| `/status` | Auto-downloader status |
| `/pause` / `/resume` | Pause or resume the auto-downloader |
| `/progress` | Current download progress |
| `/library` | Show library stats |
| `/find <query>` | Search library by artist/title |
| `/location` | Download directory path |
| `/skipped` | List skipped/failed tracks |
| `/reset_skipped` | Clear skipped tracks list |
| `/storage` | Show storage usage |
| `/help` | All available commands |

- Send a Spotify URL directly to the bot to trigger a download
- Auth-gated via `TELEGRAM_CHAT_ID`

### Notification System
- **Telegram** notifications on download success/failure and playlist completion
- **Discord webhook** support
- Storage threshold alerts (configurable MB limit)

### Async Task Queue
- **Celery + Redis** for async download processing
- Graceful fallback to threading if Redis is unavailable
- Socket.IO event bridging via Redis pub/sub
- Flower dashboard for task monitoring

---

## Tech Stack

### Backend
| Layer | Technology |
|---|---|
| Framework | Flask 3.0.0 + Flask-SocketIO 5.6.1 |
| Task Queue | Celery 5.3.6 + Redis 5.0.1 |
| Database | MongoDB 7 (PyMongo) |
| Spotify API | Spotipy 2.26.0 |
| Download | yt-dlp 2026.x |
| Audio | FFmpeg + Mutagen |
| Metadata | MusicBrainz (musicbrainzngs) |
| Matching | thefuzz[speedup] |
| Notifications | httpx (Telegram/Discord) |
| Logging | loguru |

### Frontend
| Layer | Technology |
|---|---|
| Framework | React 18.3 + Vite 5 |
| Styling | Tailwind CSS 3.4 |
| UI Components | shadCN UI |
| Routing | React Router v6 |
| Real-time | Socket.IO Client 4.7 |
| Charts | Recharts 3.8 |
| Animations | Framer Motion 11 |
| Icons | Lucide React |

### Infrastructure
| Layer | Technology |
|---|---|
| Containers | Docker + Docker Compose |
| Cache/Queue | Redis (Alpine) |
| Persistence | MongoDB 7 |

---

## Architecture

```
Browser (React + Tailwind + Socket.IO)
        │
        ▼
Flask Backend (port 5000)
├── REST API (28+ endpoints)
├── Socket.IO (real-time events)
└── Celery Worker (async tasks)
        │
        ├── Spotify API ──── metadata extraction
        ├── yt-dlp ────────── YouTube search & download
        ├── FFmpeg ─────────── MP3 conversion (192 kbps)
        ├── MusicBrainz API ── acoustic fingerprinting & ID3 tags
        └── Mutagen ────────── ID3 tag read/write
        │
        ├── MongoDB ────────── download history, MB cache, analytics
        ├── Redis ──────────── task queue, pub/sub, result backend
        └── Downloads Dir ──── organized MP3 files
        │
        ├── Telegram Bot ───── remote control daemon
        └── Discord Webhooks ── notifications
```

---

## Project Structure

```
spotify-meta-downloader/
├── backend/
│   ├── app.py                    # Flask app, Socket.IO, REST API
│   ├── tasks.py                  # Celery task definitions
│   ├── celery_app.py             # Celery + Redis configuration
│   ├── config.py                 # Environment configuration
│   ├── database.py               # MongoDB connection layer
│   ├── utils.py                  # Shared helpers
│   ├── telegram_bot.py           # Telegram bot controller
│   ├── organizer_service.py      # Compatibility shim → services/
│   ├── run_organize.py           # CLI for manual library organization
│   ├── routes/
│   │   └── library.py            # Flask Blueprint: /api/library/*
│   ├── services/
│   │   ├── spotify_service.py    # Spotify API wrapper
│   │   ├── downloader_service.py # Download + conversion engine
│   │   ├── auto_downloader.py    # Playlist auto-sync daemon
│   │   ├── organizer_service.py  # File organization + ID3
│   │   ├── tagger_service.py     # MusicBrainz tagging
│   │   ├── analytics_service.py  # MongoDB aggregation queries
│   │   ├── notifications_service.py # Telegram/Discord notifications
│   │   ├── metadata_cache.py     # Caching layer
│   │   └── strict_matcher.py     # Title/artist matching algorithm
│   ├── tests/
│   │   ├── test_full_pipeline.py
│   │   └── validation_test.py
│   ├── archive/                  # Legacy service versions
│   └── requirements.txt
│
├── frontend-react/
│   ├── src/
│   │   ├── App.jsx               # Router root (5 routes)
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx     # Download UI + real-time feed
│   │   │   ├── History.jsx       # Download history table
│   │   │   ├── Files.jsx         # File browser + delete
│   │   │   ├── LibraryPage.jsx   # File organization UI
│   │   │   └── Analytics.jsx     # Charts + stats dashboard
│   │   ├── components/           # Reusable UI components
│   │   ├── services/
│   │   │   └── api.js            # Centralized API client (28 methods)
│   │   └── hooks/
│   │       └── useSocket.jsx     # Socket.IO hook
│   └── package.json
│
├── docker-compose.yml            # Redis + MongoDB services
├── .env.example                  # Environment variable template
└── README.md
```

---

## Setup & Installation

### Prerequisites
- Python 3.10+
- Node.js 18+
- FFmpeg installed and in PATH
- Docker (optional, for Redis + MongoDB)

---

### 1. Clone the Repository

```bash
git clone https://github.com/Aswin-004/spotify-downloader.git
cd spotify-downloader
```

---

### 2. Start Infrastructure (Docker)

```bash
docker-compose up -d
```

This starts Redis (port 6379) and MongoDB (port 27017) with persistent volumes.

---

### 3. Backend Setup

```bash
cd backend
pip install -r requirements.txt
```

Copy and configure your environment:

```bash
cp ../.env.example .env
```

Edit `.env` with your credentials:

```env
# Spotify API (required)
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret

# Download location
BASE_DOWNLOAD_DIR=/path/to/your/music

# Auto-sync playlist (optional)
PLAYLIST_ID=your_spotify_playlist_id
INGEST_PLAYLIST_ID=your_ingest_playlist_id

# Database
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=spotify_downloader

# Task queue
REDIS_URL=redis://localhost:6379/0

# Telegram bot (optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Discord notifications (optional)
DISCORD_WEBHOOK_URL=your_webhook_url

# File organization
ORGANIZE_MODE=artist   # artist | genre | artist_genre
CHECK_INTERVAL=500     # seconds between playlist checks
```

Run the backend:

```bash
python app.py
```

Optionally start the Celery worker for async processing:

```bash
celery -A celery_app worker --loglevel=info --concurrency=3
```

---

### 4. Frontend Setup

```bash
cd frontend-react
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`, proxying API calls to `http://localhost:5000`.

---

## API Reference

### Download & Tracks
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/track` | Extract metadata from Spotify URL |
| `POST` | `/api/download` | Start a manual download |
| `GET` | `/api/history` | Download history |
| `POST` | `/api/history/clear` | Clear history |
| `GET` | `/api/files` | List downloaded files |
| `DELETE` | `/api/delete/<filename>` | Delete a file |

### Auto-Downloader
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/auto-status` | Auto-downloader status |
| `POST` | `/api/refresh-playlist` | Trigger manual playlist sync |
| `GET` | `/api/queue-status` | Current queue state |
| `GET` | `/api/ingest-config` | Ingest playlist configuration |

### Library Organization
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/library/organize` | Batch organize by mode |
| `POST` | `/api/library/organize-recent` | Organize recently modified files |
| `POST` | `/api/library/retag` | Batch MusicBrainz retag |
| `GET` | `/api/library/retag/status` | Retag operation status |

### Analytics
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/analytics/overview` | Summary stats |
| `GET` | `/api/analytics/downloads-per-day` | Daily download counts |
| `GET` | `/api/analytics/top-artists` | Top artists ranking |
| `GET` | `/api/analytics/source-breakdown` | Platform breakdown |
| `GET` | `/api/analytics/tagging-breakdown` | Tagging source stats |
| `GET` | `/api/analytics/recent` | Recent downloads |
| `GET` | `/api/analytics/failed` | Failed downloads |

### Health
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Service health check |
| `GET` | `/api/api-usage` | Spotify API usage stats |

---

## Screenshots

### Dashboard
![Dashboard](docs/screenshots/dashboard.png)

### Download History
![History](docs/screenshots/history.png)

### File Browser
![Files](docs/screenshots/files.png)

---

## Engineering Highlights

- **Scoring-based matching engine** — prevents wrong song downloads using multi-factor scoring (title similarity, artist match, duration, official boost)
- **Multi-stage fallback search** — 3-level YouTube search strategy improves success rate on obscure tracks
- **Graceful Celery degradation** — falls back to threading if Redis is unavailable, zero config required
- **30-day MusicBrainz cache** — MongoDB TTL index auto-expires stale fingerprint data
- **Real-time pipeline** — Socket.IO bridges Celery workers to the browser via Redis pub/sub
- **Rate-limit aware** — Spotify API throttled at 0.35s minimum between calls with exponential backoff on 429s
- **Blueprint-based routing** — Flask routes split across `app.py` and `routes/library.py` for maintainability
- **Service layer pattern** — `backend/services/` isolates all business logic from HTTP handlers

---

## Challenges & Solutions

| Challenge | Solution |
|---|---|
| Spotify rate limits | 0.35s throttle + MongoDB cache + exponential backoff |
| Wrong song matches | Multi-factor scoring + duration ceiling + keyword exemptions |
| yt-dlp errors | 3-stage format fallback (bestaudio/best) |
| Celery/Redis unavailable | Transparent threading fallback in `celery_app.py` |
| Stale metadata | MusicBrainz fingerprinting + 30-day TTL cache |
| Missing ID3 tags | Post-download Mutagen pass + MusicBrainz enrichment |
| Concurrent downloads | Threading pool with per-track deduplication guard |

---

## License

This project is for educational and personal use.

---

## Author

**Aswin Abhinab Mohapatra**
- Email: [aswin.abhinab22@gmail.com](mailto:aswin.abhinab22@gmail.com)
- GitHub: [github.com/Aswin-004](https://github.com/Aswin-004)
