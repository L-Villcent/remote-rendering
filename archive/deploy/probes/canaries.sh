#!/bin/bash
# SB acceptance helper (root, in an admin terminal - never inside the Claude session).
#   canaries.sh setup      place synthetic canaries, start a loopback canary listener,
#                          write /etc/render/probe/{probe.json,sandbox_probe.py}
#   canaries.sh run        run the probe inside a fresh sandbox with a polluted launch
#                          environment (synthetic SSH_CONNECTION, canary env, open fd 9)
#   canaries.sh teardown   remove everything again
# Only synthetic values (RFC 5737 documentation addresses, random CANARY- tokens) are used.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/../vps/config.env"; . "$here/../vps/net-constants.env"
P=/etc/render/probe
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
CANARY_PATHS=(/var/log/render-canary /var/lib/render-canary /home/code/.render-canary
              /home/code/.claude/render-canary /srv/render/up/.render-canary
              /srv/render-data/ingest/.render-canary /etc/render/render-canary)
case "${1:-}" in
setup)
  tok="CANARY-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  port=$(( 20000 + $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % 20000 ))
  install -d -m 0755 "$P"
  for f in "${CANARY_PATHS[@]}"; do install -d "$(dirname "$f")"; printf '%s\n' "$tok" >"$f"; chmod 0644 "$f"; done
  nohup python3 -I -c "import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('$LOOPBACK_V4',$port));s.listen();time.sleep(86400)" \
    >/dev/null 2>&1 & echo $! >"$P/listener.pid"
  nohup sleep 86400 "$tok" >/dev/null 2>&1 & echo $! >"$P/canary-proc.pid"
  deny=$(python3 -I - "$port" <<PY
import json, subprocess, sys
port = int(sys.argv[1])
t = [["$LOOPBACK_V4", port], ["$LOOPBACK_V4", 22], ["$LOOPBACK_V6", 22], ["$MAGICDNS_V4", 53],
     ["$METADATA_V4", 80], ["$PRIVATE_SAMPLE_V4", 80]]
# the VPS's own addresses (public and Tailnet) on port 22; values go into the file only, never to stdout
out = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, encoding="utf-8").stdout
for line in out.splitlines():
    addr = line.split()[3].split("/")[0]
    t.append([addr, 22])
print(json.dumps(t))
PY
)
  cat >"$P/probe.json" <<JSON
{"canary_token": "$tok", "canary_port": $port,
 "canary_paths": $(printf '%s\n' "${CANARY_PATHS[@]}" | python3 -I -c 'import json,sys;print(json.dumps(sys.stdin.read().split()))'),
 "deny_targets": $deny, "allow_targets": [["api.anthropic.com", 443]],
 "magicdns_names": ["localhost.ts.net"], "magicdns_resolver": "$MAGICDNS_V4",
 "env_whitelist": ["HOME", "PATH", "LANG", "TERM", "TZ", "DISABLE_AUTOUPDATER", "CLAUDE_CONFIG_DIR", "PWD", "SHLVL", "_"]}
JSON
  chmod 0644 "$P/probe.json"; install -m 0644 "$here/sandbox_probe.py" "$P/sandbox_probe.py"
  echo "setup done (synthetic canaries in place)";;
run)
  tok=$(python3 -I -c 'import json;print(json.load(open("/etc/render/probe/probe.json"))["canary_token"])')
  exec 9<"${CANARY_PATHS[0]}"
  runuser -u "$CLAUDE_USER" -- env SSH_CONNECTION="$SYNTH_DOC_V4_A 50000 $SYNTH_DOC_V4_B 22" RENDER_CANARY="$tok" \
    /usr/local/lib/render/claude-entry --probe;;
teardown)
  for f in "${CANARY_PATHS[@]}"; do rm -f "$f"; done
  for pidf in "$P/listener.pid" "$P/canary-proc.pid"; do [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null || true; done
  rm -rf "$P"; echo "teardown done";;
*) echo "usage: canaries.sh setup|run|teardown" >&2; exit 2;;
esac
