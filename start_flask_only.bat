@echo off
title Spotify Meta Downloader - Flask Backend Only
color 0A

echo.
echo  =============================================
echo   Spotify Meta Downloader - Backend Only
echo   (Telegram bot disabled for testing)
echo  =============================================
echo.

REM Disable Telegram bot to avoid conflict errors
set TELEGRAM_BOT_TOKEN=

REM Navigate to backend directory
cd /d "%~dp0backend"

REM Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found
    pause
    exit /b 1
)

REM Use venv if available
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON="%~dp0.venv\Scripts\python.exe"
) else (
    set PYTHON=python
)

echo.
echo  Starting Flask backend on http://localhost:5000
echo  (Telegram bot notifications disabled for testing)
echo.

%PYTHON% app.py

pause
