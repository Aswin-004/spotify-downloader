"""
artist_recovery.py
==================
Recover the real ARTIST of tracks whose artist tag is missing or a placeholder.

Why: on the real library, 310 of 2,153 tracks (14%) had the artist "Unknown", "Electronic"
(a genre word written into the artist field) or nothing — 104 of the 107 files in Library/Trance
among them. Every artist-based signal (curated overrides, the knowledge base, Last.fm artist
tags) is blind to those, so they were filed by a text-only guess. 240 of the 310 still carry a
Spotify track id in the file, from which the true artist is one lookup away.

Safety: a Spotify 429 blocks this app's whole Spotify access (including the live web app) for
~22 h, so the lookup is paced (default 1 call/s), capped per run, stops at the FIRST rate
limit, and every answer is cached on disk — an interrupted or repeated run costs nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from loguru import logger

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "reports" / "artist_recovery_cache.json"

# Values that are not an artist: empty, "unknown", or a GENRE written into the artist field.
PLACEHOLDER_ARTISTS = frozenset({
    "", "unknown", "unknown artist", "various artists", "various", "va", "n/a", "na", "none", "null",
    "untitled", "artist",
    "electronic", "electronica", "dance", "edm", "house", "techno", "trance", "psytrance", "psy",
    "dubstep", "drum and bass", "drum & bass", "dnb", "grime", "uk garage", "bass",
    "bollywood", "punjabi", "tamil", "indian", "hip hop", "hiphop", "hip-hop", "rap", "r&b", "rnb",
    "pop", "latin", "rock", "metal", "jazz", "classical",
})


def is_placeholder_artist(name: Optional[str]) -> bool:
    """True when `name` cannot identify an artist (missing, 'Unknown', or just a genre word)."""
    return " ".join((name or "").lower().split()) in PLACEHOLDER_ARTISTS


class RateLimited(Exception):
    """The metadata source asked us to stop; the run halts immediately and keeps its progress."""


# A Spotify 429 blocks this app's whole Spotify access for ~23 h (2026-09-02: ~80,000 s; 2026-09-20:
# 82,373 s, tripped by ~600 single-track lookups in an hour at 1 call/s — far lower than assumed).
# The app's own cooldown flag lives in process memory, so a NEW process would happily call again.
# This file carries the block across runs so the re-sort tool never does.
BLOCK_FILE = Path(__file__).resolve().parent.parent / "reports" / "spotify_block.json"


def record_block(seconds: float, path: Optional[Path] = None) -> None:
    """Remember that Spotify said 'stop' for `seconds` from now."""
    import json
    p = Path(path) if path else BLOCK_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"until": time.time() + float(seconds), "recorded": time.strftime("%Y-%m-%d %H:%M:%S")}),
                     encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[artist_recovery] could not record the Spotify block: {exc}")


def block_seconds_left(path: Optional[Path] = None) -> int:
    """Seconds of Spotify block remaining (0 when none, expired, or the file is unreadable)."""
    import json
    p = Path(path) if path else BLOCK_FILE
    try:
        return max(0, int(json.loads(p.read_text(encoding="utf-8"))["until"] - time.time()))
    except Exception:
        return 0


def _seconds_from_message(message: str) -> int:
    """'Spotify rate limited. Blocked for 82373s.' / 'cooling down. Retry in 500s.' -> seconds (0 if none)."""
    import re
    m = re.search(r"(\d+)\s*s\b", message or "")
    return int(m.group(1)) if m else 0


@dataclass
class RecoveryResult:
    found: Dict[str, dict] = field(default_factory=dict)      # spotify_id -> {"artists": [...], "title", "album"}
    partial: set = field(default_factory=set)                  # ids in `found` whose record has no length
    calls: int = 0
    from_cache: int = 0
    unknown: int = 0                                           # looked up, source has no such track
    failed: int = 0                                            # transient failure, will be retried next run
    stopped: str = ""                                          # why the run ended early, "" if it finished


def known_from_playlist(tracks: Iterable[dict]) -> Dict[str, dict]:
    """{spotify_id: {"artists": [first artist], "title", "duration_ms"}} from a playlist read
    (the app's get_playlist_tracks_by_id). Free data: one paginated read covers every track."""
    return {
        t["id"]: {"artists": [t["artist"]] if t.get("artist") else [], "title": t.get("title", ""),
                  "duration_ms": t.get("duration_ms")}
        for t in tracks if t.get("id")
    }


def validate_recovery(file_title: str, hit: dict, file_secs: Optional[float] = None,
                      *, max_length_delta: float = 20.0) -> Tuple[bool, str]:
    """
    Is this Spotify record really the SAME song as the file? Returns (ok, why-not).

    Measured on the real library: 87 of 500 recovered artists (17%) came from a Spotify id that
    points at a DIFFERENT song — the old pipeline searched Spotify with the placeholder artist
    "Indian" and got back unrelated tracks with "Indian" in the artist name ("Kiya Kiya" ->
    "Candytuft Parsley" by "The Bulbine for Indian"). Trusting those artists would drive wrong
    moves, so a recovered artist is only used when the title matches and, where both lengths are
    known, the lengths are close (a different song with the same title, or a karaoke cut, is not).
    """
    from services.strict_matcher import same_song_title

    sp_title = hit.get("title", "")
    if not same_song_title(file_title, sp_title):
        return False, f"Spotify id in the file points at a different song ('{sp_title[:40]}')"
    duration_ms = hit.get("duration_ms")
    if file_secs and duration_ms:
        delta = file_secs - duration_ms / 1000.0
        if abs(delta) > max_length_delta:
            return False, (f"file is {abs(delta):.0f}s {'longer' if delta > 0 else 'shorter'} "
                           f"than Spotify's version")
    return True, ""


def recover_artists(
    spotify_ids: Iterable[str],
    fetch: Callable[[str], Optional[dict]],
    cache,
    *,
    known: Optional[Dict[str, dict]] = None,
    want_duration: bool = False,
    max_calls: int = 300,
    pace_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> RecoveryResult:
    """
    Look up each distinct id once (cache first). `fetch(id)` returns a dict with "artists", a
    falsy dict/`{}` when the track definitely does not exist (cached), or None on a transient
    failure (not cached); it raises RateLimited to stop the run.

    known:         extra free data by id (e.g. known_from_playlist) — used instead of a call when it
                   can complete a record.
    want_duration: a cached record without "duration_ms" is incomplete and is completed (from
                   `known`, else by a call) — older caches were written before lengths were kept.
    """
    known = known or {}
    res = RecoveryResult()
    seen: set = set()
    todo: List[str] = []
    for sid in spotify_ids:
        sid = (sid or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        hit = cache.get(sid, None)
        if hit == {}:                                   # Spotify definitely has no such track
            res.from_cache += 1
            res.unknown += 1
            continue
        if hit and hit.get("artists") and (not want_duration or hit.get("duration_ms")):
            res.found[sid] = hit
            res.from_cache += 1
            continue
        kn = known.get(sid)
        if kn:
            merged = dict(hit or {})
            merged.setdefault("title", kn.get("title", ""))
            merged["duration_ms"] = kn.get("duration_ms") or merged.get("duration_ms")
            if not merged.get("artists"):
                merged["artists"] = list(kn.get("artists") or [])
            if merged["artists"] and (merged.get("duration_ms") or not want_duration):
                cache.set(sid, merged)
                res.found[sid] = merged
                res.from_cache += 1
                continue
        todo.append(sid)

    for n, sid in enumerate(todo, 1):
        if res.calls >= max_calls:
            res.stopped = f"call cap reached ({max_calls}); run again to continue"
            break
        try:
            value = fetch(sid)
        except RateLimited as exc:
            res.stopped = f"rate limited — stopped to protect the app's Spotify access ({exc})"
            break
        res.calls += 1
        if value is None:
            res.failed += 1
        elif value.get("artists"):
            cache.set(sid, value)
            res.found[sid] = value
        else:
            cache.set(sid, {})
            res.unknown += 1
        if n % 25 == 0:
            out(f"  recovered {len(res.found)} artists so far ({n}/{len(todo)} looked up)")
        if n < len(todo):
            sleep(pace_s)

    # Ids we could not complete with a length (rate limit, call cap, failure): fall back to the
    # cached record if it has artists. It is still checked by TITLE by the caller — that catches
    # the wrong-id problem; only the extra length check is lost — and reported as `partial`.
    if want_duration:
        for sid in todo:
            hit = cache.get(sid, None)
            if sid not in res.found and hit and hit.get("artists"):
                res.found[sid] = hit
                res.partial.add(sid)
    cache.flush()
    return res


def spotify_fetcher() -> Callable[[str], Optional[dict]]:
    """A `fetch` backed by the app's own Spotify service (one GET /tracks/{id} per call)."""
    from services.spotify_service import get_spotify_service

    svc = get_spotify_service()

    def fetch(spotify_id: str) -> Optional[dict]:
        try:
            track = svc._call_with_backoff(svc.sp.track, spotify_id)
        except ValueError as exc:                       # _call_with_backoff's signal for 429 / cooldown
            secs = _seconds_from_message(str(exc))
            if secs:
                record_block(secs)                      # so the NEXT process knows too
            raise RateLimited(str(exc))
        except Exception as exc:
            status = getattr(exc, "http_status", None)
            if status in (400, 404):
                return {}                               # no such track: a definite answer
            logger.debug(f"[artist_recovery] {spotify_id}: {exc}")
            return None
        return {
            "artists": [a.get("name", "") for a in track.get("artists", []) if a.get("name")],
            "title": track.get("name", ""),
            "album": (track.get("album") or {}).get("name", ""),
            "duration_ms": track.get("duration_ms"),
        }

    return fetch


def join_artists(artists: Iterable[str]) -> str:
    """'Kova, Memento Mori, Psyfeature' — the comma form the genre engine treats as a credit list."""
    return ", ".join(a for a in artists if a)
