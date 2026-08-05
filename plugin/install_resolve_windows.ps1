# Install the Sony Shot Marker panel into DaVinci Resolve on Windows.
#
# Resolve loads Utility scripts from a single folder, so "installing" is one file copy.
# It shows up under Workspace > Scripts.
#
#   powershell -ExecutionPolicy Bypass -File install_resolve_windows.ps1
#   powershell -ExecutionPolicy Bypass -File install_resolve_windows.ps1 -Uninstall
#
# Resolve rescans the Scripts menu when you open it, so no restart is needed.
#
# Pure ASCII on purpose: a smart quote pasted into a script has broken a PowerShell parse
# in this repo before, and it fails in ways that are annoying to diagnose.
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here "resolve\SonyShotMarker.py"
$destDir = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
$dest = Join-Path $destDir "SonyShotMarker.py"
$legacy = Join-Path $destDir "Resolve_ApplyShotMarks.py"

if (-not (Test-Path $src)) {
    throw "$src not found"
}

if ($Uninstall) {
    if (Test-Path $dest) { Remove-Item -Force $dest }
    Write-Host "Removed SonyShotMarker from Resolve's Scripts menu."
    exit 0
}

if (-not (Test-Path $destDir)) {
    Write-Host "error: Resolve's script folder is not there:"
    Write-Host "  $destDir"
    throw "Open DaVinci Resolve once so it creates its support folders, then re-run."
}

Copy-Item -Path $src -Destination $dest -Force

# Deliberately leaves any existing Resolve_ApplyShotMarks.py alone. That script still
# works, some people have it wired into their own workflow, and an installer has no
# business deleting a script off someone's machine.
if (Test-Path $legacy) {
    Write-Host "note: the older Resolve_ApplyShotMarks.py is still installed and still works."
    Write-Host "      Both will appear in the Scripts menu. Delete it yourself if you'd"
    Write-Host "      rather only see one."
}

Write-Host ""
Write-Host "Installed Sony Shot Marker for DaVinci Resolve"
Write-Host ("  -> {0}" -f $dest)
Write-Host ""
Write-Host "In Resolve:  Workspace > Scripts > SonyShotMarker"
