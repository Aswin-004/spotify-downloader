"""
Metadata Cache for Spotify API responses.
Caches track metadata and playlist snapshots to reduce API calls by 90%+.
Thread-safe JSON-based persistence.
"""
import json
import os
import time
import threading
import logging

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
_TRACK_CACHE_FILE = os.path.join(_CACHE_DIR, "spotify_cache.json")
_PLAYLIST_CACHE_FILE = os.path.join(_CACHE_DIR, "playlist_snapshots.json")

# Default TTLs
TRACK_TTL = 86400 * 7       # 7 days — track metadata rarely changes
PLAYLIST_SNAPSHOT_TTL = 1800  # 30 minutes — playlist content may change


class MetadataCache:
    """Thread-safe metadata cache backed by JSON files."""

    def __init__(self):
        os.makedirs(_CACHE_DIR, exist_ok=True)
        self._track_lock = threading.Lock()
        self._playlist_lock = threading.Lock()
        self._tracks = self._load_json(_TRACK_CACHE_FILE)
        self._playlists = self._load_json(_PLAYLIST_CACHE_FILE)

    # ── helpers ──

    @staticmethod
    def _load_json(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_json(path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    # ── track cache ──

    def get_track(self, track_id):
        """Return cached track metadata or None if missing/expired."""
        with self._track_lock:
            entry = self._tracks.get(track_id)
            if not entry:
                return None
            if time.time() - entry.get("fetched_at", 0) > TRACK_TTL:
                return None
            return entry.get("data")

    def set_track(self, track_id, metadata):
        """Store track metadata in cache."""
        with self._track_lock:
            self._tracks[track_id] = {
                "data": metadata,
                "fetched_at": time.time(),
            }
            self._save_json(_TRACK_CACHE_FILE, self._tracks)

    # ── playlist snapshot cache ──

    def get_playlist_snapshot(self, playlist_id):
        """Return cached playlist tracks list or None if missing/expired."""
        with self._playlist_lock:
            entry = self._playlists.get(playlist_id)
            if not entry:
                return None
            if time.time() - entry.get("fetched_at", 0) > PLAYLIST_SNAPSHOT_TTL:
                return None
            return entry.get("tracks")

    def set_playlist_snapshot(self, playlist_id, tracks):
        """Store playlist track list in cache."""
        with self._playlist_lock:
            self._playlists[playlist_id] = {
                "tracks": tracks,
                "fetched_at": time.time(),
            }
            self._save_json(_PLAYLIST_CACHE_FILE, self._playlists)

    def is_snapshot_fresh(self, playlist_id, max_age=None):
        """Check if a cached snapshot exists and is younger than max_age seconds."""
        age_limit = max_age or PLAYLIST_SNAPSHOT_TTL
        with self._playlist_lock:
            entry = self._playlists.get(playlist_id)
            if not entry:
                return False
            return (time.time() - entry.get("fetched_at", 0)) < age_limit

    def get_snapshot_age(self, playlist_id):
        """Return the age in seconds of a cached snapshot, or None."""
        with self._playlist_lock:
            entry = self._playlists.get(playlist_id)
            if not entry:
                return None
            return time.time() - entry.get("fetched_at", 0)

    def stats(self):
        """Return cache statistics."""
        with self._track_lock:
            track_count = len(self._tracks)
        with self._playlist_lock:
            playlist_count = len(self._playlists)
        return {
            "cached_tracks": track_count,
            "cached_playlists": playlist_count,
        }


# Singleton
_cache_instance = None
_cache_init_lock = threading.Lock()


def get_cache():
    """Get or create the global MetadataCache singleton."""
    global _cache_instance
    if _cache_instance is None:
        with _cache_init_lock:
            if _cache_instance is None:
                _cache_instance = MetadataCache()
                logger.info(f"Metadata cache initialized: {_cache_instance.stats()}")
    return _cache_instance
