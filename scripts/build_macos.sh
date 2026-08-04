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
  pip install -r "#ROOT_DIR/requirements.txt"
fi

cd "$SRC_DIR"

if [ ! -f "pysidedeploy.spec" ]; then
  echo "No spec file found - generating from defaults"

  pyside6-deploy --init main.py

  sed -i '' 's/^title = .*/title = AKAISDS/' pysidedeploy.spec
  sed -i '' 's|^icon = .*|icon = ../assets/icon/AKAISDS-macos.icns|' pysidedeploy.spec
  sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec
  sed -i '' 's|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --macos-app-name=AKAISDS --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template|' pysidedeploy.spec

  echo "Spec generated and configured."
fi

echo "Building AKAISDS.app..."
pyside6-deploy -c pysidedeploy.spec

echo ""
echo "Done - check the output above for exactly where the .app landed."
