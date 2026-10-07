#!/bin/bash
set -euo pipefail
NAME=${1:?Usage setup-linux-runtime.sh bf-ID}
[[ "$NAME" =~ ^bf-[a-z0-9][a-z0-9-]{0,40}$ ]] || exit 2
BASE=/opt/brandfleet/android
SCRIPTS=/opt/brandfleet/android-runtime
CONTAINER=/var/lib/lxc/$NAME
test -f "$CONTAINER/brandfleet.json"
ROOT="$BASE/data/$NAME/brandfleet/debian"
if ! test -x "$ROOT/usr/bin/node"; then
  debootstrap --variant=minbase --include=nodejs,ca-certificates,sqlite3,python3 trixie "$ROOT" https://deb.debian.org/debian
fi
if ! test -x "$ROOT/usr/bin/python3"; then
  chroot "$ROOT" apt-get update
  chroot "$ROOT" apt-get install -y --no-install-recommends python3
fi
printf 'nameserver 10.77.0.1\n' > "$ROOT/etc/resolv.conf"
install -d "$ROOT/srv/brandfleet"
install -m 0644 "$SCRIPTS/linux-server.js" "$ROOT/srv/brandfleet/server.js"
cat > "$BASE/data/$NAME/brandfleet/linuxserver.sh" <<'SH'
#!/system/bin/sh
exec /data/brandfleet/bin/busybox chroot /data/brandfleet/debian /usr/bin/node /srv/brandfleet/server.js
SH
chmod 0755 "$BASE/data/$NAME/brandfleet/linuxserver.sh"
# Android init parses /system/etc/init during early boot before /data mounts.
# Store the declaration in the pinned rootfs, executable and DB remain persistent.
cat > "$CONTAINER/rootfs/system/etc/init/brandfleet-linux.rc" <<'RC'
service brandfleet-node /system/bin/sh /data/brandfleet/linuxserver.sh
    class late_start
    user root
    group root inet
    seclabel u:r:su:s0
    disabled

on property:sys.boot_completed=1
    start brandfleet-node
RC
python3 - "$CONTAINER/brandfleet.json" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);d=json.loads(p.read_text());d.update(linuxRuntime='Debian13/Node20/SQLite',linuxPort=8081,requiresLinux=True)
tmp=p.with_suffix('.json.tmp');tmp.write_text(json.dumps(d,indent=2)+'\n');tmp.replace(p)
PY
printf 'Linux userspace prepared. Restart Android instance to activate Node service.\n'
