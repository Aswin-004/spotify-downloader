"""
Audio Download Service
Handles downloading audio files using yt-dlp
Direct, simple, and reliable YouTube download pipeline
"""
import os
import re
import logging
import yt_dlp
from pathlib import Path
from config import config
from utils import build_youtube_search_query, validate_filename, setup_logging

logger = setup_logging(__name__)


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


class DownloaderService:
    """Service for downloading audio from YouTube with intelligent fallback"""
    
    def __init__(self):
        """Initialize downloader service"""
        # Use custom DOWNLOAD_PATH from config, fallback to DOWNLOAD_DIR
        self.download_dir = config.DOWNLOAD_PATH if hasattr(config, 'DOWNLOAD_PATH') else config.DOWNLOAD_DIR
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
            print(f"[playlist] Starting download of {len(tracks)} tracks")
            
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
                        print(f"[playlist] ✓ {msg}")
                    
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
                        print(f"[playlist] ⚠ {msg}")
                
                except Exception as e:
                    error_msg = f"Track {idx} ({title}): {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    print(f"[playlist] ✗ {error_msg}")
            
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
            print(f"[playlist] Result: {summary}")
            
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
    
    def download_track(self, title, artist, album=None, progress_callback=None, output_dir=None, output_filename=None):
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
            logger.info(f"Target: {actual_dir}/{clean_name}.mp3")
            
            # Build search query for YouTube
            search_query = build_youtube_search_query(title, artist, album)
            logger.info(f"Searching YouTube for: {search_query}")
            
            # Attempt download
            try:
                filename = self._download_from_youtube(search_query, clean_name, progress_callback, output_dir=actual_dir)
                filepath = os.path.join(actual_dir, filename)
                
                # SUCCESS - Auto download worked
                result = {
                    "status": "success",
                    "filename": filename,
                    "filepath": filepath,
                    "message": f"Successfully downloaded: {filename}"
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
        print(f"[downloader] Using source: {source_name} | Query: {query}")

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
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'overwrites': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['hls', 'dash'],
                },
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            },
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
            print(f"[downloader] [{source_name}] Primary attempt failed, retrying with fallback format...")

            # Attempt 2: fallback format for restricted/age-gated videos
            fallback_opts = dict(ydl_opts)
            fallback_opts['format'] = 'worstvideo+worstaudio/worst'
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                info = ydl.extract_info(query, download=True)

        # Resolve filename
        if output_filename:
            expected_path = os.path.join(actual_dir, f'{output_filename}.mp3')
            if os.path.isfile(expected_path) and os.path.getsize(expected_path) > 1000:
                result_name = f'{output_filename}.mp3'
                msg = f"Downloaded via {source_name}: {result_name} ({os.path.getsize(expected_path)} bytes)"
                print(f"[downloader] ✓ {msg}")
                logger.info(msg)
                return result_name
        else:
            filename = self._resolve_downloaded_filename(info)
            if filename:
                filepath = os.path.join(actual_dir, filename)
                if os.path.getsize(filepath) > 1000:
                    msg = f"Downloaded via {source_name}: {filename} ({os.path.getsize(filepath)} bytes)"
                    print(f"[downloader] ✓ {msg}")
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
    
    def _download_from_youtube(self, search_query, output_filename, progress_callback=None, output_dir=None):
        """
        Download audio using multi-source fallback.
        Tries: YouTube → SoundCloud
        
        Args:
            search_query (str): What to search for
            output_filename (str): Clean filename without extension
            progress_callback (callable, optional): Called with (percent, status_text)
            output_dir (str, optional): Target directory
        
        Returns:
            str: Downloaded filename
        
        Raises:
            Exception: If all sources fail
        """
        actual_dir = output_dir or self.download_dir
        os.makedirs(actual_dir, exist_ok=True)
        logger.info(f"Starting multi-source download: {search_query}")
        print(f"[downloader] Starting multi-source download for: {search_query}")

        sources = [
            (f"ytsearch1:{search_query}", "YouTube"),
            (f"scsearch1:{search_query}", "SoundCloud"),
        ]

        last_error = None
        for source_url, source_name in sources:
            try:
                print(f"[downloader] Trying source: {source_name}")
                filename = self._try_download_with_query(source_url, source_name, progress_callback, output_dir=actual_dir, output_filename=output_filename)
                logger.info(f"Successfully downloaded via {source_name}: {filename}")
                return filename
            except Exception as e:
                last_error = e
                logger.warning(f"{source_name} failed: {str(e)[:120]}")
                print(f"[downloader] {source_name} failed, trying next...")
                continue

        error_msg = f"All sources failed. Last error: {str(last_error)}"
        logger.error(error_msg)
        print(f"[downloader] ERROR: {error_msg}")
        raise Exception(error_msg)
    
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
