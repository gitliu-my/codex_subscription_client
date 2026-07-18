#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLI_SOURCE="$ROOT/dist/codex-subscription"
APP_SOURCE="$ROOT/dist/Codex Subscription.app"
CLI_DIR="$HOME/.local/bin"
APP_DIR="$HOME/Applications"

if [ ! -x "$CLI_SOURCE" ] || [ ! -d "$APP_SOURCE" ]; then
  "$ROOT/scripts/build_macos.sh"
fi

mkdir -p "$CLI_DIR" "$APP_DIR"
install -m 755 "$CLI_SOURCE" "$CLI_DIR/codex-subscription"
rm -rf "$APP_DIR/Codex Subscription.app"
cp -R "$APP_SOURCE" "$APP_DIR/Codex Subscription.app"

printf 'Installed:\n  %s\n  %s\n' \
  "$CLI_DIR/codex-subscription" \
  "$APP_DIR/Codex Subscription.app"
