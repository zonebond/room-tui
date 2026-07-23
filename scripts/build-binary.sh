#!/usr/bin/env bash
# Build product `room` single-file binary (PyInstaller onefile).
# Dev install is NOT this path - use scripts/install-global.sh for development.
#
# Usage:
#   ./scripts/build-binary.sh
#   ./scripts/build-binary.sh --clean
#
# --clean removes only room PyInstaller workdir + room/room-* outputs.
# It does NOT wipe dist/bin entirely (keeps paper-derived / pi / theme).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  darwin) OS="macos" ;;
  mingw*|msys*|cygwin*) OS="windows" ;;
esac

# Prefer the *binary* ISA (venv may be Rosetta x86_64 on Apple Silicon).
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="x86_64" ;;
esac
if command -v python3 >/dev/null 2>&1; then
  PY_ARCH="$(python3 -c 'import platform; print(platform.machine())' 2>/dev/null || true)"
  case "$PY_ARCH" in
    arm64|aarch64) ARCH="arm64" ;;
    x86_64|amd64)  ARCH="x86_64" ;;
  esac
fi

VERSION="$(
  python3 -c "import sys; sys.path.insert(0, 'src'); from room_tui import __version__; print(__version__)" \
    2>/dev/null || echo "0.1.0"
)"

OUT_DIR="$ROOT/dist/bin"
WORK_DIR="$ROOT/build/pyinstaller"
SPEC="$ROOT/packaging/room.spec"
NAME="room-${VERSION}-${OS}-${ARCH}"

CLEAN=0
[[ "${1:-}" == "--clean" || "${1:-}" == "-c" ]] && CLEAN=1

echo "======================================"
echo "  Room binary build"
echo "  version : $VERSION"
echo "  target  : ${OS}-${ARCH}"
echo "======================================"

if [[ $CLEAN -eq 1 ]]; then
  # Only room PyInstaller workdir + room outputs.
  # Keep paper-derived / pi / theme already staged in dist/bin.
  echo "-> cleaning room build artifacts (preserving sidecars in dist/bin)"
  rm -rf "$WORK_DIR"
  if [[ -d "$OUT_DIR" ]]; then
    shopt -s nullglob
    for f in "$OUT_DIR"/room "$OUT_DIR"/room.exe "$OUT_DIR"/room-* "$OUT_DIR"/room_tui "$OUT_DIR"/room_tui*; do
      [[ -e "$f" ]] || continue
      rm -rf "$f"
      echo "   removed $(basename "$f")"
    done
    shopt -u nullglob
  fi
fi

mkdir -p "$OUT_DIR" "$WORK_DIR"

# Prefer project venv; else create with uv; else system python + pip
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "-> creating .venv"
  if command -v uv >/dev/null 2>&1; then
    (cd "$ROOT" && uv venv .venv)
  else
    python3 -m venv "$ROOT/.venv"
  fi
fi
PY="$ROOT/.venv/bin/python"

echo "-> reinstall room-tui (editable) + pyinstaller  [fresh src/]"
if command -v uv >/dev/null 2>&1; then
  (cd "$ROOT" && uv pip install --reinstall -e ".[dev]" "pyinstaller>=6.0")
else
  "$PY" -m pip install --force-reinstall --no-deps -e . -q
  "$PY" -m pip install -e ".[dev]" "pyinstaller>=6.0" -q
fi
"$PY" -c "import room_tui, PyInstaller; print('room_tui', room_tui.__version__, room_tui.__file__)"

# Drop previous room outputs so a failed rebuild cannot leave yesterday's binary
rm -f "$OUT_DIR/room" "$OUT_DIR/room.exe" "$OUT_DIR/$NAME" "$OUT_DIR/${NAME}.exe" 2>/dev/null || true

echo "-> pyinstaller ($PY) --clean"
"$PY" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$OUT_DIR" \
  --workpath "$WORK_DIR" \
  "$SPEC"

RAW="$OUT_DIR/room"
if [[ "$OS" == mingw* || "$OS" == msys* || "$OS" == cygwin* || "$OS" == windows* ]]; then
  RAW="$OUT_DIR/room.exe"
fi
if [[ ! -f "$OUT_DIR/room" && -f "$OUT_DIR/room.exe" ]]; then
  RAW="$OUT_DIR/room.exe"
fi

if [[ ! -f "$RAW" ]]; then
  echo "X binary not found under $OUT_DIR" >&2
  ls -la "$OUT_DIR" >&2 || true
  exit 1
fi

# Platform-tagged copy for package-suite
TAGGED="$OUT_DIR/${NAME}${RAW##*room}"
# Keep plain name `room` for suite, also keep tagged archive name
cp "$RAW" "$OUT_DIR/$NAME" 2>/dev/null || cp "$RAW" "$OUT_DIR/${NAME}.exe"
chmod +x "$RAW" 2>/dev/null || true

GIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
BUILT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if command -v shasum >/dev/null 2>&1; then
  SHA="$(shasum -a 256 "$RAW" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
  SHA="$(sha256sum "$RAW" | awk '{print $1}')"
else
  SHA="unknown"
fi
{
  echo "room_version=$VERSION"
  echo "git=$GIT"
  echo "built=$BUILT"
  echo "sha256=$SHA"
  echo "size=$(wc -c <"$RAW" | tr -d ' ')"
  echo "path=$RAW"
} >"$OUT_DIR/room.BUILD.txt"

echo
echo "OK built: $RAW ($(du -h "$RAW" | cut -f1))"
echo "  sha256: $SHA"
echo "  built : $BUILT  git=$GIT"
echo "  stamp : $OUT_DIR/room.BUILD.txt"
echo "  tagged: $OUT_DIR/$NAME (or .exe)"
echo
echo "Next:"
echo "  1. Build or obtain same-platform paper-derived binary"
echo "  2. ./scripts/package-suite.sh --paper-derived /path/to/paper-derived"
echo "  3. Smoke-test: $RAW --version && $RAW doctor"
