# Source map

This release contains 72 reviewed source, configuration-template and offline-test files derived from the canonical source commit `1e7f9ab1540d8a418bc9c46ba11e4bb948e9ad53`. Public documentation and artwork are maintained separately. Deployment domains, private networks and operator identifiers in the selected source use example values.

| Directory | Included implementation |
| --- | --- |
| `brandfleet-controller` | Inventory, Coolify deployment mapping, route ownership and return-state guards. |
| `brandfleet-dashboard` | Web dashboard, deployment links, mail controls and retained Android viewer code. |
| `brandfleet-services` | Shared-service administration, quarantine and backup helpers. |
| `mail-web-login` | Webmail ticket broker, client and plugin with offline fixtures. |
| `app-operations` | Selected application worker, probe and backup helpers. |
| `android-runtime`, `brandfleet-native` | Archived Android experiment and native runtime controls. See [experiment notes](docs/android-experiment.md). |

The public release retains its portable pre-stop fixture and bootstrap installation of `native-pre-stop.py` and `social-evidence.py`. The deployment-console example is `deployments.example.com`.

One-off migration, cutover and restore scripts, host-specific operational jobs, runtime inventory, environment files, credentials, database archives, APK downloads and copied third-party Android build sources are excluded. Configure and validate application backups and host settings for your own installation. See [third-party notices](THIRD_PARTY.md).

Verification recorded 150 passing offline checks across ten suites. After the final controller wording change, its 32 checks were rerun; the other nine suites were carried forward only after their selected public source hashes remained unchanged. The final selected source also passed 31 Python, 14 JavaScript and eight shell syntax checks. PHP checks and a live deployment of this sanitized public checkout were not run.
