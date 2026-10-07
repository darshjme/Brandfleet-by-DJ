#!/usr/bin/python3
"""Launch an exact Linux userspace within its Android device; no Docker runtime."""
import json,os,re,sys
from pathlib import Path
if len(sys.argv)!=2 or not re.fullmatch('[a-z0-9][a-z0-9-]{0,40}',sys.argv[1]):raise SystemExit('Invalid native service')
manifest=Path('/etc/brandfleet/native-runtime-private.json')
if manifest.stat().st_mode & 0o077:raise SystemExit('Native runtime manifest permissions must be0600')
services=json.loads(manifest.read_text())['services']
service=next(s for s in services if s['id']==sys.argv[1])
root=Path(service['rootfs']).resolve()
base=Path('/srv/brandfleet/stacks').resolve()
if base not in root.parents or not root.is_dir():raise SystemExit('Native service outside owned stack path')
argv=service['argv']
if not argv or not isinstance(argv,list) or not all(isinstance(a,str) and '\x00' not in a for a in argv) or not argv[0].startswith('/'):raise SystemExit('Invalid native argv')
env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}
for key,value in service.get('env',{}).items():
 if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key) or not isinstance(value,str) or '\x00' in value:raise SystemExit('Invalid native environment')
 env[key]=value
os.chroot(root);os.chdir(service.get('cwd','/'))
os.setgroups(service.get('groups',[]));os.setgid(int(service.get('gid',0)));os.setuid(int(service.get('uid',0)))
os.execve(argv[0],argv,env)
