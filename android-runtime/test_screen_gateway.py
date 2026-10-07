import importlib.util
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
import subprocess

spec=importlib.util.spec_from_file_location('gateway',Path(__file__).with_name('screen-gateway.py'))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
PNG=bytes([137,80,78,71,13,10,26,10])+b'test'
class GatewayTests(unittest.TestCase):
 def test_same_device_concurrent_viewers_share_capture_and_input_invalidates(self):
  cache=g.ScreenCache(ttl=10);barrier=threading.Barrier(5);calls=[];rows=[]
  def capture():calls.append(1);time.sleep(.05);return PNG
  def worker():barrier.wait();rows.append(cache.capture('bf-test','10.77.0.12:5555',capture))
  threads=[threading.Thread(target=worker) for _ in range(5)]
  for t in threads:t.start()
  for t in threads:t.join(2)
  self.assertEqual(len(calls),1);self.assertEqual(len(rows),5);self.assertEqual(sum(hit for _,hit in rows),4)
  with patch.object(g,'SCREENS',cache),patch.object(g,'owned'),patch.object(g,'require_running'),patch.object(g,'attach') as command:
   result=g.device_input('bf-test',{'action':'text','text':'quote " $() ; literal'})
   self.assertNotIn('text',result)
   self.assertEqual(command.call_args.args[1],['/system/bin/cmd','input','text','quote%s"%s$()%s;%sliteral'])
  cache.capture('bf-test','10.77.0.12:5555',capture);self.assertEqual(len(calls),2)
 def test_expiry_and_changed_serial_cannot_reuse_wrong_frame(self):
  clock=[0];cache=g.ScreenCache(ttl=.8,clock=lambda:clock[0]);calls=[]
  def capture():calls.append(1);return PNG
  cache.capture('bf-test','a',capture);clock[0]=.9;cache.capture('bf-test','a',capture)
  cache.capture('bf-test','b',capture);self.assertEqual(len(calls),3)
 def test_capture_global_concurrency_is_bounded(self):
  cache=g.ScreenCache(concurrent=2);live=[0];peak=[0];lock=threading.Lock();threads=[]
  def capture():
   with lock:live[0]+=1;peak[0]=max(peak[0],live[0])
   time.sleep(.03)
   with lock:live[0]-=1
   return PNG
  for i in range(6):threads.append(threading.Thread(target=lambda i=i:cache.capture('bf-'+str(i),str(i),capture)))
  for t in threads:t.start()
  for t in threads:t.join(2)
  self.assertEqual(peak[0],2)
 def test_capture_rejects_invalid_png_and_never_caches_it(self):
  cache=g.ScreenCache()
  with self.assertRaises(ValueError):cache.capture('bf-test','x',lambda:b'not a png')
  self.assertNotIn('bf-test',cache.entries)
 def test_input_type_and_allowlist_reject_shell_and_invalid_values(self):
  for payload in [{'action':'shell','text':'id'},{'action':'key','key':True},{'action':'key','key':999},{'action':'tap','x':False,'y':2},{'action':'text','text':'a\x00b'},{'action':'text','text':'a\nb'},{'action':'swipe','x':1,'y':2,'x2':3,'y2':4,'duration':1}]:
   with self.assertRaises(ValueError):g.input_command(payload)
  for key in (3,4,19,20,21,22,61,66,67,187):self.assertEqual(g.input_command({'action':'key','key':key}),['keyevent',str(key)])
 def test_launcher_allowlist_and_installed_component_validation(self):
  with patch.object(g,'owned',return_value={'migrated':True}),patch.object(g,'require_running'),patch.object(g,'attach',side_effect=[b'1',b'com.instagram.android/com.instagram.mainactivity\n',b'Status: ok\n']) as commands,patch.object(g,'foreground',return_value='com.instagram.android'),patch.object(g,'SCREENS',g.ScreenCache()):
   result=g.launch_social('bf-test','instagram');self.assertTrue(result['foregroundConfirmed']);self.assertEqual(result['viewerUrl'],'/android/view/bf-test')
   self.assertEqual(commands.call_args.args[1],['/system/bin/cmd','activity','start-activity','-W','-n','com.instagram.android/com.instagram.mainactivity'])
  with patch.object(g,'owned',return_value={'migrated':True}),patch.object(g,'require_running'),patch.object(g,'attach',side_effect=[b'1',b'arbitrary.pkg/Main']):
   with self.assertRaises(g.DeviceUnavailable):g.launch_social('bf-test','instagram')
  with patch.object(g,'owned') as read:
   with self.assertRaises(ValueError):g.launch_social('bf-test','com.other/.Main')
   read.assert_not_called()
 def test_launcher_refuses_unaccepted_or_unbooted_device(self):
  with patch.object(g,'owned',return_value={'migrated':False}),patch.object(g,'attach') as command:
   with self.assertRaises(g.DeviceUnavailable):g.launch_social('bf-test','instagram')
   command.assert_not_called()
  with patch.object(g,'owned',return_value={'migrated':True}),patch.object(g,'require_running'),patch.object(g,'attach',return_value=b'0'):
   with self.assertRaises(g.DeviceUnavailable):g.launch_social('bf-test','instagram')
 def test_foreground_returns_only_package_not_ui_text(self):
  with patch.object(g,'attach',return_value=b'mCurrentFocus=Window{ab u0 com.whatsapp/.Main}\nprivate secret text'):
   self.assertEqual(g.foreground('bf-test'),'com.whatsapp')
 def test_launcher_waits_for_actual_delayed_foreground_without_force_stop(self):
  with patch.object(g,'owned',return_value={'migrated':True}),patch.object(g,'require_running'),patch.object(g,'attach',side_effect=[b'1',b'com.linkedin.android/.Main',b'Status: ok']) as commands,patch.object(g,'foreground',side_effect=['com.android.launcher3','com.linkedin.android']),patch.object(g,'SCREENS',g.ScreenCache()):
   result=g.launch_social('bf-test','linkedin');self.assertTrue(result['foregroundConfirmed']);self.assertFalse(any('force-stop' in call.args[1] for call in commands.call_args_list))
 def test_actual_http_errors_distinguish_request_busy_and_runtime_failure(self):
  server=g.ThreadingHTTPServer(('127.0.0.1',0),g.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   for error,status in [(ValueError('unsupported'),400),(g.DeviceBusy('busy'),409),(g.DeviceUnavailable('absent'),503),(subprocess.TimeoutExpired(['fixed-command'],12),503),(subprocess.CalledProcessError(127,['fixed-command'],stderr=b'private diagnostics'),503),(OSError('private diagnostics'),503)]:
    with self.subTest(error=type(error).__name__),patch.object(g,'owned'),patch.object(g,'launch_social',side_effect=error):
     request=Request('http://127.0.0.1:'+str(server.server_port)+'/launch/bf-test',data=b'{"app":"instagram"}',headers={'Content-Type':'application/json'})
     with self.assertRaises(HTTPError) as response:urlopen(request,timeout=2)
     self.assertEqual(response.exception.code,status);body=json.load(response.exception);self.assertFalse(body['ok']);self.assertNotIn('private',body['error'])
  finally:server.shutdown();server.server_close();thread.join(2)
if __name__=='__main__':unittest.main()
