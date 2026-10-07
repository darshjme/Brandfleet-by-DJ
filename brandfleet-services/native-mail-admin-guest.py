#!/usr/bin/python3
"""Root-only fixed JSON administration; secrets accepted on stdin, never returned."""
import copy,fcntl,grp,hashlib,json,os,re,secrets,shutil,subprocess,sys,tarfile,time
from pathlib import Path

BASE=Path('/etc/brandfleet-mail');STATE=BASE/'state.json'
FILES=['/etc/dovecot/brandfleet-users','/etc/dovecot/dovecot.conf','/etc/postfix/brandfleet-mailboxes','/etc/postfix/brandfleet-aliases','/etc/postfix/brandfleet-sender-login','/etc/postfix/brandfleet-recipient-tls','/etc/postfix/brandfleet-sender-tls-transport','/etc/postfix/main.cf','/etc/postfix/master.cf','/etc/rspamd/brandfleet-sender-ownership.lua','/etc/rspamd/local.d/dkim_signing.conf','/root/brandfleet-private/sender-ownership.json','/etc/sogo/sogo.conf']+['/etc/dovecot/deny.'+v for v in ('imap','pop3','smtp','submission','sieve','managesieve','lmtp','doveadm')]
def run(argv,**kw):
 p=subprocess.run(argv,capture_output=True,**kw)
 if p.returncode:
  diagnostic=Path('/root/brandfleet-private/native-mail-admin-subprocess-error.log');diagnostic.write_bytes(p.stderr+p.stdout);os.chmod(diagnostic,0o600)
  raise subprocess.CalledProcessError(p.returncode,argv)
 return p
def domain(value):
 if not isinstance(value,str):raise ValueError('domain must be text')
 value=value.lower().encode('idna').decode('ascii')
 if len(value)>253 or '.' not in value or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',v) for v in value.split('.')):raise ValueError('invalid domain')
 return value
def email(value):
 if not isinstance(value,str) or value.count('@')!=1:raise ValueError('invalid email')
 local,name=value.lower().split('@');name=domain(name)
 if not re.fullmatch(r'[a-z0-9][a-z0-9._+\-]{0,63}',local):raise ValueError('invalid mailbox name')
 return local+'@'+name
def boolean(value):
 if type(value) is not bool:raise ValueError('active and sender_allowed require booleans')
 return value
def quota(value):
 if type(value) is not int or not 0<=value<=1024**5:raise ValueError('quota_bytes must be an integer between zero and 1 PiB')
 return value
def limits(value):
 if not isinstance(value,dict) or set(value)-{'aliases','mailboxes','default_quota_bytes','max_quota_bytes','total_quota_bytes'}:raise ValueError('unsupported domain limit field')
 out={}
 for key,item in value.items():
  if type(item) is not int or item<0 or item>1024**5:raise ValueError('invalid domain limit')
  out[key]=item
 return out
def protocols(value):
 if not isinstance(value,dict) or set(value)-{'imap','pop3','smtp','sieve'}:raise ValueError('protocols must contain imap, pop3, smtp, sieve booleans')
 return {key:boolean(item) for key,item in value.items()}
def atomic(path,content,mode=0o640,group=0):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.pending')
 tmp.write_text(content);os.chmod(tmp,mode);os.chown(tmp,0,group);tmp.replace(path)
def hash_password(value):
 if not isinstance(value,str) or not 12<=len(value)<=1024 or any(c in value for c in '\r\n\0'):raise ValueError('password requires 12–1024 characters without line breaks')
 result=run(['doveadm','pw','-s','BLF-CRYPT'],input=(value+'\n'+value+'\n').encode()).stdout.decode().strip()
 if not result.startswith('{BLF-CRYPT}'):raise RuntimeError('password hash generation failed')
 return result
def seed():
 mailboxes={};names=set()
 for line in Path('/etc/dovecot/brandfleet-users').read_text().splitlines():
  f=line.split(':');address=email(f[0]);names.add(address.split('@')[1]);m=re.search(r'userdb_quota_storage_size=(\d+)B',f[7]);limit=int(m.group(1)) if m else 0
  mailboxes[address]={'password_hash':f[1],'quota_bytes':limit,'active':True}
 attributes=Path('/root/brandfleet-private/mail-original-attributes.json')
 if attributes.exists():
  for address,item in json.loads(attributes.read_text())['accounts'].items():
   if address in mailboxes:mailboxes[address].update(item)
 aliases={}
 for line in Path('/etc/postfix/brandfleet-aliases').read_text().splitlines():
  address,dest=line.split(None,1);aliases[email(address)]={'destinations':[email(v.strip()) for v in dest.split(',')],'active':True}
 owners=json.loads(Path('/root/brandfleet-private/sender-ownership.json').read_text())['ownership']
 for address,item in aliases.items():item['sender_allowed']=address in owners
 dkim=json.loads(Path('/root/brandfleet-private/dkim-import.json').read_text())['domains']
 return {'version':1,'domains':{v:{'active':True,'limits':{'aliases':100,'mailboxes':100,'default_quota_bytes':1024**3,'max_quota_bytes':10*1024**3,'total_quota_bytes':100*1024**3}} for v in sorted(names)},'mailboxes':mailboxes,'aliases':aliases,'dkim':dkim}
def safe_state(state):
 result={'version':1,'domains':[],'mailboxes':[],'aliases':[],'outbound_enabled':not run(['postconf','-h','default_transport']).stdout.decode().strip().startswith('error:')}
 for name,item in sorted(state['domains'].items()):
  entry={'domain':name,**item};dk=state['dkim'].get(name)
  if dk:entry['dkim']={'selector':dk['selector'],'dns_name':dk['selector']+'._domainkey.'+name,'dns_value':'v=DKIM1; k=rsa; p='+dk['public'].strip()}
  result['domains'].append(entry)
 for address,item in sorted(state['mailboxes'].items()):result['mailboxes'].append({'email':address,'active':item['active'],'quota_bytes':item['quota_bytes'],'protocols':item.get('protocols',dict.fromkeys(('imap','pop3','smtp','sieve'),True)),'tls_enforce_in':item.get('tls_enforce_in',False),'tls_enforce_out':item.get('tls_enforce_out',False)})
 for address,item in sorted(state['aliases'].items()):result['aliases'].append({'address':address,**item})
 return result
def generate_dkim(state,name):
 key=run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048']).stdout
 public=run(['openssl','pkey','-pubout','-outform','DER'],input=key).stdout
 import base64
 state['dkim'][name]={'selector':'fleet'+time.strftime('%Y%m%d',time.gmtime()),'private':key.decode(),'public':base64.b64encode(public).decode()}
def render(state,reload_services=True):
 users=[];boxes=[];aliases=[];owners={}
 active_domains={n for n,v in state['domains'].items() if v['active']}
 for address,item in sorted(state['mailboxes'].items()):
  local,name=address.split('@')
  if not item['active'] or name not in active_domains:continue
  home='/var/vmail/'+name+'/'+local;limit=item['quota_bytes'];limitstr=str(limit)+'B' if limit else '-'
  users.append(address+':'+item['password_hash']+':5000:5000::'+home+'::userdb_quota_storage_size='+limitstr)
  boxes.append(address+' '+name+'/'+local+'/Maildir/');owners[address]=[address]
  for folder in ('Maildir/cur','Maildir/new','Maildir/tmp','sieve'):
   path=Path(home)/folder;path.mkdir(parents=True,exist_ok=True);os.chown(path,5000,5000);os.chmod(path,0o700)
  for path in (Path(home),Path(home).parent,Path(home)/'Maildir'):os.chown(path,5000,5000);os.chmod(path,0o700)
 for address,item in sorted(state['aliases'].items()):
  if item['active'] and address.split('@')[1] in active_domains:
   aliases.append(address+' '+','.join(item['destinations']))
   if item.get('sender_allowed'):owners[address]=[v for v in item['destinations'] if v in state['mailboxes'] and state['mailboxes'][v]['active']]
 # A deny lookup precedes real password verification, preserving each original service ACL.
 cfg=Path('/etc/dovecot/dovecot.conf');content=cfg.read_text()
 if 'passdb brandfleet-protocol-deny' not in content:
  content=content.replace('passdb passwd-file {','passdb brandfleet-protocol-deny {\n  driver = passwd-file\n  passwd_file_path = /etc/dovecot/deny.%{protocol}\n  deny = yes\n}\npassdb passwd-file {',1)
  atomic(cfg,content)
 for proto,field in [('imap','imap'),('pop3','pop3'),('smtp','smtp'),('submission','smtp'),('sieve','sieve'),('managesieve','sieve'),('lmtp',None),('doveadm',None)]:
  denied=[a for a,v in sorted(state['mailboxes'].items()) if field and not v.get('protocols',{}).get(field,True)]
  atomic('/etc/dovecot/deny.'+proto,'\n'.join(denied)+'\n',group=grp.getgrnam('dovecot').gr_gid)
 atomic('/etc/dovecot/brandfleet-users','\n'.join(users)+'\n',group=grp.getgrnam('dovecot').gr_gid)
 for name,lines in [('brandfleet-mailboxes',boxes),('brandfleet-aliases',aliases),('brandfleet-sender-login',[k+' '+','.join(v) for k,v in sorted(owners.items())])]:
  atomic('/etc/postfix/'+name,'\n'.join(lines)+'\n',group=grp.getgrnam('postfix').gr_gid);run(['postmap','hash:/etc/postfix/'+name])
  os.chmod('/etc/postfix/'+name+'.db',0o640);os.chown('/etc/postfix/'+name+'.db',0,grp.getgrnam('postfix').gr_gid)
 tlsin=[k+' fleet_require_tls' for k,v in sorted(state['mailboxes'].items()) if v.get('tls_enforce_in') and v['active']]
 # An alias inherits strict delivery TLS if any of its targets require it.
 tlsin += [k+' fleet_require_tls' for k,v in sorted(state['aliases'].items()) if v['active'] and any(state['mailboxes'].get(d,{}).get('tls_enforce_in') for d in v['destinations'])]
 tlsin=list(dict.fromkeys(tlsin))
 atomic('/etc/postfix/brandfleet-recipient-tls','\n'.join(tlsin)+'\n',group=grp.getgrnam('postfix').gr_gid)
 run(['postmap','hash:/etc/postfix/brandfleet-recipient-tls'])
 os.chmod('/etc/postfix/brandfleet-recipient-tls.db',0o640);os.chown('/etc/postfix/brandfleet-recipient-tls.db',0,grp.getgrnam('postfix').gr_gid)
 run(['postconf','-e','virtual_mailbox_domains='+','.join(sorted(active_domains)),'smtpd_restriction_classes=fleet_require_tls','fleet_require_tls=reject_plaintext_session','smtpd_recipient_restrictions=check_recipient_access hash:/etc/postfix/brandfleet-recipient-tls,reject_unauth_destination,reject_unlisted_recipient'])
 # Stage per-sender outbound TLS policy; do not activate any transport while outbound is disabled.
 tlsout=[k+' fleet_tls' for k,v in sorted(state['mailboxes'].items()) if v.get('tls_enforce_out') and v['active']]
 atomic('/etc/postfix/brandfleet-sender-tls-transport','\n'.join(tlsout)+'\n',group=grp.getgrnam('postfix').gr_gid);run(['postmap','hash:/etc/postfix/brandfleet-sender-tls-transport'])
 master=Path('/etc/postfix/master.cf');mt=master.read_text()
 if 'fleet_tls unix' not in mt:atomic(master,mt+'\nfleet_tls unix - - n - - smtp\n  -o smtp_tls_security_level=encrypt\n')
 atomic('/root/brandfleet-private/sender-ownership.json',json.dumps({'ownership':owners})+'\n',0o600)
 # Existing original selectors and key bytes are retained; keys never appear in response.
 entries=[]
 signing_keys=dict(state['dkim'])
 delegated_keys=Path('/root/brandfleet-private/freeresend-dkim.json')
 if delegated_keys.exists():
  for name,item in json.loads(delegated_keys.read_text()).items():
   assert name in ('vpn-brand.example.com','mail-brand.example.com','calendar-brand.example.com','trading-brand.example.com')
   signing_keys.setdefault(name,item)
 for name,item in sorted(signing_keys.items()):
  path=Path('/var/lib/rspamd/dkim')/(name+'.'+item['selector']+'.key');atomic(path,item['private'].rstrip()+'\n',0o600,grp.getgrnam('_rspamd').gr_gid);os.chown(path,__import__('pwd').getpwnam('_rspamd').pw_uid,grp.getgrnam('_rspamd').gr_gid)
  run(['openssl','pkey','-in',str(path),'-noout','-check'])
  entries.append('"'+name+'" { selector = "'+item['selector']+'"; path = "'+str(path)+'"; }')
 delegated=Path('/root/brandfleet-private/freeresend-smtp.json').exists()
 atomic('/etc/rspamd/local.d/dkim_signing.conf','enabled = true; sign_authenticated = true; sign_local = true; use_esld = false; allow_username_mismatch = '+('true' if delegated else 'false')+'; allow_hdrfrom_mismatch = false; try_fallback = false; domain {\n'+'\n'.join(entries)+'\n}\n',group=grp.getgrnam('_rspamd').gr_gid)
 if delegated:run(['python3','/opt/brandfleet/configure-freeresend-smtp.py','--no-restart'])
 # This fixed helper regenerates a validated Lua ownership table and reloads native Rspamd.
 run(['systemctl','reset-failed','rspamd']);run(['python3','/opt/brandfleet/configure-header-sender-ownership.py','--no-restart'])
 run(['doveconf','-n']);run(['postfix','check']);run(['rspamadm','configtest'])
 if reload_services:
  run(['systemctl','reload','dovecot','postfix','rspamd'])
  # Rspamd gracefully drains old workers for 16 seconds; wait before returning a ready mutation.
  time.sleep(18)
  run(['rspamc','-h','127.0.0.1:11334','stat'])
def execute(request,state):
 action=request.get('action')
 if action=='list':return state,False
 if action in ('domain_create','domain_update'):
  name=domain(request.get('domain'));exists=name in state['domains']
  if exists!=(action=='domain_update'):raise ValueError('domain already exists' if exists else 'domain does not exist')
  if not exists:state['domains'][name]={'active':True,'limits':{'aliases':100,'mailboxes':100,'default_quota_bytes':1024**3,'max_quota_bytes':10*1024**3,'total_quota_bytes':100*1024**3}};generate_dkim(state,name)
  if 'limits' in request:state['domains'][name].setdefault('limits',{}).update(limits(request['limits']))
  if 'active' in request:
   active=boolean(request['active'])
   if not active and any(v['active'] and k.endswith('@'+name) for k,v in state['mailboxes'].items()):raise ValueError('disable the domain mailboxes first')
   state['domains'][name]['active']=active
 elif action in ('mailbox_create','mailbox_update'):
  address=email(request.get('email'));name=address.split('@')[1];exists=address in state['mailboxes']
  if name not in state['domains'] or not state['domains'][name]['active']:raise ValueError('active domain required')
  if exists!=(action=='mailbox_update'):raise ValueError('mailbox already exists' if exists else 'mailbox does not exist')
  if not exists:
   bound=state['domains'][name].get('limits',{})
   if sum(k.endswith('@'+name) for k in state['mailboxes'])>=bound.get('mailboxes',100):raise ValueError('domain mailbox limit reached')
   state['mailboxes'][address]={'password_hash':hash_password(request.get('password')),'quota_bytes':quota(request.get('quota_bytes',bound.get('default_quota_bytes',1024**3))),'active':True,'protocols':{'imap':True,'pop3':False,'smtp':True,'sieve':True}}
  else:
   if 'password' in request:state['mailboxes'][address]['password_hash']=hash_password(request['password'])
   if 'quota_bytes' in request:state['mailboxes'][address]['quota_bytes']=quota(request['quota_bytes'])
   if 'active' in request:state['mailboxes'][address]['active']=boolean(request['active'])
  if 'protocols' in request:state['mailboxes'][address].setdefault('protocols',dict.fromkeys(('imap','pop3','smtp','sieve'),True)).update(protocols(request['protocols']))
  for field in ('tls_enforce_in','tls_enforce_out'):
   if field in request:state['mailboxes'][address][field]=boolean(request[field])
  bound=state['domains'][name].get('limits',{});amount=state['mailboxes'][address]['quota_bytes']
  if amount and amount>bound.get('max_quota_bytes',1024**5):raise ValueError('mailbox quota exceeds domain maximum')
  if sum(v['quota_bytes'] for k,v in state['mailboxes'].items() if k.endswith('@'+name))>bound.get('total_quota_bytes',1024**5):raise ValueError('total domain allocated quota exceeded')
 elif action in ('alias_create','alias_update'):
  address=email(request.get('address'));exists=address in state['aliases']
  if address.split('@')[1] not in state['domains']:raise ValueError('domain required')
  if exists!=(action=='alias_update'):raise ValueError('alias already exists' if exists else 'alias does not exist')
  if not exists and sum(k.endswith('@'+address.split('@')[1]) for k in state['aliases'])>=state['domains'][address.split('@')[1]].get('limits',{}).get('aliases',100):raise ValueError('domain alias limit reached')
  if not exists:state['aliases'][address]={'active':True,'sender_allowed':False,'destinations':[]}
  item=state['aliases'][address]
  if 'destinations' in request:
   raw=request['destinations']
   if not isinstance(raw,list) or not 1<=len(raw)<=30:raise ValueError('1–30 local mailbox destinations required')
   dest=[email(v) for v in raw]
   if any(v not in state['mailboxes'] for v in dest):raise ValueError('alias destination must be an existing local mailbox')
   item['destinations']=list(dict.fromkeys(dest))
  if not item['destinations']:raise ValueError('destinations required')
  for key in ('active','sender_allowed'):
   if key in request:item[key]=boolean(request[key])
 elif action=='fixture_cleanup':
  name=domain(request.get('domain'))
  if not re.fullmatch(r'fixture-[a-z0-9]{8,32}\.internal',name):raise ValueError('cleanup restricted to isolated synthetic fixture domains')
  state['domains'].pop(name,None);state['dkim'].pop(name,None)
  for field in ('mailboxes','aliases'):
   state[field]={k:v for k,v in state[field].items() if not k.endswith('@'+name)}
 else:raise ValueError('unsupported action')
 return state,True
def main():
 if os.geteuid()!=0 or Path('/etc/hostname').read_text().strip()!='bf-services':raise ValueError('restricted to native services administrator')
 raw=sys.stdin.buffer.read(65537)
 if len(raw)>65536:raise ValueError('request too large')
 request=json.loads(raw)
 if not isinstance(request,dict):raise ValueError('JSON object required')
 if request.get('action') in ('quarantine_list','quarantine_release','quarantine_delete'):
  p=run(['python3','/opt/brandfleet/native-quarantine.py','admin'],input=raw)
  print(p.stdout.decode().strip());return
 BASE.mkdir(mode=0o700,exist_ok=True)
 with (BASE/'admin.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  state=json.loads(STATE.read_text()) if STATE.exists() else seed()
  initial=copy.deepcopy(state);state,changed=execute(request,state)
  backup=None
  if changed:
   backup=BASE/('backup-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+secrets.token_hex(3)+'.tar')
   with tarfile.open(backup,'w') as tar:
    for path in FILES:
     if Path(path).exists():tar.add(path,arcname=path.lstrip('/'))
    if STATE.exists():tar.add(STATE,arcname=str(STATE).lstrip('/'))
   os.chmod(backup,0o600)
   try:
    render(state);atomic(STATE,json.dumps(state,indent=2)+'\n',0o600)
    if Path('/opt/brandfleet/configure-native-sogo.py').exists():run(['python3','/opt/brandfleet/configure-native-sogo.py'])
   except Exception:
    render(initial);atomic(STATE,json.dumps(initial,indent=2)+'\n',0o600)
    if Path('/opt/brandfleet/configure-native-sogo.py').exists():run(['python3','/opt/brandfleet/configure-native-sogo.py'])
    raise RuntimeError('native configuration failed; previous maps restored')
  result={'ok':True,'changed':changed,'state':safe_state(state),'backup':str(backup) if backup else None,'public_DNS_changed':False,'production_routing_changed':False}
  print(json.dumps(result))
if __name__=='__main__':
 try:main()
 except (ValueError,RuntimeError,json.JSONDecodeError) as error:
  print(json.dumps({'ok':False,'error':str(error)}));sys.exit(2)
 except Exception:
  import traceback
  diagnostic=Path('/root/brandfleet-private/native-mail-admin-error.log');diagnostic.write_text(traceback.format_exc());os.chmod(diagnostic,0o600)
  print(json.dumps({'ok':False,'error':'native operation failed; inspect private service diagnostics'}));sys.exit(3)
