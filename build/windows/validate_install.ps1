# =====================================================================
# U One - install + validation script for Windows 10/11.
#
# Run from the project root (the folder containing build/, installer/):
#   powershell -ExecutionPolicy Bypass -File build\windows\validate_install.ps1
#
# What it does:
#   1. Installs Output\U_One_Setup.exe silently (if not already installed)
#   2. Verifies install dir, U One.exe, bundled ffmpeg.exe/ffprobe.exe
#   3. Verifies Start Menu shortcut + Add/Remove Programs entry
#   4. Launches U One from the shortcut, confirms the process runs
#   5. Runs "U One.exe --selftest" (full pipeline: script+voice -> MP4,
#      16:9 and 9:16, hostile paths) and reports PASS/FAIL
#   6. Optionally uninstalls silently and verifies removal
#
# The remaining manual checks (GUI render, Videos-folder copy, Pexels/
# Pixabay keys) are listed at the end.
# =====================================================================
$ErrorActionPreference = "Stop"

$Root      = Split-Path (Split-Path (Split-Path $MyInvocation.MyCommand.Path -Parent) -Parent) -Parent
$SetupExe  = Join-Path $Root "Output\U_One_Setup.exe"
$InstallDir= Join-Path $env:LOCALAPPDATA "U One"
$AppExe    = Join-Path $InstallDir "U One.exe"
$AppId     = "{8F3B2A41-7C6E-4D9B-9E2F-1A5C8D3B7E01}"
$UninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
$SelfTestReport = Join-Path $env:LOCALAPPDATA "U One\selftest\report.txt"

function Check($Name, $Ok, $Detail = "") {
    $mark = if ($Ok) { "PASS" } else { "FAIL" }
    Write-Host "[$mark] $Name $Detail"
    return $Ok
}
$allOk = $true

Write-Host ""
Write-Host "== U One Windows validation ==" -ForegroundColor Cyan

# --- 1. install (silent, per-user, no admin) ---
if (-not (Test-Path $AppExe)) {
    if (-not (Test-Path $SetupExe)) {
        Write-Host "[FAIL] Installer not found: $SetupExe" -ForegroundColor Red
        Write-Host "Run build\windows\build.bat first (needs Inno Setup 6 for the installer,"
        Write-Host "or validate the portable build: Output\U_One_Portable.zip)."
        exit 1
    }
    Write-Host "Installing U_One_Setup.exe silently..."
    Start-Process -FilePath $SetupExe -ArgumentList "/SILENT" -Wait
}
$allOk = (Check "U One.exe installed (per-user, no admin)" (Test-Path $AppExe) $AppExe) -and $allOk

# --- 2. bundled FFmpeg ---
$allOk = (Check "bundled ffmpeg.exe present" (Test-Path (Join-Path $InstallDir "ffmpeg\ffmpeg.exe"))) -and $allOk
$allOk = (Check "bundled ffprobe.exe present" (Test-Path (Join-Path $InstallDir "ffmpeg\ffprobe.exe"))) -and $allOk

# --- 3. shortcuts + uninstaller registration ---
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\U One\U One.lnk"
$allOk = (Check "Start Menu shortcut" (Test-Path $startMenu) $startMenu) -and $allOk
$allOk = (Check "Add/Remove Programs entry" (Test-Path $UninstKey)) -and $allOk

# --- 4. launch from shortcut, confirm process runs ---
Write-Host "Launching U One..."
$proc = Start-Process -FilePath $AppExe -PassThru
Start-Sleep -Seconds 12
$running = -not $proc.HasExited
$allOk = (Check "U One launches and stays running" $running "PID $($proc.Id)") -and $allOk
if ($running) { Stop-Process -Id $proc.Id -Force; Start-Sleep -Seconds 2 }

# --- 5. headless end-to-end self test (no Python/pip needed by user) ---
Write-Host ""
Write-Host "Running end-to-end self test (script+voice -> MP4, 16:9 + 9:16)..."
Write-Host "This takes a few minutes - the app validates its own bundled FFmpeg."
if (Test-Path $SelfTestReport) { Remove-Item $SelfTestReport -Force }
$st = Start-Process -FilePath $AppExe -ArgumentList "--selftest" -Wait -PassThru
if (Test-Path $SelfTestReport) {
    $report = Get-Content $SelfTestReport -Raw
    Write-Host ""
    Write-Host $report
    $selfOk = ($report -match "SELFTEST PASSED") -or ($report -match "\*\*(\d+)/(\d+) passed\.\*\*" -and $Matches[1] -eq $Matches[2])
    $allOk = (Check "selftest: full pipeline on this PC" $selfOk) -and $allOk
} else {
    $allOk = (Check "selftest: report produced" $false "(exit code $($st.ExitCode))") -and $allOk
}

# --- 5b. playback check: open the rendered MP4 in the default player ---
$mp4 = Get-ChildItem (Join-Path $env:LOCALAPPDATA "U One\selftest\work dir (1)\w169") `
    -Filter *.mp4 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($mp4) {
    Write-Host ""
    Write-Host "Test render: $($mp4.FullName)"
    $open = Read-Host "Open it in your default player to confirm playback? (y/N)"
    if ($open -eq "y" -or $open -eq "Y") { Start-Process $mp4.FullName }
}

# --- 6. optional silent uninstall check ---
$answer = Read-Host "`nUninstall U One now to verify the uninstaller? (y/N)"
if ($answer -eq "y" -or $answer -eq "Y") {
    $uninst = (Get-ItemProperty $UninstKey).UninstallString
    Write-Host "Uninstalling..."
    Start-Process -FilePath ($uninst -replace '^(.*\.exe).*','$1') -ArgumentList "/SILENT" -Wait
    Start-Sleep -Seconds 3
    $gone = (-not (Test-Path $AppExe)) -and (-not (Test-Path $UninstKey))
    $allOk = (Check "uninstaller removes app + registry entry" $gone) -and $allOk
}

Write-Host ""
if ($allOk) { Write-Host "ALL AUTOMATED CHECKS PASSED" -ForegroundColor Green }
else         { Write-Host "SOME CHECKS FAILED - see above" -ForegroundColor Red }

Write-Host ""
Write-Host "Manual checks still recommended in the GUI:" -ForegroundColor Cyan
Write-Host "  1. Launch U One from the Start Menu."
Write-Host "  2. Settings -> API Settings: paste Pexels/Pixabay keys -> Save -> Test Connection."
Write-Host "  3. Paste a script, upload a voiceover (or Generate AI voice), press CREATE VIDEO."
Write-Host "  4. Confirm the finished MP4 plays and a copy appears in your Videos folder"
Write-Host "     as 'U One <project> <timestamp>.mp4'."
Write-Host "  5. Try a voiceover from a path with spaces, e.g."
Write-Host "     C:\Users\Gillani Computers\Downloads\my vid (1)\voice.wav"
