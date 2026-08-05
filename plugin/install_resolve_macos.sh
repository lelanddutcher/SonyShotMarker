#!/usr/bin/env bash
# Install the Sony Shot Marker panel into DaVinci Resolve on macOS.
#
# Resolve loads Utility scripts from a single folder, so "installing" is one file copy.
# It shows up under Workspace > Scripts.
#
#   ./install_resolve_macos.sh
#   ./install_resolve_macos.sh --uninstall
#
# Resolve rescans the Scripts menu when you open it, so no restart is needed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/resolve/SonyShotMarker.py"
DEST_DIR="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
DEST="$DEST_DIR/SonyShotMarker.py"

if [[ ! -f "$SRC" ]]; then
  echo "error: $SRC not found" >&2
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$DEST"
  echo "Removed SonyShotMarker from Resolve's Scripts menu."
  exit 0
fi

if [[ ! -d "$DEST_DIR" ]]; then
  echo "error: Resolve's script folder is not there:" >&2
  echo "  $DEST_DIR" >&2
  echo "Open DaVinci Resolve once so it creates its support folders, then re-run." >&2
  exit 1
fi

cp "$SRC" "$DEST"

# Deliberately leaves any existing Resolve_ApplyShotMarks.py alone. That script still
# works, some people have it wired into their own workflow, and an installer has no
# business deleting a script off someone's machine.
if [[ -f "$DEST_DIR/Resolve_ApplyShotMarks.py" ]]; then
  echo "note: the older Resolve_ApplyShotMarks.py is still installed and still works."
  echo "      Both will appear in the Scripts menu. Delete it yourself if you'd rather"
  echo "      only see one."
fi

echo
echo "Installed Sony Shot Marker for DaVinci Resolve"
echo "  -> $DEST"
echo
echo "In Resolve:  Workspace > Scripts > SonyShotMarker"
