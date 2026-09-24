"""
Learn from the songs you move by hand, with no command to run.

Every time the app files or moves a song it writes the crate into the file (ID3 TXXX "filed_crate"). A song
that sits in a different crate folder than its "filed_crate" says was moved by YOU (Explorer, Rekordbox,
drag-and-drop ...). For each such song, scan():

  1. remembers the artist → crate in artist_memory (source "hand_move", confidence 0.75 — enough for the
     router to follow it), so the artist's NEXT song files itself there;
  2. syncs the genre tag (TCON) to the crate and records the file in the hand-placement ledger
     (reports/user_placed.json), so no tool ever suggests moving it back;
  3. points the library index at the new path and re-stamps "filed_crate".

A song with no "filed_crate" yet (everything in the library before this existed) is stamped with the folder
it is in now: that is the baseline, nothing is learned from it. Placeholder artists ("Unknown", "Indian",
"Electronic" ...) are never learned. The playlist watcher runs scan() on every cycle (every ~10 minutes);
only files that are new since the last pass have their tags read, so a pass over ~2,000 songs is cheap.

App code that moves a file must call stamp() on the new path, or the move would look like yours.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

TAG = "filed_crate"
HAND_MOVE_CONFIDENCE = 0.75
LOG_NAME = "hand_moves.jsonl"

_seen: Dict[str, tuple] = {}          # path -> (size, mtime) of files already checked
_lock = threading.Lock()


def _library_root() -> Path:
    from config import config
    return Path(config.BASE_DOWNLOAD_DIR) / "Library"


def _crate_of(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    return rel.parts[0] if len(rel.parts) == 2 else ""       # Library/<crate>/<song>.mp3 only


def read_stamp(path) -> str:
    try:
        from mutagen.id3 import ID3
        frames = ID3(str(path)).getall(f"TXXX:{TAG}")
        return str(frames[0].text[0]).strip() if frames and frames[0].text else ""
    except Exception:
        return ""


def stamp(path, crate: Optional[str] = None) -> None:
    """Record that the APP put this file in `crate` (default: the folder it is in). Never raises."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
        p = Path(path)
        crate = crate or p.parent.name
        try:
            tags = ID3(str(p))
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall(f"TXXX:{TAG}")
        tags.add(TXXX(encoding=3, desc=TAG, text=[crate]))
        tags.save(str(p), v2_version=3 if getattr(tags, "version", (2, 4, 0))[1] == 3 else 4)
    except Exception as exc:
        logger.debug(f"[hand-moves] could not stamp {path}: {exc}")


def _first_artist(path) -> str:
    try:
        from mutagen.id3 import ID3
        frame = ID3(str(path)).get("TPE1")
        text = str(frame.text[0]).strip() if frame is not None and frame.text else ""
    except Exception:
        return ""
    for sep in (",", " & ", " feat.", " ft.", " x ", ";", "/"):
        text = text.split(sep)[0]
    return text.strip()


def _learn(path: Path, old_crate: str, new_crate: str, *, record_move=None, index_fn=None, tag_fn=None,
           placed_path=None) -> dict:
    import library_resort as lr
    from services.artist_recovery import is_placeholder_artist

    artist = _first_artist(path)
    learned = ""
    genre = lr.CRATE_TO_GENRE.get(new_crate, "")
    if genre and artist and not is_placeholder_artist(artist):
        if record_move is None:
            from services.artist_memory_service import record_move
        record_move(artist, genre, source="hand_move", min_confidence=HAND_MOVE_CONFIDENCE)
        learned = artist

    tcon = lr.FOLDER_TO_TCON.get(new_crate)
    if tcon:
        try:
            (tag_fn or lr.write_tags)(str(path), genre=tcon)
        except Exception as exc:
            logger.debug(f"[hand-moves] genre tag not synced for {path.name}: {exc}")
    try:
        placed = lr.load_user_placed(placed_path)
        placed[lr.placement_key(str(path))] = new_crate
        lr.save_user_placed(placed, placed_path)
    except Exception as exc:
        logger.debug(f"[hand-moves] ledger not updated for {path.name}: {exc}")
    try:
        old_path = str(path.parent.parent / old_crate / path.name)
        (index_fn or lr.update_library_index)(old_path, str(path), f"Library/{new_crate}")
    except Exception as exc:
        logger.debug(f"[hand-moves] library index not updated for {path.name}: {exc}")
    stamp(path, new_crate)
    return {"file": path.name, "from": old_crate, "to": new_crate, "artist_learned": learned}


def scan(root: Optional[Path] = None, *, learn_fn: Optional[Callable] = None, log_dir: Optional[Path] = None) -> dict:
    """One pass over Library/<crate>/*.mp3. Returns counts plus the hand moves found. Never raises."""
    root = Path(root or _library_root())
    stats = {"checked": 0, "baselined": 0, "hand_moves": []}
    if not root.is_dir():
        return stats
    try:
        import library_resort as lr
        skip = set(lr.SKIP_FOLDERS)
    except Exception:
        skip = set()
    learn_fn = learn_fn or _learn
    with _lock:
        for crate_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in skip):
            for f in crate_dir.glob("*.mp3"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                key = str(f)
                sig = (st.st_size, st.st_mtime)
                if _seen.get(key) == sig:
                    continue
                stats["checked"] += 1
                crate = crate_dir.name
                filed = read_stamp(f)
                if not filed:
                    stamp(f, crate)
                    stats["baselined"] += 1
                elif filed != crate:
                    try:
                        stats["hand_moves"].append(learn_fn(f, filed, crate))
                    except Exception as exc:
                        logger.warning(f"[hand-moves] could not learn from {f.name}: {exc}")
                        continue
                try:
                    st = f.stat()
                    _seen[key] = (st.st_size, st.st_mtime)
                except OSError:
                    pass
    for move in stats["hand_moves"]:
        who = f"learned {move['artist_learned']} → {move['to']}" if move["artist_learned"] else "artist unknown, nothing learned"
        logger.info(f"[hand-moves] you moved {move['file']}: {move['from']} → {move['to']} ({who})")
    if stats["hand_moves"]:
        _append_log(stats["hand_moves"], log_dir)
    return stats


def _append_log(moves, log_dir: Optional[Path] = None) -> None:
    try:
        d = Path(log_dir) if log_dir else Path(__file__).resolve().parent.parent / "reports"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LOG_NAME, "a", encoding="utf-8") as fh:
            for m in moves:
                fh.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **m}, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug(f"[hand-moves] log not written: {exc}")


def reset_cache() -> None:
    """Forget which files were already checked (tests; or after the library moved)."""
    with _lock:
        _seen.clear()
