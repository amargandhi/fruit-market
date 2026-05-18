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

# Verify required bundle inputs before we touch anything. Failing
# here is cheap and the error messages are explicit; failing later
# (e.g. missing icon at runtime) produces a confused-looking app
# with a generic Finder icon that's hard to attribute back.
ICON_SRC="Resources/AppIcon.icns"
if [[ ! -f "$ICON_SRC" ]]; then
    cat >&2 <<EOF
FAIL: app icon missing at $ICON_SRC
       The .app bundle would build without an icon and Finder/Dock
       would show a generic placeholder. Regenerate it from the
       brand master with:
         python scripts/build_app_icon.py  (or restore from git)
EOF
    exit 1
fi

echo "→ assembling $APP_BUNDLE..."
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"
cp Info.plist "$APP_BUNDLE/Contents/Info.plist"
cp "$BIN" "$APP_BUNDLE/Contents/MacOS/fm-camera"
cp "$ICON_SRC" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
chmod +x "$APP_BUNDLE/Contents/MacOS/fm-camera"

# Sanity-check the bundle: every artefact the launcher needs has
# to be present and non-empty. Catches issues like a partial cp
# or a quotation-edge whitespace bug in the bundle name.
for f in "Info.plist" "MacOS/fm-camera" "Resources/AppIcon.icns"; do
    if [[ ! -s "$APP_BUNDLE/Contents/$f" ]]; then
        echo "FAIL: bundle artifact $APP_BUNDLE/Contents/$f is missing or empty" >&2
        exit 1
    fi
done

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

# Bust macOS's icon caches so Finder + Dock pick up the freshly
# built icon on the very next launch (no logout/reboot needed).
# Without this, a rebuild keeps showing the previous icon until
# the cache rolls over on its own.
touch "$APP_BUNDLE"
killall Dock 2>/dev/null || true
killall Finder 2>/dev/null || true

echo
echo "Built: $(pwd)/$APP_BUNDLE"
echo "Icon : $(stat -f%z "$APP_BUNDLE/Contents/Resources/AppIcon.icns") bytes"
echo
echo "First-run sanity check:"
echo "  ./$APP_BUNDLE/Contents/MacOS/fm-camera \"HD Pro Webcam C920\" /tmp/fruit-market-smoke.jpg"
echo
echo "macOS will prompt for camera access on the first call."
echo "Grant it; the permission sticks for future invocations."
