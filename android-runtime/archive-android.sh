#!/bin/bash
set -euo pipefail
NAME=${1:?Usage: archive-android.sh NAME}
[[ "$NAME" =~ ^bf-[a-z0-9][a-z0-9-]{0,40}$ ]] || exit 2
BASE=/opt/brandfleet/android
test -f "/var/lib/lxc/$NAME/brandfleet.json"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE="$BASE/archives/$NAME-$STAMP"
install -d -m 0700 "$ARCHIVE"
if lxc-info -n "$NAME" -sH | grep -qx RUNNING; then /opt/brandfleet/android-runtime/stop-android.sh "$NAME"; fi
tar --xattrs --acls --numeric-owner -czf "$ARCHIVE/data.tar.gz" -C "$BASE/data" "$NAME"
cp "/var/lib/lxc/$NAME/config" "/var/lib/lxc/$NAME/brandfleet.json" "$ARCHIVE/"
sha256sum "$ARCHIVE/data.tar.gz" > "$ARCHIVE/data.sha256"
sha256sum --check "$ARCHIVE/data.sha256" >&2
tar -tzf "$ARCHIVE/data.tar.gz" >/dev/null
# Retain the complete stopped environment; a later explicit purge may free disk.
sed -i 's/^lxc.start.auto = .*/lxc.start.auto = 0/' "/var/lib/lxc/$NAME/config"
python3 - "/var/lib/lxc/$NAME/brandfleet.json" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);data=json.loads(p.read_text());data['autostart']=False
tmp=p.with_suffix('.json.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(p)
PY
printf '%s\n' "$ARCHIVE"
