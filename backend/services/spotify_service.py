"""
Spotify API Service
Handles authentication and metadata retrieval from Spotify
With smart rate-limit backoff and API usage tracking
"""
import io
import os
import re
import sys
import time
import threading
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
import logging
from config import config
from utils import extract_spotify_id, extract_spotify_track_id, setup_logging
from services.metadata_cache import get_cache

# Throttle: minimum seconds between consecutive Spotify API calls
API_THROTTLE = 0.35
_last_call_time = 0
_throttle_lock = threading.Lock()

logger = setup_logging(__name__)

# OAuth cache path (shared with auto_downloader)
_OAUTH_CACHE = os.path.join(os.path.dirname(__file__), ".spotify_cache")
_REDIRECT_URI = config.REDIRECT_URI

# API usage tracking (shared)
api_usage = {
    "calls": 0,
    "last_reset": time.time(),
    "rate_limited_until": 0,
}
_usage_lock = threading.Lock()


class SpotifyService:
    """Service for interacting with Spotify API"""
    
    def __init__(self):
        """Initialize Spotify service with credentials"""
        if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
            raise ValueError(
                "Spotify credentials not found. "
                "Please set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables"
            )
        
        self.client_id = config.SPOTIFY_CLIENT_ID
        self.client_secret = config.SPOTIFY_CLIENT_SECRET
        self.sp = None
        self._authenticate()
    
    def _authenticate(self):
        """
        Authenticate with Spotify using Client Credentials Flow
        """
        try:
            auth_manager = SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            self.sp = spotipy.Spotify(
                auth_manager=auth_manager,
                retries=0,
                requests_timeout=10
            )
            logger.info("Successfully authenticated with Spotify API")
        except Exception as e:
            logger.error(f"Failed to authenticate with Spotify: {str(e)}")
            raise
    
    @staticmethod
    def _extract_retry_seconds(*sources):
        """Extract retry duration from error message or captured stderr.
        Checks all provided sources for 'Retry will occur after: N s'."""
        for src in sources:
            msg = str(src)
            m = re.search(r'Retry will occur after:\s*(\d+)', msg)
            if m:
                return int(m.group(1))
            m = re.search(r'Retry-After[:\s]+(\d+)', msg, re.IGNORECASE)
            if m:
                return int(m.group(1))
        return None

    def _call_with_backoff(self, fn, *args, **kwargs):
        """
        Call a Spotify API function with rate-limit protection.
        On 429, captures stderr to extract the real Retry-After duration
        (spotipy/urllib3 prints it to stderr but doesn't include it in the exception).
        """
        # Block immediately if already in cooldown
        with _usage_lock:
            if time.time() < api_usage["rate_limited_until"]:
                wait_left = int(api_usage["rate_limited_until"] - time.time())
                raise ValueError(f"Spotify API cooling down. Retry in {wait_left}s.")
            api_usage["calls"] += 1

        # Request throttle — enforce minimum gap between API calls
        global _last_call_time
        with _throttle_lock:
            elapsed = time.time() - _last_call_time
            if elapsed < API_THROTTLE:
                time.sleep(API_THROTTLE - elapsed)
            _last_call_time = time.time()

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            result = fn(*args, **kwargs)
            return result
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                stderr_output = captured.getvalue()
                retry_secs = self._extract_retry_seconds(e, stderr_output) or 600
                with _usage_lock:
                    api_usage["rate_limited_until"] = time.time() + retry_secs
                logger.error(f"Spotify 429 — blocked for {retry_secs}s. Next retry after cooldown.")
                raise ValueError(f"Spotify rate limited. Blocked for {retry_secs}s.")
            raise
        finally:
            sys.stderr = old_stderr
            stderr_text = captured.getvalue()
            if stderr_text:
                sys.stderr.write(stderr_text)
    
    def _get_user_sp(self):
        """Get a user-authenticated Spotify client for playlist access.
        Uses the cached OAuth token from auto_downloader's auth flow.
        Returns None if no cached token exists.
        """
        if not os.path.exists(_OAUTH_CACHE):
            return None
        try:
            auth = SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=_REDIRECT_URI,
                scope="playlist-read-private playlist-read-collaborative",
                cache_path=_OAUTH_CACHE,
                open_browser=False,
            )
            token_info = auth.get_cached_token()
            if not token_info:
                return None
            return spotipy.Spotify(
                auth_manager=auth,
                retries=0,
                requests_timeout=10
            )
        except Exception as e:
            logger.warning(f"User OAuth failed: {e}")
            return None
    
    def get_track_metadata(self, spotify_url):
        """
        Fetch track metadata from Spotify (cache-first).
        Returns metadata dict plus a 'source' key ('cache' or 'spotify').
        """
        try:
            # Extract track ID from URL
            track_id = extract_spotify_track_id(spotify_url)
            logger.info(f"Extracted track ID: {track_id}")

            # Cache-first lookup
            cache = get_cache()
            cached = cache.get_track(track_id)
            if cached:
                logger.info(f"Cache HIT for track {track_id}: {cached.get('title')}")
                cached["source"] = "cache"
                return cached

            # Rate-limited? Return cache even if expired, or raise
            if is_rate_limited():
                raise ValueError("Spotify API cooling down and track not in cache.")

            # Fetch track details with backoff
            track = self._call_with_backoff(self.sp.track, track_id)
            
            # Extract relevant metadata
            metadata = {
                "id": track["id"],
                "title": track["name"],
                "artist": track["artists"][0]["name"] if track["artists"] else "Unknown Artist",
                "album": track["album"]["name"] if track.get("album") else "Unknown Album",
                "duration_ms": track.get("duration_ms"),
                "release_date": track.get("release_date"),
                "external_url": track.get("external_urls", {}).get("spotify"),
                "artists": [artist["name"] for artist in track.get("artists", [])],
                # CHANGED: highest-res album art (images[0] = 640x640)
                "album_art_url": (track.get("album", {}).get("images") or [{}])[0].get("url"),
            }

            # Persist to cache
            cache.set_track(track_id, metadata)
            metadata["source"] = "spotify"
            
            logger.info(f"Successfully fetched metadata for: {metadata['title']} by {metadata['artist']}")
            return metadata
        
        except ValueError as e:
            logger.error(f"Invalid Spotify URL: {str(e)}")
            raise
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                logger.error(f"Spotify rate limit hit: {str(e)}")
                raise ValueError("Spotify rate limit reached. Please wait a few minutes and try again.")
            logger.error(f"Spotify API error: {str(e)}")
            raise ValueError(f"Track not found or Spotify API error: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error fetching track metadata: {str(e)}")
            raise ValueError(f"Error fetching track: {str(e)}")
    
    def get_album_tracks(self, spotify_url):
        """
        Fetch all tracks from a Spotify album
        
        Args:
            spotify_url (str): Spotify album URL
        
        Returns:
            dict: Album metadata with tracks list
        
        Raises:
            ValueError: If URL is invalid or album not found
        """
        try:
            result = extract_spotify_id(spotify_url)
            
            if result["type"] != "album":
                raise ValueError("URL is not a valid Spotify album URL")
            
            album_id = result["id"]
            logger.info(f"Fetching album: {album_id}")
            
            album = self._call_with_backoff(self.sp.album, album_id)
            
            tracks = []
            for i, item in enumerate(album['tracks']['items']):
                if item:
                    tracks.append({
                        "title": item['name'],
                        "artist": item['artists'][0]['name'] if item.get('artists') else 'Unknown',
                        "duration_ms": item.get('duration_ms'),
                        "track_number": i + 1,
                    })
            
            logger.info(f"Successfully fetched {len(tracks)} tracks from album '{album['name']}'")
            
            return {
                "type": "album",
                "name": album['name'],
                "artist": album['artists'][0]['name'] if album.get('artists') else 'Unknown',
                "total_tracks": len(tracks),
                "release_date": album.get('release_date'),
                "tracks": tracks,
            }
        
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            raise
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                logger.error(f"Spotify rate limit hit: {str(e)}")
                raise ValueError("Spotify rate limit reached. Please wait a few minutes and try again.")
            logger.error(f"Spotify API error: {str(e)}")
            raise ValueError(f"Album not found or Spotify API error: {str(e)}")
        except Exception as e:
            logger.error(f"Error fetching album: {str(e)}")
            raise ValueError(f"Error fetching album: {str(e)}")
    
    def get_playlist_tracks(self, spotify_url):
        """
        Fetch all tracks from a Spotify playlist.
        Uses user OAuth token (required for playlist access).
        """
        try:
            result = extract_spotify_id(spotify_url)
            
            if result["type"] != "playlist":
                raise ValueError("URL is not a valid Spotify playlist URL")
            
            playlist_id = result["id"]
            logger.info(f"Fetching playlist: {playlist_id}")
            
            # Playlists require user OAuth
            user_sp = self._get_user_sp()
            if not user_sp:
                raise ValueError(
                    "User authentication required for playlist access. "
                    "Run 'python auto_downloader.py' to authorize first."
                )
            
            # Debug: log authenticated user
            try:
                me = user_sp.current_user()
                logger.info(f"Playlist access as user: {me['display_name']} ({me['id']})")
            except Exception:
                pass

            # Fetch tracks with explicit 403 handling
            try:
                results = user_sp.playlist_items(
                    playlist_id, limit=100, additional_types=["track"]
                )
            except spotipy.exceptions.SpotifyException as e:
                if e.http_status == 403:
                    raise ValueError(
                        "403 Forbidden: Playlist is private or access denied. "
                        "Make it public or login with the correct Spotify account."
                    )
                raise
            
            metadata_list = []
            while results:
                for item in results["items"]:
                    if not item:
                        continue
                    track = item.get("item") or item.get("track")
                    if track and track.get("id") and track.get("type") == "track":
                        metadata_list.append({
                            "id": track["id"],
                            "title": track["name"],
                            "artist": track["artists"][0]["name"] if track.get("artists") else "Unknown",
                            "album": track["album"]["name"] if track.get("album") else "Unknown",
                            "duration_ms": track.get("duration_ms"),
                        })
                if results.get("next"):
                    results = user_sp.next(results)
                else:
                    break
            
            logger.info(f"Playlist fetched successfully: {len(metadata_list)} track(s)")
            return metadata_list
        
        except ValueError:
            raise
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 403:
                logger.error(f"403 Forbidden on playlist {playlist_id}")
                raise ValueError(
                    "403 Forbidden: Playlist is private or access denied. "
                    "Make it public or login with the correct Spotify account."
                )
            logger.error(f"Spotify API error: {str(e)}")
            raise ValueError(f"Spotify API error: {str(e)}")
        except Exception as e:
            logger.error(f"Error fetching playlist: {str(e)}")
            raise ValueError(f"Error fetching playlist: {str(e)}")

    def get_playlist_tracks_by_id(self, playlist_id, force_refresh=False):
        """Fetch tracks from a playlist by ID (cache-first).
        Uses user OAuth (required since 2025), falls back to client credentials.
        Returns list of track dicts with id, title, artist, duration_ms.
        Also returns a 'source' field on each track when served from cache."""
        cache = get_cache()
        # Normalize playlist_id (may be a full URL)
        clean_id = playlist_id.split("/")[-1].split("?")[0] if "/" in str(playlist_id) else str(playlist_id)

        if not force_refresh:
            cached = cache.get_playlist_snapshot(clean_id)
            if cached:
                logger.info(f"Cache HIT for playlist {clean_id}: {len(cached)} tracks")
                return cached

        # Rate-limited? Serve stale cache if available
        if is_rate_limited():
            stale = cache.get_playlist_snapshot(clean_id)
            if stale:
                logger.info(f"Rate-limited — serving stale cache for {clean_id}")
                return stale
            raise ValueError("Spotify API cooling down and playlist not in cache.")

        try:
            sp_client = self._get_user_sp() or self.sp
            results = self._call_with_backoff(
                sp_client.playlist_items, playlist_id,
                limit=100, additional_types=["track"]
            )
            tracks = []
            while results:
                for item in results["items"]:
                    if not item:
                        continue
                    track = item.get("item") or item.get("track")
                    if track and track.get("id") and track.get("type") == "track":
                        tracks.append({
                            "id": track["id"],
                            "title": track["name"],
                            "artist": track["artists"][0]["name"] if track.get("artists") else "Unknown",
                            "duration_ms": track.get("duration_ms"),
                        })
                if results.get("next"):
                    results = self._call_with_backoff(sp_client.next, results)
                else:
                    break
            logger.info(f"Playlist fetched from Spotify: {len(tracks)} track(s)")
            cache.set_playlist_snapshot(clean_id, tracks)
            return tracks
        except Exception as e:
            logger.error(f"Error fetching playlist {playlist_id}: {e}")
            raise


def set_global_rate_limit(seconds):
    """Set the global rate-limit cooldown from anywhere (e.g. auto_downloader)."""
    with _usage_lock:
        new_until = time.time() + seconds
        if new_until > api_usage["rate_limited_until"]:
            api_usage["rate_limited_until"] = new_until
            logger.warning(f"Global rate limit set for {seconds}s")


def is_rate_limited():
    """Check if the system is currently rate-limited."""
    with _usage_lock:
        return time.time() < api_usage["rate_limited_until"]


def get_api_usage():
    """Return current API usage stats including cache stats."""
    cache = get_cache()
    with _usage_lock:
        return {
            "calls": api_usage["calls"],
            "last_reset": api_usage["last_reset"],
            "rate_limited_until": api_usage["rate_limited_until"],
            "is_rate_limited": time.time() < api_usage["rate_limited_until"],
            "cooldown_remaining": max(0, int(api_usage["rate_limited_until"] - time.time())),
            "cache": cache.stats(),
        }


# Create global instance
spotify_service = None


def get_spotify_service():
    """Get or create Spotify service instance"""
    global spotify_service
    if spotify_service is None:
        spotify_service = SpotifyService()
    return spotify_service
