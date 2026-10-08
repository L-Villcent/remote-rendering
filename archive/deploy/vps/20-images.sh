#!/bin/bash
# Fixed-capacity, fully preallocated loop images (security-boundary 4.1).
# Never sparse: fallocate (not truncate), mkfs -E nodiscard, mount nodiscard,
# fstrim limited to /, daily allocation check that only alerts.
. "$(dirname "$0")/lib.sh"; need_root
IMAGES=("up:$IMG_UP_GIB:/srv/render/up:noexec" "down:$IMG_DOWN_GIB:/srv/render/down:noexec"
        "data:$IMG_DATA_GIB:/srv/render-data:noexec" "agent:$IMG_AGENT_GIB:/srv/agent:")
if [ "$APPLY" = 1 ]; then "$DEPLOY_DIR/00-preflight.sh" >/dev/null || die "preflight failed (root reserve or packages)"; fi
mkd "$IMAGE_DIR" 0700 root:root
mkd /srv/render 0755 root:root
fstab_block=""
for spec in "${IMAGES[@]}"; do
  IFS=: read -r name gib mp extra <<<"$spec"
  img="$IMAGE_DIR/$name.img"
  if [ "$APPLY" = 1 ]; then
    [ -e "$img" ] || fallocate -l "${gib}G" "$img"
    read -r blocks bsize <<<"$(stat -c '%b %B' "$img")"
    [ $((blocks * bsize)) -ge $((gib * 1024 * 1024 * 1024)) ] || die "$name image is not fully allocated"
    blkid -p "$img" >/dev/null 2>&1 || mkfs.ext4 -q -E nodiscard -m 0 -L "render-$name" "$img"
  else
    run fallocate -l "${gib}G" "$img"
    say "+ verify allocated blocks of $img >= ${gib} GiB (abort otherwise)"
    run mkfs.ext4 -q -E nodiscard -m 0 -L "render-$name" "$img"
  fi
  mkd "$mp" 0755 root:root
  opts="loop,nodiscard,nodev,nosuid,acl${extra:+,$extra}"
  fstab_block+="$img $mp ext4 $opts 0 2"$'\n'
done
if [ "$APPLY" = 1 ]; then
  sed -i '/^# render-images BEGIN$/,/^# render-images END$/d' /etc/fstab
  printf '# render-images BEGIN\n%s# render-images END\n' "$fstab_block" >>/etc/fstab
  for spec in "${IMAGES[@]}"; do IFS=: read -r _ _ mp _ <<<"$spec"; mountpoint -q "$mp" || mount "$mp"; done
else
  say "+ replace '# render-images' block in /etc/fstab with:"; printf '%s' "$fstab_block" | sed 's/^/    /'
  say "+ mount the four mount points"
fi
mkd /etc/systemd/system/fstrim.service.d 0755 root:root
put /etc/systemd/system/fstrim.service.d/render.conf 0644 root:root <<'UNIT'
# Only trim the root filesystem: trimming the loop-mounted images would punch
# holes into their backing files and make them sparse again.
[Service]
ExecStart=
ExecStart=/sbin/fstrim --verbose --quiet-unsupported /
UNIT
mkd /usr/local/lib/render 0755 root:root
put /usr/local/lib/render/image-check 0755 root:root <<CHECK
#!/bin/bash
# Daily: report allocation gaps and root free space. Alerts only - never refills.
set -u
rc=0
for img in $IMAGE_DIR/*.img; do
  read -r blocks bsize size <<<"\$(stat -c '%b %B %s' "\$img")"
  if [ \$((blocks * bsize)) -lt "\$size" ]; then echo "ALERT allocation gap in \$(basename "\$img")"; rc=1; fi
done
read -r rsize ravail < <(df -B1 --output=size,avail / | tail -1)
echo "INFO root_avail_gib=\$((ravail >> 30)) root_size_gib=\$((rsize >> 30))"
exit \$rc
CHECK
put /etc/systemd/system/render-image-check.service 0644 root:root <<'UNIT'
[Unit]
Description=Render image allocation check (alert only)
[Service]
Type=oneshot
ExecStart=/usr/local/lib/render/image-check
UNIT
put /etc/systemd/system/render-image-check.timer 0644 root:root <<'UNIT'
[Unit]
Description=Daily render image allocation check
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
UNIT
run systemctl daemon-reload
run systemctl enable --now render-image-check.timer
