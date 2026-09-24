# ═══════════════════════════════════════════════════════════════════
# Spotify Meta Downloader — is the automation working?   (.\scripts\backend-status.ps1)
# ═══════════════════════════════════════════════════════════════════
# Reads only local files; makes no Spotify request.
# ═══════════════════════════════════════════════════════════════════
$ROOT    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$backend = Join-Path $ROOT "backend"

function Say($label, $value, $ok = $null) {
    $color = if ($ok -eq $true) { "Green" } elseif ($ok -eq $false) { "Yellow" } else { "Gray" }
    Write-Host ("{0,-26} {1}" -f $label, $value) -ForegroundColor $color
}

$port = 5000
$envFile = Join-Path $backend ".env"
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}

Write-Host "`nSpotify Meta Downloader - status`n" -ForegroundColor Cyan
$running = [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
Say "Backend running" $(if ($running) { "yes (port $port)" } else { "NO - nothing is watching your playlist" }) $running

$task = Get-ScheduledTask -TaskName "SpotifyMetaDownloader" -ErrorAction SilentlyContinue
$lnk  = Join-Path ([Environment]::GetFolderPath("Startup")) "SpotifyMetaDownloader.lnk"
$auto = if ($task) { "yes (scheduled task, $($task.State))" } elseif (Test-Path $lnk) { "yes (Startup shortcut)" } else { "NO - run .\scripts\install-autostart.ps1" }
Say "Starts at login" $auto ($auto -like "yes*")

$latest = Get-ChildItem (Join-Path $backend "logs") -Filter "backend_console*.log" -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) { Say "Backend output last written" ($latest.LastWriteTime.ToString("dd MMM HH:mm")) }

# the playlist watcher's own record
$watch = Join-Path $backend "ingest_watch_state.json"
if (Test-Path $watch) {
    $state = Get-Content $watch -Raw | ConvertFrom-Json
    foreach ($p in $state.PSObject.Properties) {
        $checked = [DateTimeOffset]::FromUnixTimeSeconds([int64]$p.Value.checked_at).LocalDateTime
        Say ("Playlist " + $p.Name.Substring(0, 6) + "...") ("{0} songs; last checked {1:dd MMM HH:mm}" -f $p.Value.total, $checked)
    }
} else {
    Say "Playlist watcher" "no check recorded yet (it records one after its first clean cycle)" $false
}

$rq = Join-Path $backend "ingest_requeue.json"
if (Test-Path $rq) {
    $n = @((Get-Content $rq -Raw | ConvertFrom-Json).tracks).Count
    Say "Waiting to re-download" "$n song(s)" ($n -eq 0)
}
$vf = Join-Path $backend "ingest_verify.json"
if (Test-Path $vf) {
    $v = Get-Content $vf -Raw | ConvertFrom-Json
    Say "Downloads awaiting check" (@($v.pending).Count)
    $gu = @($v.gave_up).Count
    Say "Gave up on (no file)" "$gu song(s)" ($gu -eq 0)
}
$gp = Join-Path $backend "genre_playlists.json"
if (Test-Path $gp) {
    foreach ($e in @((Get-Content $gp -Raw | ConvertFrom-Json).playlists)) { Say "Genre playlist" ("{0} -> {1}" -f $e.name, $e.crate) }
} else {
    Say "Genre playlists" "none yet (python manage_genre_playlists.py add House <your playlist link>)"
}

# songs the app could not place: they wait in the Electronic catch-all for you (or the hourly job)
try {
    $elec = $null
    if (Test-Path $envFile) {
        $b0 = Select-String -Path $envFile -Pattern '^\s*BASE_DOWNLOAD_DIR\s*=\s*(.+)$' | Select-Object -First 1
        if ($b0) { $elec = Join-Path $b0.Matches[0].Groups[1].Value.Trim().Trim('"') "Library\Electronic" }
    }
    if ($elec -and (Test-Path $elec)) {
        Say "Unsorted (Electronic)" ("{0} song(s) waiting for a folder" -f @(Get-ChildItem $elec -Filter *.mp3 -ErrorAction SilentlyContinue).Count)
    }
} catch { }

# proof of life: songs that really arrived on disk recently
try {
    $base = $null
    if (Test-Path $envFile) {
        $b = Select-String -Path $envFile -Pattern '^\s*BASE_DOWNLOAD_DIR\s*=\s*(.+)$' | Select-Object -First 1
        if ($b) { $base = $b.Matches[0].Groups[1].Value.Trim().Trim('"') }
    }
    if ($base -and (Test-Path $base)) {
        $recent = Get-ChildItem $base -Recurse -Filter *.mp3 -ErrorAction SilentlyContinue | Where-Object { $_.CreationTime -gt (Get-Date).AddHours(-24) }
        Say "New songs in last 24 h" (@($recent).Count)
    }
} catch { }
Write-Host ""
