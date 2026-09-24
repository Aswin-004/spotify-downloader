"""
genre_playlists.py
==================
Playlists YOU own whose job is a genre: "DJ - House", "DJ - Punjabi", ... A song added to one of them is
filed in that crate — no guessing, whoever the artist is.

Why this is the reliable way to file a song
-------------------------------------------
At download time the app only knows an artist if they are on its own lists (your override table, what it
learned from your moves, the built-in knowledge base). For everyone else — 88 of the 92 new songs in a
typical "Chill House" playlist — no source can say what genre it is, and the song lands in the Electronic
catch-all for you to sort by hand. But YOU know the genre at the moment you add the song. A playlist per
genre captures that knowledge once, at the only moment it costs nothing.

Rules
-----
* A song in a genre playlist is filed in that crate, ahead of every other routing rule.
* A song that is in BOTH the ingest playlist and a genre playlist goes to the genre playlist's crate.
* Spotify only lets this app read playlists the logged-in user OWNS (anything else answers 403). To use
  someone else's playlist, select all its songs and add them to a playlist of your own.
* A song that is already in the library is not moved (the downloader skips it); the conflict is logged.

Configuration lives in backend/genre_playlists.json (managed by manage_genre_playlists.py).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
STORE_FILE = str(_BACKEND_ROOT / "genre_playlists.json")
_lock = threading.RLock()
_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


# ── crate names ───────────────────────────────────────────────────────────────

def valid_crates() -> List[str]:
    """Every folder the library has a crate for (House, UK Garage, Bollywood ...), minus the catch-all."""
    from services.genre_router import GENRE_TAXONOMY
    return sorted({entry[1] for entry in GENRE_TAXONOMY.values()} - {"Electronic"})


def resolve_crate(name: str) -> str:
    """'house' / 'Tech House' / 'uk garage' -> the crate label ('House' / 'House' / 'UK Garage'); '' if unknown."""
    text = (name or "").strip()
    if not text:
        return ""
    crates = valid_crates()
    for label in crates:
        if label.lower() == text.lower():
            return label
    from services.genre_router import GENRE_TAXONOMY, normalize_genre
    canonical = normalize_genre(text)
    label = GENRE_TAXONOMY[canonical][1] if canonical in GENRE_TAXONOMY else ""
    return label if label in crates else ""


def crate_folder(crate: str) -> str:
    """'House' -> 'Library/House'; '' for a crate the library does not have (a bad config never invents a folder)."""
    if crate not in valid_crates():
        return ""
    from services.genre_router import LIBRARY_ROOT
    return f"{LIBRARY_ROOT}/{crate}"


def extract_playlist_id(text: str) -> str:
    """An id, a spotify:playlist: URI or an open.spotify.com URL (with or without ?pi=...) -> the 22-char id; '' if invalid."""
    t = (text or "").strip().split("?", 1)[0].rstrip("/")
    candidate = t.split("/")[-1].split(":")[-1]
    return candidate if _ID_RE.match(candidate) else ""


# ── configuration file ────────────────────────────────────────────────────────────

def load(path: Optional[str] = None) -> List[dict]:
    """[{'id', 'crate', 'name', 'added'}, ...]; entries with a crate the library does not have are ignored."""
    try:
        with open(path or STORE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning(f"[genre-playlists] could not read {path or STORE_FILE}: {exc}")
        return []
    out = []
    for e in data.get("playlists", []) if isinstance(data, dict) else []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        if not crate_folder(e.get("crate", "")):
            logger.warning(f"[genre-playlists] ignoring {e.get('id')}: {e.get('crate')!r} is not a crate in your library")
            continue
        out.append(e)
    return out


def _write(entries: List[dict], path: Optional[str]) -> None:
    target = path or STORE_FILE
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"playlists": entries}, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, target)


def add(playlist: str, crate: str, *, name: str = "", path: Optional[str] = None) -> dict:
    """Register (or re-point) a playlist. Raises ValueError with a plain-language reason when it cannot."""
    pid = extract_playlist_id(playlist)
    if not pid:
        raise ValueError("that does not look like a Spotify playlist link or id")
    label = resolve_crate(crate)
    if not label:
        raise ValueError(f"{crate!r} is not one of your crates. Choose one of: {', '.join(valid_crates())}")
    entry = {"id": pid, "crate": label, "name": name, "added": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _lock:
        entries = [e for e in load(path) if e["id"] != pid]
        entries.append(entry)
        _write(entries, path)
    return entry


def remove(playlist: str, *, path: Optional[str] = None) -> bool:
    pid = extract_playlist_id(playlist)
    with _lock:
        entries = load(path)
        kept = [e for e in entries if e["id"] != pid]
        if len(kept) == len(entries):
            return False
        _write(kept, path)
        return True


# ── used by the ingest cycle ────────────────────────────────────────────────────────

def merge_tracks(main: Iterable[dict], genre_reads: Iterable[Tuple[dict, List[dict]]]) -> List[dict]:
    """
    Combine the ingest playlist's songs with the songs of each genre playlist.

    genre_reads: [(config entry, [track dicts]), ...]. Every song from a genre playlist carries
    `forced_genre` (its crate) and `source_playlist`; when a song is in several places the FIRST genre
    playlist wins over the plain ingest playlist. Order is stable (ingest songs first).
    """
    merged: Dict[str, dict] = {}
    order: List[str] = []
    for t in main:
        if t.get("id") and t["id"] not in merged:
            merged[t["id"]] = dict(t)
            order.append(t["id"])
    for entry, tracks in genre_reads:
        for t in tracks:
            tid = t.get("id")
            if not tid:
                continue
            if tid in merged and merged[tid].get("forced_genre"):
                if merged[tid]["forced_genre"] != entry["crate"]:
                    logger.warning(f"[genre-playlists] {t.get('title')!r} is in two genre playlists "
                                   f"({merged[tid]['forced_genre']} and {entry['crate']}) — keeping the first")
                continue
            base = merged.get(tid) or dict(t)
            base["forced_genre"] = entry["crate"]
            base["source_playlist"] = entry["id"]
            if tid not in merged:
                order.append(tid)
            merged[tid] = base
    return [merged[i] for i in order]
