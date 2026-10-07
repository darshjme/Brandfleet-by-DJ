#!/usr/bin/python3
"""Loopback-only Android display/control fallback; proxy must require dashboard auth."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
ID = re.compile(r'^bf-[a-z0-9][a-z0-9-]{0,40}$')
LXC = Path('/var/lib/lxc')
def owned(name):
    if not ID.fullmatch(name): raise ValueError('Invalid device')
    data=json.loads((LXC/name/'brandfleet.json').read_text())
    if data.get('name') != name: raise ValueError('Unmanaged device')
    return data
def run(args, timeout=8):
    return subprocess.run(args,capture_output=True,check=True,timeout=timeout).stdout
VIEW='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Android control</title><style>body{margin:0;background:#111827;color:#e5e7eb;font:15px system-ui;text-align:center}header{padding:12px}img{max-height:78vh;max-width:100%;touch-action:none;user-select:none}button{background:#334155;color:white;border:0;border-radius:8px;margin:4px;padding:10px 15px}small{display:block;color:#94a3b8}input{border-radius:6px;padding:9px}</style></head><body><header>NAME<small>Live Android display · snapshot refresh · encrypted dashboard session</small></header><img id="screen" draggable="false"><div><button data-key="4">Back</button><button data-key="3">Home</button><button data-key="187">Apps</button><button data-key="24">Volume +</button><button data-key="25">Volume −</button></div><div><input id="text" autocomplete="off" type="password" placeholder="Type into Android focused field"><button id="send">Send text</button><button data-key="66">Enter</button></div><small id="status">Connecting…</small><script>const id='DEVICE',img=document.getElementById('screen'),status=document.getElementById('status'),url=new URL('../screen/'+id,location.href),inputUrl=new URL('../input/'+id,location.href);let refresh=true;async function shot(){if(!refresh)return;try{const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error('Device unavailable');const blob=await r.blob(),old=img.src;img.src=URL.createObjectURL(blob);img.onload=()=>{if(old.startsWith('blob:'))URL.revokeObjectURL(old)};status.textContent='Connected · tap or swipe screen';}catch(e){status.textContent=e.message}setTimeout(shot,1500)}async function input(d){const r=await fetch(inputUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});if(!r.ok)status.textContent='Control failed';}document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>input({action:'key',key:Number(b.dataset.key)}));document.getElementById('send').onclick=()=>{let t=document.getElementById('text');input({action:'text',text:t.value});t.value=''};let start;function point(e){const r=img.getBoundingClientRect();return[Math.round((e.clientX-r.left)/r.width*img.naturalWidth),Math.round((e.clientY-r.top)/r.height*img.naturalHeight)]}img.onpointerdown=e=>{start=point(e);img.setPointerCapture(e.pointerId)};img.onpointerup=e=>{const end=point(e);if(!start)return;const d=Math.abs(start[0]-end[0])+Math.abs(start[1]-end[1]);input(d<20?{action:'tap',x:end[0],y:end[1]}:{action:'swipe',x:start[0],y:start[1],x2:end[0],y2:end[1],duration:300});start=null};shot();</script></body></html>'''
SOCIAL_APPS = {'instagram':'com.instagram.android','linkedin':'com.linkedin.android','whatsapp':'com.whatsapp','x':'com.twitter.android','figma':'com.figma.mirror'}
KEYS = {3,4,19,20,21,22,24,25,26,61,66,67,187}
class DeviceBusy(ValueError): pass
class DeviceUnavailable(RuntimeError): pass
class ScreenCache:
    """Demand-only capture: share one short-lived frame per device across viewers."""
    def __init__(self, ttl=.8, concurrent=2, clock=time.monotonic):
        self.ttl=ttl; self.clock=clock; self.slots=threading.BoundedSemaphore(concurrent)
        self.guard=threading.Lock(); self.entries={}; self.locks={}
    def lock(self, name):
        with self.guard:
            if name not in self.locks:
                if len(self.locks)>=32: raise DeviceBusy('Display capacity reached')
                self.locks[name]=threading.Lock()
            return self.locks[name]
    def invalidate(self, name):
        self.entries.pop(name,None)
    def capture(self, name, serial, capture):
        lock=self.lock(name)
        if not lock.acquire(timeout=7): raise DeviceBusy('Device display busy')
        try:
            cached=self.entries.get(name)
            if cached and cached[0]==serial and self.clock()-cached[1]<self.ttl:
                return cached[2], True
            if not self.slots.acquire(timeout=2): raise DeviceBusy('Display capture busy')
            try: body=capture()
            finally: self.slots.release()
            if not body.startswith(bytes([137,80,78,71,13,10,26,10])): raise ValueError('Screenshot unavailable')
            self.entries[name]=(serial,self.clock(),body)
            return body,False
        finally: lock.release()
SCREENS=ScreenCache()
def attach(name,args,timeout=8):
    return run(['lxc-attach','-n',name,'--',*args],timeout)
def require_running(name):
    if run(['lxc-info','-n',name,'-sH']).strip()!=b'RUNNING':raise DeviceUnavailable('Device is not running')
def foreground(name):
    # Redroid14's `window windows` section omits focus; use the full window dump.
    text=attach(name,['/system/bin/dumpsys','window'],timeout=3).decode('utf-8','replace')
    # Return only package identity, never text from the active application.
    for pattern in [r'mCurrentFocus=Window\{[^}]*?\bu\d+\s+([A-Za-z0-9_.]+)/',r'mFocusedApp=.*?\bu\d+\s+([A-Za-z0-9_.]+)/']:
        match=re.search(pattern,text)
        if match:return match[1]
    return ''
def launch_social(name, app):
    if not isinstance(app,str) or app not in SOCIAL_APPS:raise ValueError('Unsupported app')
    data=owned(name)
    if data.get('migrated') is not True:raise DeviceUnavailable('Device migration is not accepted')
    require_running(name)
    if attach(name,['/system/bin/getprop','sys.boot_completed']).strip()!=b'1':raise DeviceUnavailable('Android boot is incomplete')
    package=SOCIAL_APPS[app]
    lock=SCREENS.lock(name)
    if not lock.acquire(timeout=4):raise DeviceBusy('Device control busy')
    try:
        raw=attach(name,['/system/bin/cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER',package]).decode('utf-8','replace').strip().splitlines()
        component=raw[-1] if raw else ''
        if not re.fullmatch(re.escape(package)+r'/[A-Za-z0-9_.$]+',component):raise DeviceUnavailable('App is unavailable on this device')
        # `am` is a shell wrapper using bare `cmd`; systemd's host PATH omits
        # Android binaries. Invoke the activity service directly without a shell.
        result=attach(name,['/system/bin/cmd','activity','start-activity','-W','-n',component],timeout=12).decode('utf-8','replace')
        if 'Status: ok' not in result:raise DeviceUnavailable('App did not report a successful launch')
        # Activity launch completion can precede focus assignment (observed LinkedIn).
        # Bound the confirmation wait; return honest false if a dialog owns focus.
        deadline=time.monotonic()+2
        current=foreground(name)
        while current!=package and time.monotonic()<deadline:
            time.sleep(.12);current=foreground(name)
        return {'ok':True,'app':app,'package':package,'launchedAt':datetime.now(timezone.utc).isoformat(),'foregroundConfirmed':current==package,'foregroundPackage':current,'viewerUrl':'/android/view/'+name}
    finally:
        SCREENS.invalidate(name);lock.release()
def input_command(d):
    def coord(k):
        n=d.get(k)
        if type(n)!=int or not 0<=n<=4096:raise ValueError('Invalid coordinate')
        return str(n)
    action=d.get('action')
    if action=='tap':return ['tap',coord('x'),coord('y')]
    if action=='swipe':
        duration=d.get('duration',300)
        if type(duration)!=int or not 50<=duration<=2000:raise ValueError('Invalid duration')
        return ['swipe',coord('x'),coord('y'),coord('x2'),coord('y2'),str(duration)]
    if action=='key':
        key=d.get('key')
        if type(key)!=int or key not in KEYS:raise ValueError('Unsupported key')
        return ['keyevent',str(key)]
    if action=='text':
        value=d.get('text')
        if not isinstance(value,str) or not 0<len(value)<=200 or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Invalid text')
        return ['text',value.replace(' ','%s')]
    raise ValueError('Unsupported action')
def device_input(name, d):
    owned(name);require_running(name);cmd=input_command(d);lock=SCREENS.lock(name)
    if not lock.acquire(timeout=4):raise DeviceBusy('Device control busy')
    try:
        # Direct argv exec avoids Android shell interpretation. Never log or echo text.
        attach(name,['/system/bin/cmd','input',*cmd])
        return {'ok':True,'action':d['action'],'appliedAt':datetime.now(timezone.utc).isoformat()}
    finally:SCREENS.invalidate(name);lock.release()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,body,kind='application/json',status=200,cache_hit=None):
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if cache_hit is not None:self.send_header('X-BrandFleet-Capture', 'shared-frame' if cache_hit else 'fresh')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        try:
            path=urlparse(self.path).path.strip('/').split('/')
            if len(path)!=2 or path[0] not in ['view','screen']:raise ValueError('Unknown route')
            data=owned(path[1])
            if path[0]=='view':return self.respond(VIEW.replace('DEVICE',path[1]).replace('NAME',html.escape(data.get('displayName',path[1]))).encode(),'text/html; charset=utf-8')
            if not re.fullmatch(r'10\.77\.0\.(?:[1-3][0-9]|4[0-2])',data['ip']):raise ValueError('Invalid address')
            serial=data['ip']+':5555'
            body,cached=SCREENS.capture(path[1],serial,lambda:run(['adb','-s',serial,'exec-out','screencap','-p'],timeout=6))
            self.respond(body,'image/png',cache_hit=cached)
        except DeviceBusy:self.respond(b'{"ok":false,"error":"Device display is busy; retry shortly"}',status=409)
        except (ValueError,DeviceUnavailable,OSError,subprocess.SubprocessError):self.respond(b'{"ok":false,"error":"Device unavailable"}',status=503)
    def do_POST(self):
        try:
            path=urlparse(self.path).path.strip('/').split('/')
            if len(path)!=2 or path[0] not in ['input','launch']:raise ValueError('Unknown route')
            owned(path[1]);size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request size')
            d=json.loads(self.rfile.read(size))
            if not isinstance(d,dict):raise ValueError('Invalid body')
            if path[0]=='launch':
                if set(d)!={'app'}:raise ValueError('Unsupported launch fields')
                result=launch_social(path[1],d['app'])
            else:result=device_input(path[1],d)
            self.respond(json.dumps(result).encode())
        except DeviceBusy:self.respond(b'{"ok":false,"error":"Device control is busy; retry shortly"}',status=409)
        except ValueError:self.respond(b'{"ok":false,"error":"Invalid device control request"}',status=400)
        except (DeviceUnavailable,OSError,subprocess.SubprocessError):self.respond(b'{"ok":false,"error":"Device control service unavailable; retry shortly"}',status=503)
if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',9002),Handler).serve_forever()
