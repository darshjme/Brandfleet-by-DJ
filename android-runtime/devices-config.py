#!/usr/bin/python3
"""Minimal Android char-device allowlist; no guest disks or virtualization devices."""
import os
from pathlib import Path
import stat
import sys
slot=int(sys.argv[1])
if not 1 <= slot <= 32: raise SystemExit('Invalid Binder slot')
print('lxc.cgroup2.devices.deny = a')
for major,minor in [(1,3),(1,5),(1,7),(1,8),(1,9),(1,11),(5,0),(5,1),(5,2),(136,'*'),(10,229),(10,200)]:
    print(f'lxc.cgroup2.devices.allow = c {major}:{minor} rwm')
for suffix in ['b','h','v']:
    path=Path(f'/dev/b{slot:02d}{suffix}')
    s=path.stat()
    if not stat.S_ISCHR(s.st_mode):raise SystemExit('Binder must be a char device')
    print(f'lxc.cgroup2.devices.allow = c {os.major(s.st_rdev)}:{os.minor(s.st_rdev)} rwm')
