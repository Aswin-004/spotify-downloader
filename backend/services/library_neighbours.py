"""
library_neighbours.py — "where do this artist's OTHER songs already live?"

The benchmark on Aswin's own library (2026-10-08, leave-one-out over 2508 songs): when an artist already has
>= 3 other songs filed and >= 80% of them sit in one crate, that crate is right 97% of the time (>= 2 others:
95.9%; 1 other: only 89% — not used). It covers songs by artists that are on none of his lists yet, which is where
the evidence / listening steps used to guess.

Read-only: it only reads ID3 artists of the files under <BASE_DOWNLOAD_DIR>/Library/<crate>/. Catch-all and
review folders never vote. Tags are cached by (size, mtime) so a refresh only re-reads files that changed.
NEIGHBOUR_ROUTING=false turns the step off.
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
import time
from typing import Optional

from loguru import logger

MIN_OTHERS = 3
MIN_SHARE = 0.8
_REFRESH_SEC = 600
_NON_VOTING = {"electronic", "needsreview", "quarantine", "manual", "duplicates"}
_NOT_AN_ARTIST = {"", "electronic", "dance", "unknown", "various artists"}
_SPLIT = re.compile(r",|&| x | feat\.? | ft\.? | and ", re.IGNORECASE)
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "library_artists.json")

_lock = threading.Lock()
_state: dict = {"at": 0.0, "index": {}}


def artist_keys(artist: str) -> set:
    """'A & B feat. C' -> {'a', 'b', 'c'} (lower-case, no placeholder names)."""
    return {p.strip().lower() for p in _SPLIT.split(artist or "") if p.strip()} - _NOT_AN_ARTIST


def _library_dir() -> str:
    from config import config
    return os.path.join(config.BASE_DOWNLOAD_DIR, "Library")


def _read_artist(path: str) -> str:
    try:
        from mutagen.id3 import ID3
        return str(ID3(path).get("TPE1") or "")
    except Exception:
        return ""


def build_index(library_dir: Optional[str] = None, read_artist=_read_artist, cache_file: Optional[str] = _CACHE_FILE) -> dict:
    """{artist_key: Counter({crate: songs})} for every mp3 in a voting crate. Never raises."""
    root = library_dir or _library_dir()
    try:
        cached = json.load(open(cache_file, encoding="utf-8")) if cache_file and os.path.exists(cache_file) else {}
    except Exception:
        cached = {}
    fresh: dict = {}
    index: dict = collections.defaultdict(collections.Counter)
    try:
        crates = [e for e in os.scandir(root) if e.is_dir()]
    except OSError:
        return {}
    for crate in crates:
        if crate.name.lower() in _NON_VOTING or crate.name.startswith("_"):
            continue
        try:
            files = [e for e in os.scandir(crate.path) if e.is_file() and e.name.lower().endswith(".mp3")]
        except OSError:
            continue
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            sig = [st.st_size, int(st.st_mtime)]
            hit = cached.get(f.path)
            artist = hit["artist"] if hit and hit["sig"] == sig else read_artist(f.path)
            fresh[f.path] = {"sig": sig, "artist": artist}
            for key in artist_keys(artist):
                index[key][crate.name] += 1
    if cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            tmp = cache_file + ".tmp"
            json.dump(fresh, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, cache_file)
        except Exception as exc:
            logger.debug(f"[neighbours] could not save the artist cache: {exc}")
    return dict(index)


def _index() -> dict:
    with _lock:
        if time.time() - _state["at"] > _REFRESH_SEC:
            _state["index"] = build_index()
            _state["at"] = time.time()
        return _state["index"]


def vote(artist: str, index: dict, *, min_others: int = MIN_OTHERS, min_share: float = MIN_SHARE):
    """(crate, share, songs) when the artist's filed songs clearly sit in one crate, else None."""
    votes: collections.Counter = collections.Counter()
    for key in artist_keys(artist):
        votes.update(index.get(key, {}))
    n = sum(votes.values())
    if n < min_others:
        return None
    crate, k = votes.most_common(1)[0]
    return (crate, k / n, n) if k / n >= min_share else None


def neighbour_crate(artist: str, index: Optional[dict] = None) -> str:
    """The crate this artist's other songs live in ('' when unsure). Never raises."""
    if os.getenv("NEIGHBOUR_ROUTING", "true").strip().lower() in ("0", "false", "no", "off"):
        return ""
    try:
        got = vote(artist, index if index is not None else _index())
        return got[0] if got else ""
    except Exception as exc:
        logger.debug(f"[neighbours] lookup unavailable for {artist!r}: {exc}")
        return ""
