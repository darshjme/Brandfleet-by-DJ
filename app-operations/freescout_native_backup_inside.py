"""Scoped native FreeScout backup: drain, maintenance, SQL+stable private mounts."""
import datetime,fcntl,gzip,hashlib,json,os,pathlib,secrets,signal,subprocess,tarfile,time,shutil
C=pathlib.Path('/etc/brandfleet');S=pathlib.Path('/srv/brandfleet');APP=S/'stacks/freescout';DB=S/'stacks/freescout-mariadb';BACK=S/'backups/freescout';PAUSE=C/'freescout-workers-paused'
def require(x,m):
 if not x:raise RuntimeError(m)
def storage_guard(path,payload_bytes):
 required=8*1024**3+payload_bytes;free=shutil.disk_usage(path).free
 require(free>=required,'Backup storage floor refused: free='+str(free)+' required='+str(required))
 return {'freeBytes':free,'requiredBytes':required}
def sha(p):
 h=hashlib.sha256()
 with p.open('rb')as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def run(args,**kw):
 p=subprocess.run(args,capture_output=True,timeout=300,**kw);require(p.returncode==0,'Protected backup command failed');return p.stdout
def php(*args):
 service=next(x for x in json.loads((C/'native-runtime-private.json').read_text())['services']if x['id']=='freescout-php')
 def enter():os.chroot(service['rootfs']);os.chdir('/www/html');os.setgroups([]);os.setgid(82);os.setuid(80)
 return run(['/usr/sbin/php',*args],env=service['env'],preexec_fn=enter)
def inventory(entries):
 result={}
 for p,arc in entries:
  require(p.exists()and not p.is_symlink(),'Backup source must be real')
  for q in [p]+sorted(p.rglob('*'))if p.is_dir()else[p]:
   st=q.lstat();name=arc if q==p else str(pathlib.PurePosixPath(arc)/q.relative_to(p));kind='symlink'if q.is_symlink()else'dir'if q.is_dir()else'file'if q.is_file()else'other';require(kind!='other','Unsupported private file type')
   result[name]={'path':str(q),'kind':kind,'uid':st.st_uid,'gid':st.st_gid,'mode':st.st_mode&0o7777,'bytes':st.st_size if kind=='file'else 0,'sha256':sha(q)if kind=='file'else None,'link':os.readlink(q)if kind=='symlink'else None}
 return result
def stable_archive(entries,target):
 for attempt in range(3):
  before=inventory(entries)
  with tarfile.open(target,'w:gz',compresslevel=1,dereference=False)as t:
   for n,row in before.items():t.add(row['path'],arcname=n,recursive=False)
  if inventory(entries)!=before:continue
  with tarfile.open(target,'r:gz')as t:
   require(len(t.getmembers())==len(before),'Archive coverage differs')
   for m in t.getmembers():
    row=before[m.name];require((m.uid,m.gid,m.mode)==(row['uid'],row['gid'],row['mode']),'Archive ownership differs')
    if m.isfile():
     h=hashlib.sha256()
     with t.extractfile(m)as f:
      for b in iter(lambda:f.read(1048576),b''):h.update(b)
     require(h.hexdigest()==row['sha256'],'Private archive file hash differs')
    if m.issym():require(m.linkname==row['link'],'Archive symlink differs')
  return {'files':sum(r['kind']=='file'for r in before.values()),'bytes':sum(r['bytes']for r in before.values()),'stableInventoryAndOwnershipVerified':True}
 raise RuntimeError('Private mounts changed repeatedly during backup')
def main():
 os.umask(0o077);require(os.geteuid()==0,'Root-only backup');BACK.mkdir(parents=True,mode=0o700,exist_ok=True);require(not BACK.is_symlink(),'Backup path symlink')
 with(BACK/'.lock').open('a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  status=json.loads(pathlib.Path('/run/brandfleet/native-status.json').read_text());require(not status['stopping']and len(status['services'])==5 and all(x['running']for x in status['services']),'Five healthy production services required')
  require(not(APP/'data/storage/framework/down').exists(),'Existing maintenance mode must remain untouched')
  physical_bytes=sum(q.stat().st_size for root in [DB/'var/lib/mysql',APP/'data',APP/'www/logs'] for q in root.rglob('*') if q.is_file()and not q.is_symlink());storage_guard(BACK,2*physical_bytes+1048576)
  waspaused=PAUSE.exists();maintenance=False;stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');name='native-freescout-'+stamp+'-'+secrets.token_hex(4);out=BACK/(name+'.partial');out.mkdir(mode=0o700)
  auth=DB/'run/brandfleet-mariadb/native-backup-client.private.cnf';pw=json.loads((S/'env-private.json').read_text());pw=pw.get('MARIADB_ROOT_PASSWORD')or pw.get('MYSQL_ROOT_PASSWORD');require(pw,'Private DB password unavailable');auth.write_text('[client]\nuser=root\npassword="'+pw.replace('\\','\\\\').replace('"','\\"')+'"\n');auth.chmod(0o600)
  prefix=['/usr/sbin/chroot',str(DB)];client=['--defaults-extra-file=/run/brandfleet-mariadb/native-backup-client.private.cnf','--protocol=socket','--socket=/run/brandfleet-mariadb/mysqld.sock']
  def sql(q):return run(prefix+['/usr/bin/mariadb',*client,'--batch','--skip-column-names','--execute',q]).decode().strip()
  try:
   r=subprocess.run(['/usr/bin/python3',str(S/'freescout-drain.py')],capture_output=True,text=True,timeout=25);require(r.returncode==0,'Active work: backup refused without killing jobs')
   php('/www/html/artisan','down','--no-interaction');maintenance=True;time.sleep(3)
   require(sql('SELECT COUNT(*) FROM information_schema.INNODB_TRX;')=='0','Pending transaction: backup refused')
   tables=sql("SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA='freescout' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;").splitlines();require(len(tables)==24 and all(n.replace('_','').isalnum()for n in tables),'Unexpected application schema')
   counts={t:int(sql('SELECT COUNT(*) FROM freescout.`'+t+'`;'))for t in tables};version=sql('SELECT VERSION();');dump=out/'all-databases.sql.gz'
   with dump.open('xb')as dest,(out/'dump-errors.private.log').open('wb')as err:
    source=subprocess.Popen(prefix+['/usr/bin/mariadb-dump',*client,'--all-databases','--single-transaction','--routines','--events','--triggers','--hex-blob'],stdout=subprocess.PIPE,stderr=err)
    try:
     with gzip.GzipFile(fileobj=dest,mode='wb',compresslevel=1)as pack:
      for block in iter(lambda:source.stdout.read(1048576),b''):pack.write(block)
     require(source.wait(timeout=60)==0,'Native SQL dump failed')
    finally:
     source.stdout.close()
     if source.poll()is None:source.terminate();source.wait(timeout=10)
   run(['gzip','-t',str(dump)])
   with gzip.open(dump)as f:require(b'MariaDB dump'in f.read(1024),'Native SQL header invalid')
   mounts=stable_archive([(APP/'data','mounts/data'),(APP/'www/logs','mounts/www/logs')],out/'mounts-private.tar.gz')
   recovery=stable_archive([(C,'debian/etc/brandfleet'),(S/'env-private.json','debian/srv/brandfleet/env-private.json'),(APP/'etc/nginx','app/etc/nginx'),(APP/'etc/php82','app/etc/php82'),(APP/'etc/hosts','app/etc/hosts'),*[(S/n,'debian/srv/brandfleet/'+n)for n in ['native-supervisor.py','native-entry.py','freescout-workers.py','freescout-drain.py','freescout_database_probe.py']]],out/'runtime-private.tar.gz')
   after={t:int(sql('SELECT COUNT(*) FROM freescout.`'+t+'`;'))for t in tables};require(after==counts,'Application writes changed quiesced backup counts')
   manifest={'id':'bf-freescout','created':datetime.datetime.now(datetime.timezone.utc).isoformat(),'name':name,'version':version,'applicationTables':24,'tableCounts':counts,'queueDrained':True,'webMaintenanceDuringSnapshot':True,'noActiveInnoDbTransactions':True,'mountedState':mounts,'privateRuntime':recovery,'archiveValidated':True,'restoreTested':False,'automaticPruning':False,'artifacts':{n:{'bytes':(out/n).stat().st_size,'sha256':sha(out/n)}for n in ['all-databases.sql.gz','mounts-private.tar.gz','runtime-private.tar.gz']},'restoreNotes':['Restore with exact original Maria11.4.12/PHP8.2 userspaces and existing UID maps.','Restore private mounts/config; remove maintenance marker only after controlled restored readiness.','Clear durable workers pause only after the scoped queue drain gate is installed.']};(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');out.rename(BACK/name);print(json.dumps({'passed':True,'name':name,'tables':24,'archiveValidated':True,'restoreTested':False,'noEmailSent':True}))
  finally:
   auth.unlink(missing_ok=True)
   if maintenance:php('/www/html/artisan','up','--no-interaction')
   if not waspaused:PAUSE.unlink(missing_ok=True)
if __name__=='__main__':
 try:main()
 except Exception:raise SystemExit('Native FreeScout backup failed; protected partial retained, inspect privately')
