#!/usr/bin/python3
"""Host fixed stdin/stdout adapter; no client-controlled argv or shell execution."""
import json,os,subprocess,sys
from pathlib import Path
if os.geteuid()!=0 or not Path('/var/lib/lxc/bf-services/rootfs').is_dir():raise SystemExit('Restricted to existing ingress native-services host')
data=sys.stdin.buffer.read(65537)
if len(data)>65536:print(json.dumps({'ok':False,'error':'request too large'}));raise SystemExit(2)
try:
 request=json.loads(data)
 if not isinstance(request,dict):raise ValueError()
except Exception:print(json.dumps({'ok':False,'error':'valid JSON object required'}));raise SystemExit(2)
p=subprocess.run(['lxc-attach','-n','bf-services','--','python3','/opt/brandfleet/native-mail-admin-guest.py'],input=data,capture_output=True,timeout=90)
try:result=json.loads(p.stdout)
except Exception:result={'ok':False,'error':'private native backend unavailable'}
if request.get('action')=='list' and result.get('ok'):
 # Derive public readiness only from actual owned route, active ingress and frozen source.
 enabled=False
 try:
  private=Path('/opt/brandfleet/services/private');proof=json.loads((private/'mail-final-import-proof.json').read_text());assert proof.get('rehearsal_only')is False
  manifest=json.loads((Path(proof['source_snapshot'])/'manifest.json').read_text());assert manifest['source_writers_frozen']
  assert all(not json.loads(subprocess.check_output(['docker','inspect',name]))[0]['State']['Running'] for name in manifest['source_writers'])
  assert (private/'native-mail-ingress-proof.json').is_file()
  subprocess.run(['systemctl','is-active','brandfleet-services-network'],check=True,capture_output=True)
  route=Path('/data/coolify/proxy/dynamic/brandfleet-native-mail.yaml').read_text()
  assert 'mail.example.com' in route and 'http://10.77.2.50:8080' in route
  subprocess.run(['iptables','-t','nat','-C','PREROUTING','-i','eth0','-d','192.0.2.10/32','-p','tcp','-m','multiport','--dports','25,465,587,110,143,993,995,4190','-j','DNAT','--to-destination','10.77.2.50'],check=True,capture_output=True)
  enabled=True
 except Exception:pass
 result['public_mail_enabled']=enabled
print(json.dumps(result));raise SystemExit(0 if result.get('ok') else 2)
