#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLI_SOURCE="$ROOT/dist/csub"
CLI_LAUNCHER_SOURCE="$ROOT/packaging/csub_launcher.sh"
CLI_BIN_DIR="$HOME/.local/bin"
CLI_RUNTIME_DIR="$HOME/.local/lib/csub"

if [ ! -x "$CLI_SOURCE/csub" ]; then
  "$ROOT/scripts/build_macos.sh"
fi

mkdir -p "$CLI_BIN_DIR" "$(dirname "$CLI_RUNTIME_DIR")"
rm -f "$CLI_BIN_DIR/codex-subscription"
rm -rf "$CLI_RUNTIME_DIR"
cp -R "$CLI_SOURCE" "$CLI_RUNTIME_DIR"
install -m 755 "$CLI_LAUNCHER_SOURCE" "$CLI_BIN_DIR/csub"
rm -rf "$HOME/Applications/Codex Subscription.app"

printf 'Installed:\n  %s\n  %s\n' \
  "$CLI_BIN_DIR/csub" \
  "$CLI_RUNTIME_DIR"
