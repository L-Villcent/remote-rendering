#!/bin/bash
# Undo the deployment. Data (images, accounts) is kept unless PURGE=1. Requires CONFIRM=1 with --apply.
. "$(dirname "$0")/lib.sh"; need_root
if [ "$APPLY" = 1 ] && [ "${CONFIRM:-0}" != 1 ]; then die "set CONFIRM=1 to roll back"; fi
for u in claude-sandbox.service render-publish.service render-ingest-light.service render-ingest-heavy.service render-netcheck.timer render-nft.service render-image-check.timer; do
  run systemctl disable --now "$u"
done
run rm -f /etc/ssh/sshd_config.d/50-render.conf
if [ "$APPLY" = 1 ]; then sshd -t && say "NOTE: reload ssh manually after checking: systemctl reload ssh"; else say "+ sshd -t"; fi
run rm -f /etc/systemd/system/fstrim.service.d/render.conf
run systemctl daemon-reload
for mp in /srv/agent /srv/render-data /srv/render/down /srv/render/up; do run umount "$mp"; done
if [ "$APPLY" = 1 ]; then sed -i '/^# render-images BEGIN$/,/^# render-images END$/d' /etc/fstab; else say "+ remove '# render-images' block from /etc/fstab"; fi
if [ "${PURGE:-0}" = 1 ]; then
  run rm -rf "$IMAGE_DIR" /opt/render-gates /etc/render /var/lib/render-publish
  for u in "$CLAUDE_USER" "$WORKER_USER" "$PUBLISH_USER" "$INGEST_USER" "$NETCHECK_USER"; do run userdel "$u"; done
  run groupdel "$CLAUDE_GROUP"
else
  say "NOTE: images, accounts and /etc/render kept (PURGE=1 removes them)."
fi
