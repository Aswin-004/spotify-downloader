"""
requeue_service.py
==================
Make tracks download AGAIN on the next ingest cycle — used after wrong files
(karaoke / instrumental / wrong song) have been removed from the library.

Why a dedicated mechanism is needed
-----------------------------------
`auto_downloader.ingest_download()` only downloads tracks that (a) are in the
configured ingest PLAYLIST and (b) are absent from `ingest_tracks.json` and
(c) have fewer than MAX_FAIL_ATTEMPTS recorded failures. Deleting a file alone
therefore changes nothing: the track's ID is still in the history file, so the
next cycle skips it as "already done" — and a track that was downloaded ad hoc
(never in the playlist) is never looked at again at all.

`requeue_tracks()` fixes all three:
  1. forgets the IDs in ingest history          (playlist tracks re-download)
  2. resets their failure counters              (the 3-strike permanent skip can't block them)
  3. adds them to `ingest_requeue.json`         (tracks NOT in the playlist are also fetched)

`merge_requeued_tracks()` is called by ingest_download() each cycle and appends the
queued tracks to that cycle's work list. An entry leaves the queue when its
download succeeds (or its file turns out to exist already).

The queue file lives next to ingest_tracks.json (backend/ingest_requeue.json).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
REQUEUE_FILE = str(_BACKEND_ROOT / "ingest_requeue.json")
MAX_RESOLVE_ATTEMPTS = 10           # give up on an entry Spotify can't resolve after this many cycles
_lock = threading.RLock()

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def entry_key(entry: dict) -> str:
    """Stable identity for a queue entry: the Spotify id, else 'title|artist'."""
    sid = (entry.get("spotify_id") or "").strip()
    return sid or f"{_norm(entry.get('title', ''))}|{_norm(entry.get('artist', ''))}"


# ── persistence ───────────────────────────────────────────────────────────────

def _read() -> List[dict]:
    try:
        with open(REQUEUE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return [e for e in data.get("tracks", []) if isinstance(e, dict)]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning(f"[requeue] could not read {REQUEUE_FILE}: {exc}")
        return []


def _write(entries: List[dict]) -> None:
    tmp = REQUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"tracks": entries, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, REQUEUE_FILE)


def load_requeue() -> List[dict]:
    with _lock:
        return _read()


def add_to_requeue(entries: Iterable[dict], reason: str = "") -> int:
    """Add entries ({spotify_id?, title, artist}); duplicates are ignored. Returns how many were new."""
    with _lock:
        current = _read()
        have = {entry_key(e) for e in current}
        added = 0
        for e in entries:
            if not (e.get("spotify_id") or e.get("title")):
                continue
            key = entry_key(e)
            if key in have:
                continue
            current.append({
                "spotify_id": (e.get("spotify_id") or "").strip(),
                "title": e.get("title", ""),
                "artist": e.get("artist", ""),
                "reason": reason or e.get("reason", ""),
                "queued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "attempts": 0,
            })
            have.add(key)
            added += 1
        if added:
            _write(current)
        return added


def remove_from_requeue(keys: Iterable[str]) -> int:
    """Remove entries whose Spotify id OR 'title|artist' key is in `keys`. Returns how many were removed."""
    wanted = {k for k in keys if k}
    if not wanted:
        return 0
    with _lock:
        current = _read()
        kept = [e for e in current
                if (e.get("spotify_id") or "") not in wanted and entry_key(e) not in wanted]
        removed = len(current) - len(kept)
        if removed:
            _write(kept)
        return removed


def key_for_track(track_id: str, title: str, artist: str) -> List[str]:
    """Every key a queued entry for this track might be stored under."""
    return [track_id or "", f"{_norm(title)}|{_norm(artist)}"]


# ── the operation the cleanup tool calls ─────────────────────────────────────────

def requeue_tracks(entries: List[dict], reason: str = "") -> dict:
    """
    Arrange for `entries` to be downloaded on the next ingest cycle:
    forget them in ingest history, reset failure counters, add them to the queue.
    """
    from services import auto_downloader as ad          # lazy: heavy module, avoids import cycles

    ids = [e["spotify_id"] for e in entries if e.get("spotify_id")]
    history_removed = ad.remove_tracks_from_history(ids)["removed"] if ids else 0

    failures_reset = 0
    if ids:
        counts = ad._load_failure_counts()
        for tid in ids:
            if counts.pop(tid, None) is not None:
                failures_reset += 1
        if failures_reset:
            ad._save_failure_counts(counts)

    queued = add_to_requeue(entries, reason)
    logger.info(f"[requeue] queued={queued} history_removed={history_removed} failures_reset={failures_reset}")
    return {"queued": queued, "history_removed": history_removed, "failures_reset": failures_reset}


# ── used by ingest_download() ────────────────────────────────────────────────────

def _track_dict(track: dict) -> dict:
    """Same shape SpotifyService.get_playlist_tracks_by_id() produces."""
    artists = track.get("artists") or []
    album = track.get("album") or {}
    return {
        "id": track["id"],
        "title": track.get("name", ""),
        "artist": artists[0]["name"] if artists else "Unknown",
        "artist_id": artists[0].get("id", "") if artists else "",
        "duration_ms": track.get("duration_ms"),
        "album_art_url": (album.get("images") or [{}])[0].get("url"),
        "release_date": album.get("release_date", ""),
    }


def resolve_track_info(sp_service, entry: dict) -> Optional[dict]:
    """
    Turn a queue entry into the track dict ingest_download() works with.
    Returns None when it can't be resolved right now (rate limit, no confident match).
    """
    from services.strict_matcher import _fuzzy_ratio, clean_title

    sid = (entry.get("spotify_id") or "").strip()
    if sid:
        return _track_dict(sp_service._call_with_backoff(sp_service.sp.track, sid))

    title, artist = entry.get("title", ""), entry.get("artist", "")
    if not title:
        return None
    query = f'track:{title} artist:{artist}' if artist else f"track:{title}"
    res = sp_service._call_with_backoff(sp_service.sp.search, q=query, type="track", limit=5)
    want_title = clean_title(title)
    for tr in (res.get("tracks") or {}).get("items", []):
        t_sim = _fuzzy_ratio(want_title, clean_title(tr.get("name", "")))
        a_sim = max((_fuzzy_ratio(artist.lower(), a.get("name", "").lower())
                     for a in tr.get("artists", [])), default=0.0) if artist else 1.0
        if t_sim >= 0.90 and a_sim >= 0.80:
            return _track_dict(tr)
    return None


def merge_requeued_tracks(new_tracks: List[dict], sp_service, failure_counts: dict,
                          max_fail_attempts: int = 3) -> List[dict]:
    """Append this cycle's queued tracks to `new_tracks` (which they bypass the history filter for)."""
    from services.spotify_service import is_rate_limited

    entries = load_requeue()
    if not entries:
        return new_tracks

    out = list(new_tracks)
    have = {t["id"] for t in out}
    unresolved_keys: List[str] = []
    for e in entries:
        sid = (e.get("spotify_id") or "").strip()
        if sid and sid in have:
            continue
        if sid and failure_counts.get(sid, 0) >= max_fail_attempts:
            continue
        if is_rate_limited():
            break                                   # try again next cycle; entries stay queued
        try:
            info = resolve_track_info(sp_service, e)
        except Exception as exc:
            logger.warning(f"[requeue] could not resolve {e.get('title')!r}: {exc}")
            continue
        if info is None:
            unresolved_keys.append(entry_key(e))
            continue
        if info["id"] in have:
            continue
        out.append(info)
        have.add(info["id"])
        logger.info(f"[requeue] re-downloading: {info['title']} - {info['artist']}")

    _count_failed_resolutions(unresolved_keys)
    return out


def _count_failed_resolutions(keys: List[str]) -> None:
    """Bump `attempts` for entries Spotify couldn't resolve; drop ones that never will."""
    if not keys:
        return
    with _lock:
        current = _read()
        kept = []
        for e in current:
            if entry_key(e) in keys:
                e["attempts"] = int(e.get("attempts", 0)) + 1
                if e["attempts"] >= MAX_RESOLVE_ATTEMPTS:
                    logger.warning(f"[requeue] giving up on {e.get('title')!r} — {e['attempts']} failed lookups")
                    continue
            kept.append(e)
        _write(kept)
