"""
backfill_gemini.py — 4-pass Gemini AI + librosa enrichment.

Run after master_organise.py.  Each pass is independent.

  --pass1   Classify Library/Indian/ remainder → Bollywood or Punjabi
  --pass2   Identify NeedsReview/Unknown/ songs (title + artist + genre)
  --pass3   Enrich all Library/ files missing gemini_genre tag
  --pass4   librosa BPM + key for files missing TBPM / TKEY
  (default = all 4 passes)

  --dry-run   Preview without writing anything
  --limit N   Process at most N files per pass (useful for testing)

Gemini free tier: 1500 requests/day. Rate: 1 file per 2 s is safe.
"""
from __future__ import annotations
import sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import config

BASE    = Path(config.BASE_DOWNLOAD_DIR)
LIB     = BASE / "Library"
NR      = BASE / "NeedsReview"
DRY     = "--dry-run" in sys.argv
LIMIT   = int(next((sys.argv[sys.argv.index("--limit") + 1]
                    for _ in ["x"] if "--limit" in sys.argv), 0))
RATE_S  = 4.0   # seconds between Gemini calls (free tier: ~15 RPM)


def _passes_requested() -> set[int]:
    requested = {i for i in (1, 2, 3, 4) if f"--pass{i}" in sys.argv}
    return requested or {1, 2, 3, 4}


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_tag(path: Path, frame: str) -> str:
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        tags = ID3(str(path))
        val  = tags.get(frame)
        if val is None:
            return ""
        return str(val.text[0] if hasattr(val, "text") else val).strip()
    except Exception:
        return ""


def _read_txxx(path: Path, key: str) -> str:
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        tags = ID3(str(path))
        txxx = tags.get(f"TXXX:{key}")
        if txxx and txxx.text:
            return str(txxx.text[0]).strip()
    except Exception:
        pass
    return ""


def _write_tags(path: Path, data: dict) -> None:
    """Write a dict of {frame_name: value} to ID3 tags. Creates header if missing."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, TCON, TBPM, TKEY
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()

        if "TCON" in data:
            tags["TCON"] = TCON(encoding=3, text=[data["TCON"]])
        if "TBPM" in data and data["TBPM"]:
            tags["TBPM"] = TBPM(encoding=3, text=[str(data["TBPM"])])
        if "TKEY" in data and data["TKEY"]:
            tags["TKEY"] = TKEY(encoding=3, text=[str(data["TKEY"])])

        for txxx_key in ("gemini_genre", "gemini_subgenre", "gemini_mood",
                         "gemini_instruments", "gemini_energy", "gemini_description"):
            if txxx_key in data and data[txxx_key] != "" and data[txxx_key] is not None:
                tags[f"TXXX:{txxx_key}"] = TXXX(
                    encoding=3, desc=txxx_key, text=[str(data[txxx_key])]
                )
        tags.save(str(path))
    except Exception as e:
        print(f"    [tag-warn] {e}")


def _update_mongo_path(old: Path, new: Path) -> None:
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        col.update_one(
            {"final_path": str(old)},
            {"$set": {
                "final_path":   str(new),
                "genre_folder": str(new.parent.relative_to(BASE)),
                "last_seen":    datetime.now(timezone.utc),
            }},
        )
    except Exception as e:
        print(f"    [mongo-warn] {e}")


def _update_mongo_tags(path: Path, data: dict) -> None:
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        update = {k: v for k, v in data.items() if v is not None and v != ""}
        update["last_seen"] = datetime.now(timezone.utc)
        col.update_one({"final_path": str(path)}, {"$set": update})
    except Exception as e:
        print(f"    [mongo-warn] {e}")


def _collision_safe(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem, suf = Path(name).stem, Path(name).suffix
    i = 1
    while True:
        c = dest_dir / f"{stem}_{i}{suf}"
        if not c.exists():
            return c
        i += 1


def _mv(src: Path, dest_dir: Path) -> Path:
    import shutil
    dest = _collision_safe(dest_dir, src.name)
    if not DRY:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        _update_mongo_path(src, dest)
    try:
        print(f"    → {'WOULD MOVE' if DRY else 'MOVED'} to {dest_dir.relative_to(BASE)}/")
    except ValueError:
        print(f"    → {'WOULD MOVE' if DRY else 'MOVED'} to {dest_dir}/")
    return dest


def _genre_to_folder(genre_str: str) -> str | None:
    """Map a Gemini genre string to a Library subfolder name, or None if unknown."""
    from services.genre_router import normalize_genre, GENRE_TAXONOMY
    canonical = normalize_genre(genre_str)
    if canonical and canonical in GENRE_TAXONOMY:
        return GENRE_TAXONOMY[canonical][1]   # e.g. "Bollywood", "Trance"
    # Direct match against known folder names
    known = {d.name for d in LIB.iterdir() if d.is_dir()}
    if genre_str in known:
        return genre_str
    return None


def _parse_retry_delay(err_str: str) -> int:
    """Extract retryDelay seconds from a Gemini error string, default 60."""
    import re
    m = re.search(r"retry in (\d+)", str(err_str))
    if m:
        return max(int(m.group(1)) + 2, 5)
    return 60


def _call_gemini_with_retry(fn, filepath: str, retries: int = 5) -> dict:
    for attempt in range(1, retries + 1):
        try:
            result = fn(filepath)
            if result:
                return result
            # Empty result from fn means it logged the error and returned {}
            wait = 30 * attempt
        except Exception as e:
            wait = _parse_retry_delay(str(e))
        if attempt < retries:
            print(f"    Retrying in {wait}s (attempt {attempt}/{retries})")
            time.sleep(wait)
    return {}


def _call_gemini_identify(filepath: str) -> dict:
    from services.gemini_service import identify_audio
    return _call_gemini_with_retry(identify_audio, filepath)


def _call_gemini_analyze(filepath: str) -> dict:
    from services.gemini_service import analyze_audio
    return _call_gemini_with_retry(analyze_audio, filepath)


# ── PASS 1: Classify Library/Indian/ remainder via Gemini ─────────────────────

def pass1_classify_indian():
    print("\n=== PASS 1: Classify Library/Indian/ via Gemini ===")
    indian = LIB / "Indian"
    if not indian.exists():
        print("  (Library/Indian/ not found — already done)")
        return

    files = sorted(indian.rglob("*.mp3"))
    if LIMIT:
        files = files[:LIMIT]
    print(f"  {len(files)} files to classify")

    for i, f in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {f.name}")
        if DRY:
            print("    (dry run — skip Gemini call)")
            continue
        result = _call_gemini_identify(str(f))
        if not result:
            print("    Gemini failed — leaving in Indian/")
            time.sleep(RATE_S)
            continue

        genre_str = result.get("gemini_genre", "")
        folder    = _genre_to_folder(genre_str)

        if folder and (LIB / folder).resolve() != indian.resolve():
            dest_dir = LIB / folder
            _mv(f, dest_dir)
            # Write gemini tags if we have enriched data
            tags_to_write = {
                "TCON":              genre_str,
                "gemini_genre":      result.get("gemini_genre", ""),
                "gemini_subgenre":   result.get("gemini_subgenre", ""),
                "gemini_mood":       result.get("gemini_mood", ""),
                "gemini_instruments":result.get("gemini_instruments", ""),
                "gemini_energy":     result.get("gemini_energy", ""),
                "gemini_description":result.get("gemini_description", ""),
            }
            moved_file = dest_dir / f.name
            if moved_file.exists():
                _write_tags(moved_file, tags_to_write)
        else:
            print(f"    Gemini genre={genre_str!r} — no folder match, leaving")
        time.sleep(RATE_S)


# ── PASS 2: Identify NeedsReview/Unknown/ songs ───────────────────────────────

def pass2_identify_unknown():
    print("\n=== PASS 2: Identify NeedsReview/Unknown/ via Gemini ===")
    unk = NR / "Unknown"
    if not unk.exists():
        print("  (NeedsReview/Unknown/ not found)")
        return

    files = sorted(unk.rglob("*.mp3"))
    if LIMIT:
        files = files[:LIMIT]
    print(f"  {len(files)} files to identify")

    from mutagen.id3 import ID3, ID3NoHeaderError, TPE1, TIT2

    for i, f in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {f.name}")
        if DRY:
            print("    (dry run — skip Gemini call)")
            continue
        result = _call_gemini_identify(str(f))
        if not result:
            print("    Gemini failed — leaving in Unknown/")
            time.sleep(RATE_S)
            continue

        genre_str = result.get("gemini_genre", "")
        folder    = _genre_to_folder(genre_str)
        title     = result.get("title", "")
        artist    = result.get("artist", "")

        # Write discovered title/artist + gemini tags back to the file
        tags_to_write = {
            "gemini_genre":       genre_str,
            "gemini_subgenre":    result.get("gemini_subgenre", ""),
            "gemini_mood":        result.get("gemini_mood", ""),
            "gemini_instruments": result.get("gemini_instruments", ""),
            "gemini_energy":      result.get("gemini_energy", ""),
            "gemini_description": result.get("gemini_description", ""),
        }
        if genre_str:
            tags_to_write["TCON"] = genre_str
        _write_tags(f, tags_to_write)

        # Also write ID3 title/artist if discovered and currently missing
        if title or artist:
            try:
                try:
                    id3 = ID3(str(f))
                except ID3NoHeaderError:
                    id3 = ID3()
                if title and not id3.get("TIT2"):
                    id3["TIT2"] = TIT2(encoding=3, text=[title])
                if artist and not id3.get("TPE1"):
                    id3["TPE1"] = TPE1(encoding=3, text=[artist])
                id3.save(str(f))
            except Exception as e:
                print(f"    [tag-warn] {e}")

        if folder:
            dest_dir = LIB / folder
            moved = _mv(f, dest_dir)
            _update_mongo_tags(moved if not DRY else f, {
                "gemini_genre":    genre_str,
                "gemini_mood":     result.get("gemini_mood", ""),
            })
        else:
            print(f"    genre={genre_str!r} — no folder match, leaving in Unknown/")
        time.sleep(RATE_S)


# ── PASS 3: Enrich all Library/ files missing gemini_genre ────────────────────

def pass3_enrich_library():
    print("\n=== PASS 3: Enrich Library/ files missing gemini_genre tag ===")

    to_process: list[Path] = []
    for mp3 in sorted(LIB.rglob("*.mp3")):
        existing = _read_txxx(mp3, "gemini_genre")
        if not existing:
            to_process.append(mp3)

    if LIMIT:
        to_process = to_process[:LIMIT]
    print(f"  {len(to_process)} files need enrichment")

    for i, f in enumerate(to_process, 1):
        print(f"  [{i}/{len(to_process)}] {f.name}")
        if DRY:
            print("    (dry run — skip Gemini call)")
            continue
        result = _call_gemini_analyze(str(f))
        if not result:
            print("    Gemini failed — skipping")
            time.sleep(RATE_S)
            continue

        tags_to_write = {
            "gemini_genre":       result.get("gemini_genre", ""),
            "gemini_subgenre":    result.get("gemini_subgenre", ""),
            "gemini_mood":        result.get("gemini_mood", ""),
            "gemini_instruments": result.get("gemini_instruments", ""),
            "gemini_energy":      result.get("gemini_energy", ""),
            "gemini_description": result.get("gemini_description", ""),
        }
        genre = result.get("gemini_genre", "")
        if genre and not _read_tag(f, "TCON"):
            tags_to_write["TCON"] = genre

        _write_tags(f, tags_to_write)
        _update_mongo_tags(f, {
            "gemini_genre":    genre,
            "gemini_mood":     result.get("gemini_mood", ""),
            "gemini_energy":   result.get("gemini_energy"),
        })
        time.sleep(RATE_S)


# ── PASS 4: librosa BPM + key for files missing TBPM / TKEY ──────────────────

def pass4_bpm_key():
    print("\n=== PASS 4: librosa BPM + key for files missing TBPM/TKEY ===")

    to_process: list[Path] = []
    for mp3 in sorted(LIB.rglob("*.mp3")):
        bpm  = _read_tag(mp3, "TBPM")
        tkey = _read_tag(mp3, "TKEY")
        if not bpm or not tkey:
            to_process.append(mp3)

    if LIMIT:
        to_process = to_process[:LIMIT]
    print(f"  {len(to_process)} files missing BPM or key")

    try:
        import librosa
        import numpy as np
    except ImportError:
        print("  librosa not installed — run: pip install librosa")
        return

    for i, f in enumerate(to_process, 1):
        print(f"  [{i}/{len(to_process)}] {f.name}")
        if DRY:
            print("    (dry run — skip librosa)")
            continue
        try:
            y, sr = librosa.load(str(f), sr=None, mono=True, duration=60)
            bpm_float, _ = librosa.beat.beat_track(y=y, sr=sr)
            bpm_val = round(float(bpm_float))

            chroma    = librosa.feature.chroma_cqt(y=y, sr=sr)
            chroma_avg = chroma.mean(axis=1)
            key_idx   = int(np.argmax(chroma_avg))
            KEY_NAMES  = ["C", "C#", "D", "D#", "E", "F",
                          "F#", "G", "G#", "A", "A#", "B"]
            key_str   = KEY_NAMES[key_idx % 12]

            print(f"    BPM={bpm_val}  key={key_str}")
            tags_to_write: dict = {}
            if not _read_tag(f, "TBPM"):
                tags_to_write["TBPM"] = bpm_val
            if not _read_tag(f, "TKEY"):
                tags_to_write["TKEY"] = key_str
            if tags_to_write:
                _write_tags(f, tags_to_write)
                _update_mongo_tags(f, {k.lower(): v for k, v in tags_to_write.items()})
        except Exception as e:
            print(f"    [librosa-warn] {e}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    passes = _passes_requested()
    print(f"Base: {BASE}   DRY_RUN={DRY}   passes={sorted(passes)}")
    if LIMIT:
        print(f"LIMIT={LIMIT} files per pass")

    if 1 in passes:
        pass1_classify_indian()
    if 2 in passes:
        pass2_identify_unknown()
    if 3 in passes:
        pass3_enrich_library()
    if 4 in passes:
        pass4_bpm_key()

    print("\nDone.")


if __name__ == "__main__":
    main()
