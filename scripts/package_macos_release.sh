#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE="$ROOT/dist/csub"
OUTPUT="$ROOT/release"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  printf '%s\n' '当前发布脚本只支持 macOS arm64。' >&2
  exit 64
fi
if [ ! -x "$SOURCE/csub" ] || [ ! -d "$SOURCE/_internal" ]; then
  printf '%s\n' '未找到 dist/csub，请先运行 ./scripts/build_macos.sh。' >&2
  exit 66
fi

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"
COPYFILE_DISABLE=1 tar -C "$SOURCE" -czf "$OUTPUT/csub-macos-arm64.tar.gz" .
(
  cd "$OUTPUT"
  shasum -a 256 csub-macos-arm64.tar.gz > SHA256SUMS
)

printf 'Packaged:\n  %s\n  %s\n' \
  "$OUTPUT/csub-macos-arm64.tar.gz" \
  "$OUTPUT/SHA256SUMS"
