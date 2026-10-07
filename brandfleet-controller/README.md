# Private controller

The Python controller measures existing deployments, publishes owned Traefik routes and sends fixed lifecycle requests to the Android pool. It binds to loopback on port 8211. Set a random `BRANDFLEET_CONTROLLER_TOKEN`; optionally set `BRANDFLEET_STATE` to the private state directory. The provided systemd service reads its environment from `/etc/brandfleet/controller.env`.

This is reference source from an Android architecture, not a Coolify migration tool. Existing Docker services are inspected for inventory. Controller actions are restricted to managed Android IDs, advertised operations and validated profiles. Runtime readiness and native mail/workspace publication require operator-created configuration and evidence from a real deployment.

Review these deployment inputs before installation:

| Input | Example in source |
| --- | --- |
| Pool SSH destination and key | `10.77.1.104`, `/etc/brandfleet/android-pool-key` |
| Runtime adapter | `/opt/brandfleet/android/brandfleet-android.py` |
| Route output | `/data/coolify/proxy/dynamic/brandfleet-apps.yaml` |
| Traefik certificate resolver | `cfdns` |
| Shared service root | `/var/lib/lxc/bf-services/rootfs` |
| Public ingress | `192.0.2.10` — documentation address only |
| Shared service network | `10.77.2.50` |

Install `pool-runtime.py` at the configured runtime adapter path for a separate pool VM, or install the direct runtime when co-located. Update example domains, retired-app filters and retained Google Workspace domain filters for your inventory. SSH host keys must already be trusted; the adapter requires strict host-key checking. The read-only social evidence helper needs an operator-created package catalog and validation records.

From the repository root, run `python3 -m unittest discover -s brandfleet-controller -p 'test_controller.py' -v`. Tests mock deployment commands and use temporary state.
