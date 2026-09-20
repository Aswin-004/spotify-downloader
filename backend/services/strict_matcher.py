"""
STRICT YouTube Result Matcher for Ingest Playlist
Ensures ONLY correct, full, original songs are downloaded.
Rejects remixes, karaoke, instrumental, covers, clips, and unofficial content.

Production-grade matching logic with multi-factor scoring.
Uses thefuzz for fuzzy token matching and loguru for structured logging.
"""
import re
import logging
from typing import Optional, Tuple, List, Dict

# Use loguru if available, fall back to stdlib logger
try:
    from loguru import logger
except ImportError:
    logger = logging.getLogger(__name__)  # type: ignore[assignment]

# Use thefuzz for better token-order-independent fuzzy matching;
# fall back to SequenceMatcher if not installed.
from difflib import SequenceMatcher as _SequenceMatcher  # used by the stdlib fallback below

try:
    from thefuzz import fuzz as _fuzz
    _FUZZY_AVAILABLE = True
except ImportError:
    _FUZZY_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Hard duration ceiling — reject any candidate with diff > this many seconds
# Applied as final validation BEFORE download (Step 10).
#
# PHASE 2 HARDENING: was 90s. duration_score()'s own tier table (below)
# already treats any diff > 60s as 0.0 value ("no signal") — but at 90s
# this hard gate was 30s looser than that, so a candidate could be up to
# 89s off and still pass: dur_score=0.0 zeroes out the 0.2-weight duration
# term, yet a strong title+artist score alone (e.g. title=1.0, artist=1.0
# -> final=0.5+0.3+0+0+0=0.80) clears min_score=0.40 with no duration
# check ever stopping it. Tightened to 60s so this hard gate can never be
# looser than the point duration_score() itself already calls "no match" —
# reusing an existing code-defined boundary, not an invented number.
HARD_DURATION_LIMIT_SEC = 60

# ARTIST GATE — reject candidates where the artist name has essentially no
# overlap with the YouTube title/channel AND the title isn't a near-exact
# match. Without this, a high title_score alone (e.g. "Pinky (From Zanjeer)"
# vs KATSEYE's "PINKY UP") can pass min_score even though it's a completely
# different song by an unrelated artist.
ARTIST_GATE_MIN_SCORE = 0.35
ARTIST_GATE_TITLE_EXEMPTION = 0.90

# TITLE GATE (Phase 2) — symmetric counterpart to the artist gate above:
# reject when the title barely resembles the request, even if the artist
# matches perfectly (a strong artist score alone doesn't identify WHICH
# song by that artist this is). Deliberately reuses ARTIST_GATE_MIN_SCORE's
# value rather than introducing a new threshold — see score_candidate().
TITLE_GATE_MIN_SCORE = ARTIST_GATE_MIN_SCORE

# ═══════════════════════════════════════════════════════════════════
# STRICT REJECTION KEYWORDS — mandatory hard filter (Step 2)
# Concise list targeting remixes, lofi, karaoke, and incorrect content.
# ═══════════════════════════════════════════════════════════════════

REJECT_KEYWORDS = [
    "karaoke", "instrumental", "lofi", "lo-fi",
    "slowed", "reverb", "8d", "nightcore",
    "cover",
    "bass boosted", "sped up", "tiktok", "clip",
    "mashup", "parody",
    # Unlabelled-instrumental detection — BGM/background tracks often lack
    # "instrumental" in title but are still vocal-free
    "bgm", "background music", "ringtone", "no vocals",
    "minus one", "backing track", "music only",
    # Spelling / plural variants. Matching is word-boundary based (\bword\b), so
    # "instrumental" does NOT match "Instrumentals" and "karaoke" does NOT match
    # the common misspellings below — each needs its own entry.
    "instrumentals", "karaokes", "karoke", "kareoke", "karaoké",
    "off vocal", "off-vocal", "vocals removed", "vocal removed",
    "without vocals", "without vocal", "no vocal", "vocal free", "vocalless",
    "sing along", "singalong", "beat only",
    # Desi-catalog re-recordings that are sold/uploaded as the "same" song
    "jhankar", "unplugged", "recreated", "recreation", "remake",
    "female version", "male version", "reprise",
    "piano version", "flute version", "guitar version", "violin version", "sitar version",
]


# QUALITY UPGRADE — Pre-scoring blacklist filter (applied before candidate scoring)
BLACKLISTED_KEYWORDS = [
    'cover', 'karaoke', 'nightcore',
    'sped up', 'reverb', 'slowed',
    'mashup', 'parody',
    # Instrumental detection (pre-score, catches obvious cases before scoring)
    'instrumental', 'bgm', 'ringtone', 'no vocals', 'minus one',
    'instrumentals', 'karaokes', 'karoke', 'kareoke', 'karaoké',
    'off vocal', 'vocals removed', 'without vocals', 'vocal free', 'sing along',
]

# CHANNEL-NAME rejection. Karaoke / backing-track uploaders very often copy the
# original artist and song title verbatim (for search reach) and leave "karaoke"
# out of the VIDEO title entirely — the tell is the CHANNEL name. Title-only
# filtering can't see that, and the artist gate is *satisfied* by it (the artist
# name is in the title) while the duration matches within a couple of seconds
# (it's a re-recording of the same arrangement). Exempted, like the title
# keywords, when the Spotify title itself asks for the word.
REJECT_CHANNEL_KEYWORDS = [
    "karaoke", "karaokes", "instrumental", "instrumentals", "backing track",
    "backing tracks", "minus one", "sing king", "karafun", "ameritz",
    "vocal free", "cover", "covers",
]

# YouTube auto-generates "<Artist> - Topic" channels from the distributor/label's
# own delivery, so they carry the actual studio release audio — not a
# re-recording. A modest, capped bonus: it must never rescue a wrong-song match
# (the title/artist/duration gates still run first).
TOPIC_CHANNEL_BONUS = 0.10


def is_blacklisted(title, original_track_title):  # QUALITY UPGRADE
    """Return True if the candidate title contains a blacklisted keyword
    that does NOT appear in the original Spotify title.

    Uses word-boundary regex matching (same approach as has_reject_keyword()
    above) instead of plain substring containment — plain `keyword in title_lower`
    made 'cover' match inside ordinary words like "Discovery", "Undercover", or
    "Recovered", silently rejecting legitimate results before scoring ever ran.
    """  # QUALITY UPGRADE
    title_lower = title.lower()  # QUALITY UPGRADE
    original_lower = original_track_title.lower() if original_track_title else ""  # QUALITY UPGRADE
    for keyword in BLACKLISTED_KEYWORDS:  # QUALITY UPGRADE
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, title_lower) and not re.search(pattern, original_lower):  # QUALITY UPGRADE
            return True  # QUALITY UPGRADE
    return False  # QUALITY UPGRADE

# Title cleaning patterns — remove noise before scoring (Step 1)
TITLE_NOISE_PATTERNS = [
    (r'\(official\s*(video|audio|lyric|lyrics|music\s*video)\)', '', re.IGNORECASE),
    (r'\[official\s*(video|audio|lyric|lyrics|music\s*video)\]', '', re.IGNORECASE),
    (r'\(lyrics?\)', '', re.IGNORECASE),
    (r'\[lyrics?\]', '', re.IGNORECASE),
    (r'\(hd\)', '', re.IGNORECASE),
    (r'\[hd\]', '', re.IGNORECASE),
    (r'\(4k\)', '', re.IGNORECASE),
    (r'\(audio\)', '', re.IGNORECASE),
    (r'\[audio\]', '', re.IGNORECASE),
    (r'\s*-\s*(official|audio|music)\s*(video|audio)?\s*$', '', re.IGNORECASE),
    (r'\s*feat\.?\s+.*$', '', re.IGNORECASE),
    # ── Event-rip / DJ-city noise ─────────────────────────────────────────
    (r'\bdjcity\b',            '', re.IGNORECASE),
    (r'\bfree\s*download\b',   '', re.IGNORECASE),
    (r'\bout\s*now\b',         '', re.IGNORECASE),
    (r'\bofficial\s*video\b',  '', re.IGNORECASE),
    (r'\bvisualizer\b',        '', re.IGNORECASE),
    (r'\b320\s*kbps\b',        '', re.IGNORECASE),
    (r'\bdj\s*version\b',      '', re.IGNORECASE),
    (r'\byt\s*rip\b',          '', re.IGNORECASE),
    (r'\byoutube\s*rip\b',     '', re.IGNORECASE),
]

# ═══════════════════════════════════════════════════════════════════
# VERSION / REMIX VALIDATION (Phase 2 hardening)
# ═══════════════════════════════════════════════════════════════════
# REJECT_KEYWORDS above already hard-rejects "cover", "karaoke", "mashup",
# "instrumental" etc. (Step 2), each exempted when the Spotify title itself
# requests that exact word. But it does NOT cover the "same-family, wrong-
# edition" words below — remix, radio edit, extended mix, VIP, live,
# acoustic, bootleg, rework — because a token_set_ratio title_score does
# not penalize a candidate for having EXTRA words the query didn't ask for
# ("Song Title (XYZ Remix)" vs Spotify "Song Title" still scores high on
# title alone). Left unchecked, a plausible-duration remix/live/acoustic
# cut of the right song can outscore min_score and get downloaded in place
# of the original studio version.
#
# Adapted (not copied) from legacy_identification_service.py's
# _REMIX_TOKEN_SET / _get_remix_flags(): same word-boundary token-set-
# intersection technique, reused because it's already proven in this
# codebase. Differences from the legacy set, both deliberate:
#   - "live" and "acoustic" ADDED — the legacy set lacks both, but the
#     task/product goal ("ONLY correct, full, original songs") and this
#     phase's own test matrix explicitly require rejecting live/acoustic
#     cuts, so the set must cover them.
#   - "instrumental" DROPPED — already a hard, unconditional REJECT_KEYWORDS
#     entry (Step 2 runs before this gate), so duplicating it here would be
#     dead/redundant, not a functional gap.
VERSION_TOKENS = frozenset({
    "remix", "vip", "edit", "extended", "club", "radio", "mix",
    "bootleg", "dub", "flip", "rework", "live", "acoustic",
})
_VERSION_WORD_RE = re.compile(r"\b([a-zA-Z]+)\b")


def _version_tokens(text: str) -> frozenset:
    """Extract version/edition tokens present in text (bracket-agnostic)."""
    if not text:
        return frozenset()
    return frozenset(m.group(1).lower() for m in _VERSION_WORD_RE.finditer(text)) & VERSION_TOKENS


def version_mismatch(yt_title: str, spotify_title: str) -> Optional[frozenset]:
    """
    Return the set of version tokens present in the candidate title but NOT
    requested by the Spotify title, or None if there's no mismatch.

    Symmetric to has_reject_keyword's exemption logic: a version word is
    only a problem when the CANDIDATE has it and the QUERY didn't ask for
    it. "Si Ai (Marshmello Remix)" vs Spotify title "Si Ai - Marshmello
    Remix" is a legitimate accept — both sides carry "remix". "Song Title"
    vs a candidate "Song Title (Bootleg Remix)" is not — the candidate
    introduces an edition the listener never asked for.
    """
    cand = _version_tokens(yt_title)
    if not cand:
        return None
    requested = _version_tokens(spotify_title)
    extra = cand - requested
    return extra or None


# ═══════════════════════════════════════════════════════════════════
# CORE MATCHING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════


def clean_title(title: str) -> str:
    """
    Clean and normalize a title for scoring comparison.
    Step 1: lowercase, remove noise tags, strip extra spaces.
    """
    if not title or not isinstance(title, str):
        return ""

    cleaned = title.strip().lower()

    for pattern, replacement, flags in TITLE_NOISE_PATTERNS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=flags)

    cleaned = " ".join(cleaned.split()).strip()
    return cleaned


def has_reject_keyword(title: str, exempt_from: str = "", keywords: Optional[List[str]] = None) -> Optional[str]:
    """
    Check if title contains any reject keyword.
    Case-insensitive, word-boundary matching to avoid false positives.

    Keywords that also appear in exempt_from are skipped — this allows
    tracks whose Spotify title already contains e.g. "remix" to match
    YouTube results that naturally include the same word.

    Args:
        title: YouTube title (or channel name) to check
        exempt_from: Reference text (e.g. Spotify title) whose keywords are allowed
        keywords: Keyword list to check against (default: REJECT_KEYWORDS)

    Returns:
        Matched keyword if found, None otherwise
    """
    if not title:
        return None

    title_lower = title.lower()
    exempt_lower = exempt_from.lower() if exempt_from else ""

    for keyword in (REJECT_KEYWORDS if keywords is None else keywords):
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, title_lower):
            # Skip this keyword if the Spotify title itself contains it
            if exempt_lower and re.search(pattern, exempt_lower):
                continue
            return keyword

    return None


_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _token_set_ratio_fallback(a: str, b: str) -> float:
    """
    Pure-stdlib equivalent of thefuzz.fuzz.token_set_ratio (0.0–1.0).

    Used only when thefuzz isn't installed. The previous fallback was a plain
    SequenceMatcher ratio over the whole strings, which behaves completely
    differently: an artist name appearing VERBATIM inside a longer title
    ("pritam" in "pritam - make some noise for the desi boyz") scored 0.25
    instead of 1.0, so the artist gate then rejected correct uploads and the
    pipeline drifted to worse candidates — silently, and only in environments
    missing the package (e.g. a terminal not using the project .venv).
    Same algorithm as thefuzz: strip punctuation, split into token sets, and
    compare the shared tokens against each side's leftovers.
    """
    ta = set(_NON_WORD_RE.sub(" ", a).split())
    tb = set(_NON_WORD_RE.sub(" ", b).split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    only_a = " ".join(sorted(ta - tb))
    only_b = " ".join(sorted(tb - ta))
    combined_a = f"{inter} {only_a}".strip()
    combined_b = f"{inter} {only_b}".strip()

    def _r(x: str, y: str) -> float:
        return _SequenceMatcher(None, x, y).ratio() if x and y else 0.0

    return max(_r(inter, combined_a), _r(inter, combined_b), _r(combined_a, combined_b))


if not _FUZZY_AVAILABLE:
    logger.warning(
        "[strict_matcher] 'thefuzz' is not installed in this Python environment — using a "
        "built-in token-set fallback. Matching is equivalent but slower; run "
        "`pip install -r requirements.txt` in the interpreter that launches the app."
    )


def _fuzzy_ratio(a: str, b: str) -> float:
    """
    Return normalized similarity score (0.0–1.0) between two strings.

    Uses thefuzz token_set_ratio when available (handles token re-ordering well),
    otherwise a stdlib re-implementation of the same token-set algorithm.
    """
    if not a or not b:
        return 0.0
    a = " ".join(a.lower().split())
    b = " ".join(b.lower().split())
    if not a or not b:
        return 0.0
    if _FUZZY_AVAILABLE:
        return _fuzz.token_set_ratio(a, b) / 100.0
    return _token_set_ratio_fallback(a, b)


def string_similarity(text_a: str, text_b: str) -> float:
    """Backward-compatible alias for _fuzzy_ratio."""
    return _fuzzy_ratio(text_a, text_b)


# Version tags that name the same recording in a different wrapper. A NAMED remix
# ("(Charlotte de Witte Remix)") is deliberately absent: it is a different recording.
_BENIGN_VERSION_RE = re.compile(
    r"\s*[\(\[]\s*(?:original(?:\s+(?:mix|version))?|extended(?:\s+(?:mix|version))?"
    r"|radio\s+(?:edit|mix|version)|club\s+(?:mix|edit)|album\s+version|single\s+version"
    r"|(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?|explicit|clean)\s*[\)\]]"
    r"|\s+-\s+(?:original\s+mix|extended\s+mix|radio\s+edit|(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)\s*$",
    re.IGNORECASE,
)


_BRACKET_GROUP_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+.*$")


def base_title(title: str) -> str:
    """The song's core name: cleaned, with any (...) / [...] group and a trailing ' - suffix'
    removed — 'Chhote Chhote Peg (From "Yaariyan")' -> 'chhote chhote peg'."""
    t = _DASH_SUFFIX_RE.sub("", _BRACKET_GROUP_RE.sub("", clean_title(title)))
    return " ".join(re.sub(r"[()\[\]]", "", t).split())


def same_song_title(a: str, b: str, threshold: float = 0.85) -> bool:
    """
    True when two titles name the same song once edition tags are ignored. Deliberately strict
    about the CORE name: 'Aura' is not 'Aura of Ustaad', 'WOH' is not 'Woh Ladki Jo - Sped Up'.
    A false 'different' just leaves a track unidentified; a false 'same' would trust the wrong
    metadata, so the bias is towards 'different'.
    """
    ba, bb = base_title(a), base_title(b)
    if not ba or not bb:
        return False
    return _SequenceMatcher(None, ba, bb).ratio() >= threshold


def title_similarity(a: str, b: str) -> float:
    """
    Order-sensitive 0.0–1.0 similarity of two song titles after clean_title(), ignoring
    benign version tags ("(Original Mix)", "(Radio Edit)", "- Remastered 2011").

    Use this — not _fuzzy_ratio — to decide whether two titles name the SAME song.
    token_set_ratio scores 1.0 whenever one title's words are a subset of the other's
    ("Takes" vs "Takes Me Home"), which is right for a search-relevance gate but wrong
    for identity: an external database hit for the wrong song would be accepted.
    """
    ca = _BENIGN_VERSION_RE.sub("", clean_title(a)).strip()
    cb = _BENIGN_VERSION_RE.sub("", clean_title(b)).strip()
    if not ca or not cb:
        return 0.0
    return _SequenceMatcher(None, ca, cb).ratio()


def duration_score(actual_sec: Optional[int], expected_sec: Optional[int]) -> float:
    """
    CHANGED — Smart tiered duration scoring (Step 4).
    Returns 0.0–1.0 based on how close the durations are.
    ±2 s = perfect.  ±10 s = OK.  ±25 s = marginal.  >25 s = reject.
    Heavy -50 penalty applied when diff > 2 s (via score_candidate).
    """
    if not actual_sec or not expected_sec:
        # Unknown duration (e.g. SoundCloud scsearch candidates under
        # extract_flat=True never report a length) — we have no data to hard-reject
        # on, but also must not treat "unknown" as equivalent to a verified match.
        # 0.3 sits below the "acceptable" tier (0.5, diff<=10s) so an unverified
        # candidate always scores worse than one with a confirmed correct duration,
        # while still being eligible to pass if title/artist signals are strong.
        return 0.3

    diff = abs(actual_sec - expected_sec)

    # Tiered scoring — ±2/5/10/30/60
    if diff <= 2:
        return 1.0  # perfect match
    elif diff <= 5:
        return 0.8  # good match
    elif diff <= 10:
        return 0.5  # acceptable
    elif diff <= 30:
        return 0.2  # poor match
    elif diff <= 60:
        return 0.1  # marginal — within tolerance
    else:
        return 0.0


def duration_match(actual_duration_sec: int, expected_duration_sec: int) -> Tuple[bool, float]:
    """
    Backward-compatible duration check.
    Uses the new tiered scoring internally.
    """
    score = duration_score(actual_duration_sec, expected_duration_sec)
    if score == 0.0 and actual_duration_sec and expected_duration_sec:
        return False, 0.0
    return True, score


def final_duration_check(actual_sec: Optional[int], expected_sec: Optional[int]) -> bool:
    """
    Step 10: Final hard validation before download.
    Reject if duration difference exceeds HARD_DURATION_LIMIT_SEC (90s).
    """
    if not actual_sec or not expected_sec:
        return True  # Can't check — allow
    return abs(actual_sec - expected_sec) <= HARD_DURATION_LIMIT_SEC


def score_candidate(
    yt_title: str,
    actual_duration_sec: Optional[int],
    spotify_title: str,
    artist: str,
    expected_duration_sec: Optional[int],
    uploader: Optional[str] = None,
    channel_is_verified: bool = False,
) -> Tuple[float, List[str]]:
    """
    Score a YouTube candidate using multi-factor scoring.

    Pipeline:
      Step 2: Hard filter — reject forbidden keywords
      Step 2b: Version/edition mismatch gate (remix, live, acoustic, ...)
      Step 3: Multi-factor scoring (title, artist, channel)
      Step 4: Smart duration scoring (tiered)
      Step 4b: Title gate (reject if title_score too low, any artist)
      Step 4c: Artist gate (reject if artist_score too low, any title)
      Step 5: Weighted final score
      Step 6: Official boost
      Step 10: Hard duration ceiling (HARD_DURATION_LIMIT_SEC)

    Formula:
      final = 0.5 * title_score + 0.3 * artist_score + 0.2 * duration_score + official_boost

    Returns:
        Tuple of (score, rejection_reasons)
    """
    rejections: List[str] = []

    if not yt_title:
        rejections.append("No title")
        return 0.0, rejections

    yt_lower = yt_title.lower()

    # ── STEP 2: Hard filter on forbidden keywords ──
    # Exempt keywords that already appear in the Spotify title itself
    # (e.g. if Spotify says "Si Ai - Marshmello Remix", allow "remix" in YT results)
    sp_lower = spotify_title.lower() if spotify_title else ""
    rejected_keyword = has_reject_keyword(yt_title, exempt_from=sp_lower)
    if rejected_keyword:
        rejections.append(f"Contains forbidden keyword: {rejected_keyword}")
        log_rejection(f"forbidden keyword '{rejected_keyword}'", yt_title)
        return 0.0, rejections

    # ── STEP 2a: Channel-name filter ──
    # Karaoke/backing-track uploaders usually copy the artist + song title
    # verbatim and put "karaoke" only in the CHANNEL name, so the title filter
    # above can't see them (and the artist/duration gates below would pass them).
    if uploader:
        rejected_channel_kw = has_reject_keyword(
            uploader, exempt_from=sp_lower, keywords=REJECT_CHANNEL_KEYWORDS,
        )
        if rejected_channel_kw:
            rejections.append(
                f"Uploader channel '{uploader}' looks like a karaoke/instrumental/cover "
                f"channel (keyword: {rejected_channel_kw})"
            )
            log_rejection(f"channel keyword '{rejected_channel_kw}' ({uploader})", yt_title)
            return 0.0, rejections

    # ── STEP 2b: Version/edition mismatch gate ──
    # Catches remix/VIP/edit/extended/radio/bootleg/rework/live/acoustic
    # candidates that REJECT_KEYWORDS doesn't cover (see VERSION_TOKENS).
    mismatched_versions = version_mismatch(yt_title, spotify_title)
    if mismatched_versions:
        extras = ", ".join(sorted(mismatched_versions))
        rejections.append(
            f"Version mismatch: candidate is a '{extras}' edition not requested "
            f"by the Spotify title"
        )
        log_rejection(f"version mismatch ({extras})", yt_title)
        return 0.0, rejections

    # ── STEP 10 (early): Hard duration ceiling ──
    # Intentionally skipped when actual_duration_sec is unknown (no data to hard-reject
    # against) — this is NOT an outright pass though: duration_score() below still
    # applies a reduced-confidence penalty (0.3, below the 0.5 "acceptable" tier) to the
    # weighted final score for unknown-duration candidates, so a wildly wrong-length
    # result still needs a strong title+artist match to clear min_score.
    if expected_duration_sec and actual_duration_sec:
        if not final_duration_check(actual_duration_sec, expected_duration_sec):
            diff = abs(actual_duration_sec - expected_duration_sec)
            rejections.append(
                f"Duration {actual_duration_sec}s too far from expected "
                f"{expected_duration_sec}s (diff={diff}s, limit={HARD_DURATION_LIMIT_SEC}s)"
            )
            log_rejection(f"duration diff {diff}s > {HARD_DURATION_LIMIT_SEC}s", yt_title)
            return 0.0, rejections

    # ── STEP 1 + 3: Clean titles and compute fuzzy scores ──
    clean_yt = clean_title(yt_title)
    clean_sp = clean_title(spotify_title)

    title_score = _fuzzy_ratio(clean_sp, clean_yt)
    artist_in_title = _fuzzy_ratio(artist, yt_lower)

    # Channel/uploader score
    channel_score = _fuzzy_ratio(artist, uploader) if uploader else 0.0
    artist_score = max(artist_in_title, channel_score)

    # ── STEP 4: Smart duration scoring ──
    dur_score = duration_score(actual_duration_sec, expected_duration_sec)

    # ── TITLE GATE (Phase 2 hardening): reject wrong-song matches where the
    # title barely resembles the request, even with the right artist ──
    # Confirmed false-positive: correct artist + wrong title + a middling
    # duration can still clear min_score, because the artist term's 0.3
    # weight partially buys back a weak title term (0.5 weight) — e.g.
    # title=0.20, artist=1.00, dur=0.20 -> final=0.10+0.30+0.04=0.44,
    # clears the live 0.40 threshold despite being a different song by the
    # same artist. Mirrors the existing ARTIST GATE's shape and reuses its
    # threshold (TITLE_GATE_MIN_SCORE == ARTIST_GATE_MIN_SCORE == 0.35) —
    # not a new invented number — rather than tuning the 0.5/0.3/0.2 weights
    # themselves, which would be a larger, less targeted change. No
    # exemption: an artist can release many different songs, so a strong
    # artist match legitimately says nothing about which song this is.
    if title_score < TITLE_GATE_MIN_SCORE:
        rejections.append(
            f"Title mismatch: title_score={title_score:.2f} < {TITLE_GATE_MIN_SCORE} "
            f"(artist={artist_score:.2f}, dur={dur_score:.2f}) — likely a different "
            f"song by the same artist/channel"
        )
        log_rejection(f"title mismatch (title_score={title_score:.2f})", yt_title)
        return 0.0, rejections

    # ── ARTIST GATE: reject wrong-song matches with no artist overlap ──
    # A high title_score from shared keywords (e.g. "Pinky (From Zanjeer)" vs
    # "PINKY UP") is not enough on its own — the artist must appear somewhere
    # in the title/channel unless the title is a near-exact match AND the
    # duration also lines up (two unrelated songs sharing a generic title
    # like "Criminal" rarely also share a duration).
    #
    # PHASE 2 HARDENING: the exemption previously fired at dur_score >= 0.5,
    # i.e. any duration within 10s (see duration_score() tiers). That's wide
    # enough that "Artist A - Song X" (requested) vs "Artist B - Song X"
    # (wrong artist, same generic title, coincidentally similar length) could
    # bypass the artist check entirely on title+duration alone — exactly the
    # confirmed false-positive shape from the forensic case file (title=0.50,
    # artist=0.35, dur=0.50 -> final=0.455, cleared the old 0.40 threshold).
    # Tightened to dur_score >= 1.0 (diff <= 2s — the tightest existing tier)
    # so the exemption only rescues the legitimate case it exists for: the
    # correct recording uploaded to a channel that doesn't name the artist
    # anywhere in title/uploader text (artist_score genuinely absent, not
    # contradicted). Two DIFFERENT recordings sharing an identical title AND
    # a sub-2-second duration by coincidence is not realistically preventable
    # without per-candidate audio analysis (fingerprinting), which is
    # explicitly out of scope for this phase — documented as a residual risk
    # in the Phase 2 report rather than papered over here.
    title_is_near_exact = title_score >= ARTIST_GATE_TITLE_EXEMPTION and dur_score >= 1.0
    if artist and artist_score < ARTIST_GATE_MIN_SCORE and not title_is_near_exact:
        rejections.append(
            f"Artist mismatch: artist_score={artist_score:.2f} < {ARTIST_GATE_MIN_SCORE} "
            f"and title/duration not a strong enough match "
            f"(title={title_score:.2f}, dur={dur_score:.2f}) "
            f"(likely a different song by '{uploader or yt_title}')"
        )
        log_rejection(f"artist mismatch (artist_score={artist_score:.2f}, title_score={title_score:.2f})", yt_title)
        return 0.0, rejections

    # ── STEP 6: Official boost ──
    official_bonus = 0.05 if "official" in yt_lower else 0.0

    # Verified channel boost (capped to prevent wrong tracks passing on badge alone)
    verified_bonus = 0.10 if channel_is_verified else 0.0

    # Uploader-based bonuses (conservative — badge ≠ correct track)
    uploader_lower = uploader.lower() if uploader else ""
    if 'official' in uploader_lower:
        official_bonus += 0.05  # official channel name
    if 'vevo' in uploader_lower:
        official_bonus += 0.10  # VEVO verified partner
    if uploader_lower.endswith(" - topic"):
        official_bonus += TOPIC_CHANNEL_BONUS  # auto-generated label-delivered studio audio

    # ── STEP 5: Weighted final score ──
    final = (0.5 * title_score) + (0.3 * artist_score) + (0.2 * dur_score) + official_bonus + verified_bonus
    final = max(0.0, min(1.0, final))

    logger.info(
        f"Candidate: \"{yt_title}\" | "
        f"title={title_score:.2f} artist={artist_score:.2f} dur={dur_score:.2f} "
        f"official={official_bonus:.2f} verified={verified_bonus:.2f} → score={final:.2f}"
    )

    return final, rejections


def select_best_candidate(
    candidates: List[Dict],
    spotify_title: str,
    artist: str,
    expected_duration_sec: Optional[int],
    min_score: float = 0.35,
) -> Tuple[Optional[Dict], float, str]:
    """
    Step 7+8: Accept candidates >= min_score, sort descending, pick best.

    PHASE 2: return signature widened from (candidate, reason) to
    (candidate, score, reason) so callers can compare a stage's best
    candidate against a candidate held from an earlier search stage
    (see downloader_service._download_from_youtube) instead of committing
    to the first stage that merely clears min_score. Verified exactly one
    call site existed before this change (downloader_service.py).
    """
    if not candidates:
        return None, 0.0, "No YouTube search results available"

    scored = []
    for i, candidate in enumerate(candidates):
        yt_title = candidate.get("title", "")
        duration = candidate.get("duration")
        uploader = candidate.get("uploader", "")

        # CHANGED: pass channel_is_verified for +30 boost
        verified = candidate.get("channel_is_verified", False)
        score, rejections = score_candidate(
            yt_title, duration, spotify_title, artist,
            expected_duration_sec, uploader, channel_is_verified=verified,
        )

        scored.append({
            "candidate": candidate,
            "score": score,
            "rejections": rejections,
            "index": i,
        })

        dur_str = f"{duration}s" if duration else "unknown"
        logger.info(f"  #{i+1}: \"{yt_title}\" ({dur_str}) → score={score:.3f}")
        for reason in rejections:
            logger.warning(f"    ✗ {reason}")

    # Step 8: Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    best = scored[0]
    if best["score"] < min_score:
        reasons = best["rejections"] or [f"Score {best['score']:.3f} below threshold {min_score}"]
        reason_str = " | ".join(reasons)
        logger.warning(f"No acceptable candidate (best={best['score']:.3f} < {min_score}): {reason_str}")
        return None, best["score"], f"Best candidate scored {best['score']:.3f} (below {min_score} threshold): {reason_str}"

    selected = best["candidate"]
    yt_title = selected.get("title", "Unknown")
    log_acceptance(yt_title, best["score"], selected.get("url"))
    logger.info(f"Selected: \"{yt_title}\" | Score: {best['score']:.2f}")

    return selected, best["score"], f"Selected candidate with score={best['score']:.3f}"


# ═══════════════════════════════════════════════════════════════════
# LOG HELPERS
# ═══════════════════════════════════════════════════════════════════

def log_rejection(reason: str, yt_title: str, youtube_url: Optional[str] = None):
    """Log a rejection with consistent format."""
    msg = f"🚫 REJECTED: {reason}"
    if yt_title:
        msg += f" | Title: \"{yt_title}\""
    if youtube_url:
        msg += f" | URL: {youtube_url}"
    logger.warning(msg)


def log_acceptance(yt_title: str, score: float, youtube_url: Optional[str] = None):
    """Log an acceptance with consistent format."""
    msg = f"✅ ACCEPTED: \"{yt_title}\" (score={score:.3f})"
    if youtube_url:
        msg += f" | {youtube_url}"
    logger.info(msg)
