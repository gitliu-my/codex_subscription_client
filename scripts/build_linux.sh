#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_VENV="$ROOT/.build-venv"
PYTHON=${PYTHON:-python3}

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  printf '%s\n' '当前构建脚本只支持 Linux x86_64。' >&2
  exit 64
fi

if "$PYTHON" -m venv "$BUILD_VENV" >/dev/null 2>&1 \
  && "$BUILD_VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  BUILD_PYTHON="$BUILD_VENV/bin/python"
  "$BUILD_PYTHON" -m pip install --quiet --upgrade pip
  "$BUILD_PYTHON" -m pip install --quiet "$ROOT[build]"
else
  TARGET="$BUILD_VENV/target"
  rm -rf "$TARGET"
  mkdir -p "$TARGET"
  "$PYTHON" -m pip install --quiet --upgrade --target "$TARGET" \
    "$ROOT" "pyinstaller>=6.0" "certifi>=2024.8.30"
  BUILD_PYTHON="$PYTHON"
  export PYTHONPATH="$TARGET${PYTHONPATH:+:$PYTHONPATH}"
fi

rm -rf "$ROOT/build" "$ROOT/dist"

"$BUILD_PYTHON" -m PyInstaller \
  --noconfirm --clean --onedir \
  --name csub \
  --paths "$ROOT/src" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/cli" \
  --specpath "$ROOT/build" \
  "$ROOT/packaging/cli_entry.py"

printf '\nBuilt:\n  %s\n' "$ROOT/dist/csub/csub"
