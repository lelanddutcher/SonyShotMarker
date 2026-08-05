#!/usr/bin/env bash
# Mirror the plugin into a UXP Developer Tool workspace.
#
# The repo is the single source of truth: this copies plugin/premiere-uxp into the folder
# UDT has registered, so "Load and Debug" runs the real plugin (with a devtools console)
# and "Package" produces the .ccx you upload to Adobe Developer Distribution.
#
#   ./sync_to_udt.sh                       # default workspace, see below
#   ./sync_to_udt.sh /path/to/workspace    # somewhere else
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/premiere-uxp"
DEST="${1:-$HERE/../../UXP Plugin/SonyShotMarker}"

if [[ ! -f "$SRC/manifest.json" ]]; then
  echo "error: no manifest.json under $SRC" >&2
  exit 1
fi
if [[ ! -d "$DEST" ]]; then
  echo "error: workspace not found: $DEST" >&2
  echo "       pass the path to your UDT plugin folder as the first argument." >&2
  exit 1
fi

# Replace only what the plugin owns; leave anything else in the workspace alone
# (UDT keeps its own bookkeeping in there).
rm -rf "$DEST/src" "$DEST/icons"
rm -f "$DEST/index.html" "$DEST/styles.css" "$DEST/style.css" "$DEST/manifest.json" "$DEST/main.js"

( cd "$SRC" && tar cf - manifest.json index.html styles.css src icons ) | ( cd "$DEST" && tar xf - )

VERSION="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$SRC/manifest.json")"
echo "Synced Sony Shot Marker $VERSION"
echo "  from $SRC"
echo "  to   $DEST"
echo
echo "In the UXP Developer Tool: click ••• ▸ Reload, or Load & Debug for a console."
