#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE="$ROOT/dist/csub"
OUTPUT="$ROOT/release"
ARCHIVE="$OUTPUT/csub-linux-x86_64.tar.gz"

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  printf '%s\n' '当前发布脚本只支持 Linux x86_64。' >&2
  exit 64
fi
if [ ! -x "$SOURCE/csub" ] || [ ! -d "$SOURCE/_internal" ]; then
  printf '%s\n' '未找到 dist/csub，请先运行 ./scripts/build_linux.sh。' >&2
  exit 66
fi

mkdir -p "$OUTPUT"
tar -C "$SOURCE" -czf "$ARCHIVE" .
(
  cd "$OUTPUT"
  sha256sum "$(basename "$ARCHIVE")" > SHA256SUMS.linux
)

printf 'Packaged:\n  %s\n  %s\n' \
  "$ARCHIVE" \
  "$OUTPUT/SHA256SUMS.linux"
