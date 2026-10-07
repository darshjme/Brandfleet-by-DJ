import json,pathlib,runpy,tempfile,types,unittest.mock as mock
m=runpy.run_path(str(pathlib.Path(__file__).with_name('native-pre-stop.py')));f=m['apply'];g=f.__globals__;checks=[]
with tempfile.TemporaryDirectory()as d:
 root=pathlib.Path(d);g['LXC']=root/'lxc';g['DATA']=root/'data';id='bf-freescout';p=g['LXC']/id;p.mkdir(parents=True);pause=g['DATA']/id/'brandfleet/debian/etc/brandfleet/freescout-workers-paused';pause.parent.mkdir(parents=True);pause.write_text('paused')
 def meta(**x):(p/'brandfleet.json').write_text(json.dumps({'name':id,'nativeRuntime':True,**x}))
 def check(name,fn,refused=False):
  try:fn();assert not refused
  except (RuntimeError,ValueError,subprocess.TimeoutExpired):assert refused
  checks.append({'name':name,'passed':True})
 import subprocess
 meta();check('no hook does not invoke process',lambda:f(id,'before'))
 meta(preStopHook='freescout-queue')
 proof={'ok':True,'workersDrained':True,'reservedJobs':0,'jobsKilled':False}
 with mock.patch.object(g['subprocess'],'run',return_value=types.SimpleNamespace(returncode=0,stdout=json.dumps(proof)))as run:
  check('idle permits stop',lambda:f(id,'before'));assert run.call_args[0][0]==['lxc-attach','-n',id,'--','/data/brandfleet/bin/busybox','chroot','/data/brandfleet/debian','/usr/bin/python3','/srv/brandfleet/freescout-drain.py'];checks.append({'name':'only fixed helper argv invoked','passed':True})
 for name,row,code in [('busy',{'ok':False},2),('malformed',None,0),('reserved',dict(proof,reservedJobs=1),0),('killed',dict(proof,jobsKilled=True),0)]:
  with mock.patch.object(g['subprocess'],'run',return_value=types.SimpleNamespace(returncode=code,stdout='bad'if row is None else json.dumps(row))):check(name+' refuses before stop',lambda:f(id,'before'),True)
 with mock.patch.object(g['subprocess'],'run',side_effect=subprocess.TimeoutExpired('fixed-helper',25)):check('timeout refuses before stop',lambda:f(id,'before'),True)
 with mock.patch.object(g['subprocess'],'check_output',return_value='RUNNING\n'):check('running device retains pause',lambda:f(id,'after'),True);assert pause.exists()
 with mock.patch.object(g['subprocess'],'check_output',return_value='STOPPED\n'):check('confirmed stopped clears pause',lambda:f(id,'after'));assert not pause.exists()
 meta(preStopHook='untrusted-script');check('unknown hooks refuse',lambda:f(id,'before'),True)
 meta(preStopHook='freescout-queue',nativeRuntime=False);check('non native hook refuses',lambda:f(id,'before'),True)
 meta(preStopHook='freescout-queue');pause.symlink_to('/tmp/foreign');
 with mock.patch.object(g['subprocess'],'check_output',return_value='STOPPED\n'):check('symlink cleanup refuses',lambda:f(id,'after'),True)
print(json.dumps({'passed':len(checks)}))
