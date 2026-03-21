# Spotify Meta Downloader - Quick Start Guide for Windows

Complete setup guide for Windows 10/11 with automated setup scripts.

## 🚀 Fastest Setup (5 minutes)

### 1. Prerequisites

- **Windows 10/11**
- **Python 3.10+** installed and in PATH
  - Download: https://www.python.org/downloads/
  - **Check the "Add Python to PATH" box during installation**
  - Verify: Open PowerShell and run `python --version`

### 2. Run Setup Script

Open PowerShell in the project directory and run:

```powershell
# For PowerShell users (Recommended)
.\setup.ps1
```

**OR** if you're using Command Prompt:

```cmd
setup.bat
```

The script will automatically:
- ✅ Create a Python virtual environment
- ✅ Install all dependencies (Flask, Spotipy, yt-dlp, etc.)
- ✅ Check for FFmpeg (attempt to install if missing)
- ✅ Create `.env` file with placeholders
- ✅ Print setup instructions

### 3. Configure Spotify Credentials

1. Go to: https://developer.spotify.com/dashboard
2. Create a Spotify account (free)
3. Create a new app
4. Copy your **Client ID** and **Client Secret**
5. Edit `backend\.env`:

```
SPOTIFY_CLIENT_ID=your_actual_client_id
SPOTIFY_CLIENT_SECRET=your_actual_client_secret
FLASK_ENV=development
```

### 4. Start the Application

```powershell
# If not already active, activate virtual environment
.\venv\Scripts\Activate.ps1

# Run the backend
python backend\app.py
```

You should see:
```
==================================================
Starting Spotify Meta Downloader
Environment: development
Debug: True
Server: 0.0.0.0:5000
==================================================
```

### 5. Open Frontend

Open your browser and go to:
```
http://localhost:5000
```

**Done!** 🎉 You can now paste Spotify URLs and download MP3s.

---

## 📋 Setup Script Options

### PowerShell Setup
```powershell
# Run with default settings
.\setup.ps1

# Skip FFmpeg check
.\setup.ps1 -SkipFFmpeg

# Force recreate virtual environment
.\setup.ps1 -Force
```

### Command Prompt Setup
```cmd
# Run setup
setup.bat

# Variables can be set before running
set PYTHON_PATH=python
setup.bat
```

---

## 🔧 Virtual Environment Management

### Activate Virtual Environment

**PowerShell:**
```powershell
.\.venv\Scripts\Activate.ps1
```

**Command Prompt:**
```cmd
.venv\Scripts\activate.bat
```

### Deactivate Virtual Environment

```powershell
deactivate
```

### Check Python in Virtual Environment

```powershell
python --version
pip list
```

---

## 📦 Install/Update Dependencies

```powershell
# Make sure virtual environment is activated
pip install --upgrade -r backend\requirements.txt
```

---

## 🐛 Troubleshooting

### PowerShell Execution Policy Error

```
File setup.ps1 cannot be loaded because running scripts is disabled
```

**Solution:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Python Not Found

```
'python' is not recognized as an internal or external command
```

**Solutions:**
1. Install Python from https://www.python.org
2. **During installation, check "Add Python to PATH"**
3. Restart PowerShell/Command Prompt after installation

### FFmpeg Not Found

```
ffmpeg: The term 'ffmpeg' is not recognized
```

**Solutions:**
1. Install using setup script: `.\setup.ps1`
2. Or install manually (see FFmpeg Installation section above)
3. Restart PowerShell after installation

### Port 5000 Already in Use

```
Address already in use
```

**Solutions:**
```powershell
# Find process using port 5000 and kill it
netstat -ano | findstr :5000
taskkill /PID <PID> /F

# Or use different port by editing backend\config.py
```

### Dependencies Installation Fails

```powershell
# Verify pip is updated
python -m pip install --upgrade pip

# Try installing again
pip install -r backend\requirements.txt
```

### Backend Won't Start

Make sure `.env` file exists and has correct credentials:
```powershell
# Check if file exists
Test-Path backend\.env

# View file content
Get-Content backend\.env
```

---

## 📁 Directory Structure

```
spotify-meta-downloader/
├── .venv/              # Python virtual environment
├── backend/
│   ├── app.py         # Flask app
│   ├── config.py      # Configuration
│   ├── spotify_service.py      # Spotify API
│   ├── downloader_service.py   # Download logic
│   ├── utils.py               # Utilities
│   ├── requirements.txt        # Dependencies
│   └── downloads/             # Downloaded MP3s
│
├── frontend/
│   ├── index.html             # UI
│   ├── styles.css             # Styling
│   └── app.js                 # JavaScript
│
├── setup.ps1                  # PowerShell setup
├── setup.bat                  # CMD setup
├── install-ffmpeg.bat         # FFmpeg installer
│
└── README.md                  # Full documentation
```

---

## ⚡ Quick Commands Reference

```powershell
# Setup
.\setup.ps1

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Deactivate
deactivate

# Run app
python backend\app.py

# Open browser
start http://localhost:5000

# Install packages
pip install -r backend\requirements.txt

# Check Python version
python --version

# Check FFmpeg
ffmpeg -version

# List installed packages
pip list

# Update specific package
pip install --upgrade spotipy
```

---

## 📞 Support & Help

1. **Check README.md** for detailed documentation
2. **Verify all prerequisites** are installed
3. **Check .env file** has correct credentials
4. **Check terminal output** for error messages
5. **Verify FFmpeg** is installed: `ffmpeg -version`

---

## 🎓 Next Steps After Setup

1. ✅ Run setup script
2. ✅ Add Spotify credentials to `.env`
3. ✅ Start backend: `python backend\app.py`
4. ✅ Open: http://localhost:5000
5. ✅ Paste Spotify track URL
6. ✅ Click "Fetch Metadata"
7. ✅ Click "Download as MP3"
8. ✅ Enjoy your downloaded music!

---

## 🔐 Security Notes

- **Never share your Spotify credentials publicly**
- **Keep `.env` file private** (add to `.gitignore`)
- **Downloaded content** is for personal use only
- **Respect copyright laws** in your jurisdiction

---

**Last Updated: March 2026**
