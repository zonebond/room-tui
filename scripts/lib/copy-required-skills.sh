# shellcheck shell=bash
# Copy required skills into a suite staging dir (skills/<name>/).
# Skips engine binaries already shipped as suite bin/paper-derived.
#
# Usage: copy_required_skills <stage_dir> <room_repo_root>
copy_required_skills() {
  local stage="${1:?}"
  local root="${2:?}"
  local list="$root/packaging/required-skills.txt"
  local dest_root="$stage/skills"
  mkdir -p "$dest_root"

  if [[ ! -f "$list" ]]; then
    echo "! no packaging/required-skills.txt - skip skills" >&2
    return 0
  fi

  local name src
  while IFS= read -r line || [[ -n "$line" ]]; do
    name="${line%%#*}"
    name="$(echo "$name" | tr -d '[:space:]')"
    [[ -z "$name" ]] && continue

    src=""
    # packaging/skills is vendored in-repo - always prefer for offline Windows pack
    for cand in \
      "$root/packaging/skills/$name" \
      "${PAPER_DERIVED_SKILL:-}" \
      "${OOB_DIVZERO_SKILL:-}" \
      "$root/../paper-derived/skill" \
      "$root/../paper-derived/installs/skill" \
      "$root/../oob-divzero/skill" \
      "$root/../oob-divzero/installs/skill" \
      "$HOME/.claude/skills/$name" \
      "$HOME/.pi/agent/skills/$name" \
      "$HOME/.agents/skills/$name"
    do
      [[ -z "$cand" ]] && continue
      if [[ -f "$cand/SKILL.md" ]]; then
        src="$cand"
        break
      fi
    done

    if [[ -z "$src" ]]; then
      echo "X required skill missing: $name (expected packaging/skills/$name/SKILL.md in repo)" >&2
      return 1
    fi

    local dest="$dest_root/$name"
    rm -rf "$dest"
    mkdir -p "$dest"
    # Docs only - suite already has bin/<capability>[.exe]
    cp -f "$src/SKILL.md" "$dest/SKILL.md"
    for sub in workflows references examples prompts; do
      if [[ -d "$src/$sub" ]]; then
        mkdir -p "$dest/$sub"
        cp -R "$src/$sub/." "$dest/$sub/"
      fi
    done
    # Drop fat binaries / host wheels if any slipped in
    rm -f "$dest/paper-derived" "$dest/paper-derived.exe" 2>/dev/null || true
    rm -f "$dest/oob-divzero" "$dest/oob-divzero.exe" 2>/dev/null || true
    rm -rf "$dest/pkg" "$dest/.paper-working" 2>/dev/null || true
    echo "  + skill: $name  <-  $src"
  done < "$list"
}
