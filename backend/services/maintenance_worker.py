"""
Maintenance Worker — Phase 16
==============================
Background daemon thread performing rate-limited, non-blocking
self-healing operations on the DJ library.

Design goals
------------
• Never blocks downloads or monopolizes locks
• Each task has its own cooldown window; failures trigger exponential backoff
• Idempotent: all repairs are safe to re-run
• Additive only: no files are deleted without explicit operator action

Tasks (evaluated in priority order each cycle)
-----------------------------------------------
1. orphan_manifests   — retry manifests whose staged file is gone → dead/
2. dead_letter_report — count dead-letter manifests, record metric
3. stale_staging      — warn about files stuck in Ingest/Staging > 30 min
4. needs_review_audit — warn about NeedsReview stuck > 7 days
5. fingerprint_new    — compute fingerprints for unfingerprinted tracks (10/run)
6. reconcile          — dry-run reconcile, record issue count as metric

Lifecycle
---------
    from services.maintenance_worker import start_maintenance, stop_maintenance

    start_maintenance()   # call once at app startup (idempotent)
    stop_maintenance()    # call at shutdown

Environment
-----------
    MAINTENANCE_INTERVAL=300   seconds between full cycles (default 300)
    MAINTENANCE_ENABLED=1      set to "0" to disable (e.g. in tests)
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

_BACKEND = Path(__file__).resolve().parent.parent


# ── Task descriptor ───────────────────────────────────────────────────────────

@dataclass
class _Task:
    name: str
    fn: Callable[[], None]
    cooldown_sec: float
    _last_run: float = field(default=0.0, init=False)
    _error_count: int = field(default=0, init=False)
    _last_error: str = field(default="", init=False)
    _runs: int = field(default=0, init=False)

    def is_ready(self) -> bool:
        backoff = min(self.cooldown_sec * (2 ** min(self._error_count, 5)), 1800.0)
        return (time.monotonic() - self._last_run) >= backoff

    def run(self) -> bool:
        try:
            self.fn()
            self._error_count = 0
            self._last_error = ""
            self._runs += 1
            return True
        except Exception as exc:
            self._error_count += 1
            self._last_error = str(exc)
            return False
        finally:
            self._last_run = time.monotonic()

    def as_dict(self) -> dict:
        return {
            "runs": self._runs,
            "error_count": self._error_count,
            "last_error": self._last_error,
        }


# ── Worker ────────────────────────────────────────────────────────────────────

class MaintenanceWorker:
    _POLL_SEC = int(os.getenv("MAINTENANCE_INTERVAL", "300"))
    _ENABLED = os.getenv("MAINTENANCE_ENABLED", "1") not in ("0", "false", "no")

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tasks: list[_Task] = self._register_tasks()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._ENABLED:
            logger.info("[maintenance] Disabled via MAINTENANCE_ENABLED=0")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="maintenance-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[maintenance] Started — poll interval {self._POLL_SEC}s, "
                    f"{len(self._tasks)} tasks registered")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def trigger(self, task_name: str) -> bool:
        """Force-run a named task immediately (useful for tests and manual repairs)."""
        for task in self._tasks:
            if task.name == task_name:
                task._last_run = 0.0  # bypass cooldown
                ok = task.run()
                if not ok:
                    logger.warning(f"[maintenance] trigger({task_name}) failed: {task._last_error}")
                return ok
        logger.warning(f"[maintenance] Unknown task: {task_name}")
        return False

    def status(self) -> dict:
        return {
            "enabled": self._ENABLED,
            "running": bool(self._thread and self._thread.is_alive()),
            "tasks": {t.name: t.as_dict() for t in self._tasks},
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            for task in self._tasks:
                if self._stop.is_set():
                    break
                if task.is_ready():
                    logger.debug(f"[maintenance] Running: {task.name}")
                    ok = task.run()
                    if not ok:
                        logger.warning(
                            f"[maintenance] {task.name} failed "
                            f"(consecutive #{task._error_count}): {task._last_error}"
                        )
            # Sleep in short increments so stop() responds quickly
            self._stop.wait(timeout=min(self._POLL_SEC, 60))

    # ── Task registry ─────────────────────────────────────────────────────────

    def _register_tasks(self) -> list[_Task]:
        return [
            _Task("orphan_manifests",         self._task_orphan_manifests,         cooldown_sec=300),
            _Task("dead_letter_report",       self._task_dead_letter_report,        cooldown_sec=900),
            _Task("stale_staging",            self._task_stale_staging,             cooldown_sec=300),
            _Task("needs_review_audit",       self._task_needs_review_audit,        cooldown_sec=3600),
            _Task("fingerprint_new",          self._task_fingerprint_new,           cooldown_sec=600),
            _Task("reconcile",                self._task_reconcile,                 cooldown_sec=1800),
            _Task("reclassify_needs_review",  self._task_reclassify,                cooldown_sec=3600),
            _Task("retag_catchall",           self._task_retag_catchall,            cooldown_sec=3600),
            _Task("ingestion_cycle_report",   self._task_ingestion_cycle_report,    cooldown_sec=300),
        ]

    # ── Task implementations ──────────────────────────────────────────────────

    def _task_orphan_manifests(self) -> None:
        """Move retry manifests whose staged_path no longer exists to dead/."""
        from config import config
        retry_dir = Path(config.BASE_DOWNLOAD_DIR) / "Ingest" / ".retry_queue"
        if not retry_dir.is_dir():
            return
        dead_dir = retry_dir / "dead"
        dead_dir.mkdir(exist_ok=True)
        moved = 0
        for mf in retry_dir.glob("*.json"):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                staged = data.get("staged_path", "")
                if staged and not Path(staged).is_file():
                    mf.rename(dead_dir / mf.name)
                    moved += 1
            except Exception:
                continue
        if moved:
            logger.info(f"[maintenance] Moved {moved} orphan manifest(s) → dead/")

    def _task_dead_letter_report(self) -> None:
        """Warn and record a metric if dead-letter manifests exist."""
        from config import config
        dead_dir = (Path(config.BASE_DOWNLOAD_DIR) / "Ingest"
                    / ".retry_queue" / "dead")
        count = len(list(dead_dir.glob("*.json"))) if dead_dir.is_dir() else 0
        if count:
            logger.warning(f"[maintenance] Dead-letter queue: {count} manifest(s) need attention")
        try:
            from services import metrics_service as ms
            ms.record_value(ms.M_RETRY_QUEUE_DEPTH, float(count),
                            tags={"queue": "dead_letter"})
        except Exception:
            pass

    def _task_stale_staging(self) -> None:
        """Warn about files stuck in Ingest/Staging for more than 30 minutes."""
        from config import config
        staging_dir = Path(config.BASE_DOWNLOAD_DIR) / "Ingest" / "Staging"
        if not staging_dir.is_dir():
            return
        now = time.time()
        threshold = 30 * 60
        stale = [
            f for f in staging_dir.iterdir()
            if f.is_file() and (now - f.stat().st_mtime) > threshold
        ]
        if stale:
            logger.warning(
                f"[maintenance] {len(stale)} staging file(s) stuck > 30 min: "
                f"{[f.name for f in stale[:5]]}"
            )

    def _task_needs_review_audit(self) -> None:
        """Warn about tracks in NeedsReview older than 7 days."""
        from config import config
        nr_dir = Path(config.BASE_DOWNLOAD_DIR) / "NeedsReview"
        if not nr_dir.is_dir():
            return
        now = time.time()
        threshold = 7 * 24 * 3600
        stale = [
            f for f in nr_dir.rglob("*.mp3")
            if (now - f.stat().st_mtime) > threshold
        ]
        if stale:
            logger.warning(
                f"[maintenance] {len(stale)} NeedsReview track(s) stuck > 7 days"
            )
        try:
            from services import metrics_service as ms
            ms.record_value(ms.M_NEEDS_REVIEW_ROUTED, float(len(stale)),
                            tags={"source": "maintenance_audit"})
        except Exception:
            pass

    def _task_fingerprint_new(self) -> None:
        """Compute fingerprints for up to 10 library_index docs that have none."""
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is None:
                return
            docs = list(col.find(
                {"fingerprint_source": {"$exists": False}},
                {"identity_key": 1, "final_path": 1, "_id": 0},
            ).limit(10))
            if not docs:
                return
            from services.fingerprint_service import compute as fp_compute, index_fingerprint
            done = 0
            for doc in docs:
                fp = doc.get("final_path", "")
                ik = doc.get("identity_key", "")
                if fp and ik and Path(fp).is_file():
                    result = fp_compute(fp)
                    if result.is_valid:
                        index_fingerprint(ik, result)
                        done += 1
            if done:
                logger.info(f"[maintenance] Fingerprinted {done} previously unindexed track(s)")
        except Exception as exc:
            raise RuntimeError(f"fingerprint_new: {exc}") from exc

    def _task_reconcile(self) -> None:
        """Dry-run reconciliation and publish issue count as a metric."""
        recon_path = _BACKEND / "reconcile_library_state.py"
        if not recon_path.exists():
            return
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_recon_maint", recon_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            report = mod.reconcile(dry_run=True, repair=False, reindex=False)
            issues = report.get("summary", {}).get("total_issues", 0)
            try:
                from services import metrics_service as ms
                ms.record_value(ms.M_RECON_ISSUES, float(issues))
            except Exception:
                pass
            if issues:
                logger.warning(f"[maintenance] Reconciliation: {issues} issue(s) detected")
            else:
                logger.info("[maintenance] Reconciliation: library clean")
        except Exception as exc:
            raise RuntimeError(f"reconcile task: {exc}") from exc

    def _task_reclassify(self) -> None:
        """Attempt to reclassify up to 20 NeedsReview tracks using improved routing."""
        try:
            from services.reclassification_service import run_reclassification
            report = run_reclassification(dry_run=False, limit=20)
            rerouted = report.get("rerouted", 0)
            still_unknown = report.get("still_unknown", 0)
            if rerouted:
                logger.info(f"[maintenance] Reclassification: {rerouted} track(s) moved out of NeedsReview")
            if still_unknown:
                logger.debug(f"[maintenance] Reclassification: {still_unknown} track(s) still unresolved")
        except Exception as exc:
            raise RuntimeError(f"reclassify task: {exc}") from exc

    def _task_retag_catchall(self) -> None:
        """
        Retry Gemini genre identification for files tagged routing_source=catchall
        in Library/Electronic/.  On success, move file to correct Library/{genre}/
        and clear the tag.  Processes up to 20 files per run to stay rate-limit safe.
        """
        from config import config
        import shutil as _shutil

        electronic_dir = Path(config.BASE_DOWNLOAD_DIR) / "Library" / "Electronic"
        if not electronic_dir.is_dir():
            return

        try:
            from mutagen.id3 import ID3, TXXX
        except ImportError:
            return

        candidates = [f for f in electronic_dir.glob("*.mp3") if f.is_file()]
        if not candidates:
            return

        # Filter to only catchall-tagged files
        catchall_files = []
        for f in candidates:
            try:
                tags = ID3(str(f))
                txxx = tags.get("TXXX:routing_source")
                if txxx and "catchall" in (txxx.text or []):
                    catchall_files.append(f)
            except Exception:
                continue

        if not catchall_files:
            return

        logger.info(f"[maintenance] retag_catchall: {len(catchall_files)} file(s) to retry")

        try:
            from services.genre_router import normalize_genre, _library_path
            from services.gemini_service import identify_audio, GeminiQuotaExceeded
        except ImportError as e:
            logger.debug(f"[maintenance] retag_catchall: missing dependency: {e}")
            return

        import time as _time
        from mutagen.id3 import ID3 as _ID3

        from services.genre_router import normalize_artist_key as _nak

        def _artist_lower(fp):
            try:
                return _nak(str(_ID3(str(fp)).get("TPE1", "")))
            except Exception:
                return ""

        # Sort: override-covered tracks first so they always move even when Gemini quota is gone
        def _sort_key(fp):
            a = _artist_lower(fp)
            return (0 if config.ARTIST_GENRE_OVERRIDE.get(a) else 1, fp.name)

        catchall_files.sort(key=_sort_key)

        moved = 0
        for fp in catchall_files[:30]:  # raised from 5 — most tracks now route without Gemini
            try:
                _tags = _ID3(str(fp))
                artist_name = str(_tags.get("TPE1", "")).strip()
                title_tag = str(_tags.get("TIT2", "")).strip()
                artist_lower = _nak(artist_name)

                genre_path = ""
                route_source = "unknown"

                # ── 1. ARTIST_GENRE_OVERRIDE ─────────────────────────────────
                override_genre = config.ARTIST_GENRE_OVERRIDE.get(artist_lower) if artist_lower else None
                if override_genre:
                    canonical = normalize_genre(override_genre)
                    genre_path = _library_path(canonical) if canonical else ""
                    route_source = "artist_override"

                # ── 2. Spotify artist search ─────────────────────────────────
                if not genre_path and artist_lower not in ("unknown", "electronic", ""):
                    try:
                        from services.spotify_service import get_spotify_service as _get_sp
                        from services.genre_router import resolve_genre_folder_with_confidence as _rgfc
                        _sp = _get_sp()
                        if _sp:
                            _res = _sp.sp.search(q=artist_name, type="artist", limit=1)
                            _items = _res.get("artists", {}).get("items", [])
                            _aid = _items[0]["id"] if _items else ""
                            folder, conf, src = _rgfc(_aid, artist_name, _sp.sp)
                            if folder.startswith("Library/") and folder != "Library/Electronic" and conf >= 0.5:
                                genre_path = folder
                                route_source = src
                    except Exception:
                        pass

                # ── 3. Spotify title-only search ─────────────────────────────
                if not genre_path and title_tag:
                    try:
                        from services.spotify_service import get_spotify_service as _get_sp
                        from services.genre_router import resolve_genre_folder_with_confidence as _rgfc
                        from mutagen.id3 import TPE1 as _TPE1
                        _sp = _get_sp()
                        if _sp:
                            _res = _sp.sp.search(q=f"track:{title_tag}", type="track", limit=5)
                            for _t in _res.get("tracks", {}).get("items", []):
                                _sp_artist = _t.get("artists", [{}])[0].get("name", "")
                                _sp_aid = _t.get("artists", [{}])[0].get("id", "")
                                if not _sp_artist:
                                    continue
                                folder, conf, src = _rgfc(_sp_aid, _sp_artist, _sp.sp)
                                if folder.startswith("Library/") and folder != "Library/Electronic" and conf >= 0.5:
                                    genre_path = folder
                                    route_source = f"spotify_title/{src}"
                                    try:
                                        _tags["TPE1"] = _TPE1(encoding=3, text=_sp_artist)
                                        _tags.save(str(fp))
                                    except Exception:
                                        pass
                                    break
                    except Exception:
                        pass

                # ── 4. Last.fm (free, no quota) ──────────────────────────────
                if not genre_path and title_tag and artist_name:
                    try:
                        from services.lastfm_service import lookup_genre as _lfm
                        raw = _lfm(title_tag, artist_name)
                        if raw:
                            canonical = normalize_genre(raw)
                            _lp = _library_path(canonical) if canonical else ""
                            if _lp and _lp != "Library/Electronic":
                                genre_path = _lp
                                route_source = "lastfm"
                    except Exception:
                        pass

                # ── 5. MusicBrainz search (free, no quota) ───────────────────
                if not genre_path and title_tag:
                    try:
                        from services.musicbrainz_service import lookup_by_search as _mb_search
                        mb_raw = _mb_search(title_tag, artist_name)
                        if mb_raw:
                            canonical = normalize_genre(mb_raw)
                            _lp = _library_path(canonical) if canonical else ""
                            if _lp and _lp != "Library/Electronic":
                                genre_path = _lp
                                route_source = "musicbrainz"
                    except Exception:
                        pass

                # ── 6. AcoustID fingerprint (free with key) ───────────────────
                if not genre_path:
                    try:
                        from services.musicbrainz_service import lookup_by_fingerprint as _mb_fp
                        fp_raw = _mb_fp(str(fp))
                        if fp_raw:
                            canonical = normalize_genre(fp_raw)
                            _lp = _library_path(canonical) if canonical else ""
                            if _lp and _lp != "Library/Electronic":
                                genre_path = _lp
                                route_source = "acoustid"
                    except Exception:
                        pass

                # ── 7. Gemini (last resort — costs daily quota) ───────────────
                if not genre_path:
                    from services.gemini_service import remaining_quota as _rq
                    if _rq() > 0:
                        gemini = identify_audio(str(fp))
                        raw = gemini.get("gemini_genre", "")
                        if raw:
                            canonical = normalize_genre(raw)
                            genre_path = _library_path(canonical) if canonical else ""
                            route_source = "gemini"
                    # else: quota 0 — leave this file in Electronic, try next

                if not genre_path or genre_path == "Library/Electronic":
                    continue  # still unknown — leave for next cycle

                dest_dir = Path(config.BASE_DOWNLOAD_DIR) / genre_path
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_fp = dest_dir / fp.name
                _shutil.move(str(fp), str(dest_fp))
                # Remove catchall tag from moved file
                try:
                    tags = ID3(str(dest_fp))
                    tags.delall("TXXX:routing_source")
                    tags.save()
                except Exception:
                    pass
                # Update MongoDB library index
                try:
                    from database import get_library_index_collection
                    col = get_library_index_collection()
                    if col is not None:
                        col.update_one(
                            {"final_path": str(fp)},
                            {"$set": {"final_path": str(dest_fp), "genre_folder": genre_path}},
                        )
                except Exception:
                    pass
                logger.info(f"[maintenance] retag_catchall: {fp.name} → {genre_path} via {route_source}")
                moved += 1
                if route_source == "gemini":
                    _time.sleep(4)  # rate limit only when Gemini was used
            except GeminiQuotaExceeded:
                logger.warning(f"[maintenance] retag_catchall: Gemini quota hit mid-file ({moved} moved so far) — continuing without Gemini")
                continue  # don't abort the batch — next files may resolve via steps 1-6
            except Exception as exc:
                logger.debug(f"[maintenance] retag_catchall error for {fp.name}: {exc}")
                continue

        if moved:
            logger.info(f"[maintenance] retag_catchall: moved {moved} file(s) out of Electronic/")

    def _task_ingestion_cycle_report(self) -> None:
        """
        Snapshot ingestion health after each cycle and validate flat-library
        structural assumptions.  Persists the last 20 cycles to
        reports/ingestion_cycle_report.json.
        """
        from datetime import datetime, timezone, timedelta
        from config import config

        root          = Path(config.BASE_DOWNLOAD_DIR)
        library_dir   = root / "Library"
        nr_dir        = root / "NeedsReview"
        quarantine_dir = root / "Quarantine"
        reports_dir   = _BACKEND / "reports"
        report_path   = reports_dir / "ingestion_cycle_report.json"
        window_min    = max(1, self._POLL_SEC // 60)
        now_utc       = datetime.now(timezone.utc)

        # ── Metrics from last window ───────────────────────────────────────
        routed_songs = duplicate_detections = collision_events = 0
        conf_dist: dict = {}
        low_conf_routes: list = []
        try:
            from services import metrics_service as ms
            routed_songs        = ms._sum_recent(ms.M_ORGANIZE_LATENCY,    window_min)
            duplicate_detections = ms._sum_recent(ms.M_DUPLICATE_PREVENTED, window_min)
            collision_events    = ms._sum_recent(ms.M_COLLISION_RESOLVED,   window_min)
            conf_dist           = ms._confidence_distribution(window_min)

            metrics_col = ms._get_metrics_col()
            if metrics_col is not None:
                cutoff = now_utc - timedelta(minutes=window_min)
                low_conf_routes = [
                    {"confidence": round(d["value"], 3), **d.get("tags", {})}
                    for d in metrics_col.find(
                        {"name": ms.M_GENRE_CONFIDENCE,
                         "value": {"$lt": 0.5},
                         "ts": {"$gte": cutoff}},
                        {"value": 1, "tags": 1, "_id": 0},
                    ).sort("value", 1).limit(20)
                ]
        except Exception as e:
            logger.debug(f"[maintenance] cycle_report metrics error: {e}")

        # ── NeedsReview snapshot ───────────────────────────────────────────
        nr_total = 0
        top_unresolved_artists: list = []
        if nr_dir.is_dir():
            artist_counts: dict[str, int] = {}
            for f in nr_dir.rglob("*.mp3"):
                nr_total += 1
                bucket = f.parent.name if f.parent != nr_dir else "_root"
                artist_counts[bucket] = artist_counts.get(bucket, 0) + 1
            top_unresolved_artists = sorted(
                [{"artist": a, "count": c} for a, c in artist_counts.items()],
                key=lambda x: x["count"],
                reverse=True,
            )[:10]

        # ── Quarantine snapshot ────────────────────────────────────────────
        quarantine_total = (
            sum(1 for _ in quarantine_dir.rglob("*.mp3"))
            if quarantine_dir.is_dir() else 0
        )

        # ── Failed metadata lookups (library_index docs missing spotify_id) ─
        failed_metadata = 0
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                failed_metadata = col.count_documents(
                    {"$or": [
                        {"spotify_id": {"$exists": False}},
                        {"spotify_id": ""},
                    ]}
                )
        except Exception:
            pass

        # ── Flat-library structural validation ────────────────────────────
        artist_depth_violations: list[str] = []
        nesting_violations:      list[str] = []
        orphaned_files:          list[str] = []

        if library_dir.is_dir():
            for d in sorted(library_dir.rglob("*")):
                if not d.is_dir():
                    continue
                rel   = d.relative_to(library_dir)
                depth = len(rel.parts)
                # depth-3 under Library/ = family/subgenre/artist (forbidden with mp3s)
                if depth == 3 and list(d.glob("*.mp3")):
                    artist_depth_violations.append(str(d.relative_to(root)))
                # Library/…/Library/… nesting
                if str(d.relative_to(root)).count("Library") > 1:
                    nesting_violations.append(str(d.relative_to(root)))

            for f in library_dir.rglob("*.mp3"):
                # valid flat path: Library/genre/file.mp3 → 2 parts under Library/
                if len(f.relative_to(library_dir).parts) != 2:
                    orphaned_files.append(str(f.relative_to(root)))

        flat_valid = (
            not artist_depth_violations
            and not nesting_violations
            and not orphaned_files
        )

        if not flat_valid:
            logger.warning(
                f"[maintenance] Flat-library violations — "
                f"artist_depth={len(artist_depth_violations)}, "
                f"nesting={len(nesting_violations)}, "
                f"orphaned={len(orphaned_files)}"
            )

        # ── Assemble cycle snapshot ────────────────────────────────────────
        cycle = {
            "generated_at":   now_utc.isoformat(),
            "window_minutes": window_min,
            "ingestion": {
                "routed_songs":           routed_songs,
                "needs_review_total":     nr_total,
                "duplicate_detections":   duplicate_detections,
                "quarantine_total":       quarantine_total,
                "failed_metadata_lookups": failed_metadata,
                "collision_safe_events":  collision_events,
            },
            "confidence": {
                "distribution":       conf_dist,
                "low_confidence_routes": low_conf_routes,
            },
            "unresolved": {
                "top_artists": top_unresolved_artists,
            },
            "flat_library_validation": {
                "artist_depth_folders_with_mp3s": artist_depth_violations,
                "library_nesting_violations":     nesting_violations,
                "orphaned_files":                 orphaned_files,
                "valid":                          flat_valid,
            },
        }

        # ── Persist rolling last-20 window ────────────────────────────────
        try:
            reports_dir.mkdir(parents=True, exist_ok=True)
            cycles: list = []
            if report_path.exists():
                try:
                    cycles = json.loads(
                        report_path.read_text(encoding="utf-8")
                    ).get("cycles", [])
                except Exception:
                    cycles = []
            cycles.insert(0, cycle)
            cycles = cycles[:20]
            report_path.write_text(
                json.dumps({"cycle_count": len(cycles), "cycles": cycles},
                           indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[maintenance] Failed to write ingestion_cycle_report: {e}")

        logger.info(
            f"[maintenance] Cycle report: routed={routed_songs}, "
            f"nr={nr_total}, dupes={duplicate_detections}, "
            f"collisions={collision_events}, flat_valid={flat_valid}"
        )


# ── Module-level singleton ────────────────────────────────────────────────────

_worker: MaintenanceWorker | None = None
_start_lock = threading.Lock()


def get_worker() -> MaintenanceWorker:
    global _worker
    if _worker is None:
        with _start_lock:
            if _worker is None:
                _worker = MaintenanceWorker()
    return _worker


def start_maintenance() -> None:
    """Start the maintenance daemon (idempotent — safe to call multiple times)."""
    get_worker().start()


def stop_maintenance(timeout: float = 5.0) -> None:
    """Signal the maintenance daemon to stop and wait up to *timeout* seconds."""
    global _worker
    if _worker is not None:
        _worker.stop(timeout=timeout)
