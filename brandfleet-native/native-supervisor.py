#!/usr/bin/python3
"""Root-owned native app manifests; graceful process groups with bounded restarts."""
import datetime,json,os,pwd,signal,subprocess,time
from pathlib import Path
CONFIG=Path('/etc/brandfleet/native-services.json')
STATE=Path('/run/brandfleet/native-status.json')
LOGS=Path('/var/log/brandfleet')
stop=False
services=[]
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def stopping(signum,frame):
    global stop
    stop=True
signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
STATE.parent.mkdir(parents=True,exist_ok=True);LOGS.mkdir(parents=True,exist_ok=True)
def publish():
    out={'at':now(),'stopping':stop,'services':[]}
    for s in services:
        p=s.get('process');out['services'].append({'id':s['id'],'pid':p.pid if p and p.poll() is None else None,'running':bool(p and p.poll() is None),'restarts':s['restarts'],'lastExit':s.get('lastExit'),'startedAt':s.get('startedAt')})
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(out)+'\n');tmp.replace(STATE)
config=json.loads(CONFIG.read_text())
if not isinstance(config.get('services'),list):raise SystemExit('Expected root-owned services manifest')
for item in config['services']:
    name=item.get('id','');argv=item.get('argv',[])
    if not name.replace('-','').isalnum() or not argv or not isinstance(argv,list) or not all(isinstance(a,str) for a in argv) or not argv[0].startswith('/'):
        raise SystemExit('Invalid native service')
    if item.get('stopSignal','SIGTERM') not in ['SIGTERM','SIGINT','SIGQUIT']:raise SystemExit('Unsupported stop signal')
    s=dict(item);s.update(restarts=0,nextStart=0,process=None);services.append(s)
def start(s):
    env=os.environ.copy()
    if s.get('envFile'):
        for line in Path(s['envFile']).read_text().splitlines():
            if line and not line.startswith('#'):
                key,value=line.split('=',1)
                if not key.replace('_','').isalnum():raise ValueError('Invalid environment key')
                env[key]=value
    env.update(s.get('env',{}))
    user=pwd.getpwnam(s.get('user','root'))
    def identity():os.initgroups(user.pw_name,user.pw_gid);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
    path=LOGS/(s['id']+'.log')
    if path.exists() and path.stat().st_size>10*1024*1024:path.replace(LOGS/(s['id']+'.previous.log'))
    log=path.open('ab',buffering=0)
    if 'logUid' in s:
        uid,gid=s['logUid'],s.get('logGid',s['logUid'])
        if not all(isinstance(x,int) and 0<=x<=65535 for x in [uid,gid]):raise ValueError('Invalid private log identity')
        os.fchown(log.fileno(),uid,gid);os.fchmod(log.fileno(),0o600)
    s['process']=subprocess.Popen(s['argv'],cwd=s.get('cwd','/'),env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=identity)
    log.close();s['startedAt']=now()
while not stop:
    for s in services:
        p=s['process']
        if p and p.poll() is not None:
            s['lastExit']=p.returncode;s['process']=None;s['restarts']+=1;s['nextStart']=time.monotonic()+min(60,2**min(s['restarts'],6))
        if s['process'] is None and time.monotonic()>=s['nextStart']:start(s)
    publish();time.sleep(1)
for s in reversed(services):
    p=s['process']
    if p and p.poll() is None:os.killpg(p.pid,getattr(signal,s.get('stopSignal','SIGTERM')))
deadline=time.monotonic()+25
while time.monotonic()<deadline and any(s['process'] and s['process'].poll() is None for s in services):
    publish();time.sleep(.25)
forced=[]
for s in services:
    p=s['process']
    if p and p.poll() is None:
        forced.append(s['id']);os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
publish();Path('/srv/brandfleet').mkdir(parents=True,exist_ok=True)
Path('/srv/brandfleet/native-stop.json').write_text(json.dumps({'at':now(),'graceful':not forced,'forced':forced})+'\n')
