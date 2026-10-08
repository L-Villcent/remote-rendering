#!/bin/bash
# System accounts and groups (security-boundary 2.2, 3, 4).
. "$(dirname "$0")/lib.sh"; need_root
nologin=/usr/sbin/nologin
ensure_group() { getent group "$1" >/dev/null 2>&1 || run groupadd --system "$1"; }
ensure_user() {  # name home shell
  getent passwd "$1" >/dev/null 2>&1 || run useradd --system --home-dir "$2" --no-create-home --shell "$3" --user-group "$1"
}
ensure_group "$CLAUDE_GROUP"
ensure_user "$PUBLISH_USER" /nonexistent "$nologin"
ensure_user "$INGEST_USER" /nonexistent "$nologin"
ensure_user "$NETCHECK_USER" /nonexistent "$nologin"
ensure_user "$WORKER_USER" / "$nologin"                        # chrooted SFTP; home is / inside the chroot
ensure_user "$CLAUDE_USER" /srv/agent/home /bin/bash            # interactive only via ForceCommand attach
run usermod -a -G "$CLAUDE_GROUP" "$CLAUDE_USER"
run loginctl disable-linger "$CLAUDE_USER"                     # no persistent systemd --user instance
for f in /etc/cron.deny /etc/at.deny; do                        # deny lists only; allow lists untouched
  if [ "$APPLY" = 1 ]; then grep -qx "$CLAUDE_USER" "$f" 2>/dev/null || echo "$CLAUDE_USER" >>"$f"
  else say "+ ensure $CLAUDE_USER listed in $f"; fi
done
say "NOTE: no sudo rights are granted to any of these accounts."
