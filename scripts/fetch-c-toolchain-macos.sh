#!/usr/bin/env bash
# Download a portable macOS clang toolchain for oob-divzero ASan.
# Output: vendor/c-toolchain-macos-<arch>/bin/clang  (not committed to git)
#
# Default: official LLVM GitHub prebuilt (large). Override with TOOLCHAIN_URL=.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="x86_64" ;;
esac

DEST="$ROOT/vendor/c-toolchain-macos-$ARCH"
FORCE="${FORCE:-0}"
LLVM_VER="${LLVM_VER:-18.1.8}"

if [[ -x "$DEST/bin/clang" && "$FORCE" != "1" ]]; then
  echo "Already present: $DEST/bin/clang (FORCE=1 to re-download)"
  exit 0
fi

# Official LLVM prebuilts (names vary by release - adjust LLVM_VER if 404)
if [[ "$ARCH" == "arm64" ]]; then
  DEFAULT_URL="https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM_VER}/clang+llvm-${LLVM_VER}-arm64-apple-darwin22.0.tar.xz"
else
  DEFAULT_URL="https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM_VER}/clang+llvm-${LLVM_VER}-x86_64-apple-darwin22.0.tar.xz"
fi
URL="${TOOLCHAIN_URL:-$DEFAULT_URL}"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/room-c-toolchain.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

echo "-> download $URL"
if command -v curl >/dev/null 2>&1; then
  curl -fL --progress-bar -o "$TMP/tc.tar.xz" "$URL"
else
  wget -O "$TMP/tc.tar.xz" "$URL"
fi

echo "-> extract"
mkdir -p "$TMP/out"
tar -xJf "$TMP/tc.tar.xz" -C "$TMP/out"
INNER="$(find "$TMP/out" -type f -path '*/bin/clang' | head -1 | xargs dirname | xargs dirname)"
if [[ -z "$INNER" || ! -x "$INNER/bin/clang" ]]; then
  echo "X archive has no bin/clang" >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -R "$INNER" "$DEST"
cat >"$DEST/ROOM-FETCH.txt" <<EOF
source=$URL
llvm=$LLVM_VER
arch=$ARCH
fetched=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
echo "OK $DEST/bin/clang"
