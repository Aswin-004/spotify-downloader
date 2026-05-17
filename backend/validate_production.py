"""
Production Workflow Validator — End-to-End (11 Phases)
=======================================================
Comprehensive read-only validation of the DJ pipeline's real-world behavior.
All write operations use temp directories or prefixed test keys.

Phases:
  1  — Test matrix generation          → test_matrix.json
  2  — Ingestion flow validation
  3  — Genre routing validation         → routing_validation_report.json
  4  — NeedsReview lifecycle            → needsreview_report.json
  5  — Quarantine policy validation
  6  — Remix family grouping            → remix_grouping_report.json
  7  — Duplicate policy validation
  8  — Artist memory learning           → artist_memory_report.json
  9  — Reconciliation validation
 10  — Performance measurement          → performance_report.json
 11  — Final production readiness       → production_readiness_report.json

Usage
-----
    cd backend
    python validate_production.py                # all phases
    python validate_production.py --phase 3      # single phase
    python validate_production.py --output ./reports/

Safety: read-only against Library/. No files moved or deleted.
Exit:   0 = all pass, 1 = failures found.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<7} | {message}", level="INFO")
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logger = logging.getLogger(__name__)

from config import config

BASE_DIR = Path(config.BASE_DOWNLOAD_DIR)
LIB_DIR  = BASE_DIR / "Library"
NR_DIR   = BASE_DIR / "NeedsReview"
QUA_DIR  = BASE_DIR / "Quarantine"

_NOW = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
_PASS = "✓"
_FAIL = "✗"
_WARN = "~"

# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────

def _check(label: str, cond: bool, detail: str = "") -> dict:
    status = "PASS" if cond else "FAIL"
    mark   = _PASS if cond else _FAIL
    msg    = f"  [{mark}] {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return {"check": label, "status": status, "detail": detail}


def _warn(label: str, cond: bool, detail: str = "") -> dict:
    status = "PASS" if cond else "WARN"
    mark   = _PASS if cond else _WARN
    msg    = f"  [{mark}] {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return {"check": label, "status": status, "detail": detail}


def _write_report(name: str, data: dict, output_dir: Path) -> str:
    p = output_dir / name
    try:
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(f"Report written: {p}")
    except Exception as e:
        logger.warning(f"Could not write {name}: {e}")
    return str(p)


# ─────────────────────────────────────────────────────────────────
# PHASE 1 — Test Matrix
# ─────────────────────────────────────────────────────────────────

def phase1_test_matrix(output_dir: Path) -> tuple[bool, dict]:
    print("\n[PHASE 1] Test Matrix Generation")

    matrix = {
        "Electronic": {
            "House":        {"artist": "Fred again..", "override_key": "fred again..", "expected_path_fragment": "House"},
            "UKG":          {"artist": "Sammy Virji",  "override_key": "sammy virji",  "expected_path_fragment": "UKG"},
            "Dubstep":      {"artist": "Hamdi",        "override_key": "hamdi",         "expected_path_fragment": "Dubstep"},
            "Drum and Bass":{"artist": "Chase & Status","override_key":"chase & status","expected_path_fragment": "Drum and Bass"},
            "Bass":         {"artist": "Skrillex",     "override_key": "skrillex",      "expected_path_fragment": "Bass"},
            "Techno":       {"artist": "Adam Beyer",   "override_key": None,            "expected_path_fragment": "Techno"},
        },
        "Indian": {
            "Bollywood":    {"artist": "Arijit Singh",          "override_key": "arijit singh",         "expected_path_fragment": "Bollywood"},
            "Punjabi":      {"artist": "Karan Aujla",           "override_key": "karan aujla",          "expected_path_fragment": "Punjabi"},
            "Tamil":        {"artist": "Anirudh Ravichander",   "override_key": "anirudh ravichander",  "expected_path_fragment": "Tamil"},
            "Telugu":       {"artist": "S.S. Thaman",           "override_key": None,                   "expected_path_fragment": "Telugu"},
            "Hindi Pop":    {"artist": "King",                  "override_key": "king",                 "expected_path_fragment": "Hindi Pop"},
            "Indian HipHop":{"artist": "Seedhe Maut",           "override_key": "seedhe maut",          "expected_path_fragment": "HipHop"},
        },
        "Global": {
            "HipHop":       {"artist": "Drake",         "override_key": "drake",         "expected_path_fragment": "HipHop"},
            "Pop":          {"artist": "Michael Jackson","override_key": "michael jackson","expected_path_fragment": "Pop"},
            "Rock":         {"artist": "AC/DC",          "override_key": None,            "expected_path_fragment": "Rock"},
        },
        "Special": {
            "remix":            {"scenario": "VIP/Extended/Edit variants grouped in family subfolder"},
            "bad_metadata":     {"scenario": "Missing BPM/key/artist → NeedsReview"},
            "duplicate_filename":{"scenario": "_N suffix collision guard in _safe_dest()"},
            "same_audio_diff_name":{"scenario": "Fingerprint identity_key dedup"},
            "unknown_genre":    {"scenario": "Low confidence → NeedsReview, NEVER Quarantine"},
            "corrupt_temp":     {"scenario": "*.trimmed.mp3 / _1.mp3 → Quarantine, not Library"},
        },
    }

    total = sum(len(v) for v in matrix.values())
    payload = {
        "generated_at": _NOW,
        "total_test_cases": total,
        "categories": matrix,
    }
    _write_report("test_matrix.json", payload, output_dir)
    result = _check("Test matrix generated", True, f"{total} test cases")
    return True, {"checks": [result], "matrix": payload}


# ─────────────────────────────────────────────────────────────────
# PHASE 2 — Ingestion Flow Validation
# ─────────────────────────────────────────────────────────────────

def phase2_ingestion_flow() -> tuple[bool, dict]:
    print("\n[PHASE 2] Ingestion Flow Validation")
    checks = []
    passed = True

    pipeline_stages = [
        ("services.downloader_service", "DownloaderService"),
        ("services.tagger_service",     "tag_file"),
        ("services.genre_router",       "resolve_genre_folder"),
        ("services.organizer_service",  "resolve_destination_path"),
        ("services.dedup_service",      "canonical_score"),
        ("database",                    "get_library_index_collection"),
        ("library_scanner",             "is_excluded_path"),
    ]
    for module, func in pipeline_stages:
        try:
            mod = importlib.import_module(module)
            ok  = hasattr(mod, func)
            c = _check(f"2-pipeline: {module}.{func}", ok, "" if ok else "missing")
        except Exception as e:
            c = _check(f"2-pipeline: {module}", False, str(e)[:80])
            ok = False
        checks.append(c)
        passed &= ok

    # No Ingest/Library nesting on disk
    ingest = BASE_DIR / "Ingest"
    if ingest.is_dir():
        nested = list(ingest.glob("Library"))
        c = _check("2-no Ingest/Library nesting on disk", len(nested) == 0,
                   f"nested: {nested}" if nested else "")
        checks.append(c)
        passed &= len(nested) == 0

    # No temp files in canonical Library/
    if LIB_DIR.is_dir():
        temp_in_lib = [p for p in LIB_DIR.rglob("*.mp3")
                       if p.stem.endswith(".trimmed") or p.stem.endswith("_1")]
        c = _warn("2-no temp files in Library/", len(temp_in_lib) == 0,
                  f"{len(temp_in_lib)} temp file(s) found" if temp_in_lib else "")
        checks.append(c)

    # Library_index recent entries
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            total = col.count_documents({})
            nr_count = col.count_documents(
                {"genre_folder": {"$regex": r"^NeedsReview", "$options": "i"}}
            )
            ingest_leak = col.count_documents(
                {"final_path": {"$regex": r"[/\\][Ii]ngest[/\\]"}}
            )
            checks.append(_check("2-library_index accessible", True, f"{total} entries"))
            checks.append(_check("2-no Ingest paths in library_index", ingest_leak == 0,
                                 f"{ingest_leak} leaked" if ingest_leak else ""))
            checks.append(_warn("2-NeedsReview index entries",
                                nr_count < total * 0.2 if total else True,
                                f"{nr_count}/{total} ({100*nr_count//max(total,1)}%)"))
            passed &= ingest_leak == 0
        else:
            checks.append(_warn("2-library_index", False, "MongoDB unavailable"))
    except Exception as e:
        checks.append(_check("2-library_index query", False, str(e)[:80]))

    return passed, {"checks": checks}


# ─────────────────────────────────────────────────────────────────
# PHASE 3 — Genre Routing Validation
# ─────────────────────────────────────────────────────────────────

_ROUTING_TESTS: list[tuple[str, str, str]] = [
    ("Sammy Virji",          "sammy virji",         "UKG"),
    ("Skrillex",             "skrillex",            "Bass"),
    ("Hamdi",                "hamdi",               "Dubstep"),
    ("Chase & Status",       "chase & status",      "Drum and Bass"),
    ("Karan Aujla",          "karan aujla",         "Punjabi"),
    ("Arijit Singh",         "arijit singh",        "Bollywood"),
    ("Anirudh Ravichander",  "anirudh ravichander", "Tamil"),
    ("Fred again..",         "fred again..",        "House"),
    ("AP Dhillon",           "ap dhillon",          "Punjabi"),
    ("Drake",                "drake",               "HipHop"),
    ("Michael Jackson",      "michael jackson",     "Pop"),
    ("The Weeknd",           "the weeknd",          "OpenFormat"),
    ("Seedhe Maut",          "seedhe maut",         "HipHop"),
    ("Andy C",               "andy c",              "Drum and Bass"),
]

# Artist with no override → should route to NeedsReview (no Spotify client)
_NR_TEST_ARTIST = ("__unknown_test_artist_xyz__", "unknown")


def phase3_genre_routing(output_dir: Path) -> tuple[bool, dict]:
    print("\n[PHASE 3] Genre Routing Validation")
    checks = []
    results = []
    passed = True

    try:
        from services.genre_router import (
            _library_path, GENRE_TAXONOMY, NEEDS_REVIEW_DIR,
            normalize_genre, resolve_genre_family,
        )
        from config import config as _cfg
    except Exception as e:
        c = _check("3-genre_router import", False, str(e))
        return False, {"checks": [c]}

    # 3-A: Taxonomy completeness
    for genre in ["House", "UK Garage", "Dubstep", "Bass", "Drum and Bass",
                  "Techno", "Bollywood", "Punjabi", "Tamil", "Telugu",
                  "Indian Hip Hop", "Hip Hop", "Pop", "R&B"]:
        path = _library_path(genre)
        ok   = path.startswith("Library/")
        checks.append(_check(f"3-taxonomy: {genre} → Library/", ok, path))
        passed &= ok

    # 3-B: ARTIST_GENRE_OVERRIDE routing
    for display, key, expected_frag in _ROUTING_TESTS:
        genre = _cfg.ARTIST_GENRE_OVERRIDE.get(key, "")
        if genre:
            lib_path = _library_path(genre)
            ok = expected_frag.lower() in lib_path.lower()
            c  = _check(f"3-route: {display}",  ok,
                        f"→ {lib_path} (expected …{expected_frag}…)" if not ok else lib_path)
        else:
            c  = _warn(f"3-route: {display} (no override)", True,
                       f"not in ARTIST_GENRE_OVERRIDE — will use memory/Spotify")
            ok = True
        checks.append(c)
        passed &= ok
        results.append({
            "artist":        display,
            "override_key":  key,
            "genre":         genre,
            "library_path":  _library_path(genre) if genre else None,
            "expected_frag": expected_frag,
            "pass":          ok,
        })

    # 3-C: Unknown artist → NeedsReview (no Spotify → confidence 0)
    try:
        from services.genre_router import resolve_genre_folder_with_confidence
        folder, conf, src = resolve_genre_folder_with_confidence("", _NR_TEST_ARTIST[0], None)
        ok = folder.startswith(NEEDS_REVIEW_DIR)
        checks.append(_check("3-unknown artist → NeedsReview", ok,
                             f"got: {folder} (conf={conf:.2f})"))
        passed &= ok
    except Exception as e:
        checks.append(_check("3-NeedsReview fallback", False, str(e)[:80]))
        passed = False

    # 3-D: Confidence thresholds
    try:
        from services.genre_router import CONFIDENCE_THRESHOLD
        checks.append(_check("3-confidence threshold ≥ 0.5",
                             CONFIDENCE_THRESHOLD >= 0.5, f"{CONFIDENCE_THRESHOLD}"))
    except Exception as e:
        checks.append(_check("3-confidence threshold", False, str(e)[:60]))

    # 3-E: Normalize genre roundtrip
    for raw, expected in [("uk garage", "UK Garage"), ("drum and bass", "Drum and Bass"),
                           ("bollywood", "Bollywood"), ("hip hop", "Hip Hop")]:
        norm = normalize_genre(raw)
        ok   = norm == expected
        checks.append(_check(f"3-normalize: '{raw}' → '{expected}'", ok,
                             f"got '{norm}'" if not ok else ""))
        passed &= ok

    payload = {
        "generated_at": _NOW,
        "total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "results": results,
        "checks": [c["status"] for c in checks],
    }
    _write_report("routing_validation_report.json", payload, output_dir)
    return passed, {"checks": checks, "results": results}


# ─────────────────────────────────────────────────────────────────
# PHASE 4 — NeedsReview Lifecycle
# ─────────────────────────────────────────────────────────────────

def phase4_needsreview_lifecycle(output_dir: Path) -> tuple[bool, dict]:
    print("\n[PHASE 4] NeedsReview Lifecycle")
    checks = []
    passed = True

    nr_files   = list(NR_DIR.rglob("*.mp3")) if NR_DIR.is_dir() else []
    nr_count   = len(nr_files)
    nr_folders = {f.parent.name for f in nr_files}
    checks.append(_warn("4-NeedsReview file count",
                        nr_count < 200,
                        f"{nr_count} file(s) in NeedsReview/"))

    # Sample tag quality of NeedsReview files
    missing_artist = missing_bpm = 0
    for fp in nr_files[:20]:
        try:
            from mutagen.id3 import ID3
            audio = ID3(str(fp))
            if not audio.get("TPE1"):
                missing_artist += 1
            if not audio.get("TBPM"):
                missing_bpm += 1
        except Exception:
            missing_artist += 1

    if nr_count:
        checks.append(_warn("4-NR missing artist tags",
                            missing_artist < nr_count // 2,
                            f"{missing_artist}/{min(nr_count,20)} sampled"))
        checks.append(_warn("4-NR missing BPM tags",
                            missing_bpm < nr_count // 2,
                            f"{missing_bpm}/{min(nr_count,20)} sampled"))

    # Dry-run reclassification
    try:
        from services.reclassification_service import run_reclassification
        report = run_reclassification(dry_run=True, limit=10)
        checks.append(_check("4-reclassification callable", True,
                             f"scanned={report['scanned']} rerouted(dry)={report['rerouted']}"))
        checks.append(_check("4-reclassification no errors", report["errors"] == 0,
                             f"{report['errors']} error(s)" if report["errors"] else ""))
        passed &= report["errors"] == 0
    except Exception as e:
        checks.append(_check("4-reclassification run", False, str(e)[:80]))
        passed = False

    # No NeedsReview → Quarantine (safeguard)
    if QUA_DIR.is_dir():
        qua_from_nr = [p for p in QUA_DIR.rglob("*.mp3")
                       if "NeedsReview" in str(p.parent)]
        checks.append(_check("4-NeedsReview never enters Quarantine",
                             len(qua_from_nr) == 0,
                             f"{len(qua_from_nr)} found" if qua_from_nr else ""))
        passed &= len(qua_from_nr) == 0

    # genre_router never maps valid artist to Quarantine
    try:
        from services.genre_router import _library_path, GENRE_TAXONOMY
        quarantine_routes = [k for k in GENRE_TAXONOMY
                             if "quarantine" in _library_path(k).lower()]
        checks.append(_check("4-no Quarantine in GENRE_TAXONOMY",
                             len(quarantine_routes) == 0,
                             str(quarantine_routes) if quarantine_routes else ""))
        passed &= len(quarantine_routes) == 0
    except Exception as e:
        checks.append(_check("4-quarantine-in-taxonomy check", False, str(e)[:60]))

    payload = {
        "generated_at": _NOW,
        "nr_file_count": nr_count,
        "nr_folders":    list(nr_folders)[:50],
        "missing_artist_tags_sample": missing_artist,
        "missing_bpm_sample":         missing_bpm,
    }
    _write_report("needsreview_report.json", payload, output_dir)
    return passed, {"checks": checks, "nr_count": nr_count}


# ─────────────────────────────────────────────────────────────────
# PHASE 5 — Quarantine Validation
# ─────────────────────────────────────────────────────────────────

import re as _re
_QUARANTINE_PATTERNS = _re.compile(
    r"(\.trimmed\.mp3|_\d{1,2}\.mp3|\.tmp|\.partial)$", _re.IGNORECASE
)

def phase5_quarantine_validation() -> tuple[bool, dict]:
    print("\n[PHASE 5] Quarantine Policy Validation")
    checks = []
    passed = True

    if not QUA_DIR.is_dir():
        c = _warn("5-Quarantine dir exists", False, "Quarantine/ not found — OK if empty system")
        return True, {"checks": [c], "quarantine_files": 0}

    qua_files = list(QUA_DIR.rglob("*.mp3"))
    canonical_in_qua = [p for p in qua_files
                        if not _QUARANTINE_PATTERNS.search(p.name)]

    checks.append(_warn("5-quarantine file count", len(qua_files) < 500,
                        f"{len(qua_files)} file(s) in Quarantine/"))
    checks.append(_check("5-no canonical MP3s quarantined",
                         len(canonical_in_qua) == 0,
                         f"{len(canonical_in_qua)} suspicious: {[p.name for p in canonical_in_qua[:5]]}"
                         if canonical_in_qua else ""))
    passed &= len(canonical_in_qua) == 0

    # No Library/ paths inside Quarantine (re-indexing guard)
    lib_in_qua = [p for p in QUA_DIR.rglob("*")
                  if p.is_dir() and p.name.lower() == "library"]
    checks.append(_check("5-no Library/ inside Quarantine",
                         len(lib_in_qua) == 0, str(lib_in_qua) if lib_in_qua else ""))
    passed &= len(lib_in_qua) == 0

    # library_index must not point into Quarantine
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            qua_indexed = col.count_documents(
                {"final_path": {"$regex": r"[/\\][Qq]uarantine[/\\]"}}
            )
            checks.append(_check("5-no Quarantine entries in library_index",
                                 qua_indexed == 0,
                                 f"{qua_indexed} indexed" if qua_indexed else ""))
            passed &= qua_indexed == 0
    except Exception:
        pass

    return passed, {"checks": checks, "quarantine_files": len(qua_files),
                    "canonical_in_quarantine": len(canonical_in_qua)}


# ─────────────────────────────────────────────────────────────────
# PHASE 6 — Remix Family Grouping
# ─────────────────────────────────────────────────────────────────

_REMIX_TESTS: list[tuple[str, str, str]] = [
    ("Feel So Close (Extended Mix).mp3", "Extended",     "Feel So Close"),
    ("Titanium (VIP).mp3",               "VIP",          "Titanium"),
    ("Work (Club Edit).mp3",             "Club Mix",     "Work"),
    ("One More Time (Remix).mp3",        "Remix",        "One More Time"),
    ("Mr. Brightside (Live).mp3",        "Live",         "Mr. Brightside"),
    ("Levels (Radio Edit).mp3",          "Radio",        "Levels"),
    ("Classical Piano (Instrumental Version).mp3", "Instrumental", "Classical Piano"),
    ("Original Track.mp3",               "",             "Original Track"),
    ("Clean Bandit Bootleg.mp3",         "Bootleg",      "Clean Bandit"),
]

def phase6_remix_grouping(output_dir: Path) -> tuple[bool, dict]:
    print("\n[PHASE 6] Remix Family Grouping")
    checks = []
    passed = True
    results = []

    try:
        from services.organizer_service import detect_mix_variant
    except Exception as e:
        c = _check("6-detect_mix_variant import", False, str(e))
        return False, {"checks": [c]}

    for filename, expected_variant, expected_base_frag in _REMIX_TESTS:
        base, variant = detect_mix_variant(filename)
        variant_ok = variant == expected_variant
        base_ok    = expected_base_frag.lower() in base.lower() if expected_base_frag else True
        ok = variant_ok and base_ok
        c  = _check(f"6-remix: {filename[:45]}",  ok,
                    f"variant={variant!r} base={base!r}"
                    + (f" (expected variant={expected_variant!r})" if not variant_ok else ""))
        checks.append(c)
        passed &= ok
        results.append({
            "filename": filename, "base": base,
            "variant": variant, "expected_variant": expected_variant,
            "pass": ok,
        })

    # Scan Library/ for actual family subdirectory examples
    family_dirs: list[str] = []
    if LIB_DIR.is_dir():
        for p in LIB_DIR.rglob("*"):
            if p.is_dir():
                _, variant = detect_mix_variant(p.name)
                if variant:
                    family_dirs.append(str(p.relative_to(BASE_DIR)))
    checks.append(_warn("6-family dirs on disk", True,
                        f"{len(family_dirs)} remix family subfolder(s) found"))

    # No duplicate filename collisions (_N suffix should be rare)
    collision_files = []
    if LIB_DIR.is_dir():
        collision_files = [p.name for p in LIB_DIR.rglob("*_1.mp3")]
    checks.append(_warn("6-collision _1 files in Library/",
                        len(collision_files) < 10,
                        f"{len(collision_files)} file(s)" if collision_files else "0 (good)"))

    payload = {
        "generated_at": _NOW,
        "pattern_tests": {"total": len(results), "passed": sum(1 for r in results if r["pass"])},
        "family_dirs_on_disk": family_dirs[:30],
        "collision_files_sample": collision_files[:10],
        "results": results,
    }
    _write_report("remix_grouping_report.json", payload, output_dir)
    return passed, {"checks": checks}


# ─────────────────────────────────────────────────────────────────
# PHASE 7 — Duplicate Policy Validation
# ─────────────────────────────────────────────────────────────────

# Score formula: spotify_id(+10) bpm+key(+8) artwork(+6) camelot(+4) bitrate320(+3)
#   penalties: temp(-5) ingest(-3) NeedsReview(-1)
# Exact expected scores:
#   perfect canonical : 10+8+6+4+3      = 31
#   ingest path       : 10+8+6+4+3 -3   = 28  (all metadata, Ingest penalty)
#   temp file         : 10+8+6+4+3 -5   = 26  (all metadata, temp penalty)
#   no spotify_id     :  0+8+6+4+3      = 21  (no id, Library/ path)
#   NeedsReview bare  :  0+0+0+0+0 -1   = -1  (no metadata, NR penalty)
_DEDUP_SCENARIOS: list[dict] = [
    {"name": "perfect canonical",
     "args": {"spotify_id": "6rqhFgbbKwnb9MLmUQDhG6", "has_bpm": True, "has_key": True,
               "has_artwork": True, "has_camelot": True, "bitrate_kbps": 320},
     "path": "/Library/Electronic/House/Fred again../Track.mp3",
     "min_score": 30},   # expects 31
    {"name": "ingest path",
     "args": {"spotify_id": "abc123", "has_bpm": True, "has_key": True,
               "has_artwork": True, "has_camelot": True, "bitrate_kbps": 320},
     "path": "/Ingest/Staging/Track.mp3",
     "max_score": 29},   # expects 28  (full metadata, -3 Ingest penalty)
    {"name": "temp file (.trimmed)",
     "args": {"spotify_id": "abc123", "has_bpm": True, "has_key": True,
               "has_artwork": True, "has_camelot": True, "bitrate_kbps": 320},
     "path": "/Library/House/Track.trimmed.mp3",
     "max_score": 27},   # expects 26  (full metadata, -5 temp penalty)
    {"name": "no spotify_id",
     "args": {"spotify_id": "", "has_bpm": True, "has_key": True,
               "has_artwork": True, "has_camelot": True, "bitrate_kbps": 320},
     "path": "/Library/House/Track.mp3",
     "min_score": 15},   # expects 21  (no spotify_id, all other tags present)
    {"name": "NeedsReview no metadata",
     "args": {"spotify_id": "", "has_bpm": False, "has_key": False,
               "has_artwork": False, "has_camelot": False, "bitrate_kbps": 128},
     "path": "/NeedsReview/Unknown/Track.mp3",
     "max_score": 5},    # expects -1 (no metadata, NR penalty)
]

def phase7_duplicate_policy() -> tuple[bool, dict]:
    print("\n[PHASE 7] Duplicate Policy Validation")
    checks = []
    passed = True

    try:
        from services.dedup_service import canonical_score
    except Exception as e:
        c = _check("7-canonical_score import", False, str(e))
        return False, {"checks": [c]}

    scores: dict[str, float] = {}
    for sc in _DEDUP_SCENARIOS:
        score = canonical_score(sc["path"], **sc["args"])
        scores[sc["name"]] = score
        if "min_score" in sc:
            ok = score >= sc["min_score"]
            c  = _check(f"7-dedup: {sc['name']} ≥ {sc['min_score']}",  ok,
                        f"score={score:.1f}")
        else:
            ok = score <= sc["max_score"]
            c  = _check(f"7-dedup: {sc['name']} ≤ {sc['max_score']}", ok,
                        f"score={score:.1f}")
        checks.append(c)
        passed &= ok

    # Relative ordering by actual formula: perfect(31) > ingest(28) > temp(26) > no_spotify(21) > NR(-1)
    expected_order = ["perfect canonical", "ingest path", "temp file (.trimmed)",
                      "no spotify_id", "NeedsReview no metadata"]
    for i in range(len(expected_order) - 1):
        a, b = expected_order[i], expected_order[i + 1]
        ok = scores.get(a, 0) >= scores.get(b, 0)
        checks.append(_check(f"7-ordering: {a} ≥ {b}", ok,
                             f"{scores.get(a,0):.1f} vs {scores.get(b,0):.1f}"))
        passed &= ok

    return passed, {"checks": checks, "scores": scores}


# ─────────────────────────────────────────────────────────────────
# PHASE 8 — Artist Memory Learning
# ─────────────────────────────────────────────────────────────────

_TEST_ARTIST_KEY = "__validate_prod_test_artist__"
_TEST_ARTIST_DISPLAY = "__ValidateProdTest__"

def phase8_artist_memory(output_dir: Path) -> tuple[bool, dict]:
    print("\n[PHASE 8] Artist Memory Learning")
    checks = []
    passed = True

    try:
        from services.artist_memory_service import (
            record_move, lookup_artist, forget_artist, _normalize_artist
        )
    except Exception as e:
        c = _check("8-artist_memory import", False, str(e))
        return False, {"checks": [c]}

    # 8-1: Normalization
    for raw, expected in [("SAMMY VIRJI", "sammy virji"), ("Fred again..", "fred again.."),
                           ("The Weeknd", "the weeknd"),   ("  Hamdi  ", "hamdi")]:
        norm = _normalize_artist(raw)
        ok   = norm == expected
        checks.append(_check(f"8-normalize: {raw!r} → {expected!r}", ok,
                             f"got {norm!r}" if not ok else ""))
        passed &= ok

    # 8-2: Record→Lookup roundtrip (using test key, cleaned up after)
    col_ok = False
    try:
        record_move(_TEST_ARTIST_DISPLAY, "House", source="validation_test", family="Electronic")
        mem = lookup_artist(_TEST_ARTIST_DISPLAY)
        col_ok = mem is not None and mem.get("genre") == "House"
        checks.append(_check("8-record+lookup roundtrip", col_ok,
                             f"got {mem}" if not col_ok else "genre=House ✓"))
        passed &= col_ok
    except Exception as e:
        checks.append(_warn("8-MongoDB roundtrip", False,
                            f"MongoDB unavailable ({str(e)[:60]}) — logic test only"))

    # 8-3: Confidence accumulation formula
    from services.artist_memory_service import _CONFIDENCE_PER_MOVE, _AGING_FLOOR
    for moves, expected_conf in [(1, 0.25), (2, 0.5), (4, 1.0), (8, 1.0)]:
        conf = min(1.0, moves * _CONFIDENCE_PER_MOVE)
        ok = abs(conf - expected_conf) < 0.01
        checks.append(_check(f"8-confidence: {moves} moves → {expected_conf}", ok,
                             f"got {conf}"))
        passed &= ok

    # 8-4: Aging floor
    checks.append(_check("8-aging floor ≥ 0.5", _AGING_FLOOR >= 0.5, f"{_AGING_FLOOR}"))

    # 8-5: Alias lookup
    try:
        mem_alias = lookup_artist("sammy virji")  # should be seeded
        checks.append(_warn("8-seeded artist lookup (Sammy Virji)",
                            mem_alias is not None,
                            "not in memory — run seed_artist_memory() first" if not mem_alias else ""))
    except Exception:
        pass

    # Cleanup test record
    try:
        forget_artist(_TEST_ARTIST_DISPLAY)
    except Exception:
        pass

    # Stats from real collection
    stats: dict = {}
    try:
        from database import get_artist_memory_collection
        col = get_artist_memory_collection()
        if col is not None:
            total = col.count_documents({})
            by_fam: Counter = Counter(
                doc.get("family", "unknown")
                for doc in col.find({}, {"family": 1, "_id": 0})
            )
            stats = {"total": total, "by_family": dict(by_fam.most_common())}
            checks.append(_warn("8-artist_memory entries seeded",
                                total >= 10,
                                f"{total} entries — run seed_artist_memory() to populate"
                                if total < 10 else f"{total} entries"))
    except Exception:
        pass

    payload = {
        "generated_at": _NOW,
        "normalization_tests": "all passed" if passed else "some failed",
        "collection_stats":    stats,
    }
    _write_report("artist_memory_report.json", payload, output_dir)
    return passed, {"checks": checks, "stats": stats}


# ─────────────────────────────────────────────────────────────────
# PHASE 9 — Reconciliation Validation
# ─────────────────────────────────────────────────────────────────

def phase9_reconciliation() -> tuple[bool, dict]:
    print("\n[PHASE 9] Reconciliation Validation")
    checks = []
    passed = True

    recon_path = _BACKEND / "reconcile_library_state.py"
    if not recon_path.exists():
        c = _check("9-reconcile_library_state.py exists", False)
        return False, {"checks": [c]}

    try:
        spec = importlib.util.spec_from_file_location("_recon_val", recon_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        checks.append(_check("9-reconcile module loads", True))
    except Exception as e:
        checks.append(_check("9-reconcile module loads", False, str(e)[:80]))
        return False, {"checks": checks}

    try:
        report   = mod.reconcile(dry_run=True, repair=False, reindex=False)
        summary  = report.get("summary", {})
        issues   = summary.get("total_issues", 0)
        status   = summary.get("status", "unknown")

        checks.append(_check("9-reconcile runs cleanly", True,
                             f"status={status} issues={issues}"))

        def _cat_count(key: str) -> int:
            v = report.get(key, [])
            return len(v) if isinstance(v, list) else int(v or 0)

        # Hard-fail only on structural integrity issues
        for category in ["stale_index_entries", "broken_paths", "duplicate_hashes"]:
            count = _cat_count(category)
            ok    = count == 0
            checks.append(_check(f"9-{category}", ok,
                                 f"{count} item(s)" if count else ""))
            passed &= ok

        # Warn-only on metadata completeness (enrichment backlog — not structural failure)
        warn_categories = [
            ("missing_metadata",  "files missing BPM/Key/SpotifyID (needs enrichment)"),
            ("missing_artwork",   "files missing artwork (needs enrichment)"),
            ("orphaned_files",    "on-disk files not in library_index (needs re-index)"),
            ("missing_history",   "index entries with no download_history match"),
        ]
        for key, label in warn_categories:
            count = _cat_count(key)
            checks.append(_warn(f"9-{label}", count == 0,
                                f"{count} item(s) — non-critical" if count else ""))

        return passed, {"checks": checks, "reconciliation_summary": summary}

    except Exception as e:
        checks.append(_check("9-reconcile run", False, str(e)[:80]))
        return False, {"checks": checks}


# ─────────────────────────────────────────────────────────────────
# PHASE 10 — Performance Measurement
# ─────────────────────────────────────────────────────────────────

def phase10_performance(output_dir: Path) -> tuple[bool, dict]:
    print("\n[PHASE 10] Performance Measurement")
    checks = []
    timings: dict[str, float] = {}

    def _time(label: str, fn, n: int = 100) -> float:
        t0 = time.perf_counter()
        for _ in range(n):
            try:
                fn()
            except Exception:
                pass
        elapsed = (time.perf_counter() - t0) / n * 1000  # ms per call
        timings[label] = round(elapsed, 3)
        return elapsed

    # Genre routing (config override path — no Spotify)
    try:
        from services.genre_router import _library_path
        lat = _time("genre_router._library_path", lambda: _library_path("UK Garage"))
        checks.append(_check(f"10-route latency < 1ms", lat < 1.0, f"{lat:.3f}ms/call"))
    except Exception as e:
        checks.append(_check("10-route latency", False, str(e)[:60]))

    # Artist memory lookup (cold read from MongoDB or graceful skip)
    try:
        from services.artist_memory_service import lookup_artist
        lat = _time("artist_memory.lookup", lambda: lookup_artist("Sammy Virji"), n=20)
        checks.append(_warn(f"10-artist_memory lookup < 50ms", lat < 50.0,
                            f"{lat:.1f}ms/call"))
        timings["artist_memory_lookup_ms"] = round(lat, 1)
    except Exception as e:
        checks.append(_warn("10-artist_memory latency", False, str(e)[:60]))

    # Canonical scoring
    try:
        from services.dedup_service import canonical_score
        lat = _time("canonical_score",
                    lambda: canonical_score("/Library/House/Track.mp3", spotify_id="abc",
                                            has_bpm=True, has_key=True))
        checks.append(_check(f"10-canonical_score < 1ms", lat < 1.0, f"{lat:.3f}ms/call"))
    except Exception as e:
        checks.append(_check("10-canonical_score latency", False, str(e)[:60]))

    # Library file count (just stat, no read)
    t0 = time.perf_counter()
    lib_count = sum(1 for _ in LIB_DIR.rglob("*.mp3")) if LIB_DIR.is_dir() else 0
    scan_ms   = (time.perf_counter() - t0) * 1000
    timings["library_scan_ms"]    = round(scan_ms, 0)
    timings["library_file_count"] = lib_count
    checks.append(_warn(f"10-Library/ scan time", scan_ms < 30_000,
                        f"{scan_ms:.0f}ms for {lib_count} files"))

    # Mix variant detection
    try:
        from services.organizer_service import detect_mix_variant
        lat = _time("detect_mix_variant",
                    lambda: detect_mix_variant("Feel So Close (Extended Mix).mp3"))
        checks.append(_check("10-detect_mix_variant < 0.1ms", lat < 0.1, f"{lat:.4f}ms/call"))
    except Exception as e:
        checks.append(_check("10-detect_mix_variant latency", False, str(e)[:60]))

    payload = {"generated_at": _NOW, "timings_ms": timings}
    _write_report("performance_report.json", payload, output_dir)
    return True, {"checks": checks, "timings": timings}


# ─────────────────────────────────────────────────────────────────
# PHASE 11 — Final Production Readiness
# ─────────────────────────────────────────────────────────────────

def phase11_production_readiness(
    phase_results: dict[str, tuple[bool, dict]],
    output_dir: Path,
) -> tuple[bool, dict]:
    print("\n[PHASE 11] Final Production Readiness")
    checks = []
    passed = True

    # Run verify_patches.py
    try:
        spec = importlib.util.spec_from_file_location("_vp", _BACKEND / "verify_patches.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        patch_results = [
            mod.verify_patch1(),
            mod.verify_patch2_map(),
            mod.verify_patch2_features(),
            mod.verify_patch2_txxx(),
            mod.verify_patch2_report(),
            mod.verify_patch3(),
            mod.verify_patch4(),
            mod.verify_patch5(),
        ]
        patches_ok = all(patch_results)
        checks.append(_check("11-verify_patches.py", patches_ok,
                             f"{sum(patch_results)}/{len(patch_results)} passed"))
        passed &= patches_ok
    except Exception as e:
        checks.append(_check("11-verify_patches.py", False, str(e)[:80]))
        passed = False

    # Run verify_pipeline_integrity.py phases
    try:
        vpi_path = _BACKEND / "verify_pipeline_integrity.py"
        spec = importlib.util.spec_from_file_location("_vpi", vpi_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        integrity_phases = [
            ("verify_phase1",              "DownloadResult"),
            ("verify_phase2",              "dedup_service"),
            ("verify_phase3",              "genre_router"),
            ("verify_phase4",              "NeedsReview routing"),
            ("verify_universal_org",       "universal org architecture"),
            ("verify_final_rollout",       "final rollout"),
            ("verify_artist_folder_rollout", "artist folder rollout"),
        ]
        vpi_results = []
        for fn_name, label in integrity_phases:
            fn = getattr(mod, fn_name, None)
            if fn:
                try:
                    r = fn()
                    vpi_results.append(r)
                    checks.append(_check(f"11-integrity: {label}", r))
                    passed &= r
                except Exception as e2:
                    checks.append(_check(f"11-integrity: {label}", False, str(e2)[:60]))
                    passed = False
    except Exception as e:
        checks.append(_warn("11-verify_pipeline_integrity.py", False, str(e)[:80]))

    # Aggregate all phase pass/fail
    phase_summary: dict[str, str] = {}
    for name, (ok, _) in phase_results.items():
        phase_summary[name] = "PASS" if ok else "FAIL"
        passed &= ok

    # Risk assessment from library state
    risks: list[str] = []
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col:
            nr    = col.count_documents({"genre_folder": {"$regex": r"^NeedsReview", "$options": "i"}})
            total = col.count_documents({})
            ingest_leak = col.count_documents({"final_path": {"$regex": r"[/\\][Ii]ngest[/\\]"}})
            if ingest_leak:
                risks.append(f"{ingest_leak} library_index entries still point to Ingest/ paths")
            if total and nr / total > 0.15:
                risks.append(f"{nr}/{total} ({100*nr//total}%) tracks in NeedsReview — run reclassification")
        nr_disk = sum(1 for _ in NR_DIR.rglob("*.mp3")) if NR_DIR.is_dir() else 0
        if nr_disk > 50:
            risks.append(f"{nr_disk} MP3s on disk in NeedsReview/")
    except Exception:
        pass

    # Routing confidence distribution from config overrides
    try:
        from config import config as _cfg
        from services.genre_router import GENRE_TAXONOMY
        override_genres: Counter = Counter(_cfg.ARTIST_GENRE_OVERRIDE.values())
        conf_dist = {g: c for g, c in override_genres.most_common(10)}
    except Exception:
        conf_dist = {}

    payload = {
        "generated_at":         _NOW,
        "overall_pass":         passed,
        "phase_summary":        phase_summary,
        "unresolved_risks":     risks,
        "routing_conf_dist":    conf_dist,
        "quarantine_files":     sum(1 for _ in QUA_DIR.rglob("*.mp3")) if QUA_DIR.is_dir() else 0,
        "needs_review_disk":    sum(1 for _ in NR_DIR.rglob("*.mp3")) if NR_DIR.is_dir() else 0,
        "recommended_actions": [
            "Run: python migrate_library_structure.py --verify-only",
            "Run: python migrate_library_structure.py --analyze  # detect remaining artist folders",
            "Run: python generate_org_report.py  # full rollout status",
            "Seed artist memory: from services.artist_memory_service import seed_artist_memory; seed_artist_memory()",
        ] if risks else ["System is production-ready."],
    }
    _write_report("production_readiness_report.json", payload, output_dir)

    print(f"\n{'PASS' if passed else 'FAIL'} — {len([r for r in phase_summary.values() if r == 'PASS'])}"
          f"/{len(phase_summary)} phases passed | {len(risks)} risk(s)")

    return passed, {"checks": checks, "phase_summary": phase_summary, "risks": risks}


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="End-to-end production workflow validator (11 phases)"
    )
    parser.add_argument("--phase",  type=int, default=0, help="Run single phase (1–11)")
    parser.add_argument("--output", default=str(BASE_DIR), help="Directory for report JSON files")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 65)
    print("  DJ Pipeline — Production Workflow Validator")
    print(f"  Library: {BASE_DIR}")
    print("=" * 65)

    phase_fns = {
        1:  lambda: phase1_test_matrix(output_dir),
        2:  lambda: phase2_ingestion_flow(),
        3:  lambda: phase3_genre_routing(output_dir),
        4:  lambda: phase4_needsreview_lifecycle(output_dir),
        5:  lambda: phase5_quarantine_validation(),
        6:  lambda: phase6_remix_grouping(output_dir),
        7:  lambda: phase7_duplicate_policy(),
        8:  lambda: phase8_artist_memory(output_dir),
        9:  lambda: phase9_reconciliation(),
        10: lambda: phase10_performance(output_dir),
    }

    if args.phase and args.phase in phase_fns:
        ok, result = phase_fns[args.phase]()
        sys.exit(0 if ok else 1)

    phase_results: dict[str, tuple[bool, dict]] = {}
    for n, fn in phase_fns.items():
        ok, result = fn()
        phase_results[f"phase{n}"] = (ok, result)

    ok11, _ = phase11_production_readiness(phase_results, output_dir)

    overall = all(ok for ok, _ in phase_results.values()) and ok11
    print("\n" + "=" * 65)
    print(f"  {'ALL PHASES PASSED' if overall else 'SOME PHASES FAILED'}")
    print("=" * 65)
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
