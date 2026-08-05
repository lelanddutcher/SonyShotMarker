#!/bin/bash
# release.sh — build, Developer-ID sign, notarize, staple, zip, and upload the macOS app
# to a GitHub release. One command once you've done the one-time setup below.
#
# ── One-time setup (requires the PAID Apple Developer Program, $99/yr) ──
# 1. Create a "Developer ID Application" certificate (you currently only have an
#    "Apple Development" cert, which CANNOT notarize):
#       Xcode ▸ Settings ▸ Accounts ▸ (Apple ID) ▸ Manage Certificates ▸ + ▸
#       Developer ID Application
#    Confirm it's installed:   security find-identity -p codesigning -v | grep "Developer ID"
# 2. Make an app-specific password at appleid.apple.com (Sign-In & Security ▸ App-Specific
#    Passwords), then store notary credentials once:
#       xcrun notarytool store-credentials ShotMark-Notary \
#         --apple-id "dutcher.leland@gmail.com" --team-id PKUE74YS72
#
# ── Then, to release ──
#   scripts/release.sh v0.2.0
#
set -euo pipefail
cd "$(dirname "$0")/.."
# Require the version tag explicitly — defaulting it risks shipping a stale version whose
# Sparkle appcast item would read as OLDER than what users already have (a broken update).
TAG="${1:?usage: scripts/release.sh vX.Y.Z  (pass the version tag explicitly, e.g. v0.2.0)}"
IDENTITY="${SIGN_IDENTITY:-Developer ID Application}"
PROFILE="${NOTARY_PROFILE:-ShotMark-Notary}"
APP="app/dist/Shot Mark Embedder.app"
ZIP="Shot-Mark-Embedder-${TAG}.zip"

echo "▸ building the app"
( cd app && bash build_app.sh )

echo "▸ stripping extended attributes (codesign rejects resource forks / Finder info)"
xattr -cr "$APP"

echo "▸ codesigning (Developer ID, hardened runtime, secure timestamp) — Sparkle inside-out"
# --deep mis-signs Sparkle's nested code and breaks notarization; sign each piece, then the app.
SPK="$APP/Contents/Frameworks/Sparkle.framework/Versions/B"
for nested in \
  "$SPK/XPCServices/Downloader.xpc" \
  "$SPK/XPCServices/Installer.xpc" \
  "$SPK/Updater.app" \
  "$SPK/Autoupdate" ; do
  [ -e "$nested" ] && codesign --force --options runtime --timestamp --sign "$IDENTITY" "$nested"
done
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP/Contents/Frameworks/Sparkle.framework"
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP/Contents/MacOS/EmbedMarkers"
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP"
codesign --verify --strict --verbose=2 "$APP"

echo "▸ zipping for notarization"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "▸ submitting to Apple notary service (a few minutes)…"
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait

echo "▸ stapling the ticket + re-zipping"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "▸ uploading to GitHub release $TAG"
gh release upload "$TAG" "$ZIP" --clobber

# ── Premiere Pro panel: shipped alongside the app, and usable on its own ──
# The plugin is plain JavaScript with no Adobe signing step, so it just gets packaged and
# attached. Its version lives in plugin/premiere-uxp/manifest.json and is deliberately
# independent of the app's tag - the panel and the app ship on their own schedules.
echo "▸ packaging the Premiere Pro panel"
if python3 plugin/package_plugin.py --out dist/plugin; then
  PLUGIN_VERSION="$(python3 -c 'import json;print(json.load(open("plugin/premiere-uxp/manifest.json"))["version"])')"
  PLUGIN_CCX="dist/plugin/Sony-Shot-Marker-${PLUGIN_VERSION}.ccx"
  PLUGIN_ZIP="dist/plugin/Sony-Shot-Marker-${PLUGIN_VERSION}.zip"
  echo "▸ uploading the panel to release $TAG"
  gh release upload "$TAG" "$PLUGIN_CCX" "$PLUGIN_ZIP" --clobber
  echo "  panel $PLUGIN_VERSION attached ($(basename "$PLUGIN_CCX"), $(basename "$PLUGIN_ZIP"))"
else
  echo "  ⚠ panel packaging failed — the app release above is still fine; fix and re-upload."
fi

# ── DaVinci Resolve panel: the single script, plus a zip with its installers ──
echo "▸ uploading the Resolve panel"
gh release upload "$TAG" plugin/resolve/SonyShotMarker.py --clobber
RESOLVE_ZIP="dist/plugin/Sony-Shot-Marker-Resolve-${PLUGIN_VERSION:-1.0.0}.zip"
[ -f "$RESOLVE_ZIP" ] && gh release upload "$TAG" "$RESOLVE_ZIP" --clobber

# ── Sparkle auto-update feed: EdDSA-sign the zip + emit the appcast item ──
SPARKLE_BIN="app/.build/artifacts/sparkle/Sparkle/bin"
if [ -x "$SPARKLE_BIN/sign_update" ]; then
  echo "▸ EdDSA-signing the update for Sparkle (private key read from your Keychain)"
  ED_SIG_LINE="$("$SPARKLE_BIN/sign_update" "$ZIP")"   # → sparkle:edSignature="…" length="…"
  echo "  $ED_SIG_LINE"
  echo "▸ add this <item> under <channel> in appcast.xml, then commit + push (that publishes the update):"
  cat <<ITEM
    <item>
      <title>${TAG#v}</title>
      <sparkle:version>${TAG#v}</sparkle:version>
      <sparkle:shortVersionString>${TAG#v}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>13.0</sparkle:minimumSystemVersion>
      <pubDate>$(date -R 2>/dev/null || date)</pubDate>
      <enclosure url="https://github.com/lelanddutcher/SonyShotMarker/releases/download/${TAG}/${ZIP}"
                 $ED_SIG_LINE type="application/octet-stream"/>
    </item>
ITEM
  echo "  (Or let Sparkle's generate_appcast build appcast.xml from a folder of zips.)"
else
  echo "  ⚠ Sparkle sign_update not found — run 'cd app && swift build' to fetch Sparkle, then re-run."
fi

echo "✓ Done — notarized, stapled, and uploaded: $ZIP"
echo "  Users can now download, unzip, and open it with no Gatekeeper warning."
echo "  Auto-update goes live once the signed <item> above is committed to appcast.xml."
echo "  The Premiere panel and the Resolve script are attached to the same release, so"
echo "  someone who only wants the plugin never has to download the app."
