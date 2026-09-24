@echo off
REM ===========================================================================
REM  Spotify Meta Downloader - one-time setup (Windows). Safe to run again.
REM  1. checks Python + Node.js (offers to install them with winget)
REM  2. creates .venv and installs the Python packages (incl. a bundled ffmpeg)
REM  3. installs the web app's packages and builds it
REM  4. creates backend\.env from backend\.env.example and opens it for you
REM  5. logs in to Spotify once and checks everything (backend\check_setup.py)
REM  Guide: docs\SETUP.md
REM ===========================================================================
setlocal
title Spotify Meta Downloader - Setup
cd /d "%~dp0"

echo.
echo  ==============================================
echo    Spotify Meta Downloader - Setup
echo  ==============================================
echo.

REM -- 1. Python 3.11+ ---------------------------------------------------------
set "PY="
where py >nul 2>&1 && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo  Python 3.11 or newer is needed.
    choice /c YN /m "  Install Python 3.12 now with winget"
    if errorlevel 2 goto :no_python
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo  Python installed. Close this window and run setup.bat again ^(Windows must reload PATH^).
    pause
    exit /b 0
)
for /f "delims=" %%v in ('%PY% --version') do echo  [1/5] %%v found.

REM -- 2. Node.js (for the web app) --------------------------------------------
where npm >nul 2>&1
if errorlevel 1 (
    echo  Node.js is needed to build the web app.
    choice /c YN /m "  Install Node.js LTS now with winget"
    if errorlevel 2 goto :no_node
    winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    echo.
    echo  Node.js installed. Close this window and run setup.bat again ^(Windows must reload PATH^).
    pause
    exit /b 0
)
for /f "delims=" %%v in ('node --version') do echo  [2/5] Node.js %%v found.

REM -- 3. Python environment ---------------------------------------------------
echo.
echo  [3/5] Installing Python packages (first time: a few minutes)...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :fail

REM -- 4. Web app --------------------------------------------------------------
echo.
echo  [4/5] Installing and building the web app...
pushd frontend-react
call npm install --no-audit --no-fund
if errorlevel 1 (popd & goto :fail)
call npm run build
if errorlevel 1 (popd & goto :fail)
popd

REM -- 5. Settings + Spotify login ---------------------------------------------
echo.
if not exist "backend\.env" (
    copy /y "backend\.env.example" "backend\.env" >nul
    REM a random SECRET_KEY so nobody has to invent one
    ".venv\Scripts\python.exe" -c "import pathlib,secrets; p=pathlib.Path('backend/.env'); p.write_text(p.read_text(encoding='utf-8').replace('change_me_to_a_random_64_char_hex_string', secrets.token_hex(32)), encoding='utf-8')"
    echo  [5/5] Created backend\.env - fill in the REQUIRED part ^(docs\SETUP.md explains each line^).
    start "" notepad "backend\.env"
    start "" "docs\SETUP.md"
    echo.
    echo  Save the file in Notepad, then come back here.
    pause
) else (
    echo  [5/5] backend\.env already exists - keeping your settings.
)

echo.
if not exist "backend\services\.spotify_oauth_cache" (
    echo  Logging in to Spotify: a browser window opens - log in and click Agree.
    pushd backend
    "..\.venv\Scripts\python.exe" spotify_login.py
    popd
)

echo.
echo  Checking everything...
pushd backend
"..\.venv\Scripts\python.exe" check_setup.py
set "CHECK=%errorlevel%"
popd
echo.
if "%CHECK%"=="0" (
    echo  Setup complete. From now on just double-click start.bat.
) else (
    echo  Fix the items marked FAIL ^(edit backend\.env^), then run setup.bat again.
)
pause
exit /b %CHECK%

:no_python
echo  Install Python 3.11+ from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run setup.bat again.
pause
exit /b 1

:no_node
echo  Install Node.js LTS from https://nodejs.org, then run setup.bat again.
pause
exit /b 1

:fail
echo.
echo  Setup stopped because the step above failed. Read the error, fix it, and run setup.bat again.
pause
exit /b 1
