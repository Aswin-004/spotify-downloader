# Music Library Migrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganise a 50+ artist-folder music library into four flat language/genre buckets (Punjabi, English, Hindi, House) with MD5-verified copy→delete, config-driven mapping, interactive fallback for unknowns, undo log, CLI runner, and a Telegram `/organize` command.

**Architecture:** `services/library_migrator.py` owns all logic (no Flask dependency). `run_migrate.py` is a thin argparse CLI. The Telegram `/organize` command dispatches a daemon thread that calls the same service — identical pattern to `_run_spotify_download`. Config lives in `config/artist_categories.json` (pre-seeded; user answers saved back automatically).

**Tech Stack:** Python stdlib (`pathlib`, `shutil`, `hashlib`, `json`, `argparse`, `dataclasses`), `loguru` (already in requirements)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `backend/config/artist_categories.json` | Artist→category mapping |
| Create | `backend/services/library_migrator.py` | All migration logic |
| Create | `backend/run_migrate.py` | CLI entry point |
| Modify | `backend/telegram_bot.py` | Add `/organize` handler + register it |
| Create | `backend/tests/test_library_migrator.py` | All migrator tests |

---

## Task 1: Config file and load/save helpers

**Files:**
- Create: `backend/config/artist_categories.json`
- Create: `backend/services/library_migrator.py` (config section only)
- Create: `backend/tests/test_library_migrator.py` (Task 1 tests only)

- [ ] **Step 1: Create `backend/config/artist_categories.json`**

```json
{
  "categories": ["Punjabi", "English", "Hindi", "House"],
  "mappings": {
    "Diljit Dosanjh": "Punjabi",
    "Badshah": "Punjabi",
    "AP Dhillon": "Punjabi",
    "Jazzy B": "Punjabi",
    "Yo Yo Honey Singh": "Punjabi",
    "Gurinder Gill": "Punjabi",
    "Imran Khan": "Punjabi",
    "Jaz Dhami": "Punjabi",
    "Shashwat Sachdev": "Punjabi",
    "Bad Bunny": "English",
    "Drake": "English",
    "JVKE": "English",
    "Leo Grewal": "English",
    "Meet Bros": "English",
    "Sweetaj Brar": "English",
    "Amit Trivedi": "Hindi",
    "Anirudh Ravichander": "Hindi",
    "Himesh Reshammiya": "Hindi",
    "Pritam": "Hindi",
    "Raja Baath": "Hindi",
    "This is sammy Virji": "Hindi",
    "Mika Singh": "Hindi",
    "Electronic House 2025": "House",
    "Indo House & Techno": "House",
    "House Music 2026 '\u0950' Party House Mix": "House",
    "drum and bass": "House",
    "Anuv Jain": null,
    "Cheema Y": null,
    "Gminxr": null,
    "hugel": null,
    "Ingest": null,
    "Manual": null,
    "Mau P": null,
    "NIJJAR": null,
    "Nimino": null,
    "odia": null,
    "This Is James Hype": null,
    "Unknown": null
  }
}
```

- [ ] **Step 2: Write failing tests for `load_config` and `save_config`**

Create `backend/tests/test_library_migrator.py`:

```python
"""Tests for services/library_migrator.py"""
import json
import pytest
from pathlib import Path


# ── Task 1: Config ───────────────────────────────────────────────

def test_load_config_returns_categories_and_mappings(tmp_path):
    cfg = tmp_path / "artist_categories.json"
    cfg.write_text(json.dumps({
        "categories": ["Punjabi", "English"],
        "mappings": {"Drake": "English", "Diljit Dosanjh": "Punjabi"}
    }))
    from services.library_migrator import load_config
    data = load_config(cfg)
    assert data["categories"] == ["Punjabi", "English"]
    assert data["mappings"]["Drake"] == "English"


def test_load_config_missing_file_raises(tmp_path):
    from services.library_migrator import load_config
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.json")


def test_save_config_writes_json(tmp_path):
    cfg = tmp_path / "cfg.json"
    data = {"categories": ["Punjabi"], "mappings": {"Diljit Dosanjh": "Punjabi"}}
    from services.library_migrator import save_config
    save_config(cfg, data)
    loaded = json.loads(cfg.read_text())
    assert loaded["mappings"]["Diljit Dosanjh"] == "Punjabi"


def test_save_config_roundtrip(tmp_path):
    cfg = tmp_path / "cfg.json"
    original = {"categories": ["Hindi"], "mappings": {"Pritam": "Hindi", "hugel": None}}
    from services.library_migrator import save_config, load_config
    save_config(cfg, original)
    loaded = load_config(cfg)
    assert loaded["mappings"]["hugel"] is None
    assert loaded["mappings"]["Pritam"] == "Hindi"
```

- [ ] **Step 3: Run tests — verify they fail**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: `ModuleNotFoundError: No module named 'services.library_migrator'`

- [ ] **Step 4: Create `backend/services/library_migrator.py` with config section**

```python
"""
Music Library Migrator
======================
Reorganises a folder-per-artist library into flat language/genre buckets.

Public API:
  load_config(config_path) -> Dict
  save_config(config_path, data) -> None
  migrate_library(source, dest, config_path, *, interactive, dry_run, logs_dir, progress_cb) -> MigrationResult
  build_report_text(category_stats, errors, skipped_artists, undo_log_path, duration_seconds, html) -> str
  undo_migration(undo_log_path) -> None
"""

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac"}

# Default config path relative to this file: backend/config/artist_categories.json
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "artist_categories.json"

# Default logs dir: backend/logs/
DEFAULT_LOGS_DIR = Path(__file__).parent.parent / "logs"


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

def load_config(config_path: Path) -> Dict:
    """Load artist_categories.json. Raises FileNotFoundError if missing."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config_path: Path, data: Dict) -> None:
    """Write artist_categories.json, creating parent dirs if needed."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 5: Run tests — verify they pass**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: 4 PASSED

- [ ] **Step 6: Commit**

```bash
git add backend/config/artist_categories.json backend/services/library_migrator.py backend/tests/test_library_migrator.py
git commit -m "feat: add library migrator config file and load/save helpers"
```

---

## Task 2: File operation helpers

**Files:**
- Modify: `backend/services/library_migrator.py` (add helpers after config section)
- Modify: `backend/tests/test_library_migrator.py` (append Task 2 tests)

- [ ] **Step 1: Append failing tests**

Add to `backend/tests/test_library_migrator.py`:

```python
# ── Task 2: File ops ─────────────────────────────────────────────

def test_md5_file_returns_hex_string(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"hello")
    from services.library_migrator import md5_file
    result = md5_file(f)
    assert isinstance(result, str)
    assert len(result) == 32


def test_md5_file_same_content_same_hash(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    from services.library_migrator import md5_file
    assert md5_file(a) == md5_file(b)


def test_md5_file_different_content_different_hash(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    from services.library_migrator import md5_file
    assert md5_file(a) != md5_file(b)


def test_resolve_dest_path_no_collision(tmp_path):
    dest_root = tmp_path / "dest"
    from services.library_migrator import resolve_dest_path
    result = resolve_dest_path(dest_root, "Punjabi", "song.mp3")
    assert result == dest_root / "Punjabi" / "song.mp3"
    assert (dest_root / "Punjabi").is_dir()


def test_resolve_dest_path_collision_appends_suffix(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "Punjabi").mkdir(parents=True)
    (dest_root / "Punjabi" / "song.mp3").write_bytes(b"existing")
    from services.library_migrator import resolve_dest_path
    result = resolve_dest_path(dest_root, "Punjabi", "song.mp3")
    assert result.name == "song_1.mp3"


def test_resolve_dest_path_double_collision(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "Punjabi").mkdir(parents=True)
    (dest_root / "Punjabi" / "song.mp3").write_bytes(b"x")
    (dest_root / "Punjabi" / "song_1.mp3").write_bytes(b"y")
    from services.library_migrator import resolve_dest_path
    result = resolve_dest_path(dest_root, "Punjabi", "song.mp3")
    assert result.name == "song_2.mp3"


def test_copy_verify_delete_success(tmp_path):
    src = tmp_path / "src" / "song.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio data")
    dest = tmp_path / "dest" / "Punjabi" / "song.mp3"
    dest.parent.mkdir(parents=True)
    from services.library_migrator import copy_verify_delete
    ok = copy_verify_delete(src, dest)
    assert ok is True
    assert dest.exists()
    assert not src.exists()


def test_copy_verify_delete_mismatch_deletes_dest(tmp_path, monkeypatch):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"real content")
    dest = tmp_path / "dest.mp3"
    from services import library_migrator
    call_count = {"n": 0}
    original_md5 = library_migrator.md5_file
    def fake_md5(path):
        call_count["n"] += 1
        # First call (src before copy) returns real hash
        # Second call (dest after copy) returns wrong hash
        if call_count["n"] == 2:
            return "badhash000000000000000000000000000"
        return original_md5(path)
    monkeypatch.setattr(library_migrator, "md5_file", fake_md5)
    ok = copy_verify_delete(src, dest)
    assert ok is False
    assert not dest.exists()
    assert src.exists()  # src untouched on failure


def test_fmt_bytes(tmp_path):
    from services.library_migrator import _fmt_bytes
    assert _fmt_bytes(500) == "500.0 B"
    assert _fmt_bytes(1024) == "1.0 KB"
    assert _fmt_bytes(1024 * 1024) == "1.0 MB"
    assert _fmt_bytes(1024 ** 3) == "1.0 GB"
```

- [ ] **Step 2: Run tests — verify they fail**

```
cd backend
pytest tests/test_library_migrator.py -v -k "md5 or resolve_dest or copy_verify or fmt_bytes"
```
Expected: errors about missing `md5_file`, `resolve_dest_path`, `copy_verify_delete`, `_fmt_bytes`

- [ ] **Step 3: Add file ops to `backend/services/library_migrator.py`**

Append after the config section:

```python
# ═══════════════════════════════════════════════════════════════════
# FILE OPERATION HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def md5_file(path: Path) -> str:
    """Return MD5 hex digest of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_dest_path(dest_root: Path, category: str, filename: str) -> Path:
    """
    Return collision-free destination path inside dest_root/category/.
    If dest_root/category/filename already exists, appends _1, _2, etc. to stem.
    Creates the category directory if it does not exist.
    """
    dest_folder = Path(dest_root) / category
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest_path = dest_folder / filename
    if not dest_path.exists():
        return dest_path
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while (dest_folder / f"{stem}_{counter}{suffix}").exists():
        counter += 1
    return dest_folder / f"{stem}_{counter}{suffix}"


def copy_verify_delete(src: Path, dest: Path) -> bool:
    """
    Copy src → dest, verify MD5 checksums match, then delete src.
    If checksums differ: deletes the bad copy, leaves src untouched, returns False.
    Returns True on success.
    """
    src_md5 = md5_file(src)
    shutil.copy2(str(src), str(dest))
    dest_md5 = md5_file(dest)
    if src_md5 != dest_md5:
        dest.unlink(missing_ok=True)
        logger.error(f"[migrator] MD5 mismatch {src.name} — bad copy deleted, source kept")
        return False
    src.unlink()
    return True
```

- [ ] **Step 4: Run tests — verify they pass**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/services/library_migrator.py backend/tests/test_library_migrator.py
git commit -m "feat: add migrator file op helpers (md5, resolve_dest, copy_verify_delete)"
```

---

## Task 3: Source scanner and artist resolver

**Files:**
- Modify: `backend/services/library_migrator.py` (append scan + resolve functions)
- Modify: `backend/tests/test_library_migrator.py` (append Task 3 tests)

- [ ] **Step 1: Append failing tests**

```python
# ── Task 3: Scanner + resolver ───────────────────────────────────

def test_scan_source_folders_finds_audio_files(tmp_path):
    artist = tmp_path / "Diljit Dosanjh"
    artist.mkdir()
    (artist / "song.mp3").write_bytes(b"x")
    (artist / "cover.jpg").write_bytes(b"img")
    from services.library_migrator import scan_source_folders
    result = scan_source_folders(tmp_path)
    assert "Diljit Dosanjh" in result
    assert len(result["Diljit Dosanjh"]) == 1
    assert result["Diljit Dosanjh"][0].name == "song.mp3"


def test_scan_source_folders_ignores_non_audio(tmp_path):
    artist = tmp_path / "Drake"
    artist.mkdir()
    (artist / "README.txt").write_bytes(b"x")
    from services.library_migrator import scan_source_folders
    result = scan_source_folders(tmp_path)
    assert "Drake" not in result


def test_scan_source_folders_ignores_root_files(tmp_path):
    (tmp_path / "stray.mp3").write_bytes(b"x")
    artist = tmp_path / "Drake"
    artist.mkdir()
    (artist / "song.flac").write_bytes(b"y")
    from services.library_migrator import scan_source_folders
    result = scan_source_folders(tmp_path)
    assert list(result.keys()) == ["Drake"]


def test_scan_source_folders_all_extensions(tmp_path):
    artist = tmp_path / "Artist"
    artist.mkdir()
    for ext in (".mp3", ".flac", ".wav", ".m4a", ".aac"):
        (artist / f"song{ext}").write_bytes(b"x")
    from services.library_migrator import scan_source_folders
    result = scan_source_folders(tmp_path)
    assert len(result["Artist"]) == 5


def test_resolve_artists_known_category(tmp_path):
    from services.library_migrator import resolve_artists
    mappings = {"Diljit Dosanjh": "Punjabi", "Drake": "English", "hugel": None}
    resolved, unresolved = resolve_artists(["Diljit Dosanjh", "Drake"], mappings)
    assert resolved == {"Diljit Dosanjh": "Punjabi", "Drake": "English"}
    assert unresolved == []


def test_resolve_artists_null_goes_to_unresolved(tmp_path):
    from services.library_migrator import resolve_artists
    mappings = {"hugel": None}
    resolved, unresolved = resolve_artists(["hugel"], mappings)
    assert resolved == {}
    assert unresolved == ["hugel"]


def test_resolve_artists_missing_from_config_goes_to_unresolved(tmp_path):
    from services.library_migrator import resolve_artists
    mappings = {"Drake": "English"}
    resolved, unresolved = resolve_artists(["Drake", "NewArtist"], mappings)
    assert "NewArtist" in unresolved
    assert "Drake" not in unresolved
```

- [ ] **Step 2: Run tests — verify they fail**

```
cd backend
pytest tests/test_library_migrator.py -v -k "scan or resolve_artists"
```
Expected: errors about missing `scan_source_folders`, `resolve_artists`

- [ ] **Step 3: Append to `backend/services/library_migrator.py`**

```python
# ═══════════════════════════════════════════════════════════════════
# SCANNER AND RESOLVER
# ═══════════════════════════════════════════════════════════════════

def scan_source_folders(source: Path) -> Dict[str, List[Path]]:
    """
    Scan source for artist subdirectories that contain at least one audio file.
    Returns {folder_name: [sorted list of audio file Paths]}.
    Root-level files are ignored — only subfolders are considered.
    """
    result: Dict[str, List[Path]] = {}
    for folder in sorted(Path(source).iterdir()):
        if not folder.is_dir():
            continue
        audio_files = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        )
        if audio_files:
            result[folder.name] = audio_files
    return result


def resolve_artists(
    artist_folders: List[str],
    mappings: Dict[str, Optional[str]],
) -> Tuple[Dict[str, str], List[str]]:
    """
    Split artist folder names into resolved and unresolved.
    - resolved: {artist_name: category} for artists with a non-null mapping
    - unresolved: list of artists with null mapping or missing from mappings entirely
    """
    resolved: Dict[str, str] = {}
    unresolved: List[str] = []
    for artist in artist_folders:
        category = mappings.get(artist)  # None if key missing, None if explicit null
        if category:
            resolved[artist] = category
        else:
            unresolved.append(artist)
    return resolved, unresolved
```

- [ ] **Step 4: Run tests — verify they pass**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/services/library_migrator.py backend/tests/test_library_migrator.py
git commit -m "feat: add migrator scan_source_folders and resolve_artists"
```

---

## Task 4: Interactive prompt and undo log writer

**Files:**
- Modify: `backend/services/library_migrator.py` (append prompt + undo log functions)
- Modify: `backend/tests/test_library_migrator.py` (append Task 4 tests)

- [ ] **Step 1: Append failing tests**

```python
# ── Task 4: Interactive prompt + undo log ────────────────────────

def test_prompt_unresolved_saves_answer(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    data = {
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"hugel": None}
    }
    import json as _json
    cfg.write_text(_json.dumps(data))
    # Simulate user typing "2" (English)
    monkeypatch.setattr("builtins.input", lambda _: "2")
    from services.library_migrator import prompt_unresolved, load_config
    result = prompt_unresolved(["hugel"], data["categories"], cfg, data)
    assert result == {"hugel": "English"}
    saved = load_config(cfg)
    assert saved["mappings"]["hugel"] == "English"


def test_prompt_unresolved_skip_choice(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    data = {
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"hugel": None}
    }
    import json as _json
    cfg.write_text(_json.dumps(data))
    # "5" = Skip (len(categories)+1 = 5)
    monkeypatch.setattr("builtins.input", lambda _: "5")
    from services.library_migrator import prompt_unresolved
    result = prompt_unresolved(["hugel"], data["categories"], cfg, data)
    assert result == {}


def test_write_undo_log_creates_file(tmp_path):
    entries = [
        {"from": "dest/Punjabi/song.mp3", "to": "source/Diljit Dosanjh/song.mp3"}
    ]
    from services.library_migrator import write_undo_log
    path = write_undo_log(entries, tmp_path)
    assert path.exists()
    assert path.suffix == ".json"
    assert "migrate_undo_" in path.name


def test_write_undo_log_content(tmp_path):
    import json as _json
    entries = [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
    from services.library_migrator import write_undo_log
    path = write_undo_log(entries, tmp_path)
    loaded = _json.loads(path.read_text())
    assert loaded == entries
```

- [ ] **Step 2: Run tests — verify they fail**

```
cd backend
pytest tests/test_library_migrator.py -v -k "prompt or undo_log"
```
Expected: errors about missing `prompt_unresolved`, `write_undo_log`

- [ ] **Step 3: Append to `backend/services/library_migrator.py`**

```python
# ═══════════════════════════════════════════════════════════════════
# INTERACTIVE PROMPT + UNDO LOG
# ═══════════════════════════════════════════════════════════════════

def prompt_unresolved(
    unresolved: List[str],
    categories: List[str],
    config_path: Path,
    config_data: Dict,
) -> Dict[str, str]:
    """
    Interactively prompt user to assign each unresolved artist to a category.
    Saves each answer to config_path immediately after it is given.
    Returns {artist: category} for resolved items only (skipped items are omitted).
    """
    newly_resolved: Dict[str, str] = {}
    for artist in unresolved:
        print(f'\nArtist: "{artist}"')
        for i, cat in enumerate(categories, 1):
            print(f"  [{i}] {cat}")
        print(f"  [{len(categories) + 1}] Skip")
        while True:
            try:
                raw = input("> ").strip()
                choice = int(raw)
            except (ValueError, EOFError):
                print("  Invalid input, skipping.")
                break
            if 1 <= choice <= len(categories):
                category = categories[choice - 1]
                newly_resolved[artist] = category
                config_data["mappings"][artist] = category
                save_config(config_path, config_data)
                logger.info(f"[migrator] Assigned '{artist}' → '{category}', saved")
                break
            elif choice == len(categories) + 1:
                logger.info(f"[migrator] Skipped '{artist}'")
                break
            else:
                print(f"  Please enter 1–{len(categories) + 1}")
    return newly_resolved


def write_undo_log(entries: List[Dict], logs_dir: Path) -> Path:
    """
    Write undo log JSON to logs_dir/migrate_undo_YYYYMMDD_HHMMSS.json.
    Returns the path of the written file.
    Each entry: {"from": "<dest path>", "to": "<source path>"}
    (to undo: move FROM dest back TO source)
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"migrate_undo_{ts}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    logger.info(f"[migrator] Undo log written: {log_path}")
    return log_path
```

- [ ] **Step 4: Run tests — verify they pass**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/services/library_migrator.py backend/tests/test_library_migrator.py
git commit -m "feat: add migrator interactive prompt and undo log writer"
```

---

## Task 5: Report builder

**Files:**
- Modify: `backend/services/library_migrator.py` (append report functions)
- Modify: `backend/tests/test_library_migrator.py` (append Task 5 tests)

- [ ] **Step 1: Append failing tests**

```python
# ── Task 5: Report builder ───────────────────────────────────────

def test_build_report_text_plain_contains_totals():
    from services.library_migrator import build_report_text
    stats = {
        "Punjabi": {"files": 10, "bytes": 1024 * 1024},
        "English": {"files": 5,  "bytes": 512 * 1024},
        "Hindi":   {"files": 0,  "bytes": 0},
        "House":   {"files": 0,  "bytes": 0},
    }
    text = build_report_text(
        category_stats=stats,
        errors=[],
        skipped_artists=[],
        undo_log_path=None,
        duration_seconds=90.0,
        html=False,
    )
    assert "Punjabi" in text
    assert "10" in text
    assert "1m 30s" in text
    assert "Errors   : 0" in text


def test_build_report_text_shows_skipped_artists():
    from services.library_migrator import build_report_text
    text = build_report_text(
        category_stats={cat: {"files": 0, "bytes": 0} for cat in ["Punjabi","English","Hindi","House"]},
        errors=[],
        skipped_artists=["hugel", "NIJJAR"],
        undo_log_path=None,
        duration_seconds=10.0,
        html=False,
    )
    assert "hugel" in text
    assert "NIJJAR" in text


def test_build_report_text_shows_undo_path():
    from services.library_migrator import build_report_text
    text = build_report_text(
        category_stats={cat: {"files": 0, "bytes": 0} for cat in ["Punjabi","English","Hindi","House"]},
        errors=[],
        skipped_artists=[],
        undo_log_path="logs/migrate_undo_20260408_120000.json",
        duration_seconds=5.0,
        html=False,
    )
    assert "migrate_undo_20260408_120000.json" in text


def test_build_report_text_html_wraps_pre():
    from services.library_migrator import build_report_text
    text = build_report_text(
        category_stats={cat: {"files": 0, "bytes": 0} for cat in ["Punjabi","English","Hindi","House"]},
        errors=[],
        skipped_artists=[],
        undo_log_path=None,
        duration_seconds=1.0,
        html=True,
    )
    assert text.startswith("<pre>")
    assert text.endswith("</pre>")
```

- [ ] **Step 2: Run tests — verify they fail**

```
cd backend
pytest tests/test_library_migrator.py -v -k "report"
```
Expected: errors about missing `build_report_text`

- [ ] **Step 3: Append to `backend/services/library_migrator.py`**

```python
# ═══════════════════════════════════════════════════════════════════
# REPORT BUILDER
# ═══════════════════════════════════════════════════════════════════

CATEGORIES_ORDER = ["Punjabi", "English", "Hindi", "House"]


def build_report_text(
    category_stats: Dict[str, Dict],
    errors: List[Dict],
    skipped_artists: List[str],
    undo_log_path: Optional[str],
    duration_seconds: float,
    html: bool = False,
) -> str:
    """
    Build the final migration report as a plain string or HTML <pre> block.
    category_stats format: {category: {"files": int, "bytes": int}}
    """
    lines = ["MUSIC LIBRARY MIGRATION REPORT", "=" * 32]

    total_files = 0
    total_bytes = 0
    for cat in CATEGORIES_ORDER:
        stats = category_stats.get(cat, {"files": 0, "bytes": 0})
        f, b = stats["files"], stats["bytes"]
        total_files += f
        total_bytes += b
        lines.append(f"{cat:<9}: {f:>4} files  ({_fmt_bytes(b)})")

    lines.append("-" * 32)
    lines.append(f"{'Total':<9}: {total_files:>4} files  ({_fmt_bytes(total_bytes)})")
    lines.append(f"Errors   : {len(errors)}")

    if skipped_artists:
        lines.append(f"Skipped artists (unresolved): {', '.join(skipped_artists)}")

    if undo_log_path:
        lines.append(f"Undo log : {undo_log_path}")

    mins = int(duration_seconds // 60)
    secs = int(duration_seconds % 60)
    lines.append(f"Duration : {mins}m {secs}s")

    text = "\n".join(lines)
    return f"<pre>{text}</pre>" if html else text
```

- [ ] **Step 4: Run tests — verify they pass**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/services/library_migrator.py backend/tests/test_library_migrator.py
git commit -m "feat: add migrator report builder"
```

---

## Task 6: Migration engine (migrate_library + MigrationResult)

**Files:**
- Modify: `backend/services/library_migrator.py` (append MigrationResult dataclass + migrate_library + undo_migration)
- Modify: `backend/tests/test_library_migrator.py` (append Task 6 tests)

- [ ] **Step 1: Append failing tests**

```python
# ── Task 6: Migration engine ─────────────────────────────────────
import json as _json_task6

def _make_source(tmp_path, artists):
    """Helper: create source dirs with fake audio files."""
    source = tmp_path / "source"
    for artist, filenames in artists.items():
        d = source / artist
        d.mkdir(parents=True)
        for fn in filenames:
            (d / fn).write_bytes(b"fake audio " + fn.encode())
    return source


def test_migrate_library_moves_resolved_files(tmp_path):
    source = _make_source(tmp_path, {"Diljit Dosanjh": ["song1.mp3", "song2.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Diljit Dosanjh": "Punjabi"},
    }))
    from services.library_migrator import migrate_library
    result = migrate_library(
        source=source, dest=dest, config_path=cfg,
        interactive=False, logs_dir=tmp_path / "logs",
    )
    assert result.files_moved == 2
    assert (dest / "Punjabi" / "song1.mp3").exists()
    assert (dest / "Punjabi" / "song2.mp3").exists()
    assert not (source / "Diljit Dosanjh" / "song1.mp3").exists()


def test_migrate_library_deletes_empty_source_folder(tmp_path):
    source = _make_source(tmp_path, {"Diljit Dosanjh": ["song.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Diljit Dosanjh": "Punjabi"},
    }))
    from services.library_migrator import migrate_library
    migrate_library(source=source, dest=dest, config_path=cfg,
                    interactive=False, logs_dir=tmp_path / "logs")
    assert not (source / "Diljit Dosanjh").exists()


def test_migrate_library_keeps_nonempty_source_folder(tmp_path):
    source = _make_source(tmp_path, {"Diljit Dosanjh": ["song.mp3"]})
    (source / "Diljit Dosanjh" / "cover.jpg").write_bytes(b"img")
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Diljit Dosanjh": "Punjabi"},
    }))
    from services.library_migrator import migrate_library
    result = migrate_library(source=source, dest=dest, config_path=cfg,
                             interactive=False, logs_dir=tmp_path / "logs")
    assert (source / "Diljit Dosanjh").exists()
    assert "Diljit Dosanjh" in result.non_empty_source_folders


def test_migrate_library_skips_unresolved(tmp_path):
    source = _make_source(tmp_path, {"hugel": ["track.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"hugel": None},
    }))
    from services.library_migrator import migrate_library
    result = migrate_library(source=source, dest=dest, config_path=cfg,
                             interactive=False, logs_dir=tmp_path / "logs")
    assert result.files_moved == 0
    assert "hugel" in result.skipped_artists
    assert (source / "hugel" / "track.mp3").exists()


def test_migrate_library_writes_undo_log(tmp_path):
    source = _make_source(tmp_path, {"Drake": ["track.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Drake": "English"},
    }))
    logs_dir = tmp_path / "logs"
    from services.library_migrator import migrate_library
    result = migrate_library(source=source, dest=dest, config_path=cfg,
                             interactive=False, logs_dir=logs_dir)
    assert result.undo_log_path is not None
    undo_entries = _json_task6.loads(Path(result.undo_log_path).read_text())
    assert len(undo_entries) == 1
    assert "from" in undo_entries[0]
    assert "to" in undo_entries[0]


def test_migrate_library_dry_run_moves_nothing(tmp_path):
    source = _make_source(tmp_path, {"Drake": ["song.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Drake": "English"},
    }))
    from services.library_migrator import migrate_library
    result = migrate_library(source=source, dest=dest, config_path=cfg,
                             interactive=False, dry_run=True,
                             logs_dir=tmp_path / "logs")
    assert result.files_moved == 0
    assert (source / "Drake" / "song.mp3").exists()
    assert not (dest / "English" / "song.mp3").exists()


def test_migrate_library_progress_callback_called(tmp_path):
    source = _make_source(tmp_path, {"Drake": ["a.mp3", "b.mp3", "c.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Drake": "English"},
    }))
    calls = []
    from services.library_migrator import migrate_library
    migrate_library(source=source, dest=dest, config_path=cfg,
                    interactive=False, logs_dir=tmp_path / "logs",
                    progress_cb=lambda done, total: calls.append((done, total)))
    assert len(calls) == 3
    assert calls[-1][0] == 3


def test_undo_migration_restores_files(tmp_path):
    source = _make_source(tmp_path, {"Drake": ["song.mp3"]})
    dest = tmp_path / "dest"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(_json_task6.dumps({
        "categories": ["Punjabi", "English", "Hindi", "House"],
        "mappings": {"Drake": "English"},
    }))
    from services.library_migrator import migrate_library, undo_migration
    result = migrate_library(source=source, dest=dest, config_path=cfg,
                             interactive=False, logs_dir=tmp_path / "logs")
    assert result.files_moved == 1
    assert not (source / "Drake" / "song.mp3").exists()
    undo_migration(Path(result.undo_log_path))
    assert (source / "Drake" / "song.mp3").exists()
    assert not (dest / "English" / "song.mp3").exists()
```

- [ ] **Step 2: Run tests — verify they fail**

```
cd backend
pytest tests/test_library_migrator.py -v -k "migrate_library or undo_migration"
```
Expected: errors about missing `migrate_library`, `MigrationResult`, `undo_migration`

- [ ] **Step 3: Append to `backend/services/library_migrator.py`**

```python
# ═══════════════════════════════════════════════════════════════════
# MIGRATION ENGINE
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MigrationResult:
    files_moved: int = 0
    errors: List[Dict] = field(default_factory=list)
    skipped_artists: List[str] = field(default_factory=list)
    non_empty_source_folders: List[str] = field(default_factory=list)
    category_stats: Dict[str, Dict] = field(default_factory=dict)
    undo_entries: List[Dict] = field(default_factory=list)
    undo_log_path: Optional[str] = None
    duration_seconds: float = 0.0


def migrate_library(
    source: Path,
    dest: Path,
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    interactive: bool = False,
    dry_run: bool = False,
    logs_dir: Path = DEFAULT_LOGS_DIR,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> MigrationResult:
    """
    Migrate all audio files from artist-folder source into flat category buckets at dest.

    Args:
        source:       Root directory containing artist subfolders
        dest:         Root directory for category folders (Punjabi/, English/, etc.)
        config_path:  Path to artist_categories.json
        interactive:  If True and stdin is a TTY, prompt for unresolved artists
        dry_run:      If True, scan and resolve but do not move any files
        logs_dir:     Directory to write the undo log JSON
        progress_cb:  Optional callback(files_done, files_total) called after each file

    Returns:
        MigrationResult dataclass
    """
    import time
    start = time.time()

    result = MigrationResult()
    result.category_stats = {cat: {"files": 0, "bytes": 0} for cat in CATEGORIES_ORDER}

    # ── Load config ──────────────────────────────────────────────
    config_data = load_config(Path(config_path))
    categories = config_data["categories"]
    mappings = config_data["mappings"]

    # ── Scan source ──────────────────────────────────────────────
    source_folders = scan_source_folders(Path(source))
    if not source_folders:
        logger.info(f"[migrator] No artist folders with audio found in {source}")
        result.duration_seconds = time.time() - start
        return result

    # ── Resolve artists ──────────────────────────────────────────
    resolved, unresolved = resolve_artists(list(source_folders.keys()), mappings)

    # Interactive fallback — only when TTY is available and interactive=True
    if unresolved and interactive and sys.stdin.isatty():
        newly_resolved = prompt_unresolved(unresolved, categories, Path(config_path), config_data)
        resolved.update(newly_resolved)
        # Remove newly resolved from unresolved list
        unresolved = [a for a in unresolved if a not in newly_resolved]

    result.skipped_artists = unresolved

    # ── Count total files for progress ───────────────────────────
    total_files = sum(len(files) for artist, files in source_folders.items() if artist in resolved)

    # ── Process files ────────────────────────────────────────────
    files_done = 0
    for artist, category in resolved.items():
        audio_files = source_folders.get(artist, [])
        for src_file in audio_files:
            if dry_run:
                logger.info(f"[migrator] DRY RUN: {src_file.name} → {category}/")
                files_done += 1
                if progress_cb:
                    progress_cb(files_done, total_files)
                continue

            dest_path = resolve_dest_path(Path(dest), category, src_file.name)
            src_bytes = src_file.stat().st_size

            ok = copy_verify_delete(src_file, dest_path)
            if ok:
                result.files_moved += 1
                result.undo_entries.append({
                    "from": str(dest_path),
                    "to": str(src_file),
                })
                result.category_stats[category]["files"] += 1
                result.category_stats[category]["bytes"] += src_bytes
                logger.info(f"[migrator] ✓ {src_file.name} → {category}/")
            else:
                result.errors.append({"file": str(src_file), "error": "MD5 mismatch"})

            files_done += 1
            if progress_cb:
                progress_cb(files_done, total_files)

            # Optional MongoDB update (silent fail)
            try:
                from database import _get_db
                db = _get_db()
                db.download_history.update_one(
                    {"filename": src_file.name},
                    {"$set": {"folder": category, "relative_path": str(dest_path)}},
                )
            except Exception as db_err:
                logger.debug(f"[migrator] MongoDB update skipped: {db_err}")

    # ── Post-process: clean up empty artist folders ───────────────
    if not dry_run:
        for artist in resolved:
            artist_dir = Path(source) / artist
            if artist_dir.exists():
                remaining = list(artist_dir.iterdir())
                if not remaining:
                    artist_dir.rmdir()
                    logger.info(f"[migrator] Removed empty folder: {artist}/")
                else:
                    result.non_empty_source_folders.append(artist)
                    logger.warning(f"[migrator] Non-empty folder left: {artist}/ ({len(remaining)} items)")

    # ── Write undo log ────────────────────────────────────────────
    if result.undo_entries and not dry_run:
        undo_path = write_undo_log(result.undo_entries, Path(logs_dir))
        result.undo_log_path = str(undo_path)

    result.duration_seconds = time.time() - start
    logger.info(
        f"[migrator] Done: moved={result.files_moved}, "
        f"skipped={len(result.skipped_artists)}, errors={len(result.errors)}"
    )
    return result


def undo_migration(undo_log_path: Path) -> None:
    """
    Reverse a migration using its undo log.
    Each entry: {"from": "<dest path>", "to": "<original source path>"}
    Moves the file FROM dest back TO its original location, recreating dirs as needed.
    """
    undo_log_path = Path(undo_log_path)
    if not undo_log_path.exists():
        raise FileNotFoundError(f"Undo log not found: {undo_log_path}")
    with open(undo_log_path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    logger.info(f"[migrator] Undoing {len(entries)} moves from {undo_log_path.name}")
    for entry in entries:
        src = Path(entry["from"])
        dest = Path(entry["to"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        logger.info(f"[migrator] Restored: {src.name} → {dest.parent.name}/")
    print(f"Undo complete: {len(entries)} files restored.")
```

- [ ] **Step 4: Run tests — verify they pass**

```
cd backend
pytest tests/test_library_migrator.py -v
```
Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/services/library_migrator.py backend/tests/test_library_migrator.py
git commit -m "feat: add migrate_library engine and undo_migration"
```

---

## Task 7: CLI runner

**Files:**
- Create: `backend/run_migrate.py`

No new tests needed — the migrator logic is already tested. The CLI is a thin wrapper.

- [ ] **Step 1: Create `backend/run_migrate.py`**

```python
"""
Music Library Migrator — CLI Runner
=====================================
Usage:
  python run_migrate.py                               # interactive, prompts for source/dest
  python run_migrate.py --source /src --dest /dest   # explicit paths
  python run_migrate.py --dry-run                     # preview only, no file moves
  python run_migrate.py --undo logs/migrate_undo_<ts>.json
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from services.library_migrator import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOGS_DIR,
    build_report_text,
    migrate_library,
    undo_migration,
)
from config import config as app_config


def _default_source() -> Path:
    return Path(app_config.BASE_DOWNLOAD_DIR)


def _default_dest() -> Path:
    return Path(app_config.BASE_DOWNLOAD_DIR + "_organised")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reorganise music library into language/genre folders."
    )
    parser.add_argument("--source", type=Path, default=None,
                        help=f"Source directory (default: {_default_source()})")
    parser.add_argument("--dest", type=Path, default=None,
                        help=f"Destination directory (default: {_default_dest()})")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help=f"artist_categories.json path (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would move — no files are touched")
    parser.add_argument("--undo", type=Path, default=None,
                        help="Undo a previous run using its undo log JSON")
    args = parser.parse_args()

    # ── Undo mode ─────────────────────────────────────────────────
    if args.undo:
        print(f"Undoing migration from: {args.undo}")
        undo_migration(args.undo)
        return

    source = args.source or _default_source()
    dest   = args.dest   or _default_dest()

    if not source.exists():
        print(f"ERROR: Source directory does not exist: {source}")
        sys.exit(1)

    print(f"Source : {source}")
    print(f"Dest   : {dest}")
    print(f"Config : {args.config}")
    if args.dry_run:
        print("Mode   : DRY RUN (no files will be moved)\n")
    else:
        print()

    # ── Progress display ──────────────────────────────────────────
    def progress_cb(done: int, total: int) -> None:
        pct = int(done / total * 100) if total else 0
        print(f"\r  Progress: {done}/{total} ({pct}%)", end="", flush=True)
        if done == total:
            print()

    result = migrate_library(
        source=source,
        dest=dest,
        config_path=args.config,
        interactive=True,
        dry_run=args.dry_run,
        logs_dir=DEFAULT_LOGS_DIR,
        progress_cb=progress_cb,
    )

    # ── Print report ──────────────────────────────────────────────
    print()
    print(build_report_text(
        category_stats=result.category_stats,
        errors=result.errors,
        skipped_artists=result.skipped_artists,
        undo_log_path=result.undo_log_path,
        duration_seconds=result.duration_seconds,
        html=False,
    ))

    if result.errors:
        print("\nERRORS:")
        for e in result.errors:
            print(f"  {e['file']}: {e['error']}")

    if result.non_empty_source_folders:
        print("\nNON-EMPTY SOURCE FOLDERS (left untouched):")
        for folder in result.non_empty_source_folders:
            print(f"  {folder}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify dry-run works manually**

```bash
cd backend
python run_migrate.py --dry-run
```
Expected: prints a preview table with no file moves, no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/run_migrate.py
git commit -m "feat: add run_migrate.py CLI runner with dry-run and undo support"
```

---

## Task 8: Telegram `/organize` command

**Files:**
- Modify: `backend/telegram_bot.py`
  - Add `_run_migration_thread` function (after `_run_spotify_download`, before `handle_spotify_link`)
  - Add `cmd_organize` async handler (after `cmd_storage`)
  - Register handler in `_run_bot()`
- Modify: `backend/tests/test_telegram_integration.py` (append Task 8 tests)

- [ ] **Step 1: Append failing tests to `backend/tests/test_telegram_integration.py`**

```python
# ── Task 8: Telegram /organize ───────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_organize_dispatches_thread():
    update = MagicMock()
    update.effective_chat.id = 12345
    update.effective_user.id = 12345
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("telegram_bot.TELEGRAM_CHAT_ID", 12345), \
         patch("telegram_bot._rate_limiter") as mock_rl, \
         patch("telegram_bot.threading.Thread") as mock_thread:
        mock_rl.is_allowed.return_value = True
        mock_thread.return_value.start = MagicMock()
        from telegram_bot import cmd_organize
        await cmd_organize(update, context)
        assert update.message.reply_text.called
        mock_thread.return_value.start.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_organize_rate_limited():
    update = MagicMock()
    update.effective_chat.id = 12345
    update.effective_user.id = 99
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    with patch("telegram_bot.TELEGRAM_CHAT_ID", 12345), \
         patch("telegram_bot._rate_limiter") as mock_rl:
        mock_rl.is_allowed.return_value = False
        mock_rl.get_reset_time.return_value = 30
        from telegram_bot import cmd_organize
        await cmd_organize(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "Rate limited" in text or "⏱️" in text
```

- [ ] **Step 2: Run tests — verify they fail**

```
cd backend
pytest tests/test_telegram_integration.py -v -k "organize"
```
Expected: `ImportError` or `AttributeError` — `cmd_organize` does not exist yet

- [ ] **Step 3: Add `_run_migration_thread` to `telegram_bot.py`**

Insert after `_run_spotify_download` (just before `handle_spotify_link`, around line 920):

```python
def _run_migration_thread(source: str, dest: str, chat_id: int) -> None:
    """
    Run migrate_library in a background daemon thread.
    Sends three Telegram messages: start → progress → final report.
    Always non-interactive (Telegram cannot read stdin).
    """
    from pathlib import Path
    from services.library_migrator import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_LOGS_DIR,
        migrate_library,
        build_report_text,
    )

    def _send(msg: str) -> None:
        _send_message_sync(chat_id, msg)

    def progress_cb(done: int, total: int) -> None:
        if total > 0 and (done % 25 == 0 or done == total):
            _send(f"⏳ {done}/{total} files processed...")

    try:
        result = migrate_library(
            source=Path(source),
            dest=Path(dest),
            config_path=DEFAULT_CONFIG_PATH,
            interactive=False,
            dry_run=False,
            logs_dir=DEFAULT_LOGS_DIR,
            progress_cb=progress_cb,
        )
        report = build_report_text(
            category_stats=result.category_stats,
            errors=result.errors,
            skipped_artists=result.skipped_artists,
            undo_log_path=result.undo_log_path,
            duration_seconds=result.duration_seconds,
            html=True,
        )
        _send(f"✅ <b>Migration complete!</b>\n\n{report}")
        logger.info(f"[telegram_bot] /organize done: moved={result.files_moved}")
    except Exception as e:
        logger.exception(f"[telegram_bot] /organize thread error: {e}")
        _send(f"❌ <b>Migration failed:</b>\n{str(e)[:300]}")
```

- [ ] **Step 4: Add `cmd_organize` handler to `telegram_bot.py`**

Insert after `cmd_storage` (before the SPOTIFY LINK HANDLER section):

```python
@handle_command_error("cmd_organize")
async def cmd_organize(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    if not await _auth_check(update):
        return

    user_id = update.effective_user.id
    if not _rate_limiter.is_allowed(user_id):
        reset = _rate_limiter.get_reset_time(user_id)
        await update.message.reply_text(f"⏱️ Rate limited. Wait {reset}s.")
        logger.warning(f"[cmd_organize] Rate limit: user {user_id}")
        return

    import os
    from config import config as _cfg
    source = _cfg.BASE_DOWNLOAD_DIR
    dest   = _cfg.BASE_DOWNLOAD_DIR + "_organised"
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"🗂 <b>Starting library migration...</b>\n\n"
        f"📁 <b>Source:</b> <code>{source}</code>\n"
        f"📁 <b>Dest  :</b> <code>{dest}</code>\n\n"
        f"I'll send progress updates every 25 files.",
        parse_mode="HTML",
    )
    logger.info(f"[cmd_organize] Dispatching migration thread (source={source})")

    t = threading.Thread(
        target=_run_migration_thread,
        args=(source, dest, chat_id),
        daemon=True,
        name="tg-migrate",
    )
    t.start()
```

- [ ] **Step 5: Register `/organize` in `_run_bot()`**

In `_run_bot()`, after the line that registers `cmd_storage`:

```python
    ptb_app.add_handler(CommandHandler("organize", cmd_organize))
```

- [ ] **Step 6: Update `HELP_TEXT` to include `/organize`**

Find the `HELP_TEXT` string and add the new line after `/storage`:

```python
    "/organize      — reorganise library into language/genre folders\n"
```

- [ ] **Step 7: Run tests — verify they pass**

```
cd backend
pytest tests/test_telegram_integration.py -v
```
Expected: all tests PASSED

- [ ] **Step 8: Commit**

```bash
git add backend/telegram_bot.py backend/tests/test_telegram_integration.py
git commit -m "feat: add /organize Telegram command and migration thread dispatcher"
```

---

## Task 9: Full integration smoke test

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite**

```
cd backend
pytest tests/test_library_migrator.py tests/test_telegram_integration.py -v
```
Expected: all tests PASSED, 0 failures

- [ ] **Step 2: Dry-run against real library**

```bash
cd backend
python run_migrate.py --dry-run
```
Expected:
- Prints source and dest paths
- Lists what would move where (all 26 pre-seeded artists correctly assigned)
- Lists unresolved artists (hugel, NIJJAR, etc.)
- No files touched

- [ ] **Step 3: Verify config loads correctly**

```bash
cd backend
python -c "
from services.library_migrator import load_config, DEFAULT_CONFIG_PATH
cfg = load_config(DEFAULT_CONFIG_PATH)
print('Categories:', cfg['categories'])
resolved = [k for k,v in cfg['mappings'].items() if v]
nulls    = [k for k,v in cfg['mappings'].items() if v is None]
print(f'Resolved: {len(resolved)}, Unresolved: {len(nulls)}')
"
```
Expected:
```
Categories: ['Punjabi', 'English', 'Hindi', 'House']
Resolved: 26, Unresolved: 12
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete music library migrator — service, CLI, Telegram command"
```

---

## Self-Review Checklist (completed inline)

**Spec coverage:**
- ✅ Config file pre-seeded with all 26 artists + 12 nulls — Task 1
- ✅ load_config / save_config — Task 1
- ✅ MD5 copy→verify→delete — Task 2, Task 6
- ✅ Collision handling (_1, _2) — Task 2
- ✅ Scan artist folders, filter by audio extension — Task 3
- ✅ resolve_artists (null + missing = unresolved) — Task 3
- ✅ Interactive prompt saves to config immediately — Task 4
- ✅ Non-interactive skips unresolved silently — Task 6 (interactive=False)
- ✅ Undo log written per run — Task 4 + Task 6
- ✅ undo_migration restores files — Task 6
- ✅ Empty source folders deleted post-migration — Task 6
- ✅ Non-empty source folders flagged in report — Task 6
- ✅ build_report_text plain + HTML — Task 5
- ✅ run_migrate.py with --dry-run, --undo, --source, --dest — Task 7
- ✅ /organize Telegram command — Task 8
- ✅ Progress callback every 25 files — Task 8
- ✅ /organize uses auth + rate limiter + error decorator — Task 8
- ✅ HELP_TEXT updated — Task 8
- ✅ MongoDB update optional/silent — Task 6

**Type consistency:** `MigrationResult` fields referenced consistently across Tasks 6, 7, 8. `build_report_text` signature stable from Task 5 through Task 8.
