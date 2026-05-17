#!/usr/bin/env bash
# Build fm-camera as a code-signed .app bundle.
#
# The .app gets its own TCC (privacy) entry the first time it's
# launched, so Python can subprocess-call its binary to snapshot
# without inheriting the parent process's camera sandbox.
#
# Run from anywhere; the script cd's into its own directory.

set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="FruitMarketCamera"
APP_BUNDLE="${APP_NAME}.app"
BUILD_CONFIG="release"

echo "→ swift build ($BUILD_CONFIG)..."
swift build --configuration "$BUILD_CONFIG"

BIN="$(swift build --configuration "$BUILD_CONFIG" --show-bin-path)/fm-camera"
if [[ ! -x "$BIN" ]]; then
    echo "FAIL: built binary not found at $BIN" >&2
    exit 1
fi

echo "→ assembling $APP_BUNDLE..."
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"
cp Info.plist "$APP_BUNDLE/Contents/Info.plist"
cp "$BIN" "$APP_BUNDLE/Contents/MacOS/fm-camera"
chmod +x "$APP_BUNDLE/Contents/MacOS/fm-camera"

# Ad-hoc sign so macOS treats this as a stable identity for TCC.
# Unsigned binaries get a different (and unstable) responsible-app
# identity that can drop permission grants between runs.
# Ad-hoc sign WITHOUT hardened runtime. Hardened runtime requires
# entitlements (e.g. com.apple.security.device.camera) which we
# can't declare with ad-hoc signing — Apple's tooling rejects them.
# Without hardened runtime, ad-hoc sign is enough to give the .app
# a stable TCC identity.
echo "→ ad-hoc codesigning..."
codesign --force --sign - "$APP_BUNDLE"

echo
echo "Built: $(pwd)/$APP_BUNDLE"
echo
echo "First-run sanity check:"
echo "  ./$APP_BUNDLE/Contents/MacOS/fm-camera \"HD Pro Webcam C920\" /tmp/fruit-market-smoke.jpg"
echo
echo "macOS will prompt for camera access on the first call."
echo "Grant it; the permission sticks for future invocations."
