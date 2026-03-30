"""
Download History — MongoDB-backed quality report storage.  # MUSICBRAINZ
Uses database.py for all MongoDB operations.  # MUSICBRAINZ

Stores a quality_report dict per download for analytics  # MUSICBRAINZ
and frontend reporting via Socket.IO.  # MUSICBRAINZ

Function signatures are identical to the original SQLite version  # MUSICBRAINZ
so no callers need to change.  # MUSICBRAINZ
"""
# MUSICBRAINZ — rewritten for MongoDB

from database import save_download_report, get_recent_reports  # MUSICBRAINZ


def save_report(  # MUSICBRAINZ
    track_title: str,  # MUSICBRAINZ
    artist: str,  # MUSICBRAINZ
    album: str,  # MUSICBRAINZ
    filename: str,  # MUSICBRAINZ
    report: dict,  # MUSICBRAINZ
) -> str:  # MUSICBRAINZ
    """
    Persist a quality_report dict to MongoDB.  # MUSICBRAINZ

    Returns:  # MUSICBRAINZ
        Inserted document _id as string.  # MUSICBRAINZ
    """  # MUSICBRAINZ
    return save_download_report(track_title, artist, album, filename, report)  # MUSICBRAINZ


def get_recent(limit: int = 50) -> list:  # MUSICBRAINZ
    """Return the most recent *limit* reports as dicts."""  # MUSICBRAINZ
    return get_recent_reports(limit)  # MUSICBRAINZ
