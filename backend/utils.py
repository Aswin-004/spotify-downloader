"""
Utility functions for Spotify Meta Downloader
"""
import re
import logging
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


def extract_spotify_id(url):
    """
    Extract Spotify ID and detect type (track or playlist) from Spotify URL
    
    Supports formats:
    - Track: https://open.spotify.com/track/{id}
    - Track: spotify:track:{id}
    - Playlist: https://open.spotify.com/playlist/{id}
    - Playlist: spotify:playlist:{id}
    
    Args:
        url (str): Spotify URL or URI
    
    Returns:
        dict: {
            "type": "track" | "playlist",
            "id": "spotify_id"
        }
    
    Raises:
        ValueError: If URL format is invalid
    """
    if not url or not isinstance(url, str):
        raise ValueError("Invalid Spotify URL: URL must be a non-empty string")
    
    url = url.strip()
    
    # Check for Spotify URI format (spotify:track: or spotify:playlist:)
    if url.startswith("spotify:track:"):
        track_id = url.replace("spotify:track:", "")
        if len(track_id) == 22:
            return {"type": "track", "id": track_id}
        raise ValueError("Invalid Spotify track ID format")
    
    if url.startswith("spotify:playlist:"):
        playlist_id = url.replace("spotify:playlist:", "")
        return {"type": "playlist", "id": playlist_id}
    
    # Check for Spotify URL format
    if "spotify.com/track/" in url:
        try:
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.split("/")
            
            if "track" in path_parts:
                track_idx = path_parts.index("track")
                if track_idx + 1 < len(path_parts):
                    track_id = path_parts[track_idx + 1].split("?")[0]
                    if len(track_id) == 22:
                        return {"type": "track", "id": track_id}
            
            raise ValueError("Could not extract track ID from URL")
        except Exception as e:
            raise ValueError(f"Invalid Spotify track URL format: {str(e)}")
    
    if "spotify.com/album/" in url:
        try:
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.split("/")
            
            if "album" in path_parts:
                album_idx = path_parts.index("album")
                if album_idx + 1 < len(path_parts):
                    album_id = path_parts[album_idx + 1].split("?")[0]
                    if album_id:
                        return {"type": "album", "id": album_id}
            
            raise ValueError("Could not extract album ID from URL")
        except Exception as e:
            raise ValueError(f"Invalid Spotify album URL format: {str(e)}")
    
    if url.startswith("spotify:album:"):
        album_id = url.replace("spotify:album:", "")
        return {"type": "album", "id": album_id}
    
    if "spotify.com/playlist/" in url:
        try:
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.split("/")
            
            if "playlist" in path_parts:
                playlist_idx = path_parts.index("playlist")
                if playlist_idx + 1 < len(path_parts):
                    playlist_id = path_parts[playlist_idx + 1].split("?")[0]
                    if playlist_id:
                        return {"type": "playlist", "id": playlist_id}
            
            raise ValueError("Could not extract playlist ID from URL")
        except Exception as e:
            raise ValueError(f"Invalid Spotify playlist URL format: {str(e)}")
    
    raise ValueError("URL must be a valid Spotify track/playlist/album URL or URI")


def extract_spotify_track_id(spotify_url):
    """
    Extract track ID from Spotify URL (backward compatibility)
    
    Args:
        spotify_url (str): Spotify track URL
    
    Returns:
        str: Track ID
    
    Raises:
        ValueError: If URL format is invalid
    """
    result = extract_spotify_id(spotify_url)
    if result["type"] != "track":
        raise ValueError("Expected a track URL, got playlist")
    return result["id"]


def build_youtube_search_query(title, artist, album=None):
    """
    Build a YouTube search query from track metadata
    
    Args:
        title (str): Track title
        artist (str): Artist name
        album (str, optional): Album name
    
    Returns:
        str: Search query for YouTube
    """
    if not title or not artist:
        raise ValueError("Title and artist are required")
    
    # Sanitize inputs
    title = str(title).strip()
    artist = str(artist).strip()
    
    # Build query: "Title Artist" for better results
    query = f"{title} {artist}"
    
    if album:
        query += f" {str(album).strip()}"
    
    return query


def validate_filename(filename):
    """
    Validate and sanitize filename to prevent directory traversal
    
    Args:
        filename (str): Filename to validate
    
    Returns:
        str: Sanitized filename
    
    Raises:
        ValueError: If filename is invalid
    """
    if not filename or not isinstance(filename, str):
        raise ValueError("Invalid filename")
    
    filename = filename.strip()
    
    # Remove directory traversal attempts
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("Invalid filename: contains path separators")
    
    # Remove special characters
    filename = re.sub(r'[<>:"|?*]', '', filename)
    
    if not filename:
        raise ValueError("Filename is empty after sanitization")
    
    return filename


def setup_logging(name, level=logging.INFO):
    """
    Setup logging for a module
    
    Args:
        name (str): Logger name
        level: Logging level
    
    Returns:
        logging.Logger: Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Create console handler
    handler = logging.StreamHandler()
    handler.setLevel(level)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    
    # Add handler if not already present
    if not logger.handlers:
        logger.addHandler(handler)
    
    return logger
