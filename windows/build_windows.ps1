$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# Build the PyInstaller args. Bundle WinSparkle.dll for auto-update IF it's present:
# download WinSparkle (https://winsparkle.org), drop WinSparkle.dll into windows\, and it
# ships next to the exe. Without it the app still runs — the "Check for Updates" button
# just won't appear (the appcast feed is shared with the macOS Sparkle build).
$pyArgs = @(
  "-m", "PyInstaller",
  "--noconfirm", "--clean", "--windowed",
  "--name", "Shot Mark Embedder",
  "--paths", "tools",
  "--add-data", "branding;branding",
  "--hidden-import", "embed_batch",
  "--hidden-import", "sony_shotmark",
  "--hidden-import", "run_log",
  "--collect-all", "tkinterdnd2"
)
if (Test-Path "windows\WinSparkle.dll") {
  $pyArgs += @("--add-binary", "windows\WinSparkle.dll;.")
  Write-Host "bundling WinSparkle.dll (auto-update enabled)"
} else {
  Write-Host "WinSparkle.dll not found in windows\ — building without auto-update"
}
$pyArgs += "windows\shot_mark_embedder_windows.py"

python @pyArgs

$exe = Join-Path (Get-Location) "dist\Shot Mark Embedder\Shot Mark Embedder.exe"
if (!(Test-Path $exe)) {
  throw "missing expected executable: $exe"
}
Write-Host "built: $exe"
