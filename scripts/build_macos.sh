#!/usr/bin/env bash
set -e

# build_macos.sh - builds the AKAISDS.app bundle
# Can be run from anywhere. Automatically handles venv setup if not configured.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"
VENV_DIR="$ROOT_DIR/.venv"

FRESH_VENV=false
if [ ! -d "$VENV_DIR" ]; then
  echo "No venv found - creating new venv..."
  python3 -m venv "$VENV_DIR"
  FRESH_VENV=true
fi

source "$VENV_DIR/bin/activate"

if [ "$FRESH_VENV" = true ]; then
  echo "Fresh venv - installing requirements..."
  pip install -r "$ROOT_DIR/requirements.txt"
fi

cd "$SRC_DIR"

# derive version from nearest git tag - falls back to dev placeholder if no tags exist yet
RAW_TAG=$(git -C "$ROOT_DIR" describe --tags --abbrev=0 2>/dev/null)
if [ -z "$RAW_TAG" ]; then
  VERSION="0.0.0-dev"
else
  VERSION=$(echo "$RAW_TAG" | sed 's/^v//')
fi
echo "Building version: $VERSION"
echo "APP_VERSION = \"$VERSION\"" >"$SRC_DIR/ui/_version.py"

if [ ! -f "pysidedeploy.spec" ]; then
  echo "No spec file found - generating from defaults"

  pyside6-deploy --init main.py

  sed -i '' 's/^title = .*/title = AKAISDS/' pysidedeploy.spec
  sed -i '' 's|^icon = .*|icon = ../assets/icon/AKAISDS-macos.icns|' pysidedeploy.spec
  sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec

  echo "Spec generated and configured."
fi

# re-generate extra_args on EVERY run, that way version number can update correctly
sed -i '' 's|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --macos-app-name=AKAISDS --macos-app-version=$VERSION --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template|' pysidedeploy.spec

echo "Building AKAISDS.app..."
pyside6-deploy -c pysidedeploy.spec

echo ""
echo "Done - check the output above for exactly where the .app landed."
