"""
Configuration settings for Spotify Meta Downloader
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    DEBUG = False
    FLASK_ENV = "production"
    
    # Server settings
    PORT = 5000
    HOST = "0.0.0.0"
    
    # Spotify API
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
    
    # Download settings
    # Custom download path - change this to your preferred location
    DOWNLOAD_PATH = r"C:\Users\Aswin-pc\Desktop\DJ music\all music"
    # Fallback if custom path doesn't exist
    DOWNLOAD_DIR = DOWNLOAD_PATH if os.path.exists(os.path.dirname(DOWNLOAD_PATH)) else os.path.join(os.path.dirname(__file__), "downloads")
    MAX_RETRIES = 3
    REQUEST_TIMEOUT = 30


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
