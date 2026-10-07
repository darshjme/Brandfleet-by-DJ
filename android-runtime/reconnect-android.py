#!/usr/bin/python3
import json
from pathlib import Path
import re
import subprocess
for path in Path('/var/lib/lxc').glob('bf-*/brandfleet.json'):
    data = json.loads(path.read_text())
    name = path.parent.name
    if not re.fullmatch(r'bf-[a-z0-9][a-z0-9-]{0,40}', name) or data.get('name') != name:
        continue
    ip = data.get('ip', '')
    if not re.fullmatch(r'10\.77\.0\.(?:[1-3][0-9]|4[0-2])', ip):
        continue
    result = subprocess.run(['lxc-info', '-n', name, '-sH'], capture_output=True, text=True, timeout=5)
    if result.stdout.strip() == 'RUNNING':
        subprocess.run(['adb', 'connect', ip + ':5555'], timeout=8, capture_output=True)
