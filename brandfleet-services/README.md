# Shared Debian service adapters

The native mail administrator, quarantine handler and backup helpers integrate an existing Debian LXC running mail, workspace and application backends. They do not install a complete groupware stack. Postfix, Dovecot, Rspamd, Roundcube, SOGo, Nextcloud, PostgreSQL, SQLite, Redis and the related systemd services must already be provisioned where referenced.

Install host-side helpers under `/opt/brandfleet/services` and guest helpers under `/opt/brandfleet`. Mail administration uses fixed JSON actions over standard input, validates domains and mailboxes, and maintains private root-owned state under `/etc/brandfleet-mail` and `/root/brandfleet-private`. DKIM keys, passwords, mailbox contents and migration evidence must remain outside Git. The configuration scripts alter existing mail configuration, so review the paths, domains, import assumptions and service names before using them.

`native-shared-backup.py` is a coherent backup reference for this specific service layout. It checks free space, remembers original service state, bounds quiescence, captures database state and validates archives before publishing success. Configure the example off-host SSH destination `192.0.2.172`, the trusted host key and the private key path. Its application paths, PostgreSQL version, timers and service allowlist are deployment-specific. Restore validation is still required for a new layout; do not enable its timer before proving a backup and recovery in a lab.

From the repository root, run `python3 brandfleet-services/test-native-shared-backup.py`. These offline tests mock service changes and use temporary SQLite fixtures. Live mail/quarantine integration probes from the original deployment are excluded from this public source.
