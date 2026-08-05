#!/usr/bin/env bash
# Install the Sony Shot Marker panel into Premiere Pro on macOS.
#
# Copies the plugin into Premiere's side-load folder and registers it so the panel shows
# up under Window ▸ Extensions. No Creative Cloud account, no Marketplace, no signing.
#
#   ./install_macos.sh              # install from this checkout
#   ./install_macos.sh --uninstall  # remove it again
#
# Quit Premiere first — it reads the plugin list at launch.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SHOTMARK_PLUGIN_SRC:-$HERE/premiere-uxp}"
UXP_ROOT="$HOME/Library/Application Support/Adobe/UXP"
PLUGINS_DIR="$UXP_ROOT/Plugins/External"
INFO_DIR="$UXP_ROOT/PluginsInfo/v1"
INFO_FILE="$INFO_DIR/PPRO.json"

if [[ ! -f "$SRC/manifest.json" ]]; then
  echo "error: no manifest.json under $SRC" >&2
  exit 1
fi

read_manifest() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$SRC/manifest.json" "$1"; }
PLUGIN_ID="$(read_manifest id)"
VERSION="$(read_manifest version)"
NAME="$(read_manifest name)"
TARGET="$PLUGINS_DIR/${PLUGIN_ID}_${VERSION}"

# Registers the plugin in the host's plugin list. Premiere also scans the External folder,
# but a stale or missing entry here is the usual reason a side-loaded panel never appears.
register() {
  local present="$1"
  mkdir -p "$INFO_DIR"
  PLUGIN_ID="$PLUGIN_ID" VERSION="$VERSION" NAME="$NAME" PRESENT="$present" \
  INFO_FILE="$INFO_FILE" python3 - <<'PY'
import json, os

info_file = os.environ["INFO_FILE"]
plugin_id = os.environ["PLUGIN_ID"]
present = os.environ["PRESENT"] == "1"

try:
    with open(info_file) as fh:
        data = json.load(fh)
except (OSError, ValueError):
    data = {}
plugins = [p for p in data.get("plugins", []) if p.get("pluginId") != plugin_id]

if present:
    plugins.append({
        "hostMinVersion": "25.6",
        "name": os.environ["NAME"],
        "path": "$localPlugins/External/%s_%s" % (plugin_id, os.environ["VERSION"]),
        "pluginId": plugin_id,
        "status": "enabled",
        "type": "uxp",
        "versionString": os.environ["VERSION"],
    })

data["plugins"] = plugins
with open(info_file, "w") as fh:
    json.dump(data, fh, indent=None)
print(("registered" if present else "unregistered") + " " + plugin_id)
PY
}

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$TARGET"
  register 0
  echo "Removed $NAME. Restart Premiere Pro."
  exit 0
fi

if pgrep -qx "Adobe Premiere Pro 2026" 2>/dev/null || pgrep -qf "Adobe Premiere Pro" >/dev/null 2>&1; then
  echo "note: Premiere Pro is running. Quit and reopen it to pick up the panel."
fi

mkdir -p "$PLUGINS_DIR"
rm -rf "$TARGET"
mkdir -p "$TARGET"

# Ship only what the panel needs at runtime.
( cd "$SRC" && tar cf - \
    manifest.json index.html styles.css src icons \
  ) | ( cd "$TARGET" && tar xf - )

register 1

echo
echo "Installed $NAME $VERSION"
echo "  -> $TARGET"
echo
echo "Open Premiere Pro and find it under  Window ▸ Extensions ▸ Sony Shot Marker"
