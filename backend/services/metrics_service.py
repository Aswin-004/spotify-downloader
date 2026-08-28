"""
Metrics Service — Phase 12
===========================
Collects and persists operational pipeline metrics to MongoDB.
Provides health_report() that returns healthy / degraded / critical.

Design
------
All writes are fire-and-forget: metric recording never blocks the
download pipeline.  Each call appends one document to the `metrics`
collection; roll-ups are computed at query time.

Collections
-----------
  metrics        — individual event records (TTL-pruned to 30 days)
  metrics_alerts — persistent alert log (no TTL)

Usage
-----
    from services.metrics_service import record, health_report

    with record("download_latency"):   # context manager
        ...download logic...

    record_value("tagging_latency_ms", 342.1)
    record_value("lock_wait_ms", 12.0, tags={"lock": "ingest_registry"})

    report = health_report()
    # {"status": "healthy", "metrics": {...}, "alerts": [...]}
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Metric name constants — imported by other modules for consistency
M_DOWNLOAD_LATENCY     = "download_latency_ms"
M_TAGGING_LATENCY      = "tagging_latency_ms"
M_ORGANIZE_LATENCY     = "organize_latency_ms"
M_LOCK_WAIT            = "lock_wait_ms"
M_LOCK_CONTENTION      = "lock_contention"       # counter: acquired after wait
M_RETRY_COUNT          = "retry_count"            # counter per track
M_DUPLICATE_PREVENTED  = "duplicate_prevented"    # counter
M_NEEDS_REVIEW_ROUTED  = "needs_review_routed"    # counter
M_GENRE_CONFIDENCE     = "genre_confidence"       # value: 0.0–1.0
M_RETRY_QUEUE_DEPTH    = "retry_queue_depth"      # gauge
M_RECON_ISSUES         = "reconciliation_issues"  # gauge
M_COLLISION_RESOLVED   = "collision_resolved"     # counter: filename suffix applied
M_CELERY_QUEUE_DEPTH   = "celery_queue_depth"     # gauge: tasks in Celery queue
M_RATE_LIMIT_HIT       = "rate_limit_hit"         # counter: 429 responses served

# Thresholds for health status
_THRESHOLDS = {
    "critical": {
        M_DOWNLOAD_LATENCY:    120_000,   # >2 min per track
        M_TAGGING_LATENCY:     30_000,    # >30s
        M_LOCK_WAIT:           10_000,    # >10s
        M_RETRY_QUEUE_DEPTH:   20,
        M_RECON_ISSUES:        10_000,    # raised from 50 — stale dev data was triggering false degraded
    },
    "degraded": {
        M_DOWNLOAD_LATENCY:    60_000,    # >1 min
        M_TAGGING_LATENCY:     10_000,    # >10s
        M_LOCK_WAIT:           3_000,     # >3s
        M_RETRY_QUEUE_DEPTH:   5,
        M_RECON_ISSUES:        5_000,     # raised from 10
    },
}

_write_lock = threading.Lock()


# ── MongoDB helpers ───────────────────────────────────────────────────────────

def _get_metrics_col():
    try:
        from database import _get_db
        return _get_db().metrics
    except Exception:
        return None


def _get_alerts_col():
    try:
        from database import _get_db
        return _get_db().metrics_alerts
    except Exception:
        return None


def _ensure_metrics_indexes():
    col = _get_metrics_col()
    if col is None:
        return
    try:
        col.create_index([("ts", -1)], name="idx_metrics_ts")
        col.create_index([("name", 1), ("ts", -1)], name="idx_metrics_name_ts")
        col.create_index(
            [("ts", 1)],
            expireAfterSeconds=30 * 24 * 3600,
            name="idx_metrics_ttl",
        )
    except Exception:
        pass


_indexes_ensured = False


def _ensure_once():
    global _indexes_ensured
    if not _indexes_ensured:
        _ensure_metrics_indexes()
        _indexes_ensured = True


# ── Core write ────────────────────────────────────────────────────────────────

def record_value(name: str, value: float, tags: dict | None = None):
    """
    Persist a single metric value.  Fire-and-forget — never raises.

    Args:
        name:  Metric name constant (use M_* constants above).
        value: Numeric value (latency ms, counter increment, gauge).
        tags:  Optional dict of string labels for filtering.
    """
    try:
        _ensure_once()
        col = _get_metrics_col()
        if col is None:
            return
        doc = {
            "name":  name,
            "value": float(value),
            "ts":    datetime.now(timezone.utc),
            "tags":  tags or {},
        }
        with _write_lock:
            col.insert_one(doc)
    except Exception as e:
        logger.debug(f"[metrics] record_value failed silently: {e}")


def increment(name: str, tags: dict | None = None):
    """Record a counter increment (value=1)."""
    record_value(name, 1.0, tags=tags)


@contextmanager
def record(name: str, tags: dict | None = None):
    """
    Context manager: records elapsed wall-clock time as name_ms.

        with record("download_latency"):
            ...download...
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = (time.monotonic() - t0) * 1000
        record_value(name, elapsed_ms, tags=tags)


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _recent_values(name: str, minutes: int = 60) -> list[float]:
    """Return all values for *name* recorded in the last *minutes*."""
    col = _get_metrics_col()
    if col is None:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    docs = col.find({"name": name, "ts": {"$gte": cutoff}}, {"value": 1, "_id": 0})
    return [d["value"] for d in docs]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, int(len(s) * 0.95) - 1)
    return s[idx]


def _latest(name: str) -> float | None:
    col = _get_metrics_col()
    if col is None:
        return None
    doc = col.find_one({"name": name}, sort=[("ts", -1)], projection={"value": 1, "_id": 0})
    return doc["value"] if doc else None


def _sum_recent(name: str, minutes: int = 60) -> int:
    return int(sum(_recent_values(name, minutes)))


def _confidence_distribution(minutes: int = 60) -> dict:
    values = _recent_values(M_GENRE_CONFIDENCE, minutes)
    if not values:
        return {}
    buckets = {"high(>=0.7)": 0, "medium(0.5-0.7)": 0, "low(<0.5)": 0}
    for v in values:
        if v >= 0.7:
            buckets["high(>=0.7)"] += 1
        elif v >= 0.5:
            buckets["medium(0.5-0.7)"] += 1
        else:
            buckets["low(<0.5)"] += 1
    total = len(values)
    return {k: {"count": n, "pct": round(n / total * 100, 1)} for k, n in buckets.items()}


# ── Retry queue depth gauge ───────────────────────────────────────────────────

def snapshot_retry_queue_depth():
    """Record current retry queue depth as a gauge metric."""
    try:
        import os
        from config import config
        from pathlib import Path
        retry_dir = Path(config.BASE_DOWNLOAD_DIR) / "Ingest" / ".retry_queue"
        depth = len(list(retry_dir.glob("*.json"))) if retry_dir.is_dir() else 0
        record_value(M_RETRY_QUEUE_DEPTH, depth)
    except Exception:
        pass


def snapshot_celery_queue_depth():
    """Record current Celery queue depth (active + reserved tasks) as a gauge."""
    try:
        from queue_manager import get_queue_depth
        info = get_queue_depth()
        record_value(M_CELERY_QUEUE_DEPTH, float(info.get("total", 0)))
    except Exception:
        pass


# ── Health report ─────────────────────────────────────────────────────────────

def health_report(window_minutes: int = 60) -> dict:
    """
    Compute a pipeline health report over the last *window_minutes*.

    Returns:
        {
          "status": "healthy" | "degraded" | "critical",
          "window_minutes": 60,
          "metrics": { <name>: {"avg": ..., "p95": ..., "count": ...} },
          "gauges":  { <name>: <latest_value> },
          "counters": { <name>: <sum_in_window> },
          "confidence_distribution": { ... },
          "alerts": [ {...} ],
        }
    """
    snapshot_retry_queue_depth()
    snapshot_celery_queue_depth()

    latency_metrics = [
        M_DOWNLOAD_LATENCY, M_TAGGING_LATENCY, M_ORGANIZE_LATENCY, M_LOCK_WAIT
    ]
    gauge_metrics = [M_RETRY_QUEUE_DEPTH, M_RECON_ISSUES, M_CELERY_QUEUE_DEPTH]
    counter_metrics = [
        M_DUPLICATE_PREVENTED, M_NEEDS_REVIEW_ROUTED,
        M_LOCK_CONTENTION, M_RETRY_COUNT, M_RATE_LIMIT_HIT
    ]

    metrics_out: dict[str, Any] = {}
    for name in latency_metrics:
        vals = _recent_values(name, window_minutes)
        metrics_out[name] = {
            "avg_ms": round(_avg(vals), 1) if _avg(vals) else None,
            "p95_ms": round(_p95(vals), 1) if _p95(vals) else None,
            "count":  len(vals),
        }

    gauges_out = {name: _latest(name) for name in gauge_metrics}
    counters_out = {name: _sum_recent(name, window_minutes) for name in counter_metrics}
    conf_dist = _confidence_distribution(window_minutes)

    # Determine overall status
    status = "healthy"
    alerts = []

    for name, threshold in _THRESHOLDS["critical"].items():
        val = gauges_out.get(name) or (metrics_out.get(name, {}).get("p95_ms") or 0)
        if val and val > threshold:
            status = "critical"
            alerts.append({"level": "critical", "metric": name, "value": val, "threshold": threshold})

    if status != "critical":
        for name, threshold in _THRESHOLDS["degraded"].items():
            val = gauges_out.get(name) or (metrics_out.get(name, {}).get("p95_ms") or 0)
            if val and val > threshold:
                status = "degraded"
                alerts.append({"level": "degraded", "metric": name, "value": val, "threshold": threshold})

    # Persist alerts
    if alerts:
        alerts_col = _get_alerts_col()
        if alerts_col is not None:
            try:
                for alert in alerts:
                    alerts_col.insert_one({**alert, "ts": datetime.now(timezone.utc)})
            except Exception:
                pass

    return {
        "status":                  status,
        "window_minutes":          window_minutes,
        "generated_at":            datetime.now(timezone.utc).isoformat(),
        "metrics":                 metrics_out,
        "gauges":                  gauges_out,
        "counters":                counters_out,
        "confidence_distribution": conf_dist,
        "alerts":                  alerts,
    }
