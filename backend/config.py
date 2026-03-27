"""
Configuration settings for Spotify Meta Downloader
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    DEBUG = False
    FLASK_ENV = os.getenv("FLASK_ENV", "production")
    
    # Server settings
    PORT = 5000
    HOST = "0.0.0.0"
    SECRET_KEY = os.getenv("SECRET_KEY", "spotify-downloader-secret")
    
    # Spotify API
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
    
    # Playlist configuration (single ingest playlist)
    INGEST_PLAYLIST_ID = os.getenv("INGEST_PLAYLIST_ID", "")
    
    # OAuth
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://127.0.0.1:8888/callback")
    
    # Auto-sync interval in seconds
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "500"))
    
    # Download settings
    BASE_DOWNLOAD_DIR = os.getenv("BASE_DOWNLOAD_DIR", os.path.join(os.path.dirname(__file__), "downloads"))
    DOWNLOAD_PATH = os.path.join(BASE_DOWNLOAD_DIR, "Manual")
    DOWNLOAD_DIR = DOWNLOAD_PATH if os.path.exists(os.path.dirname(BASE_DOWNLOAD_DIR)) else os.path.join(os.path.dirname(__file__), "downloads")
    MAX_RETRIES = 3
    REQUEST_TIMEOUT = 30

    # Smart folder routing: maps artist name patterns to subfolders under Auto Downloads
    FOLDER_RULES = {
        "sammy virji": "Sammy Virji",
        "interplanetary criminal": "IPC",
        "fred again": "Fred Again",
        "skrillex": "Skrillex",
    }


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    FLASK_ENV = "development"


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    FLASK_ENV = "production"


# Get config based on environment
config = DevelopmentConfig() if os.getenv("FLASK_ENV") == "development" else ProductionConfig()
