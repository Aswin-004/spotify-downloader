"""
BPM and musical key detection service.
Uses librosa to analyze downloaded MP3 files.
Writes results to ID3 tags and MongoDB.
"""
import os
import numpy as np
from pathlib import Path
from loguru import logger
from mutagen.id3 import ID3, TBPM, TKEY, error as ID3Error

# Key detection constants
PITCH_CLASSES = ['C', 'C#', 'D', 'D#', 'E', 'F',
                 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Krumhansl-Schmuckler key profiles
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def detect_bpm_and_key(filepath: str) -> dict:
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
            if bpm > 160:
                bpm = bpm // 2
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

        key_root = PITCH_CLASSES[key_idx]
        key_str  = f"{key_root} {key_mode}"

        logger.info(f"Key detected: {key_str} (confidence: {confidence:.2f})")

        result.update({
            "bpm":        bpm,
            "key":        key_str,
            "key_root":   key_root,
            "key_mode":   key_mode,
            "confidence": round(confidence, 3),
            "analyzed":   True,
            "error":      None
        })

    except Exception as e:
        logger.error(f"BPM/key analysis failed for {filepath}: {e}")
        result["error"] = str(e)

    return result


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
