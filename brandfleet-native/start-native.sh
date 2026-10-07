#!/system/bin/sh
ROOT=/data/brandfleet/debian
BUSY=/data/brandfleet/bin/busybox
# Device mounts are private to this LXC; no host block or GPU devices are granted.
for STACK in "$ROOT"/srv/brandfleet/stacks/*; do
    [ -d "$STACK" ] || continue
    "$BUSY" mkdir -p "$STACK/proc" "$STACK/dev/shm" "$STACK/etc"
    "$BUSY" mount -o bind /proc "$STACK/proc" || exit 1
    for DEV in null zero random urandom; do
        [ -e "$STACK/dev/$DEV" ] || "$BUSY" touch "$STACK/dev/$DEV"
        "$BUSY" mount -o bind "/dev/$DEV" "$STACK/dev/$DEV" || exit 1
    done
    "$BUSY" mount -t tmpfs -o size=64m,mode=1777 tmpfs "$STACK/dev/shm" || exit 1
    "$BUSY" printf 'nameserver 10.77.0.1\n' > "$STACK/etc/resolv.conf"
done
exec "$BUSY" chroot "$ROOT" /usr/bin/python3 /srv/brandfleet/native-supervisor.py
