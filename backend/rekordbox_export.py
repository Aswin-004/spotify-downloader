"""
Rekordbox XML Export — Phase 14
=================================
Generates rekordbox.xml from the MongoDB library_index.

File format: Rekordbox 6 / Engine DJ compatible XML.
Paths are written as file:///absolute/path with forward slashes.
All text is UTF-8.

Playlist buckets (auto-assigned by genre_folder prefix):
  House          — genre_folder starts with "House" or "Tech House"
  UKG            — genre_folder starts with "UK Garage" or "UK Bass"
  Bollywood      — genre_folder starts with "Indian" or "Bollywood"
  NeedsReview    — genre_folder starts with "NeedsReview"
  Warmup         — bpm < 123  (cross-genre slow openers)
  Peak Hour      — bpm >= 128

Usage
-----
    python rekordbox_export.py                    # incremental (only new/changed)
    python rekordbox_export.py --full             # full rebuild
    python rekordbox_export.py --output my.xml    # custom output path
    python rekordbox_export.py --validate-only    # check paths, no write

Exit codes: 0 = success, 1 = path validation errors found.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.dom import minidom

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
DEFAULT_OUTPUT = _BACKEND / "rekordbox.xml"

# Playlist bucket rules — evaluated in order, first match wins.
# Each entry: (playlist_name, matcher_fn(doc) -> bool)
_BUCKET_RULES: list[tuple[str, callable]] = []


def _setup_buckets():
    global _BUCKET_RULES
    _BUCKET_RULES = [
        # Evaluated in priority order — first match wins.
        # _genre_contains supports both old flat paths ("House/Artist")
        # and new Library/ hierarchy paths ("Library/Electronic/House/Artist").
        ("NeedsReview", lambda d: _genre_contains(d, "NeedsReview")),
        ("UKG",         lambda d: _genre_contains(d, ("UKG", "UK Garage", "UK Bass", "Grime"))),
        ("House",       lambda d: _genre_contains(d, ("House", "Afro House"))),
        ("Indian",      lambda d: _genre_contains(d, ("Indian", "Bollywood", "Punjabi",
                                                       "Tamil", "Telugu", "Hindi Pop"))),
        ("HipHop",      lambda d: _genre_contains(d, ("HipHop", "Hip Hop"))),
        ("Warmup",      lambda d: _bpm_lt(d, 123)),
        ("Peak Hour",   lambda d: _bpm_gte(d, 128)),
    ]


def _genre_starts(doc: dict, prefixes) -> bool:
    """Legacy helper — checks genre_folder startswith (old flat paths only)."""
    gf = (doc.get("genre_folder") or "").strip()
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    return any(gf.startswith(p) for p in prefixes)


def _genre_contains(doc: dict, fragments) -> bool:
    """
    Return True if genre_folder contains any of *fragments* as a path component.

    Supports both old flat paths  ("House/Sammy Virji")
    and new Library/ hierarchy    ("Library/Electronic/House/Sammy Virji").
    Matching is case-insensitive and normalises backslashes to forward slashes.
    """
    gf = (doc.get("genre_folder") or "").strip().replace("\\", "/").lower()
    if isinstance(fragments, str):
        fragments = (fragments,)
    for frag in fragments:
        frag_lower = frag.lower()
        # Check as path component: /House/ or starts with House/ or ends with /House
        if (f"/{frag_lower}/" in f"/{gf}/" or
                gf == frag_lower or
                gf.startswith(frag_lower + "/") or
                ("/" + frag_lower) in gf):
            return True
    return False


def _bpm_lt(doc: dict, threshold: float) -> bool:
    bpm = _parse_bpm(doc)
    return bpm is not None and bpm < threshold


def _bpm_gte(doc: dict, threshold: float) -> bool:
    bpm = _parse_bpm(doc)
    return bpm is not None and bpm >= threshold


def _parse_bpm(doc: dict) -> float | None:
    raw = doc.get("bpm") or doc.get("tagging_report", {}).get("bpm")
    if raw is None:
        # Try to read from tags if we have a path
        fp = doc.get("final_path", "")
        if fp and os.path.isfile(fp):
            try:
                from mutagen.id3 import ID3, ID3NoHeaderError
                tags = ID3(fp)
                tbpm = tags.get("TBPM")
                if tbpm and tbpm.text:
                    return float(str(tbpm.text[0]).strip())
            except Exception:
                pass
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ── Path helpers ──────────────────────────────────────────────────────────────

def _to_rekordbox_uri(filepath: str) -> str:
    """Convert absolute OS path to file:/// URI with forward slashes."""
    p = Path(filepath).resolve()
    # On Windows: C:\path\to\file.mp3 → file:///C:/path/to/file.mp3
    parts = p.parts
    if len(parts) > 1 and parts[0].endswith("\\"):
        # Windows drive letter
        drive = parts[0].rstrip("\\").rstrip("/")
        rest = "/".join(parts[1:])
        uri_path = f"{drive}/{rest}"
    else:
        uri_path = str(p).replace("\\", "/")
    # URL-encode the path (preserve / and :)
    encoded = quote(uri_path, safe="/:@")
    return f"file:///{encoded.lstrip('/')}"


def _relative_path(filepath: str) -> str:
    """Return path relative to BASE_DIR using forward slashes."""
    try:
        return str(Path(filepath).relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        return str(Path(filepath)).replace("\\", "/")


# ── ID3 metadata reader ───────────────────────────────────────────────────────

def _read_track_meta(filepath: str) -> dict:
    """Read all relevant ID3 fields for Rekordbox export."""
    meta: dict = {
        "title": "", "artist": "", "album": "", "genre": "",
        "bpm": "", "key": "", "camelot": "", "year": "",
        "track_number": "", "isrc": "", "comment": "",
        "duration_sec": 0, "bitrate": 320, "filesize": 0,
    }
    if not os.path.isfile(filepath):
        return meta
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, ID3NoHeaderError

        audio = MP3(filepath)
        meta["duration_sec"] = int(audio.info.length)
        meta["bitrate"] = int(audio.info.bitrate / 1000)
        meta["filesize"] = os.path.getsize(filepath)

        def _t(key: str) -> str:
            f = audio.tags.get(key) if audio.tags else None
            return str(f.text[0]).strip() if f and f.text else ""

        def _txxx(desc: str) -> str:
            if not audio.tags:
                return ""
            f = audio.tags.get(f"TXXX:{desc}")
            return str(f.text[0]).strip() if f and f.text else ""

        meta.update({
            "title":        _t("TIT2"),
            "artist":       _t("TPE1"),
            "album":        _t("TALB"),
            "genre":        _t("TCON"),
            "bpm":          _t("TBPM"),
            "key":          _t("TKEY"),
            "camelot":      _txxx("INITIALKEY") or _txxx("CAMELOT"),
            "year":         _t("TDRC"),
            "track_number": _t("TRCK"),
            "isrc":         _t("TSRC"),
        })
    except Exception:
        pass
    return meta


# ── XML builders ──────────────────────────────────────────────────────────────

def _build_track_elem(track_id: int, doc: dict) -> ET.Element:
    """Build a <TRACK> element from a library_index document."""
    fp = doc.get("final_path", "")
    meta = _read_track_meta(fp)

    title  = html.escape(meta["title"]  or doc.get("title", ""))
    artist = html.escape(meta["artist"] or doc.get("artist", ""))
    album  = html.escape(meta["album"]  or "")
    genre  = html.escape(meta["genre"]  or doc.get("genre_folder", "").split("/")[0])
    bpm    = meta["bpm"] or ""
    camelot = meta["camelot"] or ""
    year   = str(meta["year"])[:4] if meta["year"] else ""
    loc    = _to_rekordbox_uri(fp) if fp else ""
    date_added = ""
    if doc.get("indexed_at"):
        try:
            date_added = doc["indexed_at"].strftime("%Y-%m-%d")
        except Exception:
            pass

    attrs = {
        "TrackID":      str(track_id),
        "Name":         title,
        "Artist":       artist,
        "Album":        album,
        "Genre":        genre,
        "Kind":         "MP3 File",
        "Size":         str(meta["filesize"]),
        "TotalTime":    str(meta["duration_sec"]),
        "DiscNumber":   "0",
        "TrackNumber":  str(meta["track_number"]) if meta["track_number"] else "0",
        "Year":         year,
        "AverageBpm":   f"{float(bpm):.2f}" if bpm else "0.00",
        "DateAdded":    date_added,
        "BitRate":      str(meta["bitrate"]),
        "SampleRate":   "44100",
        "Comments":     "",
        "PlayCount":    "0",
        "Rating":       "0",
        "Location":     loc,
        "Remixer":      "",
        "Tonality":     camelot,  # Rekordbox reads Camelot from Tonality
        "Label":        "",
        "Composer":     "",
        "Mix":          "",
    }
    elem = ET.Element("TRACK", attrs)

    # Embed tempo marker when BPM is known
    if bpm:
        try:
            ET.SubElement(elem, "TEMPO", {
                "Inizio": "0.000",
                "Bpm":    f"{float(bpm):.2f}",
                "Metro":  "4/4",
                "Battito":"1",
            })
        except ValueError:
            pass

    return elem


def _build_playlist_node(name: str, track_ids: list[int]) -> ET.Element:
    node = ET.Element("NODE", {
        "Name":    name,
        "Type":    "1",
        "KeyType": "0",
        "Entries": str(len(track_ids)),
    })
    for tid in track_ids:
        ET.SubElement(node, "TRACK", {"Key": str(tid)})
    return node


# ── Main export ───────────────────────────────────────────────────────────────

def load_index_docs(incremental: bool, last_export_ts: datetime | None) -> list[dict]:
    """Load documents from library_index, optionally filtered to new/changed."""
    try:
        from database import get_library_index_collection
        col = get_library_index_collection()
        query: dict = {}
        if incremental and last_export_ts:
            query["last_seen"] = {"$gt": last_export_ts}
        docs = list(col.find(query, {"_id": 0}))
        logger.info(f"[rekordbox] Loaded {len(docs)} track(s) from library_index "
                    f"({'incremental' if incremental and last_export_ts else 'full'})")
        return docs
    except Exception as e:
        logger.error(f"[rekordbox] Cannot load library_index: {e}")
        return []


def validate_paths(docs: list[dict]) -> list[str]:
    """Return list of final_path values that are missing on disk."""
    missing = []
    for doc in docs:
        fp = doc.get("final_path", "")
        if fp and not os.path.isfile(fp):
            missing.append(fp)
    return missing


def build_xml(docs: list[dict]) -> tuple[ET.Element, dict[str, list[int]]]:
    """
    Build the full DJ_PLAYLISTS XML tree.
    Returns (root_element, bucket_map).
    """
    _setup_buckets()

    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {
        "Name": "rekordbox", "Version": "6.0.0", "Company": "AlphaTheta"
    })

    # Filter to docs with an accessible file
    valid_docs = [d for d in docs if d.get("final_path") and os.path.isfile(d["final_path"])]
    dedup_paths: set[str] = set()
    unique_docs: list[dict] = []
    for d in valid_docs:
        fp = d["final_path"]
        if fp not in dedup_paths:
            dedup_paths.add(fp)
            unique_docs.append(d)

    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(unique_docs))})
    buckets: dict[str, list[int]] = {name: [] for name, _ in _BUCKET_RULES}
    all_ids: list[int] = []

    for idx, doc in enumerate(unique_docs, start=1):
        elem = _build_track_elem(idx, doc)
        collection.append(elem)
        all_ids.append(idx)
        for name, matcher in _BUCKET_RULES:
            if matcher(doc):
                buckets[name].append(idx)
                break  # first match wins

    # Build PLAYLISTS section
    playlists_root = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(playlists_root, "NODE", {
        "Type": "0", "Name": "ROOT", "Count": str(len(_BUCKET_RULES) + 1)
    })
    # All Tracks playlist
    all_node = ET.SubElement(root_node, "NODE", {
        "Name": "All Tracks", "Type": "1", "KeyType": "0",
        "Entries": str(len(all_ids)),
    })
    for tid in all_ids:
        ET.SubElement(all_node, "TRACK", {"Key": str(tid)})

    for name, _ in _BUCKET_RULES:
        tids = buckets[name]
        if tids:
            root_node.append(_build_playlist_node(name, tids))

    return root, buckets


def prettify(root: ET.Element) -> str:
    """Return indented UTF-8 XML string."""
    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent="  ", encoding=None)
    # minidom adds a redundant XML declaration — replace it
    lines = pretty.splitlines()
    lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    return "\n".join(lines)


def export(
    output_path: Path = DEFAULT_OUTPUT,
    full: bool = False,
    validate_only: bool = False,
) -> int:
    """
    Run the export.  Returns 0 on success, 1 on validation errors.
    """
    last_ts: datetime | None = None
    incremental = not full

    # For incremental mode, read the timestamp of the last export
    if incremental and output_path.exists():
        last_ts = datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.utc)
        logger.info(f"[rekordbox] Incremental mode — changes since {last_ts.isoformat()}")

    docs = load_index_docs(incremental, last_ts)
    if not docs:
        logger.warning("[rekordbox] No tracks found — nothing to export")
        return 0

    # Path validation
    missing = validate_paths(docs)
    if missing:
        for p in missing[:10]:
            logger.warning(f"[rekordbox] Missing file: {p}")
        if len(missing) > 10:
            logger.warning(f"[rekordbox] … and {len(missing) - 10} more missing files")
        logger.warning(f"[rekordbox] {len(missing)} path(s) invalid — they will be skipped")

    if validate_only:
        logger.info(f"[rekordbox] Validate-only: {len(docs)} tracks, {len(missing)} invalid")
        return 1 if missing else 0

    # For full rebuild over an existing XML, merge with old doc set
    if full and output_path.exists():
        logger.info("[rekordbox] Full rebuild — discarding previous export")

    root, buckets = build_xml(docs)
    xml_str = prettify(root)

    output_path.write_text(xml_str, encoding="utf-8")
    total = int(root.find("COLLECTION").get("Entries", "0"))
    logger.info(f"[rekordbox] Exported {total} track(s) → {output_path}")

    bucket_summary = ", ".join(
        f"{name}: {len(ids)}" for name, ids in buckets.items() if ids
    )
    logger.info(f"[rekordbox] Playlists — {bucket_summary}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Generate Rekordbox XML from DJ library")
    parser.add_argument("--full",          action="store_true", help="Full rebuild (ignore last export timestamp)")
    parser.add_argument("--output",        default=str(DEFAULT_OUTPUT), metavar="FILE")
    parser.add_argument("--validate-only", action="store_true", help="Check paths only, no file written")
    args = parser.parse_args()
    sys.exit(export(Path(args.output), full=args.full, validate_only=args.validate_only))


if __name__ == "__main__":
    main()
