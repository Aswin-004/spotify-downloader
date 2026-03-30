"""
Celery Health Check
====================
Verifies Redis connectivity, Celery worker availability,
and task registration.

Usage:  python celery_health_check.py
"""
import sys
import os

# Ensure backend dir is on path
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


def check_redis():
    """Check if Redis is reachable."""
    print("1. Checking Redis connection...")
    try:
        from celery_app import REDIS_URL, is_redis_available
        if is_redis_available():
            print(f"   ✅ Redis is reachable at {REDIS_URL}")
            return True
        else:
            print(f"   ❌ Redis not reachable at {REDIS_URL}")
            print("   → Run: docker-compose up -d redis")
            return False
    except Exception as e:
        print(f"   ❌ Redis check failed: {e}")
        return False


def check_celery_workers():
    """Check if any Celery workers are online."""
    print("\n2. Checking Celery workers...")
    try:
        from celery_app import celery_app
        result = celery_app.control.ping(timeout=3)
        if result:
            for worker_response in result:
                for worker_name, status in worker_response.items():
                    print(f"   ✅ Worker online: {worker_name} → {status}")
            return True
        else:
            print("   ❌ No Celery workers found!")
            print("   → Run: cd backend && celery -A celery_app worker --loglevel=info --pool=solo")
            return False
    except Exception as e:
        print(f"   ❌ Celery ping failed: {e}")
        return False


def check_registered_tasks():
    """Check which tasks are registered on workers."""
    print("\n3. Checking registered tasks...")
    try:
        from celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        registered = inspect.registered()
        if registered:
            for worker, tasks in registered.items():
                print(f"   ✅ Worker '{worker}' — {len(tasks)} tasks:")
                for t in sorted(tasks):
                    print(f"      • {t}")
            return True
        else:
            print("   ❌ No tasks registered (workers may not be running)")
            return False
    except Exception as e:
        print(f"   ❌ Task inspection failed: {e}")
        return False


def check_active_tasks():
    """Show any currently active tasks."""
    print("\n4. Checking active tasks...")
    try:
        from celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        active = inspect.active()
        if active:
            total = 0
            for worker, tasks in active.items():
                total += len(tasks)
                if tasks:
                    print(f"   🔄 Worker '{worker}' — {len(tasks)} active:")
                    for t in tasks:
                        print(f"      • {t.get('name', '?')} [id={t.get('id', '?')[:8]}...]")
                else:
                    print(f"   💤 Worker '{worker}' — idle")
            if total == 0:
                print("   ✅ All workers idle")
        else:
            print("   ⚠️  Could not inspect active tasks")
        return True
    except Exception as e:
        print(f"   ❌ Active task check failed: {e}")
        return False


def main():
    print("=" * 55)
    print("  Celery Health Check — Spotify Meta Downloader")
    print("=" * 55)
    print()

    redis_ok = check_redis()
    if not redis_ok:
        print("\n❌ Redis is required. Start it first:")
        print("   docker-compose up -d redis\n")
        sys.exit(1)

    workers_ok = check_celery_workers()
    tasks_ok = check_registered_tasks() if workers_ok else False
    check_active_tasks() if workers_ok else None

    print("\n" + "=" * 55)
    if redis_ok and workers_ok and tasks_ok:
        print("  ✅ ALL CHECKS PASSED — Celery is fully operational")
    elif redis_ok and not workers_ok:
        print("  ⚠️  Redis OK but no workers — start the Celery worker")
    else:
        print("  ❌ ISSUES FOUND — see details above")
    print("=" * 55)

    sys.exit(0 if (redis_ok and workers_ok) else 1)


if __name__ == "__main__":
    main()
