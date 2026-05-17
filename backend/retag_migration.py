"""
Retag Migration Script
======================
Walks the existing DJ library and re-tags MP3 files that are missing
DJ metadata: BPM, Camelot key, TXXX:SPOTIFY_ID, TXXX:BPM, TXXX:KEY.

Also registers each found file in the library_index MongoDB collection
so the persistent O(1) dedup works for pre-existing tracks.

Usage
-----
    # Dry run — show what would be re-tagged, no changes
    python retag_migration.py --dry-run

    # Re-tag everything missing DJ tags in the default library folder
    python retag_migration.py

    # Limit to a specific subfolder
    python retag_migration.py --folder "UK Garage"

    # Limit how many files to process (useful for incremental runs)
    python retag_migration.py --limit 50

    # Force re-tag even if DJ tags already present
    python retag_migration.py --force

Exit codes: 0 = success, 1 = one or more files failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend/ is on sys.path
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


# ── Tag inspection ────────────────────────────────────────────────────────────

def _read_txxx(audio, desc: str) -> str:
    """Read a TXXX frame value by description, return '' if absent."""
    try:
        frame = audio.get(f"TXXX:{desc}")
        if frame and frame.text:
            return str(frame.text[0]).strip()
    except Exception:
        pass
    return ""


def _needs_dj_tags(filepath: str) -> bool:
    """Return True if the file is missing any critical DJ tags."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            audio = ID3(filepath)
        except ID3NoHeaderError:
            return True  # no tags at all — definitely needs tagging
        missing = (
            not audio.get("TBPM")
            or not audio.get("TKEY")
            or not _read_txxx(audio, "INITIALKEY")
            or not _read_txxx(audio, "SPOTIFY_ID")
            or not _read_txxx(audio, "BPM")
        )
        return missing
    except Exception:
        return False  # skip unreadable files silently


def _read_spotify_id_from_tags(filepath: str) -> str:
    """Extract TXXX:SPOTIFY_ID from an already-tagged file."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        audio = ID3(filepath)
        return _read_txxx(audio, "SPOTIFY_ID")
    except Exception:
        return ""


# ── Index registration ────────────────────────────────────────────────────────

def _index_file(filepath: str, spotify_id: str, title: str, artist: str,
                genre_folder: str, dry_run: bool) -> bool:
    """Register the file in library_index. Returns True on success."""
    if dry_run:
        return True
    try:
        from database import index_track
        from services.dedup_service import duplicate_identity_key, content_hash as _ch
        identity_key = duplicate_identity_key(spotify_id, title, artist)
        ch = _ch(title, artist)
        index_track(
            identity_key=identity_key,
            spotify_id=spotify_id,
            content_hash=ch,
            title=title,
            artist=artist,
            filename=os.path.basename(filepath),
            final_path=filepath,
            genre_folder=genre_folder,
        )
        return True
    except Exception as e:
        logger.warning(f"  [index] Failed to index {os.path.basename(filepath)}: {e}")
        return False


# ── Spotify metadata fetch ────────────────────────────────────────────────────

def _fetch_spotify_meta(spotify_id: str) -> dict | None:
    """Fetch track metadata from Spotify by ID."""
    if not spotify_id:
        return None
    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service()
        track = sp.sp.track(spotify_id)
        if not track:
            return None
        artists = track.get("artists", [])
        artist_name = artists[0]["name"] if artists else ""
        artist_id = artists[0]["id"] if artists else ""
        images = (track.get("album") or {}).get("images", [])
        art_url = images[0]["url"] if images else ""
        return {
            "id": spotify_id,
            "title": track.get("name", ""),
            "artist": artist_name,
            "artist_id": artist_id,
            "album": (track.get("album") or {}).get("name", ""),
            "duration_ms": track.get("duration_ms", 0),
            "release_date": (track.get("album") or {}).get("release_date", ""),
            "album_art_url": art_url,
        }
    except Exception as e:
        logger.warning(f"  [spotify] Failed to fetch {spotify_id}: {e}")
        return None


# ── Core migration logic ──────────────────────────────────────────────────────

_REPORTS_DIR = _BACKEND / "reports"

# Sentinel: files identified but confidence is 0.60–0.74 (needs review only)
_IDENTIFICATION_RESULTS: list = []


def migrate_file(
    filepath: str,
    dry_run: bool,
    force: bool,
    identify: bool = False,
) -> bool:
    """
    Re-tag a single MP3 file if it needs DJ tags.

    When identify=True and the file has no spotify_id, runs legacy
    identification first. Only enriches when confidence >= 0.75.

    Returns True on success (or if skipped because tags already present).
    """
    rel = os.path.relpath(filepath, BASE_DIR)
    parts = Path(rel).parts
    genre_folder  = parts[0] if len(parts) > 1 else ""
    artist_folder = parts[1] if len(parts) > 2 else ""

    stem = Path(filepath).stem
    if " - " in stem:
        path_artist, path_title = stem.split(" - ", 1)
    else:
        path_artist, path_title = artist_folder or "Unknown", stem

    spotify_id = _read_spotify_id_from_tags(filepath)

    # ── Legacy identification when spotify_id is missing ─────────────────────
    if identify and not spotify_id:
        try:
            from services.legacy_identification_service import (
                identify_file,
                CONF_ACCEPT_WARN,
                CONF_NEEDS_REVIEW,
            )
            id_result = identify_file(filepath)
            _IDENTIFICATION_RESULTS.append(id_result)

            if id_result.confidence >= CONF_ACCEPT_WARN:
                spotify_id   = id_result.spotify_id
                path_title   = id_result.title   or path_title
                path_artist  = id_result.artist  or path_artist
                logger.info(
                    f"  [legacy_id] Identified: '{id_result.title}' "
                    f"conf={id_result.confidence:.2f} → enrich"
                )
            elif id_result.confidence >= CONF_NEEDS_REVIEW:
                logger.info(
                    f"  [legacy_id] Low confidence ({id_result.confidence:.2f}) "
                    f"— NeedsReview only, no retag"
                )
                return True   # counted as OK; file preserved untouched
            else:
                logger.info(
                    f"  [legacy_id] Unidentified ({id_result.confidence:.2f}) "
                    f"— skipped"
                )
                return True
        except Exception as e:
            logger.warning(f"  [legacy_id] Identification failed for {rel}: {e}")

    # ── Standard DJ-tag check ─────────────────────────────────────────────────
    if not force and not _needs_dj_tags(filepath):
        logger.debug(f"  [skip] {rel} — DJ tags present")
        _index_file(filepath, spotify_id, path_title, path_artist, genre_folder, dry_run)
        return True

    logger.info(f"  [retag] {rel}")

    if dry_run:
        logger.info(f"    → DRY RUN: would retag (spotify_id={spotify_id or 'unknown'})")
        return True

    # ── Fetch Spotify metadata ────────────────────────────────────────────────
    spotify_meta = _fetch_spotify_meta(spotify_id)
    if spotify_meta is None:
        spotify_meta = {
            "id": spotify_id,
            "title": path_title,
            "artist": path_artist,
            "album": "",
            "duration_ms": 0,
            "release_date": "",
            "album_art_url": "",
        }

    try:
        from services.spotify_service import get_spotify_service
        sp_service = get_spotify_service()
    except Exception:
        sp_service = None

    try:
        from services.tagger_service import tag_file, save_tagging_report
        report = tag_file(filepath, spotify_meta, spotify_service_instance=sp_service)
        save_tagging_report(os.path.basename(filepath), report, spotify_id=spotify_id)
        bpm     = report.get("bpm", "?")
        camelot = report.get("camelot", "?")
        logger.info(f"    → BPM={bpm} | Camelot={camelot} | tags={report.get('tags_written', [])}")
    except Exception as e:
        logger.error(f"    → FAILED to retag: {e}")
        return False

    _index_file(
        filepath,
        spotify_meta.get("id", ""),
        spotify_meta.get("title", path_title),
        spotify_meta.get("artist", path_artist),
        genre_folder,
        dry_run,
    )
    return True


def run_migration(
    folder: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    force: bool = False,
    identify: bool = False,
    write_reports: bool = True,
) -> int:
    """
    Walk the library and re-tag files needing DJ metadata.

    identify=True: run legacy identification for files with no spotify_id.
    Returns count of failures (0 = success).
    """
    from library_scanner import is_excluded_path

    _IDENTIFICATION_RESULTS.clear()

    search_root = BASE_DIR / folder if folder else BASE_DIR
    if not search_root.is_dir():
        logger.error(f"Directory not found: {search_root}")
        return 1

    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Scanning: {search_root}")
    if identify:
        logger.info("[retag] Legacy identification enabled for files without spotify_id")

    all_mp3s = sorted(search_root.rglob("*.mp3"))

    excluded: list[Path] = []
    eligible: list[Path] = []
    for fp in all_mp3s:
        if is_excluded_path(fp):
            excluded.append(fp)
        else:
            eligible.append(fp)

    excluded_count   = len(excluded)
    excluded_examples = [os.path.relpath(str(fp), str(BASE_DIR)) for fp in excluded[:5]]
    for fp in excluded:
        logger.debug(f"[SKIP][EXCLUDED] {os.path.relpath(str(fp), str(BASE_DIR))}")

    mp3_files     = eligible
    total_eligible = len(mp3_files)
    if limit:
        mp3_files = mp3_files[:limit]
        logger.info(
            f"Found {len(all_mp3s)} MP3s — {excluded_count} excluded, "
            f"{total_eligible} eligible, processing {len(mp3_files)} (--limit {limit})"
        )
    else:
        logger.info(
            f"Found {len(all_mp3s)} MP3s — {excluded_count} excluded, "
            f"{total_eligible} eligible"
        )

    ok = fail = 0
    for i, fp in enumerate(mp3_files, 1):
        logger.info(f"[{i}/{len(mp3_files)}] {os.path.relpath(str(fp), str(BASE_DIR))}")
        result = migrate_file(str(fp), dry_run=dry_run, force=force, identify=identify)
        if result:
            ok += 1
        else:
            fail += 1

    logger.info(
        f"\n{'[DRY RUN] ' if dry_run else ''}Migration complete: "
        f"{ok} OK, {fail} failed — "
        f"{excluded_count} path(s) excluded (of {len(all_mp3s)} found)"
    )
    if excluded_count:
        logger.info(
            f"[SKIP][EXCLUDED] {excluded_count} file(s) skipped — "
            f"examples: {excluded_examples}"
        )

    # Write identification reports
    if identify and _IDENTIFICATION_RESULTS and write_reports:
        try:
            from services.legacy_identification_service import write_identification_report
            write_identification_report(_IDENTIFICATION_RESULTS, _REPORTS_DIR)
        except Exception as e:
            logger.warning(f"[retag] Report write failed: {e}")

    return fail


# ── Legacy library_index backfill ────────────────────────────────────────────

def _backfill_library_index_doc(doc: dict, dry_run: bool) -> dict:
    """
    Attempt to enrich one library_index doc that is missing spotify_id.

    Updates only: spotify_id, title, artist (in MongoDB) + ID3 tags.
    Never touches: final_path, genre_folder, identity_key, content_hash.

    Returns a result dict describing the outcome.
    """
    filepath     = doc.get("final_path", "")
    identity_key = doc.get("identity_key", "")

    out: dict = {
        "filepath":     filepath,
        "identity_key": identity_key,
        "status":       "skipped",
        "confidence":   0.0,
        "spotify_id":   "",
        "artist":       "",
        "title":        "",
        "album":        "",
        "reason":       "",
    }

    if not filepath or not Path(filepath).is_file():
        out["status"] = "file_not_found"
        out["reason"] = "file missing on disk"
        return out

    try:
        from services.legacy_identification_service import identify_file, CONF_ACCEPT_WARN
        id_result = identify_file(filepath)
    except Exception as exc:
        out["status"] = "identification_error"
        out["reason"] = str(exc)
        return out

    out["confidence"] = id_result.confidence
    out["artist"]     = id_result.artist
    out["title"]      = id_result.title
    out["spotify_id"] = id_result.spotify_id
    out["reason"]     = id_result.confidence_reason

    if not id_result.matched or id_result.confidence < CONF_ACCEPT_WARN:
        out["status"] = "unresolved"
        return out

    if not id_result.spotify_id:
        out["status"] = "unresolved"
        out["reason"]  = "matched but no spotify_id returned"
        return out

    # Duplicate guard: reject if another doc already owns this spotify_id
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            dup = col.find_one(
                {"spotify_id": id_result.spotify_id,
                 "identity_key": {"$ne": identity_key}},
                {"identity_key": 1, "final_path": 1},
            )
            if dup:
                out["status"] = "duplicate_spotify_id"
                out["reason"] = f"already indexed: {dup.get('final_path', 'unknown')}"
                return out
    except Exception as exc:
        out["status"] = "validation_error"
        out["reason"]  = str(exc)
        return out

    if dry_run:
        out["status"] = "would_repair"
        return out

    # Write to library_index — metadata fields only
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            patch: dict = {"spotify_id": id_result.spotify_id}
            if id_result.title:
                patch["title"] = id_result.title
            if id_result.artist:
                patch["artist"] = id_result.artist
            col.update_one({"identity_key": identity_key}, {"$set": patch})
    except Exception as exc:
        out["status"] = "update_error"
        out["reason"]  = str(exc)
        return out

    # Enrich ID3 tags
    try:
        spotify_meta = _fetch_spotify_meta(id_result.spotify_id)
        if spotify_meta:
            out["album"] = spotify_meta.get("album", "")
            try:
                from services.spotify_service import get_spotify_service
                sp_svc = get_spotify_service()
            except Exception:
                sp_svc = None
            from services.tagger_service import tag_file, save_tagging_report
            tag_rpt = tag_file(filepath, spotify_meta, spotify_service_instance=sp_svc)
            save_tagging_report(
                Path(filepath).name, tag_rpt, spotify_id=id_result.spotify_id
            )
    except Exception as exc:
        logger.warning(
            f"  [backfill] ID3 update skipped for {Path(filepath).name}: {exc}"
        )

    out["status"] = "repaired"
    return out


def run_legacy_backfill(
    dry_run: bool = False,
    limit: int | None = None,
    write_reports: bool = True,
) -> dict:
    """
    Scan library_index for docs missing spotify_id and enrich them.

    Identification threshold: CONF_ACCEPT_WARN (0.75).
    Checkpoints every 50 files → reports/legacy_backfill_checkpoint.json.
    Writes final report → reports/legacy_backfill_report.json.

    Returns:
        {scanned, repaired, unresolved, errors, remaining_unresolved,
         metadata_recovery_rate_pct}
    """
    from services.legacy_identification_service import CONF_ACCEPT_WARN, CONF_AUTO_ACCEPT
    from database import get_library_index_collection

    col = get_library_index_collection()
    if col is None:
        logger.error("[backfill] Cannot connect to library_index")
        return {"error": "no database connection"}

    _missing_query = {"$or": [
        {"spotify_id": {"$exists": False}},
        {"spotify_id": ""},
    ]}

    docs = list(col.find(
        _missing_query,
        {"identity_key": 1, "final_path": 1, "title": 1, "artist": 1},
    ))
    if limit:
        docs = docs[:limit]

    total = len(docs)
    label = "[DRY] " if dry_run else ""
    logger.info(
        f"[backfill] {label}Found {total} library_index doc(s) missing spotify_id"
    )

    checkpoint_path = _REPORTS_DIR / "legacy_backfill_checkpoint.json"
    results:        list[dict] = []
    processed_keys: set[str]  = set()

    if checkpoint_path.exists():
        try:
            cp_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            results = cp_data.get("results", [])
            processed_keys = {r["identity_key"] for r in results if r.get("identity_key")}
            logger.info(
                f"[backfill] Resumed checkpoint — {len(processed_keys)} already done"
            )
        except Exception as exc:
            logger.warning(f"[backfill] Checkpoint corrupt, starting fresh: {exc}")
            results, processed_keys = [], set()

    todo = [d for d in docs if d.get("identity_key", "") not in processed_keys]
    logger.info(f"[backfill] {len(todo)} file(s) to process after checkpoint skip")

    repaired = unresolved = errors = skipped = 0

    for i, doc in enumerate(todo, 1):
        ik = doc.get("identity_key", "")
        fp = doc.get("final_path", "")
        logger.info(f"  [{i}/{len(todo)}] {Path(fp).name if fp else ik}")

        result = _backfill_library_index_doc(doc, dry_run)
        result["identity_key"] = ik
        results.append(result)

        status = result["status"]
        if status in ("repaired", "would_repair"):
            repaired += 1
            logger.info(
                f"    → {status.upper()} conf={result['confidence']:.2f} "
                f"id={result['spotify_id'][:8]}… "
                f"'{result['title']}' / '{result['artist']}'"
            )
        elif status == "unresolved":
            unresolved += 1
        elif status in (
            "file_not_found", "identification_error",
            "update_error", "validation_error", "duplicate_spotify_id",
        ):
            errors += 1
            logger.warning(f"    → ERROR ({status}): {result['reason']}")
        else:
            skipped += 1

        if i % 50 == 0 or i == len(todo):
            try:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(
                    json.dumps({"results": results}, indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning(f"[backfill] Checkpoint write failed: {exc}")

    # Confidence distribution over all processed results
    conf_dist: dict[str, int] = {
        "high(>=0.90)":     0,
        "accept(0.75-0.89)": 0,
        "review(0.60-0.74)": 0,
        "low(<0.60)":        0,
    }
    for r in results:
        v = r.get("confidence", 0.0)
        if v >= CONF_AUTO_ACCEPT:
            conf_dist["high(>=0.90)"] += 1
        elif v >= CONF_ACCEPT_WARN:
            conf_dist["accept(0.75-0.89)"] += 1
        elif v >= 0.60:
            conf_dist["review(0.60-0.74)"] += 1
        elif v > 0:
            conf_dist["low(<0.60)"] += 1

    # Top unresolved artists (for trend visibility)
    unresolved_artist_counts: dict[str, int] = {}
    for r in results:
        if r["status"] == "unresolved":
            artist = r.get("artist", "") or Path(r.get("filepath", "")).parent.name
            if artist:
                unresolved_artist_counts[artist] = (
                    unresolved_artist_counts.get(artist, 0) + 1
                )
    top_unresolved_artists = sorted(
        [{"artist": a, "count": c} for a, c in unresolved_artist_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:20]

    remaining_in_db = col.count_documents(_missing_query)
    recovery_rate   = round(repaired / max(total, 1) * 100, 1)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run":       dry_run,
        "summary": {
            "scanned":                   total,
            "repaired":                  repaired,
            "unresolved":                unresolved,
            "errors":                    errors,
            "skipped":                   skipped,
            "remaining_in_db":           remaining_in_db,
            "metadata_recovery_rate_pct": recovery_rate,
        },
        "confidence_distribution": conf_dist,
        "top_unresolved_artists":  top_unresolved_artists,
        "results":                 results,
    }

    if write_reports:
        try:
            _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            rpath = _REPORTS_DIR / "legacy_backfill_report.json"
            rpath.write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8"
            )
            logger.info(f"[backfill] Report → {rpath}")
        except Exception as exc:
            logger.warning(f"[backfill] Report write failed: {exc}")

    logger.info(
        f"[backfill] {label}Complete: {repaired} repaired / "
        f"{unresolved} unresolved / {errors} errors — "
        f"recovery rate {recovery_rate}% — {remaining_in_db} still missing in DB"
    )

    return {
        "scanned":                   total,
        "repaired":                  repaired,
        "unresolved":                unresolved,
        "errors":                    errors,
        "remaining_unresolved":      remaining_in_db,
        "metadata_recovery_rate_pct": recovery_rate,
    }


# ── Trance analysis helper ────────────────────────────────────────────────────

def run_trance_analysis(dry_run: bool = True) -> None:
    """Phase 5: Analyse the Trance genre bucket for misrouted tracks."""
    from services.legacy_identification_service import analyze_trance_bucket
    from services.genre_router import GENRE_TAXONOMY
    # Trance routes to Library/Electronic/Electronic — find that folder
    trance_dir = BASE_DIR / "Library" / "Electronic" / "Electronic"
    if not trance_dir.is_dir():
        trance_dir = BASE_DIR / "Library" / "Electronic"
    logger.info(f"[trance] Analysing bucket: {trance_dir}")
    report = analyze_trance_bucket(trance_dir, _REPORTS_DIR)
    safe = report.get("safe_to_move_count", 0)
    total = report.get("total_files", 0)
    logger.info(f"[trance] {safe}/{total} can be rerouted safely (dry_run={dry_run})")


# ── Canonical rename helper ───────────────────────────────────────────────────

def run_canonical_renames(dry_run: bool = True, apply: bool = False) -> None:
    """Phase 4: Detect and optionally apply canonical artist folder renames."""
    from services.legacy_identification_service import analyze_canonical_renames
    report = analyze_canonical_renames(BASE_DIR, _REPORTS_DIR,
                                       dry_run=dry_run, apply_renames=apply)
    count = report.get("rename_candidates", 0)
    logger.info(f"[rename] {count} rename candidate(s) — apply={apply}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Re-tag existing library files with DJ metadata"
    )
    parser.add_argument("--dry-run",          action="store_true",
                        help="Show what would happen; make no changes")
    parser.add_argument("--folder",           metavar="FOLDER",
                        help="Subfolder within library to scan (e.g. 'UK Garage')")
    parser.add_argument("--limit",            type=int, metavar="N",
                        help="Process at most N files")
    parser.add_argument("--force",            action="store_true",
                        help="Re-tag even if DJ tags already present")
    parser.add_argument("--identify",         action="store_true",
                        help="Run legacy identification for files with no spotify_id")
    parser.add_argument("--analyze-trance",   action="store_true",
                        help="Phase 5: analyse Trance bucket for misrouted tracks")
    parser.add_argument("--canonical-renames", action="store_true",
                        help="Phase 4: report artist folder rename candidates")
    parser.add_argument("--apply-renames",    action="store_true",
                        help="Apply canonical artist folder renames (use with --canonical-renames)")
    parser.add_argument("--backfill",         action="store_true",
                        help="Repair library_index docs missing spotify_id via Spotify search")
    args = parser.parse_args()

    if args.backfill:
        result = run_legacy_backfill(
            dry_run=args.dry_run,
            limit=args.limit,
        )
        sys.exit(0 if result.get("errors", 0) == 0 else 1)

    if args.analyze_trance:
        run_trance_analysis(dry_run=args.dry_run)
        sys.exit(0)

    if args.canonical_renames:
        run_canonical_renames(dry_run=args.dry_run, apply=args.apply_renames)
        sys.exit(0)

    failures = run_migration(
        folder=args.folder,
        dry_run=args.dry_run,
        limit=args.limit,
        force=args.force,
        identify=args.identify,
    )
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
