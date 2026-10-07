#!/usr/bin/python3
"""Owned Android boot/shutdown ordering; defaults are opt-in at boot."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import time

BASE = Path('/opt/brandfleet/android')
CLI = str(BASE / 'brandfleet-android.py')
ID_RE = re.compile(r'^bf-[a-z0-9][a-z0-9-]{0,40}$')

def call(*args):
    p = subprocess.run([CLI, *args], capture_output=True, text=True, timeout=180)
    result = json.loads(p.stdout)
    if p.returncode or not result.get('ok'):
        raise RuntimeError(result.get('error', 'Lifecycle command failed'))
    return result['result']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop'])
    parser.add_argument('--id')
    args = parser.parse_args()
    if args.id and not ID_RE.fullmatch(args.id):
        raise ValueError('Invalid owned instance ID')
    profiles = Path('/sys/kernel/security/apparmor/profiles').read_text()
    if args.action == 'start':
        if 'lxc-brandfleet-android (enforce)' not in profiles:
            raise RuntimeError('Enforced Android AppArmor profile unavailable')
        if not Path('/sys/class/net/bfandroid').exists():
            raise RuntimeError('Private Android bridge unavailable')
    files = [Path('/var/lib/lxc') / args.id / 'brandfleet.json'] if args.id else sorted(Path('/var/lib/lxc').glob('bf-*/brandfleet.json'))
    result = {'action': args.action, 'instances': [], 'errors': [], 'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    for path in files:
        try:
            data = json.loads(path.read_text())
            instance = path.parent.name
            if data.get('name') != instance or not ID_RE.fullmatch(instance):
                raise ValueError('Invalid instance ownership')
            if args.action == 'start':
                if data.get('autostart') is not True:
                    result['instances'].append({'id': instance, 'action': 'skipped-autostart-false'})
                    continue
                if data.get('containmentVersion') != 2:
                    raise ValueError('Containment version is not approved')
                slot = int(data['slot'])
                if not 1 <= slot <= 32 or any(not Path(f'/dev/b{slot:02d}{suffix}').exists() for suffix in ['b', 'h', 'v']):
                    raise RuntimeError('Owned Binder triple unavailable')
                call('start', '--id', instance)
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    state = call('status', '--id', instance, '--json')
                    if state.get('ready'):
                        result['instances'].append({'id': instance, 'action': 'started-and-healthy'})
                        break
                    if state.get('state') != 'RUNNING':
                        raise RuntimeError('Android stopped during boot')
                    time.sleep(2)
                else:
                    call('stop', '--id', instance)
                    raise RuntimeError('Android health timeout; stopped rather than repeatedly booting')
            else:
                state = call('status', '--id', instance, '--json')
                if state.get('state') == 'RUNNING':
                    call('stop', '--id', instance)
                    result['instances'].append({'id': instance, 'action': 'gracefully-stopped'})
        except Exception as exc:
            result['errors'].append({'id': path.parent.name, 'error': str(exc)})
    # Retain health failures and continue independent applications. Systemd remains
    # active so ExecStop will still gracefully stop every successfully started app.
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / f'pool-{args.action}-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
