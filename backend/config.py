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
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    
    # Spotify API
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

    # Gemini API
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    
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

    # ── Scanner exclusions ─────────────────────────────────────────────────────
    # Directory names (lower-case, case-insensitive match) the scanner must
    # never enter.  Quarantine is treated as immutable archive storage.
    EXCLUDED_SCAN_DIRS: set = {
        "quarantine",
        "tempfiles",
        "cache",
        "__pycache__",
        ".git",
        "node_modules",
    }

    # Filename regex patterns (case-insensitive) the scanner must skip.
    EXCLUDED_PATTERNS: list = [
        r"\.trimmed\.mp3$",
        r"_\d{1,2}\.mp3$",
        r"\.tmp$",
        r"\.partial$",
    ]

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
        "brostep":              "Dubstep",
        "melodic dubstep":      "Dubstep",
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
        "afro melodic":         "Afro House",
        "melodic techno":       "Afro House",
        # ── House ─────────────────────────────────────────────
        "tech house":           "House",
        "deep house":           "House",
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
        # ── Bollywood ─────────────────────────────────────────
        "bollywood":            "Bollywood",
        "filmi":                "Bollywood",
        "hindi":                "Bollywood",
        "lollywood":            "Bollywood",
        "item number":          "Bollywood",
        "soundtrack":           "Bollywood",
        "hindi film":           "Bollywood",
        "bollywood dance":      "Bollywood",
        "sufi":                 "Bollywood",
        "ghazal":               "Bollywood",
        "classical indian pop": "Bollywood",
        "indian classical":     "Bollywood",
        "hindustani":           "Bollywood",
        "kollywood":            "Tamil",
        "carnatic":             "Tamil",
        "tamil pop":            "Tamil",
        "tamil":                "Tamil",
        "telugu pop":           "Telugu",
        "tollywood":            "Telugu",
        "telugu":               "Telugu",
        "indian singer-songwriter": "Bollywood",
        "mumbai indie":         "Bollywood",
        "desi":                 "Bollywood",
        # ── Indian (non-Bollywood) ────────────────────────────
        "desi hip hop":         "Indian",
        "indian hip hop":       "Indian",
        "indian pop":           "Indian",
        # ── Country / Folk ────────────────────────────────────
        "country pop":          "Country",
        "country":              "Country",
        "indie folk":           "Folk",
        "americana":            "Folk",
        "bluegrass":            "Folk",
        "folk":                 "Folk",
    }

    # ── Artist-name overrides ──────────────────────────────────────────────
    # Direct artist → genre-folder mapping checked BEFORE Spotify genre tags.
    # Keys must be lower-case; values must match an existing Ingest subfolder
    # (or one you want to create).  Add new artists here whenever a fresh
    # download lands in Uncategorized and the Spotify genre tags are absent
    # or too generic to route correctly.
    ARTIST_GENRE_OVERRIDE: dict = {
        # ── Punjabi / Desi ────────────────────────────────────────────────
        "juss":                 "Punjabi",
        "yo yo honey singh":    "Punjabi",
        "imran khan":           "Punjabi",
        "tricksingh":           "Punjabi",
        "sukha":                "Punjabi",
        "dr zeus":              "Punjabi",
        "bir":                  "Punjabi",
        "ap dhillon":           "Punjabi",
        "armaan gill":          "Punjabi",
        "karan aujla":          "Punjabi",
        "frappe ash":           "Punjabi",
        "shally rehal":         "Punjabi",
        "zehr vibe":            "Punjabi",
        "sarrb":                "Punjabi",
        "maanu":                "Punjabi",
        "talwiinder":           "Punjabi",
        "harkirat sangha":      "Punjabi",
        "badshah":              "Punjabi",
        "shubh":                "Punjabi",
        "bohemia":              "Punjabi",
        # ── Bollywood ────────────────────────────────────────────────────
        "anand raj anand":      "Bollywood",
        "shashwat sachdev":     "Bollywood",
        "wajid":                "Bollywood",
        "pritam":               "Bollywood",
        "mehul mahesh":         "Bollywood",
        "arpit bala":           "Bollywood",
        "jeet gannguli":        "Bollywood",
        "sonu nigam":           "Bollywood",
        "himesh reshammiya":    "Bollywood",
        # ── Indian Indie ─────────────────────────────────────────────────
        "aashir wajahat":       "Indian",
        "sheheryar rehan":      "Indian",
        "mitraz":               "Indian",
        "abhinsane":            "Indian",
        "sufr":                 "Indian",
        "karun":                "Indian",
        # ── Hip Hop ──────────────────────────────────────────────────────
        "seedhe maut":          "Hip Hop",
        # ── R&B / Pop ────────────────────────────────────────────────────
        "jay sean":             "R&B",
        # ── Electronic ───────────────────────────────────────────────────
        "kalera":               "Electronic",
        "kaléra":               "Electronic",
        "ian asher":            "Electronic",
        "skrillex":             "Bass",
        "hamdi":                "Dubstep",
        "upsidedown":           "Electronic",
        # ── House ────────────────────────────────────────────────────────
        "goodboys":             "House",
        # ── Dance ────────────────────────────────────────────────────────
        "shamur":               "Dance",
        "casey club":           "Dance",
        # ── Lo-Fi ────────────────────────────────────────────────────────
        "it's murph":           "Lo-Fi",
        "its murph":            "Lo-Fi",
        # ── Latin ────────────────────────────────────────────────────────
        "farruko":              "Latin",
        # ── Pop / R&B ─────────────────────────────────────────────────────
        "the weeknd":           "R&B",
        "weekend":              "R&B",
        "michael jackson":      "Pop",
        # ── Drum and Bass ────────────────────────────────────────────────
        "andy c":               "Drum and Bass",
        "shy fx":               "Drum and Bass",
        "shy_fx":               "Drum and Bass",
        "chase & status":       "Drum and Bass",
        "sub focus":            "Drum and Bass",
        "subfocus":             "Drum and Bass",
        "dimension":            "Drum and Bass",
        "pendulum":             "Drum and Bass",
        "noisia":               "Drum and Bass",
        "goldie":               "Drum and Bass",
        # ── House (global EDM) ───────────────────────────────────────────
        "fred again..":         "House",
        "fred again":           "House",
        "four tet":             "House",
        "solomun":              "House",
        "peggy gou":            "House",
        "boris brejcha":        "House",
        "fisher":               "House",
        "chris lake":           "House",
        "disclosure":           "House",
        "bicep":                "House",
        "calvin harris":        "House",
        "claptone":             "House",
        "dj koze":              "House",
        # ── Electronic (global EDM) ──────────────────────────────────────
        "martin garrix":        "Electronic",
        "hardwell":             "Electronic",
        "illenium":             "Electronic",
        "kshmr":                "Electronic",
        "diplo":                "Electronic",
        "dj snake":             "Electronic",
        "marshmello":           "Electronic",
        "zedd":                 "Electronic",
        "avicii":               "Electronic",
        "deadmau5":             "Electronic",
        "eric prydz":           "Electronic",
        "caribou":              "Electronic",
        # ── UK Garage ────────────────────────────────────────────────────
        "craig david":          "UK Garage",
        "mj cole":              "UK Garage",
        "artful dodger":        "UK Garage",
        # ── Grime ────────────────────────────────────────────────────────
        "skepta":               "Grime",
        "stormzy":              "Grime",
        "aj tracey":            "Grime",
        "ghetts":               "Grime",
        "chip":                 "Grime",
        # ── Indian Bollywood additions ────────────────────────────────────
        "arijit singh":         "Bollywood",
        "shreya ghoshal":       "Bollywood",
        "neha kakkar":          "Bollywood",
        "a.r. rahman":          "Bollywood",
        "ar rahman":            "Bollywood",
        "armaan malik":         "Bollywood",
        "jubin nautiyal":       "Bollywood",
        "darshan raval":        "Bollywood",
        "b praak":              "Bollywood",
        "guru randhawa":        "Bollywood",
        "atif aslam":           "Bollywood",
        "tanishk bagchi":       "Bollywood",
        "amit trivedi":         "Bollywood",
        "mithoon":              "Bollywood",
        "shankar-ehsaan-loy":   "Bollywood",
        # ── Indian Punjabi additions ──────────────────────────────────────
        "sidhu moosewala":      "Punjabi",
        "diljit dosanjh":       "Punjabi",
        "jass manak":           "Punjabi",
        "parmish verma":        "Punjabi",
        "mankirt aulakh":       "Punjabi",
        "hardy sandhu":         "Punjabi",
        "ammy virk":            "Punjabi",
        "gurdas maan":          "Punjabi",
        "jordan sandhu":        "Punjabi",
        "satinder sartaaj":     "Punjabi",
        "r nait":               "Punjabi",
        "tarsem jassar":        "Punjabi",
        # ── Indian Tamil additions ────────────────────────────────────────
        "anirudh ravichander":  "Tamil",
        "santhosh narayanan":   "Tamil",
        "yuvan shankar raja":   "Tamil",
        "d. imman":             "Tamil",
        "harris jayaraj":       "Tamil",
        "gv prakash kumar":     "Tamil",
        "sid sriram":           "Tamil",
        # ── Indian Telugu additions ───────────────────────────────────────
        "devi sri prasad":      "Telugu",
        "thaman s":             "Telugu",
        "mm keeravaani":        "Telugu",
        # ── Indian HipHop additions ───────────────────────────────────────
        "divine":               "Indian Hip Hop",
        "kr$na":                "Indian Hip Hop",
        "emiway bantai":        "Indian Hip Hop",
        "emiway":               "Indian Hip Hop",
        "mc stan":              "Indian Hip Hop",
        "prabh deep":           "Indian Hip Hop",
        "brodha v":             "Indian Hip Hop",
        "nucleya":              "Indian Hip Hop",
        # ── Global Hip Hop additions ──────────────────────────────────────
        "kendrick lamar":       "Hip Hop",
        "drake":                "Hip Hop",
        "j. cole":              "Hip Hop",
        "travis scott":         "Hip Hop",
        "tyler the creator":    "Hip Hop",
        "central cee":          "Hip Hop",
        "dave":                 "Hip Hop",
        # ── House (Latin / Tech House) ────────────────────────────────────
        "hugel":                "House",
        "alok":                 "House",
        "roger sanchez":        "House",
        "matt sassari":         "House",
        "jaden bojsen":         "House",
        "meduza":               "House",
        "twenty six":           "House",
        "raffa fl":             "House",
        # ── Pop ──────────────────────────────────────────────────────────
        "teddy swims":          "Pop",
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
