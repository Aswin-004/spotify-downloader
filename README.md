# 🎧 Spotify Meta Downloader

Download Spotify tracks and albums as MP3 files with clean naming and folder organization.

## Features

- Download single tracks or full albums
- Clean filenames (`01 - Track Name.mp3`)
- Album-based folder structure
- Real-time progress bar
- Multi-source fallback (YouTube → SoundCloud)
- MP3 conversion at 192 kbps via FFmpeg

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, JavaScript
- **Download:** yt-dlp + FFmpeg
- **Metadata:** Spotify Web API (via Spotipy)

## Project Structure

```
spotify-meta-downloader/
├── backend/
│   ├── app.py                  # Flask API server
│   ├── config.py               # Configuration
│   ├── downloader_service.py   # yt-dlp download engine
│   ├── spotify_service.py      # Spotify API integration
│   ├── utils.py                # URL parsing, helpers
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── .gitignore
└── README.md
```

## Setup

### 1. Install FFmpeg

FFmpeg is required for MP3 conversion.

**Windows (Chocolatey):**
```
choco install ffmpeg
```

Or download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

### 2. Get Spotify API Credentials

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create an app
3. Copy your **Client ID** and **Client Secret**

### 3. Install & Run

```bash
# Install dependencies
pip install -r backend/requirements.txt

# Create backend/.env
echo SPOTIFY_CLIENT_ID=your_id > backend/.env
echo SPOTIFY_CLIENT_SECRET=your_secret >> backend/.env

# Start the server
cd backend
python app.py
```

Open [http://localhost:5000](http://localhost:5000)

## Usage

1. Paste a Spotify track or album URL
2. Click **Fetch** to see metadata
3. Click **Download** to start downloading
4. Watch real-time progress

### Supported URLs

```
https://open.spotify.com/track/TRACK_ID
https://open.spotify.com/album/ALBUM_ID
spotify:track:TRACK_ID
spotify:album:ALBUM_ID
```

## Output Example

```
downloads/
└── After Hours/
    ├── 01 - Alone Again.mp3
    ├── 02 - Too Late.mp3
    ├── 03 - Hardest To Love.mp3
    └── ...
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/track` | Fetch metadata (track or album) |
| POST | `/api/download` | Start download (background) |
| GET | `/api/status` | Real-time progress |
| GET | `/api/downloads` | List downloaded files |
| GET | `/api/health` | Health check |

## License

For personal use only. Respect copyright laws and platform terms of service.

## 📚 Dependencies

```
flask             - Web framework
spotipy           - Spotify API client
yt-dlp            - YouTube downloader
python-dotenv     - Environment variable loading
```

## 🎓 Learning Resources

- [Spotify Web API Docs](https://developer.spotify.com/documentation/web-api)
- [yt-dlp Documentation](https://github.com/yt-dlp/yt-dlp)
- [Flask Documentation](https://flask.palletsprojects.com/)

## 📄 License

This project is for educational and personal use only. Respect copyright laws and platform terms of service.

## 👨‍💻 Development Notes

### Directory Structure Explanation

- **backend/**: Flask server and services
  - `app.py`: Main Flask application with API routes
  - `config.py`: Configuration management
  - `spotify_service.py`: Spotify API integration
  - `downloader_service.py`: YouTube download logic
  - `utils.py`: Helper functions and validation
  - `downloads/`: Downloaded MP3 files stored here

- **frontend/**: Web UI
  - `index.html`: HTML structure
  - `styles.css`: Modern Spotify-inspired styling
  - `app.js`: Frontend logic and API calls

### Adding New Features

1. Update backend services as needed
2. Add new API endpoints to `app.py`
3. Update frontend UI and JavaScript
4. Test thoroughly before deployment

## 🤝 Support

For issues or questions:
1. Check the Troubleshooting section
2. Verify all dependencies are installed
3. Check logs in terminal for error messages
4. Ensure Spotify credentials are correct

## 📞 Contact

Created for personal music archival and learning purposes.

---

**Built with ❤️ for music enthusiasts**

Last Updated: March 2026