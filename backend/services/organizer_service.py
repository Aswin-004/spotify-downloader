"""
Post-Download File Organizer Service
====================================
Automatically organizes downloaded MP3 files into structured folders based on ID3 tags.
Supports three organization modes: artist, genre, artist_genre.

Features:
  • Reads ID3 tags (artist, genre) from MP3 files
  • Maps genre keywords to predefined categories
  • Cleans folder names (removes unsafe characters)
  • Handles file collisions (appends _1, _2, etc.)
  • Updates MongoDB download_history with organized metadata
  • Supports batch organizing of existing library
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, ID3NoHeaderError
    _MUTAGEN_AVAILABLE = True
except ImportError:
    _MUTAGEN_AVAILABLE = False

from loguru import logger

from config import config
from database import _get_db

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

BASE_DOWNLOAD_DIR = Path(config.BASE_DOWNLOAD_DIR)

# Genre keyword mapping (partial match, case-insensitive) → category
GENRE_MAPPING = {
    r"bollywood|hindi|filmi": "Bollywood",
    r"hip hop|hip-hop|rap": "Hip Hop",
    r"pop": "Pop",
    r"dance|electronic|edm|house|techno": "Electronic",
    r"rock|metal|indie": "Rock",
    r"r&b|soul": "R&B",
    r"jazz": "Jazz",
    r"classical": "Classical",
    r"lo-fi|lofi": "Lo-Fi",
}

# Unsafe characters to strip from folder names (Windows + Unix conventions)
UNSAFE_CHARS = r'<>:"/\|?*'


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def clean_folder_name(name: str) -> str:
    """
    Strip unsafe characters from folder name.
    
    Args:
        name: Original folder name
        
    Returns:
        Cleaned folder name (unsafe chars removed, stripped)
    """
    if not name:
        return "Unknown"
    
    # Remove unsafe characters
    for char in UNSAFE_CHARS:
        name = name.replace(char, "")
    
    # Strip whitespace and underscore padding
    name = name.strip().strip("_")
    
    # Collapse multiple spaces
    while "  " in name:
        name = name.replace("  ", " ")
    
    return name or "Unknown"


def map_genre_to_category(genre_tag: str) -> str:
    """
    Map genre ID3 tag value to predefined category.
    
    Args:
        genre_tag: Raw genre string from ID3 tag (e.g., "Hip Hop", "Electronic")
        
    Returns:
        Mapped category or "Other" if no match
    """
    if not genre_tag:
        return "Other"
    
    genre_lower = genre_tag.lower()
    
    import re
    for pattern, category in GENRE_MAPPING.items():
        if re.search(pattern, genre_lower):
            return category
    
    return "Other"


def read_id3_tags(filepath: str) -> Dict[str, str]:
    """
    Read artist and genre from MP3 ID3 tags.
    
    Args:
        filepath: Path to MP3 file
        
    Returns:
        Dict with keys: artist, genre (both strings)
    """
    result = {"artist": "Unknown", "genre": "Unknown"}
    
    if not _MUTAGEN_AVAILABLE:
        logger.warning(f"[organizer] Mutagen not available, cannot read tags from {filepath}")
        return result
    
    try:
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            logger.debug(f"[organizer] No ID3 tags found in {filepath}, creating...")
            tags = ID3()
        
        # Read artist (TPE1 frame)
        if "TPE1" in tags:
            artist = str(tags["TPE1"].text[0]) if tags["TPE1"].text else "Unknown"
            result["artist"] = clean_folder_name(artist)
        
        # Read genre (TCON frame)
        if "TCON" in tags:
            genre = str(tags["TCON"].text[0]) if tags["TCON"].text else "Unknown"
            result["genre"] = map_genre_to_category(genre)
        
        logger.debug(f"[organizer] Read tags from {filepath}: artist={result['artist']}, genre={result['genre']}")
        
    except Exception as e:
        logger.warning(f"[organizer] Failed to read ID3 tags from {filepath}: {e}")
    
    return result


def resolve_destination_path(filename: str, folder_structure: str) -> Tuple[str, str, str]:
    """
    Resolve destination folder and new filepath, handling collisions.
    
    Args:
        filename: Just the filename with .mp3 extension
        folder_structure: Subfolder path relative to BASE_DOWNLOAD_DIR (e.g., "Hip Hop/Kendrick Lamar")
        
    Returns:
        Tuple of (dest_folder_path, new_filepath, relative_path)
    """
    dest_folder = BASE_DOWNLOAD_DIR / folder_structure
    dest_folder.mkdir(parents=True, exist_ok=True)
    
    dest_filepath = dest_folder / filename
    
    # Handle collision: append _1, _2, etc.
    if dest_filepath.exists():
        base_name = filename[:-4]  # Remove .mp3
        counter = 1
        while (dest_folder / f"{base_name}_{counter}.mp3").exists():
            counter += 1
        new_filename = f"{base_name}_{counter}.mp3"
        dest_filepath = dest_folder / new_filename
    else:
        new_filename = filename
    
    # Relative path for MongoDB storage
    relative_path = str(dest_filepath.relative_to(BASE_DOWNLOAD_DIR))
    
    return str(dest_folder), str(dest_filepath), relative_path


def organize_file(filename: str, mode: str = "artist", file_dir: Optional[str] = None, spotify_genre: str = "") -> Dict:
    """
    Organize a single MP3 file into structured folder based on ID3 tags.

    Args:
        filename: Just the filename (e.g., "Song.mp3")
        mode: "artist" | "genre" | "artist_genre"
        file_dir: Directory where the file lives. If None, defaults to BASE_DOWNLOAD_DIR root.
                  Pass this when the file was downloaded to a subfolder (e.g. auto_downloader).
        spotify_genre: Fallback genre string when the ID3 TCON tag is empty or unknown.
                       Useful when Spotify metadata is available at the call site.

    Returns:
        Dict with keys:
          - moved: bool (True if file was moved)
          - old_path: original filepath
          - new_path: new filepath after move
          - folder: folder structure used
          - artist: artist from ID3
          - genre: genre category
          - error: error message if failed (optional)
    """
    result = {
        "moved": False,
        "old_path": None,
        "new_path": None,
        "folder": None,
        "artist": None,
        "genre": None,
    }

    try:
        # Validate mode
        if mode not in ["artist", "genre", "artist_genre"]:
            raise ValueError(f"Invalid mode: {mode}. Expected 'artist', 'genre', or 'artist_genre'")

        # Find original file — check file_dir first, then BASE_DOWNLOAD_DIR root
        if file_dir:
            old_path = Path(file_dir) / filename
            if not old_path.exists():
                # Fallback: maybe file is already at root
                old_path = BASE_DOWNLOAD_DIR / filename
        else:
            old_path = BASE_DOWNLOAD_DIR / filename

        if not old_path.exists():
            raise FileNotFoundError(f"File not found: {old_path}")
        
        if not filename.lower().endswith(".mp3"):
            raise ValueError(f"Not an MP3 file: {filename}")
        
        # Read ID3 tags; fall back to spotify_genre when TCON is empty/unknown
        tags = read_id3_tags(str(old_path))
        artist = tags["artist"]
        genre = tags["genre"]
        if genre in ("Unknown", "Other") and spotify_genre:
            mapped = map_genre_to_category(spotify_genre)
            if mapped != "Other":
                genre = mapped
                logger.debug(f"[organizer] Used spotify_genre fallback for {filename}: '{spotify_genre}' → '{genre}'")
        
        # Determine folder structure based on mode
        if mode == "artist":
            folder_structure = artist
        elif mode == "genre":
            folder_structure = genre
        elif mode == "artist_genre":
            folder_structure = f"{genre}/{artist}"
        else:
            raise ValueError(f"Unexpected mode: {mode}")
        
        # Clean folder structure
        folder_structure_clean = "/".join(clean_folder_name(p) for p in folder_structure.split("/"))
        
        # Resolve destination with collision handling
        dest_folder, new_filepath, relative_path = resolve_destination_path(filename, folder_structure_clean)
        
        # Move file
        shutil.move(str(old_path), new_filepath)
        logger.info(f"[organizer] Moved: {old_path} → {new_filepath}")
        
        result["moved"] = True
        result["old_path"] = str(old_path)
        result["new_path"] = new_filepath
        result["folder"] = folder_structure_clean
        result["artist"] = artist
        result["genre"] = genre
        
        # Update MongoDB record
        try:
            db = _get_db()
            col = db.download_history
            db_result = col.update_one(
                {"filename": filename},
                {"$set": {
                    "relative_path": relative_path,
                    "folder": folder_structure_clean,
                    "organized": True,
                    "organize_mode": mode,
                }}
            )
            if db_result.matched_count == 0:
                logger.warning(f"[organizer] No MongoDB record found for '{filename}' — file moved but record not updated")
            else:
                logger.debug(f"[organizer] Updated MongoDB for {filename}")
        except Exception as db_err:
            logger.warning(f"[organizer] Failed to update MongoDB for {filename}: {db_err}")
        
    except Exception as e:
        logger.error(f"[organizer] Failed to organize {filename}: {e}")
        result["error"] = str(e)
    
    return result


def organize_recent(mode: str = "artist", hours: int = 24) -> Dict:
    """
    Organize MP3 files in BASE_DOWNLOAD_DIR root that were modified within the last N hours.

    Args:
        mode: "artist" | "genre" | "artist_genre"
        hours: Look-back window in hours (default 24)

    Returns:
        Dict with keys:
          - moved: count of successfully moved files
          - skipped: count of files not moved
          - errors: list of dicts with {'file': filename, 'error': error_message}
          - scanned: total number of files examined
    """
    import time

    result = {"moved": 0, "skipped": 0, "errors": [], "scanned": 0}

    try:
        if mode not in ["artist", "genre", "artist_genre"]:
            raise ValueError(f"Invalid mode: {mode}. Expected 'artist', 'genre', or 'artist_genre'")

        cutoff = time.time() - (hours * 3600)
        mp3_files = [
            f.name for f in BASE_DOWNLOAD_DIR.iterdir()
            if f.is_file() and f.name.lower().endswith(".mp3") and f.stat().st_mtime >= cutoff
        ]

        result["scanned"] = len(mp3_files)

        if not mp3_files:
            logger.info(f"[organizer] No MP3 files modified in last {hours}h found in root")
            return result

        logger.info(f"[organizer] organize_recent: {len(mp3_files)} file(s) modified in last {hours}h, mode='{mode}'")

        for filename in mp3_files:
            org_result = organize_file(filename, mode=mode)
            if org_result.get("error"):
                result["errors"].append({"file": filename, "error": org_result["error"]})
                logger.warning(f"[organizer] Error organizing {filename}: {org_result['error']}")
            elif org_result.get("moved"):
                result["moved"] += 1
                logger.info(f"[organizer] organize_recent moved: {filename} → {org_result.get('folder')}")
            else:
                result["skipped"] += 1

        logger.info(f"[organizer] organize_recent summary: moved={result['moved']}, skipped={result['skipped']}, errors={len(result['errors'])}")

    except Exception as e:
        logger.error(f"[organizer] organize_recent failed: {e}")
        result["errors"].append({"file": "batch_operation", "error": str(e)})

    return result


def organize_library(mode: str = "artist") -> Dict:
    """
    Organize all MP3 files in BASE_DOWNLOAD_DIR root into structured folders.
    
    Args:
        mode: "artist" | "genre" | "artist_genre"
        
    Returns:
        Dict with keys:
          - moved: count of successfully moved files
          - skipped: count of already-organized files
          - errors: list of dicts with {'file': filename, 'error': error_message}
    """
    result = {
        "moved": 0,
        "skipped": 0,
        "errors": [],
    }
    
    try:
        # Validate mode
        if mode not in ["artist", "genre", "artist_genre"]:
            raise ValueError(f"Invalid mode: {mode}. Expected 'artist', 'genre', or 'artist_genre'")
        
        # Find all .mp3 files in BASE_DOWNLOAD_DIR root (not recursively)
        mp3_files = [
            f.name for f in BASE_DOWNLOAD_DIR.iterdir()
            if f.is_file() and f.name.lower().endswith(".mp3")
        ]
        
        if not mp3_files:
            logger.info("[organizer] No MP3 files found in root directory to organize")
            return result
        
        logger.info(f"[organizer] Organizing {len(mp3_files)} files in mode '{mode}'")
        
        for filename in mp3_files:
            organize_result = organize_file(filename, mode=mode)
            
            if organize_result.get("error"):
                if "already organized" not in organize_result["error"].lower():
                    result["errors"].append({
                        "file": filename,
                        "error": organize_result["error"]
                    })
                    logger.warning(f"[organizer] Error organizing {filename}: {organize_result['error']}")
                else:
                    result["skipped"] += 1
            elif organize_result.get("moved"):
                result["moved"] += 1
                logger.info(f"[organizer] Successfully organized: {filename}")
            else:
                result["skipped"] += 1
        
        logger.info(f"[organizer] Summary: moved={result['moved']}, skipped={result['skipped']}, errors={len(result['errors'])}")
        
    except Exception as e:
        logger.error(f"[organizer] organize_library failed: {e}")
        result["errors"].append({
            "file": "batch_operation",
            "error": str(e)
        })
    
    return result
