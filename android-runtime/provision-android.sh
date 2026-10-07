#!/bin/bash
set -euo pipefail
# Creates a fresh Android instance. It NEVER copies account sessions from another device.
NAME=${1:?Usage: provision-android.sh NAME SLOT [MEMORY_MIB] [CPU_QUOTA]}
SLOT=${2:?Slot 1..32}
MEMORY=${3:-2048}
CPUS=${4:-1}
[[ "$NAME" =~ ^bf-[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo 'Invalid BrandFleet instance name' >&2; exit 2; }
[[ "$SLOT" =~ ^[0-9]+$ ]] && ((SLOT >= 1 && SLOT <= 32)) || exit 2
[[ "$MEMORY" =~ ^[0-9]+$ ]] && ((MEMORY >= 1024 && MEMORY <= 32768)) || exit 2
[[ "$CPUS" =~ ^[0-9]+$ ]] && ((CPUS >= 1 && CPUS <= 16)) || exit 2
SLOT_PAD=$(printf '%02d' "$SLOT")
IP="10.77.0.$((10 + SLOT))"
BASE=/opt/brandfleet/android
IMAGE=${BRANDFLEET_ANDROID_IMAGE:-docker.io/redroid/redroid@sha256:0a611199ba2e0b5d60af39b3327a517f6407231f4352114ed3bd3cbfe2be69aa}
DEFAULT_IMAGE=docker.io/redroid/redroid@sha256:0a611199ba2e0b5d60af39b3327a517f6407231f4352114ed3bd3cbfe2be69aa
[[ "$IMAGE" =~ ^docker\.io/redroid/redroid@sha256:[0-9a-f]{64}$ ]] || { echo 'Use an immutable official Redroid image digest.' >&2; exit 2; }
# A new OS is prepared offline and started through its qualification helper.
# Do not let a direct provisioner invocation bypass that acceptance gate.
if [[ "$IMAGE" != "$DEFAULT_IMAGE" && "${BRANDFLEET_ANDROID_NO_START:-0}" != 1 ]]; then
  echo 'Unqualified Android image: prepare with BRANDFLEET_ANDROID_NO_START=1 before a canary boot.' >&2
  exit 2
fi
CONTAINER="/var/lib/lxc/$NAME"
DATA="$BASE/data/$NAME"
test ! -e "$CONTAINER" || { echo 'Container already exists; refusing to replace it.' >&2; exit 1; }
test ! -e "$DATA" || { echo 'Persistent data already exists; refusing to replace it.' >&2; exit 1; }
grep -Rl "slot=$SLOT$" "$BASE/data"/*/brandfleet/metadata.env 2>/dev/null | grep -q . && { echo 'Binder/IP slot already reserved.' >&2; exit 1; }
test -e "/dev/b${SLOT_PAD}b"
grep -q "^lxc-brandfleet-android (enforce)" /sys/kernel/security/apparmor/profiles || { echo "Android containment AppArmor profile must be loaded first." >&2; exit 1; }
if [[ "$IMAGE" == "$DEFAULT_IMAGE" && -z "${BRANDFLEET_ANDROID_OCI_DIR:-}" ]] && ! test -f "$BASE/images/redroid14/index.json"; then
  skopeo inspect --override-arch amd64 "docker://$IMAGE" > "$BASE/images/redroid14-source.json"
  DIGEST=$(jq -r .Digest "$BASE/images/redroid14-source.json")
  NAME_REF=$(jq -r .Name "$BASE/images/redroid14-source.json")
  skopeo copy --override-arch amd64 "docker://$NAME_REF@$DIGEST" "oci:$BASE/images/redroid14:base"
fi
IMAGE_ARGS=(--image "$IMAGE" --format tsv)
if [[ -n "${BRANDFLEET_ANDROID_OCI_DIR:-}" ]]; then
  IMAGE_ARGS+=(--oci-dir "$BRANDFLEET_ANDROID_OCI_DIR")
fi
# Existing redroid14 cache cannot silently satisfy a requested newer image.
# The verifier binds the requested registry pin to the actual OCI config/layers.
IMAGE_INFO=$(python3 /opt/brandfleet/android-runtime/image-cache.py "${IMAGE_ARGS[@]}")
IFS=$'\t' read -r IMAGE_DIR IMAGE_DIGEST OCI_MANIFEST_DIGEST <<< "$IMAGE_INFO"
install -d -m 0755 "$CONTAINER" "$DATA/brandfleet/bin" "$DATA/brandfleet/www"
umoci unpack --image "$IMAGE_DIR:base" "$CONTAINER/bundle"
mv "$CONTAINER/bundle/rootfs" "$CONTAINER/rootfs"
ROOTFS="$CONTAINER/rootfs"
# umoci deliberately unpacks the root directory0700; Android service UIDs need traversal.
chmod 0755 "$ROOTFS"
test -x "$ROOTFS/system/bin/init"
# Official Redroid LXC workaround: Android must retain its container veth settings.
if test -e "$ROOTFS/vendor/bin/ipconfigstore"; then
  mv "$ROOTFS/vendor/bin/ipconfigstore" "$ROOTFS/vendor/bin/ipconfigstore.disabled-brandfleet"
fi
install -m 0755 /bin/busybox "$DATA/brandfleet/bin/busybox"
cat > "$DATA/brandfleet/metadata.env" <<EOF
name=$NAME
slot=$SLOT
ip=$IP
memory_mib=$MEMORY
cpus=$CPUS
EOF
cat > "$DATA/brandfleet/www/index.html" <<EOF
<!doctype html><html lang="en"><meta charset="utf-8"><title>$NAME</title><body><h1>$NAME</h1><p>This website is served by a process inside this Android LXC.</p><p>Private address: $IP. Persistent website data: /data/brandfleet/www.</p></body></html>
EOF
cat > "$DATA/brandfleet/webserver.sh" <<'EOF'
#!/system/bin/sh
exec /data/brandfleet/bin/busybox httpd -f -p 8080 -h /data/brandfleet/www
EOF
chmod 0755 "$DATA/brandfleet/webserver.sh"
install -d "$ROOTFS/system/etc/init"
cat > "$ROOTFS/system/etc/init/brandfleet.rc" <<'EOF'
service brandfleet-web /system/bin/sh /data/brandfleet/webserver.sh
    class late_start
    user root
    group root inet
    seclabel u:r:su:s0
    disabled

on property:sys.boot_completed=1
    start brandfleet-web
EOF
install -d -m 0755 "$DATA/brandfleet/kernel"
printf '2\n' > "$DATA/brandfleet/kernel/kptr_restrict"
printf '1\n' > "$DATA/brandfleet/kernel/unprivileged_bpf_disabled"
printf '2\n' > "$DATA/brandfleet/kernel/perf_event_paranoid"
printf '32\n' > "$DATA/brandfleet/kernel/mmap_rnd_bits"
printf '16\n' > "$DATA/brandfleet/kernel/mmap_rnd_compat_bits"
chmod 0600 "$DATA/brandfleet/kernel/"*
cat > "$CONTAINER/config" <<EOF
lxc.uts.name = $NAME
lxc.rootfs.path = dir:$ROOTFS
lxc.init.cmd = /init androidboot.hardware=redroid androidboot.redroid_gpu_mode=guest androidboot.use_memfd=1 androidboot.redroid_width=720 androidboot.redroid_height=1280 androidboot.redroid_fps=15 ro.secure=0
lxc.apparmor.profile = lxc-brandfleet-android
lxc.seccomp.profile = /usr/share/lxc/config/common.seccomp
lxc.cap.drop = sys_module sys_rawio sys_time sys_boot mac_admin mac_override
lxc.autodev = 1
lxc.autodev.tmpfs.size = 25000000
lxc.mount.auto = proc:rw sys:ro cgroup:rw
lxc.mount.entry = $DATA/brandfleet/kernel/kptr_restrict proc/sys/kernel/kptr_restrict none bind,create=file 0 0
lxc.mount.entry = $DATA/brandfleet/kernel/mmap_rnd_bits proc/sys/vm/mmap_rnd_bits none bind,create=file 0 0
lxc.mount.entry = $DATA/brandfleet/kernel/mmap_rnd_compat_bits proc/sys/vm/mmap_rnd_compat_bits none bind,create=file 0 0
lxc.mount.entry = $DATA/brandfleet/kernel/unprivileged_bpf_disabled proc/sys/kernel/unprivileged_bpf_disabled none bind,create=file 0 0
lxc.mount.entry = $DATA/brandfleet/kernel/perf_event_paranoid proc/sys/kernel/perf_event_paranoid none bind,create=file 0 0
lxc.mount.entry = /dev/kmsg dev/kmsg none bind,create=file 0 0
lxc.mount.entry = /dev/net/tun dev/tun none bind,create=file 0 0
lxc.mount.entry = /dev/null proc/sysrq-trigger none bind,ro,optional 0 0
lxc.mount.entry = /dev/null proc/kcore none bind,ro,optional 0 0
lxc.mount.entry = /dev/null proc/keys none bind,ro,optional 0 0
lxc.mount.entry = $DATA data none bind,create=dir 0 0
lxc.mount.entry = /dev/b${SLOT_PAD}b dev/binder none bind,create=file 0 0
lxc.mount.entry = /dev/b${SLOT_PAD}h dev/hwbinder none bind,create=file 0 0
lxc.mount.entry = /dev/b${SLOT_PAD}v dev/vndbinder none bind,create=file 0 0
lxc.net.0.type = veth
lxc.net.0.link = bfandroid
lxc.net.0.flags = up
lxc.net.0.name = eth0
lxc.net.0.hwaddr = 02:bf:aa:00:00:$(printf '%02x' "$SLOT")
lxc.net.0.ipv4.address = $IP/24
lxc.net.0.ipv4.gateway = 10.77.0.1
lxc.cgroup2.memory.max = $((MEMORY * 1024 * 1024))
lxc.cgroup2.memory.swap.max = 0
lxc.cgroup2.cpu.max = $((CPUS * 100000)) 100000
lxc.cgroup2.pids.max = 4096
lxc.start.auto = 0
lxc.start.order = $SLOT
EOF
python3 /opt/brandfleet/android-runtime/devices-config.py "$SLOT" >> "$CONTAINER/config"
cat > "$CONTAINER/brandfleet.json.tmp" <<EOF
{"name":"$NAME","slot":$SLOT,"ip":"$IP","memoryMiB":$MEMORY,"cpuQuota":$CPUS,"image":"$IMAGE","imageDigest":"$IMAGE_DIGEST","ociManifestDigest":"$OCI_MANIFEST_DIGEST","containmentVersion":2,"autostart":false,"status":"provisioned","webPort":8080,"adbPort":5555}
EOF
mv "$CONTAINER/brandfleet.json.tmp" "$CONTAINER/brandfleet.json"
if [[ "${BRANDFLEET_ANDROID_NO_START:-0}" != 1 ]]; then
  lxc-start -n "$NAME" -d -l INFO -o "$CONTAINER/start.log"
fi
printf '%s created at %s; web :8080; private ADB :5555\n' "$NAME" "$IP"
