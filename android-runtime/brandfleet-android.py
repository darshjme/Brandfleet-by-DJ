#!/usr/bin/python3
"""Fixed-command Android LXC lifecycle. No shell interpolation or credential output."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request

BASE = Path('/opt/brandfleet/android')
SCRIPTS = Path('/opt/brandfleet/android-runtime')
LXC = Path('/var/lib/lxc')
ID_RE = re.compile(r'^bf-[a-z0-9][a-z0-9-]{0,40}$')
DOMAIN_RE = re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')

def run(args, timeout=60, check=True):
    return subprocess.run(args, check=check, timeout=timeout, text=True, capture_output=True)

def owned(instance):
    if not ID_RE.fullmatch(instance):
        raise ValueError('Invalid instance ID')
    path = LXC / instance / 'brandfleet.json'
    if not path.is_file():
        raise ValueError('Instance is not managed by BrandFleet')
    data = json.loads(path.read_text())
    if data.get('name') != instance:
        raise ValueError('Instance ownership metadata mismatch')
    return data

def save(instance, metadata):
    path = LXC / instance / 'brandfleet.json'
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(metadata, indent=2) + '\n')
    tmp.replace(path)

def website_source(instance):
    root = BASE / 'data' / instance / 'brandfleet' / 'www'
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Website source must be an owned directory')
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                if Path(os.readlink(path)).is_absolute() or root.resolve() not in path.resolve().parents:
                    raise ValueError('Website symlink leaves its owned directory')
            elif not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                raise ValueError('Website copy rejects devices, sockets and pipes')
    return root

def native_service_health(root):
    """Verify owned native processes rather than a coincidental HTTP listener."""
    try:
        config = json.loads((root / 'etc/brandfleet/native-services.json').read_text())
        state = json.loads((root / 'run/brandfleet/native-status.json').read_text())
        expected = config['services']; observed = state['services']
        if not isinstance(expected, list) or not expected or not isinstance(observed, list):
            return False, []
        wanted = {item['id']: item.get('enabled', True) for item in expected}
        actual = {item['id']: item for item in observed}
        if len(wanted) != len(expected) or len(actual) != len(observed) or set(wanted) != set(actual) or not any(wanted.values()):
            return False, []
        at = datetime.datetime.fromisoformat(state['at'].replace('Z', '+00:00'))
        age = (datetime.datetime.now(datetime.timezone.utc) - at).total_seconds()
        healthy = not state.get('stopping', False) and -3 <= age <= 15
        summary = []
        for name, enabled in wanted.items():
            row = actual[name]; pid = row.get('pid')
            running = row.get('running') is True and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            healthy = healthy and (running if enabled else not row.get('running', False))
            summary.append({'id': name, 'enabled': bool(enabled), 'running': running, 'restarts': row.get('restarts', 0)})
        return bool(healthy), summary
    except (OSError, ValueError, TypeError, KeyError):
        return False, []

class ManagedHealthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect to the public site must not prove this instance healthy.
        return None

def managed_health_headers(info, key):
    headers = info.get(key, {})
    if not isinstance(headers, dict) or set(headers) - {'Host', 'X-Forwarded-Proto'}:
        raise ValueError('Invalid managed health headers')
    if not headers:
        return {}
    domain = info.get('domain', '')
    if (not isinstance(domain, str) or not DOMAIN_RE.fullmatch(domain)
            or headers != {'Host': domain, 'X-Forwarded-Proto': 'https'}):
        raise ValueError('Managed health headers must identify the owned domain and HTTPS')
    return headers.copy()

def managed_http_health(url, headers):
    opener = urllib.request.build_opener(ManagedHealthRedirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=2) as response:
        return response.status == 200

def status(instance):
    info = owned(instance)
    result = run(['lxc-info', '-n', instance, '-sH'], check=False, timeout=5)
    info['state'] = result.stdout.strip() or 'UNKNOWN'
    info['id'] = instance
    info.setdefault('displayName', instance)
    info.setdefault('domain', '')
    info['runtime'] = 'android-lxc'
    info['adbAddress'] = f"{info['ip']}:5555"
    port = info.get('webPort', 8080)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError('Invalid managed website port')
    health_path = info.get('healthPath', '/')
    if not isinstance(health_path, str) or not health_path.startswith('/') or health_path.startswith('//') or len(health_path) > 512 or any(ord(c) < 32 for c in health_path):
        raise ValueError('Invalid managed website health path')
    info['webPort'] = port; info['healthPath'] = health_path
    info['webAddress'] = f"http://{info['ip']}:{port}"
    info['dataPath'] = str(BASE / 'data' / instance)
    web_headers = managed_health_headers(info, 'healthHeaders')
    linux_headers = managed_health_headers(info, 'linuxHealthHeaders')
    info['bootCompleted'] = False
    info['webHealthy'] = False
    if info['state'] == 'RUNNING':
        boot = run(['lxc-attach', '-n', instance, '--', '/system/bin/getprop', 'sys.boot_completed'], timeout=5, check=False)
        info['bootCompleted'] = boot.stdout.strip() == '1'
        try:
            info['webHealthy'] = managed_http_health(info['webAddress'] + health_path, web_headers)
        except (OSError, ValueError):
            pass
        pid = run(['lxc-info', '-n', instance, '-pH'], timeout=5, check=False).stdout.strip()
        if pid.isdigit():
            info['pid'] = int(pid)
            cg = Path('/proc') / pid / 'cgroup'
            if cg.exists():
                line = next((x for x in cg.read_text().splitlines() if x.startswith('0::')), '')
                path = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
                for key, filename in [('memoryBytes', 'memory.current'), ('pids', 'pids.current')]:
                    value = path / filename
                    if value.exists():
                        info[key] = int(value.read_text().strip())
    if info['state'] == 'RUNNING' and (info.get('linuxPort') or info.get('requiresLinux')):
        info['linuxHealthy'] = False
        linux_port = info.get('linuxPort', 8081)
        linux_path = info.get('linuxHealthPath', '/health')
        if not isinstance(linux_port, int) or isinstance(linux_port, bool) or not 1 <= linux_port <= 65535:
            raise ValueError('Invalid managed Linux health port')
        if not isinstance(linux_path, str) or not linux_path.startswith('/') or linux_path.startswith('//') or len(linux_path) > 512 or any(ord(c) < 32 for c in linux_path):
            raise ValueError('Invalid managed Linux health path')
        try:
            info['linuxHealthy'] = managed_http_health(f"http://{info['ip']}:{linux_port}{linux_path}", linux_headers)
        except (OSError, ValueError):
            pass
    if info.get('nativeRuntime'):
        info['nativeHealthy'], info['nativeServices'] = native_service_health(BASE / 'data' / instance / 'brandfleet/debian')
        info['nativeHealthy'] = info['state'] == 'RUNNING' and info['nativeHealthy']
    info['ready'] = info['bootCompleted'] and info['webHealthy'] and (not info.get('requiresLinux', False) or info.get('linuxHealthy', False)) and (not info.get('nativeRuntime', False) or info.get('nativeHealthy', False))
    return info

def available_slot():
    used = set()
    for path in (BASE / 'data').glob('*/brandfleet/metadata.env'):
        for line in path.read_text().splitlines():
            if line.startswith('slot='):
                used.add(int(line[5:]))
    for slot in range(1, 33):
        if slot not in used:
            return slot
    raise ValueError('No free preallocated Binder slots; this kernel supports 32 configured slots')

def create(args):
    if not ID_RE.fullmatch(args.id):
        raise ValueError('Invalid instance ID')
    if args.domain and not DOMAIN_RE.fullmatch(args.domain.lower()):
        raise ValueError('Invalid domain')
    if args.domain:
        for path in LXC.glob('bf-*/brandfleet.json'):
            existing = json.loads(path.read_text())
            if existing.get('domain', '').lower() == args.domain.lower() or args.domain.lower() in [str(x).lower() for x in existing.get('aliases', [])]:
                raise ValueError('Domain already assigned to another BrandFleet instance')
    if not 1024 <= args.memory <= 32768 or not 1 <= args.cpus <= 16:
        raise ValueError('Resource limits outside allowed range')
    if len(list(LXC.glob('bf-*/brandfleet.json'))) >= host_status()['maxInstances']:
        raise ValueError('Isolated Android pool instance capacity reached')
    assigned = sum(json.loads(p.read_text()).get('memoryMiB', 0) for p in LXC.glob('bf-*/brandfleet.json'))
    pool = host_status()
    budget = pool['assignedBudgetMiB']
    if assigned + args.memory > budget:
        raise ValueError('Assigned Android memory would exceed pool budget with 4 GiB OS reserve')
    if pool['memoryAvailableBytes'] < (args.memory + 3072)*1024*1024:
        raise ValueError('Insufficient measured available pool RAM with3GiB free reserve')
    # Script derives address and Binder devices solely from an available numeric slot.
    run([str(SCRIPTS / 'provision-android.sh'), args.id, str(available_slot()), str(args.memory), str(args.cpus)], timeout=600)
    info = owned(args.id)
    info.update(displayName=args.name[:120], domain=args.domain.lower(),
                createdAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                appStack='static-http-pilot', socialCompatibility='not-tested')
    save(args.id, info)
    return status(args.id)

def restore(args):
    if not ID_RE.fullmatch(args.id):
        raise ValueError('Invalid instance ID')
    if not re.fullmatch(re.escape(args.id) + r'-[0-9]{8}T[0-9]{6}Z', args.archive):
        raise ValueError('Archive must be a retained timestamped archive of the same instance')
    archive = BASE / 'archives' / args.archive
    if archive.is_symlink() or not archive.is_dir():
        raise ValueError('Archive directory not found')
    original = json.loads((archive / 'brandfleet.json').read_text())
    if original.get('name') != args.id:
        raise ValueError('Archive ownership metadata mismatch')
    run(['sha256sum', '--check', str(archive / 'data.sha256')], timeout=120)
    with tarfile.open(archive / 'data.tar.gz') as tar:
        for member in tar:
            parts = Path(member.name).parts
            if not parts or parts[0] != args.id or '..' in parts or member.name.startswith('/'):
                raise ValueError('Invalid path in archive')
    args.name = original.get('displayName', args.id)
    args.domain = original.get('domain', '')
    args.memory = original['memoryMiB']; args.cpus = original['cpuQuota']
    create(args)
    fresh = owned(args.id)
    run([str(SCRIPTS / 'stop-android.sh'), args.id])
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    scratch = BASE / 'archives' / f'{args.id}-restore-fresh-{stamp}'
    shutil.move(str(BASE / 'data' / args.id), str(scratch))
    run(['tar', '--xattrs', '--acls', '--numeric-owner', '-xzf', str(archive / 'data.tar.gz'), '-C', str(BASE / 'data')], timeout=600)
    env = BASE / 'data' / args.id / 'brandfleet' / 'metadata.env'
    env.write_text(f"name={args.id}\nslot={fresh['slot']}\nip={fresh['ip']}\nmemory_mib={fresh['memoryMiB']}\ncpus={fresh['cpuQuota']}\n")
    for key in ['linuxRuntime', 'linuxPort', 'linuxHealthPath', 'linuxHealthHeaders', 'requiresLinux', 'webPort', 'healthPath', 'healthHeaders', 'appStack', 'websiteSource', 'nativeRuntime', 'aliases', 'endpoints', 'migrated', 'runtimePayloadSHA256', 'originalContainer']:
        if key in original: fresh[key] = original[key]
    fresh.update(restoredFrom=args.archive, restoredAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save(args.id, fresh)
    if fresh.get('nativeRuntime'):
        init = BASE / 'data' / args.id / 'brandfleet' / 'native-runtime.rc'
        if init.is_symlink() or not init.is_file():
            raise ValueError('Native archive lacks its persistent owned init declaration')
        shutil.copyfile(init, LXC / args.id / 'rootfs/system/etc/init/brandfleet-native.rc')
    elif fresh.get('linuxPort'):
        run([str(SCRIPTS / 'setup-linux-runtime.sh'), args.id], timeout=600)
    run(['lxc-start', '-n', args.id, '-d'])
    return status(args.id)

def host_status():
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in ['MemAvailable', 'MemTotal']:
            mem[key] = int(value.strip().split()[0]) * 1024
    return {'memoryAvailableBytes': mem['MemAvailable'], 'memoryTotalBytes': mem['MemTotal'],
            'assignedBudgetMiB': min(38912, mem['MemTotal'] // (1024*1024) - 4096),
            'cpuCount': os.cpu_count(), 'maxInstances': 32, 'androidSubnet': '10.77.0.0/24',
            'boundary': 'dedicated-vm', 'host': os.uname().nodename}

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    h = sub.add_parser('host-status'); h.add_argument('--json', action='store_true')
    s = sub.add_parser('status'); s.add_argument('--id'); s.add_argument('--json', action='store_true')
    c = sub.add_parser('create')
    c.add_argument('--id', required=True); c.add_argument('--name', required=True); c.add_argument('--domain', default='')
    c.add_argument('--memory', type=int, default=2048); c.add_argument('--cpus', type=int, default=1)
    for cmd in ['start', 'stop', 'restart', 'archive', 'remove']:
        p = sub.add_parser(cmd); p.add_argument('--id', required=True)
    p = sub.add_parser('clone'); p.add_argument('--id', required=True); p.add_argument('--new-id', required=True)
    p.add_argument('--name', required=True); p.add_argument('--domain', default='')
    p = sub.add_parser('enable-linux'); p.add_argument('--id', required=True)
    p = sub.add_parser('restore'); p.add_argument('--id', required=True); p.add_argument('--archive', required=True)
    args = parser.parse_args()
    # Read-only monitoring must never wait behind lengthy archive/provision work.
    # Metadata replacements are atomic; an instance removed during enumeration is
    # absent from the next snapshot rather than held by a global read lock.
    if args.command == 'host-status':
        print(json.dumps({'ok': True, 'result': host_status()}, indent=2))
        return
    if args.command == 'status':
        if args.id:
            result = status(args.id)
        else:
            ids = [p.parent.name for p in sorted(LXC.glob('bf-*/brandfleet.json'))]
            def snapshot(instance):
                try:
                    return status(instance)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    if not (LXC / instance / 'brandfleet.json').exists():
                        return None
                    return {'id': instance, 'name': instance, 'state': 'UNKNOWN',
                            'ready': False, 'error': str(exc)}
            with ThreadPoolExecutor(max_workers=8) as executor:
                result = [row for row in executor.map(snapshot, ids) if row is not None]
        print(json.dumps({'ok': True, 'result': result}, indent=2))
        return
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / 'lifecycle.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == 'create':
            result = create(args)
        elif args.command == 'restore':
            result = restore(args)
        elif args.command == 'enable-linux':
            if owned(args.id).get('nativeRuntime'):
                raise ValueError('Native application already owns its Linux runtime')
            run([str(SCRIPTS / 'setup-linux-runtime.sh'), args.id], timeout=600)
            result = status(args.id); result['restartRequired'] = True
        else:
            info = owned(args.id)
            if args.command in ['start', 'restart'] and info.get('containmentVersion') != 2:
                raise ValueError('Android instance must have containmentVersion2 before starting')
            if args.command == 'start':
                if status(args.id)['state'] != 'RUNNING': run(['lxc-start', '-n', args.id, '-d'])
                result = status(args.id)
            elif args.command in ['stop', 'restart']:
                if status(args.id)['state'] == 'RUNNING': run([str(SCRIPTS / 'stop-android.sh'), args.id])
                if args.command == 'restart': run(['lxc-start', '-n', args.id, '-d'])
                result = status(args.id)
            elif args.command in ['archive', 'remove']:
                output = run([str(SCRIPTS / 'archive-android.sh'), args.id], timeout=600)
                if args.command == 'remove':
                    # Archives are verified by the archive script and retained outside LXC.
                    run(['lxc-destroy', '-n', args.id], timeout=120)
                    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    target = BASE / 'archives' / f'{args.id}-removed-{stamp}'
                    shutil.move(str(BASE / 'data' / args.id), str(target))
                    result = {'id': args.id, 'state': 'REMOVED', 'retainedData': str(target), 'archive': output.stdout.strip()}
                else:
                    result = status(args.id); result['archive'] = output.stdout.strip()
            elif args.command == 'clone':
                if info.get('nativeRuntime'):
                    raise ValueError('Native application cloning requires an application-specific copy; website-only cloning is disabled')
                source = args.id
                src = website_source(source)
                args.id = args.new_id
                args.memory = info['memoryMiB']; args.cpus = info['cpuQuota']
                result = create(args)
                # Copy only website files; never copy /data/data, accounts or Android settings.
                dst = BASE / 'data' / args.id / 'brandfleet' / 'www'
                shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
                result['clonedWebsiteFrom'] = source
                result['socialSessionsCopied'] = False
        print(json.dumps({'ok': True, 'result': result}, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        sys.exit(1)
