"""
Is this copy of the app set up correctly? Checks backend/.env and every outside service, and says in plain words
what to fix. setup.bat runs it at the end; run it yourself any time:

    ..\\.venv\\Scripts\\python.exe check_setup.py            (from the backend folder)

Costs: one Spotify token request (+1 playlist request after login), one Groq model list, one MongoDB ping.
Exit code 0 = ready to run start.bat, 1 = something required is missing.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(HERE)                                   # .env is read from this folder

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
problems = 0


def say(status, what, detail=""):
    global problems
    if status == FAIL:
        problems += 1
    print(f"[{status}] {what}" + (f"\n         {detail}" if detail else ""))


def placeholder(value: str) -> bool:
    v = (value or "").strip()
    return not v or v.startswith("your_") or "YourName" in v or "USER:PASSWORD" in v or v.startswith("change_me")


def main() -> int:
    if not (HERE / ".env").exists():
        say(FAIL, "backend\\.env is missing", "Run setup.bat (it creates it), then fill it in.")
        return 1
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)

    print("\nSettings in backend\\.env")
    for key, why in (("SPOTIFY_CLIENT_ID", "Spotify app id"), ("SPOTIFY_CLIENT_SECRET", "Spotify app secret"),
                     ("INGEST_PLAYLIST_ID", "the playlist to watch"), ("BASE_DOWNLOAD_DIR", "your music folder"),
                     ("MONGODB_URI", "the database"), ("GROQ_API_KEY", "Groq key")):
        say(FAIL if placeholder(os.getenv(key, "")) else OK, f"{key} ({why})",
            "Not filled in yet — see docs/SETUP.md." if placeholder(os.getenv(key, "")) else "")
    for key, why in (("LASTFM_API_KEY", "better genres for unknown artists"),
                     ("ACOUSTID_API_KEY", "checks each download is the right song")):
        if placeholder(os.getenv(key, "")):
            say(WARN, f"{key} not set", f"Optional, but recommended: {why}.")
    if os.getenv("FLASK_ENV", "").strip().lower() == "production":
        say(FAIL, "FLASK_ENV=production", "Set FLASK_ENV=development for a copy that runs on your own PC.")

    print("\nMusic folder")
    base = os.getenv("BASE_DOWNLOAD_DIR", "")
    if not placeholder(base):
        try:
            (Path(base) / "Library").mkdir(parents=True, exist_ok=True)
            say(OK, f"{base}\\Library is ready")
        except OSError as exc:
            say(FAIL, f"cannot create {base}\\Library", str(exc))

    print("\nTools")
    try:
        from services.downloader_service import _find_ffmpeg_binary
        ff = _find_ffmpeg_binary()
        say(OK if ff else FAIL, "ffmpeg" + (f" ({ff})" if ff else " not found"),
            "" if ff else "Re-run setup.bat (it installs imageio-ffmpeg, which brings its own ffmpeg).")
    except Exception as exc:
        say(FAIL, "ffmpeg check failed", str(exc))
    from services.fingerprint_service import _FPCALC_BINARY
    import shutil
    has_fp = os.path.isfile(_FPCALC_BINARY) or shutil.which(_FPCALC_BINARY)
    if not has_fp:
        say(WARN, "fpcalc not found", "Optional: download it from https://acoustid.org/chromaprint and put "
                                      "fpcalc.exe in the backend folder (enables the wrong-song check).")

    print("\nOnline services")
    if not placeholder(os.getenv("SPOTIFY_CLIENT_SECRET", "")):
        try:
            from spotipy.oauth2 import SpotifyClientCredentials
            SpotifyClientCredentials(os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")).get_access_token(as_dict=False)
            say(OK, "Spotify app id + secret accepted")
        except Exception as exc:
            say(FAIL, "Spotify rejected the app id/secret", str(exc)[:200])
    if not placeholder(os.getenv("MONGODB_URI", "")):
        try:
            from pymongo import MongoClient
            MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=10000).admin.command("ping")
            say(OK, "MongoDB reachable")
        except Exception as exc:
            say(FAIL, "MongoDB not reachable", f"{str(exc)[:200]}\n         Atlas: check the password in the URI and "
                      "Network Access → allow your IP (or 0.0.0.0/0).")
    if not placeholder(os.getenv("GROQ_API_KEY", "")):
        try:
            from groq import Groq
            models = {m.id for m in Groq(api_key=os.getenv("GROQ_API_KEY")).models.list().data}
            say(OK, "Groq key works")
            for need in (os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"), os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")):
                if need not in models:
                    say(WARN, f"Groq model {need} is not enabled for this key",
                        "Pick one from https://console.groq.com/docs/models and set it in backend\\.env.")
        except Exception as exc:
            say(FAIL, "Groq key rejected", str(exc)[:200])

    print("\nSpotify login (for watching your playlist)")
    token = HERE / "services" / ".spotify_oauth_cache"
    if not token.exists():
        say(WARN, "not logged in yet", "Run:  ..\\.venv\\Scripts\\python.exe spotify_login.py   (once)")
    else:
        try:
            from services.auto_downloader import _get_user_sp
            from services.spotify_service import get_spotify_service
            from config import config
            if _get_user_sp() is None:
                say(FAIL, "the saved Spotify login no longer works", "Run spotify_login.py again.")
            else:
                meta = get_spotify_service().get_playlist_meta(config.INGEST_PLAYLIST_ID)
                say(OK, f"watching \"{meta['name']}\" ({meta['total']} songs)")
        except Exception as exc:
            say(FAIL, "cannot read the ingest playlist", f"{str(exc)[:200]}\n         It must be a playlist you own; "
                      "check INGEST_PLAYLIST_ID.")

    print("\n" + ("Everything required is set up. Run start.bat." if not problems
                  else f"{problems} thing(s) to fix above, then run this check again."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
