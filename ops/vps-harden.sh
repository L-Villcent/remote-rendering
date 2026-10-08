#!/bin/bash
# VPS hardening, one step at a time.  Run as root, in your own terminal, keeping the
# current session open until a NEW login has been tested.
#   vps-harden.sh check        read-only: how you can get root afterwards, what is exposed
#   vps-harden.sh sshd         key-only SSH, no root password login, X11 off (validated, reloaded)
#   vps-harden.sh firewall     drop all inbound except loopback, Tailscale and its UDP port;
#                              AUTO-REVERTS after 5 minutes unless you run `confirm`
#   vps-harden.sh confirm      keep the firewall and load it at boot
#   vps-harden.sh updates      install unattended-upgrades (security updates)
#   vps-harden.sh rollback     remove both changes
set -euo pipefail
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-hardening.conf
NFT_FILE=/etc/host-filter.nft
UNIT=/etc/systemd/system/host-filter.service
TS_PORT=41641
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }

root_paths() {
  local n=0
  if [ -s /root/.ssh/authorized_keys ] && grep -qE '^(ssh-|ecdsa-)' /root/.ssh/authorized_keys; then
    echo "OK   root has $(grep -cE '^(ssh-|ecdsa-)' /root/.ssh/authorized_keys) SSH key(s): key login as root keeps working"; n=1
  else
    echo "WARN root has no SSH key"
  fi
  if passwd -S root 2>/dev/null | awk '{exit !($2=="P")}'; then
    echo "OK   root has a password: 'su -' from a key-authenticated session keeps working"; n=1
  fi
  if getent group sudo | grep -qw code; then echo "OK   code is in sudo group"; n=1; fi
  return $((1 - n))
}

case "${1:-}" in
check)
  root_paths && echo "=> root stays reachable after 'sshd'" || echo "=> STOP: no root path would remain"
  for u in $(awk -F: '$7 !~ /(nologin|false)$/ {print $1":"$6}' /etc/passwd); do
    name=${u%%:*}; home=${u#*:}
    [ -s "$home/.ssh/authorized_keys" ] && echo "key  $name: $(grep -cE '^(ssh-|ecdsa-)' "$home/.ssh/authorized_keys") key(s)"
  done
  echo "public listeners (will be closed by 'firewall'):"
  ss -tulnH | awk '$5 ~ /^(0\.0\.0\.0|\*|\[::\]):/ {print "  " $1, $5}'
  ;;
sshd)
  root_paths >/dev/null || { echo "refusing: no way to get root afterwards (add a root key or set a root password first)"; exit 1; }
  cat >"$SSHD_DROPIN" <<'CONF'
# Hardening (ops/vps-harden.sh). Included first, so these values win over sshd_config.
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
CONF
  if sshd -t; then systemctl reload ssh; echo "sshd reloaded. Test a NEW login now before closing this session."
  else rm -f "$SSHD_DROPIN"; echo "sshd -t failed; change removed"; exit 1; fi
  ;;
firewall)
  cat >"$NFT_FILE" <<NFT
#!/usr/sbin/nft -f
# Inbound filter (ops/vps-harden.sh). Only this table is touched.
table inet host_filter
delete table inet host_filter
table inet host_filter {
  chain input {
    type filter hook input priority filter; policy drop;
    iif lo accept
    ct state established,related accept
    ct state invalid drop
    iifname "tailscale0" accept comment "everything over the Tailnet, incl. SSH and port 8765"
    udp dport $TS_PORT accept comment "Tailscale direct connections"
    meta l4proto icmp icmp type { echo-request, destination-unreachable, time-exceeded, parameter-problem } accept
    meta l4proto ipv6-icmp accept comment "IPv6 neighbour discovery / router advertisements"
    counter
  }
}
NFT
  nft -c -f "$NFT_FILE"
  nft -f "$NFT_FILE"
  systemd-run --unit=host-filter-revert --on-active=300 /usr/sbin/nft delete table inet host_filter >/dev/null
  echo "Firewall active. It REVERTS in 5 minutes unless you run: $0 confirm"
  echo "Now test from another terminal: ssh over Tailscale must still work."
  ;;
confirm)
  systemctl stop host-filter-revert.timer 2>/dev/null || true
  cat >"$UNIT" <<UNITF
[Unit]
Description=Inbound host filter (ops/vps-harden.sh)
Wants=network-pre.target
Before=network-pre.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f $NFT_FILE
ExecStop=-/usr/sbin/nft delete table inet host_filter
[Install]
WantedBy=multi-user.target
UNITF
  systemctl daemon-reload; systemctl enable host-filter.service
  echo "Firewall kept and enabled at boot."
  ;;
updates)
  apt-get update -qq && apt-get install -y -qq unattended-upgrades
  printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' >/etc/apt/apt.conf.d/20auto-upgrades
  echo "Automatic security updates enabled."
  ;;
rollback)
  rm -f "$SSHD_DROPIN"; sshd -t && systemctl reload ssh
  systemctl disable --now host-filter.service 2>/dev/null || true
  nft delete table inet host_filter 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload
  echo "Rolled back sshd drop-in and firewall."
  ;;
*) sed -n '2,12p' "$0"; exit 2;;
esac
