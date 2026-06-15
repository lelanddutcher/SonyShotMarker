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
BINDIR="$(swift build -c release --show-bin-path)"
BIN="$BINDIR/EmbedMarkers"

echo "▸ assembling bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/EmbedMarkers"
cp "../branding/AppIcon_1024.png" "$APP/Contents/Resources/cat.png"   # in-window logo = the green app-icon tile

echo "▸ bundling Sparkle.framework (auto-update)"
mkdir -p "$APP/Contents/Frameworks"
cp -R "$BINDIR/Sparkle.framework" "$APP/Contents/Frameworks/"
# the SPM exe links @rpath/Sparkle.framework — point @rpath at the bundled Frameworks dir
install_name_tool -add_rpath "@executable_path/../Frameworks" "$APP/Contents/MacOS/EmbedMarkers" 2>/dev/null || true

echo "▸ making icon (squircle master with padding + rounded corners)"
MASTER="../branding/AppIcon_1024.png"
# Use the committed/hand-edited icon as-is — Leland hand-crafts AppIcon_1024.png (there's a
# matching .psd). Only auto-generate from the cat PNG if the master is missing entirely, so
# the build never clobbers a hand-edited icon.
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
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.video</string>
  <key>SUFeedURL</key><string>https://raw.githubusercontent.com/lelanddutcher/SonyShotMarker/main/appcast.xml</string>
  <key>SUPublicEDKey</key><string>/YW+9b1o9nmwtEFTaUxMCCTZliWp0wmgMmxTwp0Zg/Y=</string>
  <key>SUEnableAutomaticChecks</key><true/>
</dict></plist>
PLIST

# strip extended attributes (codesign rejects resource forks / Finder info), then ad-hoc sign.
# Sign Sparkle's nested code inside-out, then the app (no --deep — it mis-signs Sparkle's XPC).
xattr -cr "$APP" 2>/dev/null || true
SPK="$APP/Contents/Frameworks/Sparkle.framework/Versions/B"
for nested in \
  "$SPK/XPCServices/Downloader.xpc" \
  "$SPK/XPCServices/Installer.xpc" \
  "$SPK/Updater.app" \
  "$SPK/Autoupdate" ; do
  [ -e "$nested" ] && codesign --force --sign - "$nested" >/dev/null 2>&1 || true
done
codesign --force --sign - "$APP/Contents/Frameworks/Sparkle.framework" >/dev/null 2>&1 || true
codesign --force --sign - "$APP/Contents/MacOS/EmbedMarkers" >/dev/null 2>&1 || true
codesign --force --sign - "$APP" >/dev/null 2>&1 || true
echo "▸ built: $APP"
