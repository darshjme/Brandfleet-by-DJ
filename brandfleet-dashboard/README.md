# Dashboard

The dashboard is a dependency-free Node.js server with password sessions, CSRF checks, an inventory view, a private Android viewer and a fixed one-click webmail handoff. Use Node.js 20 or newer.

For a read-only local preview, follow [the local preview guide](../docs/local-preview.md). Generate the password hash using `node server.mjs hash-password`, which reads the password from standard input. Set the values in `brandfleet.env.example` through your process manager; the server does not automatically load an environment file. Keep `BRANDFLEET_SECURE_COOKIE=1` behind HTTPS. For a loopback HTTP preview only, use `BRANDFLEET_SECURE_COOKIE=0`.

Without a configured private controller, inventory can be displayed but lifecycle actions are unavailable. Without a verified private mail broker, one-click mail login is unavailable. The fixed mail origin, mailbox and CSP form destination are examples that must be changed together for an actual deployment.

Run `npm test` from this directory. Tests start disposable loopback HTTP servers and stop them when finished.
