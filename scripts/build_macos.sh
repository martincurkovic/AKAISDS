#!/usr/bin/env bash
set -e

# build_macos.sh - builds the AKAISDS.app bundle
# Can be run from anywhere. Automatically handles venv setup if not configured.

cd "$(dirname "$0")/../src"

FRESH_VENV=false
if [ ! -d ".venv" ]; then
  echo "No venv found - creating new venv..."
  python3 -m venv .venv
  FRESH_VENV=true
fi

source .venv/bin/activate

if [ "$FRESH_VENV" = true ]; then
  echo "Fresh venv - installing requirements..."
  pip install -r ../requirements.txt
fi

if [ ! -f "pysidedeploy.spec" ]; then
  echo "No spec file found - generating from defaults..."

  pyside6-deploy --init main.py

  sed -i '' 's/^title = .*/title = AKAISDS/' pysidedeploy.spec
  sed -i '' 's|^icon = .*|icon = ../assets/icon/AKAISDS-macos.icns|' pysidedeploy.spec
  sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec
  sed -i '' 's|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template --macos-app-name=AKAISDS|' pysidedeploy.spec

  echo "Spec generated and configured."
fi

echo "Building AKAISDS.app..."
pyside6-deploy -c pysidedeploy.spec

echo ""
echo "Done - check the output above for exactly where the .app landed."
