# ═══════════════════════════════════════════════════════════════════
# Spotify Meta Downloader — start the backend automatically at login (ONE TIME)
# ═══════════════════════════════════════════════════════════════════
# Usage (from the project folder, in PowerShell):
#     .\scripts\install-autostart.ps1              install (and start it now)
#     .\scripts\install-autostart.ps1 -Remove      undo it
#
# It registers a Windows scheduled task "SpotifyMetaDownloader" that runs scripts\run-backend-background.ps1
# every time YOU log in to Windows. No administrator rights needed. If Windows refuses the scheduled task it
# falls back to a shortcut in your Startup folder, which does the same job.
# ═══════════════════════════════════════════════════════════════════
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$ROOT     = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script   = Join-Path $ROOT "scripts\run-backend-background.ps1"
$taskName = "SpotifyMetaDownloader"
$startup  = [Environment]::GetFolderPath("Startup")
$shortcut = Join-Path $startup "SpotifyMetaDownloader.lnk"
$psArgs   = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $shortcut -ErrorAction SilentlyContinue
    Write-Host "Removed. The backend will no longer start by itself (a copy that is running now keeps running)." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $script)) { throw "Cannot find $script - run this from the project folder." }

$installed = ""
try {
    $me      = "$env:USERDOMAIN\$env:USERNAME"
    $action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs -WorkingDirectory $ROOT
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Runs the Spotify Meta Downloader backend (playlist watcher + downloader) at login." `
        -Force | Out-Null
    $installed = "scheduled task '$taskName'"
    Remove-Item $shortcut -ErrorAction SilentlyContinue        # never both
    Start-ScheduledTask -TaskName $taskName
} catch {
    Write-Host "Windows would not create the scheduled task ($($_.Exception.Message)). Using the Startup folder instead." -ForegroundColor Yellow
    $wsh = New-Object -ComObject WScript.Shell
    $lnk = $wsh.CreateShortcut($shortcut)
    $lnk.TargetPath       = "powershell.exe"
    $lnk.Arguments        = $psArgs
    $lnk.WorkingDirectory = $ROOT
    $lnk.WindowStyle      = 7          # minimised / hidden
    $lnk.Save()
    $installed = "Startup-folder shortcut"
    Start-Process powershell.exe -ArgumentList $psArgs -WindowStyle Hidden
}

Write-Host ""
Write-Host "Done: $installed. The backend is starting now and will start by itself every time you log in." -ForegroundColor Green
Write-Host "Check on it any time:   .\scripts\backend-status.ps1"
Write-Host "Undo it:                .\scripts\install-autostart.ps1 -Remove"
