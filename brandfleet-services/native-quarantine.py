#!/usr/bin/python3
"""Private full-message quarantine, fixed JSON admin interface and local digests."""
import base64,hashlib,json,os,re,sqlite3,subprocess,sys,time
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
BASE=Path('/var/lib/brandfleet-quarantine');DB=BASE/'quarantine.db'
def database():
 BASE.mkdir(mode=0o700,exist_ok=True)
 db=sqlite3.connect(DB);db.row_factory=sqlite3.Row
 db.executescript('CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,received INTEGER,recipient TEXT,sender TEXT,subject TEXT,category TEXT,score REAL,notified INTEGER DEFAULT 0,released INTEGER DEFAULT 0,raw BLOB);CREATE TABLE IF NOT EXISTS throttle(recipient TEXT PRIMARY KEY,last INTEGER);')
 os.chmod(DB,0o600);return db
def state():return json.loads(Path('/etc/brandfleet-mail/state.json').read_text())
def receive(raw,meta):
 assert len(raw)<=30*1024**2
 boxes=state()['mailboxes'];recipients=meta.get('rcpt',[])
 if isinstance(recipients,str):
  recipients=json.loads(recipients) if recipients.lstrip().startswith('[') else [v.strip() for v in recipients.split(',')]
 category=meta.get('action','reject');score=float(meta.get('score',0));created=[]
 if category not in ('reject','add header','rewrite subject'):return created
 message=BytesParser(policy=policy.default).parsebytes(raw)
 with database() as db:
  for recipient in recipients:
   recipient=recipient.lower()
   if recipient not in boxes or not boxes[recipient]['active']:continue
   digest=hashlib.sha256(raw+b'\0'+recipient.encode()).hexdigest()
   db.execute('INSERT OR IGNORE INTO messages(id,received,recipient,sender,subject,category,score,raw) VALUES(?,?,?,?,?,?,?,?)',(digest,int(time.time()),recipient,str(meta.get('from','unknown')),str(message.get('Subject',''))[:1000],category,score,raw));created.append(digest)
 return created
def deliver(recipient,raw):
 subprocess.run(['doveadm','save','-u',recipient,'-m','INBOX'],input=raw,capture_output=True,check=True,timeout=30)
def action(request):
 operation=request.get('action');db=database();current=state();boxes=current['mailboxes']
 if operation=='quarantine_list':
  rows=[dict(v) for v in db.execute('SELECT id,received,recipient,sender,subject,category,score,notified,released,length(raw) AS size FROM messages ORDER BY received DESC LIMIT 500')]
  return {'ok':True,'messages':rows,'total':db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],'notifications_enabled':Path('/etc/brandfleet-mail/notifications-enabled').exists(),'notification_schedule_seconds':1200,'notification_throttle_seconds':3600,'max_age_days':365,'outbound_enabled':False}
 if operation in ('quarantine_release','quarantine_delete'):
  identifier=request.get('id')
  if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{64}',identifier):raise ValueError('invalid quarantine identifier')
  row=db.execute('SELECT * FROM messages WHERE id=?',(identifier,)).fetchone()
  if row is None:raise ValueError('quarantine message not found')
  if operation=='quarantine_release':
   if row['recipient'] not in boxes or not boxes[row['recipient']]['active']:raise ValueError('recipient no longer active')
   if row['released']:raise ValueError('message already released')
   deliver(row['recipient'],row['raw']);db.execute('UPDATE messages SET released=1 WHERE id=?',(identifier,))
  else:db.execute('DELETE FROM messages WHERE id=?',(identifier,))
  db.commit();return {'ok':True,'changed':True,'action':operation,'id':identifier,'external_emails_sent':0}
 raise ValueError('unsupported quarantine action')
def notify(fixtures_only=False):
 # Original account frequency/category/throttle preserved. Final outbound/root enable gate controls real recipients.
 enable=Path('/etc/brandfleet-mail/notifications-enabled').exists();db=database();current=state();now=int(time.time());count=0
 db.execute('DELETE FROM messages WHERE received<?',(now-365*86400,))
 for recipient,item in current['mailboxes'].items():
  if fixtures_only and not re.fullmatch(r'pilot@fixture-[a-z0-9]{8,32}\.internal',recipient):continue
  if not fixtures_only and not enable:continue
  attrs=item.get('original_attributes',{});frequency=attrs.get('quarantine_notification','never');category=attrs.get('quarantine_category','reject')
  if frequency!='hourly' or not item['active']:continue
  last=db.execute('SELECT last FROM throttle WHERE recipient=?',(recipient,)).fetchone()
  if last and now-last[0]<3600:continue
  rows=db.execute('SELECT id,subject,sender,category FROM messages WHERE recipient=? AND notified=0 AND released=0 AND score<9999'+(' AND category=\'reject\'' if category!='all' else ''),(recipient,)).fetchall()
  if not rows:continue
  mail=EmailMessage();mail['From']='quarantine@'+recipient.split('@')[1];mail['To']=recipient;mail['Subject']='BrandFleet quarantine summary';mail.set_content('Messages held for review in the authenticated BrandFleet dashboard:\n'+'\n'.join(v['id']+' '+v['subject'] for v in rows))
  deliver(recipient,mail.as_bytes());db.executemany('UPDATE messages SET notified=1 WHERE id=?',[(v['id'],) for v in rows]);db.execute('INSERT INTO throttle VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET last=excluded.last',(recipient,now));count+=1
 db.commit();return {'ok':True,'local_summaries_delivered':count,'external_emails_sent':0,'real_notifications_enabled':enable}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  if self.path!='/ingest' or self.client_address[0]!='127.0.0.1':self.send_error(404);return
  try:
   size=int(self.headers.get('Content-Length',0));assert 0<size<=30*1024**2;raw=self.rfile.read(size)
   meta={name:self.headers.get('X-Rspamd-'+name,'') for name in ('rcpt','from','action','score')}
   created=receive(raw,meta);body=json.dumps({'ok':True,'stored':len(created)}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  except Exception:self.send_error(400)
if __name__=='__main__':
 if os.geteuid()!=0 or Path('/etc/hostname').read_text().strip()!='bf-services':raise SystemExit('native administrator only')
 if sys.argv[1:] == ['serve']:HTTPServer(('127.0.0.1',8106),Handler).serve_forever()
 elif sys.argv[1:] == ['notify']:print(json.dumps(notify()))
 elif sys.argv[1:] == ['test-notify']:print(json.dumps(notify(True)))
 else:
  try:
   raw=sys.stdin.buffer.read(65537)
   if len(raw)>65536:raise ValueError('request too large')
   print(json.dumps(action(json.loads(raw))))
  except Exception as error:print(json.dumps({'ok':False,'error':str(error)}));raise SystemExit(2)
