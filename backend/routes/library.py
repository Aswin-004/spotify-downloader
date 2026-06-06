"""
Library Routes Blueprint
=========================
File browsing, artwork, audio preview, track tag editing, duplicate management,
library organisation, and Rekordbox export.
"""
import os
import shutil
from pathlib import Path
from flask import Blueprint, jsonify, request, send_from_directory, Response

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from services.auto_downloader import BASE_DOWNLOAD_DIR

# BPM/KEY — import backfill function
try:
    from bpm_key_service import backfill_library
    _BPM_KEY_AVAILABLE = True
except ImportError:
    _BPM_KEY_AVAILABLE = False

library_bp = Blueprint("library", __name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_existing_files():
    """Scan BASE_DOWNLOAD_DIR recursively for all .mp3 files."""
    files = []
    if not os.path.isdir(BASE_DOWNLOAD_DIR):
        return files
    for root, _dirs, filenames in os.walk(BASE_DOWNLOAD_DIR):
        for fname in filenames:
            if fname.lower().endswith(".mp3"):
                full = os.path.join(root, fname)
                rel_folder = os.path.relpath(root, BASE_DOWNLOAD_DIR)
                if rel_folder == ".":
                    rel_folder = ""
                files.append({
                    "name": fname,
                    "folder": rel_folder,
                    "path": full,
                    "mtime": os.path.getmtime(full),
                })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


# ── BPM backfill ──────────────────────────────────────────────────────────────

@library_bp.route("/api/library/analyze-bpm", methods=["POST"])
def analyze_bpm():
    """Backfill BPM + key for all existing MP3s."""
    if not _BPM_KEY_AVAILABLE:
        return jsonify({"error": "bpm_key_service not available — install librosa"}), 503
    result = backfill_library(BASE_DOWNLOAD_DIR or os.getenv("BASE_DOWNLOAD_DIR", "downloads"))
    return jsonify(result)


# ── File browsing ─────────────────────────────────────────────────────────────

@library_bp.route("/api/files", methods=["GET"])
def get_all_files():
    """Get all MP3 files from the download directory tree."""
    return jsonify({"success": True, "files": _load_existing_files()}), 200


@library_bp.route("/api/files/folder-tags", methods=["GET"])
def folder_tags():
    """Return BPM, Camelot key, artist, title for every MP3 in one folder.

    Query param: folder — relative path, e.g. 'Library/House'
    """
    from mutagen.id3 import ID3, ID3NoHeaderError
    from bpm_key_service import tkey_to_camelot as _t2c

    folder = request.args.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder param required"}), 400

    target = Path(BASE_DOWNLOAD_DIR) / folder
    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(Path(BASE_DOWNLOAD_DIR).resolve())):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_dir():
        return jsonify({}), 200

    result = {}
    for mp3 in sorted(resolved.glob("*.mp3")):
        try:
            tags    = ID3(str(mp3))
            bpm_f   = tags.get("TBPM")
            key_f   = tags.get("TKEY")
            tpe_f   = tags.get("TPE1")
            tit_f   = tags.get("TIT2")
            bpm_raw = str(bpm_f.text[0]).strip() if bpm_f and bpm_f.text else ""
            tkey    = str(key_f.text[0]).strip() if key_f and key_f.text else ""
            try:
                bpm_val = int(float(bpm_raw)) if bpm_raw else None
            except ValueError:
                bpm_val = None
            result[mp3.name] = {
                "bpm":         bpm_val,
                "camelot_key": _t2c(tkey),
                "artist":      str(tpe_f.text[0]).strip() if tpe_f and tpe_f.text else "",
                "title":       str(tit_f.text[0]).strip() if tit_f and tit_f.text else mp3.stem,
            }
        except (ID3NoHeaderError, Exception):
            result[mp3.name] = {"bpm": None, "camelot_key": "", "artist": "", "title": mp3.stem}

    return jsonify(result), 200


# ── Artwork + audio ───────────────────────────────────────────────────────────

@library_bp.route("/api/artwork", methods=["GET"])
def get_artwork():
    """Serve embedded album art (APIC frame) from an MP3 file."""
    filepath = request.args.get("path", "").strip()
    if not filepath:
        return "", 400
    full = os.path.realpath(
        os.path.join(BASE_DOWNLOAD_DIR, filepath)
        if not os.path.isabs(filepath) else filepath
    )
    if not full.startswith(os.path.realpath(BASE_DOWNLOAD_DIR) + os.sep):
        return "", 403
    if not os.path.isfile(full):
        return "", 404
    try:
        from mutagen.id3 import ID3
        tags = ID3(full)
        apic = tags.get("APIC:") or tags.get("APIC:Cover")
        if not apic:
            return "", 404
        return Response(
            apic.data,
            mimetype=apic.mime or "image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return "", 404


@library_bp.route("/api/preview-track", methods=["GET"])
def preview_track():
    """Stream an audio file for in-browser preview.

    Query params:
      filename  — bare filename (e.g. "Song - Artist.mp3")
      path      — relative path from BASE_DOWNLOAD_DIR (e.g. "Library/House/Song.mp3")
    """
    filename = request.args.get("filename", "").strip()
    rel_path  = request.args.get("path", "").strip()

    if not filename and not rel_path:
        return jsonify({"error": "filename or path required"}), 400

    if rel_path:
        target = Path(BASE_DOWNLOAD_DIR) / rel_path
    else:
        target = Path(BASE_DOWNLOAD_DIR) / "Library" / "Electronic" / filename

    try:
        resolved = target.resolve()
        base_resolved = Path(BASE_DOWNLOAD_DIR).resolve()
        if not str(resolved).startswith(str(base_resolved)):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_file():
        return jsonify({"error": "File not found"}), 404

    return send_from_directory(
        str(resolved.parent),
        resolved.name,
        mimetype="audio/mpeg",
        conditional=True,
    )


# ── Track tag editing ─────────────────────────────────────────────────────────

@library_bp.route("/api/track/bpm", methods=["POST"])
def update_track_bpm():
    """Manually correct BPM (and optionally key) for a track."""
    data = request.get_json(force=True) or {}
    filename  = str(data.get("filename", "")).strip()
    rel_path  = str(data.get("path", "")).strip()
    bpm_raw   = data.get("bpm")
    key_str   = str(data.get("key", "")).strip() or None

    if not filename and not rel_path:
        return jsonify({"error": "filename or path required"}), 400
    if bpm_raw is None:
        return jsonify({"error": "bpm required"}), 400

    try:
        bpm = float(bpm_raw)
        if bpm <= 0 or bpm > 300:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "bpm must be a positive number ≤ 300"}), 400

    if rel_path:
        target = Path(BASE_DOWNLOAD_DIR) / rel_path
    else:
        candidate = None
        try:
            from database import get_library_index_collection
            doc = get_library_index_collection().find_one(
                {"filename": filename}, {"folder": 1}
            )
            if doc and doc.get("folder"):
                p = Path(BASE_DOWNLOAD_DIR) / doc["folder"] / filename
                if p.is_file():
                    candidate = p
        except Exception:
            pass
        if not candidate:
            p = Path(BASE_DOWNLOAD_DIR) / "Library" / "Electronic" / filename
            if p.is_file():
                candidate = p
            else:
                found = list(Path(BASE_DOWNLOAD_DIR).rglob(filename))
                candidate = found[0] if found else p
        target = candidate

    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(Path(BASE_DOWNLOAD_DIR).resolve())):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        from bpm_key_service import write_bpm_key_to_tags, persist_audio_features
        write_bpm_key_to_tags(str(resolved), bpm, key_str)
        persist_audio_features(
            resolved.stem,
            {"bpm": bpm, "key": key_str, "analyzed": True, "manual": True},
        )
        logger.info(f"[bpm-update] {resolved.name} → {bpm} BPM · {key_str or 'key unchanged'}")
        return jsonify({"ok": True, "bpm": bpm, "key": key_str, "filename": resolved.name}), 200
    except Exception as e:
        logger.error(f"[bpm-update] Failed for {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@library_bp.route("/api/track/tags", methods=["POST"])
def update_track_tags():
    """Edit artist and/or title ID3 tags for any Library file."""
    data     = request.get_json(force=True) or {}
    rel_path = str(data.get("path", "")).strip()
    new_artist = str(data.get("artist", "")).strip() if data.get("artist") is not None else None
    new_title  = str(data.get("title",  "")).strip() if data.get("title")  is not None else None

    if not rel_path:
        return jsonify({"error": "path required"}), 400
    if new_artist is None and new_title is None:
        return jsonify({"error": "artist or title required"}), 400

    target = Path(BASE_DOWNLOAD_DIR) / rel_path
    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(Path(BASE_DOWNLOAD_DIR).resolve())):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Invalid path"}), 400

    if not resolved.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        from mutagen.id3 import ID3, TPE1, TIT2, ID3NoHeaderError
        try:
            tags = ID3(str(resolved))
        except ID3NoHeaderError:
            tags = ID3()

        if new_artist is not None:
            tags["TPE1"] = TPE1(encoding=3, text=[new_artist])
        if new_title is not None:
            tags["TIT2"] = TIT2(encoding=3, text=[new_title])
        tags.save(str(resolved))

        try:
            import datetime
            from database import get_library_index_collection
            col = get_library_index_collection()
            update = {"last_seen": datetime.datetime.now(datetime.timezone.utc)}
            if new_artist is not None:
                update["artist"] = new_artist
            if new_title is not None:
                update["title"] = new_title
            rel_folder = str(resolved.parent.relative_to(Path(BASE_DOWNLOAD_DIR))).replace("\\", "/")
            col.update_one(
                {"filename": resolved.name, "folder": rel_folder},
                {"$set": update},
            )
        except Exception:
            pass

        logger.info(f"[tag-edit] {resolved.name} — artist={new_artist!r} title={new_title!r}")
        return jsonify({"ok": True, "artist": new_artist, "title": new_title, "filename": resolved.name}), 200
    except Exception as e:
        logger.error(f"[tag-edit] {rel_path}: {e}")
        return jsonify({"error": str(e)}), 500


# ── Move + remember ───────────────────────────────────────────────────────────

@library_bp.route("/api/move-and-remember", methods=["POST"])
def move_and_remember():
    """Move a track to a chosen genre folder and record the artist→genre memory."""
    data = request.get_json() or {}
    filepath = (data.get("filepath") or "").strip()
    genre    = (data.get("genre") or "").strip()
    artist   = (data.get("artist") or "").strip()
    if not filepath or not genre:
        return jsonify({"error": "filepath and genre required"}), 400
    try:
        from services.genre_router import normalize_genre, GENRE_TAXONOMY
        _display_map = {"Drum & Bass": "Drum and Bass"}
        canonical = normalize_genre(_display_map.get(genre, genre)) or _display_map.get(genre, genre)
        entry = GENRE_TAXONOMY.get(canonical)
        if not entry:
            return jsonify({"error": f"Unknown genre: {genre}"}), 400
        _, subfolder = entry
        genre_path = f"Library/{subfolder}"
        full_path = filepath if os.path.isabs(filepath) else os.path.join(BASE_DOWNLOAD_DIR, filepath)
        try:
            Path(full_path).resolve().relative_to(Path(BASE_DOWNLOAD_DIR).resolve())
        except ValueError:
            return jsonify({"error": "Invalid path"}), 400
        src = Path(full_path)
        if not src.exists():
            return jsonify({"error": f"File not found: {filepath}"}), 404
        dest_dir = Path(BASE_DOWNLOAD_DIR) / genre_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.move(str(src), str(dest))
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                col.update_one(
                    {"final_path": full_path},
                    {"$set": {"final_path": str(dest), "genre_folder": genre_path}},
                )
        except Exception:
            pass
        if artist:
            from services.artist_memory_service import record_move as _record_move
            _record_move(artist, canonical, source="manual_move")
            logger.info(f"[move-and-remember] {artist!r} → {canonical} (manual)")
        logger.info(f"[move-and-remember] {src.name} → {genre_path}")
        return jsonify({"moved": True, "new_folder": genre_path, "genre": canonical}), 200
    except Exception as e:
        logger.error(f"[move-and-remember] {e}")
        return jsonify({"error": str(e)}), 500


# ── Organise ──────────────────────────────────────────────────────────────────

@library_bp.route("/api/library/organize", methods=["POST"])
def library_organize():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "artist")
    if mode not in ("artist", "genre", "artist_genre", "dj"):
        return jsonify({"success": False, "error": f"Invalid mode: {mode}"}), 400
    try:
        from services.organizer_service import organize_library
        result = organize_library(mode=mode)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"[library/organize] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@library_bp.route("/api/library/organize-recent", methods=["POST"])
def library_organize_recent():
    data  = request.get_json(silent=True) or {}
    mode  = data.get("mode", "artist")
    hours = int(data.get("hours", 24))
    if mode not in ("artist", "genre", "artist_genre"):
        return jsonify({"success": False, "error": f"Invalid mode: {mode}"}), 400
    try:
        from services.organizer_service import organize_recent
        result = organize_recent(mode=mode, hours=hours)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"[library/organize-recent] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ── Duplicates ────────────────────────────────────────────────────────────────

@library_bp.route("/api/duplicates", methods=["GET"])
def list_duplicates():
    """Return all files sitting in NeedsReview/Duplicates/."""
    from mutagen.id3 import ID3 as _ID3
    dup_dir = Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates"
    items = []
    if dup_dir.is_dir():
        for fp in sorted(dup_dir.glob("*.mp3")):
            try:
                tags = _ID3(str(fp))
                title  = str(tags.get("TIT2", fp.stem)).strip() or fp.stem
                artist = str(tags.get("TPE1", "")).strip()
            except Exception:
                title, artist = fp.stem, ""
            items.append({"filename": fp.name, "title": title, "artist": artist})
    return jsonify({"duplicates": items}), 200


@library_bp.route("/api/duplicates/delete", methods=["POST"])
def delete_duplicate():
    """Permanently delete a file from NeedsReview/Duplicates/."""
    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Invalid filename"}), 400
    target = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates" / filename).resolve()
    allowed = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates").resolve()
    if not str(target).startswith(str(allowed)):
        return jsonify({"error": "Access denied"}), 403
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404
    target.unlink()
    logger.info(f"[duplicates] Deleted: {filename}")
    return jsonify({"deleted": True, "filename": filename}), 200


@library_bp.route("/api/duplicates/keep", methods=["POST"])
def keep_duplicate():
    """Move a duplicate to the main library under a chosen genre."""
    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    genre    = (data.get("genre") or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Invalid filename"}), 400
    if not genre:
        return jsonify({"error": "genre required"}), 400

    src = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates" / filename).resolve()
    allowed = (Path(BASE_DOWNLOAD_DIR) / "NeedsReview" / "Duplicates").resolve()
    if not str(src).startswith(str(allowed)):
        return jsonify({"error": "Access denied"}), 403
    if not src.is_file():
        return jsonify({"error": "File not found"}), 404

    from services.genre_router import normalize_genre, _library_path
    canonical = normalize_genre(genre)
    lib_path  = _library_path(canonical) if canonical else f"Library/{genre}"
    dest_dir  = Path(BASE_DOWNLOAD_DIR) / lib_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    logger.info(f"[duplicates] Kept: {filename} → {lib_path}")
    return jsonify({"moved": True, "filename": filename, "destination": lib_path}), 200


# ── Rekordbox export ──────────────────────────────────────────────────────────

@library_bp.route("/api/export/rekordbox", methods=["GET"])
def export_rekordbox():
    """Generate a Rekordbox-compatible XML playlist for a library folder."""
    import xml.etree.ElementTree as _ET
    from mutagen.id3 import ID3 as _ID3
    from mutagen.mp3 import MP3 as _MP3
    from flask import make_response

    folder_param  = request.args.get("folder", "all").strip()
    playlist_name = request.args.get("name", "").strip() or folder_param

    base = Path(BASE_DOWNLOAD_DIR).resolve()

    if folder_param.lower() == "all":
        scan_root = base
    else:
        scan_root = (base / folder_param).resolve()
        if not str(scan_root).startswith(str(base)):
            return jsonify({"error": "Access denied"}), 403

    if not scan_root.is_dir():
        return jsonify({"error": "Folder not found"}), 404

    mp3_files = sorted(scan_root.rglob("*.mp3"))

    root_el = _ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    _ET.SubElement(root_el, "PRODUCT", Name="rekordbox", Version="6.8.5", Company="AlphaTheta")
    collection = _ET.SubElement(root_el, "COLLECTION", Entries=str(len(mp3_files)))

    for idx, mp3_path in enumerate(mp3_files, start=1):
        title, artist, bpm_val, key_val, duration_sec = "", "", "", "", 0
        try:
            tags = _ID3(str(mp3_path))
            title   = str(tags.get("TIT2", mp3_path.stem))
            artist  = str(tags.get("TPE1", ""))
            bpm_raw = str(tags.get("TBPM", ""))
            bpm_val = bpm_raw.split(".")[0] if bpm_raw else ""
            key_val = str(tags.get("TKEY", ""))
        except Exception:
            title = mp3_path.stem
        try:
            audio = _MP3(str(mp3_path))
            duration_sec = int(audio.info.length)
        except Exception:
            pass

        file_uri = "file://localhost/" + str(mp3_path).replace("\\", "/").lstrip("/")
        _ET.SubElement(
            collection, "TRACK",
            TrackID=str(idx),
            Name=title,
            Artist=artist,
            TotalTime=str(duration_sec),
            Tonality=key_val,
            AverageBpm=bpm_val,
            Location=file_uri,
            Kind="MP3 File",
        )

    playlists_el = _ET.SubElement(root_el, "PLAYLISTS")
    root_node    = _ET.SubElement(playlists_el, "NODE", Type="0", Name="ROOT", Count="1")
    pl_node      = _ET.SubElement(root_node, "NODE", Name=playlist_name, Type="1",
                                   KeyType="0", Entries=str(len(mp3_files)))
    for idx in range(1, len(mp3_files) + 1):
        _ET.SubElement(pl_node, "TRACK", Key=str(idx))

    xml_bytes = _ET.tostring(root_el, encoding="utf-8", xml_declaration=True)
    safe_name = "".join(c for c in playlist_name if c.isalnum() or c in " -_").strip() or "playlist"
    resp = make_response(xml_bytes)
    resp.headers["Content-Type"]        = "application/xml; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe_name}.xml"'
    return resp


# ── Library Gap Analysis ──────────────────────────────────────────────────────

@library_bp.route("/api/library/gaps", methods=["GET"])
def library_gaps():
    """
    Analyse the Library folder and return a gap report:
      • genre_counts   — {genre: track_count} for all Library/ sub-folders
      • thin_genres    — genres with < 8 tracks (need more music)
      • artist_counts  — {artist: track_count} across entire library (from ID3 tags)
      • solo_artists   — artists with exactly 1 track in library (worth exploring)
      • total_tracks   — total MP3 count in Library/
    """
    from mutagen.id3 import ID3 as _ID3, ID3NoHeaderError as _NoHeader
    from collections import defaultdict

    library_root = Path(BASE_DOWNLOAD_DIR) / "Library"
    if not library_root.is_dir():
        return jsonify({
            "genre_counts": {}, "thin_genres": [],
            "artist_counts": {}, "solo_artists": [],
            "total_tracks": 0,
        })

    genre_counts: dict[str, int]  = defaultdict(int)
    artist_counts: dict[str, int] = defaultdict(int)
    total = 0

    for mp3 in library_root.rglob("*.mp3"):
        # Determine genre from direct parent folder name
        try:
            genre_rel = mp3.parent.relative_to(library_root)
            genre = str(genre_rel).split(os.sep)[0] if str(genre_rel) != "." else "Uncategorized"
        except ValueError:
            genre = "Uncategorized"

        genre_counts[genre] += 1
        total += 1

        # Extract artist from ID3 tags (fall back to filename heuristic)
        try:
            tags   = _ID3(str(mp3))
            artist = str(tags.get("TPE1", "")).strip()
        except (_NoHeader, Exception):
            artist = ""

        if not artist:
            # "Song Title - Artist Name.mp3" convention
            parts = mp3.stem.rsplit(" - ", 1)
            artist = parts[-1].strip() if len(parts) == 2 else ""

        if artist:
            artist_counts[artist] += 1

    THIN_THRESHOLD  = 8
    SOLO_MAX_TRACKS = 1

    thin_genres = sorted(
        [{"genre": g, "count": c} for g, c in genre_counts.items() if c <= THIN_THRESHOLD],
        key=lambda x: x["count"],
    )
    solo_artists = sorted(
        [{"artist": a, "count": c} for a, c in artist_counts.items()
         if c <= SOLO_MAX_TRACKS and a],
        key=lambda x: x["artist"].lower(),
    )[:50]  # cap at 50

    return jsonify({
        "genre_counts":  dict(sorted(genre_counts.items(), key=lambda x: -x[1])),
        "thin_genres":   thin_genres,
        "artist_counts": dict(sorted(artist_counts.items(), key=lambda x: -x[1])[:30]),
        "solo_artists":  solo_artists,
        "total_tracks":  total,
    })
