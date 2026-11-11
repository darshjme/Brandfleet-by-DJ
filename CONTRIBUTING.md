# Contributing

BrandFleet is a configurable infrastructure reference. Start with the [local dashboard preview](docs/local-preview.md), which uses generic inventory and leaves lifecycle controls unavailable. Use Node.js 20+ for the dependency-free dashboard and Python 3 for the controller tests.

## Local checks

Run commands from the repository root:

| Area | Check |
| --- | --- |
| Dashboard, viewer and webmail UI | `node --test brandfleet-dashboard/test/*.test.mjs` |
| Private controller | `python3 -m unittest discover -s brandfleet-controller -p 'test_controller.py' -v` |
| Webmail handoff | `node --test mail-web-login/test/webmail.test.mjs` |
| Webmail broker | `python3 -m unittest discover -s mail-web-login/test -p 'test_broker.py' -v` |

These suites exercise local fixtures and mocks. They do not establish that a real host, mailbox, Android image, deployment or backup is healthy. Runtime-specific tests live alongside their components; read the matching component README before running them. GitHub Actions is disabled.

## Scope a change

Explain the concrete behavior being changed and add regression coverage when fixing a bug. Keep authentication, CSRF checks, output allowlists, stale-inventory controls and unknown status values intact. Missing measurements must remain unknown rather than become healthy or zero.

Keep credentials, private inventories, signed-in sessions and runtime data outside Git. Use example domains and temporary files in tests. Source changes do not deploy systemd services, run provisioning scripts or migrate application data; those operations need separate operator configuration and acceptance.

Preserve upstream notices in [THIRD_PARTY.md](THIRD_PARTY.md). Include the local checks actually run and any host-specific validation still outstanding in the change description.
