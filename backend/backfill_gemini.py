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
RATE_S  = 3.0   # seconds between AI calls — 20 req/min, safely under Groq's 30 RPM limit


def _passes_requested() -> set[int]:
    requested = {i for i in (1, 2, 3, 4, 5, 6, 7) if f"--pass{i}" in sys.argv}
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
                "genre_folder": new.parent.relative_to(BASE).as_posix(),
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

        # Try Last.fm first (free, no quota)
        lastfm_genre = ""
        try:
            from services.lastfm_service import lookup_genre as _lfm
            title_tag  = _read_tag(f, "TIT2")
            artist_tag = _read_tag(f, "TPE1")
            if title_tag and artist_tag:
                lastfm_genre = _lfm(title_tag, artist_tag)
                if lastfm_genre:
                    print(f"    Last.fm → {lastfm_genre}")
        except Exception as _le:
            print(f"    [lastfm-warn] {_le}")

        if lastfm_genre:
            _write_tags(f, {"gemini_genre": lastfm_genre})
            _update_mongo_tags(f, {"gemini_genre": lastfm_genre})
            continue  # skip Gemini for this file

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

# Genre keywords that indicate fast tempos (librosa often halves these)
_FAST_GENRES = {"electronic", "house", "trance", "techno", "hardstyle",
                "drum", "bass", "garage", "grime", "dubstep"}
# Genre keywords that indicate slow tempos (librosa sometimes doubles these)
_SLOW_GENRES = {"hip hop", "r&b", "rnb", "soul", "jazz", "blues",
                "reggae", "bollywood", "punjabi", "latin", "afrobeats"}


def _correct_bpm_for_genre(bpm: int, genre: str) -> int:
    """Apply genre-aware BPM halving/doubling correction without re-running librosa."""
    g = genre.lower()
    is_fast = any(kw in g for kw in _FAST_GENRES)
    is_slow = any(kw in g for kw in _SLOW_GENRES)
    if bpm < 90 and is_fast:
        return bpm * 2   # librosa locked on sub-harmonic
    if bpm > 160 and is_slow:
        return bpm // 2  # librosa locked on double-time
    if bpm < 70:
        return bpm * 2   # unrealistically slow for any common genre
    return bpm


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
        from bpm_key_service import detect_bpm_and_key
    except ImportError:
        print("  bpm_key_service not available — librosa may not be installed")
        return

    for i, f in enumerate(to_process, 1):
        print(f"  [{i}/{len(to_process)}] {f.name}")
        if DRY:
            print("    (dry run — skip librosa)")
            continue
        try:
            genre_hint = _read_tag(f, "TCON")
            result = detect_bpm_and_key(str(f), genre_hint=genre_hint)
            if not result.get("analyzed"):
                print(f"    [skip] {result.get('error', 'no result')}")
                continue
            bpm_val = result["bpm"]
            key_str = result.get("key", "")
            print(f"    BPM={bpm_val}  key={key_str}  conf={result.get('confidence', 0):.2f}")
            tags_to_write: dict = {}
            if bpm_val and not _read_tag(f, "TBPM"):
                tags_to_write["TBPM"] = bpm_val
            if key_str and not _read_tag(f, "TKEY"):
                tags_to_write["TKEY"] = key_str
            if tags_to_write:
                _write_tags(f, tags_to_write)
                _update_mongo_tags(f, {k.lower(): v for k, v in tags_to_write.items()})
        except Exception as e:
            print(f"    [librosa-warn] {e}")


def pass4b_correct_bpm_halving():
    """Fast mathematical correction for existing TBPM tags that look genre-mismatched."""
    print("\n=== PASS 4b: correct genre-mismatched BPM tags ===")
    corrected = 0
    for mp3 in sorted(LIB.rglob("*.mp3")):
        bpm_str = _read_tag(mp3, "TBPM")
        if not bpm_str:
            continue
        try:
            bpm = int(float(bpm_str))
        except ValueError:
            continue
        genre = _read_tag(mp3, "TCON") or ""
        fixed = _correct_bpm_for_genre(bpm, genre)
        if fixed != bpm:
            print(f"  {mp3.name}: {bpm} → {fixed}  ({genre or 'no genre'})")
            if not DRY:
                _write_tags(mp3, {"TBPM": fixed})
                _update_mongo_tags(mp3, {"tbpm": fixed})
            corrected += 1
    print(f"  Corrected {corrected} BPM tags")


# ── PASS 5: Embed album artwork from Spotify ──────────────────────────────────

def pass5_embed_artwork():
    print("\n=== PASS 5: Embed album artwork from Spotify ===")

    try:
        import requests as _requests
        from mutagen.id3 import ID3, APIC, ID3NoHeaderError
    except ImportError as e:
        print(f"  Missing dependency: {e} — run: pip install requests mutagen")
        return

    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service().sp
    except Exception as e:
        print(f"  Spotify unavailable: {e}")
        return

    to_process: list[Path] = []
    for mp3 in sorted(LIB.rglob("*.mp3")):
        try:
            tags = ID3(str(mp3))
            if not tags.getall("APIC"):
                to_process.append(mp3)
        except Exception:
            to_process.append(mp3)

    if LIMIT:
        to_process = to_process[:LIMIT]
    print(f"  {len(to_process)} files missing artwork")

    embedded = 0
    for i, f in enumerate(to_process, 1):
        print(f"  [{i}/{len(to_process)}] {f.name}")
        if DRY:
            print("    (dry run — skip)")
            continue

        title     = _read_tag(f, "TIT2") or f.stem
        artist    = _read_tag(f, "TPE1") or ""
        spotify_id = _read_txxx(f, "SPOTIFY_ID")
        if not title:
            print("    no title tag — skip")
            continue

        try:
            import re as _re

            img_url = ""

            # Fast path: use stored Spotify ID to skip search entirely
            if spotify_id:
                try:
                    track_data = sp.track(spotify_id)
                    img_url = (track_data.get("album", {}).get("images") or [{}])[0].get("url", "")
                    if img_url:
                        print(f"    via spotify_id={spotify_id[:8]}…")
                except Exception:
                    pass  # fall through to search

            # Slow path: search by title + artist
            if not img_url:
                clean_title = _re.sub(r'\s*[\(\[]From[^\)\]]*[\)\]]', '', title, flags=_re.IGNORECASE).strip()
                clean_title = _re.sub(r'\s*-\s*(From|OST|Soundtrack).*$', '', clean_title, flags=_re.IGNORECASE).strip()

                def _search(t, a):
                    q = f"track:{t}"
                    if a:
                        q += f" artist:{a}"
                    r = sp.search(q=q, type="track", limit=1)
                    return r.get("tracks", {}).get("items", [])

                items = _search(clean_title, artist) or _search(clean_title, "") or _search(title, "")
                if not items:
                    print("    Spotify: no match")
                    continue
                found_track = items[0]
                img_url = (found_track.get("album", {}).get("images") or [{}])[0].get("url", "")
                if not spotify_id:
                    spotify_id = found_track.get("id", "")
            if not img_url:
                print("    no artwork URL")
                continue

            img_data = _requests.get(img_url, timeout=10).content
            try:
                tags = ID3(str(f))
            except ID3NoHeaderError:
                tags = ID3()
            tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img_data)
            # Also backfill Spotify ID if we found it and it wasn't already set
            if spotify_id and not _read_txxx(f, "SPOTIFY_ID"):
                from mutagen.id3 import TXXX as _TXXX2
                tags[f"TXXX:SPOTIFY_ID"] = _TXXX2(encoding=3, desc="SPOTIFY_ID", text=[spotify_id])
            tags.save(str(f))
            print(f"    ✓ embedded ({len(img_data)//1024} KB){' + spotify_id' if spotify_id and not _read_txxx(f, 'SPOTIFY_ID') else ''}")
            embedded += 1
            time.sleep(1.0)  # 1 req/s Spotify rate limit
        except Exception as e:
            print(f"    [artwork-warn] {e}")

    print(f"  Embedded artwork for {embedded}/{len(to_process)} files")


# ── PASS 6: Backfill TXXX:SPOTIFY_ID + artwork via MusicBrainz fallback ───────

def pass6_backfill_spotify_id():
    """Search Spotify for files missing TXXX:SPOTIFY_ID; also embed artwork
    for remaining gaps using MusicBrainz Cover Art Archive as fallback."""
    print("\n=== PASS 6: Backfill SpotifyID + MusicBrainz artwork ===")

    try:
        import requests as _req
        from mutagen.id3 import ID3, APIC, TXXX as _TXXX, ID3NoHeaderError
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service().sp
    except Exception as e:
        print(f"  Setup failed: {e}")
        return

    # Build lists: files missing SpotifyID, files missing artwork
    no_sid: list[Path] = []
    no_art: list[Path] = []
    for mp3 in sorted(LIB.rglob("*.mp3")):
        try:
            tags = ID3(str(mp3))
            sid  = _read_txxx(mp3, "SPOTIFY_ID")
            if not sid:
                no_sid.append(mp3)
            if not tags.getall("APIC"):
                no_art.append(mp3)
        except Exception:
            no_sid.append(mp3)

    if LIMIT:
        no_sid = no_sid[:LIMIT]
    print(f"  Missing SpotifyID: {len(no_sid)}  |  Missing artwork: {len(no_art)}")

    sid_found = art_found = 0
    no_art_set = set(no_art)

    def _embed_artwork_from_url(url: str, path: Path) -> bool:
        try:
            data = _req.get(url, timeout=10).content
            tags = ID3(str(path))
            tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data)
            tags.save(str(path))
            return True
        except Exception:
            return False

    import re as _re  # moved outside the per-file loop

    for i, f in enumerate(no_sid, 1):
        title  = _read_tag(f, "TIT2") or f.stem
        artist = _read_tag(f, "TPE1") or ""
        print(f"  [{i}/{len(no_sid)}] {f.name}")
        if DRY:
            print("    (dry run — skip)")
            continue

        spotify_id = ""
        img_url    = ""
        try:
            clean = _re.sub(r'\s*[\(\[]From[^\)\]]*[\)\]]', '', title, flags=_re.IGNORECASE).strip()
            q = f"track:{clean}"
            if artist:
                q += f" artist:{artist}"
            items = sp.search(q=q, type="track", limit=1).get("tracks", {}).get("items", [])
            if items:
                track      = items[0]
                spotify_id = track.get("id", "")
                img_url    = (track.get("album", {}).get("images") or [{}])[0].get("url", "")
        except Exception as e:
            print(f"    [spotify-warn] {e}")

        if spotify_id:
            try:
                tags = ID3(str(f))
                tags.add(_TXXX(encoding=3, desc="SPOTIFY_ID", text=[spotify_id]))
                # Embed artwork if missing and Spotify has it
                if f in no_art_set and img_url:
                    data = _req.get(img_url, timeout=10).content
                    tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data)
                    art_found += 1
                    no_art_set.discard(f)
                tags.save(str(f))
                got_art = img_url and f not in no_art_set  # discard already removed it
                print(f"    ✓ spotify_id={spotify_id[:8]}…" + (" + artwork" if got_art else ""))
                sid_found += 1
            except Exception as e:
                print(f"    [write-warn] {e}")
        else:
            print("    no Spotify match")

        time.sleep(1.0)

    # MusicBrainz fallback for files still missing artwork after Spotify pass
    remaining_no_art = [f for f in no_art if f in no_art_set]
    if LIMIT:
        remaining_no_art = remaining_no_art[:LIMIT]
    if remaining_no_art:
        print(f"\n  MusicBrainz artwork fallback: {len(remaining_no_art)} files")
        for j, f in enumerate(remaining_no_art, 1):
            title  = _read_tag(f, "TIT2") or f.stem
            artist = _read_tag(f, "TPE1") or ""
            print(f"  [{j}/{len(remaining_no_art)}] {f.name}")
            if DRY:
                continue
            try:
                mb_url  = "https://musicbrainz.org/ws/2/recording"
                params  = {"query": f'recording:"{title}" AND artist:"{artist}"', "limit": 1, "fmt": "json"}
                r       = _req.get(mb_url, params=params, timeout=8,
                                   headers={"User-Agent": "ObsidianDJ/1.0 (aswin.abhinab22@gmail.com)"})
                recs    = r.json().get("recordings", [])
                mbid    = (recs[0].get("releases") or [{}])[0].get("id", "") if recs else ""
                if mbid:
                    art = _req.get(f"https://coverartarchive.org/release/{mbid}/front",
                                   timeout=10, allow_redirects=True)
                    if art.status_code == 200 and art.content:
                        tags = ID3(str(f))
                        tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art.content)
                        tags.save(str(f))
                        print(f"    ✓ MusicBrainz artwork embedded")
                        art_found += 1
                    else:
                        print(f"    no cover art found")
                else:
                    print(f"    no MusicBrainz match")
            except Exception as e:
                print(f"    [mb-warn] {e}")
            time.sleep(1.0)

    print(f"\n  SpotifyIDs written: {sid_found}/{len(no_sid)}")
    print(f"  Artwork embedded:   {art_found}")


# ── PASS 7: AcoustID fingerprint → ISRC → Spotify ID (exact match) ────────────

def pass7_acoustid_spotify_id():
    """
    For files still missing TXXX:SPOTIFY_ID after pass 6:
      1. Fingerprint via fpcalc → AcoustID API → MusicBrainz recording
      2. Extract ISRC from the recording
      3. Search Spotify by ISRC (exact, no title/artist guessing)
      4. Write Spotify track ID back to TXXX:SPOTIFY_ID
    Requires ACOUSTID_API_KEY env var (free at acoustid.org).
    """
    print("\n=== PASS 7: AcoustID fingerprint → Spotify ID (ISRC exact match) ===")

    import os as _os
    if not _os.getenv("ACOUSTID_API_KEY"):
        print("  ACOUSTID_API_KEY not set — skipping")
        return

    try:
        from mutagen.id3 import ID3, TXXX as _TXXX, ID3NoHeaderError
        from services.spotify_service import get_spotify_service
        from services.musicbrainz_service import _get as _mb_get, _ACOUSTID_BASE, _ACOUSTID_API_KEY, _MB_BASE
        import urllib.parse as _up
        from services.fingerprint_service import _run_fpcalc
        sp = get_spotify_service().sp
    except Exception as e:
        print(f"  Setup failed: {e}")
        return

    to_process = [f for f in sorted(LIB.rglob("*.mp3")) if not _read_txxx(f, "SPOTIFY_ID")]
    if LIMIT:
        to_process = to_process[:LIMIT]
    print(f"  {len(to_process)} files missing SpotifyID")

    found = 0
    for i, f in enumerate(to_process, 1):
        print(f"  [{i}/{len(to_process)}] {f.name}")
        if DRY:
            print("    (dry run — skip)")
            continue
        try:
            fp_str, duration = _run_fpcalc(str(f))
            if not fp_str:
                print("    fpcalc failed")
                continue

            # AcoustID lookup with ISRC metadata
            params = _up.urlencode({
                "client":      _ACOUSTID_API_KEY,
                "duration":    int(duration),
                "fingerprint": fp_str,
                "meta":        "recordings+recordingids+compress",
            })
            data    = _mb_get(f"{_ACOUSTID_BASE}/lookup?{params}")
            results = data.get("results", [])
            if not results:
                print("    no AcoustID match")
                continue

            best = max(results, key=lambda r: r.get("score", 0))
            if best.get("score", 0) < 0.85:
                print(f"    low confidence ({best.get('score',0):.2f}) — skip")
                continue

            mb_ids = [r.get("id") for r in best.get("recordings", []) if r.get("id")]
            if not mb_ids:
                print("    no MusicBrainz recording ID")
                continue

            # Fetch ISRCs from MusicBrainz
            rec = _mb_get(f"{_MB_BASE}/recording/{mb_ids[0]}?inc=isrcs&fmt=json")
            isrcs = rec.get("isrcs", [])
            if not isrcs:
                print("    no ISRC in MusicBrainz")
                continue

            # Search Spotify by ISRC — exact match
            isrc = isrcs[0]
            items = sp.search(q=f"isrc:{isrc}", type="track", limit=1).get("tracks", {}).get("items", [])
            if not items:
                print(f"    ISRC {isrc} not on Spotify")
                continue

            spotify_id = items[0]["id"]
            tags = ID3(str(f))
            tags.add(_TXXX(encoding=3, desc="SPOTIFY_ID", text=[spotify_id]))
            tags.save(str(f))
            print(f"    ✓ ISRC={isrc} → spotify_id={spotify_id[:8]}…")
            found += 1

        except Exception as e:
            print(f"    [error] {e}")
        time.sleep(1.5)  # AcoustID rate limit

    print(f"\n  SpotifyIDs written via AcoustID: {found}/{len(to_process)}")


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
        pass4b_correct_bpm_halving()
    if 5 in passes:
        pass5_embed_artwork()
    if 6 in passes:
        pass6_backfill_spotify_id()
    if 7 in passes:
        pass7_acoustid_spotify_id()

    print("\nDone.")


if __name__ == "__main__":
    main()
