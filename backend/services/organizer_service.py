"""
Folder name utilities — clean_folder_name and resolve_destination_path.
Used by genre_router and cleanup scripts.
"""

from pathlib import Path
from typing import Tuple

from loguru import logger

from config import config

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

BASE_DOWNLOAD_DIR = Path(config.BASE_DOWNLOAD_DIR)

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

    # Handle collision: skip if identical file already exists at destination
    if dest_filepath.exists():
        logger.info(f"[organizer] File already exists at destination, skipping: {dest_filepath}")
        relative_path = str(dest_filepath.relative_to(BASE_DOWNLOAD_DIR))
        return str(dest_folder), str(dest_filepath), relative_path

    # Relative path for MongoDB storage
    relative_path = str(dest_filepath.relative_to(BASE_DOWNLOAD_DIR))

    return str(dest_folder), str(dest_filepath), relative_path
