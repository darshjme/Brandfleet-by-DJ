#!/usr/bin/python3
"""Fixed pre-stop gate: pause intake, refuse a busy stop, never kill jobs."""
import datetime,json,os,pathlib,subprocess,time
PAUSE=pathlib.Path('/etc/brandfleet/freescout-workers-paused');RUN=pathlib.Path('/run/brandfleet')
def reserved():
 root=pathlib.Path('/srv/brandfleet/stacks/freescout-mariadb');private=json.loads(pathlib.Path('/srv/brandfleet/env-private.json').read_text());password=private.get('MARIADB_ROOT_PASSWORD')or private.get('MYSQL_ROOT_PASSWORD');assert password
 auth=root/'run/brandfleet-mariadb/drain-client-private.cnf';auth.write_text('[client]\nuser=root\npassword="'+password.replace('\\','\\\\').replace('"','\\"')+'"\n');auth.chmod(0o600)
 try:
  r=subprocess.run(['/usr/sbin/chroot',str(root),'/usr/bin/mariadb','--defaults-extra-file=/run/brandfleet-mariadb/drain-client-private.cnf','--protocol=socket','--socket=/run/brandfleet-mariadb/mysqld.sock','--batch','--skip-column-names','--execute','SELECT COUNT(*) FROM freescout.jobs WHERE reserved_at IS NOT NULL;'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=3,check=True);return int(r.stdout.strip())
 finally:auth.unlink(missing_ok=True)
def main():
 os.umask(0o077);assert os.geteuid()==0
 PAUSE.write_text('Platform stop requested; explicit release resumes intake.\n');PAUSE.chmod(0o600)
 deadline=time.monotonic()+20;reason='active work'
 while True:
  try:
   status=json.loads((RUN/'native-status.json').read_text());ids={s['id']for s in status['services']}
   enabled='freescout-worker'in ids or'freescout-scheduler'in ids
   if enabled:
    states=[json.loads((RUN/('freescout-'+mode+'-state.json')).read_text())for mode in ['worker','scheduler']]
    fresh=all(-3<=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(s['at'])).total_seconds()<=3 for s in states)
    idle=fresh and all(s['paused']and not s['childRunning']for s in states)
   else:idle=True
   count=reserved()
   if idle and count==0:
    print(json.dumps({'ok':True,'paused':True,'reservedJobs':0,'workersDrained':True,'jobsKilled':False}));return 0
   reason='active worker/scheduler or reserved jobs'
  except(Exception):reason='drain evidence unavailable'
  if time.monotonic()>=deadline:
   print(json.dumps({'ok':False,'paused':True,'reason':reason,'stopRefused':True,'jobsKilled':False}));return 2
  time.sleep(.25)
if __name__=='__main__':raise SystemExit(main())
