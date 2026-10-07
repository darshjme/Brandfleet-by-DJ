# Fixed-mailbox webmail handoff

The private Python broker issues a 30-second, single-use login grant for one configured dashboard owner and one fixed mailbox. A Roundcube plugin redeems it server-side. The browser receives the grant, never the mailbox password. This is a fixed-account integration, not general tenant SSO.

Before deployment, change the matching example constants in this directory and the dashboard: `https://fleet.example.com`, `https://mail.example.com`, `owner@profile-brand.example.com` and private broker address `10.77.2.50`. The broker requires a private mode-0600 JSON config containing exactly `principal`, `mailbox`, `password`, `issuerKey` and `redeemerKey`; the keys must be different random values of at least 40 characters. Create these locally and keep them outside Git. The Roundcube plugin requires its separate private PHP configuration at `/etc/brandfleet-webmail-sso/roundcube.php`, containing its `redeemerKey`.

Use HTTPS for dashboard, mail and broker; install a certificate whose name matches the configured mail server. Run the broker with `--config`, `--cert` and `--key`, following the provided systemd unit. Install both plugin files in a Roundcube `brandfleet_sso` plugin directory and enable it in Roundcube. Review your Roundcube version and private redemption configuration independently. Mailbox password rotation and authorization remain operator responsibilities.

Run the offline Python and JavaScript tests from the repository root:

```sh
python3 -m unittest discover -s mail-web-login/test -p 'test_broker.py' -v
node --test mail-web-login/test/webmail.test.mjs
```

With PHP available, run `php mail-web-login/test/plugin-test.php` to exercise the plugin using a stub Roundcube API. Real Roundcube login and TLS are separate integration checks.
