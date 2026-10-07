#!/bin/bash
set -euo pipefail
SCRIPTS=/opt/brandfleet/android-runtime
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/opt/brandfleet/backups/android-containment-$STAMP
install -d -m 0700 "$BACKUP"
install -m 0644 "$SCRIPTS/brandfleet-android.apparmor" /etc/apparmor.d/brandfleet-android
apparmor_parser -r /etc/apparmor.d/brandfleet-android
python3 - "$BACKUP" <<'PY'
from pathlib import Path
import json,shutil,sys
backup=Path(sys.argv[1])
for p in Path('/var/lib/lxc').glob('bf-*/brandfleet.json'):
    d=json.loads(p.read_text())
    if not d.get('image','').startswith('docker.io/redroid/') or not d.get('imageDigest'):continue
    name=p.parent.name
    subprocess=__import__('subprocess')
    state=subprocess.run(['lxc-info','-n',name,'-sH'],capture_output=True,text=True,check=True).stdout.strip()
    if state=='RUNNING':raise SystemExit('Stop all Android instances before applying containment: '+name)
    config=p.parent/'config';shutil.copy2(config,backup/(name+'.config'))
    lines=[x for x in config.read_text().splitlines() if not any(x.startswith(k) for k in ['lxc.apparmor.profile','lxc.cap.drop','lxc.mount.auto','lxc.start.auto','lxc.cgroup2.devices.','lxc.seccomp.profile'])]
    lines=[x for x in lines if not any(v in x for v in ['proc/sysrq-trigger','proc/kcore','proc/keys'])]
    lines+=['lxc.apparmor.profile = lxc-brandfleet-android','lxc.seccomp.profile = /usr/share/lxc/config/common.seccomp','lxc.cap.drop = sys_module sys_rawio sys_time sys_boot mac_admin mac_override','lxc.mount.auto = proc:rw sys:ro cgroup:rw','lxc.mount.entry = /dev/null proc/sysrq-trigger none bind,ro,optional 0 0','lxc.mount.entry = /dev/null proc/kcore none bind,ro,optional 0 0','lxc.mount.entry = /dev/null proc/keys none bind,ro,optional 0 0','lxc.start.auto = 0']
    lines+=subprocess.check_output(['/usr/bin/python3','/opt/brandfleet/android-runtime/devices-config.py',str(d['slot'])],text=True).splitlines()
    config.write_text('\n'.join(lines)+'\n')
    d['containmentVersion']=2;d['autostart']=False;p.write_text(json.dumps(d,indent=2)+'\n')
PY
printf 'AppArmor protection loaded; stopped Android configurations contained; autostart disabled.\n'
