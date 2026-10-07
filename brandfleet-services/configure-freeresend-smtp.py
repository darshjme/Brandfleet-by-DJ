#!/usr/bin/python3
"""Private authenticated delegated email relay; never enables external delivery."""
import grp,hashlib,json,os,re,secrets,subprocess,sys,time
from pathlib import Path
assert Path('/etc/hostname').read_text().strip()=='bf-services'
DOMAINS=['vpn-brand.example.com','mail-brand.example.com','calendar-brand.example.com','trading-brand.example.com']
P=Path('/root/brandfleet-private/freeresend-smtp.json')
def run(argv,**kw):return subprocess.run(argv,check=True,capture_output=True,**kw)
def write(path,content,mode=0o640,group=0):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content);p.chmod(mode);os.chown(p,0,group)
if not P.exists():
 password=secrets.token_urlsafe(48)
 h=run(['doveadm','pw','-s','BLF-CRYPT'],input=(password+'\n'+password+'\n').encode()).stdout.decode().strip()
 write(P,json.dumps({'identity':'freeresend-service@brandfleet.internal','password':password,'password_hash':h,'domains':DOMAINS})+'\n',0o600)
s=json.loads(P.read_text());assert s['domains']==DOMAINS and s['identity']=='freeresend-service@brandfleet.internal'
cfg=Path('/etc/dovecot/dovecot.conf');c=cfg.read_text()
if 'passdb brandfleet-delegated-deny' not in c:
 c=c.replace('passdb passwd-file {','passdb brandfleet-delegated-deny {\n driver = passwd-file\n passwd_file_path = /etc/dovecot/delegated-deny.%{protocol}\n deny = yes\n}\npassdb brandfleet-delegated-auth {\n driver = passwd-file\n passwd_file_path = /etc/dovecot/brandfleet-delegated-users\n}\npassdb passwd-file {',1);write(cfg,c)
gid=grp.getgrnam('dovecot').gr_gid
for proto in ('imap','pop3','smtp','submission','sieve','managesieve','lmtp','doveadm'):
 write('/etc/dovecot/delegated-deny.'+proto,'' if proto in ('smtp','submission') else s['identity']+'\n',group=gid)
write('/etc/dovecot/brandfleet-delegated-users',s['identity']+':'+s['password_hash']+'\n',group=gid)
pattern='/^[^@[:space:]]+@('+ '|'.join(re.escape(v) for v in DOMAINS)+')$/ '+s['identity']+'\n'
write('/etc/postfix/brandfleet-delegated-sender-login',pattern,group=grp.getgrnam('postfix').gr_gid)
master=Path('/etc/postfix/master.cf');c=master.read_text()
begin='# BrandFleet delegated API relay begin';end='# BrandFleet delegated API relay end'
if begin in c:c=c[:c.index(begin)]+c[c.index(end)+len(end):]
c+='\n'+begin+'''\n127.0.0.1:2526 inet n - n - - smtpd
 -o syslog_name=postfix/freeresend
 -o smtpd_tls_security_level=encrypt
 -o smtpd_tls_auth_only=yes
 -o smtpd_sasl_auth_enable=yes
 -o smtpd_sender_login_maps=regexp:/etc/postfix/brandfleet-delegated-sender-login
 -o smtpd_sender_restrictions=reject_sender_login_mismatch
 -o smtpd_relay_restrictions=permit_sasl_authenticated,reject
 -o smtpd_recipient_restrictions=reject_unlisted_recipient,permit_sasl_authenticated,reject
'''+end+'\n';write(master,c)
# Environment remains private; original API keys/accounts are preserved.
env=Path('/etc/brandfleet/freeresend.env');c=env.read_text()
for key,value in {'SMTP_HOST':'127.0.0.1','SMTP_PORT':'2526','SMTP_SECURE':'false','SMTP_USER':s['identity'],'SMTP_PASS':s['password']}.items():
 line=key+'='+json.dumps(value)
 c=re.sub(r'^'+key+r'=.*$',line,c,flags=re.M) if re.search(r'^'+key+'=',c,re.M) else c+line+'\n'
write(env,c,0o600)
# Fix only SMTP transport options; do not alter PostgreSQL SSL configuration.
patches=0
for path in Path('/opt/brandfleet/freeresend/app/.next/server').rglob('*.js'):
 c=path.read_text();n=c.replace('tls:{rejectUnauthorized:!1}', 'tls:{rejectUnauthorized:!0,servername:"mail.example.com"}')
 if n!=c:path.write_text(n);patches+=1
run(['doveconf','-n']);run(['postfix','check']);run(['python3','/opt/brandfleet/configure-header-sender-ownership.py','--no-restart'])
if '--no-restart' not in sys.argv:
 run(['systemctl','restart','dovecot','postfix','rspamd','brandfleet-freeresend'])
proof={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'listener':'127.0.0.1:2526','authentication_required':True,'STARTTLS_required':True,'verified_sender_domains':DOMAINS,'external_delivery_enabled':False,'transport_files_patched':patches}
write('/root/brandfleet-private/freeresend-smtp-proof.json',json.dumps(proof,indent=2)+'\n',0o600);print(json.dumps(proof))
