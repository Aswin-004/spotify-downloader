# Setting up your own copy (Windows)

You add a song to a Spotify playlist; the app downloads it as a 320 kbps MP3, tags it, and files it in a genre
folder (`House`, `Bollywood`, `Indie` ...) like a DJ would. Everything runs on your own PC with your own free
accounts. Plan for about 20 minutes the first time.

## 1. What you need

| Thing | Why | Cost |
|---|---|---|
| Windows 10/11 | the scripts are for Windows | — |
| A Spotify account | the playlist you add songs to | free or Premium |
| A Spotify developer app | lets the app read your playlist | free |
| A MongoDB Atlas database | the app's memory (library index, what it learned) | free (M0) |
| A Groq API key | listens to songs nothing else recognises and picks their genre | free |
| Last.fm + AcoustID keys | better genres; checks every download is the right song | free, optional |

Python and Node.js are installed by `setup.bat` if they are missing (it asks first). ffmpeg comes with the
Python packages; you do not install it yourself.

## 2. Get the code

```bash
git clone https://github.com/Aswin-004/spotify-downloader.git
```

(or download the ZIP from GitHub and unzip it). Put it somewhere without spaces if you can, e.g. `C:\Apps`.

## 3. Create your free accounts and keys

Keep a Notepad window open and paste each value as you get it.

**Spotify app**
1. Go to https://developer.spotify.com/dashboard, log in, **Create app**.
2. Any name/description. **Redirect URI:** `http://127.0.0.1:8888/callback` (exactly this). Tick **Web API**. Save.
3. Open the app → **Settings** → copy the **Client ID**, click **View client secret** and copy it.

**The playlist to watch**
1. In Spotify create a playlist of your own, e.g. "to download".
2. Share → **Copy link to playlist**. You can paste the whole link; the app finds the id in it.

**MongoDB Atlas (database)**
1. https://www.mongodb.com/cloud/atlas/register → create a free **M0** cluster (any region near you).
2. **Database Access** → add a user with a password (letters and digits only avoids escaping trouble).
3. **Network Access** → **Add IP Address** → **Allow access from anywhere** (or add your current IP).
4. **Connect** → **Drivers** → copy the `mongodb+srv://...` string and replace `<db_password>` with your password.

**Groq**
1. https://console.groq.com/keys → **Create API Key** → copy it.

**Optional but recommended**
- Last.fm: https://www.last.fm/api/account/create → copy the **API key**.
- AcoustID: https://acoustid.org/new-application → copy the **API key**. Then download *fpcalc* from
  https://acoustid.org/chromaprint (Windows zip) and put `fpcalc.exe` in the project's `backend` folder.

## 4. Run setup

Double-click **`setup.bat`** in the project folder. It:

1. checks Python 3.11+ and Node.js (offers to install them with winget; then run setup.bat once more),
2. installs everything into a private `.venv` folder (a few minutes the first time),
3. builds the web app,
4. creates `backend\.env` and opens it in Notepad, with this guide.

Fill in the **REQUIRED** part of `backend\.env`:

```ini
SPOTIFY_CLIENT_ID=...            # from the Spotify app
SPOTIFY_CLIENT_SECRET=...
INGEST_PLAYLIST_ID=https://open.spotify.com/playlist/...   # your playlist link
BASE_DOWNLOAD_DIR=C:\Users\You\Music\DJ music              # where the music goes
MONGODB_URI=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
GROQ_API_KEY=...
```

Add `LASTFM_API_KEY` / `ACOUSTID_API_KEY` if you made them. Leave everything under "Leave as they are" alone.
**Save** the file, go back to the setup window and press a key.

5. A browser opens for the **Spotify login**: log in and click **Agree**. (You only do this once.)
6. Setup checks every setting and connection and prints `OK` / `WARN` / `FAIL` with what to fix.
   Fix any `FAIL` in `backend\.env` and run `setup.bat` again. It keeps what is already done.

## 5. Every day

- Double-click **`start.bat`** and leave the window open. The app opens at http://localhost:5000.
- Add songs to your playlist in Spotify. Within about 10 minutes they are downloaded and filed in
  `<your music folder>\Library\<Genre>\`. Press **Sync now** in the app to check immediately.
- Optional: `scripts\install-autostart.ps1` starts the app automatically when you log in to Windows
  (see `docs/AUTOMATION.md`).

## 6. How songs are sorted

The app decides in this order and stops at the first answer:

1. The artist is on its lists (a built-in list of DJ/Indian/etc. artists, plus everything it has learned).
2. **Verified evidence** for that exact song: Last.fm, MusicBrainz, iTunes tags, remixer, tempo.
3. **Groq listens**: two 25-second clips go to Groq's Whisper (it hears the language and lyrics), then
   Groq picks one of your genre folders. It must be at least 40 % sure (`AI_MIN_CONFIDENCE`).
4. Otherwise the song waits in `Library\Electronic` (the catch-all). The hourly job lets Groq look at it again.

**It learns from you.** Move a song to another genre folder yourself (Explorer, Rekordbox ...), and the next
check notices: that artist's future songs go to the folder you chose, and nothing ever moves the song back.

Songs that should be sung (Bollywood, Punjabi, Tamil, Indian Hip Hop artists) are checked for vocals: a karaoke
or instrumental upload is rejected and the next YouTube result is tried.

## 7. If something goes wrong

Run the check any time: open a terminal in the `backend` folder and run

```bat
..\.venv\Scripts\python.exe check_setup.py
```

| Problem | Fix |
|---|---|
| `INVALID_CLIENT: Invalid redirect URI` in the browser | The Redirect URI in your Spotify app settings must be exactly `http://127.0.0.1:8888/callback` |
| `cannot read the ingest playlist` | It must be a playlist **you** own (make a copy of someone else's) |
| `MongoDB not reachable` | Check the password in `MONGODB_URI`; Atlas → Network Access → allow your IP |
| Spotify stops answering for hours (429) | Spotify locks out apps that ask too often. Wait it out; don't lower `CHECK_INTERVAL` below 600 |
| Downloads fail with HTTP 403 | YouTube changed something: `..\.venv\Scripts\python.exe -m pip install -U yt-dlp` |
| Log in to Spotify again / switch account | Delete `backend\services\.spotify_oauth_cache`, run `spotify_login.py` in the backend folder |

Your keys live only in `backend\.env` on your PC. That file is never uploaded to git; don't share it.
