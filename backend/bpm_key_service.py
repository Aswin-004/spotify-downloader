"""
BPM and musical key detection service.
Uses librosa to analyze downloaded MP3 files.
Writes results to ID3 tags and MongoDB.
"""
from datetime import datetime, timezone
import os
import numpy as np
from pathlib import Path
from loguru import logger
from mutagen.id3 import ID3, TBPM, TKEY, error as ID3Error

ANALYSIS_VERSION = "librosa-1.0"

# Fields persist_audio_features() owns inside the audio_features sub-document.
# Anything NOT listed here (lastfm_*, gemini_*, or any future enrichment) is
# written by other services and must survive a BPM/key write untouched.
_CORE_FEATURE_FIELDS = ("bpm", "key", "key_root", "key_mode", "camelot", "confidence")
_OPTIONAL_FEATURE_FIELDS = ("duration_sec", "rms_energy",
                            "spectral_centroid_mean", "zero_crossing_rate")

# Key detection constants
PITCH_CLASSES = ['C', 'C#', 'D', 'D#', 'E', 'F',
                 'F#', 'G', 'G#', 'A', 'A#', 'B']

_PITCH_TO_IDX = {p: i for i, p in enumerate(PITCH_CLASSES)}

_CAMELOT_MAP = {
    (0,  "maj"): "8B",  (0,  "min"): "5A",
    (1,  "maj"): "3B",  (1,  "min"): "12A",
    (2,  "maj"): "10B", (2,  "min"): "7A",
    (3,  "maj"): "5B",  (3,  "min"): "2A",
    (4,  "maj"): "12B", (4,  "min"): "9A",
    (5,  "maj"): "7B",  (5,  "min"): "4A",
    (6,  "maj"): "2B",  (6,  "min"): "11A",
    (7,  "maj"): "9B",  (7,  "min"): "6A",
    (8,  "maj"): "4B",  (8,  "min"): "1A",
    (9,  "maj"): "11B", (9,  "min"): "8A",
    (10, "maj"): "6B",  (10, "min"): "3A",
    (11, "maj"): "1B",  (11, "min"): "10A",
}

def tkey_to_camelot(tkey: str) -> str:
    """Convert a TKEY string (e.g. 'F# min', 'C maj', 'A') to Camelot notation."""
    if not tkey:
        return ""
    t = tkey.strip()
    # Determine mode
    if "min" in t.lower() or t.lower().endswith("m"):
        mode = "min"
    else:
        mode = "maj"
    # Extract root — strip mode suffixes
    root = t.replace(" min", "").replace(" maj", "").replace(" major", "")
    root = root.replace(" minor", "").rstrip("m").strip()
    # Normalise flats → sharps
    _FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Fb": "E", "Gb": "F#",
                      "Ab": "G#", "Bb": "A#", "Cb": "B"}
    root = _FLAT_TO_SHARP.get(root, root)
    idx = _PITCH_TO_IDX.get(root)
    if idx is None:
        return ""
    return _CAMELOT_MAP.get((idx, mode), "")


# Krumhansl-Schmuckler key profiles
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def detect_bpm_and_key(filepath: str, genre_hint: str = "") -> dict:
    """
    Analyze an MP3 file and return BPM + musical key.
    Returns: {
        bpm: int,
        key: str,          # e.g. "F# min"
        key_root: str,     # e.g. "F#"
        key_mode: str,     # "maj" or "min"
        confidence: float, # 0.0 - 1.0
        analyzed: bool,
        error: str or None
    }
    """
    result = {
        "bpm": None,
        "key": None,
        "key_root": None,
        "key_mode": None,
        "confidence": 0.0,
        "analyzed": False,
        "error": None
    }

    try:
        import librosa

        path = Path(filepath)
        if not path.exists():
            result["error"] = f"File not found: {filepath}"
            return result

        logger.info(f"Analyzing BPM + key: {path.name}")

        # load audio — use up to 30s from middle for speed + accuracy
        duration = librosa.get_duration(path=filepath)
        offset = max(0, duration / 2 - 15)  # start 15s before midpoint
        analysis_duration = min(30.0, max(0.0, duration - offset))

        y, sr = librosa.load(
            filepath,
            sr=22050,                   # standard sample rate
            mono=True,
            offset=offset,
            duration=analysis_duration  # keep window within file length
        )

        # --- BPM detection ---
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo_values = np.atleast_1d(tempo)
        bpm = None
        if tempo_values.size > 0:
            bpm = int(round(float(tempo_values[0])))

        # sanity check — reject unrealistic BPM
        if bpm is None:
            logger.warning(f"No BPM detected for {path.name} — skipping")
        elif bpm < 40 or bpm > 250:
            logger.warning(f"Unrealistic BPM {bpm} for {path.name} — skipping")
            bpm = None
        else:
            # handle half/double tempo common in librosa
            _hint = genre_hint.lower()
            _dnb = _hint in ("dnb", "drum and bass", "drum & bass", "d&b")
            _techno = _hint in ("techno", "industrial", "minimal techno")
            if bpm > 190:
                bpm = bpm // 2  # extreme double-tempo, safe for all genres
            elif 155 <= bpm <= 190:
                if not _dnb:
                    bpm = bpm // 2  # double-tempo for non-DnB genres (house, bollywood, etc.)
                # DnB: keep native tempo (160-180 BPM is correct)
            elif 78 <= bpm <= 95 and _dnb:
                bpm = bpm * 2  # librosa half-time lock on DnB snare pattern (×2 stays ≤190)
            elif 70 <= bpm <= 80 and _techno:
                bpm = bpm * 2  # librosa half-time lock on Techno kick (70-75 → 140-150 BPM)
            elif bpm < 70:
                bpm = bpm * 2
            logger.debug(f"BPM detected: {bpm}")

        # --- Key detection (Krumhansl-Schmuckler) ---
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)
        major_scores = []
        minor_scores = []
        valid_major_score_found = False
        valid_minor_score_found = False
        for i in range(12):
            rotated = np.roll(chroma_mean, -i)

            major_corr = np.corrcoef(rotated, MAJOR_PROFILE)[0, 1]
            if np.isfinite(major_corr):
                major_scores.append(float(major_corr))
                valid_major_score_found = True
            else:
                logger.warning(
                    f"Invalid major key correlation for {path.name} at rotation {i}; "
                    "using fallback score"
                )
                major_scores.append(-1.0)

            minor_corr = np.corrcoef(rotated, MINOR_PROFILE)[0, 1]
            if np.isfinite(minor_corr):
                minor_scores.append(float(minor_corr))
                valid_minor_score_found = True
            else:
                logger.warning(
                    f"Invalid minor key correlation for {path.name} at rotation {i}; "
                    "using fallback score"
                )
                minor_scores.append(-1.0)

        if not valid_major_score_found and not valid_minor_score_found:
            logger.warning(
                f"Unable to detect key for {path.name}: all key correlation scores were invalid"
            )
            key_idx = None
            key_mode = None
            confidence = 0.0
            key_root = None
            key_str = None
        else:
            best_major = max(major_scores)
            best_minor = max(minor_scores)

            if best_major >= best_minor:
                key_idx  = major_scores.index(best_major)
                key_mode = "maj"
                confidence = float(best_major)
            else:
                key_idx  = minor_scores.index(best_minor)
                key_mode = "min"
                confidence = float(best_minor)

            # Moved inside this else (was unconditional below): key_idx is only ever
            # an int here — in the "both invalid" branch above it stays None, and
            # PITCH_CLASSES[None] raised TypeError, which the outer except caught and
            # discarded the ALREADY-COMPUTED bpm from earlier in this function along
            # with everything else, returning analyzed=False for the whole call
            # instead of "BPM known, key unknown".
            key_root = PITCH_CLASSES[key_idx]
            key_str  = f"{key_root} {key_mode}"

        logger.info(f"Key detected: {key_str} (confidence: {confidence:.2f})")

        # --- Optional lightweight features (all scalars — no arrays stored) ---
        camelot = None
        if key_root and key_mode:
            camelot = _CAMELOT_MAP.get((_PITCH_TO_IDX.get(key_root, -1), key_mode))

        rms_energy            = float(np.mean(librosa.feature.rms(y=y)))
        spectral_centroid_mean= float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        zero_crossing_rate    = float(np.mean(librosa.feature.zero_crossing_rate(y=y)))

        result.update({
            "bpm":                   bpm,
            "key":                   key_str,
            "key_root":              key_root,
            "key_mode":              key_mode,
            "camelot":               camelot,
            "confidence":            round(confidence, 3),
            "duration_sec":          round(duration, 2),
            "rms_energy":            round(rms_energy, 6),
            "spectral_centroid_mean":round(spectral_centroid_mean, 2),
            "zero_crossing_rate":    round(zero_crossing_rate, 6),
            "analyzed":              True,
            "error":                 None,
        })

    except Exception as e:
        logger.error(f"BPM/key analysis failed for {filepath}: {e}")
        result["error"] = str(e)

    return result


def persist_audio_features(identity_key: str, result: dict, _col=None) -> bool:
    """
    Write audio analysis features to library_index as an additive sub-document.
    Never touches final_path, genre_folder, routing, or identity fields.
    Returns True on successful write, False if skipped or failed.

    Writes field-scoped dotted paths (``audio_features.bpm`` etc.) rather than
    replacing the whole ``audio_features`` sub-document.  Replacing it deleted
    every key this function does not itself produce — in particular the
    ``lastfm_*`` and ``gemini_*`` enrichment written by tagger_service /
    backfill_lastfm / backfill_ai.  That was reachable in normal ingest:
    auto_downloader enriches a track (Last.fm, then Gemini) and *then*
    re-persists BPM for DnB/Techno half-time correction, which wiped the
    enrichment it had just written.

    A field is only written when the incoming *result* actually carries a
    value for it, so a partial write (e.g. a manual BPM-only correction) can
    no longer null out an existing camelot/confidence/energy reading.  This
    mirrors the convention already used for the optional feature fields and
    for the fingerprint fields in database.index_track().

    Pass *_col* to inject a collection object instead of resolving the real
    one (tests only) — matches the `docs=`/`_docs=` injection convention used
    by services/recommendation_service.py.
    """
    if not result.get("analyzed") or not identity_key:
        return False
    try:
        col = _col
        if col is None:
            from database import get_library_index_collection
            col = get_library_index_collection()

        if not col.count_documents({"identity_key": identity_key}, limit=1):
            logger.debug(f"[bpm_key_service] identity_key not in library_index — skip persist: {identity_key}")
            return False

        now = datetime.now(timezone.utc)
        updates: dict = {
            "audio_features.analysis_source":    "librosa",
            "audio_features.analysis_version":   ANALYSIS_VERSION,
            "audio_features.analysis_timestamp": now.isoformat(),
        }
        for field in _CORE_FEATURE_FIELDS + _OPTIONAL_FEATURE_FIELDS:
            val = result.get(field)
            if val is not None:
                updates[f"audio_features.{field}"] = val

        col.update_one(
            {"identity_key": identity_key},
            {"$set": updates},
        )
        logger.debug(f"[bpm_key_service] audio_features persisted for {identity_key}")
        return True
    except Exception as e:
        logger.error(f"[bpm_key_service] persist_audio_features failed for {identity_key}: {e}")
        return False


def _norm_path(p: str) -> str:
    """Platform-correct path normalisation for comparison.

    library_index.final_path is stored with MIXED separators on Windows
    (e.g. ``C:\\...\\DJ music\\Library/Bollywood\\Track.mp3``), so an exact
    string match against a resolved Path fails for a large share of rows.
    os.path.normcase collapses separators and case on Windows and is a no-op
    on POSIX.
    """
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(str(p)))


def resolve_identity_key(final_path: str = "", filename: str = "", _col=None) -> str:
    """Resolve an existing library_index document's identity_key for a file.

    identity_key is ``sp:<spotify_id>`` — never a filename stem — so callers
    holding only a path must look the document up rather than synthesising a
    key.  Resolution order, most reliable first:

      1. exact final_path match (native and posix spelling)
      2. separator/case-normalised final_path match
      3. unique basename-of-final_path match
      4. unique library_index.filename match — final_path carries the
         on-disk collision suffix (``Track_1.mp3``) while filename keeps the
         un-suffixed name, so this is a distinct lookup, not a duplicate of 3

    Steps 3 and 4 require exactly ONE live match: 382 final_path basenames and
    8 filenames are ambiguous in the live index, and persisting BPM onto the
    wrong track is worse than not persisting at all.  Soft-deleted rows
    (``missing: True``) are excluded.  Returns "" when nothing resolves
    unambiguously — callers must treat that as "not persisted".
    """
    if not final_path and not filename:
        return ""
    try:
        col = _col
        if col is None:
            from database import get_library_index_collection
            col = get_library_index_collection()

        live = {"missing": {"$ne": True}}
        proj = {"_id": 0, "identity_key": 1, "final_path": 1}

        if final_path:
            spellings = {str(final_path), str(final_path).replace("\\", "/")}
            doc = col.find_one({**live, "final_path": {"$in": list(spellings)}}, proj)
            if doc and doc.get("identity_key"):
                return doc["identity_key"]

            target = _norm_path(final_path)
            target_base = target.rsplit(os.sep, 1)[-1]
            norm_hits, base_hits = [], []
            for d in col.find(live, proj):
                ik = d.get("identity_key")
                if not ik:
                    continue
                cand = _norm_path(d.get("final_path") or "")
                if not cand:
                    continue
                if cand == target:
                    norm_hits.append(ik)
                elif cand.rsplit(os.sep, 1)[-1] == target_base:
                    base_hits.append(ik)
            if len(set(norm_hits)) == 1:
                return norm_hits[0]
            if norm_hits:
                logger.warning(
                    f"[bpm_key_service] ambiguous final_path ({len(set(norm_hits))} matches) "
                    f"— refusing to guess: {final_path}"
                )
                return ""
            if len(set(base_hits)) == 1:
                return base_hits[0]
            if base_hits:
                logger.warning(
                    f"[bpm_key_service] ambiguous basename ({len(set(base_hits))} matches) "
                    f"— refusing to guess: {final_path}"
                )
                return ""

        if filename:
            name_hits = [
                d["identity_key"]
                for d in col.find({**live, "filename": filename}, proj)
                if d.get("identity_key")
            ]
            if len(set(name_hits)) == 1:
                return name_hits[0]
            if name_hits:
                logger.warning(
                    f"[bpm_key_service] ambiguous filename ({len(set(name_hits))} matches) "
                    f"— refusing to guess: {filename}"
                )

        return ""
    except Exception as e:
        logger.error(f"[bpm_key_service] resolve_identity_key failed for {final_path or filename}: {e}")
        return ""


def write_bpm_key_to_tags(filepath: str, bpm: int, key: str) -> bool:
    """Write BPM and key into MP3 ID3 tags using Mutagen."""
    try:
        tags = ID3(filepath)
        if bpm:
            tags.add(TBPM(encoding=3, text=str(bpm)))
        if key:
            tags.add(TKEY(encoding=3, text=key))
        tags.save()
        logger.debug(f"ID3 tags written — BPM: {bpm}, Key: {key}")
        return True
    except ID3Error as e:
        logger.error(f"ID3 write failed for {filepath}: {e}")
        return False
    except Exception as e:
        logger.error(f"Tag write error for {filepath}: {e}")
        return False


def write_bpm_key_to_mongo(filename: str, bpm: int, key: str, confidence: float):
    """Update download_history MongoDB record with BPM and key."""
    try:
        from database import get_download_history_collection
        col = get_download_history_collection()
        result = col.update_one(
            {"filename": filename},
            {"$set": {
                "bpm":            bpm,
                "key":            key,
                "key_confidence": confidence,
                "bpm_analyzed":   True
            }}
        )
        if result.matched_count == 0:
            logger.warning(f"No MongoDB record for {filename} — BPM/key not saved to DB")
        else:
            logger.debug(f"MongoDB updated — {filename}: BPM={bpm}, key={key}")
    except Exception as e:
        logger.error(f"MongoDB BPM/key update failed for {filename}: {e}")


def analyze_and_tag(filepath: str, filename: str) -> dict:
    """
    Full pipeline: detect BPM + key, write to ID3 tags and MongoDB.
    Call this after tagger_service completes.
    Returns the detection result dict.
    """
    result = detect_bpm_and_key(filepath)

    if result["analyzed"]:
        write_bpm_key_to_tags(filepath, result["bpm"], result["key"])
        write_bpm_key_to_mongo(filename, result["bpm"], result["key"], result["confidence"])
        logger.success(f"BPM/key complete — {filename}: {result['bpm']} BPM · {result['key']}")
    else:
        logger.warning(f"BPM/key skipped for {filename}: {result.get('error')}")

    return result


def backfill_library(base_dir: str) -> dict:
    """
    Batch analyze all MP3s in BASE_DOWNLOAD_DIR that have no BPM tag yet.
    Call this once to backfill existing library.
    """
    from database import get_download_history_collection
    col   = get_download_history_collection()
    base  = Path(base_dir)
    stats = {"analyzed": 0, "skipped": 0, "errors": 0}
    analyzed_filenames = {
        record["filename"]
        for record in col.find({"bpm_analyzed": True}, {"filename": 1, "_id": 0})
        if record.get("filename")
    }

    mp3_files = list(base.rglob("*.mp3"))
    logger.info(f"Backfill: found {len(mp3_files)} MP3s in {base_dir}")

    for mp3 in mp3_files:
        try:
            # skip if already analyzed
            if mp3.name in analyzed_filenames:
                stats["skipped"] += 1
                continue

            result = analyze_and_tag(str(mp3), mp3.name)
            if result["analyzed"]:
                stats["analyzed"] += 1
            else:
                stats["errors"] += 1

        except Exception as e:
            logger.error(f"Backfill error for {mp3.name}: {e}")
            stats["errors"] += 1

    logger.success(f"Backfill complete: {stats}")
    return stats
