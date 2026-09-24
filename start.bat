@echo off
title Spotify Meta Downloader
color 0A

echo.
echo  =============================================
echo   Spotify Meta Downloader - Starting up...
echo  =============================================
echo.

REM -- Step 1: Build frontend ------------------------------------------------
REM Already running? Never start a second copy: two copies would each download every new song.
netstat -ano | findstr /R /C:":5000 .*LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo  The app is already running - opening it in your browser.
    start "" http://localhost:5000
    exit /b 0
)

REM Not set up yet? setup.bat does everything once (packages, settings, Spotify login).
if not exist "%~dp0.venv\Scripts\python.exe" goto :needs_setup
if not exist "%~dp0backend\.env" goto :needs_setup

echo  [1/3] Building frontend...
cd /d "%~dp0frontend-react"

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: npm not found. Install Node.js from https://nodejs.org
    pause
    exit /b 1
)

call npm run build
if %errorlevel% neq 0 (
    echo  ERROR: Frontend build failed.
    pause
    exit /b 1
)
echo  [1/3] Frontend built successfully.
echo.

REM -- Step 2: Start Redis + Celery worker (optional - app works without them) -
echo  [2/3] Starting Redis + Celery worker (optional)...
cd /d "%~dp0backend"

REM Try to start Redis if redis-server is on PATH
where redis-server >nul 2>&1
if %errorlevel% equ 0 (
    start "Redis" /min redis-server
    timeout /t 2 /nobreak >nul
    echo  Redis started.
) else (
    echo  Redis not found - skipping. Downloads will use thread mode instead.
    echo  Install Redis for Windows: https://github.com/microsoftarchive/redis/releases
)

REM Try to start Celery worker if Redis is running
where redis-cli >nul 2>&1
if %errorlevel% equ 0 (
    redis-cli ping >nul 2>&1
    if %errorlevel% equ 0 (
        start "Celery Worker" /min cmd /k "cd /d "%~dp0backend" && celery -A celery_app worker --loglevel=info --concurrency=2 --pool=threads"
        echo  Celery worker started.
    )
)
echo.

REM -- Step 3: Start backend -------------------------------------------------
echo  [3/3] Starting backend on http://localhost:5000 ...

REM Prefer venv Python so all installed packages are available
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON="%~dp0.venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        echo  ERROR: Python not found. Install Python 3.10+.
        pause
        exit /b 1
    )
    set PYTHON=python
)

echo.
echo  Open your browser at:  http://localhost:5000
echo  Press Ctrl+C to stop.
echo.

%PYTHON% app.py

pause
exit /b 0

:needs_setup
echo  This copy is not set up yet. Running setup.bat first...
call "%~dp0setup.bat"
exit /b
