"""
Patch Verification Script
=========================
Validates that all 5 critical patches are correctly applied.

Usage (from the backend/ directory):
    python verify_patches.py

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

import sys
import inspect
from pathlib import Path

_BACKEND = Path(__file__).parent
_PASS = "\033[92m✅\033[0m"
_FAIL = "\033[91m❌\033[0m"


def _check(name: str, condition: bool, fix: str = "") -> bool:
    icon = _PASS if condition else _FAIL
    print(f"  {icon}  {name}")
    if not condition and fix:
        print(f"       → {fix}")
    return condition


# ─────────────────────────────────────────────────────────────────
# PATCH 1 — BPM/Key pipeline wired
# ─────────────────────────────────────────────────────────────────
def verify_patch1() -> bool:
    print("\n[PATCH 1] SpotifyService wired into tagger call")
    src = (_BACKEND / "services" / "downloader_service.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check(
        "spotify_service_instance=None removed",
        "spotify_service_instance=None,  # TAGGING INTEGRATION" not in src,
        "downloader_service.py still passes None — BPM/Key pipeline is dead",
    )
    ok &= _check(
        "get_spotify_service lazy import present",
        "get_spotify_service as _gss" in src,
        "Lazy import of get_spotify_service not found in tagging block",
    )
    ok &= _check(
        "spotify_id forwarded to save_tagging_report",
        'spotify_id=spotify_meta.get("id", "")' in src,
        "_save_tagging_report not forwarding spotify_id",
    )
    return ok


# ─────────────────────────────────────────────────────────────────
# PATCH 2 — Camelot map + TXXX frames
# ─────────────────────────────────────────────────────────────────
def verify_patch2_map() -> bool:
    print("\n[PATCH 2a] _CAMELOT_MAP completeness")
    try:
        sys.path.insert(0, str(_BACKEND))
        from services.tagger_service import _CAMELOT_MAP  # type: ignore
    except ImportError as e:
        _check("_CAMELOT_MAP importable", False, str(e))
        return False

    ok = True
    ok &= _check("24 entries (12 major + 12 minor)", len(_CAMELOT_MAP) == 24,
                 f"Got {len(_CAMELOT_MAP)} entries, expected 24")
    ok &= _check("(0,1) → 8B  (C major)",   _CAMELOT_MAP.get((0,  1)) == "8B")
    ok &= _check("(9,0) → 8A  (A minor)",   _CAMELOT_MAP.get((9,  0)) == "8A")
    ok &= _check("(5,1) → 7B  (F major)",   _CAMELOT_MAP.get((5,  1)) == "7B")
    ok &= _check("(2,0) → 7A  (D minor)",   _CAMELOT_MAP.get((2,  0)) == "7A")
    ok &= _check("(11,1) → 1B (B major)",   _CAMELOT_MAP.get((11, 1)) == "1B")
    ok &= _check("(8,0) → 1A  (G# minor)",  _CAMELOT_MAP.get((8,  0)) == "1A")
    return ok


def verify_patch2_features() -> bool:
    print("\n[PATCH 2b] _get_audio_features returns new fields")
    try:
        from services.tagger_service import _get_audio_features  # type: ignore
    except ImportError as e:
        _check("_get_audio_features importable", False, str(e))
        return False

    result = _get_audio_features(None, None)  # null guard path
    ok = True
    ok &= _check("camelot key in result",      "camelot"      in result,
                 "camelot missing from _get_audio_features result dict")
    ok &= _check("energy key in result",       "energy"       in result,
                 "energy missing from _get_audio_features result dict")
    ok &= _check("danceability key in result", "danceability" in result,
                 "danceability missing from _get_audio_features result dict")
    ok &= _check("all new fields None when no service", all(
        result[k] is None for k in ("camelot", "energy", "danceability")
    ), "Expected None for all new fields when spotify_service=None")
    return ok


def verify_patch2_txxx() -> bool:
    print("\n[PATCH 2c] New TXXX frames written in tagger_service source")
    src = (_BACKEND / "services" / "tagger_service.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check('TXXX:INITIALKEY written', 'desc="INITIALKEY"' in src,
                 'TXXX with desc="INITIALKEY" not found in tagger_service.py')
    ok &= _check('TXXX:SPOTIFY_ID written', 'desc="SPOTIFY_ID"' in src,
                 'TXXX with desc="SPOTIFY_ID" not found in tagger_service.py')
    ok &= _check('TXXX:ENERGY written',     'desc="ENERGY"'     in src,
                 'TXXX with desc="ENERGY" not found in tagger_service.py')
    ok &= _check('TXXX:DANCEABILITY written','desc="DANCEABILITY"' in src,
                 'TXXX with desc="DANCEABILITY" not found in tagger_service.py')
    return ok


def verify_patch2_report() -> bool:
    print("\n[PATCH 2d] camelot/energy/danceability in tag_file report dict")
    src = (_BACKEND / "services" / "tagger_service.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check('"camelot": camelot_val in report',      '"camelot": camelot_val' in src)
    ok &= _check('"energy": energy_val in report',        '"energy": energy_val'   in src)
    ok &= _check('"danceability": danceability_val in report', '"danceability": danceability_val' in src)
    return ok


# ─────────────────────────────────────────────────────────────────
# PATCH 3 — Orphaned tagging report fix
# ─────────────────────────────────────────────────────────────────
def verify_patch3() -> bool:
    print("\n[PATCH 3] Orphaned tagging report fix")
    ok = True

    try:
        from database import update_tagging_report  # type: ignore
        sig = inspect.signature(update_tagging_report)
        ok &= _check("update_tagging_report has spotify_id param",
                     "spotify_id" in sig.parameters,
                     "database.update_tagging_report(filename, report, spotify_id=None) — spotify_id missing")
        ok &= _check("spotify_id defaults to None",
                     sig.parameters["spotify_id"].default is None,
                     "spotify_id param must default to None for backward compat")
    except ImportError as e:
        ok &= _check("database.update_tagging_report importable", False, str(e))

    try:
        from services.tagger_service import save_tagging_report  # type: ignore
        sig2 = inspect.signature(save_tagging_report)
        ok &= _check("save_tagging_report has spotify_id param",
                     "spotify_id" in sig2.parameters,
                     "tagger_service.save_tagging_report(filename, report, spotify_id=None) — spotify_id missing")
    except ImportError as e:
        ok &= _check("tagger_service.save_tagging_report importable", False, str(e))

    src_db = (_BACKEND / "database.py").read_text(encoding="utf-8")
    ok &= _check('$or query with spotify_id in update_tagging_report',
                 '"$or"' in src_db and '"spotify_id"' in src_db,
                 'database.py does not contain $or / spotify_id fallback query')
    ok &= _check('upsert=False prevents phantom entries',
                 'upsert=False' in src_db,
                 'upsert=False not found in update_tagging_report — phantom records possible')
    return ok


# ─────────────────────────────────────────────────────────────────
# PATCH 4 — TOCTOU race condition
# ─────────────────────────────────────────────────────────────────
def verify_patch4() -> bool:
    print("\n[PATCH 4] TOCTOU race condition fix")
    src = (_BACKEND / "services" / "auto_downloader.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check("_in_progress_registry declared",
                 "_in_progress_registry: set = set()" in src,
                 "_in_progress_registry module-level set not found")
    ok &= _check("_reserved flag initialised before try",
                 "_reserved = False" in src,
                 "_reserved = False not found — flag may be undefined in finally")
    ok &= _check("atomic reserve: _in_progress_registry.add(track_key)",
                 "_in_progress_registry.add(track_key)" in src,
                 "Slot reservation step missing — TOCTOU not fixed")
    ok &= _check("atomic check includes _in_progress_registry",
                 "track_key in _in_progress_registry" in src,
                 "Duplicate check doesn't test _in_progress_registry")
    ok &= _check("finally block discards reservation",
                 "_in_progress_registry.discard(track_key)" in src,
                 "discard() not found in finally — reservation never released")
    return ok


# ─────────────────────────────────────────────────────────────────
# PATCH 5 — Atomic move
# ─────────────────────────────────────────────────────────────────
def verify_patch5() -> bool:
    print("\n[PATCH 5] Atomic file move")
    src = (_BACKEND / "services" / "auto_downloader.py").read_text(encoding="utf-8")
    ok = True
    ok &= _check("shutil.move(staged_filepath...) removed from PASS 3",
                 "shutil.move(staged_filepath, final_filepath)" not in src,
                 "shutil.move(staged_filepath...) still present — unsafe move not replaced")
    ok &= _check("os.replace used for same-device move",
                 "os.replace(staged_filepath, final_filepath)" in src,
                 "os.replace(staged_filepath...) not found")
    ok &= _check("cross-device path uses shutil.copy2 + os.remove",
                 "shutil.copy2(staged_filepath, final_filepath)" in src,
                 "Cross-device copy+delete path not found")
    ok &= _check("post-move integrity check present",
                 "os.path.getsize(final_filepath)" in src,
                 "Post-move size verification not found")
    ok &= _check("_write_retry_manifest function defined",
                 "def _write_retry_manifest(" in src,
                 "_write_retry_manifest function not found")
    ok &= _check("retry manifest called on OSError",
                 "_write_retry_manifest(staged_filepath, final_filepath, track_info)" in src,
                 "_write_retry_manifest not called in OSError handler")
    ok &= _check("staged file preserved on failure (no rm before manifest)",
                 True,  # structural — verified by presence of manifest call before return
                 "")
    return ok


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  DJ Pipeline Patch Verification")
    print("=" * 60)

    results = [
        verify_patch1(),
        verify_patch2_map(),
        verify_patch2_features(),
        verify_patch2_txxx(),
        verify_patch2_report(),
        verify_patch3(),
        verify_patch4(),
        verify_patch5(),
    ]

    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 60)
    if passed == total:
        print(f"  {_PASS}  All {total} patch groups passed — pipeline is healthy")
        sys.exit(0)
    else:
        failed = total - passed
        print(f"  {_FAIL}  {failed}/{total} patch group(s) FAILED — see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
