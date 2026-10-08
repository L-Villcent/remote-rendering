#!/bin/bash
# Shared helpers.  Modes (set by deploy.sh):
#   default      dry-run: print every command / file write, change nothing
#   RENDER_DIR   write every generated file under $RENDER_DIR/<path> for review, run nothing
#   APPLY=1      perform the changes (root only, after review and user authorization)
# Scripts never print network endpoints, addresses of peers, or file contents.
set -euo pipefail
DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd "$DEPLOY_DIR/../.." && pwd)
# shellcheck source=/dev/null
. "$DEPLOY_DIR/config.env"
# shellcheck source=/dev/null
. "$DEPLOY_DIR/net-constants.env"
APPLY=${APPLY:-0}
RENDER_DIR=${RENDER_DIR:-}

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

run() {
  if [ "$APPLY" = 1 ]; then "$@"
  elif [ -z "$RENDER_DIR" ]; then printf '+'; printf ' %q' "$@"; printf '\n'
  fi
}

# put <path> <mode> <owner:group>   (content on stdin; atomic replace when applied)
put() {
  local path=$1 mode=$2 own=$3 tmp
  if [ "$APPLY" = 1 ]; then
    tmp=$(mktemp "$(dirname "$path")/.render-XXXXXX")
    cat >"$tmp"; chmod "$mode" "$tmp"; chown "$own" "$tmp"; mv -f "$tmp" "$path"
  elif [ -n "$RENDER_DIR" ]; then
    mkdir -p "$RENDER_DIR$(dirname "$path")"; cat >"$RENDER_DIR$path"
    printf '%s %s %s\n' "$mode" "$own" "$path" >>"$RENDER_DIR/.modes"
  else
    printf '+ write %s (%s %s, %s bytes)\n' "$path" "$mode" "$own" "$(wc -c | tr -d ' ')"
  fi
}

need_root() {
  if [ "$APPLY" = 1 ] && [ "$(id -u)" != 0 ]; then die "--apply must run as root"; fi
}

# mkd <path> <mode> <owner:group>
mkd() { run install -d -m "$2" -o "${3%%:*}" -g "${3##*:}" "$1"; }

# acl <path> <access-spec> [default-spec]
acl() {
  if [ "$2" != "" ]; then run setfacl -m "$2" "$1"; fi
  if [ "${3:-}" != "" ]; then run setfacl -d -m "$3" "$1"; fi
}
