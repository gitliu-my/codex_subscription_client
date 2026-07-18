#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_VENV="$ROOT/.build-venv"
PYTHON=${PYTHON:-python3}

if command -v xcodebuild >/dev/null 2>&1 && ! xcodebuild -license check >/dev/null 2>&1; then
  printf '%s\n' \
    'macOS 尚未接受 Xcode License，无法生成独立应用。' \
    '请先在终端执行：sudo xcodebuild -license accept' >&2
  exit 69
fi

"$PYTHON" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --quiet --upgrade pip
"$BUILD_VENV/bin/python" -m pip install --quiet "$ROOT[build]"

rm -rf "$ROOT/build" "$ROOT/dist"

"$BUILD_VENV/bin/pyinstaller" \
  --noconfirm --clean --onefile \
  --name codex-subscription \
  --paths "$ROOT/src" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/cli" \
  --specpath "$ROOT/build" \
  "$ROOT/packaging/cli_entry.py"

"$BUILD_VENV/bin/pyinstaller" \
  --noconfirm --clean --windowed \
  --name "Codex Subscription" \
  --osx-bundle-identifier "com.gitliu.codex-subscription" \
  --paths "$ROOT/src" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/app" \
  --specpath "$ROOT/build" \
  "$ROOT/packaging/app_entry.py"

printf '\nBuilt:\n  %s\n  %s\n' \
  "$ROOT/dist/codex-subscription" \
  "$ROOT/dist/Codex Subscription.app"
