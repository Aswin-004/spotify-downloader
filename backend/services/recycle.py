"""
recycle.py
==========
Remove files in a RECOVERABLE way.

* Windows: sends the file to the Recycle Bin via the shell API (SHFileOperationW with
  FOF_ALLOWUNDO) — no third-party dependency. Right-click the Recycle Bin -> Restore
  puts it back exactly where it was.
* Any platform / fallback: moves the file into `<root>/_TO_DELETE/<timestamp>/...`,
  keeping its relative path, so nothing is ever destroyed by this module.

There is deliberately no permanent-delete function here.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

QUARANTINE_DIRNAME = "_TO_DELETE"

_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040          # <- this is what makes it a Recycle Bin move, not a hard delete
_FOF_NOERRORUI = 0x0400


def send_to_recycle_bin(path: str) -> bool:
    """Send `path` to the Windows Recycle Bin. Returns True only if the file is really gone."""
    if sys.platform != "win32":
        return False
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False
    try:
        import ctypes
        from ctypes import POINTER, Structure, c_uint
        from ctypes.wintypes import BOOL, HWND, LPCWSTR, UINT

        class SHFILEOPSTRUCTW(Structure):
            _fields_ = [
                ("hwnd", HWND),
                ("wFunc", UINT),
                ("pFrom", LPCWSTR),
                ("pTo", LPCWSTR),
                ("fFlags", c_uint),
                ("fAnyOperationsAborted", BOOL),
                ("hNameMappings", c_uint),
                ("lpszProgressTitle", LPCWSTR),
            ]

        shfileop = ctypes.windll.shell32.SHFileOperationW
        shfileop.argtypes = [POINTER(SHFILEOPSTRUCTW)]
        shfileop.restype = ctypes.c_int

        op = SHFILEOPSTRUCTW()
        op.hwnd = 0
        op.wFunc = _FO_DELETE
        op.pFrom = path + "\0"            # pFrom must be double-null-terminated
        op.pTo = None
        op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_NOERRORUI | _FOF_SILENT
        op.fAnyOperationsAborted = 0
        op.hNameMappings = 0
        op.lpszProgressTitle = None

        rc = shfileop(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted:
            logger.warning(f"[recycle] SHFileOperation failed for {path} (rc={rc})")
            return False
        return not os.path.exists(path)
    except Exception as exc:
        logger.warning(f"[recycle] Recycle Bin move failed for {path}: {exc}")
        return False


def quarantine(path: str, root: str, stamp: Optional[str] = None) -> Optional[str]:
    """Move `path` to `<root>/_TO_DELETE/<stamp>/<relative path>`. Returns the new path, or None."""
    src = Path(path).resolve()
    root_p = Path(root).resolve()
    if not src.is_file():
        return None
    try:
        rel = src.relative_to(root_p)
    except ValueError:
        rel = Path(src.name)
    dest = root_p / QUARANTINE_DIRNAME / (stamp or time.strftime("%Y%m%d_%H%M%S")) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while dest.exists():                        # never overwrite an earlier quarantined file
        dest = dest.with_name(f"{rel.stem}_{n}{rel.suffix}")
        n += 1
    shutil.move(str(src), str(dest))
    return str(dest)


def remove_file(path: str, root: str, mode: str = "recycle") -> Optional[str]:
    """
    Recoverably remove `path`.

    mode "recycle": Recycle Bin, falling back to quarantine if that isn't possible.
    mode "quarantine": always quarantine.
    Returns "recycle-bin", the quarantine path, or None if the file could not be removed.
    """
    if mode == "recycle" and send_to_recycle_bin(path):
        return "recycle-bin"
    try:
        return quarantine(path, root)
    except Exception as exc:
        logger.warning(f"[recycle] could not quarantine {path}: {exc}")
        return None
