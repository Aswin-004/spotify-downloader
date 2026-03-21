@echo off
REM Spotify Meta Downloader - FFmpeg Installer for Windows
REM This script installs FFmpeg automatically and adds it to PATH

setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   FFmpeg Installation Helper
echo ============================================================
echo.

REM Check if FFmpeg is already installed
ffmpeg -version >nul 2>&1
if not errorlevel 1 (
    echo [INFO] FFmpeg is already installed
    ffmpeg -version | find "ffmpeg version"
    echo.
    pause
    exit /b 0
)

echo [INFO] FFmpeg not found in PATH
echo.
echo This script will help you install FFmpeg.
echo.
echo Options:
echo 1. Download and install automatically (requires 7-Zip)
echo 2. Install via Chocolatey (requires Chocolatey)
echo 3. Manual installation instructions
echo.

set /p choice="Enter your choice (1-3): "

if "%choice%"=="1" goto install_auto
if "%choice%"=="2" goto install_choco
if "%choice%"=="3" goto manual_install
goto invalid_choice

:install_auto
echo.
echo [INFO] Checking for 7-Zip...
where /q 7z >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 7-Zip is not installed
    echo Please install 7-Zip or use option 2 (Chocolatey)
    pause
    exit /b 1
)

echo [INFO] Downloading FFmpeg...
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.7z', '%TEMP%\ffmpeg.7z')}"

if not exist "%TEMP%\ffmpeg.7z" (
    echo [ERROR] Failed to download FFmpeg
    pause
    exit /b 1
)

echo [INFO] Extracting FFmpeg...
7z x "%TEMP%\ffmpeg.7z" -o"%TEMP%\"
if errorlevel 1 (
    echo [ERROR] Failed to extract FFmpeg
    pause
    exit /b 1
)

echo [INFO] Finding FFmpeg directory...
for /d %%D in ("%TEMP%\ffmpeg*") do (
    set "FFMPEG_DIR=%%D"
)

if not defined FFMPEG_DIR (
    echo [ERROR] Could not find extracted FFmpeg directory
    pause
    exit /b 1
)

echo [INFO] Moving FFmpeg to C:\ffmpeg...
if exist "C:\ffmpeg" rmdir /s /q "C:\ffmpeg"
move "%FFMPEG_DIR%" "C:\ffmpeg"

echo [INFO] Cleaning up...
del /f /q "%TEMP%\ffmpeg.7z"

echo [INFO] Adding FFmpeg to PATH...
setx PATH "!PATH!;C:\ffmpeg\bin"
set "PATH=!PATH!;C:\ffmpeg\bin"

echo [SUCCESS] FFmpeg installed successfully
ffmpeg -version | find "ffmpeg version"

pause
exit /b 0

:install_choco
echo.
echo [INFO] Installing FFmpeg via Chocolatey...
echo.

REM Check if Chocolatey is installed
where /q choco >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Chocolatey is not installed
    echo Visit https://chocolatey.org/install to install Chocolatey
    pause
    exit /b 1
)

REM Install FFmpeg
choco install ffmpeg -y

if errorlevel 1 (
    echo [ERROR] Failed to install FFmpeg via Chocolatey
    pause
    exit /b 1
)

echo [SUCCESS] FFmpeg installed successfully
ffmpeg -version | find "ffmpeg version"

pause
exit /b 0

:manual_install
echo.
echo [INFO] Manual Installation Instructions
echo.
echo 1. Download FFmpeg essentials build from:
echo    https://www.gyan.dev/ffmpeg/builds/
echo.
echo 2. Extract the downloaded file
echo.
echo 3. Move the extracted folder to:
echo    C:\ffmpeg
echo.
echo 4. Add C:\ffmpeg\bin to your Windows PATH:
echo    - Right-click "This PC" and select "Properties"
echo    - Click "Advanced system settings"
echo    - Click "Environment Variables"
echo    - Under "System variables", click "Path" and click "Edit"
echo    - Click "New" and add: C:\ffmpeg\bin
echo    - Click "OK" on all dialogs
echo.
echo 5. Restart PowerShell or Command Prompt
echo.
echo 6. Verify installation by running:
echo    ffmpeg -version
echo.
pause
exit /b 0

:invalid_choice
echo [ERROR] Invalid choice
pause
exit /b 1
