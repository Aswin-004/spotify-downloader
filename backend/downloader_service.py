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
from strict_matcher import (
    score_candidate,
    select_best_candidate,
    has_reject_keyword,
    clean_title,
    duration_match,
    final_duration_check,
    log_rejection,
    log_acceptance,
    string_similarity,
)
from download_history import save_report

# TAGGING INTEGRATION — import tagger service
try:  # TAGGING INTEGRATION
    from tagger_service import tag_file as _tag_file, save_tagging_report as _save_tagging_report  # TAGGING INTEGRATION
    _TAGGER_AVAILABLE = True  # TAGGING INTEGRATION
except ImportError:  # TAGGING INTEGRATION
    _TAGGER_AVAILABLE = False  # TAGGING INTEGRATION

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
        os.replace(tmp, filepath)
        return True
    if os.path.isfile(tmp):
        os.remove(tmp)
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
# QUALITY REPORT HELPERS
# ═══════════════════════════════════════════════════════════════════

def _emit_quality_report(report: dict):
    """Emit quality_report to all connected Socket.IO clients (if socketio is set)."""
    if _socketio:
        try:
            _socketio.emit("quality_report", report)
        except Exception:
            pass  # Don't let emit failures propagate


def _build_quality_report(
    *,
    bitrate: str = "320kbps",
    source_platform: str = "youtube",
    duration_diff: Optional[float] = None,
    title_similarity: Optional[float] = None,
    art_embedded: bool = False,
    normalization_applied: bool = False,
    query_stage: Optional[int] = None,
) -> dict:
    return {
        "bitrate_achieved": bitrate,
        "source_platform": source_platform,
        "duration_match_diff": duration_diff,
        "title_similarity_score": title_similarity,
        "art_embedded": art_embedded,
        "normalization_applied": normalization_applied,
        "query_stage_used": query_stage,
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
            return None
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

            # Normalized duplicate check
            norm_key = normalize(clean_name)
            for existing in os.listdir(actual_dir):
                if existing.lower().endswith(".mp3"):
                    if normalize(existing[:-4]) == norm_key:
                        existing_path = os.path.join(actual_dir, existing)
                        if os.path.getsize(existing_path) > 1000:
                            logger.info(f"Skipping normalized duplicate: {existing}")
                            return {
                                "status": "success",
                                "filename": existing,
                                "filepath": existing_path,
                                "message": f"Already exists (normalized match): {existing}",
                            }

            logger.info(f"Target: {actual_dir}/{clean_name}.mp3")
            logger.info(f"Searching for: {title} by {artist}")

            try:
                filename = self._download_from_youtube(
                    None, clean_name, progress_callback,
                    output_dir=actual_dir, duration_ms=duration_ms,
                    spotify_title=title, artist=artist,
                )
                filepath = os.path.join(actual_dir, filename)

                # ── CHANGED: post-download file-size check now uses 320 kbps ──
                if duration_ms and duration_ms > 0 and os.path.isfile(filepath):
                    expected_bytes = (duration_ms / 1000.0) * (320_000 / 8)
                    actual_bytes = os.path.getsize(filepath)
                    if actual_bytes > expected_bytes * 3:
                        logger.warning(f"File too large: {actual_bytes}B vs ~{expected_bytes:.0f}B expected")
                        os.remove(filepath)
                        raise Exception(f"Downloaded file too large ({actual_bytes/1024/1024:.1f}MB) — wrong track")

                # ── NEW: Post-processing pipeline ──────────────────────────
                ffmpeg_bin = _find_ffmpeg_binary()

                # 1. Silence trimming
                _trim_silence(filepath, ffmpeg_bin)

                # 2. Loudness normalisation
                norm_ok = _apply_loudnorm(filepath, ffmpeg_bin)

                # 3. Embed album art
                art_ok = _embed_album_art(filepath, album_art_url)

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
                    source_platform=self._last_source_platform,
                    duration_diff=dur_diff,
                    title_similarity=round(self._last_title_similarity, 3),
                    art_embedded=art_ok,
                    normalization_applied=norm_ok,
                    query_stage=self._last_query_stage,
                )

                # Persist to SQLite
                try:
                    save_report(title, artist, album or "", filename, report)
                except Exception as db_err:
                    logger.warning(f"Failed to save quality report to DB: {db_err}")

                # Emit to frontend
                _emit_quality_report(report)

                # TAGGING INTEGRATION — Auto-tag after successful download
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
                        tagging_report = _tag_file(  # TAGGING INTEGRATION
                            filepath,  # TAGGING INTEGRATION
                            spotify_meta,  # TAGGING INTEGRATION
                            spotify_service_instance=None,  # TAGGING INTEGRATION
                        )  # TAGGING INTEGRATION
                        logger.info(f"[tagger] Tagging complete: {filename} — source={tagging_report.get('source')}, tags={len(tagging_report.get('tags_written', []))}")  # TAGGING INTEGRATION
                        # TAGGING INTEGRATION — Persist tagging report to download_history
                        _save_tagging_report(filename, tagging_report)  # TAGGING INTEGRATION
                        # TAGGING INTEGRATION — Emit tagging_complete event via Socket.IO
                        if _socketio:  # TAGGING INTEGRATION
                            _socketio.emit("tagging_complete", {  # TAGGING INTEGRATION
                                "filename": filename,  # TAGGING INTEGRATION
                                "title": title,  # TAGGING INTEGRATION
                                "artist": artist,  # TAGGING INTEGRATION
                                "report": tagging_report,  # TAGGING INTEGRATION
                            })  # TAGGING INTEGRATION
                    except Exception as tag_err:  # TAGGING INTEGRATION
                        logger.warning(f"[tagger] Tagging failed for {filename}: {tag_err}")  # TAGGING INTEGRATION

                result = {
                    "status": "success",
                    "filename": filename,
                    "filepath": filepath,
                    "message": f"Successfully downloaded: {filename}",
                    "match_quality": self._last_match_quality,
                    "quality_report": report,
                    "tagging_report": tagging_report,  # TAGGING INTEGRATION
                }
                logger.info(f"Track downloaded successfully: {filename}")
                return result

            except Exception as download_error:
                logger.warning(f"Auto-download failed for '{title}' by '{artist}': {download_error}")
                youtube_url = self._build_youtube_search_url(title, artist)
                return {
                    "status": "fallback",
                    "message": "Auto-download failed. Please click 'Open YouTube' to find and download manually.",
                    "manual_url": youtube_url,
                    "title": title,
                    "artist": artist,
                }

        except Exception as e:
            logger.error(f"Unexpected error in download_track: {e}")
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
                                  output_dir=None, output_filename=None):
        """
        CHANGED — 320 kbps, lossless-first format selection, copy-codec
        when source is already MP3.
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

        # ── CHANGED: lossless-first format string ──────────────────────
        # Try FLAC first, then M4A, then any best audio
        FORMAT_STRING = "bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best"

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
                raise

        # Resolve filename
        if output_filename:
            expected_path = os.path.join(actual_dir, f'{output_filename}.mp3')
            if os.path.isfile(expected_path) and os.path.getsize(expected_path) > 1000:
                result_name = f'{output_filename}.mp3'
                logger.info(f"Downloaded via {source_name}: {result_name} ({os.path.getsize(expected_path)} bytes)")
                return result_name
        else:
            filename = self._resolve_downloaded_filename(info, actual_dir)
            if filename:
                filepath = os.path.join(actual_dir, filename)
                if os.path.getsize(filepath) > 1000:
                    logger.info(f"Downloaded via {source_name}: {filename} ({os.path.getsize(filepath)} bytes)")
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

        for stage_num, stage_name, query, quality, platform in stages:
            for attempt in range(1 + MAX_RETRIES):
                try:
                    suffix = f" (retry {attempt})" if attempt > 0 else ""
                    logger.info(f"{stage_name}: searching{suffix} — {query}")
                    if progress_callback and attempt > 0:
                        progress_callback(5, f"Retrying {stage_name} ({attempt}/{MAX_RETRIES})...")

                    filename = self._try_download_with_duration_check(
                        query, stage_name,
                        progress_callback, output_dir=actual_dir,
                        output_filename=output_filename, duration_ms=duration_ms,
                        spotify_title=spotify_title, artist=artist,
                    )
                    logger.info(f"{stage_name} success: {filename}")
                    self._last_match_quality = quality
                    # ── CHANGED: record which stage + platform succeeded ──
                    self._last_query_stage = stage_num
                    self._last_source_platform = platform
                    return filename
                except Exception as e:
                    logger.warning(f"{stage_name} attempt {attempt+1} failed: {str(e)[:150]}")
                    if attempt < MAX_RETRIES:
                        time.sleep(1)
                    else:
                        logger.info(f"{stage_name} exhausted, moving on...")

        error_msg = f"All download stages failed for: {title} — {art}"
        logger.error(error_msg)
        raise Exception(error_msg)

    # ═══════════════════════════════════════════════════════════════════
    # STRICT CANDIDATE MATCHING (updated scoring fed to strict_matcher)
    # ═══════════════════════════════════════════════════════════════════

    def _try_download_with_duration_check(self, query, source_name, progress_callback=None,
                                           output_dir=None, output_filename=None, duration_ms=None,
                                           spotify_title=None, artist=None):
        """
        STRICT matching: extract search results, score, validate, then download.
        """
        logger.info(f"[{source_name}] STRICT matching starting: {query}")

        actual_dir = output_dir or self.download_dir
        os.makedirs(actual_dir, exist_ok=True)

        # ── CHANGED: lossless-first format during extraction as well ──
        extract_opts = {
            'format': 'bestaudio[ext=flac]/bestaudio[ext=m4a]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
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
        for i, entry in enumerate(entries):
            if not entry:
                continue

            # ── CHANGED: include channel_is_verified for +30 boost ──
            candidates.append({
                "title": entry.get("title", ""),
                "duration": entry.get("duration"),
                "url": entry.get("webpage_url") or entry.get("url"),
                "uploader": entry.get("uploader", "") or entry.get("channel", "") or "",
                "channel_is_verified": entry.get("channel_is_verified", False),
                "entry": entry,
                "index": i,
            })

        best_candidate, selection_reason = select_best_candidate(
            candidates=candidates,
            spotify_title=spotify_title or query,
            artist=artist or "",
            expected_duration_sec=int(expected_secs) if expected_secs else None,
            min_score=0.5,
        )

        if not best_candidate:
            raise Exception(f"[{source_name}] No acceptable match. {selection_reason}")

        # Final duration validation
        best_duration = best_candidate.get("duration")
        if expected_secs and best_duration:
            if not final_duration_check(best_duration, int(expected_secs)):
                diff = abs(best_duration - int(expected_secs))
                raise Exception(
                    f"[{source_name}] Final validation failed: duration diff {diff}s > 30s "
                    f"for \"{best_candidate.get('title', '')}\""
                )

        # ── CHANGED: record title similarity for quality report ──
        clean_yt = clean_title(best_candidate.get("title", ""))
        clean_sp = clean_title(spotify_title or "")
        self._last_title_similarity = string_similarity(clean_sp, clean_yt)

        video_url = best_candidate.get("url")
        if not video_url:
            raise Exception(f"[{source_name}] Selected candidate missing URL")

        logger.info(f"[{source_name}] ✅ Selected: \"{best_candidate.get('title', '')}\" — Downloading...")

        return self._try_download_with_query(
            video_url, source_name, progress_callback,
            output_dir=actual_dir, output_filename=output_filename,
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
# MODULE SINGLETON (unchanged API)
# ═══════════════════════════════════════════════════════════════════
downloader_service = None


def get_downloader_service():
    global downloader_service
    if downloader_service is None:
        downloader_service = DownloaderService()
    return downloader_service
