#!/bin/bash
# Directory tree, owners, modes and ACLs (security-boundary 4.2, r5.1; deploy/access-matrix.json).
. "$(dirname "$0")/lib.sh"; need_root
P=$PUBLISH_USER I=$INGEST_USER N=$NETCHECK_USER W=$WORKER_USER C=$CLAUDE_USER G=$CLAUDE_GROUP
# chroot root and Worker areas
mkd /srv/render 0755 root:root
mkd /srv/render/up 2750 "$W:$I"
acl /srv/render/up "" "u::rwx,g::r-x,o::---,m::r-x"
for d in _worker jobs handoff; do mkd "/srv/render/up/$d" 2750 "$W:$I"; done
mkd /srv/render/down 0755 root:root
for d in jobs control handoff; do mkd "/srv/render/down/$d" 2750 "$P:$W"; acl "/srv/render/down/$d" "o::---" "g::r-x,o::---"; done
for d in jobs control; do acl "/srv/render/down/$d" "u:$I:r-x" "u:$I:r-x"; done      # r5.1 D1
mkd /srv/render/down/acks 2750 "$I:$W"; acl /srv/render/down/acks "o::---" "g::r-x,o::---"
mkd /srv/render/down/.staging 0700 "$P:$P"
# ingest private + inbox
mkd /srv/render-data 0711 root:root
mkd /srv/render-data/ingest 0700 "$I:$I"
mkd /srv/render-data/inbox 0750 "root:$G"
acl /srv/render-data/inbox "u:$I:--x,u:$P:--x,u:$N:--x"
for d in jobs workers handoff; do mkd "/srv/render-data/inbox/$d" 2750 "$I:$G"; acl "/srv/render-data/inbox/$d" "o::---" "g::r-x,o::---"; done
acl /srv/render-data/inbox/jobs "u:$P:r-x" "u:$P:r-x"                                   # r5.1 D1
mkd /srv/render-data/inbox/submissions 2750 "$P:$G"; acl /srv/render-data/inbox/submissions "o::---" "g::r-x,o::---"
mkd /srv/render-data/inbox/netcheck 2750 "$N:$G"
# Claude side
mkd /srv/agent 0711 root:root
mkd /srv/agent/home 0700 "$C:$C"
mkd /srv/agent/home/.claude 0700 "$C:$C"                  # fresh config; old ~/.claude is never copied
mkd /srv/agent/work 0700 "$C:$C"
mkd /srv/agent/outbox 2750 "$C:$P"; acl /srv/agent/outbox "o::---" "g::r-x,o::---"
for d in submissions bundles control handoff; do mkd "/srv/agent/outbox/$d" 2750 "$C:$P"; done
# service state and admin config
mkd /var/lib/render-publish 0700 "$P:$P"
mkd /etc/render 0755 root:root
say "NOTE: project files are not moved automatically; copy reviewed sources into /srv/agent/work yourself."
