"""
download_verifier.py
====================
Catch a download that the app believes it finished but that has no file.

The problem
-----------
A song is written to the done-list (ingest_tracks.json) the moment its download is recorded as a success.
Nothing ever looks again. If the file then does not exist — the move failed after the bookkeeping, a
post-processing step removed it — the app considers the song "done" forever and never retries it. Four
songs from one playlist were found in exactly that state: in the done-list, no failures, no file.

The check
---------
Every successful download is recorded here. A few minutes later (default 10) the library is searched for
that file's name ANYWHERE — so a song you moved by hand to another folder is still found. If it is
missing, the song is re-queued through requeue_service (which forgets it in the done-list and downloads it
again on the next cycle). Twice at most; after that it is listed in `gave_up` for a human to look at.

Two deliberate limits keep this from ever doing damage:
* It checks ONCE, shortly after the download. A song you delete a week later on purpose is never
  resurrected — only a download that never landed is retried.
* If the library looks empty (a drive not connected, a wrong path) nothing is re-queued, and at most
  MAX_REQUEUE_PER_PASS songs are re-queued in one pass, so a mistake cannot become a mass re-download.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = str(_BACKEND_ROOT / "ingest_verify.json")

MIN_AGE_SECONDS = 600            # look no sooner than this after the download was recorded
MAX_ATTEMPTS = 2                 # re-queue a missing song at most this many times
MAX_REQUEUE_PER_PASS = 20
_lock = threading.RLock()
_SUFFIX = re.compile(r"_\d+$")   # collision names: "Song - Artist_1.mp3"


def _read(path: Optional[str]) -> dict:
    try:
        with open(path or STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("pending", [])
            data.setdefault("attempts", {})
            data.setdefault("gave_up", [])
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"[verify] could not read {path or STATE_FILE}: {exc}")
    return {"pending": [], "attempts": {}, "gave_up": []}


def _write(state: dict, path: Optional[str]) -> None:
    target = path or STATE_FILE
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, target)


def record_done(track_id: str, title: str, artist: str, filename: str, *,
                now: Optional[float] = None, path: Optional[str] = None) -> None:
    """Remember a download that was just recorded as a success, so it can be double-checked shortly after."""
    if not (track_id and filename):
        return
    now = time.time() if now is None else now
    with _lock:
        state = _read(path)
        state["pending"] = [e for e in state["pending"] if e.get("id") != track_id]
        state["pending"].append({"id": track_id, "title": title, "artist": artist,
                                 "filename": os.path.basename(filename), "done_at": now})
        _write(state, path)


def _stems(base_dir: str) -> set:
    """Every mp3 name under base_dir (lower case, without extension and without a collision suffix)."""
    out = set()
    if not os.path.isdir(base_dir):
        return out
    for _root, _dirs, files in os.walk(base_dir):
        for f in files:
            if f.lower().endswith(".mp3"):
                stem = f[:-4].strip().lower()
                out.add(stem)
                out.add(_SUFFIX.sub("", stem))
    return out


def _expected(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0].strip().lower()
    return _SUFFIX.sub("", stem)


def process_pending(base_dir: str, requeue_fn: Callable[..., dict], *, now: Optional[float] = None,
                    min_age_s: float = MIN_AGE_SECONDS, max_attempts: int = MAX_ATTEMPTS,
                    path: Optional[str] = None) -> Dict[str, list]:
    """
    Double-check every recorded download that is old enough. Returns
    {"ok": [...], "requeued": [...], "gave_up": [...], "waiting": n} (lists of track ids).
    """
    now = time.time() if now is None else now
    summary: Dict[str, list] = {"ok": [], "requeued": [], "gave_up": [], "waiting": 0}
    with _lock:
        state = _read(path)
        due = [e for e in state["pending"] if now - float(e.get("done_at") or 0) >= min_age_s]
        summary["waiting"] = len(state["pending"]) - len(due)
        if not due:
            return summary

        stems = _stems(base_dir)
        if not stems:                                          # library unreachable / empty: do nothing, try again later
            logger.warning(f"[verify] no mp3 files found under {base_dir!r} — not re-queuing anything")
            summary["waiting"] += len(due)
            return summary

        keep = [e for e in state["pending"] if e not in due]
        requeue: List[dict] = []
        for e in due:
            tid = e["id"]
            if _expected(e["filename"]) in stems:
                state["attempts"].pop(tid, None)
                summary["ok"].append(tid)
                continue
            tries = int(state["attempts"].get(tid, 0))
            if tries >= max_attempts:
                state["gave_up"].append({"id": tid, "title": e.get("title", ""), "artist": e.get("artist", ""),
                                         "filename": e.get("filename", ""), "at": now})
                state["attempts"].pop(tid, None)
                summary["gave_up"].append(tid)
                logger.error(f"[verify] giving up on {e.get('title')!r} — {tries} re-downloads and still no file")
                continue
            if len(requeue) >= MAX_REQUEUE_PER_PASS:
                keep.append(e)                                  # over the cap: look again next pass
                continue
            requeue.append(e)

        if requeue:
            try:
                requeue_fn([{"spotify_id": e["id"], "title": e.get("title", ""), "artist": e.get("artist", "")}
                            for e in requeue],
                           reason="download recorded as done but no file was found (auto-check)")
            except Exception as exc:                            # noqa: BLE001
                logger.warning(f"[verify] could not re-queue {len(requeue)} song(s): {exc} — will try again")
                keep.extend(requeue)                            # keep them; nothing is lost
            else:
                for e in requeue:
                    state["attempts"][e["id"]] = int(state["attempts"].get(e["id"], 0)) + 1
                    summary["requeued"].append(e["id"])
                    logger.warning(f"[verify] {e.get('title')} - {e.get('artist')}: recorded as done but no file — re-queued")
        state["pending"] = keep
        _write(state, path)
    return summary


def gave_up(path: Optional[str] = None) -> List[dict]:
    """Songs that still had no file after every retry (for a human to look at)."""
    with _lock:
        return list(_read(path)["gave_up"])
