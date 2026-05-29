"""
Configuration settings for Spotify Meta Downloader
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _parse_origins(raw: str) -> list[str]:
    """Split a comma-separated ALLOWED_ORIGINS string into a list."""
    return [o.strip() for o in raw.split(",") if o.strip()]


_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]


class Config:
    """Base configuration"""
    DEBUG = False
    FLASK_ENV = os.getenv("FLASK_ENV", "production")

    # Server settings
    PORT = int(os.getenv("PORT", "5000"))
    HOST = "0.0.0.0"
    SECRET_KEY = os.getenv("SECRET_KEY", "")

    # Spotify API
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

    # Gemini API (legacy — kept so old references don't break)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    # Groq API (replaces Gemini for genre classification — free tier, 14400 calls/day)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

    # Last.fm API (free — register at last.fm/api)
    LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")

    # Playlist configuration (single ingest playlist)
    INGEST_PLAYLIST_ID = os.getenv("INGEST_PLAYLIST_ID", "")

    # OAuth — REDIRECT_URI must be updated in your Spotify Developer Dashboard
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://127.0.0.1:8888/callback")

    # CORS — comma-separated list of allowed origins for API + SocketIO
    # Set ALLOWED_ORIGINS=https://your-app.vercel.app in production
    ALLOWED_ORIGINS: list[str] = _parse_origins(
        os.getenv("ALLOWED_ORIGINS", ",".join(_DEV_ORIGINS))
    )

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
        "drum & bass":          "Drum and Bass",
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
        "hip-hop":              "Hip Hop",   # Last.fm uses hyphenated form
        "west coast":           "Hip Hop",
        "east coast":           "Hip Hop",
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
        "rnb":                  "R&B",
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
        "skrillex":             "Dubstep",
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
        # Legacy / classic Bollywood (resolved from NeedsReview 2026-05-22)
        "udit narayan":         "Bollywood",
        "jatin-lalit":          "Bollywood",
        "uttam singh":          "Bollywood",
        "rahul sharma":         "Bollywood",
        "abhijeet":             "Bollywood",
        "jeet-pritam":          "Bollywood",
        "asha bhosle":          "Bollywood",
        "anu malik":            "Bollywood",
        "madan mohan":          "Bollywood",
        "shankar mahadevan":    "Bollywood",
        "lata mangeshkar":      "Bollywood",
        "kk":                   "Bollywood",
        "shaan":                "Bollywood",
        "babul supriyo":        "Bollywood",
        "rahat fateh ali khan": "Bollywood",
        "kavita krishnamurthy": "Bollywood",
        "sandesh shandilya":    "Bollywood",
        "aadesh shrivastava":   "Bollywood",
        "kumar sanu":           "Bollywood",
        "adnan sami":           "Bollywood",
        "hariharan":            "Bollywood",
        "hari haran":           "Bollywood",
        "shail hada":           "Bollywood",
        "roop kumar rathod":    "Bollywood",
        "bollywood lofi":       "Bollywood",
        "shweta shetty":        "Bollywood",
        "remo fernandes":       "Bollywood",
        "bappi lahiri":         "Bollywood",
        "jaspinder narula":     "Bollywood",
        "sohail sen":           "Bollywood",
        "brijesh shandilya":    "Bollywood",
        "aditi paul":           "Bollywood",
        "lucky ali":            "Bollywood",
        "sanjay leela bhansali":"Bollywood",
        "ram sampath":          "Bollywood",
        "mohit chauhan":        "Bollywood",
        "lijo george-dj chetas":"Bollywood",
        "anand-milind":         "Bollywood",
        "anuradha sriram":      "Bollywood",
        "ajay gogavale":        "Bollywood",
        "jasleen royal":        "Bollywood",
        "aaman trikha":         "Bollywood",
        "ruuh":                 "Bollywood",
        "monty sharma":         "Bollywood",
        "mahalakshmi iyer":     "Bollywood",
        "javed ali":            "Bollywood",
        "pawni pandey":         "Bollywood",
        "joi barua":            "Bollywood",
        "pinky":                "Bollywood",
        "prem & hardeep":       "Bollywood",
        "josh brar":            "Bollywood",
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
        # ── Pop additions ─────────────────────────────────────────────────
        "ariana grande":        "Pop",
        "tove lo":              "Pop",
        "camila cabello":       "Pop",
        "dua lipa":             "Pop",
        "charli xcx":          "Pop",
        # ── Hip Hop additions ─────────────────────────────────────────────
        "future":               "Hip Hop",
        "a$ap rocky":          "Hip Hop",
        "asap rocky":           "Hip Hop",
        "21 savage":            "Hip Hop",
        "playboi carti":        "Hip Hop",
        "lil uzi vert":         "Hip Hop",
        "pop smoke":            "Hip Hop",
        # ── House additions ────────────────────────────────────────────────
        "daphni":               "House",
        "gordo":                "House",
        "bob sinclar":          "House",
        "crunkz":               "House",
        "rampa":                "House",
        "aluna":                "House",
        "francis mercier":      "House",
        "mau p":                "House",
        "layton giordani":      "House",
        "john summit":          "House",
        "camelphat":            "House",
        "black coffee":         "House",
        "themba":               "House",
        # ── UK Garage / Grime additions ────────────────────────────────────
        "sammy virji":          "UK Garage",
        "champion":             "UK Garage",
        "jazzy":                "UK Garage",
        "conducta":             "UK Garage",
        "unknown t":            "Grime",
        # ── Electronic / Techno additions ─────────────────────────────────
        "anyma":                "Electronic",
        "chris liebing":        "Electronic",
        "charlotte de witte":   "Electronic",
        "amelie lens":          "Electronic",
        "adam beyer":           "Electronic",
        "jon hopkins":          "Electronic",
        "moderat":              "Electronic",
        # ── Afrobeats additions ────────────────────────────────────────────
        "shimza":               "Afrobeats",
        "magic system":         "Afrobeats",
        "master kg":            "Afrobeats",
        "davido":               "Afrobeats",
        "wizkid":               "Afrobeats",
        "burna boy":            "Afrobeats",
        # ── Dubstep / Bass additions ───────────────────────────────────────
        "excision":             "Dubstep",
        "zomboy":               "Dubstep",
        "subtronics":           "Dubstep",
        "kai wachi":            "Dubstep",
        # ── Latin additions ────────────────────────────────────────────────
        "bad bunny":            "Latin",
        "j balvin":             "Latin",
        "maluma":               "Latin",
        "daddy yankee":         "Latin",
        "ozuna":                "Latin",
        "karol g":              "Latin",
        "rauw alejandro":       "Latin",
        "carlos santana":       "Latin",
        # ── R&B additions ─────────────────────────────────────────────────
        "sza":                  "R&B",
        "frank ocean":          "R&B",
        "giveon":               "R&B",
        "brent faiyaz":         "R&B",
        "khalid":               "R&B",
        "monica":               "R&B",
        # ── Remixers / producers with confirmed genre ─────────────────────
        "becker & vermont":     "Punjabi",
        "marten lou":           "Punjabi",
        # ── Punjabi additions (2026-05-25) ────────────────────────────────
        "bilal saeed":          "Punjabi",
        "prem dhillon":         "Punjabi",
        "cheema y":             "Punjabi",
        "jasmeen akhtar":       "Punjabi",
        "im ravi":              "Punjabi",
        "paigham chhina":       "Punjabi",
        "pirti silon":          "Punjabi",
        # ── Hip Hop / R&B additions ───────────────────────────────────────
        "t-pain":               "R&B",
        "mos def":              "Hip Hop",
        "joe moses":            "Hip Hop",
        "devstacks":            "Hip Hop",
        # ── Grime additions ───────────────────────────────────────────────
        "big kay smg":          "Grime",
        "baggh-e smg":          "Grime",
        "embertalks":           "Grime",
        # ── Electronic / Dance additions ──────────────────────────────────
        "the cure":             "Electronic",
        "rune rk":              "Electronic",
        "dance fruits music":   "Electronic",
        "klangspiel":           "Electronic",
        "enur":                 "Electronic",
        "nausica":              "Electronic",
        "moyjo":                "Electronic",
        "sllash & doppe":       "Electronic",
        "dj burlak":            "Electronic",
        "bdt (kz)":             "Electronic",
        "kiko":                 "Electronic",
        # ── Latin additions ───────────────────────────────────────────────
        "duendes":              "Latin",
        # ── Classical (no Classical folder — nearest is Pop) ─────────────
        "george frideric handel": "Pop",
        "giuseppe verdi":         "Pop",
        # ── K-Pop ─────────────────────────────────────────────────────────
        "itzy":                   "Pop",
        # ── Telugu additions ──────────────────────────────────────────────
        "geetha madhuri":         "Telugu",
        # ── Bollywood additions ───────────────────────────────────────────
        "yasser desai":           "Bollywood",
        "atul diwakar":           "Bollywood",
        # ── Punjabi additions (2026-05-25 run 3) ──────────────────────────
        "deep dhaliwal":          "Punjabi",
        "amrinder gill":          "Punjabi",
        "nanku":                  "Punjabi",
        # ── Hip Hop additions ─────────────────────────────────────────────
        "diddy":                  "Hip Hop",
        "p. diddy":               "Hip Hop",
        "puff daddy":             "Hip Hop",
        # ── Electronic additions (2026-05-25 run 3) ───────────────────────
        "rbãr":                   "Electronic",
        "rbør":                   "Electronic",
        "rbar":                   "Electronic",
        "stoltenhoff":            "Electronic",
        "dj.nostalgia":           "Electronic",
        "nevra pulse":            "Electronic",
        "chriss aum":             "Electronic",
        "beat smuggler":          "Electronic",
        "khenya":                 "Electronic",
        "igmor":                  "Electronic",
        "metty":                  "Electronic",
        "kyanq":                  "Electronic",
        "raaccso":                "Electronic",
        "thiarajxtt":             "Electronic",
        "starly":                 "Electronic",
        # ── Punjabi additions (2026-05-25 run 4) ──────────────────────────
        "saabi bhinder":          "Punjabi",
        "jassa dhillon":          "Punjabi",
        # ── Grime additions ───────────────────────────────────────────────
        "nsg":                    "Grime",
        # ── Electronic additions (2026-05-25 run 4) ───────────────────────
        "fede aliprandi":         "Electronic",
        "lomiiel":                "Electronic",
        "ritmes de kanderi":      "Electronic",
        "rsquared":               "Electronic",
        # ── Pop additions ─────────────────────────────────────────────────
        "don williams":           "Pop",
        "danny rhys":             "Pop",
        "hotel ugly":             "Pop",
        "sorority noise":         "Pop",
        # ── Pop additions (2026-05-25 run 5) ──────────────────────────────
        "madonna":                "Pop",
        "pj masks":               "Pop",
        # ── R&B additions ─────────────────────────────────────────────────
        "chlöe":                  "R&B",
        "chloe":                  "R&B",
        # ── Hip Hop additions ─────────────────────────────────────────────
        "niska":                  "Hip Hop",
        # ── Punjabi additions (2026-05-25 run 5) ──────────────────────────
        "dum k":                  "Punjabi",
        # ── Electronic / Dance additions (2026-05-25 run 5) ───────────────
        "sash":                   "Electronic",
        "1200 micrograms":        "Electronic",
        "markus schulz":          "Electronic",
        "protoculture":           "Electronic",
        "cafe de anatolia":       "Electronic",
        "anton powers":           "Electronic",
        "daniel rateuke":         "Electronic",
        "techno project":         "Electronic",
        "azzecca":                "Electronic",
        "dan tanev":              "Electronic",
        "andi vegas":             "Electronic",
        "sega sound team":        "Electronic",
        "david lowe":             "Electronic",
        "nando marttinez":        "Electronic",
        "dope (pt)":              "Electronic",
        "j. axel":                "Electronic",
        "pato's groove":          "Electronic",
        "diego bustamante":       "Electronic",
        # ── Punjabi additions (2026-05-25 run 6) ──────────────────────────
        "sultaan":                "Punjabi",
        "pablo dutta":            "Punjabi",
        "ravi pilibanga":         "Punjabi",
        # ── Latin additions (2026-05-25 run 6) ────────────────────────────
        "santana":                "Latin",
        "tito puente":            "Latin",
        # ── Pop additions (2026-05-25 run 6) ──────────────────────────────
        "dennis lloyd":           "Pop",
        "the score":              "Pop",
        # ── Electronic additions (2026-05-25 run 6) ───────────────────────
        "kastelo":                "Electronic",
        # ── Pop additions (2026-05-25) ────────────────────────────────────
        "ellie goulding":         "Pop",
        "jung kook":              "Pop",
        "mariah carey":           "Pop",
        "bazzi":                  "Pop",
        "alex warren":            "Pop",
        "enhypen":                "Pop",
        # ── R&B / Latin additions ─────────────────────────────────────────
        "kali uchis":             "R&B",
        "iglesias":               "Latin",
        "enrique iglesias":       "Latin",
        # ── Hip Hop additions ─────────────────────────────────────────────
        "gone.fludd":             "Hip Hop",
        "buckshot":               "Hip Hop",
        "king":                   "Indian Hip Hop",
        # ── Electronic / House additions ──────────────────────────────────
        "ben böhmer":             "Electronic",
        "ben bohmer":             "Electronic",
        "radiohead":              "Electronic",
        "umwelt":                 "Electronic",
        "william deep":           "Electronic",
        "vasco c":                "Electronic",
        "gabrijel bergmann":      "Electronic",
        "yasar akpence":          "Electronic",
        "high frequency bass":    "Electronic",
        "messwave":               "Electronic",
        "zag beatmaker":          "Electronic",
        "redredred":              "Electronic",
        "proudly people":         "Electronic",
        "beatpella house":        "House",
        "scuchi":                 "Electronic",
        "dp369":                  "Electronic",
    }


class DevelopmentConfig(Config):
    """Development configuration — no hard requirement on secrets."""
    DEBUG = True
    FLASK_ENV = "development"


class ProductionConfig(Config):
    """
    Production configuration — fail fast if required secrets are missing.

    Required env vars in production:
      SECRET_KEY          — Flask session signing key (min 32 chars)
      MONGODB_URI         — MongoDB Atlas connection string
      REDIS_URL           — Upstash / Redis connection string
      SPOTIFY_CLIENT_ID   — Spotify Developer app client ID
      SPOTIFY_CLIENT_SECRET
      REDIRECT_URI        — Must match your Spotify Dashboard redirect URI
      ALLOWED_ORIGINS     — Comma-separated list of frontend origins
    """
    DEBUG = False
    FLASK_ENV = "production"

    # Redis + MongoDB URLs required in production
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")

    def __init__(self):
        missing = []

        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            missing.append(
                "SECRET_KEY (must be at least 32 chars — "
                "run: python -c \"import secrets; print(secrets.token_hex(32))\")"
            )

        if not self.REDIS_URL:
            missing.append("REDIS_URL (required in production — use Upstash or Render Redis)")

        _mongo = os.getenv("MONGODB_URI", "")
        if not _mongo or _mongo == "mongodb://localhost:27017":
            missing.append(
                "MONGODB_URI (must point to MongoDB Atlas, not localhost)"
            )

        if not self.SPOTIFY_CLIENT_ID:
            missing.append("SPOTIFY_CLIENT_ID")

        if not self.SPOTIFY_CLIENT_SECRET:
            missing.append("SPOTIFY_CLIENT_SECRET")

        _redirect = os.getenv("REDIRECT_URI", "")
        if not _redirect or "127.0.0.1" in _redirect or "localhost" in _redirect:
            missing.append(
                "REDIRECT_URI (must be your production backend URL, e.g. "
                "https://your-app.onrender.com/callback)"
            )

        _origins = os.getenv("ALLOWED_ORIGINS", "")
        if not _origins:
            missing.append(
                "ALLOWED_ORIGINS (comma-separated list of frontend origins, "
                "e.g. https://your-app.vercel.app)"
            )

        if missing:
            print("\n" + "=" * 70, file=sys.stderr)
            print("FATAL: Production startup blocked — missing required env vars:", file=sys.stderr)
            for item in missing:
                print(f"  ✗  {item}", file=sys.stderr)
            print("=" * 70 + "\n", file=sys.stderr)
            sys.exit(1)


# Get config based on environment
config = DevelopmentConfig() if os.getenv("FLASK_ENV") == "development" else ProductionConfig()
