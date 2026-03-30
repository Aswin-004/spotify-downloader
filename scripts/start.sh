#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Spotify Meta Downloader — Full Stack Launcher (Linux / macOS)
# ═══════════════════════════════════════════════════════════════════
# Usage:  bash scripts/start.sh
# ═══════════════════════════════════════════════════════════════════

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

# ── 1. Activate venv if present ──────────────────────────────────
if [ -f "$ROOT/.venv/bin/activate" ]; then
    echo "[+] Activating virtual environment ..."
    source "$ROOT/.venv/bin/activate"
fi

PIDS=()
cleanup() {
    echo ""
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "[-] Stopping PID $pid ..."
            kill "$pid" 2>/dev/null || true
        fi
    done
    echo "[x] All processes stopped."
    exit 0
}
trap cleanup INT TERM

# ── 2. Check for Redis ───────────────────────────────────────────
REDIS_OK=false
if redis-cli ping >/dev/null 2>&1; then
    REDIS_OK=true
    echo "[+] Redis detected on localhost:6379"
else
    echo "[!] Redis not reachable — Celery disabled (threading fallback)"
fi

if [ "$REDIS_OK" = true ]; then
    # ── 3. Celery worker ─────────────────────────────────────────
    echo "[+] Starting Celery worker ..."
    celery -A celery_app worker --loglevel=info &
    PIDS+=($!)

    # ── 4. Flower dashboard (http://localhost:5555) ──────────────
    echo "[+] Starting Flower dashboard on :5555 ..."
    celery -A celery_app flower --port=5555 &
    PIDS+=($!)
fi

# ── 5. Flask backend ─────────────────────────────────────────────
echo "[+] Starting Flask backend ..."
python app.py &
PIDS+=($!)

wait
