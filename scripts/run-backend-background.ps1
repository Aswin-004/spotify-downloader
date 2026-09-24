# ═══════════════════════════════════════════════════════════════════
# Spotify Meta Downloader — run the backend quietly in the background
# ═══════════════════════════════════════════════════════════════════
# This is what starts at login (see install-autostart.ps1). It runs the same `python app.py` as
# start.bat, but with no window, one copy only, and it starts the backend again if it ever stops.
# The playlist watcher lives inside the backend, so a running backend IS the automation: it notices new
# songs, downloads them and files them.
#
# Output goes to backend\logs\:  backend_console.log (normal output), backend_console.err.log (the app writes
# most of its log lines here), backend_launcher.log (when it started / stopped).
#
# You normally never run this by hand. To run it now:   .\scripts\run-backend-background.ps1
# ═══════════════════════════════════════════════════════════════════

$ROOT    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$backend = Join-Path $ROOT "backend"
$py      = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$logDir = Join-Path $backend "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$launcherLog = Join-Path $logDir "backend_launcher.log"
$out         = Join-Path $logDir "backend_console.log"
$err         = Join-Path $logDir "backend_console.err.log"

function Note($text) { Add-Content $launcherLog ("[{0}] {1}" -f (Get-Date -Format s), $text) }

# the backend port (PORT in backend\.env, default 5000)
$port = 5000
$envFile = Join-Path $backend ".env"
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}

# only one copy: if something already answers on the port, do nothing
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Note "backend already running on port $port - nothing to do"
    exit 0
}

while ($true) {
    $started = Get-Date
    Note "starting backend (port $port)"
    foreach ($f in $out, $err) { if (Test-Path $f) { Move-Item $f "$f.old" -Force -ErrorAction SilentlyContinue } }   # keep the previous run
    # Start-Process (not `& python ... *>>`): Windows PowerShell 5.1 turns a program's error stream into errors
    $p = Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $backend `
            -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -Wait -PassThru
    $seconds = ((Get-Date) - $started).TotalSeconds
    Note ("backend stopped (exit code {0}) after {1:n0} s" -f $p.ExitCode, $seconds)
    # a crash straight after starting means something is broken: wait longer instead of spinning
    if ($seconds -lt 60) { Start-Sleep -Seconds 300 } else { Start-Sleep -Seconds 15 }
}
