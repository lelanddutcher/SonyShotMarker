# Plugins

Two panels, one job: get your Sony Shot Marks out of the camera metadata and into your NLE
as native markers, without touching your footage.

| | |
|---|---|
| [`premiere-uxp/`](premiere-uxp/) | Premiere Pro panel (UXP). Needs Premiere 25.6+. |
| [`resolve/`](resolve/) | DaVinci Resolve panel (Fusion UIManager script). |

Both read the same metadata with the same parser logic and land markers on the same frames.
Neither writes to your media — files are opened read-only and left byte-for-byte identical.

If you'd rather bake the marks into a *copy* so they travel with the file into any NLE with
nothing installed, that's the [Shot Mark Embedder app](../README.md) instead.

## Install

```bash
./install_premiere_macos.sh
./install_resolve_macos.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install_premiere_windows.ps1
powershell -ExecutionPolicy Bypass -File .\install_resolve_windows.ps1
```

Each takes `--uninstall` / `-Uninstall`. Quit Premiere before installing its panel — it
reads the plugin list at launch. Resolve rescans its Scripts menu on open, so it needs no
restart.

## Packaging a release

```bash
python3 package_plugin.py
```

Writes to `dist/plugin/`:

- `Sony-Shot-Marker-<version>.ccx` — the Premiere plugin package
- `Sony-Shot-Marker-<version>.zip` — the same payload plus the Premiere installers
- `Sony-Shot-Marker-Resolve-<version>.zip` — the Resolve script plus its installers

The Premiere archives are byte-reproducible from the same source, so a release can be
verified by hash. `scripts/release.sh` builds all of them and attaches them to the GitHub
release alongside the macOS app.

For an **Adobe Developer Distribution** submission, use the UXP Developer Tool's *Package*
button rather than the `.ccx` above. It emits `7e04f014_premierepro.ccx` — the same payload
under the name Adobe's validator expects.

## Development

`sync_to_udt.sh` mirrors `premiere-uxp/` into a UXP Developer Tool workspace so "Load and
Debug" runs the real plugin with a console:

```bash
./sync_to_udt.sh "/path/to/your/UDT workspace/SonyShotMarker"
```

The repo is the source of truth; the workspace is a mirror. Don't edit the workspace copy.
