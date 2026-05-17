"""
Pre-Migration Sanitizer
========================
Classifies every MP3 in the DJ library and detects issues that MUST be
resolved before retag_migration.py runs at scale.

Phases
------
Phase 1 — Sanitation Scanner
    Walks the full library (including Ingest/) and tags each file:
      canonical       — authoritative copy to keep
      duplicate       — another copy of an already-seen track
      temp            — *.trimmed.mp3 | *_N.mp3 | *_temp*.mp3
      ingest_leftover — Ingest/ (excl. Staging/) older than threshold
      ingest_active   — Ingest/ (excl. Staging/) within threshold
      staging         — Ingest/Staging/ (active pipeline)
      needs_review    — NeedsReview/ folder
      quarantine      — Quarantine/ (already handled, skip)

Phase 2 — Duplicate Detection (three-tier, first-match wins per file)
    T1  TXXX:SPOTIFY_ID equality          confidence 1.00
    T2  sha256(first 2 MB) equality       confidence 0.99
    T3  content_hash(title,artist,dur)    confidence 0.80

Phase 3 — Ingest Quarantine
    Reports ingest leftovers; moves them with --quarantine-ingest --execute.

Phase 4 — Temp File Detection
    Regex-based; moves them with --quarantine-temp --execute.

Phase 5 — Canonical Resolution
    Scores each file in a duplicate group; highest score = canonical.
    +10 spotify_id, +8 bpm+key, +6 artwork, +4 camelot,
    +3 bitrate factor, -5 temp, -3 ingest, -1 NeedsReview.

Phase 6 — Safe Action Modes
    --dry-run (always default unless --execute is set)
    --export-only         write JSON only, no moves
    --quarantine-temp     move temp files → Quarantine/TempFiles/
    --quarantine-ingest   move ingest leftovers → Quarantine/IngestLeftovers/
    --exclude-duplicates  write migration_excludes.json

Phase 7 — Verification
    Checks four pre-migration safety conditions.
    Writes sanitize_verification.json.

Usage
-----
    python pre_migration_sanitize.py --dry-run
    python pre_migration_sanitize.py --export-only
    python pre_migration_sanitize.py --quarantine-temp --quarantine-ingest --execute
    python pre_migration_sanitize.py --exclude-duplicates

Output
------
    backend/sanitize_report.json
    backend/sanitize_verification.json
    backend/migration_excludes.json    (with --exclude-duplicates)

Exit codes: 0 = pre-migration safe, 1 = issues found, 2 = action errors
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from collections import defaultdict
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
QUARANTINE_DIR = BASE_DIR / "Quarantine"
STAGING_REL    = str(Path("Ingest") / "Staging")   # relative prefix to detect staging

DEFAULT_OUTPUT       = _BACKEND / "sanitize_report.json"
VERIFICATION_OUTPUT  = _BACKEND / "sanitize_verification.json"
EXCLUDES_OUTPUT      = _BACKEND / "migration_excludes.json"

INGEST_AGE_DAYS_DEFAULT = 1

# ── Temp-file patterns (filename only, case-insensitive) ──────────────────────
# Priority: highest-confidence patterns first.
_TEMP_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("trimmed",  re.compile(r"\.trimmed\.mp3$",   re.IGNORECASE)),
    ("copy",     re.compile(r"\s*\(copy\).*\.mp3$", re.IGNORECASE)),
    ("part",     re.compile(r"_part\d*\.mp3$",    re.IGNORECASE)),
    ("temp",     re.compile(r"_temp[^.]*\.mp3$",  re.IGNORECASE)),
    ("collision",re.compile(r"_\d{1,2}\.mp3$",   re.IGNORECASE)),  # _1.mp3 … _99.mp3
]

# Folders that are never genre folders (pipeline infrastructure)
_PIPELINE_TOPS = {"Ingest", "NeedsReview", "Quarantine", "Manual"}


# ── ID3 + audio-info reader ───────────────────────────────────────────────────

def _read_tags(filepath: str) -> dict:
    """Read ID3 tags and audio info. Always returns a dict; sets 'error' on failure."""
    result: dict = {
        "title": "", "artist": "", "album": "",
        "bpm": "", "key": "", "camelot": "", "spotify_id": "",
        "has_apic": False, "bitrate_kbps": 128,
        "duration_sec": 0.0, "duration_ms": 0,
        "error": "",
    }
    try:
        from mutagen.mp3 import MP3
        audio = MP3(filepath)
        result["duration_sec"] = float(audio.info.length)
        result["duration_ms"]  = int(audio.info.length * 1000)
        result["bitrate_kbps"] = int(audio.info.bitrate / 1000)

        if audio.tags:
            def _t(k: str) -> str:
                f = audio.tags.get(k)
                return str(f.text[0]).strip() if f and f.text else ""

            def _txxx(desc: str) -> str:
                f = audio.tags.get(f"TXXX:{desc}")
                return str(f.text[0]).strip() if f and f.text else ""

            result.update({
                "title":      _t("TIT2"),
                "artist":     _t("TPE1"),
                "album":      _t("TALB"),
                "bpm":        _t("TBPM"),
                "key":        _t("TKEY"),
                "camelot":    _txxx("INITIALKEY") or _txxx("CAMELOT"),
                "spotify_id": _txxx("SPOTIFY_ID"),
                "has_apic":   bool(audio.tags.get("APIC:")),
            })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _sha256_partial(filepath: str, limit: int = 2 * 1024 * 1024) -> str:
    """sha256 of first *limit* bytes — audio-stable even when tags change."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as fh:
            h.update(fh.read(limit))
        return h.hexdigest()
    except Exception:
        return ""


def _content_hash(title: str, artist: str, duration_ms: int) -> str:
    """Normalized title+artist+5s-bucket hash (mirrors dedup_service logic)."""
    def _norm(t: str) -> str:
        t = unicodedata.normalize("NFKD", t)
        t = "".join(c for c in t if not unicodedata.combining(c))
        t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", t)
        return " ".join(t.lower().split()).strip()

    bucket = duration_ms // 5000 if duration_ms > 0 else 0
    raw = _norm(title) + "||" + _norm(artist) + "||" + str(bucket)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ── Filename classification helpers ───────────────────────────────────────────

def _temp_kind(filename: str) -> str:
    """Return the temp pattern label if matched, else ''."""
    for label, pattern in _TEMP_PATTERNS:
        if pattern.search(filename):
            return label
    return ""


def _canonical_score(tags: dict, is_temp: bool, is_ingest: bool,
                     is_needs_review: bool) -> float:
    """Higher score → better canonical candidate."""
    score = 0.0
    if tags.get("spotify_id"):               score += 10.0
    if tags.get("bpm") and tags.get("key"):  score += 8.0
    elif tags.get("bpm") or tags.get("key"): score += 4.0
    if tags.get("has_apic"):                 score += 6.0
    if tags.get("camelot"):                  score += 4.0
    score += min(tags.get("bitrate_kbps", 0) / 320.0, 1.0) * 3.0
    if is_temp:         score -= 5.0
    if is_ingest:       score -= 3.0
    if is_needs_review: score -= 1.0
    return score


# ── Filesystem scanner ────────────────────────────────────────────────────────

def _scan_all(base_dir: Path) -> list[dict]:
    """Walk full BASE_DIR (including Ingest/), skip Quarantine and hidden dirs."""
    results: list[dict] = []
    skip = {"Quarantine", ".retry_queue"}
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for fname in files:
            if not fname.lower().endswith(".mp3"):
                continue
            fp = os.path.join(root, fname)
            rel = str(Path(fp).relative_to(base_dir))
            top = Path(rel).parts[0] if Path(rel).parts else ""
            results.append({
                "path":     fp,
                "rel":      rel,
                "filename": fname,
                "size":     os.path.getsize(fp),
                "mtime":    os.path.getmtime(fp),
                "top_dir":  top,
            })
    return results


# ── Enrichment + initial classification ──────────────────────────────────────

def _enrich(records: list[dict], ingest_age_days: int) -> list[dict]:
    """
    Read tags, compute hashes, classify each record.
    Mutates and returns records (adds fields in-place).
    """
    ingest_cutoff = time.time() - ingest_age_days * 86400
    total = len(records)

    for i, rec in enumerate(records):
        fp   = rec["path"]
        top  = rec["top_dir"]
        rel  = rec["rel"]
        fname = rec["filename"]

        # Location flags
        is_staging       = rel.startswith(STAGING_REL)
        is_ingest        = (top == "Ingest") and not is_staging
        is_needs_review  = top == "NeedsReview"
        is_quarantine    = top == "Quarantine"
        is_ingest_leftover = is_ingest and rec["mtime"] < ingest_cutoff

        tk = _temp_kind(fname)
        is_temp = bool(tk)

        tags    = _read_tags(fp)
        sha256  = _sha256_partial(fp)
        has_meta = not tags.get("error") and (tags["title"] or tags["artist"])
        ch = _content_hash(tags["title"], tags["artist"], tags["duration_ms"]) if has_meta else ""

        score = _canonical_score(tags, is_temp, is_ingest, is_needs_review)

        # Initial classification (dedup pass may promote to "duplicate")
        if is_quarantine:
            cls = "quarantine"
        elif is_staging:
            cls = "staging"
        elif is_temp:
            cls = "temp"
        elif is_ingest_leftover:
            cls = "ingest_leftover"
        elif is_ingest:
            cls = "ingest_active"
        elif is_needs_review:
            cls = "needs_review"
        else:
            cls = "canonical"   # tentative — dedup may demote

        rec.update({
            "tags":              tags,
            "sha256_partial":    sha256,
            "content_hash":      ch,
            "is_temp":           is_temp,
            "temp_kind":         tk,
            "is_ingest":         is_ingest,
            "is_staging":        is_staging,
            "is_needs_review":   is_needs_review,
            "is_quarantine":     is_quarantine,
            "is_ingest_leftover":is_ingest_leftover,
            "classification":    cls,
            "canonical_score":   score,
            "duplicate_group":   "",
            "is_canonical":      True,   # revised by _resolve_canonicals
        })

        if (i + 1) % 100 == 0:
            logger.info(f"[sanitize] Enriched {i+1}/{total}…")

    return records


# ── Duplicate detection (Phases 2 + 5) ───────────────────────────────────────

def _detect_duplicate_groups(records: list[dict]) -> list[dict]:
    """
    Group files that are duplicates of each other (three-tier).
    Updates records in-place (sets duplicate_group).
    Returns list of group dicts.
    """
    # Only consider real music (skip staging / quarantine)
    candidates = [r for r in records
                  if r["classification"] not in ("staging", "quarantine")]

    assigned: set[str] = set()
    groups: list[dict] = []
    gid_counter = [0]

    def _next_gid() -> str:
        gid_counter[0] += 1
        return f"g{gid_counter[0]:04d}"

    def _make_group(members: list[dict], tier: str, confidence: float) -> dict:
        gid = _next_gid()
        for m in members:
            m["duplicate_group"] = gid
            assigned.add(m["path"])
        return {"group_id": gid, "tier": tier, "confidence": confidence,
                "files": members, "canonical_path": "", "duplicate_paths": []}

    # T1 — Spotify ID
    by_sid: dict[str, list] = defaultdict(list)
    for r in candidates:
        sid = (r["tags"].get("spotify_id") or "").strip()
        if sid:
            by_sid[sid].append(r)
    for members in by_sid.values():
        unasgn = [m for m in members if m["path"] not in assigned]
        if len(unasgn) > 1:
            groups.append(_make_group(unasgn, "spotify_id", 1.00))

    # T2 — SHA256 of raw audio
    by_sha: dict[str, list] = defaultdict(list)
    for r in candidates:
        if r["path"] in assigned:
            continue
        sha = r.get("sha256_partial", "")
        if sha:
            by_sha[sha].append(r)
    for members in by_sha.values():
        unasgn = [m for m in members if m["path"] not in assigned]
        if len(unasgn) > 1:
            groups.append(_make_group(unasgn, "sha256_audio", 0.99))

    # T3 — Normalized metadata hash
    by_ch: dict[str, list] = defaultdict(list)
    for r in candidates:
        if r["path"] in assigned:
            continue
        ch = r.get("content_hash", "")
        if ch:
            by_ch[ch].append(r)
    for members in by_ch.values():
        unasgn = [m for m in members if m["path"] not in assigned]
        if len(unasgn) > 1:
            groups.append(_make_group(unasgn, "content_hash", 0.80))

    return groups


def _resolve_canonicals(groups: list[dict]) -> None:
    """
    Within each duplicate group, mark the best file as canonical.
    Demotes others to classification="duplicate".
    Mutates both groups and their member records.
    """
    for g in groups:
        files = g["files"]
        if not files:
            continue
        # Primary: canonical_score desc; tiebreak: mtime desc (newer)
        ranked = sorted(files,
                        key=lambda r: (r["canonical_score"], r["mtime"]),
                        reverse=True)
        canonical = ranked[0]
        canonical["is_canonical"] = True
        g["canonical_path"]   = canonical["path"]
        g["duplicate_paths"]  = [r["path"] for r in ranked[1:]]
        for r in ranked[1:]:
            r["is_canonical"]    = False
            r["classification"]  = "duplicate"


# ── Report builder ────────────────────────────────────────────────────────────

def _slim(r: dict) -> dict:
    """Compact, JSON-safe summary of one file record."""
    return {
        "path":            r["path"],
        "rel":             r["rel"],
        "filename":        r["filename"],
        "size":            r["size"],
        "mtime":           r["mtime"],
        "classification":  r["classification"],
        "is_canonical":    r["is_canonical"],
        "canonical_score": round(r["canonical_score"], 2),
        "duplicate_group": r.get("duplicate_group", ""),
        "is_temp":         r["is_temp"],
        "temp_kind":       r.get("temp_kind", ""),
        "is_ingest_leftover": r.get("is_ingest_leftover", False),
        "spotify_id":      r["tags"].get("spotify_id", ""),
        "title":           r["tags"].get("title", ""),
        "artist":          r["tags"].get("artist", ""),
        "bpm":             r["tags"].get("bpm", ""),
        "has_artwork":     r["tags"].get("has_apic", False),
        "bitrate_kbps":    r["tags"].get("bitrate_kbps", 0),
        "duration_sec":    round(r["tags"].get("duration_sec", 0.0), 1),
    }


def _build_report(records: list[dict], groups: list[dict],
                  base_dir: Path, ingest_age_days: int) -> dict:
    by_class: dict[str, list[str]] = defaultdict(list)
    for r in records:
        by_class[r["classification"]].append(r["path"])

    # Paths to exclude from migration: non-canonical duplicates + temp files
    exclude_paths = sorted({
        r["path"] for r in records
        if (r["classification"] == "duplicate" and not r["is_canonical"])
        or r["classification"] == "temp"
    })

    group_out = [
        {
            "group_id":       g["group_id"],
            "tier":           g["tier"],
            "confidence":     g["confidence"],
            "canonical_path": g["canonical_path"],
            "duplicate_paths":g["duplicate_paths"],
            "files":          [_slim(f) for f in g["files"]],
        }
        for g in groups
    ]

    counts = {k: len(v) for k, v in by_class.items()}
    return {
        "generated_at":            datetime.now(timezone.utc).isoformat(),
        "base_dir":                str(base_dir),
        "ingest_age_threshold_days": ingest_age_days,
        "summary": {
            "total_scanned":    len(records),
            "canonical":        counts.get("canonical", 0),
            "duplicate":        counts.get("duplicate", 0),
            "temp":             counts.get("temp", 0),
            "ingest_leftover":  counts.get("ingest_leftover", 0),
            "ingest_active":    counts.get("ingest_active", 0),
            "staging":          counts.get("staging", 0),
            "needs_review":     counts.get("needs_review", 0),
            "quarantine":       counts.get("quarantine", 0),
            "duplicate_groups": len(groups),
            "exclude_count":    len(exclude_paths),
        },
        "duplicate_groups":  group_out,
        "files_by_class":    {cls: sorted(paths) for cls, paths in by_class.items()},
        "exclude_paths":     exclude_paths,
        "all_files":         [_slim(r) for r in records],
    }


# ── Phase 7 — Verification ────────────────────────────────────────────────────

def _verify_report(report: dict) -> dict:
    """
    Evaluate four pre-migration safety conditions.
    Returns a verification dict; also written to sanitize_verification.json.
    """
    s      = report.get("summary", {})
    groups = report.get("duplicate_groups", [])
    issues: list[str] = []

    # 1. No ingest leftovers
    n_ingest = s.get("ingest_leftover", 0)
    ok_ingest = n_ingest == 0
    if not ok_ingest:
        issues.append(
            f"{n_ingest} ingest leftover file(s) — run --quarantine-ingest "
            "or migrate them before retag_migration"
        )

    # 2. No temp files anywhere in the library
    n_temp = s.get("temp", 0)
    ok_temp = n_temp == 0
    if not ok_temp:
        issues.append(
            f"{n_temp} temp file(s) detected — run --quarantine-temp "
            "to remove them from the migration path"
        )

    # 3. No duplicate canonical collisions (same track, multiple keepers)
    collision_groups = [
        g for g in groups
        if g["confidence"] >= 0.80 and g["duplicate_paths"]
    ]
    n_collisions = len(collision_groups)
    ok_collision = n_collisions == 0
    if not ok_collision:
        issues.append(
            f"{n_collisions} duplicate group(s) with ≥0.80 confidence — "
            "resolve before migration to prevent library_index identity conflicts"
        )

    # 4. No Spotify ID collisions (same ID in multiple folder locations)
    sid_groups = [g for g in groups if g["tier"] == "spotify_id"]
    n_sid = len(sid_groups)
    ok_sid = n_sid == 0
    if not ok_sid:
        issues.append(
            f"{n_sid} spotify_id collision(s) — same track ID in multiple "
            "folders will produce duplicate library_index entries"
        )

    return {
        "generated_at":                   datetime.now(timezone.utc).isoformat(),
        "pre_migration_safe":             len(issues) == 0,
        "no_ingest_leftovers":            ok_ingest,
        "no_temp_in_library":             ok_temp,
        "no_duplicate_canonical_collision": ok_collision,
        "no_spotify_id_collision":        ok_sid,
        "ingest_leftover_count":          n_ingest,
        "temp_count":                     n_temp,
        "collision_group_count":          n_collisions,
        "spotify_id_collision_count":     n_sid,
        "issues":                         issues,
    }


# ── Quarantine action ─────────────────────────────────────────────────────────

def _quarantine_files(
    paths: list[str],
    subdir: str,
    base_dir: Path,
    dry_run: bool,
) -> tuple[int, list[str]]:
    """
    Move *paths* to QUARANTINE_DIR/subdir/YYYYMMDD/, preserving relative structure.
    Never overwrites (adds _qN suffix).
    Returns (moved_count, error_list).
    """
    stamp     = datetime.now().strftime("%Y%m%d")
    dest_root = QUARANTINE_DIR / subdir / stamp
    moved, errors = 0, []

    for src_str in paths:
        src = Path(src_str)
        if not src.is_file():
            continue
        try:
            try:
                rel = src.relative_to(base_dir)
            except ValueError:
                rel = Path(src.name)
            dest = dest_root / rel
            if dry_run:
                logger.info(f"  [DRY] quarantine: {rel} → {subdir}/{stamp}/{rel}")
                moved += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}_q{moved}{dest.suffix}")
            shutil.move(str(src), str(dest))
            logger.info(f"  [quarantine] {rel} → {subdir}/{stamp}/{rel}")
            moved += 1
        except Exception as exc:
            errors.append(f"{src}: {exc}")
            logger.warning(f"  [quarantine-fail] {src}: {exc}")

    return moved, errors


# ── Console summary ───────────────────────────────────────────────────────────

def _print_summary(report: dict) -> None:
    s = report["summary"]
    v = report.get("verification", {})
    safe  = v.get("pre_migration_safe", False)
    green = "\033[92m"
    red   = "\033[91m"
    yel   = "\033[93m"
    rst   = "\033[0m"
    color = green if safe else red

    print(f"\n{'='*64}")
    print(f"  Pre-Migration Sanitization Report")
    print(f"  Status: {color}{'PRE-MIGRATION SAFE' if safe else 'ISSUES FOUND — DO NOT MIGRATE YET'}{rst}")
    print(f"{'='*64}")
    rows = [
        ("Total scanned",    s["total_scanned"]),
        ("Canonical",        s["canonical"]),
        ("Duplicate",        f"{s['duplicate']}  ({s['duplicate_groups']} groups, "
                             f"{s['exclude_count']} excluded from migration)"),
        ("Temp files",       s["temp"]),
        ("Ingest leftovers", s["ingest_leftover"]),
        ("Ingest active",    s["ingest_active"]),
        ("Staging",          s["staging"]),
        ("NeedsReview",      s["needs_review"]),
    ]
    for label, val in rows:
        print(f"  {label:<22} {val}")

    if v:
        print(f"\n  Verification checks:")
        checks = [
            ("no_ingest_leftovers",              "No ingest leftovers"),
            ("no_temp_in_library",               "No temp files in library"),
            ("no_duplicate_canonical_collision",  "No duplicate canonical collision"),
            ("no_spotify_id_collision",           "No Spotify ID collision"),
        ]
        for key, label in checks:
            ok   = v.get(key, False)
            icon = f"{green}✅{rst}" if ok else f"{red}❌{rst}"
            print(f"    {icon}  {label}")
        for issue in v.get("issues", []):
            print(f"       {yel}→{rst} {issue}")
    print()


# ── Main entry point ──────────────────────────────────────────────────────────

def sanitize(
    base_dir: Path = BASE_DIR,
    ingest_age_days: int = INGEST_AGE_DAYS_DEFAULT,
    dry_run: bool = True,
    export_only: bool = False,
    do_quarantine_temp: bool = False,
    do_quarantine_ingest: bool = False,
    exclude_duplicates: bool = False,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict:
    """
    Run the full pre-migration sanitation scan.
    Returns the full report dict and writes output files.
    """
    logger.info(
        f"[sanitize] Starting scan of {base_dir} "
        f"(ingest_age={ingest_age_days}d, dry_run={dry_run})"
    )

    # Phase 1: scan filesystem
    raw = _scan_all(base_dir)
    logger.info(f"[sanitize] Found {len(raw)} MP3 files to analyse")

    # Phase 1+4: enrich (read tags, compute hashes, initial classify)
    logger.info("[sanitize] Reading tags and computing hashes…")
    records = _enrich(raw, ingest_age_days)

    # Phase 2: detect duplicate groups
    logger.info("[sanitize] Detecting duplicate groups…")
    groups = _detect_duplicate_groups(records)
    logger.info(f"[sanitize] {len(groups)} duplicate group(s) found")

    # Phase 5: canonical resolution
    _resolve_canonicals(groups)

    # Build report + Phase 7 verification
    report       = _build_report(records, groups, base_dir, ingest_age_days)
    verification = _verify_report(report)
    report["verification"] = verification

    _print_summary(report)

    # Write sanitize_report.json
    output_path.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    logger.info(f"[sanitize] Report written → {output_path}")

    # Write sanitize_verification.json
    VERIFICATION_OUTPUT.write_text(
        json.dumps(verification, indent=2, default=str), encoding="utf-8"
    )
    logger.info(f"[sanitize] Verification written → {VERIFICATION_OUTPUT}")

    # Phase 6: write exclude list (output artifact — always written when requested,
    # even in export_only mode, because it is not a destructive file move)
    if exclude_duplicates:
        excludes = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "exclude_paths": report["exclude_paths"],
            "count": len(report["exclude_paths"]),
            "reason": "duplicate or temp — excluded from retag_migration.py",
        }
        EXCLUDES_OUTPUT.write_text(
            json.dumps(excludes, indent=2, default=str), encoding="utf-8"
        )
        logger.info(
            f"[sanitize] Exclude list → {EXCLUDES_OUTPUT} "
            f"({len(report['exclude_paths'])} paths)"
        )

    if export_only:
        return report

    action_errors: list[str] = []

    # Phase 3: quarantine ingest leftovers
    if do_quarantine_ingest:
        paths = [r["path"] for r in records if r["is_ingest_leftover"]]
        logger.info(f"[sanitize] Quarantining {len(paths)} ingest leftover(s)…")
        moved, errs = _quarantine_files(paths, "IngestLeftovers", base_dir, dry_run)
        logger.info(f"[sanitize] Ingest quarantine: {moved} file(s) moved, {len(errs)} error(s)")
        action_errors.extend(errs)

    # Phase 4: quarantine temp files
    if do_quarantine_temp:
        paths = [r["path"] for r in records if r["is_temp"]]
        logger.info(f"[sanitize] Quarantining {len(paths)} temp file(s)…")
        moved, errs = _quarantine_files(paths, "TempFiles", base_dir, dry_run)
        logger.info(f"[sanitize] Temp quarantine: {moved} file(s) moved, {len(errs)} error(s)")
        action_errors.extend(errs)

    if action_errors:
        logger.error(f"[sanitize] {len(action_errors)} action error(s) — check log above")

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-migration library sanitizer — classify, deduplicate, quarantine"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Explicit dry-run label (implied unless --execute is set)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually perform file moves (default is always dry-run)",
    )
    parser.add_argument(
        "--export-only", action="store_true",
        help="Write JSON report and exit — skip all file-move actions",
    )
    parser.add_argument(
        "--quarantine-temp", action="store_true",
        help="Move temp files → Quarantine/TempFiles/",
    )
    parser.add_argument(
        "--quarantine-ingest", action="store_true",
        help="Move ingest leftovers → Quarantine/IngestLeftovers/",
    )
    parser.add_argument(
        "--exclude-duplicates", action="store_true",
        help="Write migration_excludes.json with paths to skip in retag_migration.py",
    )
    parser.add_argument(
        "--ingest-age-days", type=int, default=INGEST_AGE_DAYS_DEFAULT,
        metavar="N",
        help=f"Ingest files older than N days are 'leftovers' (default {INGEST_AGE_DAYS_DEFAULT})",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT), metavar="FILE",
        help="Path for sanitize_report.json",
    )
    args = parser.parse_args()

    # dry_run is True unless --execute is explicitly passed
    dry_run = not args.execute

    report = sanitize(
        base_dir=BASE_DIR,
        ingest_age_days=args.ingest_age_days,
        dry_run=dry_run,
        export_only=args.export_only,
        do_quarantine_temp=args.quarantine_temp,
        do_quarantine_ingest=args.quarantine_ingest,
        exclude_duplicates=args.exclude_duplicates,
        output_path=Path(args.output),
    )

    v = report.get("verification", {})
    if v.get("pre_migration_safe"):
        logger.info("[sanitize] ✅ Library is pre-migration safe — retag_migration.py can proceed")
        sys.exit(0)
    else:
        n = len(v.get("issues", []))
        logger.warning(
            f"[sanitize] ❌ {n} pre-migration issue(s) found — "
            "resolve before running retag_migration.py"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
