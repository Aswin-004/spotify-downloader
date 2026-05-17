"""
Artist Knowledge Service
========================
Static multi-layer artist intelligence for routing and identification.

Adds a knowledge-base lookup layer between artist_override and
artist_memory/Spotify in the genre_router resolution chain.

Provides:
- Rich artist profiles with aliases (multilingual, DJ names, stage names)
- Fast lookup by canonical name or any alias
- Multilingual normalization (Devanagari → Latin transliteration)
- Event-rip title cleaner
- Routing confidence boosts for known artists
- Knowledge report generation
"""
from __future__ import annotations

import re
import unicodedata

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

CONFIDENCE_KNOWLEDGE_BASE = 0.85

# ── Devanagari / script → canonical English map ───────────────────────────────
_SCRIPT_TO_CANONICAL: dict[str, str] = {
    "अरिजीत सिंह":      "Arijit Singh",
    "अरिजित सिंह":      "Arijit Singh",
    "अनिरुद्ध":          "Anirudh Ravichander",
    "ए.आर. रहमान":       "A.R. Rahman",
    "बादशाह":            "Badshah",
    "नेहा कक्कड़":       "Neha Kakkar",
    "श्रेया घोषाल":      "Shreya Ghoshal",
    "करण औजला":         "Karan Aujla",
    "सिद्धू मूसेवाला":   "Sidhu Moosewala",
    "दिलजीत दोसांझ":    "Diljit Dosanjh",
    "एपी ढिल्लों":       "AP Dhillon",
    "दिव्ाइन":           "Divine",
    "प्रीतम":            "Pritam",
    "विशाल-शेखर":        "Vishal-Shekhar",
}

# ── Event-rip noise: (pattern, replacement, flags) ───────────────────────────
_EVENT_RIP_PATTERNS: list[tuple[str, str, int]] = [
    (r'\bdjcity\b',            '', re.IGNORECASE),
    (r'\bfree\s*download\b',   '', re.IGNORECASE),
    (r'\bout\s*now\b',         '', re.IGNORECASE),
    (r'\bofficial\s*video\b',  '', re.IGNORECASE),
    (r'\bvisualizer\b',        '', re.IGNORECASE),
    (r'\b320\s*kbps\b',        '', re.IGNORECASE),
    (r'\bdj\s*version\b',      '', re.IGNORECASE),
    (r'\byt\s*rip\b',          '', re.IGNORECASE),
    (r'\byoutube\s*rip\b',     '', re.IGNORECASE),
    (r'\b(hq|hd)\b',           '', re.IGNORECASE),
    (r'\baudio\b',             '', re.IGNORECASE),
]

# ── Raw profiles: (canonical_name, aliases_lowercase, genre, region, language) ─
# aliases should be lower-case normalized variants (spacing, punctuation, scripts)
_RAW_PROFILES: list[tuple[str, list[str], str, str, str]] = [

    # ══════════════════════════════════════════════════════════════════════════
    # BOLLYWOOD
    # ══════════════════════════════════════════════════════════════════════════
    ("Arijit Singh",
     ["arijit", "arjit singh", "arijit singh"],
     "Bollywood", "IN", "hi"),

    ("Pritam",
     ["pritam", "pritam chakraborty", "pritam da", "pritam chakraborthy"],
     "Bollywood", "IN", "hi"),

    ("Vishal-Shekhar",
     ["vishal shekhar", "vishal-shekhar", "vishal & shekhar", "vishal and shekhar"],
     "Bollywood", "IN", "hi"),

    ("Sachet Tandon",
     ["sachet", "sachet tandon", "sachet-parampara", "sachet parampara"],
     "Bollywood", "IN", "hi"),

    ("A.R. Rahman",
     ["ar rahman", "a r rahman", "allah rakha rahman",
      "a.r. rahman", "ar. rahman", "a.r rahman"],
     "Bollywood", "IN", "hi"),

    ("Shreya Ghoshal",
     ["shreya ghoshal", "shreya"],
     "Bollywood", "IN", "hi"),

    ("Neha Kakkar",
     ["neha kakkar", "neha"],
     "Bollywood", "IN", "hi"),

    ("Badshah",
     ["badshah", "aditya prateek singh sisodia"],
     "Bollywood", "IN", "hi"),

    ("Sonu Nigam",
     ["sonu nigam", "sonu"],
     "Bollywood", "IN", "hi"),

    ("Himesh Reshammiya",
     ["himesh reshammiya", "himesh", "himesh reshammia"],
     "Bollywood", "IN", "hi"),

    ("Atif Aslam",
     ["atif aslam", "atif"],
     "Bollywood", "PK", "hi"),

    ("Armaan Malik",
     ["armaan malik"],
     "Bollywood", "IN", "hi"),

    ("Jubin Nautiyal",
     ["jubin nautiyal", "jubin"],
     "Bollywood", "IN", "hi"),

    ("Darshan Raval",
     ["darshan raval", "darshan"],
     "Bollywood", "IN", "hi"),

    ("B Praak",
     ["b praak", "b. praak", "bpraak"],
     "Bollywood", "IN", "hi"),

    ("Guru Randhawa",
     ["guru randhawa", "guru"],
     "Bollywood", "IN", "hi"),

    ("Tony Kakkar",
     ["tony kakkar", "tony"],
     "Bollywood", "IN", "hi"),

    ("Tanishk Bagchi",
     ["tanishk bagchi", "tanishk"],
     "Bollywood", "IN", "hi"),

    ("Shankar-Ehsaan-Loy",
     ["shankar ehsaan loy", "sel", "shankar-ehsaan-loy"],
     "Bollywood", "IN", "hi"),

    ("Amit Trivedi",
     ["amit trivedi"],
     "Bollywood", "IN", "hi"),

    ("Mithoon",
     ["mithoon"],
     "Bollywood", "IN", "hi"),

    ("Vishal Mishra",
     ["vishal mishra"],
     "Bollywood", "IN", "hi"),

    ("Meet Bros",
     ["meet bros", "meet bro"],
     "Bollywood", "IN", "hi"),

    ("Dhvani Bhanushali",
     ["dhvani bhanushali", "dhvani"],
     "Bollywood", "IN", "hi"),

    ("Jeet Gannguli",
     ["jeet gannguli", "jeet"],
     "Bollywood", "IN", "hi"),

    ("Shashwat Sachdev",
     ["shashwat sachdev", "shashwat"],
     "Bollywood", "IN", "hi"),

    ("Ankit Tiwari",
     ["ankit tiwari", "ankit"],
     "Bollywood", "IN", "hi"),

    ("Asees Kaur",
     ["asees kaur", "asees"],
     "Bollywood", "IN", "hi"),

    ("Sunidhi Chauhan",
     ["sunidhi chauhan", "sunidhi"],
     "Bollywood", "IN", "hi"),

    ("Alisha Chinai",
     ["alisha chinai", "alisha"],
     "Bollywood", "IN", "hi"),

    ("Alka Yagnik",
     ["alka yagnik", "alka"],
     "Bollywood", "IN", "hi"),

    ("Jyotica Tangri",
     ["jyotica tangri", "jyotica"],
     "Bollywood", "IN", "hi"),

    ("Mamta Sharma",
     ["mamta sharma", "mamta"],
     "Bollywood", "IN", "hi"),

    ("Monali Thakur",
     ["monali thakur", "monali"],
     "Bollywood", "IN", "hi"),

    ("Neeti Mohan",
     ["neeti mohan", "neeti"],
     "Bollywood", "IN", "hi"),

    ("Rekha Bhardwaj",
     ["rekha bhardwaj", "rekha"],
     "Bollywood", "IN", "hi"),

    ("Sapna Awasthi",
     ["sapna awasthi", "sapna"],
     "Bollywood", "IN", "hi"),

    ("Vishal Bhardwaj",
     ["vishal bhardwaj"],
     "Bollywood", "IN", "hi"),

    ("Sachin-Jigar",
     ["sachin-jigar", "sachin jigar"],
     "Bollywood", "IN", "hi"),

    ("Sajid-Wajid",
     ["sajid-wajid", "sajid wajid"],
     "Bollywood", "IN", "hi"),

    ("Salim-Sulaiman",
     ["salim-sulaiman", "salim sulaiman", "salim–sulaiman"],
     "Bollywood", "IN", "hi"),

    # ══════════════════════════════════════════════════════════════════════════
    # PUNJABI
    # ══════════════════════════════════════════════════════════════════════════
    ("Karan Aujla",
     ["karan aujla", "k aujla"],
     "Punjabi", "IN", "pa"),

    ("Mika Singh",
     ["mika singh", "mika", "amrik singh"],
     "Punjabi", "IN", "pa"),

    ("AP Dhillon",
     ["ap dhillon", "a.p. dhillon", "amritpal singh dhillon", "a.p dhillon"],
     "Punjabi", "IN", "pa"),

    ("Sidhu Moosewala",
     ["sidhu moosewala", "sidhu moose wala", "shubhdeep singh sidhu", "sidhu"],
     "Punjabi", "IN", "pa"),

    ("Diljit Dosanjh",
     ["diljit dosanjh", "diljit", "diljit dosanj"],
     "Punjabi", "IN", "pa"),

    ("Jass Manak",
     ["jass manak", "jass"],
     "Punjabi", "IN", "pa"),

    ("Parmish Verma",
     ["parmish verma", "parmish"],
     "Punjabi", "IN", "pa"),

    ("Jordan Sandhu",
     ["jordan sandhu", "jordan"],
     "Punjabi", "IN", "pa"),

    ("Mankirt Aulakh",
     ["mankirt aulakh", "mankirt"],
     "Punjabi", "IN", "pa"),

    ("Gippy Grewal",
     ["gippy grewal", "gippy"],
     "Punjabi", "IN", "pa"),

    ("Hardy Sandhu",
     ["hardy sandhu", "hardeep sandhu", "harvy sandhu"],
     "Punjabi", "IN", "pa"),

    ("Ammy Virk",
     ["ammy virk", "ammy"],
     "Punjabi", "IN", "pa"),

    ("R Nait",
     ["r nait", "rnait"],
     "Punjabi", "IN", "pa"),

    ("Tarsem Jassar",
     ["tarsem jassar", "tarsem"],
     "Punjabi", "IN", "pa"),

    ("Gurdas Maan",
     ["gurdas maan", "gurdas"],
     "Punjabi", "IN", "pa"),

    ("Satinder Sartaaj",
     ["satinder sartaaj", "satinder"],
     "Punjabi", "IN", "pa"),

    ("Kaur B",
     ["kaur b", "kaurb"],
     "Punjabi", "IN", "pa"),

    ("Nimrat Khaira",
     ["nimrat khaira", "nimrat"],
     "Punjabi", "IN", "pa"),

    ("Ranjit Bawa",
     ["ranjit bawa", "ranjit"],
     "Punjabi", "IN", "pa"),

    ("Garry Sandhu",
     ["garry sandhu", "garry"],
     "Punjabi", "IN", "pa"),

    ("Ninja",
     ["ninja", "gurjind maan"],
     "Punjabi", "IN", "pa"),

    # ══════════════════════════════════════════════════════════════════════════
    # TAMIL
    # ══════════════════════════════════════════════════════════════════════════
    ("Anirudh Ravichander",
     ["anirudh", "anirudh ravichander", "anirudh ravichandran"],
     "Tamil", "IN", "ta"),

    ("Santhosh Narayanan",
     ["santhosh narayanan", "santhosh", "santhosh n"],
     "Tamil", "IN", "ta"),

    ("Yuvan Shankar Raja",
     ["yuvan", "yuvan shankar raja", "ysr"],
     "Tamil", "IN", "ta"),

    ("D. Imman",
     ["d imman", "imman", "d. imman"],
     "Tamil", "IN", "ta"),

    ("Harris Jayaraj",
     ["harris jayaraj", "harris"],
     "Tamil", "IN", "ta"),

    ("G.V. Prakash Kumar",
     ["gv prakash", "g.v. prakash", "gv prakash kumar", "g v prakash kumar"],
     "Tamil", "IN", "ta"),

    ("Sean Roldan",
     ["sean roldan"],
     "Tamil", "IN", "ta"),

    ("Sid Sriram",
     ["sid sriram", "siddharth sriram"],
     "Tamil", "IN", "ta"),

    # ══════════════════════════════════════════════════════════════════════════
    # TELUGU
    # ══════════════════════════════════════════════════════════════════════════
    ("Devi Sri Prasad",
     ["devi sri prasad", "dsp", "s. s. thaman"],
     "Telugu", "IN", "te"),

    ("Thaman S",
     ["thaman s", "thaman", "s thaman", "s. thaman", "ss thaman"],
     "Telugu", "IN", "te"),

    ("MM Keeravaani",
     ["mm keeravaani", "keeravani", "m m keeravaani", "maragathamani"],
     "Telugu", "IN", "te"),

    ("Anup Rubens",
     ["anup rubens"],
     "Telugu", "IN", "te"),

    ("Mickey J Meyer",
     ["mickey j meyer", "mickey meyer"],
     "Telugu", "IN", "te"),

    ("Radhan",
     ["radhan"],
     "Telugu", "IN", "te"),

    ("Sandeep Chowta",
     ["sandeep chowta"],
     "Telugu", "IN", "te"),

    ("Sreerama Chandra",
     ["sreerama chandra", "srirama chandra"],
     "Telugu", "IN", "te"),

    ("S. P. Balasubrahmanyam",
     ["s. p. balasubrahmanyam", "spb", "s p balasubrahmanyam", "sp balasubrahmanyam"],
     "Tamil", "IN", "ta"),

    # ══════════════════════════════════════════════════════════════════════════
    # MARATHI
    # ══════════════════════════════════════════════════════════════════════════
    ("Ajay-Atul",
     ["ajay atul", "ajay-atul", "ajay & atul", "ajay and atul"],
     "Indian", "IN", "mr"),

    ("Avadhoot Gupte",
     ["avadhoot gupte", "avadhoot"],
     "Indian", "IN", "mr"),

    ("Bela Shende",
     ["bela shende"],
     "Indian", "IN", "mr"),

    ("Nagesh Morwekar",
     ["nagesh morwekar"],
     "Indian", "IN", "mr"),

    ("Reshma Sonawane",
     ["reshma sonawane"],
     "Indian", "IN", "mr"),

    ("Sanjay Londe",
     ["sanjay londe"],
     "Indian", "IN", "mr"),

    ("Shailesh Ranade",
     ["shailesh ranade"],
     "Indian", "IN", "mr"),

    # ══════════════════════════════════════════════════════════════════════════
    # INDIAN HIP HOP
    # ══════════════════════════════════════════════════════════════════════════
    ("Seedhe Maut",
     ["seedhe maut", "encore abhi", "bayaan", "sm"],
     "Indian Hip Hop", "IN", "hi"),

    ("Divine",
     ["divine", "vivian fernandes", "mc divine"],
     "Indian Hip Hop", "IN", "hi"),

    ("KR$NA",
     ["kr$na", "krsna"],
     "Indian Hip Hop", "IN", "hi"),

    ("Emiway Bantai",
     ["emiway", "emiway bantai", "bilal shaikh"],
     "Indian Hip Hop", "IN", "hi"),

    ("Mc Stan",
     ["mc stan", "stan", "altamash faridi"],
     "Indian Hip Hop", "IN", "hi"),

    ("Prabh Deep",
     ["prabh deep"],
     "Indian Hip Hop", "IN", "hi"),

    ("Brodha V",
     ["brodha v", "anand venkateswaran"],
     "Indian Hip Hop", "IN", "hi"),

    ("EPR",
     ["epr"],
     "Indian Hip Hop", "IN", "hi"),

    ("Nucleya",
     ["nucleya", "bass rani"],
     "Indian Hip Hop", "IN", "hi"),

    ("Karma",
     ["karma", "kartik sharma"],
     "Indian Hip Hop", "IN", "hi"),

    ("Yungsta",
     ["yungsta"],
     "Indian Hip Hop", "IN", "hi"),

    # ══════════════════════════════════════════════════════════════════════════
    # GLOBAL EDM / ELECTRONIC
    # ══════════════════════════════════════════════════════════════════════════
    ("Skrillex",
     ["skrillex", "sonny moore", "sonny john moore"],
     "Electronic", "US", "en"),

    ("Fred again..",
     ["fred again", "fred again..", "fred again.."],
     "House", "GB", "en"),

    ("Sammy Virji",
     ["sammy virji", "sammy"],
     "UK Garage", "GB", "en"),

    ("Hamdi",
     ["hamdi", "hamdi ali"],
     "Electronic", "GB", "en"),

    ("Chase & Status",
     ["chase and status", "chase & status", "chase status"],
     "Drum and Bass", "GB", "en"),

    ("Sub Focus",
     ["sub focus", "subfocus", "nick douwma"],
     "Drum and Bass", "GB", "en"),

    ("Dimension",
     ["dimension", "jordan connell"],
     "Drum and Bass", "GB", "en"),

    ("Martin Garrix",
     ["martin garrix", "martijn gerard garritsen"],
     "Electronic", "NL", "en"),

    ("Hardwell",
     ["hardwell", "robbert van de corput"],
     "Electronic", "NL", "en"),

    ("Illenium",
     ["illenium", "nicholas dillon miller"],
     "Electronic", "US", "en"),

    ("KSHMR",
     ["kshmr", "niles hollowell-dhar"],
     "Electronic", "US", "en"),

    ("Solomun",
     ["solomun", "mladen solomun"],
     "House", "DE", "en"),

    ("Four Tet",
     ["four tet", "kieran hebden"],
     "House", "GB", "en"),

    ("Peggy Gou",
     ["peggy gou"],
     "House", "KR", "en"),

    ("Boris Brejcha",
     ["boris brejcha", "brejcha"],
     "House", "DE", "en"),

    ("Bicep",
     ["bicep", "matt mcbriar", "andy ferguson"],
     "House", "GB", "en"),

    ("Disclosure",
     ["disclosure", "howard lawrence", "guy lawrence"],
     "House", "GB", "en"),

    ("Jamie xx",
     ["jamie xx", "jamie smith"],
     "Electronic", "GB", "en"),

    ("Calvin Harris",
     ["calvin harris", "adam richard wiles"],
     "House", "GB", "en"),

    ("Diplo",
     ["diplo", "thomas wesley pentz"],
     "Electronic", "US", "en"),

    ("DJ Snake",
     ["dj snake", "william sami etienne grigahcine"],
     "Electronic", "FR", "en"),

    ("Marshmello",
     ["marshmello", "christopher comstock"],
     "Electronic", "US", "en"),

    ("Zedd",
     ["zedd", "anton zaslavski"],
     "Electronic", "DE", "en"),

    ("Avicii",
     ["avicii", "tim berg", "tim bergling"],
     "Electronic", "SE", "en"),

    ("Deadmau5",
     ["deadmau5", "joel zimmerman"],
     "Electronic", "CA", "en"),

    ("Eric Prydz",
     ["eric prydz", "pryda", "cirez d"],
     "Electronic", "SE", "en"),

    ("Fisher",
     ["fisher", "paul fisher"],
     "House", "AU", "en"),

    ("Chris Lake",
     ["chris lake"],
     "House", "GB", "en"),

    ("Claptone",
     ["claptone"],
     "House", "DE", "en"),

    ("DJ Koze",
     ["dj koze", "koze"],
     "House", "DE", "en"),

    ("Caribou",
     ["caribou", "dan snaith", "manitoba"],
     "Electronic", "CA", "en"),

    ("Andy C",
     ["andy c", "andrew clarke"],
     "Drum and Bass", "GB", "en"),

    ("Shy FX",
     ["shy fx", "andre williams"],
     "Drum and Bass", "GB", "en"),

    ("Pendulum",
     ["pendulum"],
     "Drum and Bass", "AU", "en"),

    ("Noisia",
     ["noisia"],
     "Drum and Bass", "NL", "en"),

    ("Goldie",
     ["goldie", "clifford price"],
     "Drum and Bass", "GB", "en"),

    ("Conducta",
     ["conducta"],
     "UK Garage", "GB", "en"),

    ("Zetts",
     ["zetts"],
     "UK Garage", "GB", "en"),

    ("WSTRN",
     ["wstrn", "western"],
     "UK Garage", "GB", "en"),

    ("Craig David",
     ["craig david"],
     "UK Garage", "GB", "en"),

    ("MJ Cole",
     ["mj cole"],
     "UK Garage", "GB", "en"),

    ("Artful Dodger",
     ["artful dodger"],
     "UK Garage", "GB", "en"),

    ("So Solid Crew",
     ["so solid crew", "so solid"],
     "Grime", "GB", "en"),

    # ══════════════════════════════════════════════════════════════════════════
    # GLOBAL HIP HOP / GRIME
    # ══════════════════════════════════════════════════════════════════════════
    ("Drake",
     ["drake", "aubrey drake graham"],
     "Hip Hop", "CA", "en"),

    ("Kendrick Lamar",
     ["kendrick lamar", "kendrick", "k dot"],
     "Hip Hop", "US", "en"),

    ("J. Cole",
     ["j cole", "j. cole", "jermaine lamarr cole"],
     "Hip Hop", "US", "en"),

    ("Travis Scott",
     ["travis scott", "la flame"],
     "Hip Hop", "US", "en"),

    ("Tyler the Creator",
     ["tyler the creator", "tyler okonma", "tyler, the creator"],
     "Hip Hop", "US", "en"),

    ("Kanye West",
     ["kanye west", "ye", "kanye"],
     "Hip Hop", "US", "en"),

    ("Central Cee",
     ["central cee", "oakie"],
     "Hip Hop", "GB", "en"),

    ("Dave",
     ["dave", "santan dave", "david orobosa omoregie"],
     "Hip Hop", "GB", "en"),

    ("Skepta",
     ["skepta", "joseph junior adenuga jr"],
     "Grime", "GB", "en"),

    ("Stormzy",
     ["stormzy", "michael ebenazer kwadjo omari owuo jr"],
     "Grime", "GB", "en"),

    ("AJ Tracey",
     ["aj tracey"],
     "Grime", "GB", "en"),

    ("Ghetts",
     ["ghetts", "justin paul emmanus clarke"],
     "Grime", "GB", "en"),

    ("Chip",
     ["chip", "chipmunk", "jahmaal fyffe"],
     "Grime", "GB", "en"),
]


# ── Index build ───────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split()).strip()


_ALIAS_MAP: dict[str, dict] = {}


def _build_index() -> None:
    for (canonical, aliases, genre, region, language) in _RAW_PROFILES:
        profile = {
            "canonical_name": canonical,
            "genre": genre,
            "region": region,
            "language": language,
            "confidence": CONFIDENCE_KNOWLEDGE_BASE,
            "source": "knowledge_base",
        }
        for name_variant in [canonical] + aliases:
            key = _normalize(name_variant)
            if key and key not in _ALIAS_MAP:
                _ALIAS_MAP[key] = profile


_build_index()


# ── Public API ────────────────────────────────────────────────────────────────

def normalize_multilingual_artist(name: str) -> str:
    """
    Map a multilingual artist name (Devanagari, Tamil, Telugu) to its
    canonical English form using a static table.
    Returns the original name unchanged if no mapping found.
    """
    if not name:
        return name
    stripped = name.strip()
    # Direct script lookup
    if stripped in _SCRIPT_TO_CANONICAL:
        return _SCRIPT_TO_CANONICAL[stripped]
    # Normalized comparison
    norm = _normalize(stripped)
    for script_name, canonical in _SCRIPT_TO_CANONICAL.items():
        if _normalize(script_name) == norm:
            return canonical
    return name


def lookup_artist_knowledge(name: str) -> dict | None:
    """
    Look up an artist in the static knowledge base.

    Returns a profile dict:
      {canonical_name, genre, region, language, confidence, source}
    or None if the artist is not known.
    """
    if not name:
        return None

    # 1. Direct normalized lookup
    key = _normalize(name)
    hit = _ALIAS_MAP.get(key)
    if hit:
        return hit

    # 2. Try multilingual normalization first
    canonical = normalize_multilingual_artist(name)
    if canonical != name:
        key2 = _normalize(canonical)
        hit = _ALIAS_MAP.get(key2)
        if hit:
            return hit

    return None


def clean_event_rip_title(title: str) -> str:
    """
    Strip event-rip noise tags from a song title.

    Removes: DJCITY, FREE DOWNLOAD, OUT NOW, OFFICIAL VIDEO,
             VISUALIZER, HQ, 320KBPS, DJ VERSION, YT RIP, AUDIO.
    Preserves actual song title semantics.
    """
    if not title:
        return title
    result = title
    for pattern, replacement, flags in _EVENT_RIP_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=flags)
    # Clean up now-empty brackets/parens
    result = re.sub(r'\(\s*[|–—\s]*\)', '', result)
    result = re.sub(r'\[\s*[|–—\s]*\]', '', result)
    result = re.sub(r'\s{2,}', ' ', result)
    return result.strip(' -–—|')


def get_knowledge_report() -> dict:
    """
    Generate a summary of the artist knowledge base for reporting.
    """
    genre_counts: dict[str, int] = {}
    region_counts: dict[str, int] = {}
    seen: set[str] = set()

    for (canonical, aliases, genre, region, _lang) in _RAW_PROFILES:
        if canonical in seen:
            continue
        seen.add(canonical)
        genre_counts[genre] = genre_counts.get(genre, 0) + 1
        region_counts[region] = region_counts.get(region, 0) + 1

    return {
        "total_artists": len(seen),
        "total_alias_keys": len(_ALIAS_MAP),
        "genre_coverage": genre_counts,
        "regional_coverage": region_counts,
        "confidence_level": CONFIDENCE_KNOWLEDGE_BASE,
    }


# ── NeedsReview bulk-resolution map ──────────────────────────────────────────
# Curated high-confidence Indian artist → exact Library/ target path.
# Keys: output of _normalize() applied to the NeedsReview folder name.
# Values: (library_path, confidence, canonical_genre)
# confidence is 0.95 — above the 0.90 bulk-resolve threshold but below 1.0
# so it never silently overrides a manual user move recorded in artist_memory.

CONFIDENCE_BULK_RESOLVE = 0.95

NEEDS_REVIEW_RESOLUTION_MAP: dict[str, tuple[str, float, str]] = {
    # ── A.R. Rahman ───────────────────────────────────────────────────────────
    "a.r. rahman":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "ar rahman":            ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "a r rahman":           ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Vishal-Shekhar ────────────────────────────────────────────────────────
    "vishal-shekhar":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "vishal shekhar":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Mika Singh ────────────────────────────────────────────────────────────
    "mika singh":           ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "mika":                 ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    # ── Neha Kakkar ───────────────────────────────────────────────────────────
    "neha kakkar":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Ajay-Atul (Marathi) ───────────────────────────────────────────────────
    "ajay-atul":            ("Library/Indian/Marathi",   CONFIDENCE_BULK_RESOLVE, "Indian"),
    "ajay atul":            ("Library/Indian/Marathi",   CONFIDENCE_BULK_RESOLVE, "Indian"),
    # ── Devi Sri Prasad ───────────────────────────────────────────────────────
    "devi sri prasad":      ("Library/Indian/Telugu",    CONFIDENCE_BULK_RESOLVE, "Telugu"),
    "dsp":                  ("Library/Indian/Telugu",    CONFIDENCE_BULK_RESOLVE, "Telugu"),
    # ── Shankar-Ehsaan-Loy ────────────────────────────────────────────────────
    "shankar-ehsaan-loy":   ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "shankar ehsaan loy":   ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sel":                  ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Sunidhi Chauhan ───────────────────────────────────────────────────────
    "sunidhi chauhan":      ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sunidhi":              ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Shreya Ghoshal ────────────────────────────────────────────────────────
    "shreya ghoshal":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "shreya":               ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Additional Bollywood ─────────────────────────────────────────────────
    "arijit singh":         ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "arijit":               ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "arjit singh":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "pritam":               ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "pritam chakraborty":   ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sachet tandon":        ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "badshah":              ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sonu nigam":           ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "himesh reshammiya":    ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "atif aslam":           ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "armaan malik":         ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "jubin nautiyal":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "b praak":              ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "guru randhawa":        ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "amit trivedi":         ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "shankar ehsaan loy":   ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Additional Punjabi ────────────────────────────────────────────────────
    "karan aujla":          ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "ap dhillon":           ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "sidhu moosewala":      ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "sidhu moose wala":     ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "diljit dosanjh":       ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "diljit":               ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "shubh":                ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "juss":                 ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    "harkirat sangha":      ("Library/Indian/Punjabi",   CONFIDENCE_BULK_RESOLVE, "Punjabi"),
    # ── Additional Tamil ─────────────────────────────────────────────────────
    "anirudh ravichander":  ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "anirudh":              ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "santhosh narayanan":   ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "yuvan shankar raja":   ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "yuvan":                ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "d. imman":             ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "harris jayaraj":       ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "sid sriram":           ("Library/Indian/Tamil",     CONFIDENCE_BULK_RESOLVE, "Tamil"),
    # ── Additional Telugu ────────────────────────────────────────────────────
    "thaman s":             ("Library/Indian/Telugu",    CONFIDENCE_BULK_RESOLVE, "Telugu"),
    "thaman":               ("Library/Indian/Telugu",    CONFIDENCE_BULK_RESOLVE, "Telugu"),
    "mm keeravaani":        ("Library/Indian/Telugu",    CONFIDENCE_BULK_RESOLVE, "Telugu"),
    # ── Indian HipHop ────────────────────────────────────────────────────────
    "seedhe maut":          ("Library/Indian/HipHop",    CONFIDENCE_BULK_RESOLVE, "Indian Hip Hop"),
    "divine":               ("Library/Indian/HipHop",    CONFIDENCE_BULK_RESOLVE, "Indian Hip Hop"),
    "kr$na":                ("Library/Indian/HipHop",    CONFIDENCE_BULK_RESOLVE, "Indian Hip Hop"),
    "emiway bantai":        ("Library/Indian/HipHop",    CONFIDENCE_BULK_RESOLVE, "Indian Hip Hop"),
    "emiway":               ("Library/Indian/HipHop",    CONFIDENCE_BULK_RESOLVE, "Indian Hip Hop"),
    "mc stan":              ("Library/Indian/HipHop",    CONFIDENCE_BULK_RESOLVE, "Indian Hip Hop"),
    # ── Bollywood vocalists (Phase 3) ────────────────────────────────────────
    "alisha chinai":        ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "alka yagnik":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "jyotica tangri":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "mamta sharma":         ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "monali thakur":        ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "neeti mohan":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "rekha bhardwaj":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sapna awasthi":        ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "vishal bhardwaj":      ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── Bollywood composer duos (Phase 3) ───────────────────────────────────
    "meet bros anjjan":     ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sachin-jigar":         ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sachin jigar":         ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sajid-wajid":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "sajid wajid":          ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "salim–sulaiman":  ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "salim-sulaiman":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    "salim sulaiman":       ("Library/Indian/Bollywood", CONFIDENCE_BULK_RESOLVE, "Bollywood"),
    # ── South Indian (Phase 3) ───────────────────────────────────────────────
    "s. p. balasubrahmanyam": ("Library/Indian/Tamil",  CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "s p balasubrahmanyam": ("Library/Indian/Tamil",    CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "sp balasubrahmanyam":  ("Library/Indian/Tamil",    CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "spb":                  ("Library/Indian/Tamil",    CONFIDENCE_BULK_RESOLVE, "Tamil"),
    "sandeep chowta":       ("Library/Indian/Telugu",   CONFIDENCE_BULK_RESOLVE, "Telugu"),
    "sreerama chandra":     ("Library/Indian/Telugu",   CONFIDENCE_BULK_RESOLVE, "Telugu"),
    "srirama chandra":      ("Library/Indian/Telugu",   CONFIDENCE_BULK_RESOLVE, "Telugu"),
    # ── Marathi (Phase 3) ────────────────────────────────────────────────────
    "bela shende":          ("Library/Indian/Marathi",  CONFIDENCE_BULK_RESOLVE, "Indian"),
    "nagesh morwekar":      ("Library/Indian/Marathi",  CONFIDENCE_BULK_RESOLVE, "Indian"),
    "reshma sonawane":      ("Library/Indian/Marathi",  CONFIDENCE_BULK_RESOLVE, "Indian"),
    "sanjay londe":         ("Library/Indian/Marathi",  CONFIDENCE_BULK_RESOLVE, "Indian"),
    "shailesh ranade":      ("Library/Indian/Marathi",  CONFIDENCE_BULK_RESOLVE, "Indian"),
}


def lookup_needsreview_routing(artist_name: str) -> tuple[str, float, str] | None:
    """
    Return high-confidence routing for a NeedsReview artist folder.

    Checks the curated NEEDS_REVIEW_RESOLUTION_MAP (conf 0.95) first, then
    falls back to the general knowledge base (conf 0.85).

    Returns:
        (library_path, confidence, genre) — e.g.
        ("Library/Indian/Bollywood", 0.95, "Bollywood")
        or None if the artist is not in the curated map.

    Only the curated map (conf 0.95) satisfies the default 0.90 bulk-resolve
    threshold; KB-only entries (0.85) will be reported as skipped.
    """
    if not artist_name:
        return None
    key = _normalize(artist_name)
    hit = NEEDS_REVIEW_RESOLUTION_MAP.get(key)
    if hit:
        return hit
    # Multilingual normalization fallback
    canonical = normalize_multilingual_artist(artist_name)
    if canonical != artist_name:
        key2 = _normalize(canonical)
        hit = NEEDS_REVIEW_RESOLUTION_MAP.get(key2)
        if hit:
            return hit
    return None
