#!/bin/bash
set -euo pipefail
NAME=${1:?Usage stop-android.sh bf-ID}
[[ "$NAME" =~ ^bf-[a-z0-9][a-z0-9-]{0,40}$ ]] || exit 2
test -f "/var/lib/lxc/$NAME/brandfleet.json"
if [[ "$(lxc-info -n "$NAME" -sH)" != RUNNING ]]; then exit 0; fi
python3 /opt/brandfleet/android-runtime/native-pre-stop.py before --id "$NAME"
# Android ctl.stop kills the service. TERM only a PID whose argv exactly matches
# an owned launcher. Mark it oneshot first so init cannot restart it during stop.
for SERVICE in brandfleet-native brandfleet-node; do
  PID=$(lxc-attach -n "$NAME" -- /system/bin/getprop "init.svc_debug_pid.$SERVICE" 2>/dev/null || true)
  if [[ "$PID" =~ ^[0-9]+$ ]] && ((PID > 1)); then
    COMMAND=$(lxc-attach -n "$NAME" -- /system/bin/cat "/proc/$PID/cmdline" 2>/dev/null | tr '\0' ' ' || true)
    VERIFIED=0
    if [[ "$SERVICE" == brandfleet-native ]] && [[ "$COMMAND" == '/usr/bin/python3 /srv/brandfleet/native-supervisor.py '* ]]; then VERIFIED=1; fi
    if [[ "$SERVICE" == brandfleet-node ]] && [[ "$COMMAND" == '/usr/bin/node /srv/brandfleet/server.js '* ]]; then VERIFIED=1; fi
    if ((VERIFIED)); then
      lxc-attach -n "$NAME" -- /system/bin/setprop ctl.oneshot_on "$SERVICE" >/dev/null 2>&1 || true
      lxc-attach -n "$NAME" -- /system/bin/kill -TERM "$PID" >/dev/null 2>&1 || true
      for n in $(seq 1 30); do
        STATE=$(lxc-attach -n "$NAME" -- /system/bin/getprop "init.svc.$SERVICE" 2>/dev/null || true)
        [[ "$STATE" != running ]] && break
        sleep 1
      done
    fi
  fi
  lxc-attach -n "$NAME" -- /system/bin/setprop ctl.stop "$SERVICE" >/dev/null 2>&1 || true
done
lxc-attach -n "$NAME" -- /system/bin/setprop ctl.stop brandfleet-web >/dev/null 2>&1 || true
lxc-stop -n "$NAME" -k

python3 /opt/brandfleet/android-runtime/native-pre-stop.py after --id "$NAME"
