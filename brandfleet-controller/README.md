# Private inventory controller

The Python controller supplies authenticated BrandFleet inventory from Docker workloads and shared native Debian services. It binds to loopback on port 8211. Set a random `BRANDFLEET_CONTROLLER_TOKEN`; optionally set `BRANDFLEET_STATE` to a private state directory. The provided systemd service reads its environment from `/etc/brandfleet/controller.env`.

## Coolify deployment mapping

A returned brand carries `returnedToCoolify: true` and a `coolifyDeployment` mapping with its managed resource, actual container names, primary container or Compose service, and qualified console URL. The controller matches current Docker containers by the mapping and Compose labels. It selects the current instance per observed service role across redeployments and reports running, stopped, degraded or unavailable state. Stopped or unhealthy observed roles affect the brand state. Memory observations come from running container cgroups; configured memory and CPU limits stay separate.

Coolify owns deployment operations. The controller provides inventory and management navigation; it does not implement a general Coolify installer or migrate application databases automatically. Operators supply accepted deployment mappings and independently preserve current data.

## Android return state

The private `coolify-return.json` under `BRANDFLEET_STATE` records the return phase and retained Android metadata. While its `active` flag is true, Android runtime readiness is suppressed and creation/lifecycle controls remain unavailable. In the accepted-awaiting-shutdown and complete phases, the retained snapshot is used without contacting the recovery pool for inventory. That snapshot is historical metadata, not a claim that a stopped Android device is live.

Returned and retired brands are excluded from Android capacity admission and owned route reconciliation. Route writes use a shared filesystem lock. Existing Coolify application routes remain owned by their deployment resources.

## Historical Android integration

The preserved Android actions are restricted to managed IDs, advertised operations and validated profiles. Runtime readiness and native mail/workspace publication require operator configuration and evidence from a real deployment. These inputs are relevant only when qualifying an independent Android lab:

| Input | Example in source |
| --- | --- |
| Pool SSH destination and key | Documentation-only network and `/etc/brandfleet/android-pool-key` |
| Runtime adapter | `/opt/brandfleet/android/brandfleet-android.py` |
| Owned Android route output | `/data/coolify/proxy/dynamic/brandfleet-apps.yaml` |
| Traefik certificate resolver | `cfdns` |
| Shared service root | `/var/lib/lxc/bf-services/rootfs` |

Install `pool-runtime.py` at the configured runtime adapter path for a separate pool VM, or install the direct runtime when co-located. Update example domains, retired-app filters and retained Google Workspace domain filters for your inventory. SSH host keys must already be trusted; the adapter requires strict host-key checking. The read-only social evidence helper needs an operator-created package catalog and validation records.

From the repository root, run:

```sh
python3 -m unittest discover -s brandfleet-controller -p 'test_controller.py' -v
```

Tests mock deployment commands and use temporary state. Live application data, job schedules, ingress and recovery need independent production acceptance.
