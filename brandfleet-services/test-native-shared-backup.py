"""Meaningful failure/recovery checks; never touches live infrastructure."""
import importlib.util,json,pathlib,sqlite3,tempfile,unittest
from unittest import mock
spec=importlib.util.spec_from_file_location('native_backup',pathlib.Path(__file__).with_name('native-shared-backup.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class BackupSafety(unittest.TestCase):
 def test_disk_floor_requires_payload_and_reserve(self):
  with self.assertRaisesRegex(RuntimeError,'reserve'):m.storage_guard(m.FLOOR+99,100)
  self.assertEqual(m.storage_guard(m.FLOOR+300,100,3)['requiredBytes'],m.FLOOR+300)
 def test_failed_unit_cannot_enter_snapshot(self):
  with mock.patch.object(m,'guest',return_value=b'LoadState=loaded\nActiveState=failed\n'):
   with self.assertRaisesRegex(RuntimeError,'failed'):m.unit_state('apache2.service')
 def test_missing_unit_refused(self):
  with mock.patch.object(m,'guest',return_value=b'LoadState=not-found\nActiveState=inactive\n'):
   with self.assertRaises(RuntimeError):m.unit_state('apache2.service')
 def test_recovery_preserves_previously_inactive_units(self):
  with tempfile.TemporaryDirectory()as d:
   base=pathlib.Path(d);states={u:'inactive'if u=='sogo.service'else'active'for u in m.UNITS};current={u:'inactive'for u in m.UNITS};calls=[]
   m.write_json(base/'active-operation.json',{'units':states,'snapshot':'owned-test'})
   def guest(args,**kw):
    calls.append(args)
    if args[:2]==['systemctl','start']:current[args[2]]='active'
    if args[:2]==['systemctl','stop']:current[args[2]]='inactive'
    if args[:2]==['systemctl','show']:return ('LoadState=loaded\nActiveState='+current[args[2]]+'\n').encode()
    return b''
   with mock.patch.object(m,'BASE',base),mock.patch.object(m,'unit_state',side_effect=lambda u:current[u]),mock.patch.object(m,'guest',side_effect=guest):
    self.assertTrue(m.recover_current()['allUnitStatesRestored'])
   self.assertEqual(current,states);self.assertNotIn(['systemctl','start','sogo.service'],calls)
   self.assertFalse((base/'active-operation.json').exists())
 def test_recovery_injected_unit_refused_without_call(self):
  with tempfile.TemporaryDirectory()as d:
   base=pathlib.Path(d);m.write_json(base/'active-operation.json',{'units':{'arbitrary.service':'active'}})
   with mock.patch.object(m,'BASE',base),mock.patch.object(m,'guest')as g:
    with self.assertRaisesRegex(RuntimeError,'allowlist'):m.recover_current()
    g.assert_not_called()
 def test_pure_python_alarm_restores_original_unit_states(self):
  import time
  with tempfile.TemporaryDirectory()as d:
   base=pathlib.Path(d);states={u:'active' for u in m.UNITS};current={u:'inactive' for u in m.UNITS}
   m.write_json(base/'active-operation.json',{'units':states,'snapshot':'owned-test'})
   def guest(args,**kw):
    if args[:2]==['systemctl','start']:current[args[2]]='active'
    if args[:2]==['systemctl','show']:return ('LoadState=loaded\nActiveState='+current[args[2]]+'\n').encode()
    return b''
   with mock.patch.object(m,'BASE',base),mock.patch.object(m,'QUIESCE_SECONDS',0.02),mock.patch.object(m,'unit_state',side_effect=lambda u:current[u]),mock.patch.object(m,'guest',side_effect=guest):
    with self.assertRaises(TimeoutError):
     with m.quiesced_capture():
      until=time.monotonic()+0.3
      while time.monotonic()<until:pass
   self.assertEqual(current,states);self.assertFalse((base/'active-operation.json').exists())
 def test_sqlite_online_backup_restores_blob_rows_and_quote_table(self):
  with tempfile.TemporaryDirectory()as d:
   a=pathlib.Path(d)/'source.sqlite';b=pathlib.Path(d)/'backup.sqlite';c=pathlib.Path(d)/'restored.sqlite'
   db=sqlite3.connect(a);db.execute('CREATE TABLE "odd""table" (id integer,data blob)');db.execute('INSERT INTO "odd""table" VALUES (?,?)',(1,b'\x00\xff'));db.commit();db.close()
   sig=m.sqlite_snapshot(a,b);self.assertEqual(sig,m.sqlite_snapshot(b,c));self.assertEqual(sig['odd"table']['rows'],1)
 def test_corrupt_sqlite_refused(self):
  with tempfile.TemporaryDirectory()as d:
   a=pathlib.Path(d)/'broken.sqlite';a.write_bytes(b'corrupt data')
   with self.assertRaises(sqlite3.DatabaseError):m.sqlite_snapshot(a,pathlib.Path(d)/'copy.sqlite')
 def test_rejected_archive_terminates_decompressor(self):
  import base64,io,subprocess,sys,tarfile
  data=io.BytesIO()
  with tarfile.open(fileobj=data,mode='w')as t:
   item=tarfile.TarInfo('../escape');item.size=0;t.addfile(item,io.BytesIO())
  command="import base64,sys,time;sys.stdout.buffer.write(base64.b64decode('"+base64.b64encode(data.getvalue()).decode()+"'));sys.stdout.buffer.flush();time.sleep(30)"
  real_popen=subprocess.Popen;children=[]
  def child(*args,**kwargs):
   proc=real_popen([sys.executable,'-c',command],stdout=subprocess.PIPE,stderr=subprocess.PIPE);children.append(proc);return proc
  with tempfile.TemporaryDirectory()as d,mock.patch.object(m.subprocess,'Popen',side_effect=child):
   with self.assertRaisesRegex(RuntimeError,'traversal'):m.validate(pathlib.Path(d),{'files':{},'paths':['var/lib/nextcloud-final-20261006064840']})
  self.assertIsNotNone(children[0].poll());self.assertTrue(children[0].stdout.closed)
 def test_exact_log_append_requires_unchanged_prefix_and_no_truncation(self):
  path='var/lib/nextcloud-final-20261006064840/nextcloud.log';expected={'bytes':10,'sha256':'verified-prefix'}
  self.assertTrue(m.diagnostic_append(path,path,expected,{'bytes':12},'verified-prefix'))
  self.assertFalse(m.diagnostic_append(path.replace('nextcloud.log','config.php'),path,expected,{'bytes':12},'verified-prefix'))
  self.assertFalse(m.diagnostic_append(path,path,expected,{'bytes':9},'verified-prefix'))
  self.assertFalse(m.diagnostic_append(path,path,expected,{'bytes':12},'rewritten-prefix'))
 def test_quiesce_deadline_prevents_late_mutation(self):
  with mock.patch.object(m,'DEADLINE',0),mock.patch.object(m.subprocess,'run')as r:
   with self.assertRaisesRegex(RuntimeError,'quiesce'):m.run(['echo','late'])
   r.assert_not_called()
 def test_compiled_cache_regular_files_are_covered(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);p=root/'opt'/'app'/'__pycache__'/'compiled.pyc';p.parent.mkdir(parents=True);p.write_bytes(b'compiled-cache')
   with mock.patch.object(m,'ROOT',root):records=m.scan_files(['opt/app'])
   self.assertIn('opt/app/__pycache__/compiled.pyc',records)
if __name__=='__main__':unittest.main()
