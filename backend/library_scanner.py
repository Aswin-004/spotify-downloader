"""
Library Scanner — centralized file discovery with hard exclusion rules.

``is_excluded_path(path)`` is the single gate that prevents migration and
indexing from ever touching:
  • Quarantine/  — immutable archive storage
  • TempFiles/, cache/, __pycache__, .git, node_modules
  • *.trimmed.mp3, *_1.mp3, *_2.mp3, *.tmp, *.partial

All checks are case-insensitive and use resolved absolute paths so that
Windows drive-letter casing and relative-path traversal cannot bypass them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Generator

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from config import config

# Pre-compile exclusion rules once at import time.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    d.lower() for d in config.EXCLUDED_SCAN_DIRS
)
_EXCLUDED_RE: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in config.EXCLUDED_PATTERNS
]


def is_excluded_path(path: str | Path) -> bool:
    """
    Return True if *path* must be skipped by the migration scanner.

    Exclusion triggers when *any* directory component of the resolved path
    matches EXCLUDED_SCAN_DIRS (case-insensitive), OR when the filename
    matches any EXCLUDED_PATTERNS regex.

    Examples that are excluded:
        .../Quarantine/House/track.mp3       ← Quarantine dir component
        .../TempFiles/whatever.mp3           ← TempFiles dir component
        .../House/Artist/track.trimmed.mp3   ← trimmed pattern
        .../House/Artist/track_1.mp3         ← collision suffix pattern
    """
    p = Path(path).resolve()
    # Check every directory component (all parts except the filename itself)
    for part in p.parts[:-1]:
        if part.lower() in _EXCLUDED_DIRS:
            return True
    # Check filename against patterns
    for pattern in _EXCLUDED_RE:
        if pattern.search(p.name):
            return True
    return False


def scan_library(base_dir: str | Path, glob: str = "*.mp3") -> Generator[Path, None, None]:
    """
    Yield eligible file paths under *base_dir* that pass ``is_excluded_path``.

    Skips excluded directories entirely (no descent) and excluded filenames.
    Logs each exclusion at DEBUG level.
    """
    base = Path(base_dir).resolve()
    for fp in sorted(base.rglob(glob)):
        if is_excluded_path(fp):
            logger.debug(f"[SKIP][EXCLUDED] {fp.relative_to(base)}")
            continue
        yield fp
