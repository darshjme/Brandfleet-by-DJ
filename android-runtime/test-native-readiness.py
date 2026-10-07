#!/usr/bin/python3
"""Regression: healthy pilot listener must not hide dead native app processes."""
import datetime,importlib.util,json,pathlib,subprocess,tempfile
spec=importlib.util.spec_from_file_location('runtime',str(pathlib.Path(__file__).with_name('brandfleet-android.py')));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Response:
 status=200
 def __enter__(self):return self
 def __exit__(self,*_):pass
m.run=lambda args,**kw:subprocess.CompletedProcess(args,0,'RUNNING'if'-sH'in args else '1' if any(a.endswith('/getprop')for a in args) else'','')
class FixtureOpener:
 def open(self, *args, **kwargs): return Response()
m.urllib.request.build_opener=lambda *args, **kwargs:FixtureOpener()
with tempfile.TemporaryDirectory()as temporary:
 p=pathlib.Path(temporary);m.BASE=p/'android';m.LXC=p/'lxc';ID='bf-fixture';root=m.BASE/'data'/ID/'brandfleet/debian';(root/'etc/brandfleet').mkdir(parents=True);(root/'run/brandfleet').mkdir(parents=True);(m.LXC/ID).mkdir(parents=True)
 (m.LXC/ID/'brandfleet.json').write_text(json.dumps({'name':ID,'ip':'10.77.0.250','nativeRuntime':True,'requiresLinux':True,'webPort':8080,'linuxPort':8080,'linuxHealthPath':'/'}))
 def state(services,expected,age=0,stopping=False):
  (root/'etc/brandfleet/native-services.json').write_text(json.dumps({'services':expected}));(root/'run/brandfleet/native-status.json').write_text(json.dumps({'at':(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=age)).isoformat(),'stopping':stopping,'services':services}));return m.status(ID)
 config=[{'id':'native-web'}];dead=[{'id':'native-web','pid':None,'running':False,'restarts':9}];alive=[{'id':'native-web','pid':42,'running':True,'restarts':0}]
 checks=[]
 s=state(dead,config);assert s['webHealthy']and s['linuxHealthy']and not s['ready'];checks.append('HTTP200 pilot cannot conceal dead native server')
 assert state(alive,config)['ready'];checks.append('Matching fresh enabled native app accepted')
 assert not state(alive,config,age=30)['ready'];checks.append('Stale native process record rejected')
 assert not state([],[])['ready'];checks.append('Empty restore-phase service list rejected')
 assert not state(alive,config,stopping=True)['ready'];checks.append('Stopping supervisor rejected')
 bot={'id':'bot','enabled':False};assert state(alive+[{'id':'bot','pid':None,'running':False}],config+[bot])['ready'];checks.append('Explicitly disabled worker allowed without start')
 assert not state(alive+[{'id':'bot','pid':43,'running':True}],config+[bot])['ready'];checks.append('Unintentionally running disabled worker rejected')
 assert not state([{'id':'unrelated','pid':42,'running':True}],config)['ready'];checks.append('Unrelated native process identity rejected')
 print(json.dumps({'status':'passed','checks':checks,'checksPassed':len(checks)}))
