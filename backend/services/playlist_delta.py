"""
playlist_delta.py
=================
Read a Spotify playlist CHEAPLY: one small request to ask "did anything change?", and a real read
only when it did.

Why this exists
---------------
`ingest_download()` used to re-read the WHOLE ingest playlist on every check. With 2,070 songs that is
21 paged requests, and with CHECK_INTERVAL=60 about 1,260 requests an hour. Spotify locks a whole app
out for ~22 hours (HTTP 429, Retry-After ~82,000 s) at traffic far below that, and while it is locked
nothing downloads. The fix is to ask Spotify for the playlist's version stamp (`snapshot_id`, which
changes whenever a song is added, removed or moved) and to read songs only when the stamp moved:

    nothing changed .......... 1 request
    a few songs added ........ 1 + 2 requests (the top of the list and the end of the list)
    a full re-read ........... only on first run, after a big change, and once every 24 h

Why the top AND the end: Spotify appends new songs to the end by default, but has a setting that adds
them to the top. Reading both windows finds them either way, and the 24 h full re-read catches anything
an incremental read cannot (a song dropped into the middle).

The state is only advanced by the caller (`commit`) once the songs it was handed have been handled, so a
crash between "read" and "downloaded" can never turn into "unchanged, nothing to do".

The client is duck-typed (SpotifyService in production, a fake in tests):
    get_playlist_meta(playlist_id)                  -> {"snapshot_id", "total"}
    get_playlist_window(playlist_id, offset, limit) -> [track dict, ...]
    get_playlist_tracks_by_id(playlist_id, force_refresh=True) -> [track dict, ...]
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = str(_BACKEND_ROOT / "ingest_watch_state.json")

FULL_RESYNC_SECONDS = 24 * 3600     # a full read at least this often
HEAD_SIZE = 50                      # newest songs may be added at the TOP of a playlist
TAIL_MARGIN = 50                    # ... or at the END; read this many beyond the number that were added
MAX_INCREMENTAL_GROWTH = 50         # more new songs than this at once: just read everything
PAGE = 100                          # Spotify's maximum page size

_lock = threading.RLock()


# ── persistence ───────────────────────────────────────────────────────────────

def load_state(path: Optional[str] = None) -> Dict[str, dict]:
    """{playlist_id: {"snapshot_id", "total", "last_full_at", "checked_at"}}; {} when there is no file."""
    try:
        with open(path or STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning(f"[watch] could not read {path or STATE_FILE}: {exc} — will do a full read")
        return {}


def save_state(state: Dict[str, dict], path: Optional[str] = None) -> None:
    target = path or STATE_FILE
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, target)


# ── the read ─────────────────────────────────────────────────────────────────

@dataclass
class PlaylistRead:
    mode: str                                    # "skip" | "incremental" | "full"
    tracks: List[dict] = field(default_factory=list)
    total: int = 0
    snapshot_id: str = ""
    was_full: bool = False


def _dedupe(tracks: List[dict]) -> List[dict]:
    seen, out = set(), []
    for t in tracks:
        tid = t.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            out.append(t)
    return out


def _read_window(client, playlist_id: str, offset: int, count: int) -> List[dict]:
    """`count` songs from `offset`, in pages of at most 100."""
    out: List[dict] = []
    while count > 0:
        limit = min(PAGE, count)
        page = client.get_playlist_window(playlist_id, offset, limit)
        out.extend(page)
        if len(page) < limit:                    # ran off the end of the playlist
            break
        offset += limit
        count -= limit
    return out


def plan_windows(total: int, growth: int) -> List[tuple]:
    """(offset, count) windows to read for an incremental look: the top, and the end — merged when they touch."""
    if total <= 0:
        return []
    head = min(HEAD_SIZE, total)
    tail_count = min(total, max(growth, 0) + TAIL_MARGIN)
    tail_offset = total - tail_count
    if tail_offset <= head:                      # the two windows overlap or touch: one read covers both
        return [(0, total if tail_offset <= 0 else max(head, tail_offset + tail_count))]
    return [(0, head), (tail_offset, tail_count)]


def read_playlist(client, playlist_id: str, prev: Optional[dict] = None, *,
                  force_full: bool = False, now: Optional[float] = None) -> PlaylistRead:
    """
    Decide how much of the playlist to read, read it, and say what was done. Raises whatever the
    client raises (rate limit, 403 ...) — the caller decides how to report it.
    """
    now = time.time() if now is None else now
    prev = prev or {}
    meta = client.get_playlist_meta(playlist_id)                       # the one cheap request
    snapshot = (meta.get("snapshot_id") or "").strip()
    total = int(meta.get("total") or 0)
    prev_total = int(prev.get("total") or 0)
    growth = total - prev_total
    stale = (now - float(prev.get("last_full_at") or 0)) >= FULL_RESYNC_SECONDS
    unchanged = bool(snapshot) and snapshot == prev.get("snapshot_id")

    if force_full or not prev or stale or growth > MAX_INCREMENTAL_GROWTH:
        why = ("forced" if force_full else "first read" if not prev else
               "24 h re-check" if stale else f"{growth} new songs at once")
        logger.info(f"[watch] full read of {playlist_id} ({why}, {total} songs)")
        tracks = client.get_playlist_tracks_by_id(playlist_id, force_refresh=True)
        return PlaylistRead("full", _dedupe(list(tracks)), total or len(tracks), snapshot, was_full=True)

    if unchanged:
        return PlaylistRead("skip", [], total, snapshot)

    tracks: List[dict] = []
    for offset, count in plan_windows(total, growth):
        tracks.extend(_read_window(client, playlist_id, offset, count))
    logger.info(f"[watch] {playlist_id} changed ({prev_total} -> {total} songs): read {len(tracks)} entries")
    return PlaylistRead("incremental", _dedupe(tracks), total, snapshot)


def commit(playlist_id: str, read: PlaylistRead, *, now: Optional[float] = None,
           path: Optional[str] = None) -> None:
    """Remember that everything `read` returned has been handled: from now on an unchanged stamp means 'skip'."""
    now = time.time() if now is None else now
    with _lock:
        state = load_state(path)
        entry = dict(state.get(playlist_id) or {})
        entry.update({"snapshot_id": read.snapshot_id, "total": read.total, "checked_at": now})
        if read.was_full:                        # only a FULL read counts as "everything was seen"
            entry["last_full_at"] = now
        state[playlist_id] = entry
        save_state(state, path)
