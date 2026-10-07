# Operating model

BrandFleet's public source preserves reusable control and runtime primitives. It excludes the private reference deployment's credentials, inventories, app code, databases and Android sessions. Configure an independent lab before connecting lifecycle operations to a real host.

## Configuration boundaries

The dashboard reads an inventory file or a separately authenticated controller. Its environment example leaves authentication material unset. The controller constrains actions and target identifiers independently of browser confirmation. Fresh inventory is required before lifecycle controls are offered; absent measurements stay unknown.

The Android gateway translates a fixed set of browser actions into private device operations. No direct ADB service belongs on a public website route. APKs, image preparation and host kernel dependencies are external to this checkout; a prepared image must be verified against the intended immutable digest.

A website has application code, process configuration and writable data. A native supervisor can manage processes, but it cannot infer every application's dependencies or transactional recovery needs. A new clone needs fresh Android userdata plus independent checks of its backend, persistence and intended domain routes.

## Capacity and recovery

Creation checks both assigned memory and measured available memory. Monitor current memory pressure, OOM events, CPU, disk and capture concurrency before increasing the device count. Capacity on the recorded host does not predict capacity on another machine.

Maintain separate recoverable copies of source, application data, Android userdata and shared-service data. A Git commit preserves source, not mailbox state or database writes. Quiesce or consistently snapshot writers where required, verify hashes, and test restore in an isolated target. Removal should retain a verified recovery archive before a runtime is discarded. Restoring a data service after new writes requires a plan for those writes.

## Mail and workspace

A domain's website route and its email routing are separate decisions. Add local mail domains explicitly and verify their DNS and reverse DNS before claiming outside delivery. Preserve external workspace MX records when that provider still owns email for a domain.

Treat workspace sharing and access control as application-level configuration. Confirm group membership, folder permissions and calendar/contact visibility for the intended organizations. The fixed-mailbox broker also needs a scoped principal and a separately protected issuer configuration; a dashboard login is not authority to open every mailbox.

## Verify the actual behavior

Local tests check authentication, serialization, action guards and selected runtime behavior without performing a production migration. Qualification on a real host additionally needs cold-boot readiness, origin health, correct routes, device controls, resource checks, backend continuity and recovery acceptance.

Social application installation is not account or publishing acceptance. Confirm foreground launch, usable sign-in, platform-supported account operation and network egress independently. Smooth video, GPU acceleration, app capacity and external mail deliverability need their own measurements.

## Return to Coolify

The owner selected Coolify on Debian for production websites after the Android experiment proved too resource heavy for the intended workflow. The planned cutover should inventory each current site's code, environment, database, storage, workers and domain routes; restore it into the chosen Debian/Coolify environment; verify behavior; then change ingress routes. Retire coupled Android website runtimes only after the recovered site and retained data have been verified.

That plan is recorded here. This public repository publication does not claim the live infrastructure has completed the return.
