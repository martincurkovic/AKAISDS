#!/usr/bin/env bash
set -e

# build_linux.sh - builds the AKAISDS standalone Linux app
# Can be run from anywhere. Handles uv sync
# Requires build-essential installed first (see prerequisite note).

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"

if ! command -v gcc >/dev/null 2>&1 && ! command -v cc >/dev/null 2>&1; then
    echo "No C compiler found - install one first:"
    echo "  sudo apt update && sudo apt install build-essential"
    exit 1
fi

cd "$ROOT_DIR"
echo "Syncing environment with uv..."
uv sync
uv pip install pip

cd "$SRC_DIR"

# derive version from nearest git tag
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

    sed -i 's/^title = .*/title = AKAISDS/' pysidedeploy.spec
    sed -i 's|^icon = .*|icon = ../assets/icon/icon_512x512.png|' pysidedeploy.spec
    sed -i 's/^mode = .*/mode = standalone/' pysidedeploy.spec
    sed -i 's|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template --include-data-dir=ui/help=ui/help|' pysidedeploy.spec

    echo "Spec generated and configured."
fi

echo "Building AKAISDS..."
uv run pyside6-deploy -c pysidedeploy.spec

echo ""
echo "Done - check the output above for exactly where the build landed."
