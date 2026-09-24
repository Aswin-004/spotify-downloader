# Automatic filing: add a song in Spotify, find it in the right folder

This is the "hands-off" setup. Once it is installed you add songs in Spotify and the app does the rest:
notices the song, downloads the right version, tags it and puts it in its genre folder.

## The three things that make it hands-off

| Piece | What it does | Where |
|---|---|---|
| **Starts by itself** | The backend (which contains the playlist watcher) starts every time you log in to Windows and restarts if it stops | `scripts/install-autostart.ps1` |
| **Gentle watcher** | Asks Spotify one tiny question every 10 minutes ("did the playlist change?"). Songs are read only if it did | `services/playlist_delta.py` |
| **Genre playlists** | A playlist of yours named for a genre files every song added to it in that crate, no guessing | `manage_genre_playlists.py` |

Plus a safety net: a song recorded as downloaded whose file does not exist (checked ~10 minutes later, anywhere in
the library) is downloaded again, twice at most (`services/download_verifier.py`).

## One-time setup (5 minutes)

1. **Start at login.** In PowerShell, from the project folder:

   ```powershell
   .\scripts\install-autostart.ps1
   ```

   Undo it any time with `.\scripts\install-autostart.ps1 -Remove`.

2. **Create your genre playlists in Spotify.** One per genre you care about, named however you like
   ("DJ - House", "DJ - Punjabi" ...). They must be playlists **you own**: Spotify only lets this app read your own
   playlists. To use someone else's playlist, open it, select all songs, and add them to a playlist of your own.

3. **Register each one** (from the `backend` folder, with the project's Python):

   ```powershell
   ..\.venv\Scripts\python.exe manage_genre_playlists.py add House "https://open.spotify.com/playlist/<id>"
   ..\.venv\Scripts\python.exe manage_genre_playlists.py add Punjabi "https://open.spotify.com/playlist/<id>"
   ..\.venv\Scripts\python.exe manage_genre_playlists.py crates    # the crate names you can use
   ..\.venv\Scripts\python.exe manage_genre_playlists.py list
   ..\.venv\Scripts\python.exe manage_genre_playlists.py check     # 1 Spotify request per playlist
   ```

   `add` checks that you own the playlist and refuses (with the reason) if you do not. No restart is needed: the
   watcher re-reads this list on every check.

## Using it every day

* You **know** the genre: add the song to the matching genre playlist. It lands in that folder.
* You **do not know** the genre: add it to your normal ingest playlist. See "Unknown genres" below.
* To force an immediate check, press **Sync now** in the app (it reads every playlist completely).

Songs already in your library are never moved by a playlist; the downloader skips them. To move one, just move it
by hand: the app notices on its next check and learns from it (see "It learns from your moves" below).

## Unknown genres: what happens to a song nobody has heard of

The app decides in this order and stops at the first answer:

1. **A genre playlist** you own says which crate. Never a guess.
2. Your override list, then what the app learned from your earlier moves, then the built-in artist list.
3. **Verified evidence** (new): Last.fm, MusicBrainz and iTunes tags for *this song*, remixer and title hints,
   and tempo. Only an answer backed by a trusted source counts, and it must name a real crate.
   Set `INGEST_EVIDENCE_ROUTING=false` in `backend/.env` to switch this step off.
4. **Groq listens** (`services/ai_listener.py`): two 25-second clips go to Groq Whisper, which hears the sung
   language and lyrics (and whether anyone sings at all); then Groq picks one of your crates from the title, artist,
   language, lyrics and tempo. It must be at least `AI_MIN_CONFIDENCE` sure (default 0.4) and name a real crate.
   An Indian crate is not accepted for a song sung in another language unless its title is in an Indian script.
5. **The Electronic catch-all.** The song is saved, tagged `routing_source=catchall`, and re-checked by the hourly
   job. If nothing knows the song it stays there: that is the honest answer, not a wrong folder.

**Hip hop has two crates: Indian Hip Hop and International Hip Hop.** The tags for both are the same words, so
the crate is decided by where the artist is from: a curated entry, Indian tags (desi, hindi, punjabi, dhh ...) or
Indian script mean Indian Hip Hop; otherwise International. Punjabi-language rappers sit on a taste boundary with the
Punjabi crate, so the tools never move them between Punjabi and Indian Hip Hop on their own. A genre playlist you
own always wins ("DJ - Indian Hip Hop" files everything in it in that crate).

Every song filed by step 1 or 3 carries a `routing_source` tag (`genre_playlist:House`, `evidence:lastfm_track+itunes`)
so you can always see *why* a song is where it is.

**It learns from your moves.** Every song the app files carries its crate in a tag (`filed_crate`). On every
check (~10 minutes) the watcher looks for songs sitting in a different crate folder than that tag says: you moved
them (Explorer, Rekordbox, anything). For each one it remembers the artist → crate (the next song by them files
itself), syncs the genre tag, and marks the song hand-placed so no tool ever moves it back. Placeholder artists
("Unknown", "Indian" ...) are never learned. Log: `backend/reports/hand_moves.jsonl`. `HAND_MOVE_LEARNING=false`
switches it off. (The first check after installing only records where every song is now; nothing is learned from it.)

**Karaoke and instrumental uploads.** When the artist is known to belong in Bollywood, Punjabi, Tamil or Indian Hip
Hop, each download is also played to Whisper: no lyrics and nothing that even sounds South Asian in three clips means
a karaoke/instrumental upload, which is thrown away and the next YouTube result tried (twice at most).

## Is it working?

```powershell
.\scripts\backend-status.ps1
```

shows whether the backend is running, whether it starts at login, when the playlist was last checked, how many songs
are waiting to be re-downloaded, and how many new songs arrived in the last day. It makes no Spotify request.

## Why the watcher is gentle (and how gentle)

Spotify locks the whole app out for about 22 hours when it is asked too much, and while locked nothing downloads.
The old watcher re-read the entire 2,070-song playlist on every check: 21 requests each, with a 60-second interval
about 1,260 requests an hour. Now:

| Situation | Requests |
|---|---|
| Nothing changed | 1 per check |
| A few songs added | 1 + 2 |
| Full re-read (first start, a big batch, and once every 24 h as a safety net) | 21 for a 2,070-song playlist |

`CHECK_INTERVAL` (seconds) is honoured but never below 600, so at most 144 checks a day.
A check's result is only remembered after every song from it was handled, so a crash cannot lose a new song.

## Limits worth knowing

* It only works while your PC is on and you are logged in: the files are on this PC, and YouTube blocks downloads from
  cloud servers.
* A playlist you do not own cannot be read (Spotify answers 403). `manage_genre_playlists.py add` tells you.
* A song that fails to download is retried on the next check that includes it (three failures and it is skipped);
  songs far from either end of a big playlist are retried by the 24-hour full read or by **Sync now**.
