#!/bin/bash
set -euo pipefail
test "$(id -u)" = 0
test -f /etc/debian_version
# This privileged Android runtime belongs only in its dedicated VM guest.
test "${1:-}" = --isolated-pool || { echo "Pass --isolated-pool only on the dedicated Android VM." >&2; exit 1; }
BASE=$(cd "$(dirname "$0")" && pwd)
install -d -m 0755 /usr/local/lib/brandfleet /opt/brandfleet/android/images /opt/brandfleet/android/data /opt/brandfleet/android/archives
install -m 0755 "$BASE/android-network.sh" /usr/local/lib/brandfleet/android-network.sh
install -m 0644 "$BASE/brandfleet-android-network.service" /etc/systemd/system/brandfleet-android-network.service
# Debian's stock kernel has no binderfs. Preallocate one isolated triple per slot.
DEVICES=""
for n in $(seq -w 1 32); do
  DEVICES+="b${n}b,b${n}h,b${n}v,"
done
DEVICES=${DEVICES%,}
if lsmod | awk '{print $1}' | grep -qx binder_linux; then
  test -e /dev/b01b || { echo 'Binder already loaded with another layout; refusing to unload it.' >&2; exit 1; }
else
  modprobe binder_linux devices="$DEVICES"
fi
printf 'options binder_linux devices=%s\n' "$DEVICES" > /etc/modprobe.d/brandfleet-android.conf
printf 'binder_linux\n' > /etc/modules-load.d/brandfleet-android.conf
modprobe tun
printf 'tun\n' > /etc/modules-load.d/brandfleet-tun.conf
chmod 0666 /dev/b[0-9][0-9][bhv]
printf 'SUBSYSTEM=="misc", KERNEL=="b[0-9][0-9][bhv]", MODE="0666"\n' > /etc/udev/rules.d/99-brandfleet-binder.rules
# Private DHCP and DNS allow Android EthernetManager to obtain a stable address.
install -d /etc/brandfleet
{
  printf 'interface=bfandroid\nbind-interfaces\nlisten-address=10.77.0.1\nno-resolv\nserver=1.1.1.1\nserver=9.9.9.9\ndhcp-authoritative\ndhcp-range=10.77.0.0,static,255.255.255.0,12h\ndhcp-option=3,10.77.0.1\ndhcp-option=6,10.77.0.1\ndhcp-leasefile=/opt/brandfleet/android/dhcp.leases\n'
  for slot in $(seq 1 32); do
    printf 'dhcp-host=02:bf:aa:00:00:%02x,10.77.0.%d,12h\n' "$slot" "$((slot+10))"
  done
} > /etc/brandfleet/android-dnsmasq.conf
install -m 0644 "$BASE/brandfleet-android-dhcp.service" /etc/systemd/system/brandfleet-android-dhcp.service
dnsmasq --test --conf-file=/etc/brandfleet/android-dnsmasq.conf
systemctl daemon-reload
systemctl enable --now brandfleet-android-network.service
systemctl enable --now brandfleet-android-dhcp.service

# Runtime containment and owned boot ordering; stock auto-start bypasses app stop hooks.
install -d /opt/brandfleet/android-runtime
install -m 0755 "$BASE"/*.sh "$BASE/brandfleet-android.py" "$BASE/pool-lifecycle.py" "$BASE/reconnect-android.py" "$BASE/screen-gateway.py" "$BASE/devices-config.py" "$BASE/image-cache.py" "$BASE/native-pre-stop.py" "$BASE/social-evidence.py" /opt/brandfleet/android-runtime/
install -m 0644 "$BASE/linux-server.js" /opt/brandfleet/android-runtime/linux-server.js
install -m 0644 "$BASE/brandfleet-android.apparmor" /opt/brandfleet/android-runtime/brandfleet-android.apparmor
ln -sfn /opt/brandfleet/android-runtime/brandfleet-android.py /opt/brandfleet/android/brandfleet-android.py
/opt/brandfleet/android-runtime/harden-runtime.sh
install -m 0644 "$BASE/brandfleet-android-pool.service" /etc/systemd/system/brandfleet-android-pool.service
install -m 0644 "$BASE/brandfleet-android-screen.service" "$BASE/brandfleet-android-reconnect.service" "$BASE/brandfleet-android-reconnect.timer" /etc/systemd/system/
systemctl disable lxc.service lxc-net.service
systemctl mask lxc.service lxc-net.service
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/brandfleet-android-pool.service
# Default instance metadata is autostart=false; existing app cutover owns opt-in.
systemctl enable brandfleet-android-pool.service
systemctl enable --now brandfleet-android-screen.service brandfleet-android-reconnect.timer
