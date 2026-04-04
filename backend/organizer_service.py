# Compatibility shim — module moved to services/organizer_service.py
from services.organizer_service import organize_file, organize_library, organize_recent

__all__ = ["organize_file", "organize_library", "organize_recent"]
