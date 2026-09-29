# CLAUDE.md — SpotifyDL (spotify-meta-downloader)

Auto-loaded every session. Keep it short; deep knowledge lives in `wiki/` (start at `wiki/README.md`).

## What this is
Windows-first local app for a DJ: watches a Spotify playlist, downloads tracks (yt-dlp via YouTube), tags them (ID3, MusicBrainz, Last.fm, AcoustID), and files them into genre crates on disk. Flask + Socket.IO backend, React/Vite frontend, MongoDB index, optional Celery/Redis.

## Who you're working with
Read `wiki/user.md` first. Short version: Aswin built this entirely through the desktop app and web UI, is new to the CLI agent workflow, wants advanced-level engineering now. Be direct, skip filler, don't add friction.

## Speech-to-text (important)
Aswin often dictates (speech-to-text) and mixes English with Hindi/Hinglish (e.g. "galat folder mein jaake save ho jaate hain"). Expect transcription garbage, wrong words, odd indentation, and stray punctuation in any message, including code pasted by voice. Read for intent, silently correct obvious artifacts, and **if anything is ambiguous or risky, verify with Aswin before acting** — especially anything that touches the music library, git history, or secrets.

## Current priorities (set 2026-09-29, in order)
1. **Stabilize** the app (known bugs below).
2. **Genre accuracy** — songs land in the wrong folder (e.g. Hindi songs ending up in Techno).
3. Test CLI + cleanup (baseline test run, CI running tests, archive one-off scripts).
4. Make it shareable for friends (setup.bat/start.bat/SETUP.md path must be reliable).
5. New features: low priority.

Known user-reported bugs: wrong-folder routing; duplicate songs; half-downloaded/broken files; some downloads not completing; classification logic is too sprawling to keep in one's head (wants it explained and simplified).

## Teaching mode
This is Aswin's first CLI-agent session and goal includes learning to work with AI models and their harnesses. Explain *why* you use a tool/approach in a sentence or two when it's new (plan mode, subagents, hooks, skills, memory, permission modes), and explain systems that were AI-built so they can own them. Keep it concise.

## Layout
- `backend/app.py` — Flask entry (~80 KB, large; blueprints in `backend/routes/`)
- `backend/services/` — all domain logic (downloader, auto_downloader, genre_router, ai_listener, tagger, ...)
- `backend/tests/` — pytest-style tests (no pytest.ini/conftest found)
- `frontend-react/` — Vite + React 18 + Tailwind + Radix + Socket.IO client
- `docs/` — user-facing docs (SETUP, AUTOMATION, PRECISION_TOOLKIT, Phase 4 design)
- `.venv/` at repo root (Python 3.11+; local venv is 3.14). Run backend from `backend/`.

## Commands (verify before relying; not all run yet this session)
- Backend: `cd backend; ..\.venv\Scripts\python.exe app.py` (serves http://localhost:5000, also serves built frontend)
- Frontend dev: `cd frontend-react; npm run dev` (5173) / `npm run build`
- Tests: `cd backend; ..\.venv\Scripts\python.exe -m pytest tests -q`
- Setup check: `..\.venv\Scripts\python.exe check_setup.py`
- Status: `.\scripts\backend-status.ps1`
- CI (`.github/workflows/ci.yml`) only does py_compile + vite build + duplicate-route-name check. It does NOT run tests.

## Hard rules
1. **Never commit or print `backend/.env`** or any key. It is git-ignored.
2. **Spotify rate limits are severe** (~22 h lockout). Never add code that polls Spotify faster or reads whole playlists casually. See `docs/AUTOMATION.md`.
3. **Never run two backend copies** — each would download every new song. Check port 5000 first.
4. **Never move/delete files in the user's music library** without explicit approval. Library tools have dry-run/report modes; use them first. Deletions go through `services/recycle.py`.
5. `SPOTIFY_GENRE_MAP` in `config.py` is order-sensitive. Do not reorder.
6. Don't commit, push, or touch `production` branch (Vercel auto-deploys it) unless asked.
7. yt-dlp is deliberately unpinned. Download 403s → upgrade yt-dlp first, not code.
8. `services/groq_service.py` is a legacy name; it calls Groq, not Gemini.

## Working style
- Match surrounding code; this repo has heavy explanatory comments on *why* — keep that habit for non-obvious changes.
- Prefer small diffs. Add/adjust a test in `backend/tests/` for behavior changes.
- After meaningful work, append to `wiki/log.md`; update the relevant wiki page if a fact changed.
- Working tree is often dirty with the user's in-progress work (see `wiki/log.md`). Never `git checkout`/`reset`/`clean` it.
