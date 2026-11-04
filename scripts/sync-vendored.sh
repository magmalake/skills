#!/usr/bin/env bash
# Copy the skills this repository vendors from other repositories into
# vendor/, unchanged, together with their upstream license.
#
# They are here only for `npx skills add magmalake/skills`, which installs
# just the skills inside one repository. Claude Code does not use these
# copies: its marketplace entries fetch the skills from their own
# repositories (.claude-plugin/marketplace.json).
#
#   scripts/sync-vendored.sh          update vendor/ from upstream main
#   scripts/sync-vendored.sh --check  fail if vendor/ differs from upstream
set -euo pipefail

# name | repository | skill folder in it | license file in it
VENDORED=(
  "mojo-syntax|https://github.com/modular/skills.git|mojo-syntax|LICENSE"
  "mojoshelf-consume|https://github.com/mojoshelf/mojoshelf.git|skills/mojoshelf-consume|LICENSE"
)

check=false
[[ "${1:-}" == "--check" ]] && check=true

root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
status=0

for entry in "${VENDORED[@]}"; do
  IFS='|' read -r name repo path license <<<"$entry"
  clone="$work/$name.git"
  git clone --quiet --depth 1 --filter=blob:none --sparse "$repo" "$clone"
  git -C "$clone" sparse-checkout set --no-cone "/$path/" "/$license"
  rev=$(git -C "$clone" rev-parse --short HEAD)

  expected="$work/$name"
  cp -R "$clone/$path" "$expected"
  cp "$clone/$license" "$expected/LICENSE"

  if $check; then
    if diff -r "$expected" "$root/vendor/$name" >/dev/null 2>&1; then
      echo "vendor/$name matches $repo $path at $rev"
    else
      echo "vendor/$name differs from $repo $path at $rev:"
      diff -r "$expected" "$root/vendor/$name" || true
      status=1
    fi
  else
    rm -rf "$root/vendor/$name"
    mkdir -p "$root/vendor"
    cp -R "$expected" "$root/vendor/$name"
    echo "vendor/$name <- $repo $path at $rev"
  fi
done

if [[ $status -ne 0 ]]; then
  echo "Run scripts/sync-vendored.sh and commit the result."
fi
exit $status
