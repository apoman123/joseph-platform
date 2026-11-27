#!/bin/sh
# TradeX installer for macOS and Linux (no admin needed).
#   curl -LsSf https://github.com/saifullahshafin/open-source-tradingview/releases/latest/download/install.sh | sh
# Options: TRADEX_DIR=~/TradeX  TRADEX_SOURCE=<folder or .zip>
# MetaTrader 5 charts are Windows-only for now; every free chart source works here.
set -eu
DIR="${TRADEX_DIR:-$HOME/TradeX}"
say() { printf '  > %s\n' "$1"; }
mkdir -p "$DIR/runtime/uv" "$DIR/runtime/tmp"
export TMPDIR="$DIR/runtime/tmp" UV_CACHE_DIR="$DIR/runtime/uv-cache" UV_PYTHON_INSTALL_DIR="$DIR/runtime/python" UV_TOOL_DIR="$DIR/runtime/uv-tools"
say "Installing TradeX into $DIR"

UV="$DIR/runtime/uv/uv"
if [ ! -x "$UV" ]; then
  say "Getting uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$DIR/runtime/uv" UV_NO_MODIFY_PATH=1 sh >/dev/null
fi

SRC="${TRADEX_SOURCE:-}"
if [ -z "$SRC" ]; then
  say "Downloading TradeX"
  SRC="$DIR/runtime/tmp/tradex.zip"
  curl -LsSf -o "$SRC" https://github.com/saifullahshafin/open-source-tradingview/archive/refs/heads/main.zip
fi
STAGE="$DIR/runtime/tmp/app"; rm -rf "$STAGE"; mkdir -p "$STAGE"
case "$SRC" in
  *.zip) unzip -q "$SRC" -d "$STAGE"; ROOT="$(find "$STAGE" -mindepth 1 -maxdepth 1 -type d | head -n 1)";;
  *) ROOT="$SRC";;
esac
# Copy app files; user data (data/), runtime, .venv and .env are kept.
( cd "$ROOT" && for f in * .[!.]*; do
    case "$f" in .git|.venv|data|runtime|mt5|.env|private|*.bak_*|'.[!.]*') continue;; esac   # private/ = owner-only extras
    [ -e "$f" ] && cp -R "$f" "$DIR/"
  done )
rm -rf "$STAGE"

say "Setting up (Python, packages, chart sources)"
if ! "$UV" run --no-project --python 3.12 python "$DIR/setup/tradex_setup.py" --all --no-mt5; then
  echo "Setup stopped. Fix the line marked ERR, then run this installer again (it continues where it stopped)."; exit 1
fi
say "Done. Start TradeX with: $DIR/tradex.sh"
