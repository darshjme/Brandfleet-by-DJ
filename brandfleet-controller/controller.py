#!/usr/bin/env python3
"""Private localhost control plane. Legacy production services remain read-only."""
import os,json,re,subprocess,threading,time,uuid,sqlite3,hmac,logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(os.getenv('BRANDFLEET_STATE','/var/lib/brandfleet'));ROOT.mkdir(parents=True,exist_ok=True)
TOKEN=os.environ['BRANDFLEET_CONTROLLER_TOKEN'];DB=ROOT/'jobs.sqlite';LOCK=threading.Lock();
RUNTIME=Path('/opt/brandfleet/android/brandfleet-android.py')
READY=Path('/etc/brandfleet/runtime-ready.json')
ROUTES=Path('/data/coolify/proxy/dynamic/brandfleet-apps.yaml')
DOMAIN_RE=re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
ID_RE=re.compile(r'bf-[a-z0-9][a-z0-9-]{0,40}')
def ready():
 try:return json.loads(READY.read_text()).get('validated') is True
 except (OSError,ValueError):return False
def now():return datetime.now(timezone.utc).isoformat()
def command(argv,timeout=25):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('Command failed; inspect private controller logs')
 return p.stdout
with sqlite3.connect(DB) as c:c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,brand_id TEXT,action TEXT,status TEXT,created TEXT,updated TEXT,message TEXT)')
def jobs():
 with sqlite3.connect(DB) as c:
  return [dict(zip(['id','requestId','brandId','action','status','createdAt','updatedAt','message'],r)) for r in c.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]
def update(j,status,msg):
 with sqlite3.connect(DB) as c:c.execute('UPDATE jobs SET status=?,updated=?,message=? WHERE id=?',(status,now(),msg[:600],j))
def newjob(rid,bid,act,argv):
 if not re.fullmatch('[a-f0-9]{32}',rid or ''):raise ValueError('Invalid operation request')
 with LOCK:
  with sqlite3.connect(DB) as c:
   found=c.execute('SELECT id FROM jobs WHERE request_id=?',(rid,)).fetchone()
   if found:return next(j for j in jobs() if j['id']==found[0])
   if c.execute("SELECT 1 FROM jobs WHERE status IN ('pending','running')").fetchone():raise ValueError('Another lifecycle operation is running')
   jid=uuid.uuid4().hex;c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(jid,rid,bid,act,'pending',now(),now(),'Queued'))
 def run():
  update(jid,'running','Working')
  try:
   command(argv,timeout=900)
   target=argv[argv.index('--new-id')+1] if '--new-id' in argv else bid
   if act in ('create','start','restart','clone'):
    deadline=time.monotonic()+180
    while True:
     state=next((a for a in android_inventory() if a.get('id',a.get('name'))==target),{})
     if state.get('ready'):break
     if time.monotonic()>=deadline:raise RuntimeError('Android boot or website readiness timed out')
     time.sleep(3)
   reconcile_routes()
   update(jid,'succeeded','Operation completed; refresh the environment status')
  except Exception:
   logging.exception('Lifecycle job %s failed',jid)
   update(jid,'failed','Operation failed; inspect the controller journal before retrying')
 threading.Thread(target=run,daemon=True).start()
 return next(j for j in jobs() if j['id']==jid)
def android_inventory(allow_cached=False,required=False):
 if not RUNTIME.exists():return []
 try:
  raw=json.loads(command(['/usr/bin/python3',str(RUNTIME),'status','--json'],timeout=5))
  result=raw.get('result',[]) if isinstance(raw,dict) else raw
  result=result if isinstance(result,list) else [result]
  cache=ROOT/'android-inventory-cache.json';temp=ROOT/('android-inventory-cache-'+uuid.uuid4().hex+'.tmp')
  temp.write_text(json.dumps({'at':time.time(),'apps':result}));temp.replace(cache)
  return result
 except Exception:
  if required:raise ValueError('A fresh Android inventory is required')
  if allow_cached:
   try:
    saved=json.loads((ROOT/'android-inventory-cache.json').read_text())
    if time.time()-saved['at']<300:return [dict(a,_inventoryStale=True) for a in saved['apps']]
   except (OSError,ValueError,KeyError,TypeError):pass
  return []
# Package results are dated rollout evidence, never an assertion of live package state.
SOCIAL_PACKAGES={'com.figma.mirror','com.whatsapp','com.twitter.android','com.linkedin.android','com.tailscale.ipn','com.instagram.android'}
SOCIAL_CACHE={'at':0,'value':None};SOCIAL_LOCK=threading.Lock()
def sanitize_package_evidence(raw):
 if not isinstance(raw,dict):raw={}
 verified=raw.get('lastVerified','')
 try:
  parsed=datetime.fromisoformat(verified.replace('Z','+00:00'))
  if parsed.tzinfo is None or parsed.timestamp()>time.time()+300:verified=''
 except (ValueError,AttributeError,TypeError):verified=''
 packages=[];seen=set()
 for p in raw.get('packages',[])[:20] if isinstance(raw.get('packages'),list) else []:
  if not isinstance(p,dict) or p.get('package') not in SOCIAL_PACKAGES or p['package'] in seen:continue
  seen.add(p['package'])
  number=lambda v: v if type(v) is int and 0<=v<2**53 else None
  clean=lambda v,n: v[:n] if isinstance(v,str) else ''
  packages.append({'app':clean(p.get('app'),80),'package':p['package'],'versionCode':number(p.get('versionCode')),'versionName':clean(p.get('versionName'),100),'uid':number(p.get('uid')),'installedAPKHashesVerified':p.get('installedAPKHashesVerified') is True,'forceStopped':p.get('forceStopped') is True,'publisherVerification':clean(p.get('publisherVerification'),250)})
 present=raw.get('evidencePresent') is True and bool(verified)
 accepted=present and raw.get('historicalEvidence') is True and raw.get('packageInstallationAccepted') is True and len(packages)==6 and all(p['installedAPKHashesVerified'] for p in packages)
 return {'evidencePresent':present,'packageInstallationAccepted':accepted,'lastVerified':verified,'packages':packages if present else [],'backendContinuity':raw.get('backendContinuity') is True,'accountLoginAttempted':raw.get('accountLoginAttempted') if type(raw.get('accountLoginAttempted')) is bool else None,'VPNActivated':raw.get('VPNActivated') if type(raw.get('VPNActivated')) is bool else None,'historicalEvidence':True}
def social_evidence_inventory():
 with SOCIAL_LOCK:
  if SOCIAL_CACHE['value'] is not None and time.monotonic()-SOCIAL_CACHE['at']<45:return SOCIAL_CACHE['value']
  try:
   raw=json.loads(command(['/usr/bin/python3',str(RUNTIME),'social-evidence','--json'],timeout=6))
   devices=raw.get('devices',[]) if isinstance(raw,dict) and raw.get('readOnly') is True else []
   value={d['id']:sanitize_package_evidence(d) for d in devices[:50] if isinstance(d,dict) and isinstance(d.get('id'),str) and ID_RE.fullmatch(d['id'])}
  except Exception:value={}
  SOCIAL_CACHE.update(at=time.monotonic(),value=value)
  return value
LAUNCH_APPS={'instagram':'com.instagram.android','linkedin':'com.linkedin.android','whatsapp':'com.whatsapp','x':'com.twitter.android','figma':'com.figma.mirror'}
class LaunchBusy(RuntimeError):pass
def launch_social(bid, app):
 if not isinstance(bid,str) or not ID_RE.fullmatch(bid) or not isinstance(app,str) or app not in LAUNCH_APPS:raise ValueError('Unsupported app or device')
 if not ready():raise RuntimeError('Android runtime is unavailable')
 # The fixed loopback tunnel reaches the owned pool gateway. It checks live ownership,
 # migrated state and process state; launch never invokes a website lifecycle action.
 request=Request('http://127.0.0.1:9002/launch/'+bid,data=json.dumps({'app':app}).encode(),headers={'Content-Type':'application/json'})
 try:
  with urlopen(request,timeout=25) as response:
   if response.status!=200:raise RuntimeError('App launch service unavailable')
   raw=json.loads(response.read(16385))
 except HTTPError as error:
  if error.code==400:raise ValueError('Unsupported launch request') from None
  if error.code==409:raise LaunchBusy('Device control is busy') from None
  raise RuntimeError('Device launch service unavailable') from None
 if not isinstance(raw,dict) or raw.get('ok') is not True or raw.get('app')!=app or raw.get('package')!=LAUNCH_APPS[app] or raw.get('viewerUrl')!='/android/view/'+bid:raise RuntimeError('Invalid launch service response')
 confirmed=raw.get('foregroundConfirmed') is True
 current=raw.get('foregroundPackage','')
 if not isinstance(current,str) or not re.fullmatch(r'[A-Za-z0-9_.]{0,200}',current):current=''
 if confirmed and current!=LAUNCH_APPS[app]:raise RuntimeError('Foreground confirmation mismatch')
 timestamp=raw.get('launchedAt','')
 try:
  parsed=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
  if parsed.tzinfo is None or abs(parsed.timestamp()-time.time())>60:raise ValueError('Stale launch confirmation')
 except (ValueError,AttributeError,TypeError):raise RuntimeError('Invalid launch confirmation') from None
 return {'ok':True,'app':app,'package':LAUNCH_APPS[app],'launchedAt':timestamp,'foregroundConfirmed':confirmed,'foregroundPackage':current,'viewerUrl':'/android/view/'+bid}
def pool_status():
 raw=json.loads(command(['/usr/bin/python3',str(RUNTIME),'host-status','--json'],timeout=3))
 result=raw.get('result',{}) if isinstance(raw,dict) else {}
 if not isinstance(result,dict) or not result.get('memoryAvailableBytes'):raise ValueError('Android pool unavailable')
 return result
def check_capacity():
 apps=[a for a in android_inventory(required=True) if a.get('retired') is not True];host=pool_status()
 assigned=sum(int(a.get('memoryMiB',4096)) for a in apps)
 budget=int(host.get('assignedBudgetMiB',24576))
 if len(apps)>=int(host.get('maxInstances',12)) or assigned+2048>budget or int(host['memoryAvailableBytes'])<5*1024**3:
  raise ValueError('Android pool capacity reached; preserve the guest memory reserve')
def domain_in_use(domain):
 if domain in ('fleet.example.com',):return True
 if any(domain in b.get('domains',[b.get('domain')]) for b in inventory()['brands']):return True
 for p in ROUTES.parent.glob('*'):
  if p.is_file() and p.suffix in ('.yaml','.yml','.json'):
   try:
    if domain in re.findall(r'Host\(`([^`]+)`\)',p.read_text()):return True
   except (OSError,UnicodeError):pass
 return False
def reconcile_routes():
 """Publish owned app routes, with original routes retained for explicit rollback."""
 apps=[a for a in android_inventory(required=True) if a.get('retired') is not True and a.get('domain') and (a.get('migrated') or (a.get('ready') and not a.get('nativeRuntime')))]
 routers={};services={};middlewares={'brandfleet-redirect':{'redirectScheme':{'scheme':'https','permanent':True}}}
 for a in apps:
  aid=a.get('id',a.get('name',''));domain=a['domain'];ip=a.get('ip','')
  if not ID_RE.fullmatch(aid) or not DOMAIN_RE.fullmatch(domain) or not re.fullmatch(r'10\.77\.0\.\d{1,3}',ip):continue
  domains=[domain]+[x for x in a.get('aliases',[]) if isinstance(x,str) and DOMAIN_RE.fullmatch(x)]
  hostrule='('+' || '.join('Host(`'+x+'`)' for x in domains)+')'
  port=a.get('webPort',8080)
  if not isinstance(port,int) or not 1<=port<=65535:continue
  priority=(2 if aid=='bf-domain-parking' else 20000) if a.get('migrated') else 100
  marker=aid+'-origin'
  middlewares[marker]={'headers':{'customResponseHeaders':{'X-BrandFleet-Instance':aid}}}
  routers[aid]={'rule':hostrule,'priority':priority,'entryPoints':['https'],'service':aid,'middlewares':[marker],'tls':{'certResolver':'cfdns'}}
  routers[aid+'-http']={'rule':hostrule,'priority':priority,'entryPoints':['http'],'service':aid,'middlewares':['brandfleet-redirect']}
  services[aid]={'loadBalancer':{'servers':[{'url':'http://'+ip+':'+str(port)}]}}
  for index,endpoint in enumerate(a.get('endpoints',[])):
   prefixes=endpoint.get('pathPrefixes',[]);epport=endpoint.get('port')
   if not isinstance(epport,int) or not 1<=epport<=65535 or not prefixes or not all(isinstance(p,str) and re.fullmatch(r'/[a-zA-Z0-9/_-]*',p) for p in prefixes):continue
   key=aid+'-endpoint-'+str(index)
   routers[key]={'rule':hostrule+' && ('+' || '.join('PathPrefix(`'+p+'`)' for p in prefixes)+')','priority':priority+1000,'entryPoints':['https'],'service':key,'middlewares':[marker],'tls':{'certResolver':'cfdns'}}
   services[key]={'loadBalancer':{'servers':[{'url':'http://'+ip+':'+str(epport)}]}}
 ROUTES.parent.mkdir(parents=True,exist_ok=True)
 temp=ROUTES.with_suffix('.tmp')
 temp.write_text(json.dumps({'http':{'routers':routers,'services':services,'middlewares':middlewares}},indent=2)+'\n')
 temp.replace(ROUTES)
# Native services are read through a fixed command; no user input reaches the guest.
SHARED_PRIVATE=Path('/opt/brandfleet/services/private')
SHARED_GUEST=Path('/var/lib/lxc/bf-services/rootfs')
SHARED_CACHE={'at':0,'value':None};SHARED_LOCK=threading.Lock()
GOOGLE_WORKSPACE_DOMAINS=['google-one.example.com','google-two.example.com']
SHARED_UNITS=('apache2','postgresql@17-main','redis-server','postfix','dovecot','rspamd','clamav-daemon','clamav-freshclam','sogo','brandfleet-quarantine','brandfleet-pocketbase','brandfleet-freeresend','brandfleet-turn','brandfleet-dns-editor')
def _shared_json(path):
 try:
  value=json.loads(path.read_text())
  return value if isinstance(value,dict) else {}
 except (OSError,ValueError):return {}
def _shared_measure():
 from concurrent.futures import ThreadPoolExecutor
 with SHARED_LOCK:
  if SHARED_CACHE['value'] is not None and time.monotonic()-SHARED_CACHE['at']<45:return SHARED_CACHE['value']
  value={'measuredAt':now(),'units':{},'apps':{},'workspace':{},'outboundEnabled':None,'turnIngressActive':None,'measurementAvailable':False,'appsMeasured':False}
  calls={
   'units':(['lxc-attach','-n','bf-services','--','systemctl','show',*SHARED_UNITS,'-p','Id','-p','ActiveState','-p','MemoryCurrent'],3),
   'apps':(['lxc-attach','-n','bf-services','--','runuser','-u','www-data','--','php','/var/www/nextcloud/occ','app:list','--output=json'],3),
   'workspace':(['lxc-attach','-n','bf-services','--','runuser','-u','www-data','--','php','/var/www/nextcloud/occ','status','--output=json'],3),
   'outbound':(['lxc-attach','-n','bf-services','--','postconf','-h','default_transport'],2),
   'turnIngress':(['systemctl','is-active','brandfleet-turn-ingress.service'],2)}
  with ThreadPoolExecutor(max_workers=4) as executor:
   futures={key:executor.submit(command,argv,timeout=timeout) for key,(argv,timeout) in calls.items()}
   for key,future in futures.items():
    try:
     output=future.result()
     if key=='units':
      for block in output.strip().split('\n\n'):
       fields=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
       if fields.get('Id'):value['units'][fields['Id'].removesuffix('.service')]=fields
      value['measurementAvailable']=bool(value['units'])
     elif key=='apps':
      apps=json.loads(output);value['apps']=apps['enabled'] if isinstance(apps.get('enabled'),dict) else {};value['appsMeasured']=isinstance(apps.get('enabled'),dict)
     elif key=='workspace':
      status=json.loads(output);value['workspace']=status if isinstance(status,dict) else {}
     elif key=='turnIngress':value['turnIngressActive']=output.strip()=='active'
     else:value['outboundEnabled']=output.strip()=='smtp'
    except Exception:pass
  SHARED_CACHE.update(at=time.monotonic(),value=value)
  return value

def _shared_status(measured,units):
 states=[measured['units'].get(unit,{}).get('ActiveState') for unit in units]
 if any(s is None for s in states):return 'unknown'
 if all(s=='active' for s in states):return 'running'
 return 'stopped' if all(s in ('inactive','failed') for s in states) else 'degraded'
def _shared_resources(measured,units):
 values=[measured['units'].get(unit,{}).get('MemoryCurrent','') for unit in units]
 return {'memoryBytes':sum(int(v) for v in values) if values and all(v.isdigit() for v in values) else None}
def _shared_import(service,docker):
 proof=_shared_json(SHARED_PRIVATE/(service+'-final-import-proof.json'))
 try:
  source=Path(proof.get('source_snapshot') or proof['source_frozen_snapshot']).resolve()
  if not source.is_relative_to(SHARED_PRIVATE.resolve()):return False
  manifest=_shared_json(source/'manifest.json')
  if manifest.get('source_writers_frozen') is not True or proof.get('rehearsal_only',False) is True:return False
  names=manifest.get('source_writers') or ([manifest['source_container']] if manifest.get('source_container') else ['legacy-workspace-app-1','legacy-workspace-cron-1'])
  states={d.get('Name','').lstrip('/'):d.get('State',{}).get('Running') for d in docker}
  return bool(names) and all(states.get(name) is False for name in names)
 except (OSError,ValueError,KeyError,TypeError):return False

def _shared_route(name,host,port=8080):
 try:
  text=(ROUTES.parent/name).read_text()
  return host in re.findall(r'Host\(`([^`]+)`\)',text) and ('http://10.77.2.50:'+str(port)) in text
 except (OSError,UnicodeError):return False

def _shared_turn_public(measured,docker):
 stage=SHARED_PRIVATE/'turn-public-validation'
 activation=_shared_json(stage/'public-activation-proof.json')
 wan=_shared_json(stage/'public-wan-validation.json')
 owned=_shared_json(SHARED_PRIVATE/'turn-ingress-owned-rules.json')
 tests=wan.get('tests',[])
 states={d.get('Name','').lstrip('/'):d.get('State',{}).get('Running') for d in docker}
 return measured.get('turnIngressActive') is True and states.get('legacy-turn') is False and activation.get('passed') is True and activation.get('originalRetainedStopped') is True and bool(owned.get('owned')) and wan.get('publicMediaTestRun') is True and wan.get('wanNATActivated') is True and wan.get('allocatedSessionsReleased') is True and isinstance(tests,list) and len(tests)>0 and wan.get('passed')==len(tests) and all(isinstance(test,dict) and test.get('passed') is True for test in tests)

def shared_services_inventory(docker):
 measured=_shared_measure();workspace_status=_shared_status(measured,('apache2','postgresql@17-main','redis-server'))
 status=measured.get('workspace',{})
 workspace_ready=workspace_status=='running' and status.get('installed') is True and status.get('maintenance') is False and status.get('needsDbUpgrade') is False
 if workspace_status=='running' and not workspace_ready:workspace_status='degraded' if status else 'unknown'
 ws_public=_shared_import('workspace',docker) and _shared_route('brandfleet-workspace-production.yaml','workspace.example.com') and bool(_shared_json(SHARED_PRIVATE/'workspace-cutover/evidence.json'))
 ws_preview=_shared_route('workspace-pilot.yaml','workspace-preview.example.com') or _shared_route('brandfleet-workspace-pilot.yaml','workspace-preview.example.com')
 ws_url='https://workspace.example.com' if ws_public else 'https://workspace-preview.example.com' if ws_preview else ''
 mail_units=('postfix','dovecot','rspamd','clamav-daemon','clamav-freshclam','apache2','sogo','brandfleet-quarantine')
 mail_status=_shared_status(measured,mail_units)
 mail_http=_shared_json(SHARED_PRIVATE/'mail-http-cutover-proof.json')
 mail_public=_shared_import('mail',docker) and _shared_route('brandfleet-native-mail.yaml','mail.example.com') and mail_http.get('native_origin_header')=='native-mail' and mail_http.get('https_status')==200 and (SHARED_PRIVATE/'native-mail-ingress-proof.json').is_file()
 mail_url='https://mail.example.com/' if mail_public else 'https://workspace-preview.example.com/webmail/' if ws_preview else ''
 fr_http=_shared_json(SHARED_PRIVATE/'freeresend-http-cutover-proof.json')
 fr_public=_shared_import('freeresend',docker) and _shared_route('brandfleet-native-freeresend.yaml','email-api.example.com',3000) and fr_http.get('native_origin_header')=='native-freeresend' and fr_http.get('https_status')==200
 turn_public=_shared_turn_public(measured,docker)
 services=[{'id':'mail','name':'Native multi-domain mail','type':'Postfix · Dovecot · Rspamd · Roundcube · SOGo','status':mail_status,'deployment':'production' if mail_public else 'preview','measuredAt':measured['measuredAt'],'url':mail_url,'detail':('Public mail ingress is active. '+('Outgoing delivery is enabled; outside delivery still requires domain-specific DNS and reputation checks.' if measured['outboundEnabled'] is True else 'Outgoing delivery is awaiting activation.' if measured['outboundEnabled'] is False else 'Outgoing delivery could not be measured.')) if mail_public else 'Native mail is privately staged; production routing has not been verified.','resources':_shared_resources(measured,mail_units)},
 {'id':'workspace','name':'Darsh Workspace','type':'Native Nextcloud in Debian LXC','status':workspace_status,'deployment':'production' if ws_public else 'preview' if ws_preview else 'unknown','measuredAt':measured['measuredAt'],'url':ws_url+'/apps/dashboard/' if ws_url else '', 'detail':'Files, Talk, Calendar, Contacts and Deck use the existing tenant identities. Google Workspace domains retain their Google mail.','resources':_shared_resources(measured,('apache2','postgresql@17-main','redis-server'))},
 {'id':'pocketbase','name':'Shared application backend','type':'Native PocketBase','status':_shared_status(measured,('brandfleet-pocketbase',)),'deployment':'private','measuredAt':measured['measuredAt'],'url':'','detail':'Private shared database API for the brand applications; it has no public dashboard route.','resources':_shared_resources(measured,('brandfleet-pocketbase',))},
 {'id':'freeresend','name':'FreeResend email API','type':'Native Next.js · PostgreSQL','status':_shared_status(measured,('brandfleet-freeresend','postgresql@17-main')),'deployment':'production' if fr_public else 'preview','measuredAt':measured['measuredAt'],'url':'https://email-api.example.com/' if fr_public else '', 'detail':'Authenticated email API with verified sender-domain restrictions. No outside delivery test has been sent.','resources':_shared_resources(measured,('brandfleet-freeresend',))},
 {'id':'turn','name':'Talk call relay','type':'Native coturn','status':_shared_status(measured,('brandfleet-turn',)),'deployment':'production' if turn_public else 'private','measuredAt':measured['measuredAt'],'url':'','detail':'Public authenticated UDP and TCP allocations and bidirectional media relay passed from an owned public host.' if turn_public else 'Private authenticated relay is validated; public media connectivity is awaiting verified network acceptance.','resources':_shared_resources(measured,('brandfleet-turn',))}]
 dns_public=_shared_route('brandfleet-native-dns.yaml','dns.example.com',8089)
 if dns_public or 'brandfleet-dns-editor' in measured['units']:
  services.append({'id':'dns-editor','name':'DNS management','type':'Native FastAPI · Cloudflare API','status':_shared_status(measured,('brandfleet-dns-editor',)),'deployment':'production' if dns_public else 'private','measuredAt':measured['measuredAt'],'url':'https://dns.example.com/' if dns_public else '', 'detail':'Password-gated Cloudflare record editor for the existing allowed zones. Website hosting and domain DNS stay separate; this tool has no Android device.','resources':_shared_resources(measured,('brandfleet-dns-editor',))})
 apps=[]
 for app,name,path,detail in [('files','Files','/apps/files/','Private files, versions and sharing.'),('spreed','Team chat & calls','/apps/spreed/','Talk conversations, team chat and calls. Public call relay is verified separately.'),('calendar','Calendar','/apps/calendar/','Existing calendars and CalDAV synchronization.'),('contacts','Contacts','/apps/contacts/','Existing contacts and CardDAV synchronization.'),('deck','Projects','/apps/deck/','Deck boards, cards and team project planning.')]:
  version=measured['apps'].get(app,'')
  app_status='ready' if version and workspace_ready else 'degraded' if version else 'unavailable' if measured.get('appsMeasured') else 'unknown'
  apps.append({'id':app,'name':name,'path':path,'version':version,'status':app_status,'url':ws_url+path if ws_url and version else '', 'detail':detail})
 apps.append({'id':'webmail','name':'Email','path':'/','version':'','status':'ready' if mail_status=='running' and mail_url else mail_status,'url':mail_url,'detail':'Roundcube webmail, with SOGo available for groupware and mobile synchronization.'})
 tenant=_shared_json(SHARED_GUEST/'root/brandfleet-private/workspace-tenant-validation.json')
 verified=tenant.get('passed')==8 and len(tenant.get('tests',[]))==8 and all(t.get('passed') is True for t in tenant['tests'])
 workspace={'status':workspace_status,'measuredAt':measured['measuredAt'],'deployment':'production' if ws_public else 'preview' if ws_preview else 'unknown','url':ws_url,'previewUrl':'https://workspace-preview.example.com' if ws_preview else '', 'version':status.get('versionstring',''),'apps':apps,'tenantIsolation':{'status':'verified' if verified else 'unknown','verifiedAt':tenant.get('checked_at','') if verified else '', 'detail':'Two domain tenants passed private file access and cross-domain sharing denial checks.' if verified else 'Tenant permission validation is not available.'},'retainedGoogleDomains':GOOGLE_WORKSPACE_DOMAINS}
 infra={'id':'shared-native','name':'Shared services · bf-services','type':'Unprivileged Debian LXC','address':'10.77.2.50','status':'running' if measured['measurementAvailable'] else 'unknown','detail':'Mail, workspace and shared application backends have their own systemd services inside this shared Debian container.','resources':{}}
 try:
  cgroup=Path('/sys/fs/cgroup/lxc.payload.bf-services');quota,period=(cgroup/'cpu.max').read_text().split()
  infra['resources']={'memoryBytes':int((cgroup/'memory.current').read_text()),'memoryLimitBytes':int((cgroup/'memory.max').read_text()),'cpuCores':int(quota)/int(period) if quota!='max' else None}
 except (OSError,ValueError):pass
 return services,workspace,infra

def inventory():
 raw=json.loads(command(['docker','inspect',*command(['docker','ps','-aq']).split()]))
 services,workspace,shared_host=shared_services_inventory(raw)
 shared_hosts={'mail':'mail.example.com','workspace':'workspace.example.com','freeresend':'email-api.example.com'}
 verified_shared_domains={shared_hosts[s['id']] for s in services if s.get('deployment')=='production' and s.get('id') in shared_hosts}
 android=android_inventory(allow_cached=True)
 package_evidence=social_evidence_inventory()
 migrated_domains={x for a in android if a.get('migrated') for x in [a.get('domain',''),*a.get('aliases',[])]}
 brands=[]
 for d in raw:
  n=d['Name'].lstrip('/');lab=d.get('Config',{}).get('Labels') or {};rules=' '.join(str(v) for k,v in lab.items() if k.endswith('.rule'))
  domains=re.findall(r'Host\(`([^`]+)`\)',rules)
  if not domains:continue
  if set(domains).issubset(migrated_domains|verified_shared_domains):continue
  status='running' if d['State'].get('Running') else 'stopped';health=d['State'].get('Health',{}).get('Status')
  if health=='unhealthy':status='degraded'
  bid='legacy-'+d['Id'][:12]
  brands.append({'id':bid,'name':domains[0].removeprefix('www.'),'domain':domains[0],'domains':sorted(set(domains)),'url':'https://'+domains[0],'status':status,'phase':'existing-docker','description':n,'website':{'status':status,'runtime':d['Config']['Image'],'containerId':d['Id'][:12],'detail':'Existing production service; migration not yet validated'},'android':{'status':'unavailable','detail':'Android migration pending compatibility validation'},'automation':{'status':'unknown','detail':'Existing job dependencies are being inventoried'},'social':[],'backup':{'status':'unverified','repositoryUrl':'','detail':'Configure and validate your own application backup independently'},'actions':[]})
 for a in android:
  if not isinstance(a,dict) or a.get('retired') is True:continue
  aid=a.get('id') or a.get('name','');s=a.get('state','UNKNOWN').lower();domain=a.get('domain','');ip=a.get('ip') or a.get('privateIp','')
  if not ID_RE.fullmatch(aid):continue
  actions=(['stop','restart','clone','archive','remove'] if s=='running' else ['start','clone','archive','remove']) if ready() and not a.get('_inventoryStale') else []
  if a.get('nativeRuntime') and 'clone' in actions:actions.remove('clone')
  brands.append({'id':aid,'name':a.get('displayName') or a.get('name') or aid,'domain':domain,'domains':[domain,*a.get('aliases',[])],'url':'https://'+domain if domain else '', 'status':s if a.get('webHealthy') or s!='running' else 'degraded','phase':'android-production' if a.get('migrated') else 'android-pilot','description':'Android LXC in isolated pool VM; social accounts require setup','packageEvidence':package_evidence.get(aid,sanitize_package_evidence({})),'resources':{'cpuCores':a.get('cpuQuota'),'memoryBytes':a.get('memoryBytes'),'memoryLimitBytes':int(a.get('memoryMiB',0))*1024*1024},'website':{'status':'running' if a.get('webHealthy') else 'pending','runtime':a.get('appStack','Android static HTTP'),'privateIp':ip,'url':'https://'+domain if domain else ''},'android':{'status':s if a.get('bootCompleted') else 'pending','runtime':'Redroid Android 14 LXC','privateIp':ip,'detail':'Private Android screen; social sessions not connected','screenUrl':('https://fleet.example.com/android/view/'+aid) if a.get('bootCompleted') else ''},'automation':{'status':'unavailable','detail':'No social publishing account connected'},'backup':{'status':'retained-on-removal','detail':'Remove archives Android data first; native apps require a separate deployment to clone. Android accounts are never copied.'},'actions':actions})
 mem={}
 for line in Path('/proc/meminfo').read_text().splitlines():
  k,v=line.split(':',1);mem[k]=int(v.split()[0])*1024
 out={'updatedAt':now(),'source':'Private ingress and Android pool inventory','mode':'isolated-android-fleet','notes':['Each migrated website runs inside its own Android LXC. Remaining services are marked as existing deployments.','Shared mail and workspace run in the measured native Debian services container. Google Workspace domains retain their Google mail.','New domains require a DNS A record to ingress. A shared IP does not isolate sender reputation.'],'infrastructure':[{'id':'website-ingress','name':'Website ingress and services ingress','type':'Debian KVM VM','address':'192.0.2.10','status':'running','detail':'Live inventory; shared physical Proxmox failure remains possible','resources':{'cpuCores':os.cpu_count(),'memoryBytes':mem['MemTotal']-mem.get('MemAvailable',mem.get('MemFree',0)),'memoryLimitBytes':mem['MemTotal']}}], 'brands':brands,'services':[], 'capabilities':{'create':ready(),'profiles':[{'id':'android-static','name':'Android 14 + website · 2 GB / 1 CPU'}] if ready() else []}}
 out['services'],out['workspace']=services,workspace
 out['infrastructure'].append(shared_host)
 try:
  pool=pool_status()
  budget=int(pool.get('assignedBudgetMiB',24576));limit=int(pool.get('maxInstances',12))
  active_android=[a for a in android if a.get('retired') is not True]
  assigned=sum(int(a.get('memoryMiB',4096)) for a in active_android)
  out['infrastructure'].append({'id':'android-pool','name':'Android pool VM','type':'Debian KVM with Android LXC','address':'10.77.1.104','status':'running','detail':f'Private pool behind ingress; {budget/1024:g} GiB assigned memory budget, {limit} unit limit; measured memory reserve required','resources':{'cpuCores':pool['cpuCount'],'memoryBytes':pool['memoryTotalBytes']-pool['memoryAvailableBytes'],'memoryLimitBytes':pool['memoryTotalBytes']}})
  if len(active_android)>=limit or assigned+2048>budget or int(pool['memoryAvailableBytes'])<5*1024**3:
   out['capabilities']['create']=False
   out['notes'].append('Creation is paused while the pool has insufficient reserved memory. Existing environments remain independently manageable.')
 except Exception:
  out['notes'].append('Android pool status could not be read; new unit creation is unavailable.')
  out['capabilities']['create']=False
 if any(a.get('_inventoryStale') for a in android):
  out['notes'].append('Android inventory is temporarily unavailable. Brand rows show their last verified state; lifecycle controls are disabled until the next fresh reading.')
  out['capabilities']['create']=False
  stale_ids={a.get('id',a.get('name')) for a in android if a.get('_inventoryStale')}
  for b in out['brands']:
   if b['id'] in stale_ids:
    b['status']='unknown';b['website']['status']='unknown';b['android']['status']='unknown';b['android']['detail']='Last verified device; fresh status is temporarily unavailable';b['actions']=[]
 return out
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data):
  b=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def authorized(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+TOKEN)
 def do_GET(self):
  if not self.authorized():return self.send(401,{'error':'Unauthorized'})
  try:
   if self.path=='/inventory':return self.send(200,inventory())
   if self.path=='/jobs':return self.send(200,{'jobs':jobs()})
   self.send(404,{'error':'Not found'})
  except Exception:self.send(503,{'error':'Live inventory failed'})
 def do_POST(self):
  if not self.authorized():return self.send(401,{'error':'Unauthorized'})
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size<1 or size>16384:return self.send(413,{'error':'Invalid request length'})
   d=json.loads(self.rfile.read(size))
   if not isinstance(d,dict):raise ValueError('Invalid body')
   if self.path=='/mail-admin':
    allowed={'list','domain_create','domain_update','mailbox_create','mailbox_update','alias_create','alias_update','quarantine_list','quarantine_release','quarantine_delete'}
    if d.get('action') not in allowed:return self.send(400,{'error':'Unsupported mail operation'})
    fields={'action','domain','email','password','quota_bytes','active','address','destinations','sender_allowed','protocols','tls_enforce_in','tls_enforce_out','limits','id'}
    payload={k:v for k,v in d.items() if k in fields}
    result=subprocess.run(['/usr/bin/python3','/opt/brandfleet/services/native-mail-admin.py'],input=json.dumps(payload),capture_output=True,text=True,timeout=100)
    try:out=json.loads(result.stdout)
    except ValueError:return self.send(503,{'error':'Mail administration is unavailable'})
    if result.returncode or out.get('ok') is not True:return self.send(400,{'error':str(out.get('error','Mail operation refused'))[:300]})
    return self.send(200,{k:v for k,v in out.items() if k in {'ok','changed','state','public_DNS_changed','production_routing_changed','messages','total','notifications_enabled','notification_schedule_seconds','notification_throttle_seconds','max_age_days','outbound_enabled','public_mail_enabled'}})
   launch_match=re.fullmatch(r'/brands/(bf-[a-z0-9][a-z0-9-]{0,40})/social/launch',self.path)
   if launch_match:
    if set(d)!={'app'}:return self.send(400,{'error':'Choose one supported app'})
    try:return self.send(200,launch_social(launch_match[1],d['app']))
    except ValueError:return self.send(400,{'error':'Choose one supported app'})
    except LaunchBusy:return self.send(409,{'error':'Device control is busy; retry shortly'})
    except Exception:return self.send(503,{'error':'Android launch confirmation is unavailable'})
   if self.path=='/brands':
    if not ready() or d.get('profile')!='android-static':return self.send(409,{'error':'Profile not validated'})
    name=d.get('name','').strip();domain=d.get('domain','').strip().lower()
    if not name or len(name)>100 or not DOMAIN_RE.fullmatch(domain):raise ValueError('Invalid name or domain')
    if domain_in_use(domain):return self.send(409,{'error':'Domain already belongs to an environment'})
    check_capacity()
    bid='bf-'+uuid.uuid4().hex[:12]
    argv=['/usr/bin/python3',str(RUNTIME),'create','--id',bid,'--name',name,'--domain',domain,'--memory','2048','--cpus','1']
    return self.send(202,{'job':newjob(d.get('requestId'),bid,'create',argv)})
   m=re.fullmatch(r'/brands/(bf-[a-z0-9][a-z0-9-]{0,40})/actions/(start|stop|restart|clone|archive|remove)',self.path)
   if not m:return self.send(404,{'error':'Unknown operation'})
   bid,act=m.groups();a=next((a for a in inventory()['brands'] if a['id']==bid),None)
   if not a or act not in a.get('actions',[]):return self.send(409,{'error':'Unsupported operation'})
   if act in ('stop','restart','archive','remove') and d.get('confirm')!=bid:return self.send(400,{'error':'Environment confirmation required'})
   argv=['/usr/bin/python3',str(RUNTIME),act,'--id',bid]
   if act=='clone':
    check_capacity();argv+=['--new-id','bf-'+uuid.uuid4().hex[:12],'--name',a['name']+' copy']
   self.send(202,{'job':newjob(d.get('requestId'),bid,act,argv)})
  except (ValueError,TypeError):self.send(400,{'error':'Invalid request or lifecycle operation already running'})
  except Exception:self.send(500,{'error':'Operation failed'})
if __name__=='__main__':
 with sqlite3.connect(DB) as c:c.execute("UPDATE jobs SET status='interrupted',updated=?,message='Controller restarted; inspect actual runtime state before retrying' WHERE status IN ('running','pending')",(now(),))
 ThreadingHTTPServer(('127.0.0.1',8211),Handler).serve_forever()
