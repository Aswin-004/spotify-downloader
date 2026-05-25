"""
Auto-reclassification Engine — Phase 5
=======================================
Periodically scans NeedsReview/ and attempts to re-route tracks whose
metadata confidence has improved, using:

  1. Artist memory  — has the user previously moved this artist?
  2. Spotify API    — fresh genre lookup via the artist ID in ID3 tags
  3. Fingerprint    — acoustid match against already-indexed tracks
  4. Genre enrichment — MusicBrainz cross-reference via TXXX:SPOTIFY_ID

Safe-by-design
--------------
• Dry-run mode by default (set ``dry_run=False`` to execute moves).
• Preserves library_index identity_key — only ``final_path`` is updated.
• Preserves fingerprints — only index document is patched.
• Preserves retry manifests — only finished moves clear them.
• Never deletes source files; creates destination parent dirs first.
• Re-raises nothing — all errors are logged and counted.

Usage
-----
    from services.reclassification_service import run_reclassification
    report = run_reclassification(dry_run=True)
    # {'scanned': 42, 'rerouted': 7, 'still_unknown': 35, 'errors': 0}

Integration
-----------
Called by the maintenance worker (Phase 16) via task "reclassify_needs_review"
at a 3600 s cooldown (once per hour).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from config import config

BASE_DIR     = Path(config.BASE_DOWNLOAD_DIR)
NR_DIR       = BASE_DIR / "NeedsReview"
_REPORTS_DIR = Path(__file__).parent.parent / "reports"

_CONFIDENCE_REROUTE      = 0.5   # minimum confidence to move out of NeedsReview
_BULK_RESOLVE_THRESHOLD  = 0.90  # bulk-resolve only moves at this confidence or higher


def _read_id3_tags(filepath: str) -> dict:
    """Extract TXXX:SPOTIFY_ID and TPE1 (artist) from an MP3."""
    tags: dict = {"spotify_id": "", "artist": ""}
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        audio = ID3(filepath)
        txxx = audio.get("TXXX:SPOTIFY_ID")
        if txxx and txxx.text:
            tags["spotify_id"] = str(txxx.text[0]).strip()
        tpe1 = audio.get("TPE1")
        if tpe1 and tpe1.text:
            tags["artist"] = str(tpe1.text[0]).strip()
        tit2 = audio.get("TIT2")
        if tit2 and tit2.text:
            tags["title"] = str(tit2.text[0]).strip()
    except Exception:
        pass
    return tags


def _try_reroute(
    filepath: Path,
    sp=None,
) -> Optional[str]:
    """
    Attempt to determine a better genre for a single NeedsReview track.

    Returns the new Library/ folder path on success, or None if still unknown.
    """
    tags        = _read_id3_tags(str(filepath))
    artist_name = tags.get("artist", "") or filepath.parent.name
    spotify_id  = tags.get("spotify_id", "")

    # 1. Artist memory — fastest, no API call
    try:
        from services.artist_memory_service import lookup_artist
        mem = lookup_artist(artist_name)
        if mem and mem.get("confidence", 0) >= _CONFIDENCE_REROUTE:
            from services.genre_router import _library_path, clean_folder_name
            from services.organizer_service import clean_folder_name as _cfn
            lib    = _library_path(mem["genre"])
            folder = f"{lib}/{_cfn(artist_name)}"
            logger.info(
                f"[reclass] {filepath.name}: memory → {folder} "
                f"(conf={mem['confidence']:.2f})"
            )
            return folder
    except Exception:
        pass

    # 2. Spotify genre lookup using TXXX:SPOTIFY_ID
    if spotify_id and sp is not None:
        try:
            from services.genre_router import resolve_genre_folder_with_confidence
            # Fetch artist_id from Spotify track
            track = sp.track(spotify_id)
            artists = (track or {}).get("artists", [])
            artist_id = artists[0]["id"] if artists else ""
            if artist_id:
                folder, conf, source = resolve_genre_folder_with_confidence(
                    artist_id, artist_name, sp
                )
                if conf >= _CONFIDENCE_REROUTE and not folder.startswith("NeedsReview"):
                    logger.info(
                        f"[reclass] {filepath.name}: spotify → {folder} "
                        f"(conf={conf:.2f}, src={source})"
                    )
                    return folder
        except Exception as e:
            logger.debug(f"[reclass] Spotify lookup failed for {filepath.name}: {e}")

    # 3. Fingerprint match against library_index
    try:
        from services.fingerprint_service import compute as fp_compute, find_duplicate
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            fp_result = fp_compute(str(filepath))
            if fp_result.is_valid:
                match = find_duplicate(fp_result, col)
                if match and match.get("genre_folder"):
                    folder = match["genre_folder"]
                    if not folder.startswith("NeedsReview"):
                        logger.info(
                            f"[reclass] {filepath.name}: fingerprint match → {folder}"
                        )
                        return folder
    except Exception:
        pass

    # 4. Artist folder intelligence — use parent folder name as artist hint
    try:
        from services.artist_folder_service import route_artist_folder
        from services.organizer_service import clean_folder_name as _cfn
        folder_artist = filepath.parent.name
        route = route_artist_folder(folder_artist, filepath.parent)
        if route and not route.startswith("NeedsReview"):
            full_folder = f"{route}/{_cfn(artist_name or folder_artist)}"
            logger.info(
                f"[reclass] {filepath.name}: artist_folder_service → {full_folder}"
            )
            return full_folder
    except Exception:
        pass

    return None


def _execute_move(
    src: Path,
    dest_folder_rel: str,
    dry_run: bool,
    artist_name: str = "",
) -> bool:
    """
    Move *src* to ``BASE_DIR/{dest_folder_rel}/{collision_safe_name}``.
    Collision strategy: artist suffix → spotify_id → md5 hash.
    Updates library_index final_path.
    Returns True on success.
    """
    from services.organizer_service import _collision_safe_name

    dest_dir  = BASE_DIR / dest_folder_rel

    if dry_run:
        logger.info(f"[reclass][DRY] Would move: {src.name} → {dest_folder_rel}/")
        return True

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Collision-safe name: artist suffix → spotify_id → hash
        spotify_id = ""
        try:
            from mutagen.id3 import ID3, ID3NoHeaderError
            audio = ID3(str(src))
            txxx = audio.get("TXXX:SPOTIFY_ID")
            if txxx and txxx.text:
                spotify_id = str(txxx.text[0]).strip()
        except Exception:
            pass

        dest_path = _collision_safe_name(dest_dir, src.name, artist_name, spotify_id)

        src_st  = os.stat(src)
        dest_st = os.stat(dest_dir)
        if src_st.st_dev == dest_st.st_dev:
            os.replace(src, dest_path)
        else:
            shutil.copy2(src, dest_path)
            if os.path.getsize(dest_path) != os.path.getsize(src):
                os.remove(dest_path)
                raise OSError("Cross-device copy size mismatch")
            os.remove(src)

        # Update library_index
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                res = col.update_one(
                    {"final_path": str(src)},
                    {"$set": {
                        "final_path":   str(dest_path),
                        "filename":     dest_path.name,
                        "genre_folder": dest_folder_rel,
                    }},
                )
                if res.matched_count == 0:
                    logger.warning(
                        f"[reclass] library_index: 0 documents matched "
                        f"final_path={str(src)!r} — genre_folder not updated"
                    )
        except Exception as idx_err:
            logger.warning(f"[reclass] library_index update failed: {idx_err}")

        # Record the move in artist memory
        try:
            from services.artist_memory_service import record_move
            from services.genre_router import GENRE_TAXONOMY
            # Extract genre from dest_folder_rel (last significant component)
            parts = dest_folder_rel.replace("Library/", "").split("/")
            genre_name = parts[-1] if parts else ""
            for canonical, (family, subpath) in GENRE_TAXONOMY.items():
                if subpath.split("/")[-1] == genre_name or canonical == genre_name:
                    artist_part = dest_path.parent.name
                    record_move(artist_part, canonical, source="reclassification")
                    break
        except Exception:
            pass

        logger.info(f"[reclass] Moved: {src.name} → {dest_path}")
        return True

    except Exception as e:
        logger.error(f"[reclass] Move failed {src.name}: {e}")
        return False


def _artist_dest_folder_rel(library_path: str, _artist_folder_name: str = "") -> str:
    """
    Return the dest_folder_rel for _execute_move.

    Flat structure: returns library_path directly — no artist subfolder.
    ``artist_folder_name`` is retained as a parameter so call sites do not
    need to change; it is used only for validation messaging.
    Guards against Library/Library nesting.
    """
    dest = library_path
    parts = dest.split("/")
    if parts.count("Library") > 1:
        raise ValueError(f"Library/Library nesting detected in: {dest!r}")
    if not dest.startswith("Library/"):
        raise ValueError(f"Target must start with Library/: {dest!r}")
    return dest


def bulk_resolve_needsreview_artists(
    base_dir: Optional[Path] = None,
    dry_run: bool = True,
    confidence_threshold: float = _BULK_RESOLVE_THRESHOLD,
) -> dict:
    """
    Bulk-resolve high-confidence Indian artist NeedsReview folders.

    Scans each immediate artist subfolder under NeedsReview/ and moves all
    .mp3 files to the correct Library/ path when confidence >= threshold.

    Resolution order:
      1. NEEDS_REVIEW_RESOLUTION_MAP (conf 0.95) — curated Indian artists
      2. config.ARTIST_GENRE_OVERRIDE (conf 1.0) — explicit overrides

    Artists that resolve below the threshold are skipped and reported.

    Args:
        base_dir:             Root download dir. Defaults to config.BASE_DOWNLOAD_DIR.
        dry_run:              Log moves without executing (default True).
        confidence_threshold: Minimum confidence to trigger a move (default 0.90).

    Returns:
        {
          moved_artists, skipped_artists, unresolved_artists, errors,
          total_files_moved, dry_run, report_path
        }
    """
    from services.artist_knowledge_service import lookup_needsreview_routing
    from services.genre_router import _library_path
    from services.organizer_service import clean_folder_name

    root       = Path(base_dir) if base_dir else BASE_DIR
    nr_dir     = root / "NeedsReview"
    label      = "[DRY] " if dry_run else ""

    moved_artists: list[dict]      = []
    skipped_artists: list[dict]    = []
    unresolved_artists: list[str]  = []
    rollback_log: list[dict]       = []
    errors: list[str]              = []

    if not nr_dir.is_dir():
        logger.warning(f"[bulk_resolve] NeedsReview dir not found: {nr_dir}")
        return _bulk_report(
            moved_artists, skipped_artists, unresolved_artists,
            rollback_log, errors, dry_run,
        )

    # Collect artist-level subdirectories only
    artist_dirs = [d for d in sorted(nr_dir.iterdir()) if d.is_dir()]
    logger.info(f"[bulk_resolve] {label}Scanning {len(artist_dirs)} NeedsReview artist folder(s)")

    for artist_dir in artist_dirs:
        artist_name = artist_dir.name
        conf        = 0.0
        source      = ""
        lib_path    = ""
        genre       = ""

        # 1. Curated resolution map (conf 0.95)
        try:
            hit = lookup_needsreview_routing(artist_name)
            if hit:
                lib_path, conf, genre = hit
                source = "needs_review_resolution_map"
        except Exception as e:
            logger.debug(f"[bulk_resolve] lookup_needsreview_routing({artist_name!r}) error: {e}")

        # 2. config.ARTIST_GENRE_OVERRIDE (conf 1.0)
        if not lib_path:
            from services.genre_router import normalize_artist_key as _nak
            override_genre = config.ARTIST_GENRE_OVERRIDE.get(_nak(artist_name), "")
            if override_genre:
                try:
                    lib_path = _library_path(override_genre)
                    conf     = 1.0
                    genre    = override_genre
                    source   = "artist_genre_override"
                except Exception:
                    pass

        if not lib_path:
            unresolved_artists.append(artist_name)
            logger.debug(f"[bulk_resolve] {artist_name!r}: unresolved — no routing found")
            continue

        if conf < confidence_threshold:
            skipped_artists.append({
                "artist": artist_name,
                "confidence": conf,
                "source": source,
                "reason": f"conf {conf:.2f} < threshold {confidence_threshold:.2f}",
            })
            logger.debug(
                f"[bulk_resolve] {label}{artist_name!r}: skipped "
                f"(conf={conf:.2f} < {confidence_threshold:.2f})"
            )
            continue

        # Safety: build dest_folder_rel with nesting guard
        try:
            dest_folder_rel = _artist_dest_folder_rel(lib_path, artist_name)
        except ValueError as ve:
            errors.append(f"{artist_name}: {ve}")
            logger.error(f"[bulk_resolve] {artist_name!r}: safety check failed — {ve}")
            continue

        # Move all .mp3 files in this artist folder
        mp3_files    = list(artist_dir.glob("*.mp3"))
        files_moved  = 0
        file_errors  = 0

        for mp3 in mp3_files:
            ok = _execute_move(mp3, dest_folder_rel, dry_run=dry_run, artist_name=artist_name)
            if ok:
                files_moved += 1
                rollback_log.append({
                    "artist":    artist_name,
                    "src":       str(mp3),
                    "dest_dir":  dest_folder_rel,
                    "dry_run":   dry_run,
                })
            else:
                file_errors += 1
                errors.append(f"{artist_name}/{mp3.name}: move failed")

        # Record artist_memory for the artist (once per folder, not per file)
        if files_moved > 0 and not dry_run:
            try:
                from services.artist_memory_service import record_move
                record_move(artist_name, genre, source="bulk_needsreview_resolve")
            except Exception as e:
                logger.debug(f"[bulk_resolve] artist_memory update skipped for {artist_name!r}: {e}")

        moved_artists.append({
            "artist":       artist_name,
            "target":       dest_folder_rel,
            "genre":        genre,
            "confidence":   conf,
            "source":       source,
            "files_moved":  files_moved,
            "file_errors":  file_errors,
        })
        logger.info(
            f"[bulk_resolve] {label}{artist_name!r} → {dest_folder_rel} "
            f"({files_moved} file(s), conf={conf:.2f}, src={source})"
        )

    return _bulk_report(
        moved_artists, skipped_artists, unresolved_artists,
        rollback_log, errors, dry_run,
    )


def _bulk_report(
    moved: list[dict],
    skipped: list[dict],
    unresolved: list[str],
    rollback_log: list[dict],
    errors: list[str],
    dry_run: bool,
) -> dict:
    """Write reports/needsreview_bulk_resolution.json and return summary dict."""
    total_files = sum(a.get("files_moved", 0) for a in moved)

    report = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "dry_run":           dry_run,
        "confidence_threshold": _BULK_RESOLVE_THRESHOLD,
        "moved_artists":     moved,
        "skipped_artists":   skipped,
        "unresolved_artists": unresolved,
        "rollback_log":      rollback_log,
        "errors":            errors,
        "summary": {
            "moved_count":      len(moved),
            "skipped_count":    len(skipped),
            "unresolved_count": len(unresolved),
            "total_files_moved": total_files,
            "error_count":      len(errors),
        },
    }

    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = _REPORTS_DIR / "needsreview_bulk_resolution.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(f"[bulk_resolve] Report written → {report_path}")
        report["report_path"] = str(report_path)
    except Exception as e:
        logger.warning(f"[bulk_resolve] Failed to write report: {e}")
        report["report_path"] = ""

    return {
        "moved_artists":      len(moved),
        "skipped_artists":    len(skipped),
        "unresolved_artists": len(unresolved),
        "total_files_moved":  total_files,
        "errors":             len(errors),
        "dry_run":            dry_run,
        "report_path":        report.get("report_path", ""),
    }


def diagnose_needsreview_move(
    artist_name: str = "A.R. Rahman",
    base_dir: Optional[Path] = None,
    dry_run: bool = True,
) -> dict:
    """
    Trace a single NeedsReview artist through every layer of the move pipeline
    and write reports/needsreview_move_diagnostics.json.

    Nothing is moved — the diagnosis itself always runs as a simulation.
    The ``dry_run`` argument is passed through to _execute_move only to
    show what the live call would do.
    """
    from services.artist_knowledge_service import lookup_needsreview_routing

    root   = Path(base_dir) if base_dir else BASE_DIR
    nr_dir = root / "NeedsReview"

    diag: dict = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "artist":         artist_name,
        "dry_run_param":  dry_run,
        "source":         "",
        "destination":    "",
        "confidence":     0.0,
        "move_attempted": False,
        "move_executed":  False,
        "blocked_reason": "",
        "layers": {},
    }

    # ── Layer 1: NeedsReview directory ────────────────────────────────────────
    nr_exists   = nr_dir.is_dir()
    artist_dir  = nr_dir / artist_name
    artist_exists = artist_dir.is_dir()
    mp3_files   = list(artist_dir.glob("*.mp3")) if artist_exists else []
    diag["layers"]["L1_directory"] = {
        "nr_dir":        str(nr_dir),
        "nr_exists":     nr_exists,
        "artist_dir":    str(artist_dir),
        "artist_exists": artist_exists,
        "mp3_count":     len(mp3_files),
        "mp3_files":     [f.name for f in mp3_files],
    }
    if not nr_exists:
        diag["blocked_reason"] = f"NeedsReview dir not found: {nr_dir}"
        return _write_diagnostics(diag)
    if not artist_exists:
        diag["blocked_reason"] = f"Artist folder not found: {artist_dir}"
        return _write_diagnostics(diag)
    if not mp3_files:
        diag["blocked_reason"] = f"No MP3 files in {artist_dir}"
        return _write_diagnostics(diag)

    # ── Layer 2: Routing resolution ───────────────────────────────────────────
    hit      = lookup_needsreview_routing(artist_name)
    from services.genre_router import normalize_artist_key as _nak
    override = config.ARTIST_GENRE_OVERRIDE.get(_nak(artist_name), "")
    diag["layers"]["L2_routing"] = {
        "resolution_map_hit": hit,
        "artist_genre_override": override,
    }
    if hit:
        lib_path, conf, genre = hit
        source = "needs_review_resolution_map"
    elif override:
        try:
            from services.genre_router import _library_path
            lib_path = _library_path(override)
            conf     = 1.0
            source   = "artist_genre_override"
        except Exception as e:
            diag["blocked_reason"] = f"_library_path failed for override {override!r}: {e}"
            return _write_diagnostics(diag)
    else:
        diag["blocked_reason"] = (
            "No routing found — artist not in NEEDS_REVIEW_RESOLUTION_MAP "
            "or ARTIST_GENRE_OVERRIDE"
        )
        return _write_diagnostics(diag)

    diag["source"]     = source
    diag["confidence"] = conf

    # ── Layer 3: Confidence threshold ─────────────────────────────────────────
    diag["layers"]["L3_threshold"] = {
        "confidence":           conf,
        "threshold":            _BULK_RESOLVE_THRESHOLD,
        "passes":               conf >= _BULK_RESOLVE_THRESHOLD,
    }
    if conf < _BULK_RESOLVE_THRESHOLD:
        diag["blocked_reason"] = (
            f"conf {conf:.2f} < threshold {_BULK_RESOLVE_THRESHOLD:.2f}"
        )
        return _write_diagnostics(diag)

    # ── Layer 4: Destination path ─────────────────────────────────────────────
    try:
        dest_rel  = _artist_dest_folder_rel(lib_path, artist_name)
        dest_full = root / dest_rel
    except ValueError as ve:
        diag["layers"]["L4_destination"] = {"error": str(ve)}
        diag["blocked_reason"] = f"Safety check failed: {ve}"
        return _write_diagnostics(diag)

    diag["destination"] = dest_rel
    diag["layers"]["L4_destination"] = {
        "library_path":   lib_path,
        "dest_folder_rel": dest_rel,
        "dest_full":      str(dest_full),
        "dest_exists":    dest_full.is_dir(),
    }

    # ── Layer 5: _execute_move guard ──────────────────────────────────────────
    # Simulate — do NOT actually call _execute_move here
    diag["move_attempted"] = True
    diag["layers"]["L5_execute_move"] = {
        "dry_run":           dry_run,
        "guard_triggers":    dry_run,
        "guard_location":    "reclassification_service._execute_move:174 — `if dry_run: return True`",
        "filesystem_write":  not dry_run,
        "would_move":        [
            {
                "src":  str(mp3),
                "dest": str(dest_full / mp3.name),
            }
            for mp3 in mp3_files
        ],
    }

    if dry_run:
        diag["blocked_reason"] = (
            "dry_run=True: _execute_move returns True without touching filesystem. "
            "No caller passes dry_run=False to bulk_resolve_needsreview_artists()."
        )
    else:
        diag["move_executed"]  = True
        diag["blocked_reason"] = ""

    # ── Layer 6: Live caller audit ────────────────────────────────────────────
    diag["layers"]["L6_caller_audit"] = {
        "bulk_resolve_default_dry_run": True,
        "maintenance_worker_calls":     "run_reclassification(dry_run=False) — NOT bulk_resolve",
        "bulk_resolve_live_callers":    "NONE — bulk_resolve_needsreview_artists never called with dry_run=False",
        "fix":                          "call bulk_resolve_needsreview_artists(dry_run=False) from CLI or Telegram",
    }

    return _write_diagnostics(diag)


def _write_diagnostics(diag: dict) -> dict:
    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTS_DIR / "needsreview_move_diagnostics.json"
        path.write_text(json.dumps(diag, indent=2, default=str), encoding="utf-8")
        logger.info(f"[diagnose] Report → {path}")
        diag["report_path"] = str(path)
    except Exception as e:
        logger.warning(f"[diagnose] Failed to write diagnostics: {e}")
    return diag


def run_reclassification(
    dry_run: bool = True,
    limit: int = 50,
    sp=None,
) -> dict:
    """
    Scan NeedsReview/ and attempt to reroute tracks with improved confidence.

    Args:
        dry_run: When True (default), log intended moves but don't execute.
        limit:   Maximum number of files to process per run.
        sp:      Authenticated spotipy.Spotify client (optional; enables
                 Spotify API lookups for tracks with TXXX:SPOTIFY_ID).

    Returns:
        {
          "scanned":       int,
          "rerouted":      int,
          "still_unknown": int,
          "errors":        int,
          "dry_run":       bool,
        }
    """
    if not NR_DIR.is_dir():
        return {"scanned": 0, "rerouted": 0, "still_unknown": 0, "errors": 0, "dry_run": dry_run}

    mp3_files = sorted(NR_DIR.rglob("*.mp3"))[:limit]
    scanned = rerouted = still_unknown = errors = 0

    for fp in mp3_files:
        scanned += 1
        try:
            dest = _try_reroute(fp, sp=sp)
            if dest:
                ok = _execute_move(fp, dest, dry_run=dry_run, artist_name=fp.parent.name)
                if ok:
                    rerouted += 1
                else:
                    errors += 1
            else:
                still_unknown += 1
        except Exception as e:
            errors += 1
            logger.error(f"[reclass] Unexpected error for {fp.name}: {e}")

    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        f"[reclass] {label}Reclassification complete: "
        f"{rerouted} rerouted, {still_unknown} still unknown, "
        f"{errors} errors (of {scanned} scanned)"
    )
    return {
        "scanned":       scanned,
        "rerouted":      rerouted,
        "still_unknown": still_unknown,
        "errors":        errors,
        "dry_run":       dry_run,
    }


def generate_library_flatten_plan(base_dir: Optional[Path] = None) -> dict:
    """
    Analyse the current Library/ structure and produce a migration plan for
    the flat path change (remove artist subfolder).

    Writes reports/library_flatten_plan.json.
    Read-only — no files moved.
    """
    root        = Path(base_dir) if base_dir else BASE_DIR
    library_dir = root / "Library"

    affected_builders = [
        {
            "location": "genre_router._resolve_core (×4)",
            "old":      "Library/{family}/{subgenre}/{artist}",
            "new":      "Library/{family}/{subgenre}",
            "status":   "patched",
        },
        {
            "location": "reclassification_service._artist_dest_folder_rel",
            "old":      "library_path + '/' + clean_artist",
            "new":      "library_path (direct)",
            "status":   "patched",
        },
        {
            "location": "reclassification_service._execute_move collision guard",
            "old":      "stem_{n}.mp3",
            "new":      "stem - Artist.mp3 → stem - spotifyId.mp3 → stem - md5.mp3",
            "status":   "patched",
        },
        {
            "location": "organizer_service.resolve_destination_path",
            "old":      "skip-if-exists only",
            "new":      "_collision_safe_name (artist → spotify_id → hash)",
            "status":   "patched",
        },
        {
            "location": "auto_downloader.py:600 (MusicBrainz branch)",
            "old":      "os.path.join(BASE, mapped, clean_folder_name(artist))",
            "new":      "os.path.join(BASE, mapped)  — remove artist append",
            "status":   "NOT patched — out of target scope, manual update required",
        },
    ]

    # Scan existing Library for artist-depth folders
    existing_artist_folders: list[dict] = []
    collision_scenarios:     list[dict] = []

    if library_dir.is_dir():
        for genre_dir in library_dir.rglob("*"):
            if not genre_dir.is_dir():
                continue
            rel = genre_dir.relative_to(library_dir)
            depth = len(rel.parts)
            if depth != 2:          # depth 2 = family/subgenre/artist
                continue
            mp3s = list(genre_dir.glob("*.mp3"))
            if not mp3s:
                continue
            parent_rel = str(rel.parent)  # family/subgenre
            existing_artist_folders.append({
                "artist_folder": str(rel),
                "flat_target":   f"Library/{parent_rel}",
                "file_count":    len(mp3s),
                "files":         [f.name for f in mp3s],
            })

        # Detect collision scenarios: same filename in sibling artist folders
        flat_targets: dict[str, list[str]] = {}
        for entry in existing_artist_folders:
            target = entry["flat_target"]
            for fname in entry["files"]:
                key = f"{target}/{fname}"
                flat_targets.setdefault(key, []).append(entry["artist_folder"])

        for key, sources in flat_targets.items():
            if len(sources) > 1:
                collision_scenarios.append({
                    "flat_path":      key,
                    "colliding_from": sources,
                    "resolution":     "artist suffix applied by _collision_safe_name",
                })

    # Rekordbox impact
    rekordbox_impact = {
        "path_references": "Rekordbox XML uses absolute paths read from ID3 tags — unaffected by folder restructure",
        "playlist_buckets": "Bucketing uses genre/path keywords — flat structure compatible",
        "action_required":  "Re-export rekordbox XML after live migration to refresh absolute paths",
    }

    # Unresolved risks
    unresolved_risks = [
        {
            "risk":   "auto_downloader MusicBrainz branch (line 600) still appends artist",
            "impact": "MusicBrainz-sourced tracks land in artist subfolder; Spotify-sourced tracks land flat",
            "fix":    "Remove `clean_folder_name(artist)` join from auto_downloader.py:600",
        },
        {
            "risk":   "Existing Library structure has artist subfolders",
            "impact": "Mixed flat + artist-depth paths until migration runs",
            "fix":    "Run run_bulk_resolve.py --live then a one-shot flatten migration script",
        },
        {
            "risk":   "NeedsReview retains artist subfolders intentionally",
            "impact": "NeedsReview layout differs from Library layout — acceptable by design",
            "fix":    "None — NeedsReview artist folders are a triage aid",
        },
    ]

    total_files   = sum(e["file_count"] for e in existing_artist_folders)
    report = {
        "generated_at":            datetime.now(timezone.utc).isoformat(),
        "base_dir":                str(root),
        "summary": {
            "artist_folders_found":  len(existing_artist_folders),
            "total_files_to_flatten": total_files,
            "collision_scenarios":   len(collision_scenarios),
            "unpatched_builders":    sum(1 for b in affected_builders if "NOT patched" in b["status"]),
        },
        "affected_path_builders":   affected_builders,
        "existing_artist_folders":  existing_artist_folders,
        "collision_scenarios":      collision_scenarios,
        "rekordbox_impact":         rekordbox_impact,
        "unresolved_risks":         unresolved_risks,
    }

    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTS_DIR / "library_flatten_plan.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info(f"[flatten_plan] Report → {path}")
        report["report_path"] = str(path)
    except Exception as e:
        logger.warning(f"[flatten_plan] Failed to write report: {e}")

    return report


def flatten_existing_artist_folders(
    base_dir: Optional[Path] = None,
    dry_run: bool = True,
) -> dict:
    """
    Move MP3 files from legacy artist-depth Library folders up one level.

    Example:
        FROM Library/Indian/Bollywood/A.R. Rahman/song.mp3
        TO   Library/Indian/Bollywood/song.mp3

    Artist folders at depth-2 below Library/ that contain .mp3 files are
    detected and their contents flattened.  Empty artist folders are removed
    after successful moves.  A rollback manifest is written so moves can be
    reversed manually if needed.

    Args:
        base_dir: Root download dir. Defaults to config.BASE_DOWNLOAD_DIR.
        dry_run:  When True (default) log intended moves without executing.

    Returns:
        {
          artist_folders_found, files_moved, collisions_resolved,
          folders_removed, failures, dry_run, report_path
        }
    """
    from services.organizer_service import _collision_safe_name

    root        = Path(base_dir) if base_dir else BASE_DIR
    library_dir = root / "Library"
    label       = "[DRY] " if dry_run else ""

    artist_folders_found: list[str] = []
    files_moved:          list[dict] = []
    collisions_resolved:  list[dict] = []
    folders_removed:      list[str]  = []
    failures:             list[dict] = []
    rollback:             list[dict] = []

    if not library_dir.is_dir():
        logger.warning(f"[flatten] Library dir not found: {library_dir}")
        return _flatten_report(
            artist_folders_found, files_moved, collisions_resolved,
            folders_removed, failures, rollback, dry_run,
        )

    # Collect artist-depth dirs: depth 2 relative to Library/ (family/subgenre/artist)
    artist_depth_dirs: list[Path] = []
    for candidate in sorted(library_dir.rglob("*")):
        if not candidate.is_dir():
            continue
        rel   = candidate.relative_to(library_dir)
        depth = len(rel.parts)
        if depth != 3:
            continue
        if list(candidate.glob("*.mp3")):
            artist_depth_dirs.append(candidate)

    logger.info(
        f"[flatten] {label}Found {len(artist_depth_dirs)} artist-depth folder(s) "
        f"with MP3 files to flatten"
    )

    for artist_dir in artist_depth_dirs:
        flat_target = artist_dir.parent   # one level up: Library/family/subgenre
        mp3s        = list(artist_dir.glob("*.mp3"))
        artist_folders_found.append(str(artist_dir.relative_to(root)))
        folder_move_count = 0
        folder_fail_count = 0

        for mp3 in mp3s:
            tags = _read_id3_tags(str(mp3))
            artist_name = tags.get("artist", "") or artist_dir.name
            spotify_id  = tags.get("spotify_id", "")

            dest_path = _collision_safe_name(flat_target, mp3.name, artist_name, spotify_id)
            collided  = dest_path.name != mp3.name

            if dry_run:
                logger.info(
                    f"[flatten][DRY] {mp3.relative_to(root)} → "
                    f"{dest_path.relative_to(root)}"
                    + (" [collision resolved]" if collided else "")
                )
                entry = {
                    "src":      str(mp3.relative_to(root)),
                    "dest":     str(dest_path.relative_to(root)),
                    "collided": collided,
                    "dry_run":  True,
                }
                files_moved.append(entry)
                if collided:
                    collisions_resolved.append(entry)
                folder_move_count += 1
                continue

            # Live move
            try:
                flat_target.mkdir(parents=True, exist_ok=True)

                src_dev  = os.stat(mp3).st_dev
                dest_dev = os.stat(flat_target).st_dev
                if src_dev == dest_dev:
                    os.replace(mp3, dest_path)
                else:
                    shutil.copy2(mp3, dest_path)
                    if os.path.getsize(dest_path) != os.path.getsize(mp3):
                        os.remove(dest_path)
                        raise OSError("Cross-device copy size mismatch")
                    os.remove(mp3)

                entry = {
                    "src":      str(mp3.relative_to(root)),
                    "dest":     str(dest_path.relative_to(root)),
                    "collided": collided,
                    "dry_run":  False,
                }
                files_moved.append(entry)
                rollback.append({
                    "dest": str(dest_path),
                    "src":  str(mp3),
                })
                if collided:
                    collisions_resolved.append(entry)

                # Update library_index
                try:
                    from database import get_library_index_collection
                    col = get_library_index_collection()
                    if col is not None:
                        new_folder = str(flat_target.relative_to(root))
                        col.update_one(
                            {"final_path": str(mp3)},
                            {"$set": {
                                "final_path":   str(dest_path),
                                "filename":     dest_path.name,
                                "genre_folder": new_folder,
                            }},
                        )
                except Exception as idx_err:
                    logger.warning(f"[flatten] library_index update skipped: {idx_err}")

                logger.info(
                    f"[flatten] {mp3.relative_to(root)} → {dest_path.relative_to(root)}"
                    + (" [collision]" if collided else "")
                )
                folder_move_count += 1

            except Exception as exc:
                logger.error(f"[flatten] Failed to move {mp3}: {exc}")
                failures.append({"file": str(mp3.relative_to(root)), "error": str(exc)})
                folder_fail_count += 1

        # Remove artist folder if now empty and all moves succeeded
        if not dry_run and folder_fail_count == 0 and folder_move_count > 0:
            try:
                remaining = list(artist_dir.iterdir())
                if not remaining:
                    artist_dir.rmdir()
                    folders_removed.append(str(artist_dir.relative_to(root)))
                    logger.info(f"[flatten] Removed empty folder: {artist_dir.relative_to(root)}")
                else:
                    logger.warning(
                        f"[flatten] Artist folder not empty after moves "
                        f"({len(remaining)} item(s) remain): {artist_dir.relative_to(root)}"
                    )
            except Exception as exc:
                logger.warning(f"[flatten] Could not remove {artist_dir}: {exc}")

    return _flatten_report(
        artist_folders_found, files_moved, collisions_resolved,
        folders_removed, failures, rollback, dry_run,
    )


def _flatten_report(
    artist_folders_found: list,
    files_moved:          list,
    collisions_resolved:  list,
    folders_removed:      list,
    failures:             list,
    rollback:             list,
    dry_run:              bool,
) -> dict:
    report = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "dry_run":             dry_run,
        "summary": {
            "artist_folders_found": len(artist_folders_found),
            "files_moved":          len(files_moved),
            "collisions_resolved":  len(collisions_resolved),
            "folders_removed":      len(folders_removed),
            "failures":             len(failures),
        },
        "artist_folders_found": artist_folders_found,
        "files_moved":          files_moved,
        "collisions_resolved":  collisions_resolved,
        "folders_removed":      folders_removed,
        "failures":             failures,
        "rollback_manifest":    rollback,
    }

    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTS_DIR / "library_flatten_execution.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info(f"[flatten] Report → {path}")
        report["report_path"] = str(path)
    except Exception as e:
        logger.warning(f"[flatten] Failed to write report: {e}")
        report["report_path"] = ""

    return {
        "artist_folders_found": len(artist_folders_found),
        "files_moved":          len(files_moved),
        "collisions_resolved":  len(collisions_resolved),
        "folders_removed":      len(folders_removed),
        "failures":             len(failures),
        "dry_run":              dry_run,
        "report_path":          report.get("report_path", ""),
    }
