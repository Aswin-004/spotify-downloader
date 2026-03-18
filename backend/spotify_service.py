"""
Spotify API Service
Handles authentication and metadata retrieval from Spotify
"""
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import logging
from config import config
from utils import extract_spotify_id, extract_spotify_track_id, setup_logging

logger = setup_logging(__name__)


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
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            logger.info("Successfully authenticated with Spotify API")
        except Exception as e:
            logger.error(f"Failed to authenticate with Spotify: {str(e)}")
            raise
    
    def get_track_metadata(self, spotify_url):
        """
        Fetch track metadata from Spotify
        
        Args:
            spotify_url (str): Spotify track URL or URI
        
        Returns:
            dict: Track metadata containing title, artist, album, and more
        
        Raises:
            ValueError: If URL is invalid or track not found
        """
        try:
            # Extract track ID from URL
            track_id = extract_spotify_track_id(spotify_url)
            logger.info(f"Extracted track ID: {track_id}")
            
            # Fetch track details
            track = self.sp.track(track_id)
            
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
            }
            
            logger.info(f"Successfully fetched metadata for: {metadata['title']} by {metadata['artist']}")
            return metadata
        
        except ValueError as e:
            logger.error(f"Invalid Spotify URL: {str(e)}")
            raise
        except spotipy.exceptions.SpotifyException as e:
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
            
            album = self.sp.album(album_id)
            
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
            logger.error(f"Spotify API error: {str(e)}")
            raise ValueError(f"Album not found or Spotify API error: {str(e)}")
        except Exception as e:
            logger.error(f"Error fetching album: {str(e)}")
            raise ValueError(f"Error fetching album: {str(e)}")
    
    def get_playlist_tracks(self, spotify_url):
        """
        Fetch all tracks from a Spotify playlist
        
        Args:
            spotify_url (str): Spotify playlist URL
        
        Returns:
            list: List of track metadata dicts with structure:
                [{
                    "id": "track_id",
                    "title": "Track Name",
                    "artist": "Artist Name",
                    "album": "Album Name",
                    "duration_ms": 180000
                }, ...]
        
        Raises:
            ValueError: If URL is invalid or playlist not found
        """
        try:
            # Extract playlist ID from URL
            result = extract_spotify_id(spotify_url)
            
            if result["type"] != "playlist":
                raise ValueError("URL is not a valid Spotify playlist URL")
            
            playlist_id = result["id"]
            logger.info(f"Fetching playlist: {playlist_id}")
            
            # Fetch playlist tracks
            results = self.sp.playlist_tracks(playlist_id, limit=50)
            tracks = results["items"]
            
            # Handle pagination - get up to 200 tracks
            offset = 50
            while results["next"] and offset < 200:
                results = self.sp.playlist_tracks(playlist_id, offset=offset, limit=50)
                tracks.extend(results["items"])
                offset += 50
            
            # Extract metadata for each track
            metadata_list = []
            for item in tracks:
                track = item["track"]
                if track:  # Skip local tracks and None entries
                    metadata = {
                        "id": track["id"],
                        "title": track["name"],
                        "artist": track["artists"][0]["name"] if track["artists"] else "Unknown",
                        "album": track["album"]["name"] if track.get("album") else "Unknown",
                        "duration_ms": track.get("duration_ms"),
                    }
                    metadata_list.append(metadata)
            
            logger.info(f"Successfully fetched {len(metadata_list)} tracks from playlist")
            return metadata_list
        
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error fetching playlist: {str(e)}")
            raise ValueError(f"Error fetching playlist: {str(e)}")


# Create global instance
spotify_service = None


def get_spotify_service():
    """Get or create Spotify service instance"""
    global spotify_service
    if spotify_service is None:
        spotify_service = SpotifyService()
    return spotify_service
