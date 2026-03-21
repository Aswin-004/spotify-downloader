"""
Audio Download Service
Handles downloading audio files using yt-dlp
Direct, simple, and reliable YouTube download pipeline
With duration validation and multi-stage fallback
"""
import os
import re
import time
import logging
import threading
from difflib import SequenceMatcher
import yt_dlp
from pathlib import Path
from config import config
from utils import build_youtube_search_query, build_youtube_fallback_query, validate_filename, setup_logging

logger = setup_logging(__name__)

# Global download queue status (shared with app.py)
download_queue_status = {
    "total": 0,
    "completed": 0,
    "current": None,
    "pending": [],
    "active_workers": 0,
}
_queue_lock = threading.Lock()

# Manual download priority flag — auto workers yield when manual is active
# When set, it means manual download is NOT active (auto can proceed)
_manual_idle = threading.Event()
_manual_idle.set()  # starts idle (auto can proceed)


def set_manual_active(active):
    """Signal that a manual download is active (auto workers should yield)."""
    if active:
        _manual_idle.clear()  # block auto workers
    else:
        _manual_idle.set()  # unblock auto workers


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


def clean_filename(name):
    """
    Clean filename by removing invalid Windows characters
    This is the comprehensive filename safety function
    
    Args:
        name (str): Filename or name to clean
    
    Returns:
        str: Clean filename safe for Windows filesystem
    """
    if not isinstance(name, str):
        name = str(name)
    
    # Remove invalid Windows characters: / \ * ? " : < > |
    name = re.sub(r'[\/*?:"<>|]', '', name)
    # Remove commas
    name = name.replace(',', '')
    # Remove leading/trailing whitespace and trailing dots
    name = name.strip().rstrip('.')
    # Collapse multiple spaces to single space
    name = re.sub(r'\s+', ' ', name)
    
    return name


# Alias for backward compatibility
def sanitize_filename(name):
    """Alias for clean_filename for backward compatibility"""
    return clean_filename(name)


def normalize(text):
    """Normalize a string for consistent duplicate comparison."""
    return " ".join(text.lower().split()).strip()


class DownloaderService:
    """Service for downloading audio from YouTube with intelligent fallback"""
    
    def __init__(self):
        """Initialize downloader service"""
        # Use custom DOWNLOAD_PATH from config, fallback to DOWNLOAD_DIR
        self.download_dir = config.DOWNLOAD_PATH if hasattr(config, 'DOWNLOAD_PATH') else config.DOWNLOAD_DIR
        self._last_match_quality = "exact"  # Track match quality: exact, approx, fallback
        self._ensure_download_dir()
    
    def _ensure_download_dir(self):
        """Ensure download directory exists"""
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"Download directory: {self.download_dir}")
    
    def _find_ffmpeg(self):
        """
        Locate ffmpeg binary directory.
        Checks: spotdl bundle → system PATH → None
        
        Returns:
            str or None: Directory containing ffmpeg, or None if in PATH
        """
        import shutil
        # Check spotdl's bundled ffmpeg
        spotdl_ffmpeg = Path.home() / '.spotdl' / 'ffmpeg.exe'
        if spotdl_ffmpeg.exists():
            logger.info(f"Using ffmpeg from spotdl: {spotdl_ffmpeg.parent}")
            return str(spotdl_ffmpeg.parent)
        # Check system PATH
        if shutil.which('ffmpeg'):
            return None  # yt-dlp will find it automatically
        logger.warning("ffmpeg not found - MP3 conversion may fail")
        return None
    
    def _build_youtube_search_url(self, title, artist):
        """
        Build a YouTube search URL as fallback when auto-download fails
        
        Args:
            title (str): Track title
            artist (str): Artist name
        
        Returns:
            str: YouTube search URL
        """
        # URL encode the search query
        import urllib.parse
        search_query = f"{title} {artist}"
        encoded_query = urllib.parse.quote(search_query)
        return f"https://www.youtube.com/results?search_query={encoded_query}"
    
    def download_playlist(self, tracks):
        """
        Download all tracks from a playlist
        Returns aggregated results with success, fallback, and error counts
        
        Args:
            tracks (list): List of track dicts with title, artist, album
        
        Returns:
            dict: Download result containing:
                - status (str): Overall status - "success", "mixed", or "all_fallback"
                - total (int): Total number of tracks
                - successful (int): Number of successful auto-downloads
                - fallback (int): Number of fallback links provided
                - failed (int): Number of completely failed tracks
                - downloads (list): List of successfully downloaded filenames
                - tracks_with_links (list): List of tracks with fallback YouTube links
                - errors (list): List of completely failed tracks
                - message (str): Summary message
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
                    
                    # Download individual track (now returns status-based response)
                    result = self.download_track(title, artist, album)
                    
                    # Handle new response format
                    if result['status'] == 'success':
                        # Auto-download succeeded
                        downloads.append(result['filename'])
                        msg = f"Downloaded ({idx}/{len(tracks)}): {result['filename']}"
                        logger.info(msg)
                    
                    elif result['status'] == 'fallback':
                        # Auto-download failed, but fallback link provided
                        fallback_tracks.append({
                            'title': result['title'],
                            'artist': result['artist'],
                            'manual_url': result['manual_url'],
                            'message': result['message']
                        })
                        msg = f"Fallback link for ({idx}/{len(tracks)}): {title}"
                        logger.info(msg)
                
                except Exception as e:
                    error_msg = f"Track {idx} ({title}): {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
            
            successful = len(downloads)
            fallback_count = len(fallback_tracks)
            failed = len(errors)
            total = len(tracks)
            
            # Determine overall status
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
                "message": summary
            }
        
        except Exception as e:
            logger.error(f"Error downloading playlist: {str(e)}")
            error_msg = f"Failed to download playlist: {str(e)}"
            return {
                "status": "error",
                "total": len(tracks),
                "successful": 0,
                "fallback": 0,
                "failed": len(tracks),
                "downloads": [],
                "tracks_with_links": [],
                "errors": [error_msg],
                "message": error_msg
            }
    
    def download_track(self, title, artist, album=None, progress_callback=None, output_dir=None, output_filename=None, duration_ms=None):
        """
        Download track audio from YouTube and convert to MP3
        With intelligent fallback: if auto-download fails, provide manual YouTube link
        
        Args:
            title (str): Track title
            artist (str): Artist name
            album (str, optional): Album name
            progress_callback (callable, optional): Called with (percent, status_text)
            output_dir (str, optional): Custom output directory (e.g. album folder)
            output_filename (str, optional): Custom filename without extension (e.g. "01 - Track")
            duration_ms (int, optional): Expected track duration in ms for validation
        
        Returns:
            dict: Download result with status 'success' or 'fallback'
        """
        try:
            safe_title = sanitize_filename(title)
            safe_artist = sanitize_filename(artist)
            
            if not safe_title or not safe_artist:
                raise ValueError("Title and artist cannot be empty")
            
            # Determine output directory and filename
            actual_dir = output_dir or self.download_dir
            if output_filename:
                clean_name = sanitize_filename(output_filename)
            else:
                clean_name = safe_title  # Clean title only, no YouTube naming
            
            os.makedirs(actual_dir, exist_ok=True)
            
            # Skip if file already exists (duplicate prevention)
            expected_path = os.path.join(actual_dir, f"{clean_name}.mp3")
            if os.path.isfile(expected_path) and os.path.getsize(expected_path) > 1000:
                logger.info(f"Skipping duplicate: {clean_name}.mp3")
                return {
                    "status": "success",
                    "filename": f"{clean_name}.mp3",
                    "filepath": expected_path,
                    "message": f"Already exists: {clean_name}.mp3"
                }

            # Normalized duplicate check across the target directory
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
                                "message": f"Already exists (normalized match): {existing}"
                            }
            
            logger.info(f"Target: {actual_dir}/{clean_name}.mp3")
            
            # Build search query for YouTube
            search_query = build_youtube_search_query(title, artist, album)
            logger.info(f"Searching YouTube for: {search_query}")
            
            # Attempt download
            try:
                filename = self._download_from_youtube(search_query, clean_name, progress_callback, output_dir=actual_dir, duration_ms=duration_ms, spotify_title=title, artist=artist)
                filepath = os.path.join(actual_dir, filename)
                
                # Post-download file size sanity check
                if duration_ms and duration_ms > 0 and os.path.isfile(filepath):
                    expected_bytes = (duration_ms / 1000.0) * (192000 / 8)  # 192kbps MP3
                    actual_bytes = os.path.getsize(filepath)
                    if actual_bytes > expected_bytes * 3:
                        logger.warning(f"File too large: {actual_bytes} bytes vs ~{expected_bytes:.0f} expected")
                        os.remove(filepath)
                        raise Exception(f"Downloaded file too large ({actual_bytes/1024/1024:.1f}MB vs ~{expected_bytes/1024/1024:.1f}MB expected) — wrong track")

                # SUCCESS - Auto download worked
                result = {
                    "status": "success",
                    "filename": filename,
                    "filepath": filepath,
                    "message": f"Successfully downloaded: {filename}",
                    "match_quality": self._last_match_quality,
                }
                logger.info(f"Track downloaded successfully: {filename}")
                return result
            
            except Exception as download_error:
                # FALLBACK - Auto download failed, provide manual link
                error_msg = str(download_error)
                logger.warning(f"Auto-download failed for '{title}' by '{artist}': {error_msg}")
                logger.info(f"Providing fallback YouTube search link")
                
                # Build YouTube search URL for manual download
                youtube_url = self._build_youtube_search_url(title, artist)
                
                result = {
                    "status": "fallback",
                    "message": f"Auto-download failed. Please click 'Open YouTube' to find and download manually.",
                    "manual_url": youtube_url,
                    "title": title,
                    "artist": artist
                }
                logger.info(f"Fallback response provided: {youtube_url}")
                return result
        
        except Exception as e:
            # Unexpected error - still provide fallback
            logger.error(f"Unexpected error in download_track: {str(e)}")
            youtube_url = self._build_youtube_search_url(title, artist) if title and artist else "https://www.youtube.com"
            
            return {
                "status": "fallback",
                "message": f"Track preparation failed. Click 'Open YouTube' to download manually.",
                "manual_url": youtube_url,
                "title": title,
                "artist": artist
            }
    
    def _try_download_with_query(self, query, source_name="YouTube", progress_callback=None, output_dir=None, output_filename=None):
        """
        Download audio using yt-dlp with stable extraction options.
        Uses android+web player clients to bypass JS runtime requirement.
        Converts to MP3 via FFmpeg postprocessor.
        
        Args:
            query (str): Full search query (e.g. "ytsearch1:...")
            source_name (str): Name for logging
            progress_callback (callable, optional): Called with (percent, status_text)
        
        Returns:
            str: Downloaded filename
        
        Raises:
            Exception: If download fails
        """
        logger.info(f"[{source_name}] Downloading: {query}")

        # Locate ffmpeg (shipped with spotdl or in PATH)
        ffmpeg_dir = self._find_ffmpeg()

        def _progress_hook(d):
            if not progress_callback:
                return
            if d['status'] == 'downloading':
                pct_str = d.get('_percent_str', '0%').strip().replace('%', '')
                try:
                    pct = int(float(pct_str))
                except (ValueError, TypeError):
                    pct = 0
                progress_callback(pct, f"Downloading via {source_name}")
            elif d['status'] == 'finished':
                progress_callback(90, "Converting to MP3...")

        actual_dir = output_dir or self.download_dir
        if output_filename:
            outtmpl = os.path.join(actual_dir, f'{output_filename}.%(ext)s')
        else:
            outtmpl = os.path.join(actual_dir, '%(title)s.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
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
                'preferredquality': '192',
            }],
            'outtmpl': outtmpl,
            'progress_hooks': [_progress_hook],
        }

        if ffmpeg_dir:
            ydl_opts['ffmpeg_location'] = ffmpeg_dir

        info = None

        # Attempt 1: bestaudio with preferred opts
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
        except Exception as e:
            logger.warning(f"[{source_name}] Primary attempt failed: {str(e)[:120]}")

            # Attempt 2: broader format — any audio stream, even from combined formats
            fallback_opts = dict(ydl_opts)
            fallback_opts['format'] = 'bestaudio*'
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                info = ydl.extract_info(query, download=True)

        # Resolve filename
        if output_filename:
            expected_path = os.path.join(actual_dir, f'{output_filename}.mp3')
            if os.path.isfile(expected_path) and os.path.getsize(expected_path) > 1000:
                result_name = f'{output_filename}.mp3'
                msg = f"Downloaded via {source_name}: {result_name} ({os.path.getsize(expected_path)} bytes)"
                logger.info(msg)
                return result_name
        else:
            filename = self._resolve_downloaded_filename(info)
            if filename:
                filepath = os.path.join(actual_dir, filename)
                if os.path.getsize(filepath) > 1000:
                    msg = f"Downloaded via {source_name}: {filename} ({os.path.getsize(filepath)} bytes)"
                    logger.info(msg)
                    return filename

        raise Exception(f"[{source_name}] Audio file not created or too small")

    def _resolve_downloaded_filename(self, info):
        """
        Resolve the final MP3 filename from yt-dlp's info dict.
        Handles search results (entries list) and direct downloads.
        
        Returns:
            str or None: Filename if found on disk, else None
        """
        if not info:
            return None

        # Search results wrap info in an 'entries' list
        if 'entries' in info:
            entries = info['entries']
            if not entries:
                return None
            info = entries[0]

        # Build expected filename: same title as yt-dlp used, but .mp3 extension
        title = info.get('title', '')
        if not title:
            return None

        expected = f"{title}.mp3"
        filepath = os.path.join(self.download_dir, expected)

        if os.path.isfile(filepath):
            return expected

        # Fallback: scan directory for any file containing the title prefix
        title_prefix = title[:40]
        for f in os.listdir(self.download_dir):
            if f.endswith('.mp3') and f.startswith(title_prefix):
                return f

        return None
    
    def _download_from_youtube(self, search_query, output_filename, progress_callback=None, output_dir=None, duration_ms=None, spotify_title=None, artist=None):
        """
        Download audio using multi-stage fallback with duration validation.
        Stage 1: YouTube filtered (ytsearch5 + duration check + title filter)
        Stage 2: YouTube unfiltered fallback
        Stage 3: SoundCloud
        
        Args:
            search_query (str): Filtered search query
            output_filename (str): Clean filename without extension
            progress_callback (callable, optional): Called with (percent, status_text)
            output_dir (str, optional): Target directory
            duration_ms (int, optional): Expected duration in ms for validation
            spotify_title (str, optional): Original Spotify track title for content filtering
        
        Returns:
            str: Downloaded filename
        
        Raises:
            Exception: If all sources fail
        """
        actual_dir = output_dir or self.download_dir
        os.makedirs(actual_dir, exist_ok=True)
        logger.info(f"Starting multi-stage download: {search_query}")

        MAX_RETRIES = 2  # retries per stage

        # Stage 1: YouTube with filtered query + duration validation + title filter (ytsearch10)
        for attempt in range(1 + MAX_RETRIES):
            try:
                suffix = f" (retry {attempt})" if attempt > 0 else ""
                logger.info(f"Stage 1: YouTube filtered (ytsearch10){suffix}")
                if progress_callback and attempt > 0:
                    progress_callback(5, f"Retrying stage 1 ({attempt}/{MAX_RETRIES})...")
                filename = self._try_download_with_duration_check(
                    f"ytsearch10:{search_query}", "YouTube-filtered",
                    progress_callback, output_dir=actual_dir,
                    output_filename=output_filename, duration_ms=duration_ms,
                    spotify_title=spotify_title, artist=artist
                )
                logger.info(f"Stage 1 success: {filename}")
                self._last_match_quality = "exact"
                return filename
            except Exception as e:
                logger.warning(f"Stage 1 attempt {attempt+1} failed: {str(e)[:120]}")
                if attempt < MAX_RETRIES:
                    logger.info(f"[downloader] Stage 1 retry {attempt+1}/{MAX_RETRIES}...")
                    time.sleep(1)
                else:
                    logger.info("[downloader] Stage 1 exhausted, trying stage 2...")

        # Stage 2: YouTube with unfiltered query + duration validation
        parts = search_query.split(" official audio")[0] if " official audio" in search_query else search_query
        unfiltered_query = parts + " audio"
        for attempt in range(1 + MAX_RETRIES):
            try:
                suffix = f" (retry {attempt})" if attempt > 0 else ""
                logger.info(f"Stage 2: YouTube unfiltered{suffix}")
                if progress_callback and attempt > 0:
                    progress_callback(5, f"Retrying stage 2 ({attempt}/{MAX_RETRIES})...")
                filename = self._try_download_with_duration_check(
                    f"ytsearch3:{unfiltered_query}", "YouTube-unfiltered",
                    progress_callback, output_dir=actual_dir,
                    output_filename=output_filename, duration_ms=duration_ms,
                    spotify_title=spotify_title, artist=artist
                )
                logger.info(f"Stage 2 success: {filename}")
                self._last_match_quality = "approx"
                return filename
            except Exception as e:
                logger.warning(f"Stage 2 attempt {attempt+1} failed: {str(e)[:120]}")
                if attempt < MAX_RETRIES:
                    logger.info(f"[downloader] Stage 2 retry {attempt+1}/{MAX_RETRIES}...")
                    time.sleep(1)
                else:
                    logger.info("[downloader] Stage 2 exhausted, trying SoundCloud...")

        # Stage 3: SoundCloud fallback + duration validation
        for attempt in range(1 + MAX_RETRIES):
            try:
                suffix = f" (retry {attempt})" if attempt > 0 else ""
                logger.info(f"Stage 3: SoundCloud{suffix}")
                if progress_callback and attempt > 0:
                    progress_callback(5, f"Retrying stage 3 ({attempt}/{MAX_RETRIES})...")
                filename = self._try_download_with_duration_check(
                    f"scsearch3:{search_query}", "SoundCloud",
                    progress_callback, output_dir=actual_dir,
                    output_filename=output_filename, duration_ms=duration_ms,
                    spotify_title=spotify_title, artist=artist
                )
                logger.info(f"Stage 3 success: {filename}")
                self._last_match_quality = "fallback"
                return filename
            except Exception as e:
                logger.warning(f"Stage 3 attempt {attempt+1} failed: {str(e)[:120]}")
                if attempt < MAX_RETRIES:
                    logger.info(f"[downloader] Stage 3 retry {attempt+1}/{MAX_RETRIES}...")
                    time.sleep(1)

        error_msg = f"All download stages failed for: {search_query}"
        logger.error(error_msg)
        raise Exception(error_msg)

    @staticmethod
    def _title_has_unwanted_tag(yt_title, spotify_title):
        """Check if YouTube title contains unwanted variants not in the Spotify title.
        Returns the tag name if found, None otherwise."""
        unwanted = ['remix', 'live', 'cover', 'karaoke', 'instrumental']
        yt_lower = yt_title.lower()
        sp_lower = spotify_title.lower()
        for tag in unwanted:
            if tag in yt_lower and tag not in sp_lower:
                return tag
        return None

    @staticmethod
    def _string_similarity(a, b):
        """Compute normalized similarity between two strings (0.0–1.0)."""
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    @staticmethod
    def _duration_score(actual_secs, expected_secs, tolerance=15):
        """Score duration match: 1.0 for exact, decaying to 0.0 at tolerance boundary."""
        if not actual_secs or not expected_secs:
            return 0.5  # unknown duration, neutral score
        delta = abs(actual_secs - expected_secs)
        if delta <= tolerance:
            return 1.0 - (delta / tolerance)
        return 0.0

    def _score_candidate(self, entry, spotify_title, artist, expected_secs):
        """Score a YouTube candidate using fuzzy similarity.
        Returns (score, has_unwanted_tag) where score is 0.0–1.0."""
        yt_title = entry.get('title', '')
        vid_duration = entry.get('duration')

        title_sim = self._string_similarity(spotify_title, yt_title) if spotify_title else 0.5
        artist_sim = self._string_similarity(artist, yt_title) if artist else 0.5
        dur_score = self._duration_score(vid_duration, expected_secs)

        # Weighted score: title 50%, artist 30%, duration 20%
        score = title_sim * 0.5 + artist_sim * 0.3 + dur_score * 0.2

        # Bonuses for audio-specific results
        yt_lower = yt_title.lower()
        if 'official audio' in yt_lower:
            score += 0.08
        elif 'audio' in yt_lower:
            score += 0.03
        # Penalty for video-type results
        if 'official video' in yt_lower or 'music video' in yt_lower:
            score -= 0.05
        if 'lyric' in yt_lower:
            score -= 0.02

        has_unwanted = self._title_has_unwanted_tag(yt_title, spotify_title) if spotify_title else None

        return max(0.0, min(1.0, score)), has_unwanted

    def _try_download_with_duration_check(self, query, source_name, progress_callback=None,
                                           output_dir=None, output_filename=None, duration_ms=None,
                                           spotify_title=None, artist=None):
        """
        Search YouTube, score candidates with fuzzy similarity matching,
        then download the best match. Uses ±15s duration tolerance.
        Falls back to tagged (remix/live) results if no clean match exists.
        """
        logger.info(f"[{source_name}] Extracting info: {query}")
        ffmpeg_dir = self._find_ffmpeg()
        actual_dir = output_dir or self.download_dir

        extract_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 15,
        }

        with yt_dlp.YoutubeDL(extract_opts) as ydl:
            info = ydl.extract_info(query, download=False)

        entries = info.get('entries', [info]) if info else []
        if not entries:
            raise Exception(f"[{source_name}] No search results")

        expected_secs = (duration_ms / 1000.0) if duration_ms and duration_ms > 0 else None

        # Score all candidates
        clean_candidates = []   # no unwanted tags
        tagged_candidates = []  # has remix/live/cover but still a match
        for i, entry in enumerate(entries):
            if not entry:
                continue
            yt_title = entry.get('title', '')
            vid_duration = entry.get('duration')

            score, unwanted_tag = self._score_candidate(entry, spotify_title, artist, expected_secs)

            # Hard reject: duration way too far off (>3x or <0.3x)
            if expected_secs and vid_duration:
                if vid_duration > expected_secs * 3 or vid_duration < expected_secs * 0.3:
                    logger.info(f"[{source_name}] #{i+1} REJECT '{yt_title}' — duration {vid_duration}s wildly off ({expected_secs:.0f}s expected)")
                    continue

            # Duration within ±15s is strongly preferred
            dur_ok = True
            if expected_secs and vid_duration:
                dur_ok = abs(vid_duration - expected_secs) <= 15

            delta_str = f"Δ{abs(vid_duration - expected_secs):.1f}s" if expected_secs and vid_duration else "no-dur"
            log_line = f"[{source_name}] #{i+1} '{yt_title}' — {delta_str}, score={score:.3f}"

            if unwanted_tag:
                tagged_candidates.append((score, entry, i, unwanted_tag))
                logger.info(f"{log_line} (tagged: {unwanted_tag})")
            elif dur_ok:
                clean_candidates.append((score, entry, i))
                logger.info(f"{log_line} CANDIDATE")
            else:
                # Duration outside ±15s but within 0.3x-3x — keep as low-priority
                clean_candidates.append((score * 0.6, entry, i))
                logger.info(f"{log_line} RELAXED")

        # Pick best clean candidate
        best_entry = None
        if clean_candidates:
            clean_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_entry, best_idx = clean_candidates[0]
            logger.info(f"[{source_name}] SELECTED #{best_idx+1} '{best_entry.get('title', '')}' (score={best_score:.3f})")

        # Fallback: allow tagged results (remix/live) if no clean match
        if not best_entry and tagged_candidates:
            tagged_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_entry, best_idx, tag = tagged_candidates[0]
            logger.info(f"[{source_name}] TAGGED FALLBACK #{best_idx+1} '{best_entry.get('title', '')}' (score={best_score:.3f}, tag={tag})")

        if not best_entry:
            if expected_secs:
                raise Exception(f"[{source_name}] No results within acceptable duration range ({expected_secs:.0f}s expected)")
            # No duration info — just use the first result
            best_entry = entries[0] if entries[0] else None

        if not best_entry:
            raise Exception(f"[{source_name}] No valid candidates found")

        # Download the chosen entry by URL
        video_url = best_entry.get('webpage_url') or best_entry.get('url')
        if not video_url:
            raise Exception(f"[{source_name}] No URL for selected entry")

        logger.info(f"[{source_name}] Downloading: {best_entry.get('title', 'Unknown')} ({video_url})")
        return self._try_download_with_query(
            video_url, source_name, progress_callback,
            output_dir=actual_dir, output_filename=output_filename
        )
    
    def get_downloads_list(self):
        """
        Get list of downloaded files
        
        Returns:
            list: List of downloaded filenames
        """
        try:
            files = []
            if os.path.exists(self.download_dir):
                files = [f for f in os.listdir(self.download_dir) 
                        if f.endswith('.mp3')]
            
            logger.info(f"Found {len(files)} downloaded files")
            return sorted(files, key=lambda x: os.path.getmtime(
                os.path.join(self.download_dir, x)
            ), reverse=True)
        
        except Exception as e:
            logger.error(f"Error listing downloads: {str(e)}")
            return []
    
    def delete_download(self, filename):
        """
        Delete a downloaded file
        
        Args:
            filename (str): Filename to delete
        
        Returns:
            dict: Result of deletion
        """
        try:
            # Validate filename
            filename = validate_filename(filename)
            filepath = os.path.join(self.download_dir, filename)
            
            # Ensure file is in download directory (prevent path traversal)
            if not os.path.abspath(filepath).startswith(
                os.path.abspath(self.download_dir)
            ):
                raise ValueError("Invalid file path")
            
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted file: {filename}")
                return {
                    "success": True,
                    "message": f"File deleted: {filename}"
                }
            else:
                raise FileNotFoundError(f"File not found: {filename}")
        
        except Exception as e:
            logger.error(f"Error deleting file: {str(e)}")
            return {
                "success": False,
                "message": f"Error deleting file: {str(e)}"
            }


# Create global instance
downloader_service = None


def get_downloader_service():
    """Get or create downloader service instance"""
    global downloader_service
    if downloader_service is None:
        downloader_service = DownloaderService()
    return downloader_service
