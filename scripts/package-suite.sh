#!/usr/bin/env bash
# Assemble product suite zip: room + paper-derived + one-click installers.
#
# Usage:
#   ./scripts/package-suite.sh --paper-derived /path/to/paper-derived --pi /path/to/pi
#   ./scripts/package-suite.sh --paper-derived ... --pi ... --room dist/bin/room
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# macOS + Windows script encoding gate (ASCII punctuation; .ps1 UTF-8 BOM)
if command -v python3 >/dev/null 2>&1; then
  python3 "$ROOT/scripts/check-script-encoding.py" || {
    echo "Fix with: python3 scripts/check-script-encoding.py --fix" >&2
    exit 1
  }
fi

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  mingw*|msys*|cygwin*) OS="windows" ;;
  darwin) OS="macos" ;;
  linux) OS="linux" ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="x86_64" ;;
esac

VERSION="$(
  python3 -c "import sys; sys.path.insert(0, 'src'); from room_tui import __version__; print(__version__)" \
    2>/dev/null || echo "0.1.0"
)"

ROOM_BIN=""
PD_BIN=""
PI_BIN_FILE=""
OOB_BIN=""
C_TOOLCHAIN=""
OUT_ROOT="$ROOT/dist/suite"
ALLOW_NO_PI=0
ALLOW_NO_OOB=0
REQUIRE_C_TOOLCHAIN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --paper-derived|-p)
      PD_BIN="$2"; shift 2 ;;
    --pi)
      PI_BIN_FILE="$2"; shift 2 ;;
    --oob-divzero|--oob)
      OOB_BIN="$2"; shift 2 ;;
    --c-toolchain)
      C_TOOLCHAIN="$2"; shift 2 ;;
    --room|-r)
      ROOM_BIN="$2"; shift 2 ;;
    --version)
      VERSION="$2"; shift 2 ;;
    --out)
      OUT_ROOT="$2"; shift 2 ;;
    --allow-no-pi)
      ALLOW_NO_PI=1; shift ;;
    --allow-no-oob)
      ALLOW_NO_OOB=1; shift ;;
    --require-c-toolchain)
      REQUIRE_C_TOOLCHAIN=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1 ;;
  esac
done

# Default: rebuild room so suite never ships a stale binary.
# Pass ROOM_SKIP_BUILD=1 to reuse dist/bin/room (not recommended).
if [[ "${ROOM_SKIP_BUILD:-0}" != "1" && -z "$ROOM_BIN" ]]; then
  echo "-> rebuilding room (set ROOM_SKIP_BUILD=1 to skip)"
  "$ROOT/scripts/build-binary.sh"
fi

if [[ -z "$ROOM_BIN" ]]; then
  if [[ -x "$ROOT/dist/bin/room" ]]; then
    ROOM_BIN="$ROOT/dist/bin/room"
  elif [[ -f "$ROOT/dist/bin/room.exe" ]]; then
    ROOM_BIN="$ROOT/dist/bin/room.exe"
  else
    echo "X room binary not found. Run ./scripts/build-binary.sh first, or pass --room" >&2
    exit 1
  fi
fi

if [[ -z "$PD_BIN" || ! -f "$PD_BIN" ]]; then
  echo "X Need --paper-derived /path/to/paper-derived binary" >&2
  echo "  Example: --paper-derived ../paper-derived/build/paper-derived-0.1.0-darwin-arm64" >&2
  exit 1
fi

if [[ -z "$PI_BIN_FILE" || ! -f "$PI_BIN_FILE" ]]; then
  if [[ $ALLOW_NO_PI -eq 1 ]]; then
    echo "! packing without pi (users will need pi on PATH)" >&2
    PI_BIN_FILE=""
  else
    echo "X Need --pi /path/to/pi binary (bun compile of coding-agent)" >&2
    echo "  From pi monorepo: cd packages/coding-agent && npm run build:binary" >&2
    echo "  Then: --pi .../packages/coding-agent/dist/pi" >&2
    echo "  Or pass --allow-no-pi for engine-only suite (not recommended)" >&2
    exit 1
  fi
fi

if [[ ! -f "$ROOM_BIN" ]]; then
  echo "X room binary not found: $ROOM_BIN" >&2
  exit 1
fi

# Prefer ISA of the actual room binary (Rosetta builds are x86_64 on arm64 Macs).
if command -v file >/dev/null 2>&1; then
  ft="$(file -b "$ROOM_BIN" 2>/dev/null || true)"
  if echo "$ft" | grep -q 'arm64\|aarch64'; then
    ARCH="arm64"
  elif echo "$ft" | grep -q 'x86_64\|x86-64\|Intel 64'; then
    ARCH="x86_64"
  fi
fi

STAGE_NAME="room-suite-${VERSION}-${OS}-${ARCH}"
STAGE="$OUT_ROOT/$STAGE_NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE/bin"

echo "======================================"
echo "  Room suite package"
echo "  version : $VERSION"
echo "  target  : ${OS}-${ARCH}"
echo "  room    : $ROOM_BIN"
echo "  engine  : $PD_BIN"
echo "  pi      : ${PI_BIN_FILE:-"(none)"}"
echo "======================================"

# Binaries
# shellcheck source=lib/copy-pi-runtime.sh
source "$ROOT/scripts/lib/copy-pi-runtime.sh"

if [[ -n "$PI_BIN_FILE" ]]; then
  echo "-> verify Room-branded pi"
  python3 "$ROOT/scripts/verify-room-pi.py" --binary "$PI_BIN_FILE" --repo "$ROOT"
fi

if [[ "$OS" == "windows" ]]; then
  cp "$ROOM_BIN" "$STAGE/bin/room.exe"
  cp "$PD_BIN" "$STAGE/bin/paper-derived.exe"
  chmod +x "$STAGE/bin/room.exe" "$STAGE/bin/paper-derived.exe" 2>/dev/null || true
  if [[ -n "$PI_BIN_FILE" ]]; then
    cp "$PI_BIN_FILE" "$STAGE/bin/pi.exe"
    chmod +x "$STAGE/bin/pi.exe" 2>/dev/null || true
    echo "-> pi runtime assets (theme/ etc.)"
    copy_pi_runtime "$PI_BIN_FILE" "$STAGE/bin"
    # brand stamp must ship with suite
    if [[ -f "$(dirname "$PI_BIN_FILE")/pi.ROOM.txt" ]]; then
      cp -f "$(dirname "$PI_BIN_FILE")/pi.ROOM.txt" "$STAGE/bin/pi.ROOM.txt"
    elif [[ -f "$ROOT/dist/bin/pi.ROOM.txt" ]]; then
      cp -f "$ROOT/dist/bin/pi.ROOM.txt" "$STAGE/bin/pi.ROOM.txt"
    else
      echo "X missing pi.ROOM.txt for suite" >&2
      exit 1
    fi
  fi
else
  cp "$ROOM_BIN" "$STAGE/bin/room"
  cp "$PD_BIN" "$STAGE/bin/paper-derived"
  chmod +x "$STAGE/bin/room" "$STAGE/bin/paper-derived"
  if [[ -n "$PI_BIN_FILE" ]]; then
    cp "$PI_BIN_FILE" "$STAGE/bin/pi"
    chmod +x "$STAGE/bin/pi"
    echo "-> pi runtime assets (theme/ etc.)"
    copy_pi_runtime "$PI_BIN_FILE" "$STAGE/bin"
    if [[ -f "$(dirname "$PI_BIN_FILE")/pi.ROOM.txt" ]]; then
      cp -f "$(dirname "$PI_BIN_FILE")/pi.ROOM.txt" "$STAGE/bin/pi.ROOM.txt"
    elif [[ -f "$ROOT/dist/bin/pi.ROOM.txt" ]]; then
      cp -f "$ROOT/dist/bin/pi.ROOM.txt" "$STAGE/bin/pi.ROOM.txt"
    else
      echo "X missing pi.ROOM.txt for suite" >&2
      exit 1
    fi
  fi
fi

# oob-divzero capability CLI
if [[ -z "$OOB_BIN" ]]; then
  for cand in \
    "$ROOT/dist/bin/oob-divzero" \
    "$ROOT/dist/bin/oob-divzero.exe" \
    "$ROOT/../oob-divzero/dist/oob-divzero" \
    "$(command -v oob-divzero 2>/dev/null || true)"
  do
    [[ -n "$cand" && -f "$cand" ]] || continue
    OOB_BIN="$cand"
    break
  done
fi
if [[ -n "$OOB_BIN" && -f "$OOB_BIN" ]]; then
  if [[ "$OS" == "windows" ]]; then
    cp "$OOB_BIN" "$STAGE/bin/oob-divzero.exe"
  else
    cp "$OOB_BIN" "$STAGE/bin/oob-divzero"
    chmod +x "$STAGE/bin/oob-divzero"
  fi
  echo "  oob-divzero <- $OOB_BIN"
elif [[ $ALLOW_NO_OOB -eq 1 ]]; then
  echo "! packing without oob-divzero binary (--allow-no-oob)" >&2
else
  echo "X Need --oob-divzero /path/to/oob-divzero (or --allow-no-oob)" >&2
  exit 1
fi

# Installers
if [[ "$OS" == "windows" ]]; then
  cp "$ROOT/installs/win/install.ps1" "$STAGE/install.ps1"
  cp "$ROOT/installs/win/install.bat" "$STAGE/install.bat"
else
  # macOS / linux share shell installer
  cp "$ROOT/installs/macos/install.sh" "$STAGE/install.sh"
  chmod +x "$STAGE/install.sh"
fi

# Docs + config
cp "$ROOT/installs/README.md" "$STAGE/README.md"
cp "$ROOT/packaging/config.example.toml" "$STAGE/config.example.toml"

# Required skills (paper-derived docs; binary already in bin/)
# shellcheck source=lib/copy-required-skills.sh
source "$ROOT/scripts/lib/copy-required-skills.sh"
echo "-> required skills"
copy_required_skills "$STAGE" "$ROOT" || {
  echo "X copy_required_skills failed" >&2
  exit 1
}
cp -f "$ROOT/packaging/required-skills.txt" "$STAGE/required-skills.txt" 2>/dev/null || true

# Hard gate: incomplete suite must not ship
if [[ "$OS" == "windows" ]]; then
  STAGE_PD="$STAGE/bin/paper-derived.exe"
else
  STAGE_PD="$STAGE/bin/paper-derived"
fi
STAGE_SKILL="$STAGE/skills/paper-derived/SKILL.md"
STAGE_OOB_SKILL="$STAGE/skills/oob-divzero/SKILL.md"
if [[ ! -f "$STAGE_PD" ]]; then
  echo "X suite missing $STAGE_PD" >&2
  exit 1
fi
if [[ ! -f "$STAGE_SKILL" ]]; then
  echo "X suite missing $STAGE_SKILL (vendored packaging/skills?)" >&2
  exit 1
fi
if [[ ! -f "$STAGE_OOB_SKILL" ]]; then
  echo "X suite missing $STAGE_OOB_SKILL (vendored packaging/skills/oob-divzero?)" >&2
  exit 1
fi
PD_SIZE=$(wc -c <"$STAGE_PD" | tr -d ' ')
if [[ "${PD_SIZE:-0}" -lt 1000000 ]]; then
  echo "X paper-derived binary too small (${PD_SIZE} bytes)" >&2
  exit 1
fi
echo "  paper-derived: $(basename "$STAGE_PD") (${PD_SIZE} bytes)"
echo "  skill: paper-derived OK"
echo "  skill: oob-divzero OK"

# C toolchain for oob ASan
if [[ -z "$C_TOOLCHAIN" ]]; then
  for cand in \
    "$ROOT/vendor/c-toolchain-${OS}-${ARCH}" \
    "$ROOT/vendor/c-toolchain-${OS}" \
    "$ROOT/vendor/c-toolchain"
  do
    if [[ -x "$cand/bin/clang" || -x "$cand/bin/gcc" || -x "$cand/bin/clang.exe" ]]; then
      C_TOOLCHAIN="$cand"
      break
    fi
  done
fi
if [[ -n "$C_TOOLCHAIN" ]]; then
  echo "-> bundling C toolchain -> tools/c-toolchain"
  rm -rf "$STAGE/tools/c-toolchain"
  mkdir -p "$STAGE/tools/c-toolchain"
  tc_root="$C_TOOLCHAIN"
  [[ "$(basename "$C_TOOLCHAIN")" == "bin" ]] && tc_root="$(dirname "$C_TOOLCHAIN")"
  cp -R "$tc_root/." "$STAGE/tools/c-toolchain/"
  if [[ ! -x "$STAGE/tools/c-toolchain/bin/clang" && ! -x "$STAGE/tools/c-toolchain/bin/gcc" ]]; then
    echo "X c-toolchain copy missing bin/clang|gcc" >&2
    exit 1
  fi
  cat >"$STAGE/tools/c-toolchain/ROOM-NOTICE.txt" <<'EOF'
Room bundles a C toolchain for oob-divzero ASan verification.
See upstream licenses in this tree. Redistributed for offline install.
EOF
  echo "  c-toolchain OK"
elif [[ $REQUIRE_C_TOOLCHAIN -eq 1 ]]; then
  echo "X --require-c-toolchain but no toolchain found (scripts/fetch-c-toolchain-macos.sh)" >&2
  exit 1
else
  echo "! suite without tools/c-toolchain - oob ASan needs system clang (dev OK)" >&2
fi

# Room bootstrap needs `paper-derived version` JSON + capabilities
echo "-> verify paper-derived version (Room-compatible)"
if ! VER_OUT="$("$STAGE_PD" version 2>&1)"; then
  echo "X paper-derived does not support 'version' (Room requires it):" >&2
  echo "$VER_OUT" >&2
  echo "  Rebuild engine from paper-derived branch claude0 (product line; not master)." >&2
  exit 1
fi
if ! echo "$VER_OUT" | grep -q '"capabilities"' \
  || ! echo "$VER_OUT" | grep -q 'out-text-prompt' \
  || ! echo "$VER_OUT" | grep -q 'session-run'; then
  echo "X paper-derived version JSON missing required capabilities:" >&2
  echo "$VER_OUT" >&2
  exit 1
fi
echo "  paper-derived version: OK"

if [[ -n "$PI_BIN_FILE" ]]; then
  python3 "$ROOT/scripts/verify-room-pi.py" --suite "$STAGE"
fi

# Version manifest
{
  echo "room=${VERSION}"
  echo "os=${OS}"
  echo "arch=${ARCH}"
  echo "paper-derived=$(basename "$PD_BIN")"
  if [[ -n "$PI_BIN_FILE" ]]; then
    echo "pi=$(basename "$PI_BIN_FILE")"
    echo "pi_brand=room"
  else
    echo "pi="
    echo "pi_brand="
  fi
  echo "skills=paper-derived"
  echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$STAGE/VERSION"

# Zip
mkdir -p "$OUT_ROOT"
ZIP="$OUT_ROOT/${STAGE_NAME}.zip"
rm -f "$ZIP"
(
  cd "$OUT_ROOT"
  if command -v zip >/dev/null 2>&1; then
    zip -r -q "$(basename "$ZIP")" "$STAGE_NAME"
  else
    # Fallback: tar.gz
    ZIP="${ZIP%.zip}.tar.gz"
    tar -czf "$(basename "$ZIP")" "$STAGE_NAME"
  fi
)

echo
echo "OK suite folder: $STAGE"
echo "OK archive     : $ZIP ($(du -h "$ZIP" | cut -f1))"
echo
echo "Ship to users:"
echo "  1. Send $ZIP"
echo "  2. User unzips and runs install.sh / install.ps1"
echo "  3. room doctor  (pi.exe + theme/ are bundled)"
