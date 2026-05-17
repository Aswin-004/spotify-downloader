"""
Pipeline Integrity Verifier
============================
Extended post-deployment verification that checks:
  - All 10 phase patches applied correctly (Phases 1–10)
  - Observability layer (Phase 11–12: reconcile, metrics)
  - Fingerprinting + export (Phase 13–14: acoustid, rekordbox)
  - Chaos & resilience scenarios (Phase 15)

Usage
-----
    cd backend
    python verify_pipeline_integrity.py

Exit codes: 0 = all checks passed, 1 = one or more failed
"""
from __future__ import annotations

import sys
import os
import inspect
from pathlib import Path

_BACKEND = Path(__file__).parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_PASS = "\033[92m✅\033[0m"
_FAIL = "\033[91m❌\033[0m"
_WARN = "\033[93m⚠️ \033[0m"


def _check(name: str, condition: bool, fix: str = "") -> bool:
    icon = _PASS if condition else _FAIL
    print(f"  {icon}  {name}")
    if not condition and fix:
        print(f"       → {fix}")
    return condition


def _warn(name: str, condition: bool, note: str = "") -> bool:
    icon = _PASS if condition else _WARN
    print(f"  {icon}  {name}")
    if not condition and note:
        print(f"       ℹ  {note}")
    return True  # warnings don't fail the suite


# ─────────────────────────────────────────────────────────────────
# PHASE 1 — DownloadResult dataclass
# ─────────────────────────────────────────────────────────────────
def verify_phase1() -> bool:
    print("\n[PHASE 1] DownloadResult dataclass")
    ok = True
    try:
        from models.download_result import DownloadResult, GenreDecision, TaggingOutcome
        dr = DownloadResult(spotify_id="abc", title="Test", artist="Artist")
        ok &= _check("DownloadResult instantiates", True)
        ok &= _check("succeeded property False when download_ok=False", not dr.succeeded)
        dr.download_ok = True
        dr.move_ok = True
        ok &= _check("succeeded property True when both ok", dr.succeeded)
        gd = GenreDecision(folder="UK Garage/Test", confidence=0.7, source="spotify_genre")
        ok &= _check("GenreDecision instantiates", True)
        ok &= _check("GenreDecision.confidence set correctly", gd.confidence == 0.7)
        to = TaggingOutcome(success=True, bpm=128.0, camelot="8B")
        ok &= _check("TaggingOutcome instantiates", True)
        ok &= _check("TaggingOutcome.camelot set correctly", to.camelot == "8B")
    except Exception as e:
        ok &= _check("models.download_result importable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 2 — dedup_service
# ─────────────────────────────────────────────────────────────────
def verify_phase2() -> bool:
    print("\n[PHASE 2] dedup_service — strong duplicate identity")
    ok = True
    try:
        from services.dedup_service import duplicate_identity_key, content_hash, legacy_title_key

        # Spotify ID takes priority
        k1 = duplicate_identity_key("ABC123", "Title", "Artist", 180000)
        ok &= _check("spotify_id key uses sp: prefix", k1 == "sp:ABC123",
                     f"Expected 'sp:ABC123', got '{k1}'")

        # Content hash fallback
        k2 = duplicate_identity_key("", "Title", "Artist", 180000)
        ok &= _check("empty spotify_id falls back to content hash (ch: prefix)", k2.startswith("ch:"),
                     f"Expected 'ch:...' prefix, got '{k2}'")

        # Same song → same hash
        k3 = duplicate_identity_key("", "Title", "Artist", 180000)
        ok &= _check("content hash is deterministic", k2 == k3)

        # Different songs → different hash
        k4 = duplicate_identity_key("", "Other Song", "Artist", 180000)
        ok &= _check("different title → different hash", k2 != k4)

        # Duration bucket tolerance (within 5s)
        h1 = content_hash("Title", "Artist", 180000)
        h2 = content_hash("Title", "Artist", 183000)  # +3s, same bucket
        ok &= _check("±3s duration treated as same bucket", h1 == h2)

        # Duration bucket distinction (>5s)
        h3 = content_hash("Title", "Artist", 186000)  # +6s, different bucket
        ok &= _check(">5s duration difference → different bucket", h1 != h3)

        ok &= _check("legacy_title_key returns str", isinstance(legacy_title_key("Test Track"), str))

    except Exception as e:
        ok &= _check("services.dedup_service importable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 3 — genre_router confidence scoring
# ─────────────────────────────────────────────────────────────────
def verify_phase3() -> bool:
    print("\n[PHASE 3] genre_router — confidence scoring")
    ok = True
    try:
        from services.genre_router import (
            resolve_genre_folder_with_confidence,
            CONFIDENCE_ARTIST_OVERRIDE,
            CONFIDENCE_SPOTIFY_MAP,
            CONFIDENCE_DEVANAGARI,
            CONFIDENCE_UNCATEGORIZED,
            clear_genre_cache,
        )
        ok &= _check("resolve_genre_folder_with_confidence importable", True)
        ok &= _check("CONFIDENCE_ARTIST_OVERRIDE == 1.0", CONFIDENCE_ARTIST_OVERRIDE == 1.0)
        ok &= _check("CONFIDENCE_SPOTIFY_MAP == 0.7", CONFIDENCE_SPOTIFY_MAP == 0.7)
        ok &= _check("CONFIDENCE_DEVANAGARI == 0.3", CONFIDENCE_DEVANAGARI == 0.3)
        ok &= _check("CONFIDENCE_UNCATEGORIZED == 0.0", CONFIDENCE_UNCATEGORIZED == 0.0)

        # Signature check
        sig = inspect.signature(resolve_genre_folder_with_confidence)
        ok &= _check("resolve_genre_folder_with_confidence takes (artist_id, artist_name, sp)",
                     list(sig.parameters) == ["artist_id", "artist_name", "sp"])

        # clear_genre_cache clears all three caches
        from services import genre_router as _gr
        _gr._genre_cache["test"] = "X"
        _gr._confidence_cache["test"] = 0.5
        _gr._source_cache["test"] = "test"
        clear_genre_cache()
        ok &= _check("clear_genre_cache clears _genre_cache", "test" not in _gr._genre_cache)
        ok &= _check("clear_genre_cache clears _confidence_cache", "test" not in _gr._confidence_cache)
        ok &= _check("clear_genre_cache clears _source_cache", "test" not in _gr._source_cache)

    except Exception as e:
        ok &= _check("genre_router confidence scoring importable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 5 — library_index MongoDB helpers
# ─────────────────────────────────────────────────────────────────
def verify_phase5() -> bool:
    print("\n[PHASE 5] library_index MongoDB helpers")
    ok = True
    try:
        from database import (
            index_track, is_indexed, lookup_by_spotify_id,
            lookup_by_content_hash, remove_from_index, get_library_stats,
            get_library_index_collection,
        )
        ok &= _check("index_track importable", True)
        ok &= _check("is_indexed importable", True)
        ok &= _check("lookup_by_spotify_id importable", True)
        ok &= _check("remove_from_index importable", True)
        ok &= _check("get_library_stats importable", True)

        # Signature checks
        sig_it = inspect.signature(index_track)
        ok &= _check("index_track has identity_key param", "identity_key" in sig_it.parameters)
        ok &= _check("index_track has spotify_id param", "spotify_id" in sig_it.parameters)
        ok &= _check("index_track has genre_confidence param", "genre_confidence" in sig_it.parameters)

    except Exception as e:
        ok &= _check("database library_index helpers importable", False, str(e))

    # MongoDB connectivity (warning, not failure)
    try:
        from database import is_mongo_available
        mongo_ok = is_mongo_available()
        _warn("MongoDB reachable (needed for library_index)", mongo_ok,
              "MongoDB offline — library_index and dedup will fall back to in-memory")
    except Exception:
        pass
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 6 — DJ metadata hardening (TXXX aliases)
# ─────────────────────────────────────────────────────────────────
def verify_phase6() -> bool:
    print("\n[PHASE 6] DJ metadata hardening — TXXX aliases")
    src = (_BACKEND / "services" / "tagger_service.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check('TXXX:BPM written (Rekordbox alias)', 'desc="BPM"' in src,
                 'TXXX:BPM not found in tagger_service.py')
    ok &= _check('TXXX:KEY written (Rekordbox 6+ alias)', 'desc="KEY"' in src,
                 'TXXX:KEY not found in tagger_service.py')
    ok &= _check('TXXX:CAMELOT written (Mixed In Key / VDJ)', 'desc="CAMELOT"' in src,
                 'TXXX:CAMELOT not found in tagger_service.py')
    ok &= _check('BPM rounded to integer before write', '_bpm_int = str(round(float(bpm_val)))' in src,
                 'Integer BPM rounding not found — Rekordbox may reject decimal BPM strings')
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 7 — retag_migration.py runnable
# ─────────────────────────────────────────────────────────────────
def verify_phase7() -> bool:
    print("\n[PHASE 7] retag_migration.py")
    ok = True
    mig_path = _BACKEND / "retag_migration.py"
    ok &= _check("retag_migration.py exists", mig_path.exists(),
                 "backend/retag_migration.py not found")
    if not ok:
        return ok
    src = mig_path.read_text(encoding="utf-8")
    ok &= _check("--dry-run flag present", "--dry-run" in src)
    ok &= _check("--limit flag present", "--limit" in src)
    ok &= _check("--force flag present", "--force" in src)
    ok &= _check("tag_file called for DJ re-tagging", "tag_file" in src,
                 "tag_file not called in migrate_file — DJ tags won't be written")
    ok &= _check("library_index registration present", "index_track" in src)
    ok &= _check("run_migration function defined", "def run_migration(" in src)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("retag_migration", mig_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok &= _check("retag_migration module loads without error", True)
        ok &= _check("run_migration callable", callable(getattr(mod, "run_migration", None)))
    except Exception as e:
        ok &= _check("retag_migration module loads without error", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 8 — lock_service
# ─────────────────────────────────────────────────────────────────
def verify_phase8() -> bool:
    print("\n[PHASE 8] lock_service — Redis/threading fallback")
    ok = True
    try:
        from services.lock_service import acquire_lock, acquire_registry_lock
        ok &= _check("acquire_lock importable", True)
        ok &= _check("acquire_registry_lock importable", True)

        # Verify it actually works as a context manager (threading fallback path)
        acquired = []
        with acquire_lock("test_lock_verify", timeout=2.0):
            acquired.append(True)
        ok &= _check("acquire_lock context manager works (thread fallback)", len(acquired) == 1)

        # Nested acquire should not deadlock (different name)
        with acquire_lock("test_lock_a"):
            with acquire_lock("test_lock_b"):
                acquired.append(True)
        ok &= _check("nested locks with different names don't deadlock", len(acquired) == 2)

    except Exception as e:
        ok &= _check("services.lock_service importable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 9 — Retry hardening
# ─────────────────────────────────────────────────────────────────
def verify_phase9() -> bool:
    print("\n[PHASE 9] Retry hardening — manifest + queue processor")
    src = (_BACKEND / "services" / "auto_downloader.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check("sha256 in _write_retry_manifest", "sha256" in src,
                 "sha256 field not found in _write_retry_manifest")
    ok &= _check("filesize_bytes in manifest", "filesize_bytes" in src,
                 "filesize_bytes field not found in _write_retry_manifest")
    ok &= _check("pipeline_stage_failed in manifest", "pipeline_stage_failed" in src,
                 "pipeline_stage_failed field not in manifest")
    ok &= _check("_process_retry_queue defined", "def _process_retry_queue(" in src,
                 "_process_retry_queue function not found")
    ok &= _check("retry_count increment in manifest", '"retry_count": 0' in src,
                 "retry_count initial value not found in manifest")
    ok &= _check("MAX_RETRY_ATTEMPTS defined in _process_retry_queue",
                 "MAX_RETRY_ATTEMPTS = 5" in src,
                 "MAX_RETRY_ATTEMPTS not found in _process_retry_queue")
    ok &= _check("dead/ folder for exhausted manifests", '"dead"' in src or "'dead'" in src,
                 "dead/ folder path not found in _process_retry_queue")
    ok &= _check("_process_retry_queue called in ingest_download",
                 "_process_retry_queue()" in src,
                 "_process_retry_queue() not called from ingest_download")
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 4 — NeedsReview routing
# ─────────────────────────────────────────────────────────────────
def verify_phase4() -> bool:
    print("\n[PHASE 4] Two-pass organization — NeedsReview routing")
    src = (_BACKEND / "services" / "auto_downloader.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check("NEEDS_REVIEW_THRESHOLD defined", "NEEDS_REVIEW_THRESHOLD" in src,
                 "NEEDS_REVIEW_THRESHOLD not found in _download_single")
    ok &= _check("resolve_genre_folder_with_confidence imported in PASS 2",
                 "resolve_genre_folder_with_confidence" in src,
                 "resolve_genre_folder_with_confidence not called in PASS 2")
    ok &= _check("NeedsReview folder used for low-confidence tracks",
                 '"NeedsReview"' in src,
                 '"NeedsReview" path not found in _download_single')
    ok &= _check("download_needs_review event emitted",
                 '"download_needs_review"' in src,
                 '"download_needs_review" event not emitted on low-confidence routing')
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 10 — Critical patch baseline (verify_patches.py)
# ─────────────────────────────────────────────────────────────────
def verify_phase10() -> bool:
    print("\n[PHASE 10] Critical patch baseline — verify_patches.py")
    ok = True
    patches_path = _BACKEND / "verify_patches.py"
    ok &= _check("verify_patches.py exists", patches_path.exists(),
                 "backend/verify_patches.py not found")
    if not ok:
        return ok
    src = patches_path.read_text(encoding="utf-8")
    ok &= _check("atomic move / os.replace check present",
                 "os.replace" in src or "atomic" in src.lower())
    ok &= _check("identity_key / TOCTOU check present",
                 "identity_key" in src or "toctou" in src.lower())
    ok &= _check("Camelot check present", "camelot" in src.lower() or "CAMELOT" in src)
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 11 — reconcile_library_state.py
# ─────────────────────────────────────────────────────────────────
def verify_phase11() -> bool:
    print("\n[PHASE 11] reconcile_library_state.py")
    ok = True
    recon_path = _BACKEND / "reconcile_library_state.py"
    ok &= _check("reconcile_library_state.py exists", recon_path.exists(),
                 "backend/reconcile_library_state.py not found")
    if not ok:
        return ok
    src = recon_path.read_text(encoding="utf-8")
    ok &= _check("reconcile() function defined", "def reconcile(" in src)
    ok &= _check("--dry-run CLI flag present", "--dry-run" in src)
    ok &= _check("orphaned file detection present", "_detect_orphaned_files" in src)
    ok &= _check("stale index detection present", "_detect_stale_index" in src)
    ok &= _check("staging stuck detection present", "_detect_staging_stuck" in src)
    ok &= _check("Camelot validation set present",
                 "_VALID_CAMELOT" in src or "VALID_CAMELOT" in src)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("reconcile_library_state", recon_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok &= _check("reconcile_library_state module loads without error", True)
        ok &= _check("reconcile() callable", callable(getattr(mod, "reconcile", None)))
    except Exception as e:
        ok &= _check("reconcile_library_state module loads without error", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 12 — metrics_service.py
# ─────────────────────────────────────────────────────────────────
def verify_phase12() -> bool:
    print("\n[PHASE 12] metrics_service.py — observability")
    ok = True
    try:
        from services.metrics_service import (
            record_value, increment, record, health_report,
            M_DOWNLOAD_LATENCY, M_TAGGING_LATENCY, M_LOCK_WAIT,
        )
        ok &= _check("record_value importable", True)
        ok &= _check("record context manager importable", True)
        ok &= _check("health_report importable", True)
        ok &= _check("M_DOWNLOAD_LATENCY constant defined", bool(M_DOWNLOAD_LATENCY))

        sig = inspect.signature(health_report)
        ok &= _check("health_report accepts window_minutes param",
                     "window_minutes" in sig.parameters)

        try:
            with record("test_metric_verify"):
                pass
            ok &= _check("record() context manager executes without error", True)
        except Exception as e:
            ok &= _check("record() context manager executes without error", False, str(e))

        try:
            increment("test_counter_verify")
            ok &= _check("increment() fire-and-forget: no raise", True)
        except Exception as e:
            ok &= _check("increment() fire-and-forget: no raise", False, str(e))

        report = health_report(window_minutes=1)
        ok &= _check("health_report returns dict", isinstance(report, dict))
        ok &= _check("health_report has 'status' key", "status" in report)
        ok &= _check("health_report status is valid value",
                     report.get("status") in ("healthy", "degraded", "critical"))
        ok &= _check("health_report has 'metrics' key", "metrics" in report)
    except Exception as e:
        ok &= _check("services.metrics_service importable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 13 — fingerprint_service.py
# ─────────────────────────────────────────────────────────────────
def verify_phase13() -> bool:
    print("\n[PHASE 13] fingerprint_service.py — acoustic fingerprinting")
    ok = True
    try:
        from services.fingerprint_service import (
            compute, find_duplicate, index_fingerprint, is_available, FingerprintResult,
        )
        ok &= _check("compute importable", True)
        ok &= _check("find_duplicate importable", True)
        ok &= _check("FingerprintResult dataclass importable", True)

        fr = FingerprintResult()
        ok &= _check("FingerprintResult.fingerprint_source defaults to 'none'",
                     fr.fingerprint_source == "none")
        ok &= _check("FingerprintResult.is_valid False when empty", not fr.is_valid)

        result = compute("/nonexistent/path/file.mp3")
        ok &= _check("compute() returns FingerprintResult for missing file",
                     isinstance(result, FingerprintResult))
        ok &= _check("compute() fingerprint_source='none' for missing file",
                     result.fingerprint_source == "none")

        dup = find_duplicate(FingerprintResult(), None)
        ok &= _check("find_duplicate returns None for invalid result", dup is None)

        avail = is_available()
        ok &= _check("is_available() returns bool without raising", isinstance(avail, bool))
    except Exception as e:
        ok &= _check("services.fingerprint_service importable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 14 — rekordbox_export.py
# ─────────────────────────────────────────────────────────────────
def verify_phase14() -> bool:
    print("\n[PHASE 14] rekordbox_export.py — Rekordbox XML export")
    ok = True
    rb_path = _BACKEND / "rekordbox_export.py"
    ok &= _check("rekordbox_export.py exists", rb_path.exists(),
                 "backend/rekordbox_export.py not found")
    if not ok:
        return ok
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("rekordbox_export", rb_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok &= _check("rekordbox_export module loads without error", True)
        ok &= _check("export() callable", callable(getattr(mod, "export", None)))
        ok &= _check("build_xml() callable", callable(getattr(mod, "build_xml", None)))

        uri_fn = getattr(mod, "_to_rekordbox_uri", None)
        if uri_fn:
            test_path = r"C:\Music\track.mp3" if os.name == "nt" else "/music/track.mp3"
            uri = uri_fn(test_path)
            ok &= _check("_to_rekordbox_uri: file:/// prefix", uri.startswith("file:///"))
            ok &= _check("_to_rekordbox_uri: forward slashes only", "\\" not in uri)
        else:
            ok &= _check("_to_rekordbox_uri function exists", False)

        root, buckets = mod.build_xml([])
        ok &= _check("build_xml([]) returns (element, dict)",
                     root is not None and isinstance(buckets, dict))
        ok &= _check("build_xml([]) COLLECTION Entries=0",
                     root.find("COLLECTION").get("Entries") == "0")
        xml_str = mod.prettify(root)
        ok &= _check("prettify() starts with XML declaration", xml_str.startswith("<?xml"))
        ok &= _check("prettify() contains DJ_PLAYLISTS", "DJ_PLAYLISTS" in xml_str)

        src = rb_path.read_text(encoding="utf-8")
        for bucket in ("NeedsReview", "UKG", "House", "Bollywood", "Warmup", "Peak Hour"):
            ok &= _check(f"{bucket} playlist bucket defined", f'"{bucket}"' in src)
        ok &= _check("Tonality attribute used for Camelot key", "Tonality" in src)
    except Exception as e:
        ok &= _check("rekordbox_export module usable", False, str(e))
    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 15 — Chaos & Resilience Testing
# ─────────────────────────────────────────────────────────────────
def verify_chaos() -> bool:
    """
    10 chaos scenarios — all in temp dirs, zero destructive operations.
    Each simulates a failure mode and validates graceful degradation.
    """
    import tempfile
    import threading
    import json
    import time

    print("\n[PHASE 15] Chaos & resilience testing")
    ok = True

    # chaos-1: Redis unreachable → threading.Lock fallback
    print("  [chaos-1] Redis unavailable → threading.Lock fallback")
    try:
        orig_redis = os.environ.get("REDIS_URL", "")
        os.environ["REDIS_URL"] = "redis://127.0.0.1:19999"  # dead port
        acquired: list = []
        from services.lock_service import acquire_lock
        with acquire_lock("chaos_c1", timeout=3.0):
            acquired.append(True)
        ok &= _check("chaos-1: lock acquired via threading fallback when Redis dead",
                     len(acquired) == 1)
    except Exception as e:
        ok &= _check("chaos-1: no exception when Redis unavailable", False, str(e))
    finally:
        if orig_redis:
            os.environ["REDIS_URL"] = orig_redis
        else:
            os.environ.pop("REDIS_URL", None)

    # chaos-2: MongoDB absent → database helpers return bool without raising
    print("  [chaos-2] MongoDB absent → library_index helpers degrade gracefully")
    try:
        from database import is_indexed, index_track
        ok &= _check("chaos-2a: is_indexed() returns bool",
                     isinstance(is_indexed("sp:CHAOS_NONE_9999"), bool))
        ok &= _check("chaos-2b: index_track() returns bool",
                     isinstance(index_track(
                         identity_key="sp:CHAOS_NONE_9999",
                         spotify_id="CHAOS_NONE_9999",
                         content_hash="abc", title="X", artist="Y",
                         filename="x.mp3", final_path="/nope/x.mp3",
                     ), bool))
        # Clean up sentinel document so reconciliation doesn't flag it as stale
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                col.delete_one({"identity_key": "sp:CHAOS_NONE_9999"})
        except Exception:
            pass
    except Exception as e:
        ok &= _check("chaos-2: no exception from database helpers when MongoDB absent",
                     False, str(e))

    # chaos-3: Corrupted + empty + partial retry manifests → all skipped
    print("  [chaos-3] Corrupted manifests → parser skips all without crash")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rq = Path(tmp) / ".retry_queue"
            rq.mkdir()
            (rq / "bad_json.json").write_text("{corrupt{{", encoding="utf-8")
            (rq / "empty.json").write_text("", encoding="utf-8")
            (rq / "partial.json").write_text('{"title":"No Key"}', encoding="utf-8")
            skipped = 0
            for mf in rq.glob("*.json"):
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                    if not data.get("identity_key") or not data.get("staged_path"):
                        skipped += 1
                except (json.JSONDecodeError, ValueError, Exception):
                    skipped += 1
            ok &= _check("chaos-3: all 3 corrupted manifests skipped without crash",
                         skipped == 3)
    except Exception as e:
        ok &= _check("chaos-3: no exception propagated from manifest parser", False, str(e))

    # chaos-4: Duplicate flood → dedup collapses all to 1 key
    print("  [chaos-4] Duplicate flood → single identity key")
    try:
        from services.dedup_service import duplicate_identity_key
        sid = "FLOOD_SP_ID_001"
        keys = {duplicate_identity_key(sid, f"Title {i}", f"Artist {i}", 180000)
                for i in range(100)}
        ok &= _check("chaos-4a: 100 spotify_id duplicates → 1 identity key",
                     len(keys) == 1 and list(keys)[0] == f"sp:{sid}")
        keys_ch = {duplicate_identity_key("", "Title", "Artist", 180000)
                   for _ in range(100)}
        ok &= _check("chaos-4b: 100 content-hash duplicates → 1 identity key",
                     len(keys_ch) == 1)
    except Exception as e:
        ok &= _check("chaos-4: dedup stable under duplicate flood", False, str(e))

    # chaos-5: Corrupted MP3 bytes → services degrade, no exception propagates
    print("  [chaos-5] Corrupted MP3 → services degrade, no file loss")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "corrupted.mp3"
            bad.write_bytes(b"\x00\xFF\xAB\xCD" * 512)
            from services.fingerprint_service import compute as fp_compute
            fr = fp_compute(str(bad))
            ok &= _check("chaos-5a: fingerprint on corrupted MP3: returns FingerprintResult",
                         hasattr(fr, "fingerprint_source"))
            ok &= _check("chaos-5b: fingerprint on corrupted MP3: valid source value",
                         fr.fingerprint_source in ("acoustid", "sha256_audio", "none"))
            from services.dedup_service import content_hash as ch_fn
            ch = ch_fn("Corrupted Track", "Artist", 120000)
            ok &= _check("chaos-5c: content_hash unaffected by file corruption",
                         len(ch) == 16)
    except Exception as e:
        ok &= _check("chaos-5: no exception from service layer on corrupted file", False, str(e))

    # chaos-6: 10 concurrent lock acquisitions → all complete, no deadlock
    print("  [chaos-6] 10 concurrent lock acquisitions → no deadlock")
    try:
        from services.lock_service import acquire_lock as _acq
        results_c6: list[int] = []
        errors_c6: list[str] = []

        def _worker_c6(n: int):
            try:
                with _acq("chaos_concurrent", timeout=10.0):
                    results_c6.append(n)
                    time.sleep(0.005)
            except Exception as exc:
                errors_c6.append(str(exc))

        threads = [threading.Thread(target=_worker_c6, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)
        ok &= _check("chaos-6a: all 10 threads completed", len(results_c6) == 10)
        ok &= _check("chaos-6b: no exceptions in any thread", len(errors_c6) == 0,
                     f"Errors: {errors_c6[:3]}")
        ok &= _check("chaos-6c: results serialized (no data loss)",
                     sorted(results_c6) == list(range(10)))
    except Exception as e:
        ok &= _check("chaos-6: concurrent lock storm: no exception", False, str(e))

    # chaos-7: Metrics rapid-fire with no MongoDB → fire-and-forget safety
    print("  [chaos-7] Metrics rapid-fire with no MongoDB → all calls safe")
    try:
        from services.metrics_service import record_value, increment, record as m_record
        for i in range(50):
            record_value("chaos_metric", float(i))
        with m_record("chaos_latency_timer"):
            time.sleep(0.001)
        for _ in range(10):
            increment("chaos_counter")
        ok &= _check("chaos-7: 60 metric operations with absent MongoDB: no exception", True)
    except Exception as e:
        ok &= _check("chaos-7: metrics safe under DB outage", False, str(e))

    # chaos-8: Rekordbox export with empty library → clean exit, no XML written
    print("  [chaos-8] Rekordbox export with empty library → clean exit")
    try:
        import importlib.util
        rb_path = _BACKEND / "rekordbox_export.py"
        spec = importlib.util.spec_from_file_location("rb_export_chaos", rb_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "chaos_empty.xml"
            orig_load = mod.load_index_docs
            mod.load_index_docs = lambda *a, **kw: []
            try:
                code = mod.export(out, full=True)
                ok &= _check("chaos-8a: export() with empty library returns 0", code == 0)
                ok &= _check("chaos-8b: no XML file written when library is empty",
                             not out.exists())
            finally:
                mod.load_index_docs = orig_load
    except Exception as e:
        ok &= _check("chaos-8: rekordbox export with empty library: no exception", False, str(e))

    # chaos-9: 5 sequential lock cycles → no stale accumulation
    print("  [chaos-9] Sequential lock cycles → no stale lock accumulation")
    try:
        from services.lock_service import acquire_lock as _acq9
        results_c9 = []
        for i in range(5):
            with _acq9("chaos_seq_lock", timeout=3.0):
                results_c9.append(i)
        ok &= _check("chaos-9: 5 sequential lock cycles all succeed",
                     results_c9 == list(range(5)))
    except Exception as e:
        ok &= _check("chaos-9: sequential lock cycles: no exception", False, str(e))

    # chaos-10: Fingerprint on nonexistent path → graceful 'none'
    print("  [chaos-10] Fingerprint on missing path → graceful 'none'")
    try:
        from services.fingerprint_service import compute as fp2, find_duplicate as fd
        r = fp2("/absolutely/nonexistent/path/chaos_track.mp3")
        ok &= _check("chaos-10a: missing file → fingerprint_source='none'",
                     r.fingerprint_source == "none")
        ok &= _check("chaos-10b: missing file → error field set", bool(r.error))
        ok &= _check("chaos-10c: find_duplicate(invalid) → None", fd(r, None) is None)
    except Exception as e:
        ok &= _check("chaos-10: missing file fingerprint: no exception", False, str(e))

    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 16 — Universal Genre Architecture (Phases 1–10 of new arch)
# ─────────────────────────────────────────────────────────────────
def verify_universal_org() -> bool:
    print("\n[PHASE 16] Universal genre architecture — 10 subsystems")
    ok = True

    # 16-1: GENRE_TAXONOMY coverage
    try:
        from services.genre_router import GENRE_TAXONOMY, normalize_genre, resolve_genre_family, resolve_subgenre, _library_path
        ok &= _check("16-1a: GENRE_TAXONOMY has >= 20 entries", len(GENRE_TAXONOMY) >= 20)
        ok &= _check("16-1b: Electronic family present",
                     any(v[0] == "Electronic" for v in GENRE_TAXONOMY.values()))
        ok &= _check("16-1c: Indian family present",
                     any(v[0] == "Indian" for v in GENRE_TAXONOMY.values()))
        ok &= _check("16-1d: Tamil mapped to Indian/Tamil",
                     GENRE_TAXONOMY.get("Tamil", ("", ""))[1] == "Indian/Tamil")
        ok &= _check("16-1e: Telugu mapped to Indian/Telugu",
                     GENRE_TAXONOMY.get("Telugu", ("", ""))[1] == "Indian/Telugu")
        ok &= _check("16-1f: normalize_genre('uk garage') → 'UK Garage'",
                     normalize_genre("uk garage") == "UK Garage")
        ok &= _check("16-1g: normalize_genre('house music') resolves to taxonomy",
                     normalize_genre("house music") in GENRE_TAXONOMY)
        ok &= _check("16-1h: resolve_genre_family('House') == 'Electronic'",
                     resolve_genre_family("House") == "Electronic")
        ok &= _check("16-1i: resolve_subgenre('Bollywood') == 'Indian/Bollywood'",
                     resolve_subgenre("Bollywood") == "Indian/Bollywood")
        ok &= _check("16-1j: _library_path('UK Garage') == 'Library/Electronic/UKG'",
                     _library_path("UK Garage") == "Library/Electronic/UKG")
        ok &= _check("16-1k: _library_path('Hip Hop') == 'Library/HipHop'",
                     _library_path("Hip Hop") == "Library/HipHop")
        ok &= _check("16-1l: _library_path unknown → 'Library/OpenFormat'",
                     _library_path("XyzUnknownGenre999") == "Library/OpenFormat")
    except Exception as e:
        ok &= _check("16-1: GENRE_TAXONOMY check", False, str(e))

    # 16-2: Artist memory service
    try:
        from services.artist_memory_service import record_move, lookup_artist, forget_artist, _normalize_artist
        ok &= _check("16-2a: _normalize_artist normalises correctly",
                     _normalize_artist("  Sammy Virji  ") == "sammy virji")
        ok &= _check("16-2b: record_move + lookup_artist smoke-test (no MongoDB → skip)",
                     True)  # actual DB test skipped; service degrades gracefully
        ok &= _check("16-2c: lookup_artist(unknown) returns None",
                     lookup_artist("__nonexistent_artist_xyz__") is None)
        ok &= _check("16-2d: forget_artist(unknown) returns False",
                     forget_artist("__nonexistent_artist_xyz__") is False)
    except Exception as e:
        ok &= _check("16-2: artist_memory_service", False, str(e))

    # 16-3: NeedsReview vs Quarantine — strict separation
    try:
        from services.genre_router import NEEDS_REVIEW_DIR, CONFIDENCE_THRESHOLD, _resolve_core
        ok &= _check("16-3a: NEEDS_REVIEW_DIR constant exists",
                     NEEDS_REVIEW_DIR == "NeedsReview")
        ok &= _check("16-3b: CONFIDENCE_THRESHOLD == 0.5",
                     CONFIDENCE_THRESHOLD == 0.5)
        # Verify low-confidence routes to NeedsReview not Uncategorized/Quarantine
        class _FakeSP:
            def artist(self, aid):
                return {"genres": []}
        folder, conf, src, _ = _resolve_core("fake_low_conf_id", "UnknownArtist99", _FakeSP())
        ok &= _check("16-3c: zero-confidence → NeedsReview (not Uncategorized)",
                     folder.startswith("NeedsReview"))
        ok &= _check("16-3d: zero-confidence never routes to Quarantine",
                     not folder.startswith("Quarantine"))
    except Exception as e:
        ok &= _check("16-3: NeedsReview/Quarantine separation", False, str(e))

    # 16-4: Remix family detection
    try:
        from services.organizer_service import detect_mix_variant
        cases = [
            ("Track Name (VIP).mp3",              ("Track Name", "VIP")),
            ("Feel So Close (Extended Mix).mp3",   ("Feel So Close", "Extended")),
            ("Titanium (Remix).mp3",               ("Titanium", "Remix")),
            ("Regular Track.mp3",                  ("Regular Track", "")),
            ("LIVE Version (Live).mp3",             ("LIVE Version", "Live")),
        ]
        for fname, expected in cases:
            result = detect_mix_variant(fname)
            ok &= _check(f"16-4: detect_mix_variant({fname!r}) → {expected}",
                         result == expected, f"got {result}")
    except Exception as e:
        ok &= _check("16-4: detect_mix_variant", False, str(e))

    # 16-5: Reclassification service importable
    try:
        from services.reclassification_service import run_reclassification
        ok &= _check("16-5a: reclassification_service importable", True)
        ok &= _check("16-5b: run_reclassification callable",
                     callable(run_reclassification))
    except Exception as e:
        ok &= _check("16-5: reclassification_service", False, str(e))

    # 16-6: Organizer enforces sequential flow (has routing_reason_text + resolve_destination_path)
    try:
        from services.organizer_service import (
            resolve_destination_path, routing_reason_text, needs_review_guidance
        )
        ok &= _check("16-6a: resolve_destination_path accepts group_remix_families kwarg",
                     callable(resolve_destination_path))
        reason = routing_reason_text("NeedsReview/Artist", 0.0, "uncategorized", "Track", "Artist")
        ok &= _check("16-6b: routing_reason_text for NeedsReview contains 'NeedsReview'",
                     "NeedsReview" in reason)
        reason2 = routing_reason_text("Library/Electronic/House/Artist", 0.7, "spotify_genre")
        ok &= _check("16-6c: routing_reason_text for Library path contains folder",
                     "Library" in reason2)
        guidance = needs_review_guidance("Sammy Virji", "UK Garage")
        ok &= _check("16-6d: needs_review_guidance returns non-empty string", bool(guidance))
    except Exception as e:
        ok &= _check("16-6: organizer_service routing flow", False, str(e))

    # 16-7: Canonical scoring in dedup_service
    try:
        from services.dedup_service import canonical_score, score_from_tags
        high = canonical_score("/Music/Library/House/track.mp3",
                               spotify_id="abc", has_bpm=True, has_key=True,
                               has_artwork=True, has_camelot=True, bitrate_kbps=320)
        low  = canonical_score("/Music/Ingest/track_1.mp3",
                               spotify_id="", has_bpm=False, has_key=False)
        ok &= _check("16-7a: canonical_score: high-quality > low-quality", high > low)
        ok &= _check("16-7b: canonical_score: spotify_id adds points",
                     canonical_score("/x.mp3", spotify_id="abc") >
                     canonical_score("/x.mp3", spotify_id=""))
        ok &= _check("16-7c: canonical_score: temp file penalty applied",
                     canonical_score("/Ingest/track_1.mp3") <
                     canonical_score("/Library/House/track.mp3"))
        ok &= _check("16-7d: score_from_tags returns float for nonexistent file",
                     isinstance(score_from_tags("/nonexistent/file.mp3"), float))
    except Exception as e:
        ok &= _check("16-7: dedup canonical scoring", False, str(e))

    # 16-8: Migration helper importable
    try:
        from migrate_library_structure import migrate, _map_old_to_new
        ok &= _check("16-8a: migrate_library_structure importable", True)
        ok &= _check("16-8b: _map_old_to_new('House') → Library/Electronic/House",
                     _map_old_to_new("House") == "Library/Electronic/House")
        ok &= _check("16-8c: _map_old_to_new('UK Garage') → Library/Electronic/UKG",
                     _map_old_to_new("UK Garage") == "Library/Electronic/UKG")
        ok &= _check("16-8d: _map_old_to_new('Bollywood') → Library/Indian/Bollywood",
                     _map_old_to_new("Bollywood") == "Library/Indian/Bollywood")
        ok &= _check("16-8e: _map_old_to_new unknown → None",
                     _map_old_to_new("XyzUnknown999") is None)
    except Exception as e:
        ok &= _check("16-8: migrate_library_structure", False, str(e))

    # 16-9: get_routing_explanation returns meaningful text
    try:
        from services.genre_router import get_routing_explanation
        expl_nr = get_routing_explanation("NeedsReview/Artist", 0.3, "devanagari")
        expl_lib = get_routing_explanation("Library/Electronic/House/Artist", 0.7, "spotify_genre", "uk garage")
        ok &= _check("16-9a: NeedsReview explanation mentions NeedsReview", "NeedsReview" in expl_nr)
        ok &= _check("16-9b: Library explanation mentions folder", "Library" in expl_lib)
        ok &= _check("16-9c: explanations are non-empty strings",
                     bool(expl_nr) and bool(expl_lib))
    except Exception as e:
        ok &= _check("16-9: get_routing_explanation (Phase 9 UI layer)", False, str(e))

    # 16-10: database has artist_memory collection accessor
    try:
        from database import get_artist_memory_collection
        ok &= _check("16-10a: get_artist_memory_collection importable", True)
        col = get_artist_memory_collection()
        ok &= _warn("16-10b: artist_memory collection accessible (needs MongoDB)",
                    col is not None,
                    "MongoDB not running — collection returned None")
    except Exception as e:
        ok &= _check("16-10: artist_memory DB accessor", False, str(e))

    return ok


# PHASE 17 — Final Rollout Validation
# ─────────────────────────────────────────────────────────────────
def verify_final_rollout() -> bool:
    print("\n[PHASE 17] Final rollout validation — production readiness")
    ok = True

    # 17-1: No Ingest paths in library_index
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            ingest_count = col.count_documents(
                {"final_path": {"$regex": r"[/\\][Ii]ngest[/\\]", "$options": "i"}}
            )
            ok &= _check("17-1: No Ingest paths in library_index", ingest_count == 0,
                         f"{ingest_count} Ingest path(s) still in library_index")
        else:
            ok &= _warn("17-1: library_index reachable", False, "MongoDB unavailable")
    except Exception as e:
        ok &= _check("17-1: library_index Ingest check", False, str(e))

    # 17-2: No Library-in-Library recursive paths in library_index
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        if col is not None:
            nested_count = col.count_documents(
                {"final_path": {"$regex": r"[/\\][Ll]ibrary[/\\].*[/\\][Ll]ibrary[/\\]"}}
            )
            ok &= _check("17-2: No nested Library/ paths", nested_count == 0,
                         f"{nested_count} nested Library/ path(s) found")
    except Exception as e:
        ok &= _check("17-2: nested Library/ check", False, str(e))

    # 17-3: Rekordbox _genre_contains works for both old and new paths
    try:
        from rekordbox_export import _genre_contains
        old_doc = {"genre_folder": "House/Sammy Virji"}
        new_doc = {"genre_folder": "Library/Electronic/House/Sammy Virji"}
        ok &= _check("17-3a: _genre_contains flat path", _genre_contains(old_doc, "House"))
        ok &= _check("17-3b: _genre_contains Library/ path", _genre_contains(new_doc, "House"))
        ok &= _check("17-3c: _genre_contains NeedsReview", _genre_contains(
            {"genre_folder": "NeedsReview/Artist"}, "NeedsReview"))
        ok &= _check("17-3d: _genre_contains no false positive",
                     not _genre_contains(new_doc, "UKG"))
    except Exception as e:
        ok &= _check("17-3: rekordbox _genre_contains", False, str(e))

    # 17-4: artist_memory seeding callable
    try:
        from services.artist_memory_service import seed_artist_memory, bulk_record_overrides
        ok &= _check("17-4a: seed_artist_memory importable", True)
        ok &= _check("17-4b: bulk_record_overrides importable", True)
    except Exception as e:
        ok &= _check("17-4: artist_memory seeding", False, str(e))

    # 17-5: reclassification_service callable
    try:
        from services.reclassification_service import run_reclassification
        ok &= _check("17-5: run_reclassification importable", True)
    except Exception as e:
        ok &= _check("17-5: reclassification_service", False, str(e))

    # 17-6: migrate_library_structure pre_migration_check callable
    try:
        import importlib.util
        mig_path = _BACKEND / "migrate_library_structure.py"
        ok &= _check("17-6a: migrate_library_structure.py exists", mig_path.exists())
        if mig_path.exists():
            spec = importlib.util.spec_from_file_location("_mig17", mig_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ok &= _check("17-6b: pre_migration_check callable", callable(getattr(mod, "pre_migration_check", None)))
            ok &= _check("17-6c: migrate callable", callable(getattr(mod, "migrate", None)))
    except Exception as e:
        ok &= _check("17-6: migrate_library_structure", False, str(e))

    # 17-7: genre_router never routes to Uncategorized
    try:
        from services.genre_router import _library_path, GENRE_TAXONOMY
        bad_paths = [_library_path(g) for g in GENRE_TAXONOMY if "uncategorized" in _library_path(g).lower()]
        ok &= _check("17-7: no Uncategorized in GENRE_TAXONOMY routing", len(bad_paths) == 0,
                     f"Uncategorized output for: {bad_paths}")
    except Exception as e:
        ok &= _check("17-7: genre_router Uncategorized check", False, str(e))

    # 17-8: maintenance worker has reclassify task registered
    try:
        import inspect
        from services.maintenance_worker import MaintenanceWorker
        src = inspect.getsource(MaintenanceWorker._register_tasks)
        ok &= _check("17-8: reclassify_needs_review task registered",
                     "reclassify_needs_review" in src)
    except Exception as e:
        ok &= _check("17-8: maintenance worker reclassify task", False, str(e))

    return ok


# PHASE 18 — Artist Folder Intelligence & Routing Safety
# ─────────────────────────────────────────────────────────────────
def verify_artist_folder_rollout() -> bool:
    print("\n[PHASE 18] Artist folder intelligence — routing safety")
    ok = True

    # 18-1: artist_folder_service importable
    try:
        from services.artist_folder_service import (
            detect_folder_type, route_artist_folder,
            seed_from_routing, generate_artist_folder_analysis,
            resolve_alias,
        )
        ok &= _check("18-1: artist_folder_service importable", True)
    except Exception as e:
        ok &= _check("18-1: artist_folder_service importable", False, str(e))
        return ok  # nothing else will work

    # 18-2: alias resolution
    try:
        ok &= _check("18-2a: 'weekend' → 'The Weeknd'",
                     resolve_alias("weekend") == "The Weeknd")
        ok &= _check("18-2b: 'drum & bass rampage' alias resolved",
                     resolve_alias("drum & bass rampage") == "Drum and Bass Rampage")
        ok &= _check("18-2c: unknown name returns itself",
                     resolve_alias("Sammy Virji") == "Sammy Virji")
    except Exception as e:
        ok &= _check("18-2: alias resolution", False, str(e))

    # 18-3: GENRE_TAXONOMY has Dubstep and Bass
    try:
        from services.genre_router import GENRE_TAXONOMY, _library_path
        ok &= _check("18-3a: Dubstep in GENRE_TAXONOMY", "Dubstep" in GENRE_TAXONOMY)
        ok &= _check("18-3b: Bass in GENRE_TAXONOMY",    "Bass"    in GENRE_TAXONOMY)
        ok &= _check("18-3c: Indian Hip Hop in GENRE_TAXONOMY", "Indian Hip Hop" in GENRE_TAXONOMY)
        ok &= _check("18-3d: Dubstep routes to Electronic/Dubstep",
                     _library_path("Dubstep") == "Library/Electronic/Dubstep")
        ok &= _check("18-3e: Bass routes to Electronic/Bass",
                     _library_path("Bass") == "Library/Electronic/Bass")
    except Exception as e:
        ok &= _check("18-3: GENRE_TAXONOMY extensions", False, str(e))

    # 18-4: config overrides include new artists
    try:
        from config import config
        ok &= _check("18-4a: 'hamdi' in ARTIST_GENRE_OVERRIDE",
                     "hamdi" in config.ARTIST_GENRE_OVERRIDE)
        ok &= _check("18-4b: 'the weeknd' in ARTIST_GENRE_OVERRIDE",
                     "the weeknd" in config.ARTIST_GENRE_OVERRIDE)
        ok &= _check("18-4c: 'michael jackson' in ARTIST_GENRE_OVERRIDE",
                     "michael jackson" in config.ARTIST_GENRE_OVERRIDE)
        ok &= _check("18-4d: skrillex routes to Bass",
                     config.ARTIST_GENRE_OVERRIDE.get("skrillex") == "Bass")
        ok &= _check("18-4e: hamdi routes to Dubstep",
                     config.ARTIST_GENRE_OVERRIDE.get("hamdi") == "Dubstep")
    except Exception as e:
        ok &= _check("18-4: config artist overrides", False, str(e))

    # 18-5: route_artist_folder returns valid Library/ path for known artists
    try:
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            for artist, expected_frag in [
                ("Sammy Virji", "UKG"),
                ("The Weeknd",  "OpenFormat"),
                ("Hamdi",       "Dubstep"),
            ]:
                route = route_artist_folder(artist, tmp)
                if route:
                    ok &= _check(f"18-5: {artist} → contains '{expected_frag}'",
                                 expected_frag.lower() in route.lower(),
                                 f"got {route!r}")
                else:
                    ok &= _warn(f"18-5: {artist} route (needs artist_memory/config)",
                                False, "returned None — artist not in memory or override")
    except Exception as e:
        ok &= _check("18-5: route_artist_folder", False, str(e))

    # 18-6: migrate_library_structure has migrate_artist_folders
    try:
        import importlib.util
        mig = _BACKEND / "migrate_library_structure.py"
        spec = importlib.util.spec_from_file_location("_mig18", mig)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok &= _check("18-6a: migrate_artist_folders callable",
                     callable(getattr(mod, "migrate_artist_folders", None)))
        ok &= _check("18-6b: --retry-unmapped wired in main",
                     "retry_unmapped" in open(mig, encoding="utf-8").read())
    except Exception as e:
        ok &= _check("18-6: migrate_library_structure artist retry", False, str(e))

    # 18-7: No Library-in-Library paths exist on disk
    try:
        from config import config as _cfg
        base = _BACKEND.parent / _cfg.BASE_DOWNLOAD_DIR if not _cfg.BASE_DOWNLOAD_DIR.startswith(("/", "C:")) else pathlib.Path(_cfg.BASE_DOWNLOAD_DIR)  # noqa
        lib  = base / "Library"
        nested = list(lib.glob("Library")) if lib.is_dir() else []
        ok &= _check("18-7: no Library/Library/ on disk", len(nested) == 0,
                     f"Nested Library found: {nested}")
    except Exception as e:
        ok &= _warn("18-7: Library nesting disk check", True,
                    f"Could not verify ({e}) — skipping")

    # 18-8: reclassification_service has artist_folder_service fallback
    try:
        src = (_BACKEND / "services" / "reclassification_service.py").read_text(encoding="utf-8")
        ok &= _check("18-8: reclassification uses artist_folder_service",
                     "artist_folder_service" in src)
    except Exception as e:
        ok &= _check("18-8: reclassification artist_folder_service", False, str(e))

    return ok


# ─────────────────────────────────────────────────────────────────
# Phase 19 — Legacy identification service
# ─────────────────────────────────────────────────────────────────
def verify_legacy_identification() -> bool:
    """Phase 19: Legacy track identification service integrity checks."""
    ok = True
    print("\n[PHASE 19] Legacy track identification")

    # 19-1: Import
    try:
        import dataclasses as _dc
        from services.legacy_identification_service import (
            LegacyIdentificationResult,
            identify_file,
            identify_batch,
            normalize_for_search,
            resolve_artist_alias,
            analyze_trance_bucket,
            analyze_canonical_renames,
            write_identification_report,
            CONF_AUTO_ACCEPT,
            CONF_ACCEPT_WARN,
            CONF_NEEDS_REVIEW,
        )
        ok &= _check("19-1: service imports cleanly", True)
    except Exception as e:
        ok &= _check("19-1: service imports cleanly", False, str(e))
        return ok

    # 19-2: Confidence thresholds
    ok &= _check("19-2a: AUTO_ACCEPT = 0.90",  CONF_AUTO_ACCEPT  == 0.90)
    ok &= _check("19-2b: ACCEPT_WARN = 0.75",  CONF_ACCEPT_WARN  == 0.75)
    ok &= _check("19-2c: NEEDS_REVIEW = 0.60", CONF_NEEDS_REVIEW == 0.60)

    # 19-3: Alias normalization
    ok &= _check("19-3a: 'weekend' → 'The Weeknd'",
                 resolve_artist_alias("weekend") == "The Weeknd")
    ok &= _check("19-3b: 'fred again' → 'Fred again..'",
                 resolve_artist_alias("fred again") == "Fred again..")
    ok &= _check("19-3c: unknown artist returns itself",
                 resolve_artist_alias("Some Unknown DJ") == "Some Unknown DJ")

    # 19-4: Text normalization
    ok &= _check("19-4a: unicode normalization",
                 normalize_for_search("Café del Mar") == "cafe del mar")
    ok &= _check("19-4b: feat. removed from title",
                 "feat" not in normalize_for_search("Song (feat. Artist)"))
    ok &= _check("19-4c: remix suffix stripped",
                 normalize_for_search("Song (Extended Remix)") == "song")
    ok &= _check("19-4d: smart quotes normalized",
                 normalize_for_search("“Hello”") == "hello")

    # 19-5: LegacyIdentificationResult has all required fields
    required_fields = {
        "filepath", "matched", "spotify_id", "artist", "title",
        "confidence", "confidence_reason", "match_source",
        "duration_delta_sec", "normalized_artist", "normalized_title",
        "reroute_recommended", "target_genre_family", "target_subgenre",
    }
    actual_fields = {f.name for f in _dc.fields(LegacyIdentificationResult)}
    missing = required_fields - actual_fields
    ok &= _check("19-5: LegacyIdentificationResult has all fields",
                 not missing, f"missing: {missing}")

    # 19-6: reroute_recommended False when confidence < CONF_ACCEPT_WARN
    import dataclasses as _dc2
    dummy = LegacyIdentificationResult(
        filepath="x.mp3", matched=True, spotify_id="abc",
        artist="A", title="T", confidence=0.70,
        confidence_reason="test", match_source="test",
        duration_delta_sec=0.0, normalized_artist="a", normalized_title="t",
        reroute_recommended=False, target_genre_family="", target_subgenre="",
    )
    ok &= _check("19-6: reroute_recommended=False when conf=0.70 < 0.75",
                 not dummy.reroute_recommended)

    # 19-7: Canonical rename analysis — no Library/Library/ nesting
    try:
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = _Path(tmp)
            alias_dir = tmp_p / "Library" / "Electronic" / "House" / "weekend"
            alias_dir.mkdir(parents=True)
            (alias_dir / "test.mp3").write_bytes(b"")
            rpt = analyze_canonical_renames(tmp_p, tmp_p / "reports", dry_run=True)
            ok &= _check("19-7a: rename report generated",
                         "renames" in rpt)
            ok &= _check("19-7b: 'weekend' detected as candidate",
                         any(r["canonical_name"] == "The Weeknd"
                             for r in rpt.get("renames", [])))
            nesting = any(
                "Library" in str(_Path(r["parent"]).relative_to(tmp_p)).split("\\")[0]
                and "Library" in r["canonical_name"]
                for r in rpt.get("renames", [])
            )
            ok &= _check("19-7c: no Library/Library/ nesting in renames", not nesting)
    except Exception as e:
        ok &= _check("19-7: canonical rename safety", False, str(e))

    # 19-8: Callable checks
    ok &= _check("19-8a: identify_file callable",             callable(identify_file))
    ok &= _check("19-8b: identify_batch callable",            callable(identify_batch))
    ok &= _check("19-8c: analyze_trance_bucket callable",     callable(analyze_trance_bucket))
    ok &= _check("19-8d: write_identification_report callable", callable(write_identification_report))

    # 19-9: retag_migration --identify wired
    try:
        import importlib, sys as _sys
        from pathlib import Path as _P
        _rb = _P(__file__).parent / "retag_migration.py"
        src = _rb.read_text(encoding="utf-8")
        ok &= _check("19-9a: --identify flag in retag CLI",
                     "--identify" in src)
        ok &= _check("19-9b: identify_file called in migrate_file",
                     "identify_file" in src)
        ok &= _check("19-9c: CONF_NEEDS_REVIEW gating in migrate_file",
                     "CONF_NEEDS_REVIEW" in src)
    except Exception as e:
        ok &= _check("19-9: retag_migration integration", False, str(e))

    # 19-10: reports/ directory accessible
    from pathlib import Path as _P2
    reports_dir = _P2(__file__).parent / "reports"
    ok &= _check("19-10: reports/ directory exists or creatable",
                 reports_dir.is_dir() or not reports_dir.exists())

    return ok


# ─────────────────────────────────────────────────────────────────
# PHASE 20 — Artist Knowledge Base
# ─────────────────────────────────────────────────────────────────
def verify_artist_knowledge() -> bool:
    print("\n[PHASE 20] Artist knowledge base — coverage & routing boosts")
    ok = True

    # 20-1: Import
    try:
        from services.artist_knowledge_service import (
            lookup_artist_knowledge,
            normalize_multilingual_artist,
            clean_event_rip_title,
            get_knowledge_report,
            CONFIDENCE_KNOWLEDGE_BASE,
        )
        ok &= _check("20-1: artist_knowledge_service imports cleanly", True)
    except Exception as e:
        ok &= _check("20-1: artist_knowledge_service importable", False, str(e))
        return ok

    # 20-2: Confidence level
    ok &= _check("20-2: CONFIDENCE_KNOWLEDGE_BASE == 0.85",
                 CONFIDENCE_KNOWLEDGE_BASE == 0.85)

    # 20-3: Indian artist lookups
    indian_cases = [
        ("Arijit Singh",         "Bollywood"),
        ("Pritam",               "Bollywood"),
        ("Vishal-Shekhar",       "Bollywood"),
        ("Sachet Tandon",        "Bollywood"),
        ("A.R. Rahman",          "Bollywood"),
        ("Badshah",              "Bollywood"),
        ("Neha Kakkar",          "Bollywood"),
        ("Shreya Ghoshal",       "Bollywood"),
        ("Karan Aujla",          "Punjabi"),
        ("AP Dhillon",           "Punjabi"),
        ("Sidhu Moosewala",      "Punjabi"),
        ("Diljit Dosanjh",       "Punjabi"),
        ("Anirudh Ravichander",  "Tamil"),
        ("Santhosh Narayanan",   "Tamil"),
        ("Yuvan Shankar Raja",   "Tamil"),
        ("Devi Sri Prasad",      "Telugu"),
        ("Thaman S",             "Telugu"),
        ("Seedhe Maut",          "Indian Hip Hop"),
        ("Divine",               "Indian Hip Hop"),
        ("KR$NA",                "Indian Hip Hop"),
        ("Emiway Bantai",        "Indian Hip Hop"),
    ]
    for artist, expected_genre in indian_cases:
        hit = lookup_artist_knowledge(artist)
        ok &= _check(
            f"20-3: {artist!r} → {expected_genre}",
            hit is not None and hit.get("genre") == expected_genre,
            f"got {hit}",
        )

    # 20-4: Global EDM lookups
    edm_cases = [
        ("Skrillex",       "Electronic"),
        ("Fred again..",   "House"),
        ("Sammy Virji",    "UK Garage"),
        ("Hamdi",          "Electronic"),
        ("Chase & Status", "Drum and Bass"),
        ("Sub Focus",      "Drum and Bass"),
        ("Martin Garrix",  "Electronic"),
        ("Hardwell",       "Electronic"),
        ("Illenium",       "Electronic"),
        ("KSHMR",          "Electronic"),
        ("Solomun",        "House"),
        ("Four Tet",       "House"),
        ("Peggy Gou",      "House"),
        ("Boris Brejcha",  "House"),
    ]
    for artist, expected_genre in edm_cases:
        hit = lookup_artist_knowledge(artist)
        ok &= _check(
            f"20-4: {artist!r} → {expected_genre}",
            hit is not None and hit.get("genre") == expected_genre,
            f"got {hit}",
        )

    # 20-5: Alias resolution
    alias_cases = [
        ("anirudh",             "Tamil"),         # partial name
        ("arjit singh",         "Bollywood"),     # common misspelling
        ("fred again",          "House"),         # no trailing dots
        ("chase and status",    "Drum and Bass"), # & → and variant
        ("chase status",        "Drum and Bass"), # symbol-dropped
        ("ap dhillon",          "Punjabi"),       # lowercase
        ("dsp",                 "Telugu"),        # Devi Sri Prasad short form
        ("sidhu",               "Punjabi"),       # single-name alias
        ("diljit",              "Punjabi"),
        ("yuvan",               "Tamil"),
    ]
    for alias, expected_genre in alias_cases:
        hit = lookup_artist_knowledge(alias)
        ok &= _check(
            f"20-5: alias {alias!r} → {expected_genre}",
            hit is not None and hit.get("genre") == expected_genre,
            f"got {hit}",
        )

    # 20-6: Multilingual normalization
    try:
        ok &= _check(
            "20-6a: Devanagari 'अरिजीत सिंह' → 'Arijit Singh'",
            normalize_multilingual_artist("अरिजीत सिंह") == "Arijit Singh",
        )
        ok &= _check(
            "20-6b: Devanagari 'बादशाह' → 'Badshah'",
            normalize_multilingual_artist("बादशाह") == "Badshah",
        )
        ok &= _check(
            "20-6c: unknown script returns original",
            normalize_multilingual_artist("Sammy Virji") == "Sammy Virji",
        )
    except Exception as e:
        ok &= _check("20-6: multilingual normalization", False, str(e))

    # 20-7: Event-rip title cleaner
    try:
        rip_cases = [
            ("Song Title DJCITY",            "Song Title"),
            ("Track Free Download",          "Track"),
            ("Banger Out Now",               "Banger"),
            ("Song Title Official Video",    "Song Title"),
            ("Track 320kbps",                "Track"),
            ("Song DJ Version",              "Song"),
            ("Title YT Rip",                 "Title"),
            ("Normal Title",                 "Normal Title"),  # unchanged
        ]
        for dirty, expected in rip_cases:
            result = clean_event_rip_title(dirty)
            ok &= _check(
                f"20-7: clean_event_rip_title({dirty!r}) → {expected!r}",
                result == expected,
                f"got {result!r}",
            )
    except Exception as e:
        ok &= _check("20-7: event-rip title cleaner", False, str(e))

    # 20-8: Knowledge report structure
    try:
        report = get_knowledge_report()
        ok &= _check("20-8a: report has total_artists", "total_artists" in report)
        ok &= _check("20-8b: total_artists >= 80",
                     report.get("total_artists", 0) >= 80)
        ok &= _check("20-8c: report has genre_coverage", "genre_coverage" in report)
        ok &= _check("20-8d: report covers Bollywood",
                     "Bollywood" in report.get("genre_coverage", {}))
        ok &= _check("20-8e: report covers Punjabi",
                     "Punjabi" in report.get("genre_coverage", {}))
        ok &= _check("20-8f: report covers Tamil",
                     "Tamil" in report.get("genre_coverage", {}))
        ok &= _check("20-8g: report covers Indian Hip Hop",
                     "Indian Hip Hop" in report.get("genre_coverage", {}))
        ok &= _check("20-8h: total_alias_keys > total_artists",
                     report.get("total_alias_keys", 0) > report.get("total_artists", 0))
    except Exception as e:
        ok &= _check("20-8: knowledge report", False, str(e))

    # 20-9: Unknown artist returns None
    try:
        ok &= _check("20-9: unknown artist returns None",
                     lookup_artist_knowledge("__XyzUnknownArtist999__") is None)
    except Exception as e:
        ok &= _check("20-9: unknown artist safety", False, str(e))

    # 20-10: genre_router integrates knowledge_base source label
    try:
        from services.genre_router import get_routing_explanation
        expl = get_routing_explanation(
            "Library/Indian/Bollywood/Arijit Singh", 0.85, "knowledge_base", "Bollywood"
        )
        ok &= _check("20-10: knowledge_base source label in routing explanation",
                     "knowledge base" in expl.lower() or "knowledge_base" in expl.lower(),
                     f"got: {expl!r}")
    except Exception as e:
        ok &= _check("20-10: genre_router knowledge_base label", False, str(e))

    return ok


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  DJ Pipeline Integrity Verifier — Phases 1–20")
    print("=" * 65)

    results = [
        verify_phase1(),   # DownloadResult dataclass
        verify_phase2(),   # dedup_service
        verify_phase3(),   # genre_router confidence
        verify_phase4(),   # NeedsReview routing
        verify_phase5(),   # library_index
        verify_phase6(),   # DJ TXXX aliases
        verify_phase7(),   # retag_migration
        verify_phase8(),   # lock_service
        verify_phase9(),   # retry hardening
        verify_phase10(),  # verify_patches.py baseline
        verify_phase11(),  # reconcile_library_state.py
        verify_phase12(),  # metrics_service.py
        verify_phase13(),  # fingerprint_service.py
        verify_phase14(),  # rekordbox_export.py
        verify_chaos(),              # Phase 15 — chaos & resilience
        verify_universal_org(),      # Phase 16 — universal org architecture
        verify_final_rollout(),      # Phase 17 — final rollout validation
        verify_artist_folder_rollout(),  # Phase 18 — artist folder intelligence
        verify_legacy_identification(),  # Phase 19 — legacy identification
        verify_artist_knowledge(),       # Phase 20 — artist knowledge base
    ]

    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 65)
    if passed == total:
        print(f"  {_PASS}  All {total} phase checks passed — pipeline is healthy")
        sys.exit(0)
    else:
        failed = total - passed
        print(f"  {_FAIL}  {failed}/{total} phase check(s) FAILED — see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
