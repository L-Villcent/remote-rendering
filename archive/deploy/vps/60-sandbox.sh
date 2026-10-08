#!/bin/bash
# Claude sandbox: dedicated uid + bubblewrap (all namespaces) + pasta network namespace
# + render-nft egress rules (security-boundary 2).  The session is reachable only via
# dtach (byte relay); no tmux client and no other host-side execution path exists outside.
. "$(dirname "$0")/lib.sh"; need_root
run apt-get install -y --no-install-recommends bubblewrap passt dtach tmux
mkd /etc/render/sandbox 0755 root:root
if [ "$APPLY" = 1 ]; then uid=$(id -u "$CLAUDE_USER"); gid=$(id -g "$CLAUDE_USER"); else uid=UID gid=GID; fi
put /etc/render/sandbox/passwd 0644 root:root <<P
root:x:0:0::/nonexistent:/usr/sbin/nologin
$CLAUDE_USER:x:$uid:$gid::/home/agent:/bin/bash
P
put /etc/render/sandbox/group 0644 root:root <<G
root:x:0:
$CLAUDE_USER:x:$gid:
G
put /etc/render/sandbox/hosts 0644 root:root <<H
$LOOPBACK_V4 localhost
$LOOPBACK_V6 localhost
H
{ for r in ${SANDBOX_RESOLVERS_V4//,/ } ${SANDBOX_RESOLVERS_V6//,/ }; do echo "nameserver $r"; done; echo "options timeout:2 attempts:2"; } \
  | put /etc/render/sandbox/resolv.conf 0644 root:root
put /etc/tmpfiles.d/render-claude.conf 0644 root:root <<T
d /run/claude-sandbox 0700 $CLAUDE_USER $CLAUDE_USER -
T
mkd /usr/local/lib/render 0755 root:root
put /usr/local/lib/render/claude-entry 0755 root:root <<ENTRY
#!/bin/bash
# Runs as $CLAUDE_USER (claude-sandbox.service), or by an admin with --probe for SB tests.
set -euo pipefail
for fd in /proc/self/fd/*; do n=\${fd##*/}; if [ "\$n" -gt 2 ] 2>/dev/null; then eval "exec \$n>&-" 2>/dev/null || true; fi; done
CMD=(/usr/bin/dtach -N /run/claude-sandbox/sock -z /usr/bin/tmux -L sandbox new-session -A -s claude /opt/claude-code/bin/claude)
EXTRA=()
if [ "\${1:-}" = --probe ]; then
  CMD=(/usr/bin/python3 -I -B /probe/sandbox_probe.py /probe/probe.json)
  EXTRA=(--ro-bind /etc/render/probe /probe)
fi
U=\$(id -u); G=\$(id -g)
exec /usr/bin/env -i HOME=/home/agent PATH=/opt/claude-code/bin:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 \\
  TERM=xterm-256color TZ=UTC DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=/home/agent/.claude \\
  /usr/bin/pasta --config-net --quiet --no-map-gw -t none -u none -T none -U none -- \\
  /usr/bin/bwrap --unshare-all --share-net --die-with-parent --new-session --cap-drop ALL --uid "\$U" --gid "\$G" \\
    --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \\
    --ro-bind /etc/ssl /etc/ssl --ro-bind-try /etc/ca-certificates /etc/ca-certificates \\
    --ro-bind /etc/nsswitch.conf /etc/nsswitch.conf --ro-bind-try /etc/localtime /etc/localtime \\
    --ro-bind-try /etc/alternatives /etc/alternatives --ro-bind-try /etc/terminfo /etc/terminfo \\
    --ro-bind /etc/render/sandbox/passwd /etc/passwd --ro-bind /etc/render/sandbox/group /etc/group \\
    --ro-bind /etc/render/sandbox/hosts /etc/hosts --ro-bind /etc/render/sandbox/resolv.conf /etc/resolv.conf \\
    --ro-bind $CLAUDE_CODE_DIR /opt/claude-code --ro-bind-try $FFMPEG_DIR /opt/ffmpeg \\
    --bind /srv/agent/home /home/agent --bind /srv/agent/work /work --bind /srv/agent/outbox /outbox \\
    --ro-bind /srv/render-data/inbox /inbox \\
    --proc /proc --dev /dev --tmpfs /run --bind /run/claude-sandbox /run/claude-sandbox \\
    --size $((SANDBOX_TMP_MB * 1024 * 1024)) --tmpfs /tmp \\
    --chdir /work "\${EXTRA[@]}" -- "\${CMD[@]}"
ENTRY
put /usr/local/lib/render/claude-attach 0755 root:root <<'ATTACH'
#!/bin/bash
# sshd ForceCommand for claude-agent: relay terminal bytes to the sandboxed session only.
exec /usr/bin/dtach -a /run/claude-sandbox/sock -z -r winch
ATTACH
put /etc/systemd/system/claude-sandbox.service 0644 root:root <<UNIT
[Unit]
Description=Claude Code inside the render sandbox
Requires=render-nft.service
After=render-nft.service network-online.target
Wants=network-online.target
[Service]
User=$CLAUDE_USER
Group=$CLAUDE_USER
ExecStart=/usr/local/lib/render/claude-entry
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
[Install]
WantedBy=multi-user.target
UNIT
run systemd-tmpfiles --create /etc/tmpfiles.d/render-claude.conf
run systemctl daemon-reload
run systemctl enable claude-sandbox.service
say "NOTE: start it only after SB acceptance (80-verify + probes); then log in to Claude Code inside the sandbox."
