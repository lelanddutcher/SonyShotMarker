$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "Shot Mark Embedder" `
  --add-data "tools;tools" `
  --add-data "branding;branding" `
  --collect-all tkinterdnd2 `
  windows\shot_mark_embedder_windows.py

$exe = Join-Path (Get-Location) "dist\Shot Mark Embedder\Shot Mark Embedder.exe"
if (!(Test-Path $exe)) {
  throw "missing expected executable: $exe"
}
Write-Host "built: $exe"
