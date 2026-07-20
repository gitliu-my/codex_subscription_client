#!/bin/sh
set -eu

RUNTIME_DIR=${CSUB_RUNTIME_DIR:-"$HOME/.local/lib/csub"}
exec "$RUNTIME_DIR/csub" "$@"
