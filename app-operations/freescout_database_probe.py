import json,pathlib,subprocess,sys
root=pathlib.Path('/srv/brandfleet/stacks/freescout-mariadb');private=json.loads(pathlib.Path('/srv/brandfleet/env-private.json').read_text());password=private.get('MARIADB_ROOT_PASSWORD')or private.get('MYSQL_ROOT_PASSWORD');assert password
auth=root/'run/brandfleet-mariadb/probe-client-private.cnf';auth.write_text('[client]\nuser=root\npassword="'+password.replace('\\','\\\\').replace('"','\\"')+'"\n');auth.chmod(0o600)
try:
 r=subprocess.run(['/usr/sbin/chroot',str(root),'/usr/bin/mariadb','--defaults-extra-file=/run/brandfleet-mariadb/probe-client-private.cnf','--protocol=socket','--socket=/run/brandfleet-mariadb/mysqld.sock','--batch','--skip-column-names','--execute','SELECT COUNT(*) FROM freescout.users;'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=3)
 sys.exit(0 if r.returncode==0 and r.stdout.strip().isdigit()else 1)
finally:auth.unlink(missing_ok=True)
