# shellcheck shell=bash
# Copy bun-compiled pi sidecar assets next to the suite pi binary.
# Pi resolves themes via dirname(process.execPath)/theme/dark.json
#
# Usage (sourced):
#   source scripts/lib/copy-pi-runtime.sh
#   copy_pi_runtime /path/to/pi[.exe] /path/to/suite/bin
#
# Optional env:
#   PI_ASSETS_DIR  - override directory that holds theme/ (default: dirname of pi)

copy_pi_runtime() {
  local pi_bin="$1"
  local dest_bin="$2"
  local assets_dir="${PI_ASSETS_DIR:-}"

  if [[ -z "$pi_bin" || ! -f "$pi_bin" ]]; then
    echo "copy_pi_runtime: pi binary missing: $pi_bin" >&2
    return 1
  fi
  mkdir -p "$dest_bin"

  if [[ -z "$assets_dir" ]]; then
    assets_dir="$(cd "$(dirname "$pi_bin")" && pwd)"
  fi

  # Known sidecars produced by packages/coding-agent `npm run build:binary` / copy-binary-assets
  local name
  for name in \
    theme \
    assets \
    export-html \
    docs \
    examples \
    package.json \
    README.md \
    CHANGELOG.md \
    photon_rs_bg.wasm
  do
    local src="$assets_dir/$name"
    if [[ -e "$src" ]]; then
      if [[ -d "$src" ]]; then
        rm -rf "$dest_bin/$name"
        cp -R "$src" "$dest_bin/$name"
      else
        cp -f "$src" "$dest_bin/$name"
      fi
      echo "  + pi asset: $name"
    fi
  done

  # Fallback: theme JSON from monorepo source tree if dist/theme missing
  if [[ ! -f "$dest_bin/theme/dark.json" ]]; then
    local try
    for try in \
      "$assets_dir/../src/modes/interactive/theme" \
      "$assets_dir/../../src/modes/interactive/theme" \
      "$assets_dir/modes/interactive/theme"
    do
      if [[ -f "$try/dark.json" ]]; then
        mkdir -p "$dest_bin/theme"
        cp -f "$try"/*.json "$dest_bin/theme/" 2>/dev/null || true
        echo "  + pi asset: theme (from $try)"
        break
      fi
    done
  fi

  if [[ ! -f "$dest_bin/theme/dark.json" ]]; then
    echo "X pi theme assets missing (need theme/dark.json next to pi)." >&2
    echo "  After build:binary, dist/ should contain theme/." >&2
    echo "  Pass PI_ASSETS_DIR=/path/to/dir-with-theme or re-run npm run build:binary" >&2
    return 1
  fi
  return 0
}
