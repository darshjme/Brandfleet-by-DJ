#!/usr/bin/python3
"""Fixed optional queue drain before any platform-owned process is signalled."""
import argparse,json,pathlib,re,subprocess,sys
LXC=pathlib.Path('/var/lib/lxc');DATA=pathlib.Path('/opt/brandfleet/android/data')
def apply(ident,phase):
 if not re.fullmatch(r'bf-[a-z0-9][a-z0-9-]{0,40}',ident):raise ValueError('Invalid managed ID')
 meta=json.loads((LXC/ident/'brandfleet.json').read_text())
 if meta.get('name')!=ident:raise ValueError('Ownership mismatch')
 hook=meta.get('preStopHook')
 if hook is None:return {'hook':None,'allowed':True}
 if hook!='freescout-queue'or ident!='bf-freescout'or not meta.get('nativeRuntime'):raise ValueError('Unknown or wrongly owned pre-stop hook')
 if phase=='before':
  result=subprocess.run(['lxc-attach','-n',ident,'--','/data/brandfleet/bin/busybox','chroot','/data/brandfleet/debian','/usr/bin/python3','/srv/brandfleet/freescout-drain.py'],capture_output=True,text=True,timeout=25)
  try:proof=json.loads(result.stdout.strip())
  except (ValueError,TypeError):proof={}
  if result.returncode!=0 or proof.get('ok')is not True or proof.get('workersDrained')is not True or proof.get('reservedJobs')!=0 or proof.get('jobsKilled')is not False:
   raise RuntimeError('FreeScout stop refused: queue drain not proven; device and services remain running')
  return {'hook':hook,'allowed':True,'reservedJobs':0,'workersDrained':True,'jobsKilled':False}
 if subprocess.check_output(['lxc-info','-n',ident,'-sH'],text=True,timeout=5).strip()!='STOPPED':raise RuntimeError('Pause cleanup requires confirmed STOPPED')
 root=DATA/ident/'brandfleet/debian';pause=root/'etc/brandfleet/freescout-workers-paused'
 if pause.is_symlink()or any(p.is_symlink()for p in [root,root/'etc',root/'etc/brandfleet']):raise ValueError('Pause path symlink rejected')
 pause.unlink(missing_ok=True)
 return {'hook':hook,'completedStop':True,'pauseCleared':True}
def main():
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['before','after']);p.add_argument('--id',required=True);a=p.parse_args()
 try:print(json.dumps({'ok':True,'result':apply(a.id,a.phase)}))
 except(Exception):print(json.dumps({'ok':False,'stopRefused':True,'reason':'Managed pre-stop or cleanup guard refused'}));return 2
 return 0
if __name__=='__main__':raise SystemExit(main())
