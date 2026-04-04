"""  # ANALYTICS
Analytics Service — MongoDB aggregation queries for the Analytics Dashboard.  # ANALYTICS
Reads from download_history, musicbrainz_cache, and tagging_failures collections.  # ANALYTICS
"""  # ANALYTICS

from database import (  # ANALYTICS
    get_download_history_collection,  # ANALYTICS
    get_musicbrainz_cache_collection,  # ANALYTICS
    get_tagging_failures_collection,  # ANALYTICS
)  # ANALYTICS
from datetime import datetime, timedelta, timezone  # ANALYTICS


def _downloads():  # ANALYTICS
    """Shortcut to download_history collection."""  # ANALYTICS
    return get_download_history_collection()  # ANALYTICS


def _failures():  # ANALYTICS
    """Shortcut to tagging_failures collection."""  # ANALYTICS
    return get_tagging_failures_collection()  # ANALYTICS


def _mb_cache():  # ANALYTICS
    """Shortcut to musicbrainz_cache collection."""  # ANALYTICS
    return get_musicbrainz_cache_collection()  # ANALYTICS


def get_overview_stats():  # ANALYTICS
    """Return high-level dashboard statistics."""  # ANALYTICS
    downloads = _downloads()  # ANALYTICS
    total = downloads.count_documents({})  # ANALYTICS
    tagged = downloads.count_documents({"tagging_report": {"$exists": True}})  # ANALYTICS
    failed = _failures().count_documents({})  # ANALYTICS

    # Unique artists  # ANALYTICS
    artists = len(downloads.distinct("artist"))  # ANALYTICS

    # Success rate (tagged / total)  # ANALYTICS
    rate = round((tagged / total * 100), 1) if total > 0 else 0  # ANALYTICS

    return {  # ANALYTICS
        "total_downloads": total,  # ANALYTICS
        "success_rate": rate,  # ANALYTICS
        "total_storage_mb": 0,  # ANALYTICS — file_size_mb not tracked; placeholder
        "total_artists": artists,  # ANALYTICS
        "failed_downloads": failed,  # ANALYTICS
        "musicbrainz_cached": _mb_cache().count_documents({}),  # ANALYTICS
    }  # ANALYTICS


def get_downloads_per_day(days=30):  # ANALYTICS
    """Return download counts grouped by date for the last N days."""  # ANALYTICS
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)  # ANALYTICS
    pipeline = [  # ANALYTICS
        {"$match": {"downloaded_at": {"$gte": cutoff}}},  # ANALYTICS
        {"$group": {  # ANALYTICS
            "_id": {  # ANALYTICS
                "$dateToString": {  # ANALYTICS
                    "format": "%Y-%m-%d",  # ANALYTICS
                    "date": "$downloaded_at",  # ANALYTICS
                }  # ANALYTICS
            },  # ANALYTICS
            "count": {"$sum": 1},  # ANALYTICS
        }},  # ANALYTICS
        {"$sort": {"_id": 1}},  # ANALYTICS
    ]  # ANALYTICS
    results = list(_downloads().aggregate(pipeline))  # ANALYTICS
    return [{"date": r["_id"], "count": r["count"]} for r in results]  # ANALYTICS


def get_top_artists(limit=10):  # ANALYTICS
    """Return top artists by download count."""  # ANALYTICS
    pipeline = [  # ANALYTICS
        {"$group": {  # ANALYTICS
            "_id": "$artist",  # ANALYTICS
            "count": {"$sum": 1},  # ANALYTICS
        }},  # ANALYTICS
        {"$sort": {"count": -1}},  # ANALYTICS
        {"$limit": limit},  # ANALYTICS
    ]  # ANALYTICS
    results = list(_downloads().aggregate(pipeline))  # ANALYTICS
    return [  # ANALYTICS
        {  # ANALYTICS
            "artist": r["_id"] or "Unknown",  # ANALYTICS
            "count": r["count"],  # ANALYTICS
            "storage_mb": 0,  # ANALYTICS
        }  # ANALYTICS
        for r in results  # ANALYTICS
    ]  # ANALYTICS


def get_source_breakdown():  # ANALYTICS
    """Return download counts grouped by source_platform."""  # ANALYTICS
    pipeline = [  # ANALYTICS
        {"$group": {  # ANALYTICS
            "_id": "$source_platform",  # ANALYTICS
            "count": {"$sum": 1},  # ANALYTICS
        }},  # ANALYTICS
    ]  # ANALYTICS
    results = list(_downloads().aggregate(pipeline))  # ANALYTICS
    total = sum(r["count"] for r in results)  # ANALYTICS
    return [  # ANALYTICS
        {  # ANALYTICS
            "platform": r["_id"] or "unknown",  # ANALYTICS
            "count": r["count"],  # ANALYTICS
            "percentage": round(r["count"] / total * 100, 1) if total > 0 else 0,  # ANALYTICS
        }  # ANALYTICS
        for r in results  # ANALYTICS
    ]  # ANALYTICS


def get_tagging_breakdown():  # ANALYTICS
    """Return how many tracks were tagged by each source."""  # ANALYTICS
    downloads = _downloads()  # ANALYTICS
    mb_count = downloads.count_documents({"tagging_report.source": "musicbrainz"})  # ANALYTICS
    sp_count = downloads.count_documents({"tagging_report.source": "spotify_fallback"})  # ANALYTICS
    none_count = downloads.count_documents({"tagging_report": {"$exists": False}})  # ANALYTICS
    return [  # ANALYTICS
        {"source": "MusicBrainz", "count": mb_count},  # ANALYTICS
        {"source": "Spotify Fallback", "count": sp_count},  # ANALYTICS
        {"source": "Untagged", "count": none_count},  # ANALYTICS
    ]  # ANALYTICS


def get_recent_downloads(limit=10):  # ANALYTICS
    """Return the most recent downloads for the dashboard table."""  # ANALYTICS
    results = list(_downloads().find(  # ANALYTICS
        {},  # ANALYTICS
        {"track_title": 1, "artist": 1, "downloaded_at": 1,  # ANALYTICS
         "source_platform": 1, "tagging_report": 1},  # ANALYTICS
    ).sort("downloaded_at", -1).limit(limit))  # ANALYTICS
    for r in results:  # ANALYTICS
        r["_id"] = str(r["_id"])  # ANALYTICS
        if r.get("downloaded_at"):  # ANALYTICS
            r["downloaded_at"] = r["downloaded_at"].isoformat()  # ANALYTICS
    return results  # ANALYTICS


def get_failed_downloads(limit=20):  # ANALYTICS
    """Return the most recent tagging failures."""  # ANALYTICS
    results = list(_failures().find(  # ANALYTICS
        {},  # ANALYTICS
        {"title": 1, "artist": 1, "error": 1, "timestamp": 1, "track_id": 1},  # ANALYTICS
    ).sort("timestamp", -1).limit(limit))  # ANALYTICS
    for r in results:  # ANALYTICS
        r["_id"] = str(r["_id"])  # ANALYTICS
        if r.get("timestamp"):  # ANALYTICS
            r["timestamp"] = r["timestamp"].isoformat()  # ANALYTICS
    return results  # ANALYTICS
