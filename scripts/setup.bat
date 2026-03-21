@echo off
REM Spotify Meta Downloader - Windows CMD Setup Script
REM Supports Windows PowerShell and Command Prompt

setlocal enabledelayedexpansion
setlocal enableextensions

REM =============================================================================
REM COLOR CODES
REM =============================================================================

REM ANSI color codes (Windows 10+)
for /F %%A in ('copy /Z "%~f0" nul') do set "BS=%%A"

set "GREEN=[92m"
set "RED=[91m"
set "YELLOW=[93m"
set "CYAN=[96m"
set "MAGENTA=[95m"
set "RESET=[0m"

REM =============================================================================
REM UTILITY FUNCTIONS
REM =============================================================================

:print_header
setlocal
set "title=%~1"
echo.
echo %MAGENTA%============================================================%RESET%
echo %MAGENTA%   %title%%RESET%
echo %MAGENTA%============================================================%RESET%
endlocal
exit /b

:print_status
setlocal
set "status=%~1"
set "message=%~2"
if "%status%"=="SUCCESS" (
    echo %GREEN%[SUCCESS]%RESET% %message%
) else if "%status%"=="ERROR" (
    echo %RED%[ERROR]%RESET% %message%
) else if "%status%"=="WARNING" (
    echo %YELLOW%[WARNING]%RESET% %message%
) else (
    echo %CYAN%[INFO]%RESET% %message%
)
endlocal
exit /b

:print_section
setlocal
set "title=%~1"
echo.
echo %CYAN%%%^ %title%%RESET%
endlocal
exit /b

:exit_error
setlocal
set "message=%~1"
call :print_status ERROR "%message%"
pause
exit /b 1

:test_python
python --version >nul 2>&1
if errorlevel 1 (
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%i"
exit /b 0

:test_ffmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    exit /b 1
)
exit /b 0

:create_venv
if exist ".venv\" (
    call :print_status SUCCESS "Virtual environment already exists"
    exit /b 0
)

call :print_status INFO "Creating virtual environment..."
python -m venv .venv
if errorlevel 1 (
    call :exit_error "Failed to create virtual environment"
)
call :print_status SUCCESS "Virtual environment created"
exit /b 0

:activate_venv
call :print_status INFO "Activating virtual environment..."
call .venv\Scripts\activate.bat
if errorlevel 1 (
    call :exit_error "Failed to activate virtual environment"
)
call :print_status SUCCESS "Virtual environment activated"
exit /b 0

:install_deps
call :print_status INFO "Installing packages from requirements.txt..."
pip install --upgrade pip -q
pip install -r backend\requirements.txt
if errorlevel 1 (
    call :exit_error "Failed to install dependencies"
)
call :print_status SUCCESS "All dependencies installed successfully"
exit /b 0

:create_env_file
set "ENV_FILE=backend\.env"

if exist "%ENV_FILE%" (
    call :print_status SUCCESS "Environment file already exists"
    exit /b 0
)

call :print_status INFO "Creating %ENV_FILE% with placeholders..."

(
    echo # Spotify Meta Downloader - Environment Configuration
    echo # Created: %date% %time%
    echo.
    echo # Spotify API Credentials
    echo # Get these from: https://developer.spotify.com/dashboard
    echo SPOTIFY_CLIENT_ID=12eb015dc5a04c7abc9fb884e99ae193
    echo SPOTIFY_CLIENT_SECRET=e906b5f83bee4ce88faabaa90c7cd81d
    echo.
    echo # Flask Environment
    echo FLASK_ENV=development
) > "%ENV_FILE%"

if errorlevel 1 (
    call :exit_error "Failed to create .env file"
)

call :print_status SUCCESS ".env file created successfully"
exit /b 0

:print_instructions
echo.
echo %YELLOW%^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^%RESET%
echo %YELLOW%  IMPORTANT: UPDATE YOUR SPOTIFY CREDENTIALS%RESET%
echo %YELLOW%^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^%RESET%
echo.
echo 1. Go to Spotify Developer Dashboard:
echo    %CYAN%https://developer.spotify.com/dashboard%RESET%
echo.
echo 2. Create a new application
echo.
echo 3. Copy your credentials ^(Client ID and Client Secret^)
echo.
echo 4. Edit: backend\.env
echo    Replace the placeholder values with your actual credentials:
echo.
echo    %CYAN%SPOTIFY_CLIENT_ID=your_actual_client_id%RESET%
echo    %CYAN%SPOTIFY_CLIENT_SECRET=your_actual_client_secret%RESET%
echo.
echo %YELLOW%^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^%RESET%
echo.
echo %GREEN%NEXT STEPS:%RESET%
echo.
echo 1. Update your Spotify credentials in backend\.env
echo.
echo 2. Start the backend server:
echo    %CYAN%python backend\app.py%RESET%
echo.
echo 3. Open in your browser:
echo    %CYAN%http://localhost:5000%RESET%
echo.
echo %YELLOW%^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^%RESET%
echo.
echo %CYAN%USEFUL COMMANDS:%RESET%
echo   Activate venv:    .venv\Scripts\activate.bat
echo   Deactivate venv:  deactivate
echo   Update packages:  pip install --upgrade -r backend\requirements.txt
echo.
echo %YELLOW%^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^%RESET%
echo.
exit /b 0

REM =============================================================================
REM MAIN EXECUTION
REM =============================================================================

call :print_header "Spotify Meta Downloader - Setup"

REM Step 1: Check Python
call :print_section "Checking Python Installation"
call :test_python
if errorlevel 1 (
    call :exit_error "Python is not installed or not in PATH. Please install Python 3.10+ from https://www.python.org"
)
call :print_status SUCCESS "Python found: %PYTHON_VERSION%"

REM Step 2: Create virtual environment
call :print_section "Creating Python Virtual Environment"
call :create_venv

REM Step 3: Activate virtual environment
call :print_section "Activating Virtual Environment"
call :activate_venv

REM Step 4: Install dependencies
call :print_section "Installing Python Dependencies"
call :install_deps

REM Step 5: Check FFmpeg
call :print_section "Checking FFmpeg Installation"
call :test_ffmpeg
if errorlevel 1 (
    call :print_status WARNING "FFmpeg not found. Please download manually from:"
    echo    https://www.gyan.dev/ffmpeg/builds/
    echo.
    echo    Or use Chocolatey: choco install ffmpeg
    echo.
    echo    Then add C:\ffmpeg\bin to your PATH environment variable.
) else (
    call :print_status SUCCESS "FFmpeg is installed"
)

REM Step 6: Create .env file
call :print_section "Setting Up Environment Configuration"
call :create_env_file

REM Step 7: Print instructions
call :print_header "Setup Complete! [SUCCESS]"
call :print_instructions

pause
endlocal
