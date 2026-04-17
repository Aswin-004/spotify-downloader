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

    # Genre routing: maps Spotify artist genre tags (lower-case substring keys)
    # to the destination parent folder under the ingest base. The dict is
    # intentionally ordered — longer/more specific keys appear before shorter
    # ones within each group (e.g. "tech house" before "house", "death metal"
    # before "metal"). Do not reorder.
    SPOTIFY_GENRE_MAP = {
        # ── UK Electronic ─────────────────────────────────────
        "uk garage":            "UK Garage",
        "bassline":             "UK Garage",
        "speed garage":         "UK Garage",
        "uk funky":             "UK Garage",
        "2-step":               "UK Garage",
        "new uk garage":        "UK Garage",
        "uk bass":              "UK Bass",
        "dubstep":              "UK Bass",
        "liquid funk":          "Drum and Bass",
        "drum and bass":        "Drum and Bass",
        "drumstep":             "Drum and Bass",
        "neurofunk":            "Drum and Bass",
        "jungle":               "Drum and Bass",
        "dnb":                  "Drum and Bass",
        "grime":                "Grime",
        "uk hip hop":           "Grime",
        "road rap":             "Grime",
        "uk drill":             "Grime",
        # ── Afro House ────────────────────────────────────────────────
        "melodic house techno": "Afro House",
        "afro house":           "Afro House",
        "afro tech":            "Afro House",
        "afrotech":             "Afro House",
        "organic house":        "Afro House",
        "tribal house":         "Afro House",
        "south african house":  "Afro House",
        "melodic techno":       "Afro House",
        # ── House ─────────────────────────────────────────────
        "tech house":           "House",
        "deep house":           "House",
        "afro house":           "House",
        "melodic house":        "House",
        "progressive house":    "House",
        "electro house":        "House",
        "tropical house":       "House",
        "future house":         "House",
        "bass house":           "House",
        "chicago house":        "House",
        "vocal house":          "House",
        "house":                "House",
        # ── Techno / Trance ───────────────────────────────────
        "techno":               "Techno",
        "industrial techno":    "Techno",
        "minimal techno":       "Techno",
        "trance":               "Trance",
        "psytrance":            "Trance",
        "progressive trance":   "Trance",
        "uplifting trance":     "Trance",
        "goa trance":           "Trance",
        "full on":              "Trance",
        "darkpsy":              "Trance",
        "hitech":               "Trance",
        "forest":               "Trance",
        # ── Electronic / EDM ──────────────────────────────────
        "edm":                  "Electronic",
        "electronic":           "Electronic",
        "electropop":           "Electronic",
        "electronica":          "Electronic",
        "synth-pop":            "Electronic",
        "synthwave":            "Electronic",
        "chillwave":            "Electronic",
        "future bass":          "Electronic",
        "complextro":           "Electronic",
        "big room":             "Electronic",
        "hardstyle":            "Electronic",
        "hardcore":             "Electronic",
        # ── Ambient / Lo-Fi ───────────────────────────────────
        "ambient":              "Ambient",
        "lo-fi":                "Lo-Fi",
        "lofi":                 "Lo-Fi",
        "chillhop":             "Lo-Fi",
        "study music":          "Lo-Fi",
        # ── Dance / Disco ─────────────────────────────────────
        "dance pop":            "Dance",
        "disco":                "Dance",
        "nu-disco":             "Dance",
        "funk":                 "Dance",
        "dance":                "Dance",
        # ── Hip Hop / Rap ─────────────────────────────────────
        "hip hop":              "Hip Hop",
        "rap":                  "Hip Hop",
        "trap":                 "Hip Hop",
        "drill":                "Hip Hop",
        "boom bap":             "Hip Hop",
        "conscious hip hop":    "Hip Hop",
        "cloud rap":            "Hip Hop",
        "emo rap":              "Hip Hop",
        "phonk":                "Hip Hop",
        "gangsta rap":          "Hip Hop",
        # ── R&B / Soul ────────────────────────────────────────
        "r&b":                  "R&B",
        "soul":                 "R&B",
        "neo soul":             "R&B",
        "contemporary r&b":     "R&B",
        "quiet storm":          "R&B",
        "new jack swing":       "R&B",
        # ── Pop ───────────────────────────────────────────────
        "k-pop":                "K-Pop",
        "j-pop":                "J-Pop",
        "mandopop":             "Asian Pop",
        "cantopop":             "Asian Pop",
        "indie pop":            "Pop",
        "art pop":              "Pop",
        "dream pop":            "Pop",
        "power pop":            "Pop",
        "pop":                  "Pop",
        # ── Rock / Metal ──────────────────────────────────────
        "metalcore":            "Metal",
        "heavy metal":          "Metal",
        "death metal":          "Metal",
        "black metal":          "Metal",
        "metal":                "Metal",
        "alternative rock":     "Rock",
        "classic rock":         "Rock",
        "indie rock":           "Rock",
        "post-punk":            "Rock",
        "grunge":               "Rock",
        "shoegaze":             "Rock",
        "punk":                 "Rock",
        "rock":                 "Rock",
        # ── Jazz / Blues ──────────────────────────────────────
        "smooth jazz":          "Jazz",
        "bebop":                "Jazz",
        "jazz":                 "Jazz",
        "soul blues":           "Blues",
        "blues":                "Blues",
        # ── Classical ─────────────────────────────────────────
        "chamber music":        "Classical",
        "orchestral":           "Classical",
        "classical":            "Classical",
        "baroque":              "Classical",
        "opera":                "Classical",
        # ── Reggae / Afrobeats ────────────────────────────────
        "dancehall":            "Reggae",
        "reggae":               "Reggae",
        "dub":                  "Reggae",
        "amapiano":             "Afrobeats",
        "afroswing":            "Afrobeats",
        "afrobeats":            "Afrobeats",
        "afropop":              "Afrobeats",
        "afro pop":             "Afrobeats",
        "highlife":             "Afrobeats",
        # ── Latin ─────────────────────────────────────────────
        "urbano latino":        "Latin",
        "latin trap":           "Latin",
        "latin hip hop":        "Latin",
        "reggaeton":            "Latin",
        "latin pop":            "Latin",
        "bachata":              "Latin",
        "cumbia":               "Latin",
        "salsa":                "Latin",
        # ── Indian / South Asian ──────────────────────────────
        "punjabi hip hop":      "Punjabi",
        "desi pop":             "Punjabi",
        "bhangra":              "Punjabi",
        "haryanvi":             "Punjabi",
        "punjabi":              "Punjabi",
        "desi hip hop":         "Indian",
        "indian hip hop":       "Indian",
        "indian pop":           "Indian",
        "tollywood":            "Indian",
        "kollywood":            "Indian",
        "carnatic":             "Indian",
        "hindustani":           "Indian",
        "desi":                 "Indian",
        "bollywood":            "Bollywood",
        "filmi":                "Bollywood",
        "hindi":                "Bollywood",
        "indian pop":        "Bollywood",       
        "desi":              "Bollywood",
        "filmi":             "Bollywood",
        "lollywood":         "Bollywood",
        "item number":       "Bollywood",
        "soundtrack":        "Bollywood",
        # ── Bollywood expanded ─────────────────────────────────────────────
        "filmi":                    "Bollywood",
        "indian pop":               "Bollywood",
        "hindi film":               "Bollywood",
        "bollywood dance":          "Bollywood",
        "desi":                     "Bollywood",
        "item number":              "Bollywood",
        "sufi":                     "Bollywood",
        "ghazal":                   "Bollywood",
        "classical indian pop":     "Bollywood",
        "indian classical":         "Bollywood",
        "hindustani":               "Bollywood",
        "tollywood":                "Bollywood",
        "kollywood":                "Bollywood",
        "carnatic":                 "Bollywood",
        "tamil pop":                "Bollywood",
        "telugu pop":               "Bollywood",
        "indian singer-songwriter": "Bollywood",
        "mumbai indie":             "Bollywood",
        "desi hip hop":             "Indian",
        # ── Country / Folk ────────────────────────────────────
        "country pop":          "Country",
        "country":              "Country",
        "indie folk":           "Folk",
        "americana":            "Folk",
        "bluegrass":            "Folk",
        "folk":                 "Folk",
        # ── Afro House / Afro Tech ─────────────────────────────
        "afro tech":            "Afro House",
        "afrotech":             "Afro House",
        "afro house":           "Afro House",
        "organic house":        "Afro House",
        "melodic house techno": "Afro House",
        "afro melodic":         "Afro House",
        "south african house":  "Afro House",
        "amapiano":             "Afro House",
        "tribal house":         "Afro House",    
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
