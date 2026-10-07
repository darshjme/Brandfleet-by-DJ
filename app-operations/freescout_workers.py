#!/usr/bin/python3
"""One Laravel worker and one minute scheduler; a durable pause drains intake."""
import argparse,datetime,json,os,pathlib,signal,subprocess,time,zoneinfo
PAUSE=pathlib.Path('/etc/brandfleet/freescout-workers-paused')
RUN=pathlib.Path('/run/brandfleet')
stop=False
def stopping(*_):
 global stop
 stop=True
def main(mode):
 os.umask(0o077);RUN.mkdir(parents=True,exist_ok=True)
 signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
 state=RUN/('freescout-'+mode+'-state.json');child=None;signalled=False;last_minute=None
 def publish():
  tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':mode,'paused':PAUSE.exists(),'stopping':stop,'childPid':child.pid if child and child.poll()is None else None,'childRunning':bool(child and child.poll()is None),'singleWorker':mode=='worker'})+'\n');tmp.replace(state)
 while not stop:
  paused=PAUSE.exists()
  if child and child.poll()is not None:child=None;signalled=False
  # Laravel's pcntl handler finishes the current job before exiting. Signal
  # only its PID so a subprocess belonging to that job is not interrupted.
  if paused and mode=='worker'and child and not signalled:
   child.send_signal(signal.SIGTERM);signalled=True
  minute=int(time.time()//60)
  if child is None and not paused:
   try:
    status=json.loads((RUN/'native-status.json').read_text());ready=not status.get('stopping')and all(next(x for x in status['services']if x['id']==n)['running']for n in ['freescout-mariadb','freescout-php','freescout-nginx'])
   except(OSError,ValueError,KeyError,StopIteration):ready=False
   local=datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Helsinki'));rotate=RUN/'freescout-logrotate-day'
   if ready and mode=='scheduler'and local.strftime('%H:%M')=='23:59'and(not rotate.exists()or rotate.read_text()!=local.date().isoformat()):
    child=subprocess.Popen(['/usr/bin/python3','/srv/brandfleet/native-entry.py','freescout-logrotate']);rotate.write_text(local.date().isoformat())
   elif ready and(mode=='worker'or minute!=last_minute):
    child=subprocess.Popen(['/usr/bin/python3','/srv/brandfleet/native-entry.py','freescout-'+mode]);last_minute=minute
  publish();time.sleep(.25)
 # The platform pre-stop gate guarantees both children have drained before
 # this wrapper receives TERM. A direct signal still lets an idle worker exit.
 if child and child.poll()is None and mode=='worker':child.send_signal(signal.SIGTERM)
 deadline=time.monotonic()+20
 while child and child.poll()is None and time.monotonic()<deadline:publish();time.sleep(.25)
 publish()
 if child and child.poll()is None:raise SystemExit('Active work did not drain; platform pre-stop gate was bypassed')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--mode',choices=['worker','scheduler'],required=True);main(p.parse_args().mode)
