# Operating model

BrandFleet presents website and shared-service status. Coolify manages Docker deployments on Debian; the separate shared Debian LXC owns mail, workspace and common backends. The public source excludes live credentials, inventories, application code, databases and Android sessions. Configure an independent lab before connecting it to a real host.

## Configuration boundaries

The dashboard reads an inventory file or an authenticated private controller. Fresh inventory is required before lifecycle controls are offered; absent measurements stay unknown. The controller maps brands to managed Coolify resources and actual application containers instead of assuming a container name remains stable across deployments.

Use Coolify for website deployment operations, with the intended application image, private environment, domain routes, persistent paths and service limits. Compose stacks can give databases, workers and frontends separate service definitions. Container isolation does not replace backups or eliminate the shared VM, ingress and storage dependencies.

Android creation and lifecycle controls remain unavailable after the website return. The stopped recovery pool is not a live inventory dependency. The historical gateway translates fixed browser actions into private device operations; no direct ADB service belongs on a public website route.

## Cutover and rollback

Inventory each current website's code, runtime configuration, database, storage, workers and domain aliases. Retained old Docker volumes may be stale after an Android-hosted period; preserve and transfer current native data instead. Freeze writers at the application's consistency boundary, restore into the candidate, verify persistence and behavior, then change owned ingress routes.

Stop the original writers after acceptance and keep their recovery material. A later rollback must account for writes made after the cutover; starting an old database beside a current one risks split data. Retarget backups and maintenance schedules to accepted containers and shared-service endpoints before stopping the pool VM.

Retire unused demo routes and workloads only within their owned scope. Keep required shared-service bridge, DHCP, mail and workspace units active. Disable obsolete Android screen/reconnect services after all websites are independent. Do not infer that every unit with “Android” in its name is disposable: shared networking can retain that historical name.

## Capacity and recovery

Monitor available memory, OOM events, CPU, disk and service restarts. Measure physical host memory after VM shutdown separately from app container limits and idle usage. A small idle footprint is not a peak-load benchmark. Keep spare capacity for databases, backup jobs and redeployments.

Maintain recoverable copies of source, private configuration, writable data and shared-service state. Quiesce or consistently snapshot writers where required, verify archive hashes and test restore in an isolated target. Record which checks actually ran; an archive listing or checksum does not establish a full restore.

The accepted migration includes verified cold restores. A separate restore drill of the new nightly logical archives remains unrun. The trading website's current verified backup is local; it has no configured offhost target. Other configured offhost jobs do not provide recovery coverage for it.

Keep original Android data and recovery disks while they are the rollback boundary. No need to run their device runtimes for website uptime. Restarting an optional Android environment requires its own resource and compatibility acceptance.

## Mail and workspace

A website route and email routing are separate decisions. Add local mail domains explicitly and verify their MX, SPF, DKIM, DMARC and reverse DNS before claiming outside delivery. Preserve Google Workspace MX for domains that already use it.

Workspace memberships, groups, folder permissions and calendar/contact visibility define organizational access. The fixed-mailbox broker uses a scoped principal and separately protected issuer configuration. A dashboard login is not authority to open every mailbox, and the broker is not general Google Workspace single sign-on.

## Verify actual behavior

Local tests check authentication, inventory mapping, serialization, action guards and selected runtime behavior. Production acceptance additionally checks candidate routes with trusted TLS, current data, restart persistence, shared dependencies, job retargeting and measured capacity.

Social application installation is not account or publishing acceptance. Foreground launch, usable sign-in, supported account operation and app-specific egress need independent checks. Mail protocol health does not prove outside deliverability, and TURN relay checks do not prove a complete browser-to-browser call. Trading automation remains paused unless separately authorized and accepted.
