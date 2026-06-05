#!/bin/bash
# Build a real, double-clickable EmbedMarkers.app bundle from the SwiftPM executable.
# Self-contained: pure-Swift embed (no python/exiftool). The cat is the app icon + the
# in-window logo. Output: app/dist/Shot Mark Embedder.app
set -e
cd "$(dirname "$0")"

APP_NAME="Shot Mark Embedder"
CAT="../branding/cat sticking tongue out.png"
DIST="dist"
APP="$DIST/$APP_NAME.app"

echo "▸ swift build -c release"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/EmbedMarkers"

echo "▸ assembling bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/EmbedMarkers"
cp "$CAT" "$APP/Contents/Resources/cat.png"      # in-window logo (Bundle.main)

echo "▸ making icon (squircle master with padding + rounded corners)"
MASTER="../branding/AppIcon_1024.png"
[ -f "$MASTER" ] || python3 make_icon.py
ICONSET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$MASTER" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$MASTER" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>com.lelanddutcher.shotmarkembedder</string>
  <key>CFBundleExecutable</key><string>EmbedMarkers</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.video</string>
</dict></plist>
PLIST

# ad-hoc codesign so Gatekeeper lets it launch locally
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "▸ built: $APP"
