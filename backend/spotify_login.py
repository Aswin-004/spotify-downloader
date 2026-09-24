"""
One-time Spotify login for the playlist watcher.

Run it once after filling in backend/.env (setup.bat tells you when):

    ..\.venv\Scripts\python.exe spotify_login.py

A browser opens; log in to Spotify and allow access. The token is saved to backend/services/.spotify_oauth_cache and
refreshed automatically from then on, so you only do this again if you delete that file or change accounts.
The Redirect URI in your Spotify app's dashboard must be exactly the REDIRECT_URI in backend/.env
(default http://127.0.0.1:8888/callback).
"""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))            # .env is read from this folder

if __name__ == "__main__":
    runpy.run_module("services.auto_downloader", run_name="__main__")
