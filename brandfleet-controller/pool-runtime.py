#!/usr/bin/python3
"""Private fixed destination adapter for the isolated Android VM."""
import sys,subprocess,shlex
ALLOWED={'status','host-status','create','start','stop','restart','archive','remove','clone','social-evidence'}
args=sys.argv[1:]
if not args or args[0] not in ALLOWED or len(args)>18 or any('\x00' in s or len(s)>300 for s in args):
 raise SystemExit('Invalid runtime operation')
if args == ['social-evidence','--json']:
 remote=shlex.join(['/usr/bin/python3','/opt/brandfleet/android-runtime/social-evidence.py','--json'])
 timeout=5
else:
 if args and args[0]=='social-evidence':raise SystemExit('Invalid evidence operation')
 remote=shlex.join(['/usr/bin/python3','/opt/brandfleet/android-runtime/brandfleet-android.py',*args])
 timeout=None
r=subprocess.run(['/usr/bin/ssh','-i','/etc/brandfleet/android-pool-key','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes','root@10.77.1.104',remote],timeout=timeout)
raise SystemExit(r.returncode)
