#!/bin/bash
# sshd drop-in for the two restricted accounts (security-boundary 2.2, 4.3).
# Root / password hardening for other accounts is a SEPARATE change and not done here.
# Applying validates with `sshd -t`; it reloads sshd only when RELOAD_SSHD=1.
# Keep an existing session open while testing to avoid lock-out.
. "$(dirname "$0")/lib.sh"; need_root
REQ="open,close,read,write,lstat,fstat,opendir,readdir,remove,mkdir,rmdir,realpath,stat,rename,posix-rename,fsync,limits"
NOFWD="AllowTcpForwarding no
  AllowStreamLocalForwarding no
  AllowAgentForwarding no
  X11Forwarding no
  PermitTunnel no
  PermitUserEnvironment no
  AuthenticationMethods publickey
  PasswordAuthentication no
  KbdInteractiveAuthentication no"
mkd /etc/ssh/authorized_keys 0755 root:root
for u in "$WORKER_USER" "$CLAUDE_USER"; do
  if [ "$APPLY" = 1 ]; then [ -e "/etc/ssh/authorized_keys/$u" ] || install -m 0644 -o root -g root /dev/null "/etc/ssh/authorized_keys/$u"
  else say "+ create empty /etc/ssh/authorized_keys/$u (root 0644) if absent; keys are added later by the user"; fi
done
put /etc/ssh/sshd_config.d/50-render.conf 0644 root:root <<CONF
# Managed by deploy/vps/40-sshd.sh. Keys live in root-owned /etc/ssh/authorized_keys/<user>
# and must carry: restrict,from="$TAILNET_V4,$TAILNET_V6"

# 1) Any session for the two accounts from outside the Tailnet ranges: no usable auth method.
Match User $WORKER_USER,$CLAUDE_USER Address *,!$TAILNET_V4,!$TAILNET_V6
  PubkeyAuthentication no
  PasswordAuthentication no
  KbdInteractiveAuthentication no
  AuthenticationMethods publickey

# 2) Worker: chrooted SFTP with an explicit request allow-list (no setstat, links, copy-data).
Match User $WORKER_USER
  AuthorizedKeysFile /etc/ssh/authorized_keys/%u
  ChrootDirectory /srv/render
  ForceCommand internal-sftp -u 0027 -p $REQ
  PermitTTY no
  $NOFWD

# 3) Claude: attach to the sandboxed session only (no shell, no forwarding).
Match User $CLAUDE_USER
  AuthorizedKeysFile /etc/ssh/authorized_keys/%u
  ForceCommand /usr/local/lib/render/claude-attach
  PermitTTY yes
  $NOFWD

Match all
CONF
if [ "$APPLY" = 1 ]; then
  sshd -t || { rm -f /etc/ssh/sshd_config.d/50-render.conf; die "sshd -t failed; drop-in removed"; }
  if [ "${RELOAD_SSHD:-0}" = 1 ]; then systemctl reload ssh; else say "NOTE: validated; reload with RELOAD_SSHD=1 after review"; fi
else
  say "+ sshd -t (remove the drop-in again if validation fails)"
fi
