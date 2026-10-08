#!/bin/bash
# Install a reviewed gate release (tools/make_gate_release.py) and its systemd units.
# Usage: RELEASE_TAR=... RELEASE_MANIFEST=... deploy.sh --apply 70-gates
# Every file is verified against the manifest before installation.
. "$(dirname "$0")/lib.sh"; need_root
DEST=/opt/render-gates/$GATES_VERSION
if [ "$APPLY" = 1 ]; then
  [ -f "${RELEASE_TAR:-}" ] && [ -f "${RELEASE_MANIFEST:-}" ] || die "set RELEASE_TAR and RELEASE_MANIFEST"
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  tar -C "$tmp" -xf "$RELEASE_TAR"
  src="$tmp/render-gates-$GATES_VERSION"
  (cd "$src" && sha256sum -c --quiet "$RELEASE_MANIFEST") || die "manifest verification failed"
  [ "$(cd "$src" && find . -type f | wc -l)" = "$(wc -l <"$RELEASE_MANIFEST")" ] || die "release has files not in the manifest"
  rm -rf "$DEST"; mkdir -p /opt/render-gates; cp -a "$src" "$DEST"
  chown -R root:root "$DEST"; find "$DEST" -type d -exec chmod 0755 {} +; find "$DEST" -type f -exec chmod 0644 {} +
  chmod 0755 "$DEST/bin/render-gate"; ln -sfn "$DEST" /opt/render-gates/current
else
  say "+ verify RELEASE_TAR against RELEASE_MANIFEST, install to $DEST (root, 0644/0755), link /opt/render-gates/current"
fi
put /etc/render/gates.json 0644 root:root <<JSON
{"schemas_dir": "/opt/render-gates/current/schemas", "profiles": "/etc/render/profiles.json",
 "toolchains": "/etc/render/toolchains.json",
 "intervals": {"publish": 3, "ingest-light": 5, "ingest-heavy": 5, "netcheck": 60},
 "owners": {"outbox": "$CLAUDE_USER", "up": "$WORKER_USER"}}
JSON
for f in profiles toolchains; do
  if [ "$APPLY" = 1 ] && [ -e "/etc/render/$f.json" ]; then continue; fi
  if [ $f = profiles ]; then body='{"profiles_version": 1, "profiles": {}}'; else body='{}'; fi
  printf '%s\n' "$body" | put "/etc/render/$f.json" 0644 root:root      # placeholders until MX-01 registration
done
if [ "$APPLY" != 1 ] || [ ! -e /etc/render/netcheck-map.json ]; then
  printf '{"worker-local": ""}\n' | put /etc/render/netcheck-map.json 0600 root:root   # node id filled in by the user
fi
HARDEN='NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
PrivateNetwork=yes
IPAddressDeny=any
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
CapabilityBoundingSet=
UMask=0027
MemoryMax=256M
CPUQuota=50%
IOSchedulingClass=idle
TasksMax=16
NoExecPaths=/srv /var/lib/render-publish
ExecPaths=/opt/render-gates /usr'
unit() {  # name user rw ro inaccessible exec_args type
  put "/etc/systemd/system/$1.service" 0644 root:root <<UNIT
[Unit]
Description=$1 (protocol-v1 gate)
After=local-fs.target
[Service]
Type=$7
User=$2
Group=$2
ExecStart=/opt/render-gates/current/bin/render-gate $6 --config /etc/render/gates.json
$( [ "$7" = simple ] && printf 'Restart=always\nRestartSec=5' )
ReadWritePaths=$3
ReadOnlyPaths=$4
InaccessiblePaths=$5
$HARDEN
UNIT
}
unit render-publish "$PUBLISH_USER" "/srv/render/down /srv/render-data/inbox/submissions /var/lib/render-publish" \
  "/srv/agent/outbox /srv/render-data/inbox/jobs /etc/render" "-/srv/agent/home -/srv/agent/work -/srv/render/up -/srv/render-data/ingest" publish simple
for part in light heavy; do
  unit "render-ingest-$part" "$INGEST_USER" "/srv/render/down/acks /srv/render-data/inbox/jobs /srv/render-data/inbox/workers /srv/render-data/inbox/handoff /srv/render-data/ingest" \
    "/srv/render/up /srv/render/down/jobs /srv/render/down/control /etc/render" "-/srv/agent" "ingest-$part" simple
done
unit render-netcheck "$NETCHECK_USER" "/srv/render-data/inbox/netcheck" "/etc/render" "-/srv/agent -/srv/render" "netcheck --once" oneshot
mkd /etc/systemd/system/render-netcheck.service.d 0755 root:root
put /etc/systemd/system/render-netcheck.service.d/credential.conf 0644 root:root <<'UNIT'
[Service]
LoadCredential=netcheck-map:/etc/render/netcheck-map.json
UNIT
put /etc/systemd/system/render-netcheck.timer 0644 root:root <<'UNIT'
[Unit]
Description=render-netcheck every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
UNIT
run systemctl daemon-reload
run systemctl enable render-publish.service render-ingest-light.service render-ingest-heavy.service render-netcheck.timer
say "NOTE: services are enabled, not started; start them after 80-verify passes (START is never automatic)."
