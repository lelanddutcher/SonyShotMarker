# Install the Sony Shot Marker panel into Premiere Pro on Windows.
#
# Copies the plugin into Premiere's side-load folder and registers it, so the panel shows
# up under Window > Extensions. No Creative Cloud account, no Marketplace, no signing.
#
#   powershell -ExecutionPolicy Bypass -File install_windows.ps1
#   powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Uninstall
#
# Quit Premiere first - it reads the plugin list at launch.
#
# Kept to pure ASCII on purpose: a smart quote from a copy-paste has broken a script in
# this repo before, and PowerShell fails on it in ways that are annoying to diagnose.
param(
    [switch]$Uninstall,
    [string]$Source
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Source) { $Source = Join-Path $here "premiere-uxp" }

$manifestPath = Join-Path $Source "manifest.json"
if (-not (Test-Path $manifestPath)) {
    throw "no manifest.json under $Source"
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$pluginId = $manifest.id
$version = $manifest.version
$name = $manifest.name

$uxpRoot = Join-Path $env:APPDATA "Adobe\UXP"
$pluginsDir = Join-Path $uxpRoot "Plugins\External"
$infoDir = Join-Path $uxpRoot "PluginsInfo\v1"
$infoFile = Join-Path $infoDir "PPRO.json"
$target = Join-Path $pluginsDir ("{0}_{1}" -f $pluginId, $version)

# Premiere also scans the External folder, but a missing or stale entry here is the usual
# reason a side-loaded panel never shows up.
function Set-Registration([bool]$present) {
    New-Item -ItemType Directory -Force -Path $infoDir | Out-Null

    $plugins = @()
    if (Test-Path $infoFile) {
        try {
            $existing = Get-Content $infoFile -Raw | ConvertFrom-Json
            if ($existing.plugins) {
                $plugins = @($existing.plugins | Where-Object { $_.pluginId -ne $pluginId })
            }
        } catch {
            Write-Host "note: could not parse $infoFile, rewriting it"
        }
    }

    if ($present) {
        $plugins += [pscustomobject]@{
            hostMinVersion = "25.6"
            name           = $name
            path           = ('$localPlugins/External/{0}_{1}' -f $pluginId, $version)
            pluginId       = $pluginId
            status         = "enabled"
            type           = "uxp"
            versionString  = $version
        }
    }

    # -Compress keeps it on one line, matching what the host itself writes.
    [pscustomobject]@{ plugins = $plugins } | ConvertTo-Json -Depth 5 -Compress |
        Set-Content -Path $infoFile -Encoding UTF8
}

if ($Uninstall) {
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    Set-Registration $false
    Write-Host "Removed $name. Restart Premiere Pro."
    exit 0
}

if (Get-Process -Name "Adobe Premiere Pro" -ErrorAction SilentlyContinue) {
    Write-Host "note: Premiere Pro is running. Quit and reopen it to pick up the panel."
}

New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force -Path $target | Out-Null

# Ship only what the panel needs at runtime.
foreach ($item in @("manifest.json", "index.html", "styles.css", "src", "icons")) {
    $from = Join-Path $Source $item
    if (Test-Path $from) {
        Copy-Item -Path $from -Destination $target -Recurse -Force
    }
}

Set-Registration $true

Write-Host ""
Write-Host ("Installed {0} {1}" -f $name, $version)
Write-Host ("  -> {0}" -f $target)
Write-Host ""
Write-Host "Open Premiere Pro and find it under  Window > Extensions > Sony Shot Marker"
