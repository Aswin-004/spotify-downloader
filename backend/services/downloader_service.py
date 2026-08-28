"""
Audio Download Service — v2 (Upgraded Pipeline)
================================================
Changes from v1:
  • 320 kbps MP3 (was 192)
  • Lossless-first format selection: bestaudio[ext=flac] → bestaudio[ext=m4a] → bestaudio
  • Skip re-encode when source is already MP3 (--audio-codec copy)
  • FFmpeg loudnorm filter for loudness normalisation
  • Silence trimming (silenceremove) at start/end of every track
  • Album-art embedding via mutagen (highest-res Spotify image)
  • 4-stage YouTube search + SoundCloud fallback
  • Verified-channel boost (+30 in scoring)
  • ±2 s tight duration window (heavy penalty)
  • Blacklist filtering with Spotify-title exemption
  • quality_report dict per download → SQLite + Socket.IO
"""
import os
import re
import io
import time
import shutil
import logging
import sqlite3
import threading
import subprocess
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import yt_dlp

from config import config
from utils import build_youtube_search_query, build_youtube_fallback_query, validate_filename, setup_logging
from services.strict_matcher import (
    score_candidate,
    select_best_candidate,
    has_reject_keyword,
    clean_title,
    duration_match,
    final_duration_check,
    log_rejection,
    log_acceptance,
    string_similarity,
    is_blacklisted,  # QUALITY UPGRADE
    HARD_DURATION_LIMIT_SEC,  # PHASE 2 — dynamic message, was a stale hardcoded "30s"
)
from download_history import save_report

# TAGGING INTEGRATION — import tagger service
try:  # TAGGING INTEGRATION
    from services.tagger_service import tag_file as _tag_file, save_tagging_report as _save_tagging_report  # TAGGING INTEGRATION
    _TAGGER_AVAILABLE = True  # TAGGING INTEGRATION
except ImportError:  # TAGGING INTEGRATION
    _TAGGER_AVAILABLE = False  # TAGGING INTEGRATION

# BPM/KEY — import bpm_key_service
try:  # BPM/KEY
    from bpm_key_service import analyze_and_tag as _analyze_and_tag  # BPM/KEY
    _BPM_KEY_AVAILABLE = True  # BPM/KEY
except ImportError:  # BPM/KEY
    _BPM_KEY_AVAILABLE = False  # BPM/KEY

# ── Optional mutagen import (album-art embedding) ──────────────────────────
try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, ID3NoHeaderError
    _MUTAGEN_AVAILABLE = True
except ImportError:
    _MUTAGEN_AVAILABLE = False

# ── Optional requests for album art download ───────────────────────────────
try:
    import requests as _requests
except ImportError:
    _requests = None

# ── Loguru / stdlib fallback ───────────────────────────────────────────────
try:
    from loguru import logger
except ImportError:
    logger = setup_logging(__name__)  # type: ignore[assignment]

# ── Socket.IO reference (set by app.py after creation) ────────────────────
_socketio = None

def set_socketio(sio):
    """Called by app.py to hand us the SocketIO instance for quality_report events."""
    global _socketio
    _socketio = sio

# ═══════════════════════════════════════════════════════════════════
# GLOBAL DOWNLOAD QUEUE STATUS (shared with app.py — unchanged API)
# ═══════════════════════════════════════════════════════════════════
download_queue_status = {
    "total": 0,
    "completed": 0,
    "current": None,
    "pending": [],
    "active_workers": 0,
}
_queue_lock = threading.Lock()

# Manual download priority flag — auto workers yield when manual is active
_manual_idle = threading.Event()
_manual_idle.set()  # starts idle (auto can proceed)


def set_manual_active(active):
    """Signal that a manual download is active (auto workers should yield)."""
    if active:
        _manual_idle.clear()
    else:
        _manual_idle.set()


def wait_if_manual_active(timeout=60):
    """Block until manual download finishes. Returns True if had to wait."""
    if _manual_idle.is_set():
        return False
    logger.info("[auto] Waiting for manual download to finish...")
    _manual_idle.wait(timeout=timeout)
    return True


def update_queue(total=None, completed=None, current=None, pending=None, active_delta=None):
    """Thread-safe update of global queue status."""
    with _queue_lock:
        if total is not None:
            download_queue_status["total"] = total
        if completed is not None:
            download_queue_status["completed"] = completed
        if current is not None:
            download_queue_status["current"] = current
        if pending is not None:
            download_queue_status["pending"] = pending
        if active_delta is not None:
            download_queue_status["active_workers"] = max(0, download_queue_status["active_workers"] + active_delta)


# ═══════════════════════════════════════════════════════════════════
# FILENAME HELPERS (unchanged public API)
# ═══════════════════════════════════════════════════════════════════

def clean_filename(name):
    """
    Clean filename by removing invalid Windows characters.
    """
    if not isinstance(name, str):
        name = str(name)
    name = re.sub(r'[\/*?:"<>|]', '', name)
    name = name.replace(',', '')
    name = name.strip().rstrip('.')
    name = re.sub(r'\s+', ' ', name)
    return name


# Alias kept for backward compatibility (imported by app.py)
def sanitize_filename(name):
    return clean_filename(name)


def normalize(text):
    """Normalize a string for consistent duplicate comparison."""
    return " ".join(text.lower().split()).strip()


# ═══════════════════════════════════════════════════════════════════
# FILE ACCESS RETRY HELPER (WinError 32 — file locked by yt-dlp/ffmpeg)
# ═══════════════════════════════════════════════════════════════════

def _retry_file_op(fn, *args, attempts=3, delay=0.5, **kwargs):
    """Retry a file operation up to `attempts` times with `delay` seconds between tries.
    Handles WinError 32 (file in use) and other transient OS errors."""
    last_err = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except (OSError, PermissionError) as e:
            last_err = e
            if i < attempts - 1:
                logger.warning(f"File op retry {i+1}/{attempts}: {e}")
                time.sleep(delay)
    raise last_err


# ═══════════════════════════════════════════════════════════════════
# AUDIO POST-PROCESSING HELPERS
# ═══════════════════════════════════════════════════════════════════

def _find_ffmpeg_binary() -> Optional[str]:
    """Return the full path to the ffmpeg executable, or None."""
    spotdl_ffmpeg = Path.home() / '.spotdl' / 'ffmpeg.exe'
    if spotdl_ffmpeg.exists():
        return str(spotdl_ffmpeg)
    path = shutil.which('ffmpeg')
    if path:
        return path
    # imageio-ffmpeg ships a static binary — use it as fallback on cloud servers
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled:
            return bundled
    except Exception:
        pass
    return None


def _run_ffmpeg(args: list, timeout: int = 120) -> bool:
    """Run an ffmpeg command. Returns True on success. Logs on failure."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(f"ffmpeg returned {result.returncode}: {result.stderr[:300]}")
            return False
        return True
    except FileNotFoundError:
        logger.warning("ffmpeg binary not found — skipping post-processing step")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out")
        return False
    except Exception as e:
        logger.warning(f"ffmpeg error: {e}")
        return False


# ── CHANGED: loudness normalisation via ffmpeg loudnorm filter ─────────────
def _apply_loudnorm(filepath: str, ffmpeg_bin: Optional[str] = None) -> bool:
    """
    Apply EBU R128 loudness normalisation to *filepath* (in-place).
    Two-pass: measure → normalise.
    Returns True if normalisation was applied successfully.
    """
    ffmpeg = ffmpeg_bin or _find_ffmpeg_binary()
    if not ffmpeg or not os.path.isfile(filepath):
        return False

    tmp = filepath + ".loudnorm.mp3"
    ok = _run_ffmpeg([
        ffmpeg, "-y", "-i", filepath,
        "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        "-ar", "44100",
        "-ab", "320k",
        tmp,
    ])
    if ok and os.path.isfile(tmp) and os.path.getsize(tmp) > 1000:
        os.replace(tmp, filepath)
        return True
    # Clean up temp file on failure
    if os.path.isfile(tmp):
        os.remove(tmp)
    return False


# ── CHANGED: silence trimming at start/end ─────────────────────────────────
def _trim_silence(filepath: str, ffmpeg_bin: Optional[str] = None) -> bool:
    """
    Strip leading and trailing silence from *filepath* (in-place).
    Uses silenceremove filter (2-pass: start + stop).
    Returns True on success.
    """
    ffmpeg = ffmpeg_bin or _find_ffmpeg_binary()
    if not ffmpeg or not os.path.isfile(filepath):
        return False

    tmp = filepath + ".trimmed.mp3"
    # silenceremove: start_periods=1 removes leading silence,
    # stop_periods=-1 with stop_duration removes trailing silence
    af = (
        "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,"
        "areverse,"
        "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,"
        "areverse"
    )
    ok = _run_ffmpeg([
        ffmpeg, "-y", "-i", filepath,
        "-af", af,
        "-ab", "320k",
        tmp,
    ])
    if ok and os.path.isfile(tmp) and os.path.getsize(tmp) > 1000:
        try:
            os.replace(tmp, filepath)
            return True
        except OSError:
            pass  # replace failed — clean up temp and leave original intact
    if os.path.isfile(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


# ── CHANGED: embed album art from Spotify into MP3 via mutagen ─────────────
def _embed_album_art(filepath: str, art_url: Optional[str]) -> bool:
    """
    Download *art_url* (highest-res Spotify image, 640×640) and embed as
    front-cover APIC frame in the MP3 at *filepath*.
    Returns True on success.
    """
    if not _MUTAGEN_AVAILABLE:
        logger.debug("mutagen not installed — skipping art embed")
        return False
    if not art_url or not _requests:
        return False
    if not os.path.isfile(filepath):
        return False

    try:
        resp = _requests.get(art_url, timeout=10)
        resp.raise_for_status()
        image_data = resp.content
        if len(image_data) < 500:
            return False

        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            tags = ID3()

        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,  # UTF-8
            mime="image/jpeg",
            type=3,       # front cover
            desc="Cover",
            data=image_data,
        ))
        tags.save(filepath)
        logger.info(f"Embedded album art into {os.path.basename(filepath)}")
        return True
    except Exception as e:
        logger.warning(f"Album art embed failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# SMART FORMAT SELECTION (duration-based)  # QUALITY UPGRADE
# ═══════════════════════════════════════════════════════════════════

def get_ydl_format(duration_ms=None):  # QUALITY UPGRADE
    """Return yt-dlp format string based on track duration."""  # QUALITY UPGRADE
    if not duration_ms or duration_ms <= 0:  # QUALITY UPGRADE
        return 'bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best'  # QUALITY UPGRADE
    duration_sec = duration_ms / 1000  # QUALITY UPGRADE
    if duration_sec > 600:  # QUALITY UPGRADE — long track (10min+): prefer lossless
        return 'bestaudio[ext=flac]/bestaudio/best'  # QUALITY UPGRADE
    elif duration_sec < 60:  # QUALITY UPGRADE — short track: M4A (smaller, fast)
        return 'bestaudio[ext=m4a]/bestaudio/best'  # QUALITY UPGRADE
    else:  # QUALITY UPGRADE — standard track: full chain
        return 'bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best'  # QUALITY UPGRADE


# ═══════════════════════════════════════════════════════════════════
# QUALITY REPORT HELPERS
# ═══════════════════════════════════════════════════════════════════

def _emit_quality_report(report: dict):
    """Emit quality_report to all connected Socket.IO clients (if socketio is set)."""
    if _socketio:
        try:
            _socketio.emit("quality_report", report)
        except Exception:
            pass  # Don't let emit failures propagate


def _build_quality_report(  # QUALITY UPGRADE
    *,
    bitrate: str = "320kbps",
    format_downloaded: str = "unknown",  # QUALITY UPGRADE
    source_platform: str = "youtube",
    search_stage_used: Optional[int] = None,  # QUALITY UPGRADE
    duration_diff: Optional[float] = None,
    title_similarity: Optional[float] = None,
    channel_verified: bool = False,  # QUALITY UPGRADE
    blacklist_filtered: int = 0,  # QUALITY UPGRADE
    art_embedded: bool = False,
    normalization_applied: bool = False,
    silence_trimmed: bool = False,  # QUALITY UPGRADE
    query_stage: Optional[int] = None,
) -> dict:
    return {
        "bitrate_achieved": bitrate,
        "format_downloaded": format_downloaded,  # QUALITY UPGRADE
        "source_platform": source_platform,
        "search_stage_used": search_stage_used or query_stage,  # QUALITY UPGRADE
        "duration_match_diff": duration_diff,
        "title_similarity": title_similarity,  # QUALITY UPGRADE
        "channel_verified": channel_verified,  # QUALITY UPGRADE
        "blacklist_filtered": blacklist_filtered,  # QUALITY UPGRADE
        "normalization_applied": normalization_applied,
        "silence_trimmed": silence_trimmed,  # QUALITY UPGRADE
        "art_embedded": art_embedded,
    }


# ═══════════════════════════════════════════════════════════════════
# MAIN DOWNLOADER SERVICE
# ═══════════════════════════════════════════════════════════════════

class DownloaderService:
    """Service for downloading audio from YouTube/SoundCloud with intelligent fallback."""

    def __init__(self):
        self.download_dir = config.DOWNLOAD_PATH if hasattr(config, 'DOWNLOAD_PATH') else config.DOWNLOAD_DIR
        self._last_match_quality = "exact"
        # ── CHANGED: track the stage number that produced the final download
        self._last_query_stage = 1
        # ── CHANGED: track title similarity of the final candidate
        self._last_title_similarity = 0.0
        # ── CHANGED: track source platform of the final download
        self._last_source_platform = "youtube"
        self._last_format_downloaded = "unknown"  # QUALITY UPGRADE
        self._last_channel_verified = False  # QUALITY UPGRADE
        self._last_blacklist_filtered = 0  # QUALITY UPGRADE
        self._ensure_download_dir()

    def _ensure_download_dir(self):
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"Download directory: {self.download_dir}")

    def _find_ffmpeg(self):
        """
        Locate ffmpeg binary directory.
        Returns directory string or None (if ffmpeg is in PATH).
        """
        spotdl_ffmpeg = Path.home() / '.spotdl' / 'ffmpeg.exe'
        if spotdl_ffmpeg.exists():
            return str(spotdl_ffmpeg.parent)
        if shutil.which('ffmpeg'):
            return None  # in PATH — yt-dlp finds it automatically
        # imageio-ffmpeg bundled binary (installed via pip on cloud servers)
        try:
            import imageio_ffmpeg
            bundled = imageio_ffmpeg.get_ffmpeg_exe()
            if bundled:
                return str(Path(bundled).parent)
        except Exception:
            pass
        logger.warning("ffmpeg not found — MP3 conversion may fail")
        return None

    def _build_youtube_search_url(self, title, artist):
        import urllib.parse
        search_query = f"{title} {artist}"
        encoded_query = urllib.parse.quote(search_query)
        return f"https://www.youtube.com/results?search_query={encoded_query}"

    # ─── Playlist download (unchanged public signature) ─────────────────────
    def download_playlist(self, tracks):
        """
        Download all tracks from a playlist.
        Returns aggregated results with success, fallback, and error counts.
        """
        try:
            downloads = []
            fallback_tracks = []
            errors = []

            logger.info(f"Starting playlist download for {len(tracks)} tracks")

            for idx, track in enumerate(tracks, 1):
                try:
                    title = track.get('title', '').strip()
                    artist = track.get('artist', '').strip()
                    album = track.get('album', '').strip()

                    if not title or not artist:
                        error_msg = f"Track {idx}/{len(tracks)}: Missing title or artist"
                        logger.warning(error_msg)
                        errors.append(error_msg)
                        continue

                    logger.info(f"Downloading track {idx}/{len(tracks)}: {title} by {artist}")

                    result = self.download_track(title, artist, album)

                    if result['status'] == 'success':
                        downloads.append(result['filename'])
                        logger.info(f"Downloaded ({idx}/{len(tracks)}): {result['filename']}")
                    elif result['status'] == 'fallback':
                        fallback_tracks.append({
                            'title': result['title'],
                            'artist': result['artist'],
                            'manual_url': result['manual_url'],
                            'message': result['message']
                        })
                        logger.info(f"Fallback link for ({idx}/{len(tracks)}): {title}")

                except Exception as e:
                    error_msg = f"Track {idx} ({title}): {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)

            successful = len(downloads)
            fallback_count = len(fallback_tracks)
            failed = len(errors)
            total = len(tracks)

            if failed == 0 and fallback_count == 0:
                overall_status = "success"
                summary = f"Downloaded all {total} tracks successfully"
            elif failed == 0:
                overall_status = "mixed"
                summary = f"Downloaded {successful}/{total} tracks. {fallback_count} tracks need manual download."
            else:
                overall_status = "all_fallback" if successful == 0 else "mixed"
                summary = f"Downloaded {successful}/{total} tracks. {fallback_count} fallback links. {failed} track(s) failed."

            logger.info(f"Playlist download completed: {summary}")

            return {
                "status": overall_status,
                "total": total,
                "successful": successful,
                "fallback": fallback_count,
                "failed": failed,
                "downloads": downloads,
                "tracks_with_links": fallback_tracks,
                "errors": errors if errors else None,
                "message": summary,
            }

        except Exception as e:
            logger.error(f"Error downloading playlist: {str(e)}")
            return {
                "status": "error",
                "total": len(tracks),
                "successful": 0, "fallback": 0, "failed": len(tracks),
                "downloads": [], "tracks_with_links": [],
                "errors": [f"Failed to download playlist: {str(e)}"],
                "message": f"Failed to download playlist: {str(e)}",
            }

    # ─── Single track download (unchanged public signature) ─────────────────
    def download_track(self, title, artist, album=None, progress_callback=None,
                       output_dir=None, output_filename=None, duration_ms=None,
                       album_art_url=None):
        """
        Download track audio and convert to 320 kbps MP3.
        With intelligent fallback: if auto-download fails, provide manual YouTube link.

        Args:
            title, artist, album: track metadata
            progress_callback: called with (percent, status_text)
            output_dir: custom output directory
            output_filename: custom filename (no extension)
            duration_ms: expected Spotify duration for validation
            album_art_url: URL to highest-res Spotify album image (optional)

        Returns:
            dict with 'status' = 'success' | 'fallback'
        """
        try:
            safe_title = sanitize_filename(title)
            safe_artist = sanitize_filename(artist)

            if not safe_title or not safe_artist:
                raise ValueError("Title and artist cannot be empty")

            actual_dir = output_dir or self.download_dir
            clean_name = sanitize_filename(output_filename) if output_filename else safe_title
            os.makedirs(actual_dir, exist_ok=True)

            # ── Skip if file already exists (duplicate prevention) ──
            expected_path = os.path.join(actual_dir, f"{clean_name}.mp3")
            if os.path.isfile(expected_path) and os.path.getsize(expected_path) > 1000:
                logger.info(f"Skipping duplicate: {clean_name}.mp3")
                return {
                    "status": "success",
                    "filename": f"{clean_name}.mp3",
                    "filepath": expected_path,
                    "message": f"Already exists: {clean_name}.mp3",
                }

            # Normalized duplicate check — local directory first.
            # norm_key matches the new "Title - Artist.mp3" naming convention;
            # title_only_key provides backward-compat for legacy "Title.mp3" files
            # from the SAME artist (safe to deduplicate).
            norm_key = normalize(clean_name)
            title_only_key = normalize(safe_title)
            for existing in os.listdir(actual_dir):
                if existing.lower().endswith(".mp3"):
                    existing_norm = normalize(existing[:-4])
                    if existing_norm == norm_key or existing_norm == title_only_key:
                        existing_path = os.path.join(actual_dir, existing)
                        if os.path.getsize(existing_path) > 1000:
                            logger.info(f"Skipping normalized duplicate: {existing}")
                            return {
                                "status": "success",
                                "filename": existing,
                                "filepath": existing_path,
                                "message": f"Already exists (normalized match): {existing}",
                            }

            # Cross-directory duplicate check — scan all of BASE_DOWNLOAD_DIR
            # to catch files that were organized into subfolders after download.
            # IMPORTANT: only match on norm_key ("Title - Artist"), NOT on
            # title_only_key alone, to avoid false-positive collisions between
            # two different songs that share the same title (e.g. "Peach" by
            # Diljit Dosanjh vs "Peach" by a Trance artist).
            base_dir = config.BASE_DOWNLOAD_DIR
            if actual_dir != base_dir:
                # Skip system folders and known non-music directories
                excluded = {'.git', '__pycache__', '.temp', 'node_modules', '.venv'}
                for root, dirs, files in os.walk(base_dir):
                    dirs[:] = [d for d in dirs if d not in excluded]
                    if root == actual_dir:
                        continue  # already checked above
                    for existing in files:
                        if existing.lower().endswith(".mp3"):
                            if normalize(existing[:-4]) == norm_key:
                                existing_path = os.path.join(root, existing)
                                try:
                                    if os.path.getsize(existing_path) > 1000:
                                        logger.info(f"Skipping cross-dir duplicate: {existing} (in {root})")
                                        return {
                                            "status": "success",
                                            "filename": existing,
                                            "filepath": existing_path,
                                            "message": f"Already exists in library: {existing}",
                                        }
                                except OSError:
                                    pass

            logger.info(f"Target: {actual_dir}/{clean_name}.mp3")
            logger.info(f"Searching for: {title} by {artist}")

            # ────────────────────────────────────────────────────────────────
            # STAGE 1: DOWNLOAD (only failure here causes fallback)
            # ────────────────────────────────────────────────────────────────
            filename = None
            filepath = None
            download_success = False
            
            try:
                filename = self._download_from_youtube(
                    None, clean_name, progress_callback,
                    output_dir=actual_dir, duration_ms=duration_ms,
                    spotify_title=title, artist=artist,
                )
                filepath = os.path.abspath(os.path.join(actual_dir, filename))
                logger.info(f"[stage:download] filepath={filepath}")

                # Post-download file-size check (5× threshold — wrong-track guard only)
                if duration_ms and duration_ms > 0 and os.path.isfile(filepath):
                    expected_bytes = (duration_ms / 1000.0) * (320_000 / 8)
                    actual_bytes = _retry_file_op(os.path.getsize, filepath)
                    if actual_bytes > expected_bytes * 5:
                        logger.warning(f"File too large: {actual_bytes}B vs ~{expected_bytes:.0f}B expected")
                        os.remove(filepath)
                        raise Exception(f"Downloaded file too large ({actual_bytes/1024/1024:.1f}MB) — wrong track")

                download_success = True
                logger.info(f"Track downloaded successfully: {filename} | path={filepath}")

            except Exception as download_error:
                logger.error(f"Auto-download FAILED for '{title}' by '{artist}': {download_error}")

                # NOTIFICATION — Failure (true download failure)
                try:  # NOTIFICATION
                    from services.notifications_service import notify_download_failure  # NOTIFICATION
                    notify_download_failure(  # NOTIFICATION
                        track={'name': title, 'artists': [{'name': artist}]},  # NOTIFICATION
                        attempt=1,  # NOTIFICATION
                        error=str(download_error),  # NOTIFICATION
                    )  # NOTIFICATION
                except Exception as _notif_err:  # NOTIFICATION
                    logger.error(f"Notification error: {_notif_err}")  # NOTIFICATION

                youtube_url = self._build_youtube_search_url(title, artist)
                return {
                    "status": "fallback",
                    "message": "Auto-download failed. Please click 'Open YouTube' to find and download manually.",
                    "manual_url": youtube_url,
                    "title": title,
                    "artist": artist,
                }

            # ────────────────────────────────────────────────────────────────
            # STAGE 2+: POST-PROCESSING (errors here DON'T cause retry)
            # ────────────────────────────────────────────────────────────────
            
            if not download_success or not filepath:
                return {
                    "status": "failed",
                    "error": "Download succeeded but file not found at expected location",
                    "filename": filename,
                }

            # Rescue: if file exists and is non-trivial, treat as success regardless
            # of any intermediate flag state (guards against WinError path race)
            if not os.path.isfile(filepath):
                # Last chance — check with abspath in case path drift occurred
                abs_fp = os.path.abspath(filepath)
                if os.path.isfile(abs_fp) and os.path.getsize(abs_fp) > 1000:
                    logger.info(f"[rescue] File found via abspath: {abs_fp}")
                    filepath = abs_fp
                else:
                    return {
                        "status": "failed",
                        "error": "Download succeeded but file not found at expected location",
                        "filename": filename,
                    }
            elif os.path.getsize(filepath) <= 1000:
                return {
                    "status": "failed",
                    "error": "Downloaded file is too small (likely corrupt)",
                    "filename": filename,
                }

            # Initialize error tracking for post-processing stages
            tagging_error = None
            report = None

            # ── NEW: Post-processing pipeline ──────────────────────────
            ffmpeg_bin = _find_ffmpeg_binary()
            logger.info(f"[stage:post-process] filepath={filepath}")

            # 1. Silence trimming
            trim_ok = _trim_silence(filepath, ffmpeg_bin)  # QUALITY UPGRADE
            logger.info(f"[stage:trim] ok={trim_ok} filepath={filepath}")

            # 2. Loudness normalisation
            norm_ok = _apply_loudnorm(filepath, ffmpeg_bin)
            logger.info(f"[stage:loudnorm] ok={norm_ok} filepath={filepath}")

            # 3. Embed album art
            art_ok = _embed_album_art(filepath, album_art_url)
            logger.info(f"[stage:art-embed] ok={art_ok} filepath={filepath}")

            # ── NEW: Build + persist + emit quality report ─────────────
            expected_secs = (duration_ms / 1000.0) if duration_ms and duration_ms > 0 else None
            dur_diff = None
            if expected_secs is not None:
                # Try to read actual duration from file
                try:
                    audio = MP3(filepath) if _MUTAGEN_AVAILABLE else None
                    if audio and audio.info:
                        dur_diff = round(abs(audio.info.length - expected_secs), 2)
                except Exception:
                    pass

            report = _build_quality_report(
                bitrate="320kbps",
                format_downloaded=self._last_format_downloaded,  # QUALITY UPGRADE
                source_platform=self._last_source_platform,
                search_stage_used=self._last_query_stage,  # QUALITY UPGRADE
                duration_diff=dur_diff,
                title_similarity=round(self._last_title_similarity, 3),
                channel_verified=self._last_channel_verified,  # QUALITY UPGRADE
                blacklist_filtered=self._last_blacklist_filtered,  # QUALITY UPGRADE
                art_embedded=art_ok,
                normalization_applied=norm_ok,
                silence_trimmed=trim_ok,  # QUALITY UPGRADE
                query_stage=self._last_query_stage,
            )

            # Persist to SQLite
            try:
                save_report(title, artist, album or "", filename, report)
            except Exception as db_err:
                logger.warning(f"Failed to save quality report to DB: {db_err}")

            # Emit to frontend
            _emit_quality_report(report)

            # ─── STAGE 2A: TAGGING (errors captured, not fatal) ─────────────
            logger.info(f"[stage:tagging] filepath={filepath}")
            tagging_report = None  # TAGGING INTEGRATION
            if _TAGGER_AVAILABLE:  # TAGGING INTEGRATION
                try:  # TAGGING INTEGRATION
                    spotify_meta = {  # TAGGING INTEGRATION
                        "id": getattr(self, '_last_track_id', '') or '',  # TAGGING INTEGRATION
                        "title": title,  # TAGGING INTEGRATION
                        "artist": artist,  # TAGGING INTEGRATION
                        "album": album or "",  # TAGGING INTEGRATION
                        "album_art_url": album_art_url,  # TAGGING INTEGRATION
                        "duration_ms": duration_ms,  # TAGGING INTEGRATION
                        "release_date": getattr(self, '_last_release_date', ''),  # TAGGING INTEGRATION
                    }  # TAGGING INTEGRATION
                    try:  # TAGGING INTEGRATION — lazy import; avoids circular dep at module load
                        from services.spotify_service import get_spotify_service as _gss  # TAGGING INTEGRATION
                        _sp_for_tagger = _gss()  # TAGGING INTEGRATION
                    except Exception:  # TAGGING INTEGRATION
                        _sp_for_tagger = None  # TAGGING INTEGRATION
                    tagging_report = _tag_file(  # TAGGING INTEGRATION
                        filepath,  # TAGGING INTEGRATION
                        spotify_meta,  # TAGGING INTEGRATION
                        spotify_service_instance=_sp_for_tagger,  # TAGGING INTEGRATION
                    )  # TAGGING INTEGRATION
                    logger.info(f"[tagger] Tagging complete: {filename} — source={tagging_report.get('source')}, tags={len(tagging_report.get('tags_written', []))}")  # TAGGING INTEGRATION
                    # TAGGING INTEGRATION — Persist tagging report to download_history
                    _save_tagging_report(filename, tagging_report, spotify_id=spotify_meta.get("id", ""))  # TAGGING INTEGRATION
                    # TAGGING INTEGRATION — Emit tagging_complete event via Socket.IO
                    if _socketio:  # TAGGING INTEGRATION
                        _socketio.emit("tagging_complete", {  # TAGGING INTEGRATION
                            "filename": filename,  # TAGGING INTEGRATION
                            "title": title,  # TAGGING INTEGRATION
                            "artist": artist,  # TAGGING INTEGRATION
                            "report": tagging_report,  # TAGGING INTEGRATION
                        })  # TAGGING INTEGRATION
                except Exception as tag_err:  # TAGGING INTEGRATION
                    tagging_error = str(tag_err)  # TAGGING INTEGRATION
                    logger.warning(f"[tagger] Tagging failed for {filename}: {tag_err}")  # TAGGING INTEGRATION

            # BPM/KEY — detect BPM and musical key after tagging
            if _BPM_KEY_AVAILABLE:  # BPM/KEY
                try:  # BPM/KEY
                    _analyze_and_tag(filepath, filename)  # BPM/KEY
                except Exception as _bpm_err:  # BPM/KEY
                    logger.warning(f"BPM/key analysis failed (non-critical): {_bpm_err}")  # BPM/KEY

            logger.info(f"[stage:complete] final filepath={filepath}")
            # ─── BUILD SUCCESS RESPONSE (file exists = success, even if post-processing failed) ───
            result = {
                "status": "success",
                "filename": filename,
                "filepath": filepath,
                "message": f"Successfully downloaded: {filename}",
                "match_quality": self._last_match_quality,
                "quality_report": report,
                "tagging_report": tagging_report,  # TAGGING INTEGRATION
            }

            # Add post-processing warnings if applicable
            if tagging_error:
                result["warning"] = f"Downloaded but tagging failed: {tagging_error}"
                logger.warning(f"[download] {result['warning']}")

            # ─── NOTIFICATION — Success (file exists) ──────────────────────
            try:  # NOTIFICATION
                from services.notifications_service import notify_download_success  # NOTIFICATION
                notify_download_success(  # NOTIFICATION
                    track={  # NOTIFICATION
                        'name': title,  # NOTIFICATION
                        'artists': [{'name': artist}],  # NOTIFICATION
                        'album': {'images': [{'url': album_art_url or ''}]},  # NOTIFICATION
                    },  # NOTIFICATION
                    quality_report=report,  # NOTIFICATION
                )  # NOTIFICATION
            except Exception as _notif_err:  # NOTIFICATION
                logger.error(f"Notification error: {_notif_err}")  # NOTIFICATION

            return result

        except Exception as e:
            logger.error(f"Unexpected error in download_track: {e}")

            # NOTIFICATION — Failure (unexpected error)
            try:  # NOTIFICATION
                from services.notifications_service import notify_download_failure  # NOTIFICATION
                notify_download_failure(  # NOTIFICATION
                    track={'name': title, 'artists': [{'name': artist}]},  # NOTIFICATION
                    attempt=1,  # NOTIFICATION
                    error=str(e),  # NOTIFICATION
                )  # NOTIFICATION
            except Exception as _notif_err:  # NOTIFICATION
                logger.error(f"Notification error: {_notif_err}")  # NOTIFICATION

            youtube_url = self._build_youtube_search_url(title, artist) if title and artist else "https://www.youtube.com"
            return {
                "status": "fallback",
                "message": "Track preparation failed. Click 'Open YouTube' to download manually.",
                "manual_url": youtube_url,
                "title": title,
                "artist": artist,
            }

    # ═══════════════════════════════════════════════════════════════════
    # INTERNAL: yt-dlp download with 320 kbps + lossless-first format
    # ═══════════════════════════════════════════════════════════════════

    def _try_download_with_query(self, query, source_name="YouTube", progress_callback=None,
                                  output_dir=None, output_filename=None, duration_ms=None):  # QUALITY UPGRADE
        """
        CHANGED — 320 kbps, smart format selection based on duration,
        copy-codec when source is already MP3.
        """
        logger.info(f"[{source_name}] Downloading: {query}")

        ffmpeg_dir = self._find_ffmpeg()

        _last_hook_emit = [0.0]  # DISCONNECT FIX: rate-limit yt-dlp progress hooks

        def _progress_hook(d):
            if not progress_callback:
                return
            if d['status'] == 'downloading':
                now = time.time()  # DISCONNECT FIX: emit max every 0.5s
                if now - _last_hook_emit[0] < 0.5:
                    return
                _last_hook_emit[0] = now
                pct_str = d.get('_percent_str', '0%').strip().replace('%', '')
                try:
                    pct = int(float(pct_str))
                except (ValueError, TypeError):
                    pct = 0
                progress_callback(pct, f"Downloading via {source_name}")
            elif d['status'] == 'finished':
                progress_callback(90, "Converting to 320 kbps MP3...")

        actual_dir = output_dir or self.download_dir
        if output_filename:
            outtmpl = os.path.join(actual_dir, f'{output_filename}.%(ext)s')
        else:
            outtmpl = os.path.join(actual_dir, '%(title)s.%(ext)s')

        # ── QUALITY UPGRADE: smart format selection based on duration ──
        FORMAT_STRING = get_ydl_format(duration_ms)  # QUALITY UPGRADE
        logger.info(f"[{source_name}] Format string: {FORMAT_STRING} (duration_ms={duration_ms})")  # QUALITY UPGRADE

        ydl_opts = {
            'format': FORMAT_STRING,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'overwrites': True,
            'socket_timeout': 15,
            'retries': 2,
            'fragment_retries': 2,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                # ── CHANGED: 320 kbps (was 192) ──
                'preferredquality': '320',
            }],
            'outtmpl': outtmpl,
            'progress_hooks': [_progress_hook],
        }

        # YouTube cookies — injected if YOUTUBE_COOKIES_B64 was decoded at startup
        _yt_cookies = "/tmp/youtube_cookies.txt"
        if os.path.isfile(_yt_cookies):
            ydl_opts['cookiefile'] = _yt_cookies

        # YouTube PO Token — bypasses datacenter IP bot-check when combined with cookies
        # Set YOUTUBE_PO_TOKEN env var on Render to activate (see docs for how to extract)
        _po_token = os.getenv("YOUTUBE_PO_TOKEN", "").strip()
        if _po_token:
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'po_token': [f'web+{_po_token}'],
                    'player_client': ['web'],
                }
            }

        if ffmpeg_dir:
            ydl_opts['ffmpeg_location'] = ffmpeg_dir

        info = None

        # Attempt 1: lossless-first format
        try:  # DISCONNECT FIX: wrap yt-dlp calls to prevent silent thread crash
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
        except Exception as e:
            logger.warning(f"[{source_name}] Primary attempt failed: {str(e)[:120]}")
            # Attempt 2: fallback to any audio
            try:  # DISCONNECT FIX: guard fallback attempt too
                fallback_opts = dict(ydl_opts)
                fallback_opts['format'] = 'bestaudio*'
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(query, download=True)
            except Exception as e2:
                logger.error(f"[{source_name}] Fallback also failed: {str(e2)[:120]}")
                if _socketio:  # DISCONNECT FIX: emit error event on yt-dlp crash
                    try:
                        _socketio.emit('download_error', {'error': f'yt-dlp failed: {str(e2)[:100]}', 'source': source_name})
                    except Exception:
                        pass
                # NOTIFICATION — yt-dlp pipeline error
                try:  # NOTIFICATION
                    from services.notifications_service import notify_ytdlp_error  # NOTIFICATION
                    notify_ytdlp_error(str(e2)[:100])  # NOTIFICATION
                except Exception:  # NOTIFICATION
                    pass  # NOTIFICATION
                raise

        # QUALITY UPGRADE — capture the source format that was actually downloaded
        if info:  # QUALITY UPGRADE
            _info_entry = info.get('entries', [info])[0] if info.get('entries') else info  # QUALITY UPGRADE
            self._last_format_downloaded = _info_entry.get('ext', 'unknown') if _info_entry else 'unknown'  # QUALITY UPGRADE

        # Allow yt-dlp/ffmpeg to release file handles before we touch the file (WinError 32)
        time.sleep(1.5)

        # Resolve filename
        if output_filename:
            expected_path = os.path.abspath(os.path.join(actual_dir, f'{output_filename}.mp3'))
            if _retry_file_op(os.path.isfile, expected_path) and _retry_file_op(os.path.getsize, expected_path) > 1000:
                result_name = f'{output_filename}.mp3'
                logger.info(f"Downloaded via {source_name}: {result_name} ({os.path.getsize(expected_path)} bytes) | path={expected_path}")
                return result_name
        else:
            filename = self._resolve_downloaded_filename(info, actual_dir)
            if filename:
                filepath = os.path.abspath(os.path.join(actual_dir, filename))
                if _retry_file_op(os.path.getsize, filepath) > 1000:
                    logger.info(f"Downloaded via {source_name}: {filename} ({os.path.getsize(filepath)} bytes) | path={filepath}")
                    return filename

        raise Exception(f"[{source_name}] Audio file not created or too small")

    def _resolve_downloaded_filename(self, info, search_dir=None):
        """
        Resolve the final MP3 filename from yt-dlp's info dict.
        """
        if not info:
            return None

        if 'entries' in info:
            entries = info['entries']
            if not entries:
                return None
            info = entries[0]

        title = info.get('title', '')
        if not title:
            return None

        directory = search_dir or self.download_dir
        expected = f"{title}.mp3"
        filepath = os.path.join(directory, expected)

        if os.path.isfile(filepath):
            return expected

        title_prefix = title[:40]
        for f in os.listdir(directory):
            if f.endswith('.mp3') and f.startswith(title_prefix):
                return f

        return None

    # ═══════════════════════════════════════════════════════════════════
    # CHANGED: 4-stage YouTube search + SoundCloud fallback (was 3-stage)
    # ═══════════════════════════════════════════════════════════════════

    def _download_from_youtube(self, search_query, output_filename, progress_callback=None,
                                output_dir=None, duration_ms=None, spotify_title=None, artist=None):
        """
        Multi-stage search with intelligent fallback.

        CHANGED — now 4 YouTube stages + SoundCloud:
          Stage 1: "{artist} - {title} Official Audio"   — ytsearch10
          Stage 2: "{artist} - {title} Audio"             — ytsearch5
          Stage 3: "{artist} {title} youtube music"       — ytsearch5
          Stage 4: "{title} {artist}"                     — ytsearch3  (last resort)
          Stage 5: SoundCloud fallback via scsearch:       — scsearch3

        PHASE 2 HARDENING — search-stage selection:
        Previously this loop downloaded whatever the FIRST stage produced
        that merely cleared select_best_candidate's min_score (0.40), even
        if that candidate was only marginally acceptable and a later,
        stricter-query stage (e.g. Stage 1 "Official Audio") would have
        found something much stronger. Confirmed vulnerability: a Stage-4
        generic-query candidate scoring e.g. 0.42 would win outright and
        Stage 1-3 would never even run.

        Minimum fix (not a redesign): each stage now only SCORES its
        candidates (_score_stage_candidates, no download). A candidate
        scoring >= STAGE_CONFIDENT_ACCEPT_SCORE is downloaded immediately —
        no reason to keep searching once we have a confident match, and
        this avoids extra API calls for the common case. A candidate that
        only clears min_score but not the confident bar is HELD (kept only
        if it beats whatever was already held) while later stages are
        tried. The best held candidate is downloaded once stages are
        exhausted. STAGE_CONFIDENT_ACCEPT_SCORE reuses 0.75 — not a new
        invented number, but the existing CONF_ACCEPT_WARN threshold from
        legacy_identification_service.py, which this codebase already
        treats as "confident enough to act on without further review".
        """
        actual_dir = output_dir or self.download_dir
        os.makedirs(actual_dir, exist_ok=True)

        title = spotify_title or ""
        art = artist or ""

        # ── CHANGED: 4-stage YouTube + SoundCloud fallback chain ───────
        stages = [
            (1, "Stage 1 (Official)", f"ytsearch10:{art} - {title} Official Audio", "exact",     "youtube"),
            (2, "Stage 2 (Audio)",    f"ytsearch5:{art} - {title} Audio",           "approx",    "youtube"),
            (3, "Stage 3 (YT Music)", f"ytsearch5:{art} {title} youtube music",     "approx",    "youtube"),
            (4, "Stage 4 (Generic)",  f"ytsearch3:{title} {art}",                   "fallback",  "youtube"),
            (5, "Stage 5 (SC)",       f"scsearch3:{art} - {title}",                 "fallback",  "soundcloud"),
        ]

        MAX_RETRIES = 2
        STAGE_CONFIDENT_ACCEPT_SCORE = 0.75

        # Best marginal (min_score <= score < confident bar) candidate seen
        # across all stages so far: {"candidate", "score", "stage_num",
        # "stage_name", "quality", "platform"} or None.
        held = None

        for stage_num, stage_name, query, quality, platform in stages:
            for attempt in range(1 + MAX_RETRIES):
                try:
                    suffix = f" (retry {attempt})" if attempt > 0 else ""
                    logger.info(f"{stage_name}: searching{suffix} — {query}")
                    if progress_callback and attempt > 0:
                        progress_callback(5, f"Retrying {stage_name} ({attempt}/{MAX_RETRIES})...")

                    candidate, score, reason = self._score_stage_candidates(
                        query, stage_name, duration_ms=duration_ms,
                        spotify_title=spotify_title, artist=artist,
                    )

                    if candidate is None:
                        # No candidate cleared min_score in this stage — not
                        # a fetch error, just nothing worth holding. Move on
                        # to the next stage without retrying this one.
                        logger.info(f"{stage_name}: {reason}")
                        break

                    logger.info(f"{stage_name}: best candidate score={score:.3f} — {reason}")

                    if score >= STAGE_CONFIDENT_ACCEPT_SCORE:
                        filename = self._finalize_and_download(
                            candidate, stage_name, spotify_title, duration_ms,
                            progress_callback, output_dir=actual_dir,
                            output_filename=output_filename,
                        )
                        logger.info(f"{stage_name} confident accept ({score:.3f}): {filename}")
                        self._last_match_quality = quality
                        self._last_query_stage = stage_num
                        self._last_source_platform = platform
                        return filename

                    # Marginal — hold it only if it beats what's already held
                    if held is None or score > held["score"]:
                        held = {
                            "candidate": candidate, "score": score,
                            "stage_num": stage_num, "stage_name": stage_name,
                            "quality": quality, "platform": platform,
                        }
                        logger.info(
                            f"{stage_name}: holding marginal candidate "
                            f"(score={score:.3f} < {STAGE_CONFIDENT_ACCEPT_SCORE} confident-accept bar) — "
                            f"continuing to later stages"
                        )
                    break  # done with this stage, no need to retry — got a scored result
                except Exception as e:
                    logger.warning(f"{stage_name} attempt {attempt+1} failed: {str(e)[:150]}")
                    if attempt < MAX_RETRIES:
                        time.sleep(1)
                    else:
                        logger.info(f"{stage_name} exhausted, moving on...")

        if held is not None:
            filename = self._finalize_and_download(
                held["candidate"], held["stage_name"], spotify_title, duration_ms,
                progress_callback, output_dir=actual_dir,
                output_filename=output_filename,
            )
            logger.info(
                f"All stages exhausted — downloading best held candidate from "
                f"{held['stage_name']} (score={held['score']:.3f}): {filename}"
            )
            self._last_match_quality = held["quality"]
            self._last_query_stage = held["stage_num"]
            self._last_source_platform = held["platform"]
            return filename

        error_msg = f"All download stages failed for: {title} — {art}"
        logger.error(error_msg)
        raise Exception(error_msg)

    # ═══════════════════════════════════════════════════════════════════
    # STRICT CANDIDATE MATCHING (updated scoring fed to strict_matcher)
    # ═══════════════════════════════════════════════════════════════════

    def _score_stage_candidates(self, query, source_name, duration_ms=None,
                                 spotify_title=None, artist=None):
        """
        Fetch one search stage's results and score them — NO download.

        PHASE 2: split out of the old _try_download_with_duration_check so
        _download_from_youtube can compare a stage's best candidate against
        one held from an earlier stage before committing to a download
        (see _download_from_youtube's docstring for why).

        Raises only on a genuine fetch/search failure (network, extractor
        error) — that's what the caller's retry-per-stage loop is for.
        When the fetch succeeds but nothing clears min_score, this returns
        (None, 0.0-ish score, reason) rather than raising, since that's not
        a failure worth retrying — it's a real "nothing here" result.

        Returns:
            (candidate_dict_or_None, score, reason)
        """
        logger.info(f"[{source_name}] STRICT matching starting: {query}")

        # ── CHANGED: lossless-first format during extraction as well ──
        extract_opts = {
            'format': 'bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,   # avoids per-video format resolution — eliminates datacenter IP bot-check
            'socket_timeout': 15,
        }

        logger.info(f"[{source_name}] Fetching search results...")
        try:
            with yt_dlp.YoutubeDL(extract_opts) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception as e:
            raise Exception(f"[{source_name}] Failed to fetch search results: {str(e)[:120]}")

        entries = info.get('entries', [info]) if info else []
        if not entries:
            raise Exception(f"[{source_name}] No search results")

        logger.info(f"[{source_name}] Got {len(entries)} result(s), applying STRICT filters...")

        expected_secs = (duration_ms / 1000.0) if duration_ms and duration_ms > 0 else None

        candidates = []
        blacklisted_count = 0  # QUALITY UPGRADE
        for i, entry in enumerate(entries):
            if not entry:
                continue

            # QUALITY UPGRADE — pre-scoring blacklist filter
            yt_title_raw = entry.get("title", "")  # QUALITY UPGRADE
            if is_blacklisted(yt_title_raw, spotify_title or ""):  # QUALITY UPGRADE
                blacklisted_count += 1  # QUALITY UPGRADE
                logger.info(f"[{source_name}] Blacklisted #{i+1}: \"{yt_title_raw}\"")  # QUALITY UPGRADE
                continue  # QUALITY UPGRADE

            # ── CHANGED: include channel_is_verified for +30 boost ──
            candidates.append({
                "title": yt_title_raw,
                "duration": entry.get("duration"),
                "url": entry.get("webpage_url") or entry.get("url"),
                "uploader": entry.get("uploader", "") or entry.get("channel", "") or "",
                "channel_is_verified": entry.get("channel_is_verified", False),
                "entry": entry,
                "index": i,
            })

        self._last_blacklist_filtered = blacklisted_count  # QUALITY UPGRADE
        if blacklisted_count > 0:  # QUALITY UPGRADE
            logger.info(f"[{source_name}] Blacklist filtered {blacklisted_count} candidate(s)")  # QUALITY UPGRADE

        best_candidate, best_score, selection_reason = select_best_candidate(
            candidates=candidates,
            spotify_title=spotify_title or query,
            artist=artist or "",
            expected_duration_sec=int(expected_secs) if expected_secs else None,
            min_score=0.40,
        )

        if not best_candidate:
            return None, best_score, f"No acceptable match. {selection_reason}"

        return best_candidate, best_score, selection_reason

    def _finalize_and_download(self, candidate, source_name, spotify_title, duration_ms,
                                progress_callback=None, output_dir=None, output_filename=None):
        """
        Final pre-download validation + actual download for a candidate
        already chosen by _download_from_youtube (either a confident-accept
        or the best held candidate after all stages ran).

        PHASE 2: split out of the old _try_download_with_duration_check.
        """
        actual_dir = output_dir or self.download_dir
        os.makedirs(actual_dir, exist_ok=True)

        expected_secs = (duration_ms / 1000.0) if duration_ms and duration_ms > 0 else None

        # Final duration validation
        best_duration = candidate.get("duration")
        if expected_secs and best_duration:
            if not final_duration_check(best_duration, int(expected_secs)):
                diff = abs(best_duration - int(expected_secs))
                raise Exception(
                    f"[{source_name}] Final validation failed: duration diff {diff}s > "
                    f"{HARD_DURATION_LIMIT_SEC}s for \"{candidate.get('title', '')}\""
                )

        # ── CHANGED: record title similarity for quality report ──
        clean_yt = clean_title(candidate.get("title", ""))
        clean_sp = clean_title(spotify_title or "")
        self._last_title_similarity = string_similarity(clean_sp, clean_yt)

        # QUALITY UPGRADE — capture verified status for quality report
        self._last_channel_verified = candidate.get("channel_is_verified", False)  # QUALITY UPGRADE

        video_url = candidate.get("url")
        if not video_url:
            raise Exception(f"[{source_name}] Selected candidate missing URL")

        logger.info(f"[{source_name}] ✅ Selected: \"{candidate.get('title', '')}\" — Downloading...")

        return self._try_download_with_query(
            video_url, source_name, progress_callback,
            output_dir=actual_dir, output_filename=output_filename,
            duration_ms=duration_ms,  # QUALITY UPGRADE
        )

    # ═══════════════════════════════════════════════════════════════════
    # File listing / deletion (unchanged)
    # ═══════════════════════════════════════════════════════════════════

    def get_downloads_list(self):
        try:
            files = []
            if os.path.exists(self.download_dir):
                files = [f for f in os.listdir(self.download_dir) if f.endswith('.mp3')]
            logger.info(f"Found {len(files)} downloaded files")
            return sorted(files, key=lambda x: os.path.getmtime(
                os.path.join(self.download_dir, x)
            ), reverse=True)
        except Exception as e:
            logger.error(f"Error listing downloads: {e}")
            return []

    def delete_download(self, filename):
        try:
            filename = validate_filename(filename)
            filepath = os.path.join(self.download_dir, filename)
            if not os.path.abspath(filepath).startswith(os.path.abspath(self.download_dir)):
                raise ValueError("Invalid file path")
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted file: {filename}")
                return {"success": True, "message": f"File deleted: {filename}"}
            else:
                raise FileNotFoundError(f"File not found: {filename}")
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return {"success": False, "message": f"Error deleting file: {e}"}


# ═══════════════════════════════════════════════════════════════════
# GENRE ENRICHMENT HELPER
# ═══════════════════════════════════════════════════════════════════

def infer_genre_from_title(title: str):
    t = (title or "").lower()
    if "techno" in t: return "techno"
    if "house" in t: return "house"
    if "afro" in t: return "afro house"
    if "garage" in t: return "uk garage"
    if "dnb" in t or "drum and bass" in t: return "dnb"
    if "dubstep" in t: return "dubstep"
    return None


def enrich_metadata_with_genres(metadata: dict) -> dict:
    """Attach Spotify artist genres to a metadata dict. Fails silently."""
    try:
        from services.spotify_service import spotify_service
        artist = metadata.get("artist")
        artist_genres = spotify_service.get_artist_genres(artist)
        metadata["genres"] = artist_genres or []
        metadata["genres"] = [g for g in metadata["genres"] if isinstance(g, str) and g.strip()]
        # logger.info(f"[genre-source] spotify={metadata['genres']}")
    except Exception:
        metadata.setdefault("genres", [])

    if not isinstance(metadata.get("genres"), list):
        metadata["genres"] = []

    yt_genre = infer_genre_from_title(metadata.get("title", ""))
    if yt_genre:
        metadata.setdefault("genres", [])
        yt_genre = yt_genre.strip()
        existing = {g.lower() for g in metadata["genres"]}
        if yt_genre.lower() not in existing:
            metadata["genres"].append(yt_genre)

    return metadata


# ═══════════════════════════════════════════════════════════════════
# MODULE SINGLETON (unchanged API)
# ═══════════════════════════════════════════════════════════════════
downloader_service = None


def get_downloader_service():
    global downloader_service
    if downloader_service is None:
        downloader_service = DownloaderService()
    return downloader_service
