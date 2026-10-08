#!/bin/bash
# Read-only checks. Prints PASS/FAIL and sizes only; never network details.
. "$(dirname "$0")/lib.sh"
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then say "PASS $1"; else say "FAIL $1"; fail=1; fi; }
need=$(( (IMG_UP_GIB + IMG_DOWN_GIB + IMG_DATA_GIB + IMG_AGENT_GIB) * 1024 * 1024 * 1024 ))
read -r size avail < <(df -B1 --output=size,avail / | tail -1)
keep=$(( ROOT_RESERVE_GIB * 1024 * 1024 * 1024 ))
pct=$(( size * ROOT_RESERVE_PCT / 100 )); [ "$pct" -gt "$keep" ] && keep=$pct
say "INFO root_size_gib=$((size >> 30)) root_avail_gib=$((avail >> 30)) images_gib=$((need >> 30)) reserve_gib=$((keep >> 30))"
chk "root free after images >= reserve" "[ $((avail - need)) -ge $keep ]"
for p in bubblewrap passt dtach acl nftables e2fsprogs openssh-server; do chk "package $p" "dpkg -s $p"; done
chk "sshd includes sshd_config.d" "grep -Eq '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config.d/\*\.conf' /etc/ssh/sshd_config"
chk "unprivileged user namespaces" "[ \"\$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo 1)\" = 1 ]"
chk "claude code installed at CLAUDE_CODE_DIR" "[ -x $CLAUDE_CODE_DIR/bin/claude ]"
exit $fail
