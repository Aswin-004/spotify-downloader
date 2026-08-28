"""
Production Startup Check
=========================
Runs before Gunicorn starts.  Validates that all required services
are reachable and exits with a non-zero code if any check fails,
which causes Render to surface the error rather than silently cycling.

Exit codes:
  0  — all checks passed
  1  — config validation failed (missing env vars)
  2  — MongoDB not reachable
  3  — Redis not reachable

Run: python startup_check.py
"""
import os
import sys
import time

# ── 1. Force production config validation ─────────────────────────────────────
# This imports config.py which calls ProductionConfig.__init__() if
# FLASK_ENV=production — it will sys.exit(1) if any required var is missing.
print("[startup] Loading config...")
try:
    from config import config  # triggers fail-fast if production + missing vars
    print(f"[startup] Config loaded: FLASK_ENV={config.FLASK_ENV}")
except SystemExit as e:
    sys.exit(int(str(e)) if str(e).isdigit() else 1)

# ── 2. MongoDB reachability ───────────────────────────────────────────────────
print("[startup] Checking MongoDB...")
max_attempts = 5
for attempt in range(1, max_attempts + 1):
    try:
        from database import is_mongo_available
        if is_mongo_available():
            print(f"[startup] MongoDB OK (attempt {attempt})")
            break
    except Exception as exc:
        print(f"[startup] MongoDB check error: {exc}", file=sys.stderr)
    if attempt == max_attempts:
        print("[startup] FATAL: MongoDB not reachable after 5 attempts", file=sys.stderr)
        sys.exit(2)
    print(f"[startup] MongoDB not ready, retrying in 3s... ({attempt}/{max_attempts})")
    time.sleep(3)

# ── 3. Redis reachability ─────────────────────────────────────────────────────
redis_url = os.getenv("REDIS_URL", "")
if redis_url:
    print("[startup] Checking Redis...")
    for attempt in range(1, max_attempts + 1):
        try:
            import redis as _redis_lib
            r = _redis_lib.from_url(redis_url, socket_connect_timeout=3)
            r.ping()
            print(f"[startup] Redis OK (attempt {attempt})")
            break
        except Exception as exc:
            if attempt == max_attempts:
                print(f"[startup] FATAL: Redis not reachable after 5 attempts: {exc}", file=sys.stderr)
                sys.exit(3)
            print(f"[startup] Redis not ready ({exc}), retrying in 3s... ({attempt}/{max_attempts})")
            time.sleep(3)
else:
    print("[startup] REDIS_URL not set — skipping Redis check (threading fallback)")

# ── 4. Report BASE_DOWNLOAD_DIR ───────────────────────────────────────────────
base_dir = os.getenv("BASE_DOWNLOAD_DIR", "")
if base_dir:
    if not os.path.isdir(base_dir):
        try:
            os.makedirs(base_dir, exist_ok=True)
            print(f"[startup] Created BASE_DOWNLOAD_DIR: {base_dir}")
        except Exception as exc:
            print(f"[startup] WARNING: Cannot create BASE_DOWNLOAD_DIR {base_dir}: {exc}", file=sys.stderr)
    else:
        print(f"[startup] BASE_DOWNLOAD_DIR exists: {base_dir}")
else:
    print("[startup] WARNING: BASE_DOWNLOAD_DIR not set — file downloads will fail")

# ── 5. YouTube cookies (optional) ────────────────────────────────────────────
_yt_cookies_b64 = os.getenv("YOUTUBE_COOKIES_B64", "")
if _yt_cookies_b64:
    try:
        import base64
        _cookies_path = "/tmp/youtube_cookies.txt"
        with open(_cookies_path, "wb") as _f:
            _f.write(base64.b64decode(_yt_cookies_b64))
        print(f"[startup] YouTube cookies decoded → {_cookies_path}")
    except Exception as _e:
        print(f"[startup] WARNING: Failed to decode YOUTUBE_COOKIES_B64: {_e}", file=sys.stderr)
else:
    print("[startup] YOUTUBE_COOKIES_B64 not set — downloads will run without cookies")

# ── 6. YouTube PO Token status ────────────────────────────────────────────────
_po_token = os.getenv("YOUTUBE_PO_TOKEN", "").strip()
if _po_token:
    print(f"[startup] YouTube PO Token active ({len(_po_token)} chars) — datacenter IP bypass enabled")
else:
    print("[startup] YOUTUBE_PO_TOKEN not set — set this env var to bypass YouTube IP block on Render")

print("[startup] All checks passed — starting Gunicorn")
sys.exit(0)
