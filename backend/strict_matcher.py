"""
STRICT YouTube Result Matcher for Ingest Playlist
Ensures ONLY correct, full, original songs are downloaded.
Rejects remixes, karaoke, instrumental, covers, clips, and unofficial content.

Production-grade matching logic with strict validation.
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
try:
    from thefuzz import fuzz as _fuzz
    _FUZZY_AVAILABLE = True
except ImportError:
    from difflib import SequenceMatcher as _SequenceMatcher  # type: ignore
    _FUZZY_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Maximum absolute duration difference (seconds) before a candidate is rejected.
# Increase this constant if valid tracks are being rejected due to timing variation.
STRICT_DURATION_TOLERANCE_SEC = 15

# ═══════════════════════════════════════════════════════════════════
# STRICT REJECTION KEYWORDS — mandatory filtering
# ═══════════════════════════════════════════════════════════════════

REJECT_KEYWORDS = [
    # Remixes and variations
    "remix", "remixed", "rmx",

    # Singing-related rejections
    "karaoke", "cover", "covers", "covering",

    # Instrumental versions
    "instrumental", "inst", "beat",

    # Modified/slowed media
    "lofi", "lo-fi", "slowed", "slownd", "reverb", "reverbed",
    "8d", "8d audio", "nightcore", "nightcored",

    # Bass / speed modifications
    "bass boosted", "sped up",

    # Platform clips
    "tiktok",

    # Video/live versions (not full audio)
    "live", "live version", "live performance", "live concert",

    # Partial content
    "edit", "edited", "version", "mix", "mixed",
    "short", "short version", "clip", "trailer", "preview",
    "acapella", "acappella", "vocals only",

    # Label/type indicators (not full songs)
    "intro", "outro", "interlude", "skit",
]

# ═══════════════════════════════════════════════════════════════════
# REQUIRED OFFICIAL SIGNALS
# At least one of these must appear in the YouTube title,
# UNLESS the uploader/channel name closely matches the artist name
# (which indicates an official artist channel upload).
# ═══════════════════════════════════════════════════════════════════

ALLOWED_HINTS = [
    "official audio",
    "official video",
    "official music video",
    "official",
    "audio",
]

# Title cleaning patterns — remove noise before matching
TITLE_NOISE_PATTERNS = [
    (r'\s*\(official\s+(video|audio|lyric|lyrics|music\s+video)\).*$', '', re.IGNORECASE),
    (r'\s*\(hd\).*$', '', re.IGNORECASE),
    (r'\s*\(4k\).*$', '', re.IGNORECASE),
    (r'\s*\[official.*?\].*$', '', re.IGNORECASE),
    (r'\s*\[hd\].*$', '', re.IGNORECASE),
    (r'\s*-\s*(official|audio|music)\s*(video|audio).*$', '', re.IGNORECASE),
    (r'\s*feat\.?.*$', '', re.IGNORECASE),  # featured artists often confuse matching
]

# ═══════════════════════════════════════════════════════════════════
# CORE MATCHING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════


def clean_title(title: str) -> str:
    """
    Clean YouTube title by removing noise patterns and extra whitespace.

    Args:
        title: Raw YouTube title

    Returns:
        Cleaned title suitable for similarity matching
    """
    if not title or not isinstance(title, str):
        return ""

    cleaned = title.strip()

    # Apply noise patterns
    for pattern, replacement, flags in TITLE_NOISE_PATTERNS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=flags)

    # Remove leading/trailing spaces and extra whitespace
    cleaned = " ".join(cleaned.split()).strip()

    return cleaned


def has_reject_keyword(title: str) -> Optional[str]:
    """
    Check if title contains any reject keyword.
    Case-insensitive, word-boundary matching to avoid false positives.

    Args:
        title: YouTube title to check

    Returns:
        Matched keyword if found, None otherwise
    """
    if not title:
        return None

    title_lower = title.lower()

    for keyword in REJECT_KEYWORDS:
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, title_lower):
            return keyword

    return None


def _fuzzy_ratio(a: str, b: str) -> float:
    """
    Return normalized similarity score (0.0–1.0) between two strings.

    Uses thefuzz token_set_ratio when available (handles token re-ordering well),
    otherwise falls back to SequenceMatcher.
    """
    if not a or not b:
        return 0.0
    a = " ".join(a.lower().split())
    b = " ".join(b.lower().split())
    if not a or not b:
        return 0.0
    if _FUZZY_AVAILABLE:
        return _fuzz.token_set_ratio(a, b) / 100.0
    return _SequenceMatcher(None, a, b).ratio()


def string_similarity(text_a: str, text_b: str) -> float:
    """Backward-compatible alias for _fuzzy_ratio."""
    return _fuzzy_ratio(text_a, text_b)


def duration_match(actual_duration_sec: int, expected_duration_sec: int) -> Tuple[bool, float]:
    """
    Validate duration is within acceptable bounds.

    Two-stage check:
    1. Ratio guard  — rejects completely wrong durations (< 0.7× or > 1.5× expected)
    2. Absolute guard — rejects within-range but still too-different durations
                        (diff > STRICT_DURATION_TOLERANCE_SEC)

    Args:
        actual_duration_sec:   YouTube video duration in seconds
        expected_duration_sec: Spotify track duration in seconds

    Returns:
        Tuple of (is_valid, score) where score 0.0–1.0 reflects closeness
    """
    if not actual_duration_sec or not expected_duration_sec:
        # No duration info — neutral
        return True, 0.5

    ratio = actual_duration_sec / expected_duration_sec

    # Stage 1: hard-reject wildly wrong durations
    if ratio < 0.7 or ratio > 1.5:
        return False, 0.0

    # Stage 2: absolute tolerance check
    diff = abs(actual_duration_sec - expected_duration_sec)
    if diff > STRICT_DURATION_TOLERANCE_SEC:
        return False, 0.0

    # Score: 1.0 at exact match, approaches 0 at tolerance boundary
    score = 1.0 - (diff / STRICT_DURATION_TOLERANCE_SEC)
    return True, max(0.0, min(1.0, score))


def score_candidate(
    yt_title: str,
    actual_duration_sec: Optional[int],
    spotify_title: str,
    artist: str,
    expected_duration_sec: Optional[int],
    uploader: Optional[str] = None,
) -> Tuple[float, List[str]]:
    """
    Score a YouTube candidate for download suitability.

    Pipeline:
      1. Reject forbidden keywords
      2. Duration hard-reject (ratio + absolute tolerance)
      3. Require official signal (bypass if channel matches artist)
      4. Fuzzy title matching (thefuzz token_set_ratio)
      5. Fuzzy artist matching (title mention + channel name)
      6. Official bonus (+0.1)

    Scoring formula:
      final = (0.7 × title_score) + (0.3 × artist_score) + official_bonus
      Minimum passing threshold is enforced in select_best_candidate (default 0.6).

    Args:
        yt_title:             YouTube video title
        actual_duration_sec:  Video duration in seconds
        spotify_title:        Spotify track title
        artist:               Artist name
        expected_duration_sec: Expected duration in seconds
        uploader:             YouTube channel/uploader name (used for artist matching)

    Returns:
        Tuple of (score, rejection_reasons)
    """
    rejections = []

    if not yt_title:
        rejections.append("No title")
        return 0.0, rejections

    yt_lower = yt_title.lower()

    # STEP 1: Reject on forbidden keywords
    rejected_keyword = has_reject_keyword(yt_title)
    if rejected_keyword:
        logger.warning(f"Rejected (forbidden keyword '{rejected_keyword}'): {yt_title}")
        rejections.append(f"Contains forbidden keyword: {rejected_keyword}")
        return 0.0, rejections

    # STEP 2: Duration hard-reject
    if expected_duration_sec and actual_duration_sec:
        dur_valid, dur_score = duration_match(actual_duration_sec, expected_duration_sec)
        if not dur_valid:
            diff = abs(actual_duration_sec - expected_duration_sec)
            logger.warning(
                f"Rejected (duration mismatch, diff={diff}s): "
                f"{yt_title} ({actual_duration_sec}s vs expected {expected_duration_sec}s)"
            )
            rejections.append(
                f"Duration {actual_duration_sec}s too far from expected {expected_duration_sec}s "
                f"(diff={diff}s, tolerance={STRICT_DURATION_TOLERANCE_SEC}s)"
            )
            return 0.0, rejections
    else:
        dur_score = 0.5  # Unknown duration — neutral

    # STEP 3: Require official signal
    # Bypass this check when the uploader name closely matches the artist
    # (i.e., the upload is from the official artist channel).
    uploader_is_artist = False
    if uploader and artist:
        chan_score = _fuzzy_ratio(artist, uploader)
        uploader_is_artist = chan_score >= 0.7

    if not uploader_is_artist:
        if not any(hint in yt_lower for hint in ALLOWED_HINTS):
            logger.warning(f"Rejected (no official signal): {yt_title}")
            rejections.append(
                "No official signal: 'official'/'audio' absent from title "
                "and channel name does not match artist"
            )
            return 0.0, rejections

    # STEP 4: Clean titles and compute fuzzy title score
    clean_yt = clean_title(yt_title)
    clean_sp = clean_title(spotify_title)
    title_score = _fuzzy_ratio(clean_sp, clean_yt)

    # Hard-reject on very low title similarity (likely wrong song entirely)
    if title_score < 0.3:
        logger.warning(
            f"Rejected (title mismatch {title_score:.2f}): "
            f"'{clean_sp}' vs '{clean_yt}'"
        )
        rejections.append(
            f"Title too different: '{clean_sp}' vs '{clean_yt}' (score={title_score:.2f})"
        )
        return 0.0, rejections

    # STEP 5: Fuzzy artist score — check both title text and uploader/channel
    artist_in_title = _fuzzy_ratio(artist, yt_lower)
    if uploader:
        artist_via_channel = _fuzzy_ratio(artist, uploader)
        artist_score = max(artist_in_title, artist_via_channel)
    else:
        artist_score = artist_in_title

    # STEP 6: Official content bonus
    official_bonus = 0.1 if "official" in yt_lower else 0.0

    # STEP 7: Final weighted score
    final_score = (0.7 * title_score) + (0.3 * artist_score) + official_bonus
    final_score = max(0.0, min(1.0, final_score))

    logger.debug(
        f"Score: title={title_score:.2f} artist={artist_score:.2f} "
        f"official_bonus={official_bonus:.2f} → final={final_score:.2f} | '{yt_title}'"
    )

    return final_score, rejections


def select_best_candidate(
    candidates: List[Dict],
    spotify_title: str,
    artist: str,
    expected_duration_sec: Optional[int],
    min_score: float = 0.6,
) -> Tuple[Optional[Dict], str]:
    """
    Select the best candidate from YouTube search results.

    Scores every candidate via score_candidate() and returns the highest-scoring
    one that meets the minimum threshold (default 0.6 = 60% confidence).
    If no candidate passes, returns (None, reason) so the caller can skip the track.

    Args:
        candidates:            List of YouTube result dicts (title, duration, url, uploader, …)
        spotify_title:         Spotify track title
        artist:                Artist name
        expected_duration_sec: Expected duration in seconds
        min_score:             Minimum acceptable score (0.0–1.0)

    Returns:
        Tuple of (best_candidate_dict, detail_string) or (None, reason_string) if no match
    """
    if not candidates:
        return None, "No YouTube search results available"

    scored = []
    for i, candidate in enumerate(candidates):
        yt_title = candidate.get("title", "")
        duration = candidate.get("duration")
        uploader = candidate.get("uploader", "")

        score, rejections = score_candidate(
            yt_title,
            duration,
            spotify_title,
            artist,
            expected_duration_sec,
            uploader,
        )

        scored.append({
            "candidate": candidate,
            "score": score,
            "rejections": rejections,
            "index": i,
        })

        dur_str = f"{duration}s" if duration else "unknown"
        logger.info(
            f"  Candidate #{i+1}: \"{yt_title}\" ({dur_str}) → score={score:.3f}"
        )
        for reason in rejections:
            logger.warning(f"    ✗ {reason}")

    # Sort highest score first
    scored.sort(key=lambda x: x["score"], reverse=True)

    best = scored[0]
    if best["score"] < min_score:
        reasons = best["rejections"] or ["Score below threshold, no good match found"]
        reason_str = " | ".join(reasons)
        logger.warning(
            f"No acceptable candidate (best score={best['score']:.3f} < {min_score}): {reason_str}"
        )
        return None, (
            f"Best candidate scored {best['score']:.3f} "
            f"(below {min_score} threshold): {reason_str}"
        )

    selected = best["candidate"]
    yt_title = selected.get("title", "Unknown")
    logger.info(f"Selected: \"{yt_title}\" | Score: {best['score']:.2f}")

    return selected, f"Selected candidate with score={best['score']:.3f}"


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
