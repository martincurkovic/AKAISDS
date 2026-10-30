#!/usr/bin/env bash
set -e

# build_macos.sh - builds the AKAISDS.app bundle and its DMG
# Can be run from anywhere. Handles uv sync
#
# AKAISDS_FLAVOR=legacy builds the macOS 12 variant (Qt 6.8, see dev_docs/macos12-support-handoff.md): its own environment (.venv-legacy, so the
# ordinary .venv is left alone), wheels picked for macOS 12 (the older NumPy), VelociLoops and Nuitka compiled for 12, and AKAISDS-macos-{arm,intel}-legacy.dmg.
# Anything else (the default) is the current build, unchanged. Check a result with scripts/check_minos.sh.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"

case "$(uname -m)" in
arm64) DMG_ARCH="arm"; UV_PLATFORM="aarch64-apple-darwin" ;;
*) DMG_ARCH="intel"; UV_PLATFORM="x86_64-apple-darwin" ;;
esac

FLAVOR="${AKAISDS_FLAVOR:-current}"
case "$FLAVOR" in
current)
    MIN_MACOS="14.0"
    DMG_SUFFIX=""
    VENV_DIR="$ROOT_DIR/.venv"
    ;;
legacy)
    MIN_MACOS="12.0"
    DMG_SUFFIX="-legacy"
    VENV_DIR="$ROOT_DIR/.venv-legacy"
    # uv's --python-platform reads this, and picks wheels for that macOS (NumPy's macosx_11_0 / 10_13 instead of its macosx_14_0); clang
    # and Nuitka's C compiler read it too, so what we compile targets 12 as well
    export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"
    export UV_PROJECT_ENVIRONMENT="$VENV_DIR"
    ;;
*)
    echo "AKAISDS_FLAVOR must be 'current' or 'legacy', not '$FLAVOR'" >&2
    exit 1
    ;;
esac
echo "Flavor: $FLAVOR (macOS $MIN_MACOS and later)"

cd "$ROOT_DIR"
echo "Syncing environment with uv..."
if [ "$FLAVOR" = "legacy" ]; then
    uv sync --no-group qt-current --group qt-legacy --python-platform "$UV_PLATFORM"
    # --no-sync from here on: a plain `uv run` would sync again, back to the default groups and wheels
    UV_RUN="uv run --no-sync"
else
    uv sync
    UV_RUN="uv run"
fi
uv pip install --python "$VENV_DIR/bin/python" pip

# the REX2 reader (third_party/velociloops) is a small C++ library, compiled for THIS machine's architecture into src/core/native
$UV_RUN python "$ROOT_DIR/tools/build_velociloops.py"

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

    $UV_RUN pyside6-deploy --init main.py

    sed -i '' 's/^title = .*/title = AKAISDS/' pysidedeploy.spec
    sed -i '' 's|^icon = .*|icon = ../assets/icon/AKAISDS-macos.icns|' pysidedeploy.spec
    sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec

    echo "Spec generated and configured."
fi

# the spec keeps the interpreter it was generated with, so point it at this flavor's environment on every run
sed -i '' "s|^python_path = .*|python_path = $VENV_DIR/bin/python|" pysidedeploy.spec

# re-generate extra_args on EVERY run, that way version number can update correctly
sed -i '' "s|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --macos-app-name=AKAISDS --macos-app-version=$VERSION --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template --include-data-files=ui/style_system6.qss.template=ui/style_system6.qss.template --include-data-dir=ui/fonts=ui/fonts --include-data-dir=ui/help=ui/help --include-package-data=certifi --include-data-files=core/native/libvelociloops.dylib=core/native/libvelociloops.dylib|" pysidedeploy.spec

echo "Building AKAISDS.app..."
$UV_RUN pyside6-deploy -c pysidedeploy.spec

# Nuitka has no option for a minimum macOS version, so set it in Info.plist: on anything older macOS then says the app can't be
# opened with this version instead of letting it crash. Nuitka's ad-hoc signature seals Info.plist, so sign again the way Nuitka does.
plutil -replace LSMinimumSystemVersion -string "$MIN_MACOS" AKAISDS.app/Contents/Info.plist
/usr/bin/codesign -s - --force --deep --preserve-metadata=entitlements AKAISDS.app

# The DMG: dmgbuild (a dev dependency, pure Python) writes the window layout directly - no Finder/AppleScript, so it works on a
# headless CI runner. It opens as a window with AKAISDS.app, an Applications shortcut and a background image with an arrow and
# "Drag AKAISDS to the Applications folder". Layout: scripts/dmg_settings.py; background: assets/dmg/dmg_background.tiff
# (regenerate with assets/dmg/create_dmg_background.py). The name is what the workflows upload and the Homebrew cask downloads.
DMG_NAME="AKAISDS-macos-$DMG_ARCH$DMG_SUFFIX.dmg"
echo "Creating $DMG_NAME..."
rm -f "$ROOT_DIR/$DMG_NAME"
$UV_RUN dmgbuild \
    -s "$ROOT_DIR/scripts/dmg_settings.py" \
    -D app="AKAISDS.app" \
    -D background="$ROOT_DIR/assets/dmg/dmg_background.tiff" \
    "AKAISDS" "$ROOT_DIR/$DMG_NAME"

echo ""
echo "Done - check the output above for exactly where the .app landed."
echo "DMG: $ROOT_DIR/$DMG_NAME"
