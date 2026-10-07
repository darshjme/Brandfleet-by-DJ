#!/usr/bin/env python3
"""Coherent native shared-service backup; protected archives, isolated restores, no pruning."""
from contextlib import contextmanager
import argparse, datetime, fcntl, hashlib, json, os, pathlib, re, shutil, signal, sqlite3, subprocess, sys, tarfile, time
ROOT = pathlib.Path('/var/lib/lxc/bf-services/rootfs')
BASE = pathlib.Path('/opt/brandfleet/native-backups/shared')
NAME = 'brandfleet-native-backup-shared'
OFFHOST = 'root@192.0.2.172'
OFFROOT = '/root/brandfleet-shared-backups'
SSH = ['-i', '/root/.ssh/brandfleet-backup', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
UNITS = ['cron.service', 'brandfleet-quarantine-notify.timer', 'brandfleet-quarantine.service', 'sogo.service', 'apache2.service', 'brandfleet-freeresend.service', 'brandfleet-pocketbase.service', 'postfix.service', 'dovecot.service', 'rspamd.service', 'brandfleet-rspamd-redis.service']
ESSENTIAL = ['apache2.service', 'brandfleet-freeresend.service', 'brandfleet-pocketbase.service', 'postfix.service', 'dovecot.service', 'rspamd.service', 'brandfleet-rspamd-redis.service', 'postgresql@17-main.service', 'brandfleet-turn.service']
FIXED_PATHS = ['etc', 'var/www/nextcloud', 'var/www/html', 'var/vmail', 'var/spool/postfix', 'var/lib/rspamd', 'var/lib/brandfleet-rspamd-redis', 'var/lib/pocketbase', 'var/lib/coturn', 'var/lib/brandfleet-quarantine', 'root/brandfleet-private', 'opt/brandfleet']
SQLITE = {'pocketbase-data': 'var/lib/pocketbase/data.db', 'pocketbase-auxiliary': 'var/lib/pocketbase/auxiliary.db', 'quarantine': 'var/lib/brandfleet-quarantine/quarantine.db', 'turn': 'var/lib/coturn/turndb'}
QUIESCE_SECONDS = 180
FLOOR = 8 * 1024**3
DEADLINE = None

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def run(args, timeout=120, data=None, output=None):
    if DEADLINE is not None:
        timeout = min(timeout, max(1, int(DEADLINE - time.monotonic())))
        require(time.monotonic() < DEADLINE, 'Bounded native quiesce elapsed')
    result = subprocess.run(args, input=data, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode != 0 and BASE.is_dir():
        write_json(BASE / 'last-command-error-private.json', {'argv': args, 'returncode': result.returncode, 'stderr': result.stderr.decode(errors='replace')[:30000]})
    require(result.returncode == 0, 'Protected operation failed: ' + pathlib.Path(args[0]).name)
    return result.stdout if output is None else b''

def guest(args, **kw):
    return run(['lxc-attach', '-n', 'bf-services', '--', *args], **kw)

def pg(args, **kw):
    return guest(['runuser', '-u', 'postgres', '--', *args], **kw)

def write_json(path, data):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    tmp.chmod(0o600)
    tmp.replace(path)

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def storage_guard(free, payload, multiplier=1):
    needed = FLOOR + payload * multiplier
    require(free >= needed, 'Backup disk reserve refused')
    return {'freeBytes': free, 'requiredBytes': needed}

def unit_state(unit):
    text = guest(['systemctl', 'show', unit, '--property=LoadState', '--property=ActiveState']).decode()
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    require(values.get('LoadState') == 'loaded' and values.get('ActiveState') in ('active', 'inactive'), 'Missing, failed or transitional native unit: ' + unit)
    return values['ActiveState']

def config():
    require(run(['lxc-info', '-n', 'bf-services', '-sH']).strip() == b'RUNNING', 'Native shared container must be running')
    for unit in ESSENTIAL:
        require(unit_state(unit) == 'active', 'Required native service inactive: ' + unit)
    php = "include '/var/www/nextcloud/config/config.php'; echo json_encode(array_intersect_key($CONFIG,array_flip(['dbname','datadirectory','maintenance','apps_paths'])));"
    c = json.loads(guest(['php', '-r', php]))
    require(c.get('maintenance', False) is False, 'Existing workspace maintenance is refused')
    require(re.fullmatch(r'brandfleet_workspace_final_\d{14}', c['dbname']), 'Unexpected workspace database')
    data = c['datadirectory'].lstrip('/')
    require(re.fullmatch(r'var/lib/nextcloud-final-\d{14}', data), 'Unexpected workspace data directory')
    writable = [x['path'].lstrip('/') for x in c['apps_paths'] if x.get('writable')]
    require(len(writable) == 1 and re.fullmatch(r'var/lib/nextcloud-final-apps-\d{14}', writable[0]), 'Unexpected workspace custom apps')
    mailcfg = (ROOT / 'etc/roundcube/config.inc.php').read_text()
    # The database name only is selected from protected PHP configuration.
    match = re.search(r"pgsql://[^\s'\"]+/([A-Za-z0-9_]+)", mailcfg)
    require(match and re.fullmatch(r'brandfleet_roundcube_final_\d{14}', match[1]), 'Unexpected webmail database')
    dbs = [c['dbname'], match[1], 'brandfleet_sogo', 'brandfleet_freeresend']
    for db in dbs:
        require(pg(['psql', '-XAt', '-d', db, '-c', 'SELECT 1']).strip() == b'1', 'Required native database unreadable')
    paths = FIXED_PATHS + [data, writable[0]]
    for p in paths:
        require((ROOT / p).is_dir() and not (ROOT / p).is_symlink(), 'Required backup coverage path absent: ' + p)
    for p in SQLITE.values():
        require((ROOT / p).is_file() and not (ROOT / p).is_symlink(), 'Required SQLite state absent')
    return {'databases': dbs, 'paths': paths, 'workspaceMaintenance': False}

def recover_current():
    global DEADLINE
    DEADLINE = None
    statefile = BASE / 'active-operation.json'
    if not statefile.exists():
        return {'recoveryNeeded': False}
    state = json.loads(statefile.read_text())
    require(set(state['units']) == set(UNITS), 'Recovery unit allowlist mismatch')
    require(all(x in ('active', 'inactive') for x in state['units'].values()), 'Recovery state invalid')
    failures = []
    for unit in reversed(UNITS):
        try:
            expected = state['units'][unit]
            detail = guest(['systemctl', 'show', unit, '--property=LoadState', '--property=ActiveState']).decode()
            values = dict(line.split('=', 1) for line in detail.splitlines() if '=' in line)
            require(values.get('LoadState') == 'loaded', 'Recovery native unit missing')
            current = values.get('ActiveState')
            if current != expected:
                guest(['systemctl', 'start' if expected == 'active' else 'stop', unit], timeout=45)
            require(unit_state(unit) == expected, 'Native state recovery differs')
        except Exception:
            failures.append(unit)
    require(not failures, 'Native state recovery incomplete: ' + ','.join(failures))
    state['recoveredAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    write_json(BASE / 'last-operation.json', state)
    statefile.unlink()
    return {'recoveryNeeded': True, 'allUnitStatesRestored': True}

@contextmanager
def quiesced_capture():
    """Interrupt pure-Python work as well as subprocesses, then recover all prior states."""
    global DEADLINE
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    require(previous_timer == (0.0, 0.0), 'Existing process alarm refused')
    def elapsed(signum, frame):
        raise TimeoutError('Bounded native quiesce elapsed')
    signal.signal(signal.SIGALRM, elapsed)
    signal.setitimer(signal.ITIMER_REAL, QUIESCE_SECONDS)
    DEADLINE = time.monotonic() + QUIESCE_SECONDS
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        DEADLINE = None
        recover_current()

def sqlite_snapshot(source, destination):
    src = sqlite3.connect('file:' + str(source) + '?mode=ro', uri=True, timeout=15)
    dst = sqlite3.connect(destination)
    try:
        def progress(status, remaining, total):
            if DEADLINE is not None:
                require(time.monotonic() < DEADLINE, 'Bounded native quiesce elapsed')
        src.backup(dst, pages=256, progress=progress)
        require(dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLite integrity failed')
        tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        proof = {}
        for table in tables:
            escaped = '"' + table.replace('"', '""') + '"'
            row_hashes = []
            for row in dst.execute('SELECT * FROM ' + escaped):
                encoded = json.dumps([{'bytesHex': x.hex()} if isinstance(x, bytes) else x for x in row], separators=(',', ':'), ensure_ascii=True)
                row_hashes.append(hashlib.sha256(encoded.encode()).hexdigest())
            proof[table] = {'rows': len(row_hashes), 'contentSHA256': hashlib.sha256('\n'.join(sorted(row_hashes)).encode()).hexdigest()}
        destination.chmod(0o600)
        return proof
    finally:
        dst.close()
        src.close()

def sql_signature(db):
    # Emit only counts and sorted row digests; no row contents leave PostgreSQL.
    sql = """CREATE TEMP TABLE bf_counts(schema_name text, table_name text, rows bigint, content_hash text);
DO $$ DECLARE r record; n bigint; h text; BEGIN
FOR r IN SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_temp_%' AND schemaname NOT LIKE 'pg_toast%' ORDER BY schemaname,tablename LOOP
EXECUTE format('SELECT count(*),md5(coalesce(string_agg(row_to_json(t)::text,E''\\n'' ORDER BY row_to_json(t)::text),'''')) FROM %I.%I t',r.schemaname,r.tablename) INTO n,h;
INSERT INTO bf_counts VALUES(r.schemaname,r.tablename,n,h); END LOOP; END $$;
SELECT coalesce(json_agg(bf_counts ORDER BY schema_name,table_name),'[]'::json) FROM bf_counts;"""
    raw = pg(['psql', '-XqAt', '-v', 'ON_ERROR_STOP=1', '-d', db], data=sql.encode(), timeout=120)
    return json.loads(raw)

def scan_files(paths):
    records = {}
    excludes = set(SQLITE.values())
    for top in paths:
        for directory, dirs, files in os.walk(ROOT / top, followlinks=False):
            # Include every regular file, including compiled caches; symlink targets remain separately preserved by tar.
            dirs[:] = [d for d in dirs if not (pathlib.Path(directory) / d).is_symlink()]
            for file in files:
                p = pathlib.Path(directory) / file
                rel = str(p.relative_to(ROOT))
                if any(rel == x or rel in (x + '-wal', x + '-shm', x + '-journal') for x in excludes):
                    continue
                if p.is_symlink() or not p.is_file():
                    continue
                records[rel] = {'bytes': p.stat().st_size, 'sha256': digest(p)}
    return records

def estimate(paths):
    total = 0
    for top in paths:
        for directory, dirs, files in os.walk(ROOT / top, followlinks=False):
            dirs[:] = [d for d in dirs if not (pathlib.Path(directory) / d).is_symlink()]
            for file in files:
                p = pathlib.Path(directory) / file
                if not p.is_symlink() and p.is_file():
                    total += p.stat().st_size + 1024
    return total

def offhost_guard(size):
    code = "import shutil,json;free=shutil.disk_usage('/root').free;need=" + str(FLOOR + size) + ";assert free>=need,'Backup disk reserve refused';print(json.dumps({'freeBytes':free,'requiredBytes':need}))"
    return json.loads(run(['ssh', *SSH, OFFHOST, 'python3 -'], data=code.encode(), timeout=30))

def mirror(directory, manifest):
    files = list(manifest['artifacts']) + ['manifest-private.json', 'SHA256SUMS']
    offhost_guard(sum((directory / n).stat().st_size for n in files))
    stage = OFFROOT + '/' + directory.name + '.partial'
    # All remote path components are fixed or generated and strictly validated.
    require(re.fullmatch(r'native-shared-\d{8}T\d{6}Z-[0-9a-f]{8}', directory.name), 'Unexpected archive name')
    command = 'umask 077; mkdir -p ' + OFFROOT + '; test ! -e ' + stage + ' && test ! -e ' + OFFROOT + '/' + directory.name + ' && mkdir -m 700 ' + stage
    run(['ssh', *SSH, OFFHOST, command], timeout=30)
    for n in files:
        run(['scp', *SSH, '-q', str(directory / n), OFFHOST + ':' + stage + '/' + n], timeout=900)
    command = 'cd ' + stage + ' && sha256sum --check --status SHA256SUMS && chmod 600 * && cd .. && mv ' + directory.name + '.partial ' + directory.name
    run(['ssh', *SSH, OFFHOST, command], timeout=120)
    return OFFROOT + '/' + directory.name

def diagnostic_append(path, expected_path, expected, captured, prefix_sha):
    """One exact Nextcloud diagnostic log may append; its baseline prefix must remain exact."""
    return (path == expected_path and re.fullmatch(r'var/lib/nextcloud-final-\d{14}/nextcloud\.log', path)
            and captured['bytes'] >= expected['bytes'] and prefix_sha == expected['sha256'])

def validate(directory, manifest):
    records = manifest['files']
    seen = set()
    mismatches = []
    diagnostics = []
    data_paths = [p for p in manifest['paths'] if re.fullmatch(r'var/lib/nextcloud-final-\d{14}', p)]
    require(len(data_paths) == 1, 'Exact workspace diagnostic-log path required')
    diagnostic_path = data_paths[0] + '/nextcloud.log'
    stream = subprocess.Popen(['zstd', '-dc', str(directory / 'payload-private.tar.zst')], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=stream.stdout, mode='r|') as archive:
            while True:
                member = archive.next()
                if member is None:
                    break
                p = pathlib.PurePosixPath(member.name)
                require(not p.is_absolute() and '..' not in p.parts, 'Archive traversal refused')
                if member.isfile() and member.name in records:
                    h = hashlib.sha256()
                    prefix = hashlib.sha256()
                    remaining = records[member.name]['bytes']
                    with archive.extractfile(member) as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b''):
                            h.update(block)
                            prefix.update(block[:remaining])
                            remaining = max(0, remaining - len(block))
                    equal = h.hexdigest() == records[member.name]['sha256'] and member.size == records[member.name]['bytes']
                    if not equal:
                        detail = {'path': member.name, 'expected': records[member.name], 'captured': {'bytes': member.size, 'sha256': h.hexdigest()}, 'capturedPrefixSHA256': prefix.hexdigest()}
                        if diagnostic_append(member.name, diagnostic_path, detail['expected'], detail['captured'], prefix.hexdigest()):
                            detail['classification'] = 'Exact append-only Nextcloud diagnostic log; full archived bytes retained'
                            diagnostics.append(detail)
                        else:
                            mismatches.append(detail)
                    seen.add(member.name)
                archive.members.clear()
        require(stream.wait(timeout=60) == 0, 'Archive decompression failed')
    finally:
        # A rejected archive must not leave a decompressor blocked on its stdout pipe.
        if stream.poll() is None:
            stream.terminate()
            try:
                stream.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stream.kill()
                stream.wait(timeout=5)
        stream.stdout.close()
        stream.stderr.close()
    if diagnostics:
        write_json(directory / 'diagnostic-log-classification-private.json', {'diagnostics': diagnostics})
    if mismatches:
        write_json(directory / 'archive-mismatch-private.json', {'mismatches': mismatches, 'count': len(mismatches)})
    require(not mismatches, 'Archived data/config bytes differ; exact protected diagnostic retained')
    require(seen == set(records), 'Archive coverage incomplete')
    restore = directory / 'restore-test'
    restore.mkdir(mode=0o700)
    restored = []
    try:
        for label, sig in manifest['sqlite'].items():
            actual = sqlite_snapshot(directory / (label + '.sqlite'), restore / (label + '.sqlite'))
            require(actual == sig, 'Isolated SQLite row restoration differs')
        for index, original in enumerate(manifest['databases']):
            dump = directory / ('postgres-' + str(index) + '.dump')
            guest_dump = '/var/lib/postgresql/backup-restore-' + directory.name + '-' + str(index) + '.dump'
            target = ROOT / guest_dump.lstrip('/')
            require(not target.exists(), 'Existing restore-test input refused')
            db = 'bf_backup_restore_' + directory.name.split('-')[2].lower().replace('t', '').replace('z', '') + '_' + str(index)
            require(re.fullmatch(r'bf_backup_restore_[0-9]+_[0-3]', db), 'Isolated database name invalid')
            query = "SELECT count(*) FROM pg_database WHERE datname='" + db + "'"
            require(pg(['psql', '-XAt', '-d', 'postgres', '-c', query]).strip() == b'0', 'Existing restore-test database refused')
            try:
                shutil.copyfile(dump, target)
                target.chmod(0o600)
                # Host-copied files start owned by unmapped host root. Use the existing native
                # postgres directory's mapped UID/GID; guest chown cannot own an unmapped inode.
                owner = (ROOT / 'var/lib/postgresql/17/main/PG_VERSION').stat()
                os.chown(target, owner.st_uid, owner.st_gid)
                require(guest(['stat', '-c', '%u:%g', guest_dump]).strip() == guest(['id', '-u', 'postgres']).strip() + b':' + guest(['id', '-g', 'postgres']).strip(), 'Mapped PostgreSQL restore-input ownership differs')
                pg(['pg_restore', '--list', guest_dump])
                pg(['createdb', db])
                pg(['pg_restore', '--no-owner', '--no-acl', '--exit-on-error', '-d', db, guest_dump], timeout=180)
                actual = sql_signature(db)
                expected = [x for x in manifest['postgresSignatures'][original] if not x['schema_name'].startswith(('pg_temp_', 'pg_toast'))]
                if actual != expected:
                    write_json(directory / ('postgres-restore-mismatch-' + str(index) + '-private.json'), {'expected': expected, 'restored': actual})
                require(actual == expected, 'Isolated PostgreSQL rows/schema differ; protected diagnostic retained')
                restored.append({'databaseIndex': index, 'tableCount': len(actual), 'rowCount': sum(x['rows'] for x in actual)})
            finally:
                # The absent-before, generated name is exclusively helper-owned, including partial createdb failures.
                try:
                    if pg(['psql', '-XAt', '-d', 'postgres', '-c', query]).strip() == b'1':
                        pg(['dropdb', db])
                finally:
                    if target.exists():
                        target.unlink()
    finally:
        shutil.rmtree(restore)  # Only helper-owned isolated temporary restored copies.
    return {'archiveRegularFilesHashVerified': len(seen) - len(diagnostics), 'diagnosticLogsFullArchiveHashVerified': len(diagnostics), 'diagnosticBaselinePrefixesSHA256Verified': len(diagnostics), 'sqliteActualRestores': len(manifest['sqlite']), 'postgresActualRestores': restored}

def backup():
    global DEADLINE
    os.umask(0o077)
    require(os.geteuid() == 0, 'Root-only native backup')
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not BASE.is_symlink() and BASE.stat().st_mode & 0o777 == 0o700, 'Protected backup directory required')
    with (BASE / '.backup.lock').open('a') as lock, (ROOT / 'etc/brandfleet-mail/admin.lock').open('a') as admin:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(admin, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not (BASE / 'active-operation.json').exists(), 'Previous backup requires state recovery')
        c = config()
        needed = estimate(c['paths']) + 64 * 1024**2
        local = storage_guard(shutil.disk_usage(BASE).free, needed, 3)
        remote = offhost_guard(needed)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        name = 'native-shared-' + stamp + '-' + os.urandom(4).hex()
        directory = BASE / name
        directory.mkdir(mode=0o700)
        state = {'units': {x: unit_state(x) for x in UNITS}, 'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'snapshot': name}
        write_json(BASE / 'active-operation.json', state)
        manifest = {'snapshot': name, 'databases': c['databases'], 'paths': c['paths'], 'unitStatesBefore': state['units'], 'workspaceMaintenanceBefore': False, 'automaticPruning': False, 'storageGuard': {'local': local, 'offhostBeforeQuiesce': remote}, 'sqlite': {}, 'postgresSignatures': {}, 'files': {}, 'artifacts': {}}
        started = time.monotonic()
        with quiesced_capture():
            # Persist the filter cache before its graceful stop.
            if state['units']['brandfleet-rspamd-redis.service'] == 'active':
                guest(['redis-cli', '-p', '6380', 'SAVE'])
            for unit in UNITS:
                if state['units'][unit] == 'active':
                    guest(['systemctl', 'stop', unit], timeout=30)
            # Refuse a still-running scheduled Nextcloud job rather than capture mixed file/DB state.
            process_rows = guest(['ps', '-eo', 'args']).decode().splitlines()
            require(not any(re.search(r'^(?:/usr/bin/)?php[^\n]* /var/www/nextcloud/(cron\.php|occ)( |$)', row) for row in process_rows), 'Existing workspace job must finish before backup')
            for label, rel in SQLITE.items():
                manifest['sqlite'][label] = sqlite_snapshot(ROOT / rel, directory / (label + '.sqlite'))
            for index, db in enumerate(c['databases']):
                manifest['postgresSignatures'][db] = sql_signature(db)
                with (directory / ('postgres-' + str(index) + '.dump')).open('wb') as handle:
                    pg(['pg_dump', '--format=custom', db], output=handle)
            with (directory / 'postgres-roles-private.sql').open('wb') as handle:
                pg(['pg_dumpall', '--globals-only'], output=handle)
            manifest['files'] = scan_files(c['paths'])
            write_json(directory / 'capture-manifest-private.json', manifest)
            exclusions = ['--exclude=' + x + suffix for x in SQLITE.values() for suffix in ('', '-wal', '-shm', '-journal')]
            # All regular captured files, including logs and caches, must match the protected baseline.
            with (directory / 'payload-private.tar').open('wb') as handle:
                run(['tar', '-C', str(ROOT), '--hard-dereference', *exclusions, '-cf', '-', *c['paths']], output=handle, timeout=120)
            manifest['quiesceSeconds'] = round(time.monotonic() - started, 3)
        require(manifest.get('quiesceSeconds', QUIESCE_SECONDS + 1) <= QUIESCE_SECONDS, 'Bounded quiesce exceeded')
        run(['zstd', '-q', '-T1', '-3', '--keep', str(directory / 'payload-private.tar')], timeout=900)
        (directory / 'payload-private.tar').unlink()
        return complete_snapshot(directory, manifest)

def complete_snapshot(directory, manifest):
    manifest['restoreValidation'] = validate(directory, manifest)
    require(all(unit_state(u) == s for u, s in manifest['unitStatesBefore'].items()), 'Unit states differ after backup')
    manifest['artifacts'] = {}
    for p in directory.iterdir():
        if p.is_file() and p.name not in ('archive-mismatch-private.json', 'capture-manifest-private.json', 'manifest-private.json', 'SHA256SUMS'):
            p.chmod(0o600)
            manifest['artifacts'][p.name] = {'sha256': digest(p), 'bytes': p.stat().st_size}
    write_json(directory / 'manifest-private.json', manifest)
    files = [*manifest['artifacts'], 'manifest-private.json']
    (directory / 'SHA256SUMS').write_text(''.join(digest(directory / n) + '  ' + n + '\n' for n in files))
    (directory / 'SHA256SUMS').chmod(0o600)
    offpath = mirror(directory, manifest)
    proof = {'completedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'passed': True, 'snapshot': directory.name, 'localPath': str(directory), 'offhost': OFFHOST + ':' + offpath, 'coherentNativeBackup': True, 'quiesceSeconds': manifest['quiesceSeconds'], 'unitStatesRestored': True, 'regularFilesSHA256Verified': manifest['restoreValidation']['archiveRegularFilesHashVerified'], 'diagnosticLogArchiveAndPrefixVerified': manifest['restoreValidation']['diagnosticLogsFullArchiveHashVerified'], 'sqliteActualRestores': manifest['restoreValidation']['sqliteActualRestores'], 'postgresActualRestores': manifest['restoreValidation']['postgresActualRestores'], 'archiveBytes': sum(x['bytes'] for x in manifest['artifacts'].values()), 'offhostSHA256Verified': True, 'directoryMode': '0700', 'fileMode': '0600', 'automaticPruning': False, 'storageGuard': manifest['storageGuard']}
    write_json(BASE / 'last-success.json', proof)
    print(json.dumps(proof))
    return proof
def resume_validation(name):
    """Finish one recovered protected capture without stopping or reimporting any live service."""
    require(os.geteuid() == 0, 'Root-only native backup validation')
    require(re.fullmatch(r'native-shared-\d{8}T\d{6}Z-[0-9a-f]{8}', name or ''), 'Snapshot name refused')
    with (BASE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not (BASE / 'active-operation.json').exists(), 'Unrecovered operation refused')
        state = json.loads((BASE / 'last-operation.json').read_text())
        require(state['snapshot'] == name and set(state['units']) == set(UNITS), 'Recovered capture identity differs')
        elapsed = (datetime.datetime.fromisoformat(state['recoveredAt']) - datetime.datetime.fromisoformat(state['startedAt'])).total_seconds()
        require(0 < elapsed <= QUIESCE_SECONDS, 'Recovered downtime deadline proof missing')
        require(all(unit_state(u) == status for u, status in state['units'].items()), 'Recovered service states differ')
        config()
        directory = BASE / name
        require(directory.is_dir() and not directory.is_symlink() and directory.stat().st_mode & 0o777 == 0o700, 'Protected capture required')
        manifest = json.loads((directory / 'capture-manifest-private.json').read_text())
        require(manifest['snapshot'] == name and manifest['unitStatesBefore'] == state['units'], 'Protected capture manifest identity differs')
        payload_size = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        if 'storageGuard' not in manifest:
            manifest['storageGuard'] = {'localBeforeValidation': storage_guard(shutil.disk_usage(BASE).free, payload_size), 'offhostBeforeValidation': offhost_guard(payload_size)}
        manifest['quiesceSeconds'] = round(elapsed, 3)
        manifest['quiesceTimingIncludesRecovery'] = True
        manifest['validationResumedWithoutQuiesce'] = True
        return complete_snapshot(directory, manifest)

def install():
    require(os.geteuid() == 0, 'Root-only native backup install')
    proof = json.loads((BASE / 'last-success.json').read_text())
    age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(proof['completedAt'])
    require(proof['passed'] and proof['offhostSHA256Verified'] and proof['sqliteActualRestores'] == 4 and len(proof['postgresActualRestores']) == 4 and age.total_seconds() < 3600, 'Fresh accepted native backup required before retiring timers')
    config()
    units = pathlib.Path('/etc/systemd/system')
    legacy = BASE / 'legacy-units'
    legacy.mkdir(mode=0o700, exist_ok=True)
    for old in ['legacy-workspace-backup', 'legacy-mail-backup']:
        for suffix in ['service', 'timer']:
            path = units / (old + '.' + suffix)
            require(path.is_file() and not path.is_symlink(), 'Expected legacy backup unit missing')
            saved = legacy / path.name
            if not saved.exists():
                shutil.copyfile(path, saved)
                saved.chmod(0o600)
            require(saved.read_bytes() == path.read_bytes(), 'Saved legacy backup unit differs')
    definitions = {NAME + '.service': '[Unit]\nDescription=Coherent native mail workspace and shared-service backup\nWants=network-online.target\nAfter=network-online.target brandfleet-services-lxc.service\nRequires=brandfleet-services-lxc.service\n[Service]\nType=oneshot\nUser=root\nUMask=0077\nExecStart=/usr/bin/python3 /opt/brandfleet/services/native-shared-backup.py backup\nExecStopPost=/usr/bin/python3 /opt/brandfleet/services/native-shared-backup.py recover\nTimeoutStartSec=1800\nTimeoutStopSec=180\nNice=19\nIOSchedulingClass=idle\nMemoryMax=768M\nPrivateTmp=true\n', NAME + '.timer': '[Unit]\nDescription=Daily native shared-services coherent backup\n[Timer]\nOnCalendar=*-*-* 01:45:00 UTC\nRandomizedDelaySec=300\nPersistent=true\nUnit=' + NAME + '.service\n[Install]\nWantedBy=timers.target\n'}
    for name, text in definitions.items():
        path = units / name
        require(not path.is_symlink(), 'Unit symlink refused')
        require(not path.exists() or path.read_text() == text, 'Existing native backup unit differs')
        path.write_text(text)
        path.chmod(0o644)
    run(['systemd-analyze', 'verify', *[str(units / n) for n in definitions]])
    run(['systemctl', 'daemon-reload'])
    stamp = pathlib.Path('/var/lib/systemd/timers') / ('stamp-' + NAME + '.timer')
    if not stamp.exists():
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch(mode=0o600)
        accepted = datetime.datetime.fromisoformat(proof['completedAt']).timestamp()
        os.utime(stamp, (accepted, accepted))
    run(['systemctl', 'enable', '--now', NAME + '.timer'])
    run(['systemctl', 'disable', '--now', 'legacy-workspace-backup.timer', 'legacy-mail-backup.timer'])
    print(json.dumps({'nativeTimer': NAME + '.timer', 'schedule': '01:45 UTC daily plus up to 300s jitter, persistent', 'legacyOnlyDisabled': ['legacy-workspace-backup.timer', 'legacy-mail-backup.timer'], 'legacyDefinitionsSaved': str(legacy), 'automaticPruning': False}))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['backup', 'recover', 'install-timer', 'resume-validation'])
    parser.add_argument('--snapshot')
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == 'backup':
        backup()
    elif args.action == 'resume-validation':
        resume_validation(args.snapshot)
    elif args.action == 'recover':
        print(json.dumps(recover_current()))
    else:
        install()

if __name__ == '__main__':
    def interrupted(signum, frame):
        raise RuntimeError('Backup interrupted; native service states will be recovered')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except Exception as error:
        print('Native shared backup refused/failed: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        sys.exit(1)
