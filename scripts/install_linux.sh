#!/bin/sh
set -eu

REPOSITORY=${CSUB_REPOSITORY:-gitliu-my/codex_subscription_client}
ARCHIVE_NAME=csub-linux-x86_64.tar.gz
BIN_DIR=${CSUB_BIN_DIR:-"$HOME/.local/bin"}
RUNTIME_DIR=${CSUB_RUNTIME_DIR:-"$HOME/.local/lib/csub"}
LOCAL_ARCHIVE=${1:-}

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  printf '%s\n' 'csub Linux 安装包目前只支持 x86_64。' >&2
  exit 64
fi
case "$BIN_DIR:$RUNTIME_DIR" in
  "$HOME"/*:"$HOME"/*) ;;
  *)
    printf '%s\n' '安装目录必须位于当前用户 HOME 下。' >&2
    exit 64
    ;;
esac

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT HUP INT TERM
ARCHIVE="$TEMP_DIR/$ARCHIVE_NAME"

if [ -n "$LOCAL_ARCHIVE" ]; then
  cp "$LOCAL_ARCHIVE" "$ARCHIVE"
else
  BASE_URL="https://github.com/$REPOSITORY/releases/latest/download"
  printf '%s\n' '正在下载 csub Linux x86_64...'
  curl -fL --retry 3 --connect-timeout 15 \
    "$BASE_URL/$ARCHIVE_NAME" -o "$ARCHIVE"
  curl -fL --retry 3 --connect-timeout 15 \
    "$BASE_URL/SHA256SUMS" -o "$TEMP_DIR/SHA256SUMS"
  EXPECTED=$(awk -v name="$ARCHIVE_NAME" '$2 == name {print $1}' "$TEMP_DIR/SHA256SUMS")
  if [ -z "$EXPECTED" ]; then
    printf '%s\n' '发布校验文件中缺少 Linux 安装包。' >&2
    exit 65
  fi
  ACTUAL=$(sha256sum "$ARCHIVE" | awk '{print $1}')
  if [ "$ACTUAL" != "$EXPECTED" ]; then
    printf '%s\n' 'Linux 安装包 SHA256 校验失败。' >&2
    exit 65
  fi
fi

EXTRACTED="$TEMP_DIR/extracted"
mkdir -p "$EXTRACTED"
tar -xzf "$ARCHIVE" -C "$EXTRACTED"
if [ ! -x "$EXTRACTED/csub" ] || [ ! -d "$EXTRACTED/_internal" ]; then
  printf '%s\n' 'Linux 安装包结构无效。' >&2
  exit 66
fi

STAGING="$TEMP_DIR/runtime"
mkdir -p "$STAGING" "$BIN_DIR" "$(dirname "$RUNTIME_DIR")"
cp -R "$EXTRACTED/." "$STAGING/"
rm -rf "$RUNTIME_DIR"
mv "$STAGING" "$RUNTIME_DIR"

cat > "$TEMP_DIR/csub" <<'EOF'
#!/bin/sh
set -eu
RUNTIME_DIR=${CSUB_RUNTIME_DIR:-"$HOME/.local/lib/csub"}
exec "$RUNTIME_DIR/csub" "$@"
EOF
install -m 755 "$TEMP_DIR/csub" "$BIN_DIR/csub"

printf 'csub 已安装：\n  %s\n  %s\n' "$BIN_DIR/csub" "$RUNTIME_DIR"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    printf '\n请将下面一行加入 shell 配置后重新打开终端：\n'
    printf '  export PATH="%s:$PATH"\n' "$BIN_DIR"
    ;;
esac
