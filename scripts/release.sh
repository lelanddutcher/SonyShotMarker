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
#         --apple-id "dutcher.leland@gmail.com" --team-id KG6926BEBK
#
# ── Then, to release ──
#   scripts/release.sh v0.1.0
#
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-v0.1.0}"
IDENTITY="${SIGN_IDENTITY:-Developer ID Application}"
PROFILE="${NOTARY_PROFILE:-ShotMark-Notary}"
APP="app/dist/Shot Mark Embedder.app"
ZIP="Shot-Mark-Embedder-${TAG}.zip"

echo "▸ building the app"
( cd app && bash build_app.sh )

echo "▸ codesigning (Developer ID, hardened runtime, secure timestamp)"
codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$APP"
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

echo "✓ Done — notarized, stapled, and uploaded: $ZIP"
echo "  Users can now download, unzip, and open it with no Gatekeeper warning."
