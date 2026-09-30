"""
pipeline_guards.py — the three checks that keep the library clean (P0 of wiki/master-plan.md).

  1. ingest_lock()          one ingest run at a time, across threads AND across backend copies
  2. validate_download()    decodes? right length? (tiered: accept / flag / reject)
  3. DiskIdentityIndex      "is this Spotify song already in the library?" answered from the files' own tags

Pure standard library + mutagen. No Spotify, no Mongo, no network: everything is unit-tested.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from typing import Dict, NamedTuple, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

# ── 1. one ingest run at a time ──────────────────────────────────────────────────────────────────────────────
# A run lock file holds the PID of the owner. A lock whose owner is gone (crash, power cut) or that is older than
# STALE_LOCK_SECONDS is taken over, so a dead run can never block ingest forever.
STALE_LOCK_SECONDS = 6 * 3600
_thread_lock = threading.Lock()


def _pid_alive(pid: int) -> bool:
    """Is a process with this PID running? Never signals it (os.kill(pid, 0) would TERMINATE it on Windows)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)   # POSIX: signal 0 only checks existence
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextlib.contextmanager
def ingest_lock(lock_path: str):
    """
    Yields True when this caller owns the ingest run, False when another run (thread or process) is active.
    Never blocks. The caller must skip its work on False.
    """
    if not _thread_lock.acquire(blocking=False):
        yield False
        return
    fd = None
    try:
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                break
            except FileExistsError:
                if not _lock_is_stale(lock_path):
                    yield False
                    return
                with contextlib.suppress(OSError):
                    os.remove(lock_path)   # dead owner: take over, then retry once
        if fd is None:
            yield False
            return
        yield True
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.remove(lock_path)
        _thread_lock.release()


def _lock_is_stale(lock_path: str) -> bool:
    try:
        age = time.time() - os.path.getmtime(lock_path)
        if age > STALE_LOCK_SECONDS:
            return True
        with open(lock_path, "r") as fh:
            pid = int((fh.read() or "0").strip() or 0)
        return not _pid_alive(pid)
    except (OSError, ValueError):
        return True


# ── 2. is the download a good file? ──────────────────────────────────────────────────────────────────────────
# Tiers measured on this library's own data (reports/duration_scan_20260928.json, 1,730 tracks): 80% of files are
# within 5 s of Spotify's length, 88% within 10 s, then a thin smooth tail (radio edit vs album version) until ~30 s
# where the wrong-song/truncated files pile up. So: accept, flag, reject — not a hard 10 s line.
ACCEPT_SECONDS, ACCEPT_FRACTION = 8.0, 0.03
REJECT_SECONDS, REJECT_FRACTION = 30.0, 0.15
MIN_BYTES = 100_000          # a real song is never this small; the old guard was 1 KB


class Verdict(NamedTuple):
    ok: bool                 # False -> discard the file, do not file it
    flagged: bool            # True -> keep it, but list it in the health report
    reason: str
    local_s: float = 0.0
    diff_s: float = 0.0


def duration_tolerances(official_s: float) -> tuple[float, float]:
    """(accept_up_to, reject_beyond) in seconds for a song of this official length."""
    return (max(ACCEPT_SECONDS, ACCEPT_FRACTION * official_s),
            max(REJECT_SECONDS, REJECT_FRACTION * official_s))


def validate_download(path: str, official_duration_s: Optional[float]) -> Verdict:
    """
    Header-level check (no full decode, cheap): file exists and is big enough, opens as an MP3, has a real length,
    and the length agrees with Spotify's. Without an official duration only the file checks run.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return Verdict(False, False, "file missing")
    if size < MIN_BYTES:
        return Verdict(False, False, f"file too small ({size} bytes)")
    try:
        from mutagen.mp3 import MP3
        local = float(MP3(path).info.length)
    except Exception as exc:
        return Verdict(False, False, f"not a readable mp3 ({type(exc).__name__})")
    if local <= 1.0:
        return Verdict(False, False, "zero length", local_s=local)
    if not official_duration_s or official_duration_s <= 0:
        return Verdict(True, False, "ok (no official duration to compare)", local_s=local)
    diff = local - official_duration_s
    accept, reject = duration_tolerances(official_duration_s)
    if abs(diff) > reject:
        kind = "shorter" if diff < 0 else "longer"
        return Verdict(False, False, f"{abs(diff):.0f}s {kind} than Spotify's {official_duration_s:.0f}s "
                                     f"(limit {reject:.0f}s): wrong version, clip or truncated", local, diff)
    if abs(diff) > accept:
        return Verdict(True, True, f"{abs(diff):.0f}s off Spotify's length (flagged, not rejected)", local, diff)
    return Verdict(True, False, "ok", local, diff)


# ── 3. is this song already in the library? ──────────────────────────────────────────────────────────────────
class DiskIdentityIndex:
    """
    spotify_id -> path, read from the SPOTIFY_ID tag inside the mp3 files themselves (the file, not the database,
    is the truth; the database index can be stale). Reads are cached per (path, mtime) so a rescan of an
    unchanged library costs one stat per file.
    """

    def __init__(self, tag_reader=None):
        self._read = tag_reader or _read_spotify_id
        self._cache: Dict[str, tuple] = {}       # path -> (mtime, spotify_id)
        self._by_id: Dict[str, str] = {}

    def scan(self, root: str, skip_dirs=("staging", "quarantine", "_to_delete", "needsreview")) -> int:
        by_id: Dict[str, str] = {}
        seen = set()
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]
            for name in files:
                if not name.lower().endswith(".mp3"):
                    continue
                path = os.path.join(base, name)
                seen.add(path)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                cached = self._cache.get(path)
                if cached and cached[0] == mtime:
                    sid = cached[1]
                else:
                    sid = self._read(path)
                    self._cache[path] = (mtime, sid)
                if sid:
                    by_id.setdefault(sid, path)
        for gone in set(self._cache) - seen:
            del self._cache[gone]
        self._by_id = by_id
        return len(by_id)

    def find(self, spotify_id: str) -> str:
        """Path of the library file that already carries this Spotify id, or ''."""
        path = self._by_id.get(spotify_id or "", "")
        return path if path and os.path.isfile(path) else ""


def _read_spotify_id(path: str) -> str:
    try:
        from mutagen.id3 import ID3
        frame = ID3(path).get("TXXX:SPOTIFY_ID")
        return str(frame.text[0]).strip() if frame and frame.text else ""
    except Exception:
        return ""
