#!/bin/bash
# Post-deployment checks that need no sandbox session (FS-03..06 at config level,
# FS-14/15/17, nft table, units). Prints PASS/FAIL only - never command output.
. "$(dirname "$0")/lib.sh"
if [ "$APPLY" != 1 ]; then
  say "+ python3 -I -B $REPO_DIR/deploy/probes/fs_check.py $REPO_DIR/deploy/access-matrix.json"
  say "+ sshd -T effective-config checks for $WORKER_USER and $CLAUDE_USER (Tailnet vs loopback source)"
  say "+ nft list table inet render_sandbox; systemctl is-enabled for render units"
  exit 0
fi
need_root
fail=0
ok() { if "$@" >/dev/null 2>&1; then say "PASS $label"; else say "FAIL $label"; fail=1; fi; }
eff() { sshd -T -C "user=$1,host=verify.invalid,addr=$2" 2>/dev/null | tr 'A-Z' 'a-z'; }
has() { grep -qx -- "$2" <<<"$1"; }
w=$(eff "$WORKER_USER" "$VERIFY_TAILNET_ADDR")
label="FS-01 chroot";            ok has "$w" "chrootdirectory /srv/render"
label="FS-03 sftp request list";  ok grep -q -- "-p open,close,read,write" <<<"$w"
label="FS-04 no tty/forwarding";  ok bash -c 'grep -qx "permittty no" <<<"$0" && grep -qx "allowtcpforwarding no" <<<"$0" && grep -qx "allowstreamlocalforwarding no" <<<"$0"' "$w"
label="FS-05 publickey only";     ok bash -c 'grep -qx "passwordauthentication no" <<<"$0" && grep -qx "authenticationmethods publickey" <<<"$0"' "$w"
l=$(eff "$WORKER_USER" "$LOOPBACK_V4")
label="FS-06 non-Tailnet source has no auth method"; ok has "$l" "pubkeyauthentication no"
c=$(eff "$CLAUDE_USER" "$VERIFY_TAILNET_ADDR")
label="SB-11 claude-agent forced to attach";  ok has "$c" "forcecommand /usr/local/lib/render/claude-attach"
label="SB-09 egress table loaded";            ok nft list table inet render_sandbox
for u in render-nft.service render-publish.service render-ingest-light.service render-ingest-heavy.service render-netcheck.timer render-image-check.timer claude-sandbox.service; do
  label="unit enabled $u"; ok systemctl is-enabled "$u"
done
python3 -I -B "$REPO_DIR/deploy/probes/fs_check.py" "$REPO_DIR/deploy/access-matrix.json" || fail=1
exit $fail
