"""
genre_evidence.py
=================
Track-level genre classification by EVIDENCE VOTING.

Why this exists
---------------
The old chain took the first source that produced *any* genre and stopped: one Last.fm tag,
one MusicBrainz hit for whatever recording happened to rank first, or — worst — a Groq guess
made from the title text alone (which leans "House" for every unfamiliar electronic name).
Genre was also decided per ARTIST, so a Techno DJ's trance track followed the artist.

Here every available signal casts a weighted vote for a library folder label ("House",
"Techno", "Trance", "Bollywood", ...); a track is only classified when the votes are strong
enough AND clearly ahead of the runner-up. Otherwise the engine ABSTAINS and the caller
leaves the file where it is. A wrong answer moves a file into the wrong crate; an abstention
costs nothing.

Signals (weight = the most a signal can contribute)
    artist_override   2.5  config.ARTIST_GENRE_OVERRIDE (a catch-all override says nothing)
    knowledge_base    2.0  x confidence   static artist knowledge
    lastfm_track      2.0  the track's own community tags (identity-checked)
    lastfm_artist     1.6  the artist's tags — abundant but coarse: artists cross genres
    musicbrainz       1.5  tags of the recording that provably IS this track
    itunes            1.6  iTunes' genre for the track (title + artist + LENGTH checked); generic
                           genres ("Dance", "Electronic") are ignored; ONE matching release is a
                           weak vote (reliability 0.5), two agreeing releases a full one
    fingerprint       2.0  AcoustID -> MusicBrainz tags (audio-proven identity), opt-in
    remixer           1.5  "(<DJ> Remix)": a remix takes the remixer's genre, not the original's
    script            0.8  Devanagari / Gurmukhi / Tamil / Telugu lettering in the tags
    title_hint        0.6  an explicit genre word in the title
    llm               0.8  Groq text guess, opt-in — deliberately too weak to decide alone
BPM is not a vote: it scales down UNCURATED evidence for labels the tempo makes implausible
(a Last.fm "house" tag on a 174 BPM track), tolerating half/double-time detection errors. It
never scales down a curated artist table or a fingerprint match.

Verified vs unverified
    Last.fm has NO track-level tags for most DJ tracks (measured live: even tracks with
    hundreds of thousands of listeners came back empty), so artist-level tags often carry the
    answer. That is exactly the signal that files a Techno DJ's trance track under Techno.
    A Decision is therefore `verified` only when at least one TRUSTED source backs the winner
    (curated tables or track-level evidence); an answer resting on artist tags / hints alone is
    still returned but flagged unverified, and callers hold it for review instead of moving it.

The engine is dependency-injected (`fetchers`) so it is fully unit-testable offline, and all
network answers go through a disk cache so re-running an evaluation costs nothing.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger

# ── weights & thresholds (tune with eval_genre_classifier.py, not by feel) ───────────────
W_OVERRIDE = 2.5
W_KNOWLEDGE_BASE = 2.0
W_LASTFM_TRACK = 2.0
W_LASTFM_ARTIST = 1.6
W_MUSICBRAINZ = 1.5
W_ITUNES = 1.6
W_FINGERPRINT = 2.0
W_REMIXER = 1.5
W_SCRIPT = 0.8
W_TITLE_HINT = 0.6
W_LLM = 0.8

EVIDENCE_FULL = 2.5      # total vote weight at which evidence counts as "enough"
MIN_EVIDENCE = 1.0       # below this total the engine never answers
MIN_CONFIDENCE = 0.60    # override with GENRE_EVIDENCE_MIN_CONFIDENCE once you have measured precision
MIN_MARGIN = 0.25        # (top - runner_up) / top must reach this, else it is a conflict

# Sources that make an answer `verified` (see the module docstring). "lastfm_artist", "script",
# "title_hint" and "llm" are deliberately absent: alone, they are inference, not evidence.
TRUSTED_SOURCES = frozenset({"artist_override", "knowledge_base", "lastfm_track", "musicbrainz",
                             "fingerprint", "remixer", "itunes"})

MIN_TAG_REL = 0.25            # ignore tags with < 25% of the top tag's votes
LASTFM_FULL_TOP = 60          # a best genre tag with >= this Last.fm count is fully reliable
MB_FULL_TOP = 3               # ... or this many MusicBrainz voters
UNVERIFIED_FACTOR = 0.7       # Last.fm gave no resolved track name, so identity is unchecked

LASTFM_MIN_TITLE_SIM = 0.80
LASTFM_MIN_ARTIST_SIM = 0.60

CATCH_ALL_LABELS = {"", "Electronic", "NeedsReview"}

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
DEFAULT_CACHE_PATH = REPORTS_DIR / "genre_evidence_cache.json"


# ── tag / text -> library label ─────────────────────────────────────────────────────────
# (regex over a normalised tag, folder label, strength). A tag may match several rows:
# "melodic house and techno" honestly supports BOTH House and Techno and lets the vote sort
# it out. Generic dance tags (edm, electronic, dance, electro, ambient, ...) are absent on
# purpose: they say nothing about which crate a track belongs in.
_TAG_RULES: List[Tuple[re.Pattern, str, float]] = [
    (re.compile(p), label, s) for p, label, s in [
        (r"\b(?:psy ?trance|trance)\b",                                    "Trance", 1.0),
        (r"\btechno\b",                                                    "Techno", 1.0),
        (r"\bhouse\b",                                                     "House", 1.0),
        (r"\b(?:drum ?(?:and|n|&) ?bass|drum ?bass|dnb|d&b|jungle|liquid funk|neurofunk)\b",
                                                                           "Drum & Bass", 1.0),
        (r"\b(?:dubstep|brostep|riddim)\b",                                "Dubstep", 1.0),
        (r"\bgarage\b(?!\s*rock)",                                        "UK Garage", 0.7),
        (r"\b(?:uk garage|ukg|2 step|two step|speed garage|bassline|uk funky)\b",
                                                                           "UK Garage", 1.0),
        (r"\b(?:grime|uk drill)\b",                                        "Grime", 1.0),
        (r"\b(?:bollywood|hindi|filmi)\b",                                 "Bollywood", 1.0),
        (r"\b(?:desi|telugu)\b(?!\s+(?:hip ?hop|rap))",                    "Bollywood", 0.8),
        (r"\bindian\b(?!\s+hip ?hop)",                                     "Bollywood", 0.6),
        (r"\b(?:punjabi|bhangra)\b",                                       "Punjabi", 1.0),
        (r"\b(?:tamil|kollywood)\b",                                       "Tamil", 1.0),
        # Hip hop is one genre with TWO crates: the same tags describe Seedhe Maut and Kendrick Lamar.
        # Explicitly Indian hip hop tags say so; every other hip hop tag says "hip hop", and
        # split_hip_hop() then decides Indian vs international from where the artist is from.
        (r"\b(?:desi hip ?hop|indian hip ?hop|hindi (?:hip ?hop|rap)|desi rap|dhh)\b",
                                                                           "Indian Hip Hop", 1.0),
        (r"\b(?:hip ?hop|rap)\b",                                          "International Hip Hop", 0.9),
        (r"\b(?:r&b|rnb)\b",                                               "R&B", 0.9),
        (r"\bsoul\b",                                                      "R&B", 0.4),   # also tags Indian film songs
        (r"\b(?:latin|latino|reggaeton|cumbia|salsa|bachata|urbano)\b",     "Latin", 0.9),
        (r"\bindie\b",                                                     "Indie", 0.8),
        (r"\bpop\b",                                                       "Pop", 0.5),
    ]
]

# Words that are safe to trust when found inside a TITLE ("House" is not: "Safe House").
_TITLE_HINT_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(p), label) for p, label in [
        (r"\b(?:psy ?trance|trance)\b",                       "Trance"),
        (r"\btechno\b",                                       "Techno"),
        (r"\b(?:drum ?(?:and|n|&) ?bass|dnb)\b",              "Drum & Bass"),
        (r"\bdubstep\b",                                      "Dubstep"),
        (r"\bbollywood\b",                                    "Bollywood"),
        (r"\b(?:punjabi|bhangra)\b",                          "Punjabi"),
        (r"\btamil\b",                                        "Tamil"),
    ]
]

_REMIXER_RE = re.compile(
    r"[\(\[]\s*([^()\[\]]+?)\s+(?:remix|rework|re-?edit|edit|bootleg|flip|vip)\s*[\)\]]", re.IGNORECASE
)

# (first, last) code point -> label
_SCRIPT_LABELS: List[Tuple[int, int, str]] = [
    (0x0900, 0x097F, "Bollywood"),   # Devanagari (Hindi)
    (0x0A00, 0x0A7F, "Punjabi"),     # Gurmukhi
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Bollywood"),   # Telugu -> Bollywood crate (see GENRE_TAXONOMY)
]

# ── BPM plausibility ───────────────────────────────────────────────────────────────────
# Deliberately wide: these only remove labels a tempo makes implausible. Replace per label
# with ranges measured from the user's own folders (eval_genre_classifier.py --write-bpm-priors).
DEFAULT_BPM_RANGES: Dict[str, List[Tuple[float, float]]] = {
    "House":       [(116, 134)],
    "Techno":      [(118, 160)],
    "Trance":      [(124, 152)],
    "Drum & Bass": [(158, 184), (79, 92)],
    "Dubstep":     [(136, 152), (68, 76)],
    "UK Garage":   [(122, 146)],
    "Grime":       [(132, 148), (66, 74)],
}
BPM_NEAR_MISS = 6.0
BPM_NEAR_FACTOR = 0.6
BPM_FAR_FACTOR = 0.35
# Curated / proven sources are never scaled down by tempo (see decide()).
BPM_EXEMPT_SOURCES = frozenset({"artist_override", "knowledge_base", "fingerprint", "remixer"})


# ── data ────────────────────────────────────────────────────────────────────────────────
@dataclass
class Vote:
    source: str
    label: str
    weight: float
    detail: str = ""


@dataclass
class Decision:
    genre: str = ""                       # folder label ("Techno"), "" when abstaining
    confidence: float = 0.0
    abstain: bool = True
    reason: str = ""
    sources: List[str] = field(default_factory=list)      # sources that backed the winner
    scores: Dict[str, float] = field(default_factory=dict)
    votes: List[Vote] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """True when a trusted source (curated table or track-level evidence) backs the answer."""
        return (not self.abstain) and any(s in TRUSTED_SOURCES for s in self.sources)

    def explain(self) -> str:
        head = (f"{self.genre} ({self.confidence:.0%})" if not self.abstain else f"abstain: {self.reason}")
        ranked = ", ".join(f"{k} {v:.2f}" for k, v in sorted(self.scores.items(), key=lambda kv: -kv[1]))
        return f"{head} | {ranked}" if ranked else head


# ── small helpers ───────────────────────────────────────────────────────────────────────
def label_for_genre(genre: str) -> str:
    """Folder label for any genre string ('Afro House' -> 'House', 'Library/Techno' -> 'Techno')."""
    if not genre:
        return ""
    from services.genre_router import GENRE_TAXONOMY, _library_path, normalize_genre

    g = genre.replace("\\", "/").strip("/")
    if g.startswith("Library/"):
        g = g[len("Library/"):]
    g = g.split("/")[0]
    canonical = g if g in GENRE_TAXONOMY else normalize_genre(g)
    if not canonical:
        return ""
    return _library_path(canonical).split("/", 1)[-1]


def _normalise_tag(tag: str) -> str:
    """Lowercase, drop apostrophes ("drum'n'bass" -> "drumnbass"), turn - _ / into spaces."""
    text = (tag or "").lower().replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[-_/]+", " ", text).split())


def tag_labels(tag: str) -> Dict[str, float]:
    """{label: strength} a single tag supports (empty for generic / unmappable tags)."""
    t = _normalise_tag(tag)
    out: Dict[str, float] = {}
    for pattern, label, strength in _TAG_RULES:
        if pattern.search(t):
            out[label] = max(out.get(label, 0.0), strength)
    return out


def votes_from_tags(
    source: str, tags: Sequence[Tuple[str, int]], weight: float, full_top: float, factor: float = 1.0
) -> List[Vote]:
    """
    Turn a weighted tag list into per-label votes.

    The signal's weight is shared out in proportion to each label's tag mass, and scaled by
    how much community agreement stands behind its best genre tag (`full_top`): a track whose
    top genre tag was applied by 3 people speaks more quietly than one applied by 100.
    """
    counts = [max(0, int(c)) for _, c in tags]
    top = max(counts, default=0)
    if top <= 0:
        return []
    mass: Dict[str, float] = defaultdict(float)
    best_count: Dict[str, int] = defaultdict(int)
    used: Dict[str, List[str]] = defaultdict(list)
    for (name, count), c in zip(tags, counts):
        if c / top < MIN_TAG_REL:
            continue
        for label, strength in tag_labels(name).items():
            mass[label] += c * strength
            best_count[label] = max(best_count[label], c)
            used[label].append(name)
    total = sum(mass.values())
    if total <= 0:
        return []
    reliability = min(1.0, max(best_count.values()) / float(full_top))
    return [
        Vote(source, label, weight * reliability * factor * m / total, ", ".join(used[label][:3]))
        for label, m in mass.items()
    ]


def bpm_factor(label: str, bpm: Optional[float], ranges: Optional[dict] = None) -> float:
    """1.0 when the tempo fits the label (also at half/double time), else a penalty."""
    ranges = ranges or DEFAULT_BPM_RANGES
    spans = ranges.get(label)
    if not bpm or not spans:
        return 1.0
    if isinstance(spans, (list, tuple)) and len(spans) == 2 and not isinstance(spans[0], (list, tuple)):
        spans = [tuple(spans)]                       # a single [lo, hi] pair
    nearest = None
    for candidate in (bpm, bpm * 2.0, bpm / 2.0):
        for lo, hi in spans:
            if lo <= candidate <= hi:
                return 1.0
            gap = lo - candidate if candidate < lo else candidate - hi
            nearest = gap if nearest is None else min(nearest, gap)
    return BPM_NEAR_FACTOR if (nearest is not None and nearest <= BPM_NEAR_MISS) else BPM_FAR_FACTOR


def _script_label(text: str) -> str:
    for ch in text or "":
        cp = ord(ch)
        for lo, hi, label in _SCRIPT_LABELS:
            if lo <= cp <= hi:
                return label
    return ""


def _artist_candidates(artist: str) -> List[str]:
    """The full credit first, then each individual artist ("A & B feat. C" -> A, B, C)."""
    out = [artist]
    try:
        from services.audio_verifier import _split_artists

        out.extend(p for p in _split_artists(artist) if p and p != artist.strip().lower())
    except Exception:
        pass
    return [a for a in out if a and a.strip()]


_CREDIT_SPLIT_RE = re.compile(r"\s*(?:,|;|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b)\s*", re.IGNORECASE)


def primary_artist(credit: str) -> str:
    """
    The lead artist of a credit, for asking an external database about ONE artist.

    Splits only on unambiguous separators (comma, ';', feat/ft/featuring) — "Kova, Memento Mori"
    -> "Kova". It deliberately does NOT split on '&' / 'and' / 'x': "Above & Beyond" is one act,
    and querying "Above" would return a different artist's tags.
    """
    parts = [p.strip() for p in _CREDIT_SPLIT_RE.split(credit or "") if p and p.strip()]
    return parts[0] if parts else (credit or "").strip()


def _override_label(artist: str) -> str:
    """Label from ARTIST_GENRE_OVERRIDE / the knowledge base for one artist name ('' if none/catch-all)."""
    from config import config
    from services.genre_router import normalize_artist_key

    override = config.ARTIST_GENRE_OVERRIDE.get(normalize_artist_key(artist))
    label = label_for_genre(override) if override else ""
    return "" if label in CATCH_ALL_LABELS else label


def _knowledge_label(artist: str) -> Tuple[str, float]:
    try:
        from services.artist_knowledge_service import lookup_artist_knowledge

        kb = lookup_artist_knowledge(artist)
    except Exception:
        kb = None
    if not kb:
        return "", 0.0
    label = label_for_genre(kb.get("genre", ""))
    return ("", 0.0) if label in CATCH_ALL_LABELS else (label, float(kb.get("confidence", 0.0)))


# ── disk cache for network answers ──────────────────────────────────────────────────────
_MISSING = object()


class TagCache:
    """Small JSON cache: {key: [timestamp, value]}. Definite answers only — never failures."""

    def __init__(self, path: Optional[Path] = None, ttl_days: float = 30.0, flush_every: int = 20):
        self.path = Path(path) if path else None
        self.ttl = ttl_days * 86400.0
        self._flush_every = flush_every
        self._lock = threading.Lock()
        self._data: Dict[str, list] = {}
        self._dirty = 0
        if self.path and self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"[genre_evidence] ignoring unreadable cache {self.path}: {exc}")

    def get(self, key: str, default=_MISSING):
        with self._lock:
            entry = self._data.get(key)
        if not entry or time.time() - entry[0] > self.ttl:
            return default
        return entry[1]

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = [time.time(), value]
            self._dirty += 1
            due = self._dirty >= self._flush_every
        if due:
            self.flush()

    def flush(self) -> None:
        if not self.path:
            return
        with self._lock:
            if not self._dirty:
                return
            snapshot = json.dumps(self._data, ensure_ascii=False)
            self._dirty = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(snapshot)
            os.replace(tmp, self.path)
        except Exception as exc:
            logger.warning(f"[genre_evidence] could not write cache {self.path}: {exc}")

    def __len__(self) -> int:
        return len(self._data)


# ── network signals ─────────────────────────────────────────────────────────────────────
class LiveFetchers:
    """
    The real network signals. Each method returns a definite answer (dict / str) or None when
    the source was unreachable; only definite answers are cached.

        lastfm_track_tags(artist, title)  -> {"tags": [(name, count)], "artist", "track"} | None
        lastfm_artist_tags(artist)        -> {"tags": [...], "artist"}                   | None
        musicbrainz_tags(artist, title)   -> {"tags": [{"name","count"}], "title","artist"} | None
        fingerprint_genre(path)           -> folder label | ""
        llm_genre(path)                   -> folder label | ""
    """

    def __init__(self, cache: Optional[TagCache] = None):
        self.cache = cache if cache is not None else TagCache(DEFAULT_CACHE_PATH)
        atexit.register(self.cache.flush)

    def _cached(self, key: str, fetch):
        hit = self.cache.get(key)
        if hit is not _MISSING:
            return hit
        value = fetch()
        if value is not None:
            self.cache.set(key, value)
        return value

    @staticmethod
    def _keys(artist: str, title: str = "") -> str:
        from services.genre_router import normalize_artist_key

        return f"{normalize_artist_key(artist)}||{' '.join((title or '').lower().split())}"

    def lastfm_track_tags(self, artist: str, title: str):
        from services import lastfm_service

        return self._cached("lfm_track|" + self._keys(artist, title),
                            lambda: lastfm_service.get_track_top_tags(artist, title))

    def lastfm_artist_tags(self, artist: str):
        from services import lastfm_service

        return self._cached("lfm_artist|" + self._keys(artist),
                            lambda: lastfm_service.get_artist_top_tags(artist))

    def musicbrainz_tags(self, artist: str, title: str):
        from services import musicbrainz_service

        return self._cached("mb|" + self._keys(artist, title),
                            lambda: musicbrainz_service.search_recording_tags(title, artist))

    def itunes_candidates(self, artist: str, title: str):
        """Raw iTunes results for the query (identity filtering happens later, per file length).
        Cached once per (artist, title): [] = iTunes has nothing, None = unavailable (not cached)."""
        from services import itunes_service

        return self._cached("itunes|" + self._keys(artist, title),
                            lambda: itunes_service.fetch_candidates(artist, title))

    def fingerprint_genre(self, path: str) -> str:
        from services import musicbrainz_service

        return label_for_genre(musicbrainz_service.lookup_by_fingerprint(path))

    def llm_genre(self, path: str) -> str:
        from services.groq_service import identify_audio

        return label_for_genre(identify_audio(path).get("gemini_genre", ""))


_default_fetchers: Optional[LiveFetchers] = None
_default_fetchers_lock = threading.Lock()


def default_fetchers() -> LiveFetchers:
    """One shared LiveFetchers (and therefore one loaded cache) for the whole process."""
    global _default_fetchers
    with _default_fetchers_lock:
        if _default_fetchers is None:
            _default_fetchers = LiveFetchers()
        return _default_fetchers


# ── the engine ──────────────────────────────────────────────────────────────────────────
def _same_named(asked: str, resolved: str, fuzzy: bool = True) -> float:
    from services import strict_matcher as sm

    return sm._fuzzy_ratio(asked.lower(), resolved.lower()) if fuzzy else sm.title_similarity(asked, resolved)


def _lastfm_track_votes(f, artist: str, title: str) -> List[Vote]:
    res = f.lastfm_track_tags(artist, title)
    if not res or not res.get("tags"):
        return []
    factor = 1.0
    r_track, r_artist = res.get("track", ""), res.get("artist", "")
    if r_track or r_artist:
        # autocorrect can answer for a DIFFERENT track; keep it only if it is this one.
        if r_track and _same_named(title, r_track, fuzzy=False) < LASTFM_MIN_TITLE_SIM:
            return []
        if r_artist and max(_same_named(a, r_artist) for a in _artist_candidates(artist)) < LASTFM_MIN_ARTIST_SIM:
            return []
    else:
        factor = UNVERIFIED_FACTOR
    return votes_from_tags("lastfm_track", res["tags"], W_LASTFM_TRACK, LASTFM_FULL_TOP, factor)


def _lastfm_artist_votes(f, artist: str) -> List[Vote]:
    res = f.lastfm_artist_tags(artist)
    if not res or not res.get("tags"):
        return []
    r_artist = res.get("artist", "")
    if r_artist and _same_named(artist, r_artist) < 0.75:
        return []
    return votes_from_tags("lastfm_artist", res["tags"], W_LASTFM_ARTIST, LASTFM_FULL_TOP)


def _musicbrainz_votes(f, artist: str, title: str) -> List[Vote]:
    res = f.musicbrainz_tags(artist, title)
    if not res or not res.get("tags"):
        return []
    pairs = [(t.get("name", ""), int(t.get("count", 0))) for t in res["tags"] if isinstance(t, dict)]
    return votes_from_tags("musicbrainz", pairs, W_MUSICBRAINZ, MB_FULL_TOP)


ITUNES_FULL_TOP = 2            # two matching releases that agree = a fully reliable iTunes vote


def _itunes_votes(f, artist: str, title: str, duration_s: Optional[float]) -> List[Vote]:
    from services import itunes_service

    get = getattr(f, "itunes_candidates", None)
    if get is None:
        return []
    candidates = get(artist, title)
    if not candidates:
        return []
    genres = itunes_service.matching_genres(candidates, artist, title, duration_s)
    return votes_from_tags("itunes", genres, W_ITUNES, ITUNES_FULL_TOP)


def collect_votes(
    artist: str, title: str, *, path: Optional[str] = None, online: bool = False,
    use_fingerprint: bool = False, use_llm: bool = False, use_musicbrainz: bool = True,
    use_itunes: bool = False, duration_s: Optional[float] = None, fetchers=None,
) -> List[Vote]:
    votes: List[Vote] = []
    artist, title = (artist or "").strip(), (title or "").strip()

    # ── offline signals ──
    for candidate in _artist_candidates(artist):
        label = _override_label(candidate)
        if label:
            votes.append(Vote("artist_override", label, W_OVERRIDE, candidate))
            break
    for candidate in _artist_candidates(artist):
        label, conf = _knowledge_label(candidate)
        if label:
            votes.append(Vote("knowledge_base", label, W_KNOWLEDGE_BASE * conf, candidate))
            break

    remix = _REMIXER_RE.search(title)
    if remix:
        remixer = remix.group(1).strip()
        label = _override_label(remixer) or _knowledge_label(remixer)[0]
        if label:
            votes.append(Vote("remixer", label, W_REMIXER, remixer))

    script = _script_label(artist) or _script_label(title)
    if script:
        votes.append(Vote("script", script, W_SCRIPT))

    t = _normalise_tag(title)
    hinted = {label for pattern, label in _TITLE_HINT_RULES if pattern.search(t)}
    for label in sorted(hinted):
        votes.append(Vote("title_hint", label, W_TITLE_HINT / len(hinted), "title"))

    # ── network signals (each independently allowed to fail) ──
    if online:
        f = fetchers if fetchers is not None else default_fetchers()
        lead = primary_artist(artist)                 # ask external databases about ONE artist
        lookups = [(_lastfm_track_votes, (lead, title)), (_lastfm_artist_votes, (lead,))]
        if use_musicbrainz:
            lookups.append((_musicbrainz_votes, (lead, title)))
        if use_itunes:
            lookups.append((lambda ff, a, t: _itunes_votes(ff, a, t, duration_s), (lead, title)))
        for fn, args in lookups:
            if not all(args):
                continue
            try:
                votes.extend(fn(f, *args))
            except Exception as exc:
                logger.debug(f"[genre_evidence] {fn.__name__} failed for {artist!r} / {title!r}: {exc}")
        if path and use_fingerprint:
            try:
                label = f.fingerprint_genre(path)
                if label and label not in CATCH_ALL_LABELS:
                    votes.append(Vote("fingerprint", label, W_FINGERPRINT))
            except Exception as exc:
                logger.debug(f"[genre_evidence] fingerprint failed for {path}: {exc}")
        if path and use_llm:
            try:
                label = f.llm_genre(path)
                if label and label not in CATCH_ALL_LABELS:
                    votes.append(Vote("llm", label, W_LLM))
            except Exception as exc:
                logger.debug(f"[genre_evidence] llm failed for {path}: {exc}")
    return split_hip_hop([v for v in votes if v.weight > 0 and v.label not in CATCH_ALL_LABELS])


HIP_HOP_INTERNATIONAL = "International Hip Hop"
HIP_HOP_INDIAN = "Indian Hip Hop"
_INDIAN_LABELS = frozenset({"Bollywood", "Punjabi", "Tamil", HIP_HOP_INDIAN})
# Signals that say nothing about where an ARTIST is from: a word in a title, or a remixer's crate.
_NOT_ORIGIN_SOURCES = frozenset({"title_hint", "remixer"})
_MIN_ORIGIN_WEIGHT = 0.3


def split_hip_hop(votes: List[Vote]) -> List[Vote]:
    """
    Hip hop has two crates, Indian and international, and the tags for both are the same words. What
    separates them is where the artist is from, and the evidence for that is any Indian signal among
    the votes themselves: a curated entry, Indian tags (desi, hindi, punjabi, dhh ...), or Indian script.
    If there is one, every generic hip hop vote is counted for Indian Hip Hop; otherwise it stays
    international. Weak or title-only signals are not enough to reclassify an artist.
    """
    if not any(v.label == HIP_HOP_INTERNATIONAL for v in votes):
        return votes
    indian_origin = any(v.label in _INDIAN_LABELS and v.source not in _NOT_ORIGIN_SOURCES
                        and v.weight >= _MIN_ORIGIN_WEIGHT for v in votes)
    if not indian_origin:
        return votes
    return [Vote(v.source, HIP_HOP_INDIAN, v.weight, v.detail) if v.label == HIP_HOP_INTERNATIONAL else v
            for v in votes]


def is_indian_artist(artist: str, title: str = "", *, fetchers=None) -> bool:
    """
    Does ANY Indian signal exist for this artist (curated list, Indian tags, Indian script)? Used to pick
    between the Indian Hip Hop and International Hip Hop crates when something else has already said
    "hip hop". Costs nothing extra when Last.fm was asked already (answers are cached). Never raises.
    """
    try:
        votes = collect_votes(artist, title, online=True, use_musicbrainz=False, fetchers=fetchers)
    except Exception as exc:
        logger.debug(f"[genre_evidence] is_indian_artist failed for {artist!r}: {exc}")
        return False
    return any(v.label in _INDIAN_LABELS and v.source not in _NOT_ORIGIN_SOURCES
               and v.weight >= _MIN_ORIGIN_WEIGHT for v in votes)


def _min_confidence(explicit: Optional[float]) -> float:
    """Cut-off precedence: the caller's value, then GENRE_EVIDENCE_MIN_CONFIDENCE, then the default."""
    if explicit is not None:
        return explicit
    try:
        return float(os.getenv("GENRE_EVIDENCE_MIN_CONFIDENCE", ""))
    except ValueError:
        return MIN_CONFIDENCE


def decide(votes: Sequence[Vote], bpm: Optional[float] = None, bpm_ranges: Optional[dict] = None,
           min_confidence: Optional[float] = None) -> Decision:
    """Pure decision rule: votes (+ tempo) in, Decision out. Never raises."""
    cutoff = _min_confidence(min_confidence)
    scores: Dict[str, float] = defaultdict(float)
    for v in votes:
        # Tempo tempers weak, uncurated evidence; it never overrules a curated artist table or a
        # fingerprint. (Measured on the real library: Skrillex's "Bangarang" is 110 BPM, outside
        # any tidy "dubstep range" — a tempo veto on the override would have discarded it.)
        factor = 1.0 if v.source in BPM_EXEMPT_SOURCES else bpm_factor(v.label, bpm, bpm_ranges)
        scores[v.label] += v.weight * factor
    decision = Decision(scores=dict(scores), votes=list(votes))
    if not scores:
        decision.reason = "no evidence"
        return decision

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (top_label, top), second = ranked[0], (ranked[1] if len(ranked) > 1 else (None, 0.0))
    total = sum(scores.values())
    if total < MIN_EVIDENCE:
        decision.reason = f"not enough evidence ({total:.2f} < {MIN_EVIDENCE})"
        return decision
    if second[1] and (top - second[1]) / top < MIN_MARGIN:
        decision.reason = f"conflicting evidence: {top_label} {top:.2f} vs {second[0]} {second[1]:.2f}"
        return decision
    confidence = (top / total) * min(1.0, total / EVIDENCE_FULL)
    if confidence < cutoff:
        decision.reason = f"low confidence {confidence:.2f} for {top_label}"
        return decision

    decision.genre, decision.confidence, decision.abstain, decision.reason = top_label, round(confidence, 3), False, "voted"
    decision.sources = sorted({v.source for v in votes if v.label == top_label})
    return decision


def classify_track(
    artist: str,
    title: str,
    *,
    path: Optional[str] = None,
    bpm: Optional[float] = None,
    online: bool = False,
    use_fingerprint: bool = False,
    use_llm: bool = False,
    use_musicbrainz: bool = True,
    use_itunes: bool = False,
    duration_s: Optional[float] = None,
    bpm_priors: Optional[dict] = None,
    min_confidence: Optional[float] = None,
    fetchers=None,
) -> Decision:
    """
    Classify ONE track. Always returns a Decision (check `.abstain` and `.verified`); never raises.

    online=False uses only offline signals (overrides, knowledge base, remixer, script, title,
    BPM). online=True adds Last.fm + MusicBrainz; use_fingerprint / use_llm add the slower
    AcoustID lookup and the (weak) Groq text guess. min_confidence lowers/raises the answer
    cut-off (default 0.60 or GENRE_EVIDENCE_MIN_CONFIDENCE) — the evaluation harness lowers
    it to see how precision falls as confidence does.
    """
    try:
        ranges = dict(DEFAULT_BPM_RANGES)
        if bpm_priors:
            ranges.update({k: v for k, v in bpm_priors.items() if v})
        votes = collect_votes(artist, title, path=path, online=online, use_fingerprint=use_fingerprint,
                              use_llm=use_llm, use_musicbrainz=use_musicbrainz, use_itunes=use_itunes,
                              duration_s=duration_s, fetchers=fetchers)
        return decide(votes, bpm, ranges, min_confidence)
    except Exception as exc:                                    # a classifier must fail closed
        logger.warning(f"[genre_evidence] classify_track failed for {artist!r} / {title!r}: {exc}")
        return Decision(reason=f"error: {exc}")
