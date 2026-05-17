"""
Identification Accuracy Audit
==============================
Large-scale dry-run accuracy measurement for the legacy identification
matcher after improvements. Processes >= min_files MP3s across mixed
genres and writes reports/identification_accuracy_report.json.

Usage
-----
    python audit_identification_accuracy.py
    python audit_identification_accuracy.py --min-files 300
    python audit_identification_accuracy.py --base-dir /path/to/library
    python audit_identification_accuracy.py --seed 7 --min-files 500

NO writes. NO retagging. NO rerouting.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from loguru import logger
    logger.remove()
    logger.add(
        sys.stderr,
        format="{time:HH:mm:ss} | {level:<7} | {message}",
        level="INFO",
    )
except ImportError:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    logger = logging.getLogger(__name__)

from config import config

_REPORTS_DIR = _BACKEND / "reports"
BASE_DIR = Path(config.BASE_DOWNLOAD_DIR)

# ── Folder-type classification ────────────────────────────────────────────────

_REMIX_KW     = frozenset({"remix", "vip", "edit", "bootleg", "flip", "rework", "dub"})
_ELECTRONIC_KW = frozenset({
    "electronic", "dnb", "drum", "bass", "techno", "house",
    "trance", "jungle", "garage", "dubstep", "ambient", "breaks",
})
_INDIAN_KW    = frozenset({"indian", "bollywood", "hindi", "punjabi", "tamil",
                            "telugu", "bhangra", "desi", "filmi"})
_LIVE_KW      = frozenset({"live", "session", "extended", "acoustic", "instrumental"})

_WORD_RE = re.compile(r"\b([a-z]+)\b")


def _path_words(fp: Path) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(str(fp).lower()))


def _folder_type(fp: Path) -> str:
    words = _path_words(fp)
    if words & _INDIAN_KW:
        return "indian"
    if words & _REMIX_KW:
        return "remix"
    if words & _ELECTRONIC_KW:
        return "electronic"
    if words & _LIVE_KW:
        return "live"
    return "general"


def _has_remix_tokens(fp: Path) -> bool:
    return bool(frozenset(_WORD_RE.findall(fp.stem.lower())) & _REMIX_KW)


def _has_live_tokens(fp: Path) -> bool:
    return bool(frozenset(_WORD_RE.findall(fp.stem.lower())) & _LIVE_KW)


# ── Path helpers ──────────────────────────────────────────────────────────────

def _artist_from_path(fp: Path, base: Path) -> str:
    try:
        parts = fp.relative_to(base).parts
        if len(parts) >= 3:
            return parts[-2]
        stem = fp.stem
        if " - " in stem:
            return stem.split(" - ", 1)[0].strip()
        return parts[-2] if len(parts) >= 2 else "Unknown"
    except Exception:
        return "Unknown"


def _genre_from_path(fp: Path, base: Path) -> str:
    try:
        return fp.relative_to(base).parts[0]
    except Exception:
        return "Unknown"


# ── Sampling ──────────────────────────────────────────────────────────────────

def _sample_files(base_dir: Path, min_files: int, seed: int = 42) -> list[Path]:
    """
    Collect eligible MP3s and return >= min_files covering remix, electronic,
    indian, live, and general folder types proportionally.
    """
    from library_scanner import scan_library

    buckets: dict[str, list[Path]] = defaultdict(list)
    for fp in scan_library(base_dir):
        buckets[_folder_type(fp)].append(fp)

    total_eligible = sum(len(v) for v in buckets.values())
    logger.info(f"[audit] Total eligible MP3s: {total_eligible}")
    for t in sorted(buckets):
        logger.info(f"[audit]   {t:<12}: {len(buckets[t])} files")

    if total_eligible <= min_files:
        all_files = [fp for files in buckets.values() for fp in files]
        random.Random(seed).shuffle(all_files)
        return all_files

    rng = random.Random(seed)

    # Guaranteed minimums per bucket; fill remainder from general
    slots: dict[str, int] = {
        "remix":      min(len(buckets.get("remix", [])),      max(30, min_files // 7)),
        "electronic": min(len(buckets.get("electronic", [])), max(40, min_files // 5)),
        "indian":     min(len(buckets.get("indian", [])),     max(30, min_files // 7)),
        "live":       min(len(buckets.get("live", [])),       max(20, min_files // 10)),
    }
    used = sum(slots.values())
    slots["general"] = min(
        len(buckets.get("general", [])),
        max(min_files - used, min_files // 4),
    )

    selected: list[Path] = []
    selected_set: set[Path] = set()
    for t, n in slots.items():
        pool = buckets.get(t, [])
        drawn = rng.sample(pool, min(n, len(pool)))
        for fp in drawn:
            if fp not in selected_set:
                selected.append(fp)
                selected_set.add(fp)

    # Top-up to hit min_files if still short
    shortage = max(0, min_files - len(selected))
    if shortage:
        remaining = [fp for files in buckets.values() for fp in files
                     if fp not in selected_set]
        rng.shuffle(remaining)
        for fp in remaining[:shortage]:
            selected.append(fp)
            selected_set.add(fp)

    rng.shuffle(selected)
    logger.info(
        f"[audit] Sampling breakdown: "
        + ", ".join(f"{t}={slots.get(t, 0)}" for t in slots)
        + f" → total={len(selected)}"
    )
    return selected


# ── Core audit ────────────────────────────────────────────────────────────────

def _record(fp: Path, r, base: Path) -> dict:
    return {
        "filepath":   str(fp.relative_to(base)),
        "title":      r.title,
        "artist":     r.artist,
        "spotify_id": r.spotify_id,
        "confidence": round(r.confidence, 4),
        "reason":     r.confidence_reason,
        "source":     r.match_source,
        "delta_sec":  r.duration_delta_sec,
    }


def run_audit(base_dir: Path, min_files: int = 200, seed: int = 42) -> dict:
    from services.legacy_identification_service import (
        identify_file,
        CONF_AUTO_ACCEPT,
        CONF_ACCEPT_WARN,
        CONF_NEEDS_REVIEW,
    )

    files = _sample_files(base_dir, min_files, seed=seed)
    total = len(files)
    logger.info(f"[audit] Processing {total} files …")

    results: list[tuple[Path, object]] = []
    errors = 0
    for i, fp in enumerate(files, 1):
        try:
            r = identify_file(fp)
            results.append((fp, r))
            band = (
                "AUTO_ACCEPT"  if r.confidence >= CONF_AUTO_ACCEPT  else
                "ACCEPT_WARN"  if r.confidence >= CONF_ACCEPT_WARN  else
                "NEEDS_REVIEW" if r.confidence >= CONF_NEEDS_REVIEW else
                "UNIDENTIFIED"
            )
            logger.info(
                f"  [{i}/{total}] {fp.name[:55]:<55} "
                f"{band:<12} conf={r.confidence:.2f}"
            )
        except Exception as e:
            logger.error(f"  [{i}/{total}] ERROR {fp.name}: {e}")
            errors += 1

    # ── Band counts ───────────────────────────────────────────────────────────
    auto_accept  = [(fp, r) for fp, r in results
                    if r.matched and r.confidence >= CONF_AUTO_ACCEPT]
    accept_warn  = [(fp, r) for fp, r in results
                    if r.matched and CONF_ACCEPT_WARN <= r.confidence < CONF_AUTO_ACCEPT]
    needs_review = [(fp, r) for fp, r in results
                    if r.matched and CONF_NEEDS_REVIEW <= r.confidence < CONF_ACCEPT_WARN]
    unidentified = [(fp, r) for fp, r in results
                    if not r.matched or r.confidence < CONF_NEEDS_REVIEW]
    identified   = auto_accept + accept_warn

    confidences   = [r.confidence for _, r in results]
    avg_conf      = sum(confidences) / max(len(confidences), 1)

    # ── Remix / live subsets ──────────────────────────────────────────────────
    remix_files   = [(fp, r) for fp, r in results if _has_remix_tokens(fp)]
    remix_matched = [(fp, r) for fp, r in remix_files
                     if r.matched and r.confidence >= CONF_NEEDS_REVIEW]

    live_files    = [(fp, r) for fp, r in results if _has_live_tokens(fp)]
    live_matched  = [(fp, r) for fp, r in live_files
                     if r.matched and r.confidence >= CONF_NEEDS_REVIEW]

    # ── Top failures by artist / genre folder ─────────────────────────────────
    artist_fails: dict[str, int] = defaultdict(int)
    genre_fails:  dict[str, int] = defaultdict(int)
    for fp, _ in unidentified:
        artist_fails[_artist_from_path(fp, base_dir)] += 1
        genre_fails[_genre_from_path(fp, base_dir)]   += 1

    top_artists = sorted(artist_fails.items(), key=lambda x: x[1], reverse=True)[:15]
    top_genres  = sorted(genre_fails.items(),  key=lambda x: x[1], reverse=True)[:10]

    # ── Sampled examples ──────────────────────────────────────────────────────
    rng = random.Random(seed + 1)

    def _sample(pairs, n=10):
        draw = rng.sample(pairs, min(n, len(pairs)))
        return [_record(fp, r, base_dir) for fp, r in draw]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_dir":     str(base_dir),
        "thresholds": {
            "auto_accept":  CONF_AUTO_ACCEPT,
            "accept_warn":  CONF_ACCEPT_WARN,
            "needs_review": CONF_NEEDS_REVIEW,
        },
        "summary": {
            "total_scanned":        total,
            "errors":               errors,
            "auto_accept":          len(auto_accept),
            "accept_warn":          len(accept_warn),
            "needs_review":         len(needs_review),
            "unidentified":         len(unidentified),
            "id_rate_pct":          round(len(identified) / max(total, 1) * 100, 1),
            "avg_confidence":       round(avg_conf, 4),
            "remix_files_scanned":  len(remix_files),
            "remix_match_rate_pct": round(
                len(remix_matched) / max(len(remix_files), 1) * 100, 1
            ),
            "live_files_scanned":   len(live_files),
            "live_match_rate_pct":  round(
                len(live_matched) / max(len(live_files), 1) * 100, 1
            ),
        },
        "top_unmatched_artists": [
            {"artist": a, "count": c} for a, c in top_artists
        ],
        "top_unmatched_genres": [
            {"genre": g, "count": c} for g, c in top_genres
        ],
        "sampled_matched":   _sample(identified),
        "sampled_unmatched": _sample(unidentified),
    }

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run accuracy audit for the legacy identification matcher. "
            "Reads files only — no tags written, no files moved."
        )
    )
    parser.add_argument(
        "--min-files", type=int, default=200, metavar="N",
        help="Minimum number of files to process (default: 200)",
    )
    parser.add_argument(
        "--base-dir", type=str, default=None, metavar="DIR",
        help="Override library base directory",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducible file sampling (default: 42)",
    )
    args = parser.parse_args()

    base = Path(args.base_dir) if args.base_dir else BASE_DIR
    if not base.is_dir():
        logger.error(f"Base directory not found: {base}")
        sys.exit(1)

    report = run_audit(base, min_files=args.min_files, seed=args.seed)

    out = _REPORTS_DIR / "identification_accuracy_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    s = report["summary"]
    logger.info(
        f"\n[audit] ── RESULTS ──────────────────────────────────────\n"
        f"  Total scanned     : {s['total_scanned']}  (errors={s['errors']})\n"
        f"  AUTO_ACCEPT       : {s['auto_accept']}\n"
        f"  ACCEPT_WARN       : {s['accept_warn']}\n"
        f"  NEEDS_REVIEW      : {s['needs_review']}\n"
        f"  UNIDENTIFIED      : {s['unidentified']}\n"
        f"  ID Rate           : {s['id_rate_pct']}%\n"
        f"  Avg Confidence    : {s['avg_confidence']}\n"
        f"  Remix files       : {s['remix_files_scanned']}  "
        f"→ matched {s['remix_match_rate_pct']}%\n"
        f"  Live/Edit files   : {s['live_files_scanned']}  "
        f"→ matched {s['live_match_rate_pct']}%\n"
        f"  Report → {out}"
    )

    top_a = report["top_unmatched_artists"][:5]
    if top_a:
        logger.info("  Top unmatched artists: " +
                    ", ".join(f"{x['artist']}({x['count']})" for x in top_a))

    sys.exit(0)


if __name__ == "__main__":
    main()
