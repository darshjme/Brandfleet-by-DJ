# FreeScout native runtime helpers

These are reference helpers for one native FreeScout deployment: queue draining, worker scheduling, a database health probe and a guarded backup. They assume the fixed `bf-freescout` identity, a private runtime manifest, five named services, 24 application tables and the associated MariaDB/PHP userspaces.

Install them under `/srv/brandfleet` in the application's Debian userspace. The runtime expects `freescout-workers.py` and `freescout-drain.py`; rename the corresponding underscore-named source files when installing. Keep `env-private.json`, database credentials and generated archives outside Git. Adapt and validate service identities, mount paths, database version and table schema before using these helpers with another application. The backup explicitly does not claim a completed restore test.

The Android pre-stop fixture verifies the fixed queue-drain contract offline. Full application recovery requires a separate database and filesystem restore test in your own lab.
