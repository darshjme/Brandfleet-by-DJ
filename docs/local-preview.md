# Local dashboard preview

Use Node.js **20 or newer**. The dashboard uses built-in modules, so this preview does not need `npm install`. It reads a static example inventory and has no configured lifecycle controller or Android device. Run it from the repository root in Bash or Zsh.

## 1. Run the local suite

```sh
node --test brandfleet-dashboard/test/*.test.mjs
```

## 2. Create a local login

Choose a new local password of at least 16 characters. The following `read` command waits for it without echoing input; type the password and press Enter. The hashing helper returns the dashboard's expected scrypt format.

```sh
read -r -s BRANDFLEET_LOCAL_PASSWORD
export BRANDFLEET_PASSWORD_HASH="$(printf '%s' "$BRANDFLEET_LOCAL_PASSWORD" | node brandfleet-dashboard/server.mjs hash-password)"
unset BRANDFLEET_LOCAL_PASSWORD
```

## 3. Start the preview

```sh
HOST=127.0.0.1 PORT=8170 \
BRANDFLEET_USERNAME=admin \
BRANDFLEET_SECURE_COOKIE=0 \
BRANDFLEET_CONTROLLER_URL='' \
BRANDFLEET_CONTROLLER_TOKEN='' \
BRANDFLEET_INVENTORY_FILE="$PWD/docs/examples/inventory.example.json" \
node brandfleet-dashboard/server.mjs
```

Open `http://127.0.0.1:8170` and sign in as `admin` using the password you entered. The example brand is illustrative, and missing measurements remain unknown. Device launch and lifecycle actions are unavailable because this preview is not connected to a private runtime.

`BRANDFLEET_SECURE_COOKIE=0` permits cookies over local HTTP. A deployed dashboard needs TLS and secure cookies enabled. The server binds to loopback for this preview; keep controller and ADB endpoints private in a configured deployment.

Press **Ctrl+C** to stop the process. Then remove the hash from your shell environment if it is no longer needed:

```sh
unset BRANDFLEET_PASSWORD_HASH
```

## Host integration is separate

The supplied service files and runtime helpers are reference material. A real Coolify deployment needs operator-owned Debian/Docker configuration, application images, domain routes, private control paths, resource limits, persistent storage and tested recovery. The preserved Android experiment additionally requires a qualified kernel and Android image. The public example inventory intentionally does not provide live endpoints or credentials.
