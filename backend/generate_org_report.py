"""
Organization Rollout Report — Phase 7
======================================
Queries library_index + artist_memory + disk state to produce
organization_rollout_report.json beside BASE_DIR.

Usage
-----
    cd backend
    python generate_org_report.py
    python generate_org_report.py --output /custom/path/report.json

Exit codes: 0 = success, 1 = error
"""
from __future__ import annotations

import argparse
import json
import sys
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
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

from config import config

BASE_DIR = Path(config.BASE_DOWNLOAD_DIR)
LIB_DIR  = BASE_DIR / "Library"
NR_DIR   = BASE_DIR / "NeedsReview"


def _count_library_index() -> dict:
    """Return per-genre_folder counts and confidence stats from library_index."""
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is None:
            return {"available": False}
        total  = col.count_documents({})
        nr     = col.count_documents({"genre_folder": {"$regex": r"^NeedsReview", "$options": "i"}})
        ingest = col.count_documents({"final_path":   {"$regex": r"[/\\][Ii]ngest[/\\]"}})
        genre_counts: Counter = Counter()
        for doc in col.find({}, {"genre_folder": 1, "_id": 0}):
            gf = (doc.get("genre_folder") or "unknown").replace("\\", "/")
            top = gf.split("/")[0] if "/" in gf else gf
            genre_counts[top] += 1
        return {
            "available":     True,
            "total":         total,
            "needs_review":  nr,
            "ingest_leaked": ingest,
            "by_top_folder": dict(genre_counts.most_common()),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def _count_artist_memory() -> dict:
    """Return artist_memory stats."""
    try:
        from database import get_artist_memory_collection
        col = get_artist_memory_collection()
        if col is None:
            return {"available": False}
        total  = col.count_documents({})
        by_fam: Counter = Counter()
        by_src: Counter = Counter()
        high_conf = col.count_documents({"confidence": {"$gte": 0.75}})
        for doc in col.find({}, {"family": 1, "source": 1, "_id": 0}):
            by_fam[doc.get("family", "unknown")] += 1
            by_src[doc.get("source",  "unknown")] += 1
        return {
            "available":     True,
            "total":         total,
            "high_confidence": high_conf,
            "by_family":     dict(by_fam.most_common()),
            "by_source":     dict(by_src.most_common()),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def _count_disk_state() -> dict:
    """Walk Library/ and NeedsReview/ to count files and folders."""
    lib_files = lib_folders = 0
    genre_dist: Counter = Counter()
    if LIB_DIR.is_dir():
        for p in LIB_DIR.rglob("*.mp3"):
            lib_files += 1
            # Genre = first two components after Library/ e.g. Electronic/House
            try:
                rel   = p.relative_to(LIB_DIR)
                genre = "/".join(rel.parts[:2]) if len(rel.parts) >= 3 else rel.parts[0]
                genre_dist[genre] += 1
            except ValueError:
                pass
        lib_folders = sum(1 for d in LIB_DIR.rglob("*") if d.is_dir())

    nr_files = sum(1 for _ in NR_DIR.rglob("*.mp3")) if NR_DIR.is_dir() else 0

    return {
        "library_mp3_files":    lib_files,
        "library_subfolders":   lib_folders,
        "needs_review_mp3":     nr_files,
        "canonical_genre_dist": dict(genre_dist.most_common()),
    }


def _rollback_manifests() -> list[str]:
    """List rollback manifest files in BASE_DIR."""
    return sorted(str(p.name) for p in BASE_DIR.glob("migration_rollback_*.json"))


def _artist_folder_analysis() -> dict:
    """Load most recent artist_folder_analysis.json if present."""
    candidates = sorted(BASE_DIR.glob("artist_folder_analysis.json"))
    if not candidates:
        return {"available": False}
    try:
        data = json.loads(candidates[-1].read_text(encoding="utf-8"))
        return {
            "available":    True,
            "total":        data.get("total_unmapped", 0),
            "resolved":     data.get("resolved", 0),
            "unresolved":   data.get("unresolved", 0),
            "unresolved_list": data.get("unresolved_list", []),
        }
    except Exception:
        return {"available": False}


def _retry_reports() -> list[dict]:
    """Summarise retry_unmapped_report_*.json files."""
    summaries = []
    for p in sorted(BASE_DIR.glob("retry_unmapped_report_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            summaries.append({
                "file":          p.name,
                "generated_at":  data.get("generated_at"),
                "moved":         data.get("moved", 0),
                "errors":        data.get("errors", 0),
                "still_unmapped": len(data.get("still_unmapped", [])),
            })
        except Exception:
            summaries.append({"file": p.name, "parse_error": True})
    return summaries


def _risks(idx: dict, disk: dict, mem: dict, aaf: dict) -> list[str]:
    risks = []
    if idx.get("ingest_leaked", 0):
        risks.append(f"{idx['ingest_leaked']} library_index entries still point to Ingest/ paths")
    if idx.get("needs_review", 0) and idx["needs_review"] > 20:
        risks.append(f"{idx['needs_review']} tracks still in NeedsReview — run reclassification")
    if disk.get("needs_review_mp3", 0) > 50:
        risks.append(f"{disk['needs_review_mp3']} MP3s on disk in NeedsReview/")
    if aaf.get("unresolved", 0):
        folders = aaf.get("unresolved_list", [])
        risks.append(f"{aaf['unresolved']} artist folders still unrouted: {folders[:5]}")
    if not mem.get("available"):
        risks.append("artist_memory MongoDB collection unavailable — no confidence aging")
    return risks


def generate_report(output_path: Path | None = None) -> dict:
    if output_path is None:
        output_path = BASE_DIR / "organization_rollout_report.json"

    logger.info("[org_report] Collecting library_index stats…")
    idx  = _count_library_index()
    logger.info("[org_report] Collecting artist_memory stats…")
    mem  = _count_artist_memory()
    logger.info("[org_report] Walking Library/ on disk…")
    disk = _count_disk_state()

    aaf     = _artist_folder_analysis()
    retries = _retry_reports()
    manifests = _rollback_manifests()
    risks   = _risks(idx, disk, mem, aaf)

    report = {
        "generated_at":              datetime.now(timezone.utc).isoformat(),
        "library_index":             idx,
        "artist_memory":             mem,
        "disk_state":                disk,
        "artist_folder_analysis":    aaf,
        "retry_unmapped_runs":       retries,
        "rollback_manifests":        manifests,
        "remaining_risks":           risks,
        "summary": {
            "total_indexed":         idx.get("total", 0),
            "canonical_library_files": disk.get("library_mp3_files", 0),
            "needs_review_files":    disk.get("needs_review_mp3", 0),
            "artist_memory_entries": mem.get("total", 0),
            "unresolved_folders":    aaf.get("unresolved", 0),
            "risk_count":            len(risks),
        },
    }

    try:
        output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info(f"[org_report] Written: {output_path}")
    except Exception as e:
        logger.error(f"[org_report] Write failed: {e}")

    s = report["summary"]
    logger.info(
        f"[org_report] Summary: {s['canonical_library_files']} canonical files | "
        f"{s['needs_review_files']} NeedsReview | "
        f"{s['artist_memory_entries']} artist memories | "
        f"{s['unresolved_folders']} unresolved folders | "
        f"{s['risk_count']} risk(s)"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="Generate organization rollout report")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    out = Path(args.output) if args.output else None
    report = generate_report(output_path=out)
    sys.exit(0 if report["summary"]["risk_count"] == 0 else 1)


if __name__ == "__main__":
    main()
