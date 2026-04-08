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


# ── New analytics functions ──────────────────────────────────────────────────

def get_cache_analytics():  # ANALYTICS
    """Return MusicBrainz cache statistics: size, hit rate, average age, API calls saved."""  # ANALYTICS
    cache = _mb_cache()  # ANALYTICS
    total = cache.count_documents({})  # ANALYTICS

    # Sum all recorded cache hits across every document
    hits_pipeline = [  # ANALYTICS
        {"$group": {"_id": None, "total_hits": {"$sum": {"$ifNull": ["$cache_hit_count", 0]}}}},  # ANALYTICS
    ]  # ANALYTICS
    hits_result = list(cache.aggregate(hits_pipeline))  # ANALYTICS
    total_hits = int(hits_result[0]["total_hits"]) if hits_result else 0  # ANALYTICS

    # Total lookups = initial fetch (wrote to cache) + every subsequent hit
    total_lookups = total + total_hits  # ANALYTICS
    hit_rate = round(total_hits / total_lookups * 100, 1) if total_lookups > 0 else 0.0  # ANALYTICS

    # Average age of cached entries in days
    age_pipeline = [  # ANALYTICS
        {"$match": {"cached_at": {"$exists": True}}},  # ANALYTICS
        {"$project": {  # ANALYTICS
            "age_ms": {"$subtract": [datetime.now(timezone.utc), "$cached_at"]},  # ANALYTICS
        }},  # ANALYTICS
        {"$group": {"_id": None, "avg_ms": {"$avg": "$age_ms"}}},  # ANALYTICS
    ]  # ANALYTICS
    age_result = list(cache.aggregate(age_pipeline))  # ANALYTICS
    avg_age_days = round(age_result[0]["avg_ms"] / 86_400_000, 1) if age_result else 0.0  # ANALYTICS

    return {  # ANALYTICS
        "total_cached_tracks": total,  # ANALYTICS
        "cache_hit_rate": hit_rate,  # ANALYTICS
        "avg_age_days": avg_age_days,  # ANALYTICS
        "api_calls_saved": total_hits,  # ANALYTICS — each hit avoided one MusicBrainz API call
        "total_hits": total_hits,  # ANALYTICS
    }  # ANALYTICS


def get_tagging_failure_summary():  # ANALYTICS
    """Return failure counts by error_type and a 7-day retry trend."""  # ANALYTICS
    # Breakdown by error_type
    type_pipeline = [  # ANALYTICS
        {"$group": {  # ANALYTICS
            "_id": "$error_type",  # ANALYTICS
            "count": {"$sum": 1},  # ANALYTICS
            "latest": {"$max": "$timestamp"},  # ANALYTICS
        }},  # ANALYTICS
        {"$sort": {"count": -1}},  # ANALYTICS
    ]  # ANALYTICS
    type_results = list(_failures().aggregate(type_pipeline))  # ANALYTICS

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # ANALYTICS
    retry_pipeline = [  # ANALYTICS
        {"$match": {"last_retry_timestamp": {"$gte": cutoff}}},  # ANALYTICS
        {"$group": {  # ANALYTICS
            "_id": {  # ANALYTICS
                "$dateToString": {"format": "%Y-%m-%d", "date": "$last_retry_timestamp"},  # ANALYTICS
            },  # ANALYTICS
            "retries": {"$sum": {"$ifNull": ["$retry_count", 0]}},  # ANALYTICS
        }},  # ANALYTICS
        {"$sort": {"_id": 1}},  # ANALYTICS
    ]  # ANALYTICS
    retry_trend = list(_failures().aggregate(retry_pipeline))  # ANALYTICS

    # Recent failures with error_type included
    recent = list(_failures().find(  # ANALYTICS
        {},  # ANALYTICS
        {"title": 1, "artist": 1, "error_type": 1, "timestamp": 1, "retry_count": 1},  # ANALYTICS
    ).sort("timestamp", -1).limit(10))  # ANALYTICS
    for r in recent:  # ANALYTICS
        r["_id"] = str(r["_id"])  # ANALYTICS
        if r.get("timestamp"):  # ANALYTICS
            r["timestamp"] = r["timestamp"].isoformat()  # ANALYTICS

    return {  # ANALYTICS
        "by_error_type": [  # ANALYTICS
            {  # ANALYTICS
                "error_type": r["_id"] or "unknown",  # ANALYTICS
                "count": r["count"],  # ANALYTICS
                "latest": r["latest"].isoformat() if r.get("latest") else None,  # ANALYTICS
            }  # ANALYTICS
            for r in type_results  # ANALYTICS
        ],  # ANALYTICS
        "retry_trend": [  # ANALYTICS
            {"date": r["_id"], "retries": r["retries"]}  # ANALYTICS
            for r in retry_trend  # ANALYTICS
        ],  # ANALYTICS
        "recent_failures": recent,  # ANALYTICS
    }  # ANALYTICS


def get_weekly_download_stats():  # ANALYTICS
    """Return this week's total, success rate, and top-3 artists."""  # ANALYTICS
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # ANALYTICS
    downloads = _downloads()  # ANALYTICS

    total_week = downloads.count_documents({"downloaded_at": {"$gte": cutoff}})  # ANALYTICS
    tagged_week = downloads.count_documents({  # ANALYTICS
        "downloaded_at": {"$gte": cutoff},  # ANALYTICS
        "tagging_report": {"$exists": True},  # ANALYTICS
    })  # ANALYTICS
    success_rate = round(tagged_week / total_week * 100, 1) if total_week > 0 else 0.0  # ANALYTICS

    top3_pipeline = [  # ANALYTICS
        {"$match": {"downloaded_at": {"$gte": cutoff}}},  # ANALYTICS
        {"$group": {"_id": "$artist", "count": {"$sum": 1}}},  # ANALYTICS
        {"$sort": {"count": -1}},  # ANALYTICS
        {"$limit": 3},  # ANALYTICS
    ]  # ANALYTICS
    top3 = list(downloads.aggregate(top3_pipeline))  # ANALYTICS

    return {  # ANALYTICS
        "total_this_week": total_week,  # ANALYTICS
        "success_rate": success_rate,  # ANALYTICS
        "top_artists": [  # ANALYTICS
            {"artist": r["_id"] or "Unknown", "count": r["count"]}  # ANALYTICS
            for r in top3  # ANALYTICS
        ],  # ANALYTICS
    }  # ANALYTICS
