#!/bin/bash
# Usage:
#   deploy.sh [steps...]                 dry-run (default): print every action, change nothing
#   deploy.sh --render DIR [steps...]    write all generated files under DIR for review, change nothing
#   deploy.sh --apply [steps...]         perform changes: root only, requires I_HAVE_REVIEWED=yes
# Steps default to 00..80 in order; 99-rollback only when named explicitly.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
export APPLY=0 RENDER_DIR=""
case "${1:-}" in
  --apply) export APPLY=1; shift
           [ "${I_HAVE_REVIEWED:-}" = yes ] || { echo "refusing: set I_HAVE_REVIEWED=yes after reviewing --render output" >&2; exit 2; };;
  --render) [ -n "${2:-}" ] || { echo "--render needs a directory" >&2; exit 2; }
            RENDER_DIR=$(mkdir -p "$2" && cd "$2" && pwd); export RENDER_DIR; shift 2;;
esac
steps=("$@")
[ ${#steps[@]} -gt 0 ] || steps=(00-preflight 10-accounts 20-images 30-dirs 40-sshd 50-nftables 60-sandbox 70-gates 80-verify)
for s in "${steps[@]}"; do
  f="$here/${s%.sh}.sh"; [ -f "$f" ] || { echo "unknown step $s" >&2; exit 2; }
  if [ "$APPLY" != 1 ] && [ "$s" = 00-preflight ]; then echo "== $s (read-only checks; skipped in dry-run/render)"; continue; fi
  echo "== $s"
  bash "$f"
done
