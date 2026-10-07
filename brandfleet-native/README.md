# Native process supervision

These helpers run fixed application commands in a Debian userspace inside an Android LXC. `native-entry.py` reads a root-owned private runtime manifest; `native-supervisor.py` maintains process state, restarts enabled services and records shutdown; `start-native.sh` prepares the userspace and starts the supervisor.

Provision the userspace, application source, private environment and persistent mounts separately. Install the Python helpers under `/srv/brandfleet` inside that userspace and the launcher at the path referenced by your Android init service. The source does not include application code, databases or a manifest from the original deployment. Use actual application health and supervisor state when deciding readiness; a working placeholder HTTP listener is insufficient.

The controller and runtime offline tests exercise native readiness and stop behavior. Deployment-specific process and database recovery must be validated in your own lab.
