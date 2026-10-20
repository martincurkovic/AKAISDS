#!/usr/bin/env bash
set -e

# build_macos.sh - builds the AKAISDS.app bundle
# Can be run from anywhere. Handles uv sync

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"

cd "$ROOT_DIR"
echo "Syncing environment with uv..."
uv sync
uv pip install pip

# the REX2 reader (third_party/velociloops) is a small C++ library, compiled for THIS machine's architecture into src/core/native
uv run python "$ROOT_DIR/tools/build_velociloops.py"

cd "$SRC_DIR"

# derive version from nearest git tag - falls back to dev placeholder if no tags exist yet
RAW_TAG=$(git -C "$ROOT_DIR" describe --tags --abbrev=0 2>/dev/null || true)
if [ -z "$RAW_TAG" ]; then
    VERSION="0.0.0-dev"
else
    VERSION=$(echo "$RAW_TAG" | sed 's/^v//')
fi
echo "Building version: $VERSION"
echo "APP_VERSION = \"$VERSION\"" >"$SRC_DIR/ui/_version.py"

if [ ! -f "pysidedeploy.spec" ]; then
    echo "No spec file found - generating from defaults"

    uv run pyside6-deploy --init main.py

    sed -i '' 's/^title = .*/title = AKAISDS/' pysidedeploy.spec
    sed -i '' 's|^icon = .*|icon = ../assets/icon/AKAISDS-macos.icns|' pysidedeploy.spec
    sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec

    echo "Spec generated and configured."
fi

# re-generate extra_args on EVERY run, that way version number can update correctly
sed -i '' "s|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --macos-app-name=AKAISDS --macos-app-version=$VERSION --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template --include-data-dir=ui/help=ui/help --include-package-data=certifi --include-data-files=core/native/libvelociloops.dylib=core/native/libvelociloops.dylib|" pysidedeploy.spec

echo "Building AKAISDS.app..."
uv run pyside6-deploy -c pysidedeploy.spec

# Nuitka has no option for a minimum macOS version, so set it in Info.plist: on anything older macOS then says the app can't be
# opened with this version instead of letting it crash. Nuitka's ad-hoc signature seals Info.plist, so sign again the way Nuitka does.
MIN_MACOS="14.0"
plutil -replace LSMinimumSystemVersion -string "$MIN_MACOS" AKAISDS.app/Contents/Info.plist
/usr/bin/codesign -s - --force --deep --preserve-metadata=entitlements AKAISDS.app

# The DMG: dmgbuild (a dev dependency, pure Python) writes the window layout directly - no Finder/AppleScript, so it works on a
# headless CI runner. It opens as a window with AKAISDS.app, an Applications shortcut and a background image with an arrow and
# "Drag AKAISDS to the Applications folder". Layout: scripts/dmg_settings.py; background: assets/dmg/dmg_background.tiff
# (regenerate with assets/dmg/create_dmg_background.py). The name is what the workflows upload and the Homebrew cask downloads.
case "$(uname -m)" in
arm64) DMG_ARCH="arm" ;;
*) DMG_ARCH="intel" ;;
esac
DMG_NAME="AKAISDS-macos-$DMG_ARCH.dmg"
echo "Creating $DMG_NAME..."
rm -f "$ROOT_DIR/$DMG_NAME"
uv run dmgbuild \
    -s "$ROOT_DIR/scripts/dmg_settings.py" \
    -D app="AKAISDS.app" \
    -D background="$ROOT_DIR/assets/dmg/dmg_background.tiff" \
    "AKAISDS" "$ROOT_DIR/$DMG_NAME"

echo ""
echo "Done - check the output above for exactly where the .app landed."
echo "DMG: $ROOT_DIR/$DMG_NAME"
