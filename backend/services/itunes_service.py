"""
itunes_service.py
=================
A TRACK-LEVEL genre from the free iTunes Search API (no key). It fills the gap Last.fm leaves: on the
real library Last.fm had no track tags for most DJ tracks, so ~600 tracks had no genre evidence at all.

iTunes answers with title, artist, LENGTH and a genre per track, so an answer can be identity-checked
before it is believed. Measured on tracks with known answers: Pritam "Channa Mereya" -> Bollywood,
Charlotte de Witte "Doppler" -> Techno, Chris Lake "La Noche" -> House, Sammy Virji "Shella Verse" ->
Garage, AP Dhillon "With You" -> Punjabi ... but also generic ("Dance", "Electronic", "Worldwide") and
sometimes plain wrong (Hamdi "Palm Trees" -> "Latin"). Hence: a vote, never an oracle — generic genres
are ignored and a single result is a weak vote (see genre_evidence).

Politeness: ~20 calls/minute are allowed; requests are spaced MIN_INTERVAL apart, and every answer
(including "nothing found") is cached by the caller so no query is ever repeated.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

BASE_URL = "https://itunes.apple.com/search"
MIN_INTERVAL = 3.0
MAX_RESULTS = 8
MIN_TITLE_SIM_THRESHOLD = 0.85          # same_song_title() threshold
MIN_ARTIST_SIM = 0.60
MAX_LENGTH_DELTA = 6.0                   # seconds, when the file's length is known

_lock = threading.Lock()
_last_call = 0.0


def fetch_candidates(artist: str, title: str, *, get: Optional[Callable] = None,
                     sleep: Callable[[float], None] = time.sleep) -> Optional[List[Dict]]:
    """
    Raw candidates for a query: [{"artist", "title", "secs", "genre"}, ...] — [] when iTunes has
    nothing, None when the request failed (not to be cached).
    """
    global _last_call
    term = f"{artist} {title}".strip()
    if not term:
        return None
    if get is None:
        import requests
        get = lambda **kw: requests.get(BASE_URL, timeout=12, headers={"User-Agent": "spotify-meta-downloader/1.0"}, **kw)  # noqa: E731
    with _lock:
        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            sleep(wait)
        _last_call = time.time()
    try:
        resp = get(params={"term": term, "entity": "song", "limit": MAX_RESULTS})
        if getattr(resp, "status_code", 200) != 200:
            return None                                     # 403/429/5xx: unavailable, try again later
        results = resp.json().get("results", [])
    except Exception:
        return None
    out = []
    for r in results:
        if r.get("kind") not in (None, "song"):
            continue
        out.append({"artist": r.get("artistName", ""), "title": r.get("trackName", ""),
                    "secs": round((r.get("trackTimeMillis") or 0) / 1000.0, 1),
                    "genre": r.get("primaryGenreName", "")})
    return out


def matching_genres(candidates: List[Dict], artist: str, title: str, duration_s: Optional[float] = None) -> List[List]:
    """
    [[genre, count], ...] over the candidates that ARE this track: same core title, a similar artist,
    and — when the file's length is known — a length within a few seconds. Different releases of one
    song (album / single / compilation) each count once, so agreement between them is evidence.
    """
    from services import strict_matcher as sm

    counts: Dict[str, int] = {}
    for c in candidates or []:
        if not c.get("genre") or not sm.same_song_title(title, c.get("title", ""), MIN_TITLE_SIM_THRESHOLD):
            continue
        if artist and sm._fuzzy_ratio(artist.lower(), c.get("artist", "").lower()) < MIN_ARTIST_SIM:
            continue
        if duration_s and c.get("secs") and abs(duration_s - c["secs"]) > MAX_LENGTH_DELTA:
            continue
        counts[c["genre"]] = counts.get(c["genre"], 0) + 1
    return [[g, n] for g, n in sorted(counts.items(), key=lambda kv: -kv[1])]
