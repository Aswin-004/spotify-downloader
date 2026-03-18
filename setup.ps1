#Requires -Version 5.0
<#
.SYNOPSIS
    Spotify Meta Downloader - Windows PowerShell Setup Script
    
.DESCRIPTION
    Automated setup script for Spotify Meta Downloader project.
    Handles Python environment, FFmpeg installation, and dependency setup.
    
.EXAMPLE
    .\setup.ps1
    
.EXAMPLE
    .\setup.ps1 -SkipFFmpeg
    
.PARAMETER SkipFFmpeg
    Skip FFmpeg installation/verification (optional)
#>

param(
    [switch]$SkipFFmpeg = $false,
    [switch]$Force = $false
)

# Color definitions
$Colors = @{
    "SUCCESS" = "Green"
    "ERROR" = "Red"
    "WARNING" = "Yellow"
    "INFO" = "Cyan"
    "HEADER" = "Magenta"
}

# Utility functions
function Write-Status {
    param(
        [string]$Message,
        [string]$Status = "INFO"
    )
    $color = $Colors[$Status]
    Write-Host "[$Status]" -ForegroundColor $color -NoNewline
    Write-Host " $Message"
}

function Write-Header {
    param([string]$Title)
    Write-Host "`n" + ("=" * 60) -ForegroundColor $Colors["HEADER"]
    Write-Host "   $Title" -ForegroundColor $Colors["HEADER"]
    Write-Host ("=" * 60) -ForegroundColor $Colors["HEADER"]
}

function Write-Section {
    param([string]$Title)
    Write-Host "`n>>> $Title" -ForegroundColor $Colors["INFO"]
}

function Exit-WithError {
    param([string]$Message)
    Write-Status $Message "ERROR"
    exit 1
}

function Test-ExecutionPolicy {
    $policy = Get-ExecutionPolicy
    if ($policy -eq "Restricted") {
        Write-Status "Current execution policy is Restricted" "WARNING"
        Write-Host "   To fix, run:" -ForegroundColor $Colors["WARNING"]
        Write-Host "   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor $Colors["WARNING"]
        return $false
    }
    return $true
}

function Test-Python {
    try {
        $pythonVersion = python --version 2>&1
        return @{
            Installed = $true
            Version = $pythonVersion
        }
    } catch {
        return @{
            Installed = $false
            Version = $null
        }
    }
}

function Test-FFmpeg {
    try {
        $null = ffmpeg -version 2>&1
        return $true
    } catch {
        return $false
    }
}

function Download-FFmpeg {
    Write-Section "Downloading FFmpeg"
    
    $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.7z"
    $downloadPath = "$env:TEMP\ffmpeg.7z"
    $extractPath = "$env:TEMP\ffmpeg-build"
    
    try {
        Write-Status "Downloading FFmpeg from gyan.dev..." "INFO"
        
        # Download file
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($ffmpegUrl, $downloadPath)
        
        if (-not (Test-Path $downloadPath)) {
            Exit-WithError "FFmpeg download failed"
        }
        
        Write-Status "FFmpeg downloaded successfully" "SUCCESS"
        
        Write-Status "Extracting FFmpeg..." "INFO"
        
        # Check if 7z is available, otherwise try Windows built-in
        if (Get-Command 7z -ErrorAction SilentlyContinue) {
            7z x $downloadPath -o"$env:TEMP\"
        } else {
            # Use Windows built-in expand for zip or try PowerShell extraction
            Write-Status "Note: 7z not found, attempting PowerShell extraction..." "WARNING"
            # For .7z files, we'd need 7z or similar tool
            Exit-WithError "7z.exe not found. Please install 7-Zip or manually download FFmpeg from https://www.gyan.dev/ffmpeg/builds/"
        }
        
        # Find the FFmpeg directory
        $ffmpegDir = Get-ChildItem "$env:TEMP\" -Filter "ffmpeg*" -Directory | Select-Object -First 1
        
        if (-not $ffmpegDir) {
            Exit-WithError "Could not find extracted FFmpeg directory"
        }
        
        Write-Status "FFmpeg extracted to: $($ffmpegDir.FullName)" "SUCCESS"
        
        Write-Status "Moving FFmpeg to C:\ffmpeg..." "INFO"
        
        # Create C:\ffmpeg if it doesn't exist
        if (Test-Path "C:\ffmpeg") {
            Remove-Item -Path "C:\ffmpeg" -Recurse -Force
        }
        
        Move-Item -Path $ffmpegDir.FullName -Destination "C:\ffmpeg" -Force
        
        Write-Status "FFmpeg installed to C:\ffmpeg" "SUCCESS"
        
        # Clean up
        if (Test-Path $downloadPath) {
            Remove-Item -Path $downloadPath -Force
        }
        
        return $true
    } catch {
        Write-Status "FFmpeg download/installation failed: $_" "ERROR"
        return $false
    }
}

function Add-FFmpegToPath {
    Write-Section "Adding FFmpeg to PATH"
    
    $ffmpegBin = "C:\ffmpeg\bin"
    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    
    if ($currentPath -like "*$ffmpegBin*") {
        Write-Status "FFmpeg is already in PATH" "SUCCESS"
        return $true
    }
    
    try {
        Write-Status "Adding $ffmpegBin to PATH..." "INFO"
        
        $newPath = $currentPath + ";$ffmpegBin"
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        $env:PATH += ";$ffmpegBin"
        
        Write-Status "FFmpeg added to PATH successfully" "SUCCESS"
        Write-Status "Note: You may need to restart PowerShell/VS Code for PATH changes to take effect" "WARNING"
        
        return $true
    } catch {
        Write-Status "Failed to add FFmpeg to PATH: $_" "ERROR"
        Write-Status "You can manually add C:\ffmpeg\bin to your PATH environment variable" "WARNING"
        return $false
    }
}

function Create-VirtualEnvironment {
    Write-Section "Creating Python Virtual Environment"
    
    if (Test-Path ".venv") {
        if ($Force) {
            Write-Status "Removing existing virtual environment..." "WARNING"
            Remove-Item -Path ".venv" -Recurse -Force
        } else {
            Write-Status "Virtual environment already exists" "SUCCESS"
            return $true
        }
    }
    
    try {
        Write-Status "Creating .venv..." "INFO"
        python -m venv .venv
        
        if ($LASTEXITCODE -ne 0) {
            Exit-WithError "Failed to create virtual environment"
        }
        
        Write-Status "Virtual environment created successfully" "SUCCESS"
        return $true
    } catch {
        Exit-WithError "Error creating virtual environment: $_"
    }
}

function Activate-VirtualEnvironment {
    Write-Section "Activating Virtual Environment"
    
    $activateScript = ".\\.venv\\Scripts\\Activate.ps1"
    
    if (-not (Test-Path $activateScript)) {
        Exit-WithError "Virtual environment activation script not found"
    }
    
    try {
        Write-Status "Activating virtual environment..." "INFO"
        & $activateScript
        
        Write-Status "Virtual environment activated" "SUCCESS"
        return $true
    } catch {
        Exit-WithError "Failed to activate virtual environment: $_"
    }
}

function Install-Dependencies {
    Write-Section "Installing Python Dependencies"
    
    if (-not (Test-Path "backend\requirements.txt")) {
        Exit-WithError "requirements.txt not found in backend directory"
    }
    
    try {
        Write-Status "Installing packages from requirements.txt..." "INFO"
        pip install -r backend\requirements.txt --upgrade pip
        
        if ($LASTEXITCODE -ne 0) {
            Exit-WithError "Failed to install dependencies"
        }
        
        Write-Status "All dependencies installed successfully" "SUCCESS"
        return $true
    } catch {
        Exit-WithError "Error installing dependencies: $_"
    }
}

function Create-EnvFile {
    Write-Section "Setting Up Environment Configuration"
    
    $envFile = "backend\.env"
    
    if (Test-Path $envFile) {
        Write-Status "Environment file already exists at $envFile" "SUCCESS"
        
        if (-not $Force) {
            Write-Status "Skipping .env creation (use -Force to overwrite)" "WARNING"
            return $true
        }
    }
    
    try {
        Write-Status "Creating $envFile with placeholders..." "INFO"
        
        $envContent = @"
# Spotify Meta Downloader - Environment Configuration
# Created: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

# Spotify API Credentials
# Get these from: https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID=12eb015dc5a04c7abc9fb884e99ae193
SPOTIFY_CLIENT_SECRET=e906b5f83bee4ce88faabaa90c7cd81d

# Flask Environment (development or production)
FLASK_ENV=development
"@

        Set-Content -Path $envFile -Value $envContent -Encoding UTF8
        
        Write-Status ".env file created successfully" "SUCCESS"
        return $true
    } catch {
        Exit-WithError "Failed to create .env file: $_"
    }
}

function Print-FinalInstructions {
    Write-Header "Setup Complete! 🎉"
    
    Write-Host "`n" + "⚠️  IMPORTANT: UPDATE YOUR SPOTIFY CREDENTIALS" -ForegroundColor $Colors["WARNING"]
    Write-Host ("=" * 60) -ForegroundColor $Colors["WARNING"]
    
    Write-Host "`n1. Go to Spotify Developer Dashboard:" -ForegroundColor White
    Write-Host "   https://developer.spotify.com/dashboard" -ForegroundColor $Colors["INFO"]
    
    Write-Host "`n2. Create a new application"
    
    Write-Host "`n3. Copy your credentials (Client ID and Client Secret)"
    
    Write-Host "`n4. Edit: backend\.env" -ForegroundColor White
    Write-Host "   Replace the placeholder values with your actual credentials:" -ForegroundColor White
    
    Write-Host "`n   SPOTIFY_CLIENT_ID=your_actual_client_id" -ForegroundColor $Colors["INFO"]
    Write-Host "   SPOTIFY_CLIENT_SECRET=your_actual_client_secret" -ForegroundColor $Colors["INFO"]
    
    Write-Host "`n" + ("=" * 60)
    
    Write-Host "`n✅ Next Steps:" -ForegroundColor $Colors["SUCCESS"]
    Write-Host "`n1. Activate virtual environment (if not already active):" -ForegroundColor White
    Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor $Colors["INFO"]
    
    Write-Host "`n2. Update your Spotify credentials in backend\.env"
    
    Write-Host "`n3. Start the backend server:" -ForegroundColor White
    Write-Host "   python backend\app.py" -ForegroundColor $Colors["INFO"]
    
    Write-Host "`n4. Open in your browser:" -ForegroundColor White
    Write-Host "   http://localhost:5000" -ForegroundColor $Colors["INFO"]
    
    Write-Host "`n" + ("=" * 60)
    
    Write-Host "`n💡 Useful Commands:" -ForegroundColor $Colors["INFO"]
    Write-Host "   Activate venv:    .\.venv\Scripts\Activate.ps1" -ForegroundColor White
    Write-Host "   Deactivate venv:  deactivate" -ForegroundColor White
    Write-Host "   Update packages:  pip install --upgrade -r backend\requirements.txt" -ForegroundColor White
    
    Write-Host "`n" + ("=" * 60) + "`n"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

try {
    Write-Header "Spotify Meta Downloader - Setup"
    
    # Check execution policy
    if (-not (Test-ExecutionPolicy)) {
        Write-Host "`n"
        $response = Read-Host "Continue anyway? (y/n)"
        if ($response -ne "y") {
            exit 0
        }
    }
    
    # Step 1: Check Python
    Write-Section "Checking Python Installation"
    $pythonCheck = Test-Python
    
    if (-not $pythonCheck.Installed) {
        Exit-WithError "Python is not installed or not in PATH"
    }
    
    Write-Status "Python found: $($pythonCheck.Version)" "SUCCESS"
    
    # Step 2: Create virtual environment
    Create-VirtualEnvironment
    
    # Step 3: Activate virtual environment
    Activate-VirtualEnvironment
    
    # Step 4: Install dependencies
    Install-Dependencies
    
    # Step 5: Check/Install FFmpeg
    if (-not $SkipFFmpeg) {
        Write-Section "Checking FFmpeg Installation"
        
        if (Test-FFmpeg) {
            Write-Status "FFmpeg is already installed" "SUCCESS"
        } else {
            Write-Status "FFmpeg not found" "WARNING"
            Write-Status "Attempting automatic FFmpeg installation..." "INFO"
            
            $ffmpegInstalled = Download-FFmpeg
            
            if ($ffmpegInstalled) {
                Add-FFmpegToPath
                
                # Verify FFmpeg is now available
                if (Test-FFmpeg) {
                    Write-Status "FFmpeg verification: SUCCESS" "SUCCESS"
                } else {
                    Write-Status "FFmpeg installed but not yet in PATH. Restart PowerShell/VS Code and run setup again." "WARNING"
                }
            } else {
                Write-Status "Automatic FFmpeg installation failed." "WARNING"
                Write-Status "Please manually install from: https://www.gyan.dev/ffmpeg/builds/" "WARNING"
            }
        }
    } else {
        Write-Status "Skipping FFmpeg check (use -SkipFFmpeg)" "INFO"
    }
    
    # Step 6: Create .env file
    Create-EnvFile
    
    # Step 7: Print final instructions
    Print-FinalInstructions
    
} catch {
    Exit-WithError "Unexpected error: $_"
}
