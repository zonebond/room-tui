#!/usr/bin/env bash
# Build Room-branded pi from third_party/pi submodule only.
# Never use code.research/pi.
#
# Usage:
#   ./scripts/build-room-pi.sh
#   PI_ROOM_SRC=/path/to/pi ./scripts/build-room-pi.sh   # override (rare)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_SRC="$ROOT/third_party/pi"
PI_ROOM_SRC="${PI_ROOM_SRC:-$DEFAULT_SRC}"

# Hard refuse known shared research tree
case "$PI_ROOM_SRC" in
  */code.research/pi|*/code.research/pi/)
    echo "X Refusing PI_ROOM_SRC=$PI_ROOM_SRC" >&2
    echo "  Use room-tui/third_party/pi (git submodule)." >&2
    exit 1
    ;;
esac

if [[ ! -d "$PI_ROOM_SRC/packages/coding-agent" ]]; then
  echo "X Room pi source not found: $PI_ROOM_SRC" >&2
  echo "  Init submodule:" >&2
  echo "    git submodule update --init --recursive" >&2
  exit 1
fi

echo "-> apply Room pi brand (scheme B)"
python3 "$ROOT/scripts/apply-room-pi-brand.py" "$PI_ROOM_SRC"

# Cross-platform shell UTF-8 (macOS locale + Win10/11 PS multi-decode)
PATCH="$ROOT/scripts/patches/room-pi-utf8-shell.patch"
if [[ -f "$PATCH" ]]; then
  echo "-> apply Room pi UTF-8 shell patch (macOS + Win10/11)"
  if (cd "$PI_ROOM_SRC" && git apply --check "$PATCH" 2>/dev/null); then
    (cd "$PI_ROOM_SRC" && git apply "$PATCH")
    echo "   applied room-pi-utf8-shell.patch"
  elif (cd "$PI_ROOM_SRC" && git apply --reverse --check "$PATCH" 2>/dev/null); then
    echo "   already applied room-pi-utf8-shell.patch"
  else
    echo "! could not apply room-pi-utf8-shell.patch (source drift?); continuing" >&2
  fi
fi

python3 "$ROOT/scripts/verify-room-pi.py" --source "$PI_ROOM_SRC"

if ! command -v bun >/dev/null 2>&1; then
  echo "X bun not on PATH (required for build:binary)" >&2
  exit 1
fi

echo "========================================"
echo "  Room-branded pi build"
echo "  source: $PI_ROOM_SRC"
echo "========================================"

if [[ -f "$PI_ROOM_SRC/package.json" ]]; then
  echo "-> npm install (monorepo root)"
  (cd "$PI_ROOM_SRC" && npm install)
fi

CA="$PI_ROOM_SRC/packages/coding-agent"
echo "-> npm run build:binary"
(cd "$CA" && npm run build:binary)

OUT=""
for cand in "$CA/dist/pi" "$CA/dist/pi.exe"; do
  if [[ -f "$cand" ]]; then
    OUT="$cand"
    break
  fi
done
if [[ -z "$OUT" ]]; then
  echo "X build:binary did not produce dist/pi[.exe]" >&2
  exit 1
fi

mkdir -p "$ROOT/dist/bin"
STAGE_NAME="pi"
if [[ "$OUT" == *.exe ]]; then
  STAGE_NAME="pi.exe"
fi
cp -f "$OUT" "$ROOT/dist/bin/$STAGE_NAME"
if [[ -d "$CA/dist/theme" ]]; then
  rm -rf "$ROOT/dist/bin/theme"
  cp -R "$CA/dist/theme" "$ROOT/dist/bin/theme"
fi

{
  echo "brand=room"
  echo "configDir=.config/room-tui"
  echo "default_agent=~/.config/room-tui/agent"
  echo "env=ROOM_CODING_AGENT_DIR"
  echo "source=$PI_ROOM_SRC"
  echo "binary=$OUT"
  echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$ROOT/dist/bin/pi.ROOM.txt"

# Hard gate: stamp + binary must be Room-branded before packaging can use them
python3 "$ROOT/scripts/verify-room-pi.py" --dist "$ROOT/dist/bin" --binary "$ROOT/dist/bin/$STAGE_NAME" --repo "$ROOT"

echo ""
echo "OK Room pi: $ROOT/dist/bin/$STAGE_NAME"
echo "   default agent dir: ~/.config/room-tui/agent"
echo "   stamp: dist/bin/pi.ROOM.txt (verified)"
