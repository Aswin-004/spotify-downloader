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
try:
    from thefuzz import fuzz as _fuzz
    _FUZZY_AVAILABLE = True
except ImportError:
    from difflib import SequenceMatcher as _SequenceMatcher  # type: ignore
    _FUZZY_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Hard duration ceiling — reject any candidate with diff > this many seconds
# Applied as final validation BEFORE download (Step 10).
HARD_DURATION_LIMIT_SEC = 30

# ═══════════════════════════════════════════════════════════════════
# STRICT REJECTION KEYWORDS — mandatory hard filter (Step 2)
# Concise list targeting remixes, lofi, karaoke, and incorrect content.
# ═══════════════════════════════════════════════════════════════════

REJECT_KEYWORDS = [
    "remix", "karaoke", "instrumental", "lofi", "lo-fi",
    "slowed", "reverb", "8d", "nightcore",
    "cover", "edit", "version",
    "bass boosted", "sped up", "tiktok", "clip",
    "mashup", "parody",  # QUALITY UPGRADE
]


# QUALITY UPGRADE — Pre-scoring blacklist filter (applied before candidate scoring)
BLACKLISTED_KEYWORDS = [  # QUALITY UPGRADE
    'cover', 'karaoke', 'nightcore',  # QUALITY UPGRADE
    'sped up', 'reverb', 'slowed',  # QUALITY UPGRADE
    'remix', 'mashup', 'parody',  # QUALITY UPGRADE
]  # QUALITY UPGRADE


def is_blacklisted(title, original_track_title):  # QUALITY UPGRADE
    """Return True if the candidate title contains a blacklisted keyword
    that does NOT appear in the original Spotify title."""  # QUALITY UPGRADE
    title_lower = title.lower()  # QUALITY UPGRADE
    original_lower = original_track_title.lower() if original_track_title else ""  # QUALITY UPGRADE
    for keyword in BLACKLISTED_KEYWORDS:  # QUALITY UPGRADE
        if keyword in title_lower and keyword not in original_lower:  # QUALITY UPGRADE
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
]

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


def has_reject_keyword(title: str, exempt_from: str = "") -> Optional[str]:
    """
    Check if title contains any reject keyword.
    Case-insensitive, word-boundary matching to avoid false positives.

    Keywords that also appear in exempt_from are skipped — this allows
    tracks whose Spotify title already contains e.g. "remix" to match
    YouTube results that naturally include the same word.

    Args:
        title: YouTube title to check
        exempt_from: Reference text (e.g. Spotify title) whose keywords are allowed

    Returns:
        Matched keyword if found, None otherwise
    """
    if not title:
        return None

    title_lower = title.lower()
    exempt_lower = exempt_from.lower() if exempt_from else ""

    for keyword in REJECT_KEYWORDS:
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, title_lower):
            # Skip this keyword if the Spotify title itself contains it
            if exempt_lower and re.search(pattern, exempt_lower):
                continue
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


def duration_score(actual_sec: Optional[int], expected_sec: Optional[int]) -> float:
    """
    CHANGED — Smart tiered duration scoring (Step 4).
    Returns 0.0–1.0 based on how close the durations are.
    ±2 s = perfect.  ±10 s = OK.  ±25 s = marginal.  >25 s = reject.
    Heavy -50 penalty applied when diff > 2 s (via score_candidate).
    """
    if not actual_sec or not expected_sec:
        return 0.5  # Unknown duration — neutral

    diff = abs(actual_sec - expected_sec)

    # QUALITY UPGRADE: tighter tiered scoring — ±2/5/10/30
    if diff <= 2:  # QUALITY UPGRADE
        return 1.0  # QUALITY UPGRADE — perfect match
    elif diff <= 5:  # QUALITY UPGRADE
        return 0.8  # QUALITY UPGRADE — good match
    elif diff <= 10:  # QUALITY UPGRADE
        return 0.5  # QUALITY UPGRADE — acceptable
    elif diff <= 30:  # QUALITY UPGRADE
        return 0.2  # QUALITY UPGRADE — poor match
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
    Reject if duration difference exceeds HARD_DURATION_LIMIT_SEC (30s).
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
      Step 3: Multi-factor scoring (title, artist, channel)
      Step 4: Smart duration scoring (tiered)
      Step 5: Weighted final score
      Step 6: Official boost
      Step 10: Hard duration ceiling (30s)

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

    # ── STEP 10 (early): Hard duration ceiling ──
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

    # ── STEP 6: Official boost ──
    official_bonus = 0.05 if "official" in yt_lower else 0.0

    # ── CHANGED: Verified channel boost (+0.30 = +30 on 0–100 scale) ──
    verified_bonus = 0.30 if channel_is_verified else 0.0

    # QUALITY UPGRADE — uploader-based bonuses
    uploader_lower = uploader.lower() if uploader else ""  # QUALITY UPGRADE
    if 'official' in uploader_lower:  # QUALITY UPGRADE
        official_bonus += 0.15  # QUALITY UPGRADE — official channel name
    if 'vevo' in uploader_lower:  # QUALITY UPGRADE
        official_bonus += 0.20  # QUALITY UPGRADE — VEVO verified partner

    # ── CHANGED: Heavy penalty when duration exceeds ±2 s ──
    tight_penalty = 0.0
    if actual_duration_sec and expected_duration_sec:
        if abs(actual_duration_sec - expected_duration_sec) > 2:
            tight_penalty = -0.50  # heavy penalty

    # ── STEP 5: Weighted final score ──
    final = (0.5 * title_score) + (0.3 * artist_score) + (0.2 * dur_score) + official_bonus + verified_bonus + tight_penalty
    final = max(0.0, min(1.0, final))

    logger.info(
        f"Candidate: \"{yt_title}\" | "
        f"title={title_score:.2f} artist={artist_score:.2f} dur={dur_score:.2f} "
        f"official={official_bonus:.2f} verified={verified_bonus:.2f} tight_pen={tight_penalty:.2f} → score={final:.2f}"
    )

    return final, rejections


def select_best_candidate(
    candidates: List[Dict],
    spotify_title: str,
    artist: str,
    expected_duration_sec: Optional[int],
    min_score: float = 0.5,
) -> Tuple[Optional[Dict], str]:
    """
    Step 7+8: Accept candidates >= min_score, sort descending, pick best.
    """
    if not candidates:
        return None, "No YouTube search results available"

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
        return None, f"Best candidate scored {best['score']:.3f} (below {min_score} threshold): {reason_str}"

    selected = best["candidate"]
    yt_title = selected.get("title", "Unknown")
    log_acceptance(yt_title, best["score"], selected.get("url"))
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
