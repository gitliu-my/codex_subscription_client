#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_VENV="$ROOT/.build-venv"
PYTHON=${PYTHON:-python3}

"$PYTHON" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --quiet --upgrade pip
"$BUILD_VENV/bin/python" -m pip install --quiet "$ROOT[build]"

rm -rf "$ROOT/build" "$ROOT/dist"

"$BUILD_VENV/bin/pyinstaller" \
  --noconfirm --clean --onedir \
  --name csub \
  --paths "$ROOT/src" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/cli" \
  --specpath "$ROOT/build" \
  "$ROOT/packaging/cli_entry.py"

printf '\nBuilt:\n  %s\n' "$ROOT/dist/csub/csub"
