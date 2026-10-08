#!/bin/bash
# Egress policy for the sandbox uid only (security-boundary 2.5). Touches no other table.
# Loaded by render-nft.service; claude-sandbox.service Requires= it (fail closed).
. "$(dirname "$0")/lib.sh"; need_root
mkd /etc/render/nft 0755 root:root
put /etc/render/nft/render-sandbox.nft 0644 root:root <<NFT
#!/usr/sbin/nft -f
# Managed by deploy/vps/50-nftables.sh. Applies only to sockets owned by $CLAUDE_USER
# (the pasta process that carries all sandbox traffic runs as that user).
table inet render_sandbox
delete table inet render_sandbox
table inet render_sandbox {
  set deny4 { type ipv4_addr; flags interval; elements = { $DENY_V4 } }
  set deny6 { type ipv6_addr; flags interval; elements = { $DENY_V6 } }
  set dns4  { type ipv4_addr; elements = { $SANDBOX_RESOLVERS_V4 } }
  set dns6  { type ipv6_addr; elements = { $SANDBOX_RESOLVERS_V6 } }
  chain output {
    type filter hook output priority filter; policy accept;
    meta skuid != "$CLAUDE_USER" accept
    fib daddr type local counter reject comment "VPS own addresses incl. services on all interfaces"
    ip daddr @dns4 meta l4proto { tcp, udp } th dport 53 accept
    ip6 daddr @dns6 meta l4proto { tcp, udp } th dport 53 accept
    ip daddr @deny4 counter reject comment "loopback, private, Tailnet, MagicDNS, link-local, metadata"
    ip6 daddr @deny6 counter reject
    meta l4proto tcp th dport { 80, 443 } accept
    counter reject
  }
}
NFT
put /etc/systemd/system/render-nft.service 0644 root:root <<'UNIT'
[Unit]
Description=Render sandbox egress rules (table inet render_sandbox only)
After=nftables.service
PartOf=nftables.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/render/nft/render-sandbox.nft
ExecStop=-/usr/sbin/nft delete table inet render_sandbox
[Install]
WantedBy=multi-user.target
UNIT
if [ "$APPLY" = 1 ]; then nft -c -f /etc/render/nft/render-sandbox.nft || die "nft syntax check failed"; else say "+ nft -c -f /etc/render/nft/render-sandbox.nft"; fi
run systemctl daemon-reload
run systemctl enable --now render-nft.service
