# ═══════════════════════════════════════════════════════════════════
# Spotify Meta Downloader — Full Stack Launcher (Windows PowerShell)
# ═══════════════════════════════════════════════════════════════════
# Usage:  .\scripts\start.ps1
#   Starts Redis (Docker), MongoDB (Docker), Celery worker,
#   Flower dashboard, and the Flask backend.  Ctrl+C stops everything.
# ═══════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# ── 1. Activate venv if present ──────────────────────────────────
$venvActivate = "$ROOT\.venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    Write-Host "[+] Activating virtual environment ..." -ForegroundColor Cyan
    & $venvActivate
}

# ── 2. Start Redis ────────────────────────────────────────────────  # CELERY FIX
$redisRunning = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", 6379)
    $tcp.Close()
    $redisRunning = $true
    Write-Host "[+] Redis already running on localhost:6379" -ForegroundColor Green
} catch {
    # Try local redis-server first (winget install)  # CELERY FIX
    $redisExe = Get-Command redis-server -ErrorAction SilentlyContinue  # CELERY FIX
    if (-not $redisExe) {  # CELERY FIX
        # Refresh PATH in case Redis was just installed  # CELERY FIX
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")  # CELERY FIX
        $redisExe = Get-Command redis-server -ErrorAction SilentlyContinue  # CELERY FIX
    }  # CELERY FIX
    if ($redisExe) {  # CELERY FIX
        Write-Host "[+] Starting local Redis server ..." -ForegroundColor Cyan  # CELERY FIX
        Start-Process $redisExe.Source -WindowStyle Minimized  # CELERY FIX
        Start-Sleep -Seconds 2  # CELERY FIX
        try {  # CELERY FIX
            $tcp2 = New-Object System.Net.Sockets.TcpClient  # CELERY FIX
            $tcp2.Connect("127.0.0.1", 6379)  # CELERY FIX
            $tcp2.Close()  # CELERY FIX
            $redisRunning = $true  # CELERY FIX
            Write-Host "[+] Redis is running!" -ForegroundColor Green  # CELERY FIX
        } catch {  # CELERY FIX
            Write-Host "[!] Redis started but not responding" -ForegroundColor Yellow  # CELERY FIX
        }  # CELERY FIX
    } else {  # CELERY FIX
        # Try Docker as fallback  # CELERY FIX
        $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue  # CELERY FIX
        if ($dockerCmd) {  # CELERY FIX
            Write-Host "[+] Starting Redis via Docker ..." -ForegroundColor Cyan  # CELERY FIX
            Push-Location $ROOT  # CELERY FIX
            try {
                docker compose up -d redis  # CELERY FIX
                Start-Sleep -Seconds 3  # CELERY FIX
                try {  # CELERY FIX
                    $tcp3 = New-Object System.Net.Sockets.TcpClient  # CELERY FIX
                    $tcp3.Connect("127.0.0.1", 6379)  # CELERY FIX
                    $tcp3.Close()  # CELERY FIX
                    $redisRunning = $true  # CELERY FIX
                    Write-Host "[+] Redis is running (Docker)!" -ForegroundColor Green  # CELERY FIX
                } catch {  # CELERY FIX
                    Write-Host "[!] Docker Redis started but not responding" -ForegroundColor Yellow  # CELERY FIX
                }
            } catch {
                Write-Host "[!] Docker compose failed — Celery disabled" -ForegroundColor Yellow  # CELERY FIX
            }
            Pop-Location  # CELERY FIX
        } else {  # CELERY FIX
            Write-Host "[!] No Redis or Docker found — Celery will be disabled (threading fallback)" -ForegroundColor Yellow  # CELERY FIX
            Write-Host "    Install Redis: winget install Redis.Redis" -ForegroundColor Yellow  # CELERY FIX
        }
    }
}

# ── 2b. Start MongoDB ────────────────────────────────────────────  # MUSICBRAINZ
$mongoRunning = $false  # MUSICBRAINZ
try {  # MUSICBRAINZ
    $tcp = New-Object System.Net.Sockets.TcpClient  # MUSICBRAINZ
    $tcp.Connect("127.0.0.1", 27017)  # MUSICBRAINZ
    $tcp.Close()  # MUSICBRAINZ
    $mongoRunning = $true  # MUSICBRAINZ
    Write-Host "[+] MongoDB already running on localhost:27017" -ForegroundColor Green  # MUSICBRAINZ
} catch {  # MUSICBRAINZ
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue  # MUSICBRAINZ
    if ($dockerCmd) {  # MUSICBRAINZ
        Write-Host "[+] Starting MongoDB via Docker ..." -ForegroundColor Cyan  # MUSICBRAINZ
        Push-Location $ROOT  # MUSICBRAINZ
        try {  # MUSICBRAINZ
            docker compose up -d mongo  # MUSICBRAINZ
            Start-Sleep -Seconds 3  # MUSICBRAINZ
            try {  # MUSICBRAINZ
                $tcp2 = New-Object System.Net.Sockets.TcpClient  # MUSICBRAINZ
                $tcp2.Connect("127.0.0.1", 27017)  # MUSICBRAINZ
                $tcp2.Close()  # MUSICBRAINZ
                $mongoRunning = $true  # MUSICBRAINZ
                Write-Host "[+] MongoDB is running (Docker)!" -ForegroundColor Green  # MUSICBRAINZ
            } catch {  # MUSICBRAINZ
                Write-Host "[!] Docker MongoDB started but not responding" -ForegroundColor Yellow  # MUSICBRAINZ
            }  # MUSICBRAINZ
        } catch {  # MUSICBRAINZ
            Write-Host "[!] Docker compose failed for MongoDB" -ForegroundColor Yellow  # MUSICBRAINZ
        }  # MUSICBRAINZ
        Pop-Location  # MUSICBRAINZ
    } else {  # MUSICBRAINZ
        Write-Host "[!] MongoDB not found — download history and tagging cache will fail" -ForegroundColor Yellow  # MUSICBRAINZ
        Write-Host "    Run: docker compose up -d mongo" -ForegroundColor Yellow  # MUSICBRAINZ
    }  # MUSICBRAINZ
}  # MUSICBRAINZ

Push-Location "$ROOT\backend"

$jobs = @()

if ($redisRunning) {
    # ── 3. Celery worker ─────────────────────────────────────────
    Write-Host "[+] Starting Celery worker ..." -ForegroundColor Cyan
    $jobs += Start-Process -FilePath "python" `
        -ArgumentList "-m", "celery", "-A", "celery_app", "worker", "--loglevel=info", "--pool=solo" `
        -NoNewWindow -PassThru

    Start-Sleep -Seconds 2  # CELERY FIX: give worker time to connect

    # ── 4. Flower dashboard (http://localhost:5555) ──────────────
    Write-Host "[+] Starting Flower dashboard on :5555 ..." -ForegroundColor Cyan
    $jobs += Start-Process -FilePath "python" `
        -ArgumentList "-m", "celery", "-A", "celery_app", "flower", "--port=5555" `
        -NoNewWindow -PassThru

    Write-Host ""  # CELERY FIX
    Write-Host "  Flower dashboard: http://localhost:5555" -ForegroundColor Magenta  # CELERY FIX
    Write-Host ""  # CELERY FIX
} else {
    Write-Host "[!] Redis not reachable — Celery will be disabled (threading fallback)" -ForegroundColor Yellow
}

# ── 5. Flask backend ─────────────────────────────────────────────
Write-Host "[+] Starting Flask backend ..." -ForegroundColor Cyan
try {
    python app.py
} finally {
    # Cleanup child processes on exit
    foreach ($j in $jobs) {
        if (-not $j.HasExited) {
            Write-Host "[-] Stopping PID $($j.Id) ..." -ForegroundColor Yellow
            Stop-Process -Id $j.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Pop-Location
    Write-Host "[x] All processes stopped." -ForegroundColor Red
}
