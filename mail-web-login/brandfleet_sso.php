<?php
/** BrandFleet's fixed-mailbox grant bridge; normal Roundcube IMAP auth is retained. */
class brandfleet_sso extends rcube_plugin
{
    public $task = '.*';
    private const ORIGIN = 'https://fleet.example.com';
    private const AUDIENCE = 'https://mail.example.com';
    private const MAILBOX = 'owner@profile-brand.example.com';
    private $accepted = false;

    public function init()
    {
        $this->include_script('brandfleet_sso.js');
        $this->add_hook('startup', [$this, 'startup']);
        $this->add_hook('authenticate', [$this, 'authenticate']);
        $this->add_hook('login_after', [$this, 'login_after']);
    }

    public function startup($args)
    {
        if (isset($_POST['_brandfleet_grant'])) {
            $args['task'] = 'login';
            $args['action'] = 'login';
        }
        return $args;
    }

    public function authenticate($args)
    {
        if (!isset($_POST['_brandfleet_grant'])) {
            return $args; // Preserve normal/manual webmail authentication.
        }
        $grant = $_POST['_brandfleet_grant'];
        $args['valid'] = false;
        $args['abort'] = true;
        $args['user'] = '';
        $args['pass'] = '';
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST'
            || ($_SERVER['HTTP_ORIGIN'] ?? '') !== self::ORIGIN
            || ($_SERVER['HTTP_HOST'] ?? '') !== 'mail.example.com'
            || !is_string($grant) || !preg_match('/^[A-Za-z0-9_-]{43}$/D', $grant)) {
            return $args;
        }
        $credential = $this->redeem($grant);
        if (!is_array($credential) || ($credential['mailbox'] ?? '') !== self::MAILBOX
            || ($credential['imapHost'] ?? '') !== 'ssl://mail.example.com:993'
            || !is_string($credential['password'] ?? null) || $credential['password'] === '') {
            return $args;
        }
        // Never mix a prior webmail identity with the explicitly requested Darsh login.
        rcmail::get_instance()->kill_session();
        unset($_POST['_url'], $_GET['_url']);
        $this->accepted = true;
        $args['user'] = self::MAILBOX;
        $args['pass'] = $credential['password'];
        $args['host'] = 'ssl://mail.example.com:993';
        $args['cookiecheck'] = false;
        $args['valid'] = true;
        $args['abort'] = false;
        return $args;
    }

    public function login_after($args)
    {
        // Ignore caller-supplied task/compose/return parameters for this bridge.
        return $this->accepted ? ['_task' => 'mail', '_mbox' => 'INBOX'] : $args;
    }

    protected function redeem($grant)
    {
        // This root-installed private include contains only the broker redemption key.
        $private = '/etc/brandfleet-webmail-sso/roundcube.php';
        if (!is_file($private)) {
            return null;
        }
        $config = require $private;
        if (!is_array($config) || !is_string($config['redeemerKey'] ?? null)) {
            return null;
        }
        $context = stream_context_create([
            'http' => [
                'method' => 'POST', 'timeout' => 8, 'ignore_errors' => false,
                'follow_location' => 0,
                'header' => "Content-Type: application/json\r\nAuthorization: Bearer " . $config['redeemerKey'] . "\r\n",
                'content' => json_encode(['grant' => $grant, 'audience' => self::AUDIENCE]),
            ],
            'ssl' => ['verify_peer' => true, 'verify_peer_name' => true,
                'peer_name' => 'mail.example.com'],
        ]);
        // TLS uses the independently renewed native mail certificate. No public endpoint.
        $response = @file_get_contents('https://10.77.2.50:8087/redeem', false, $context, 0, 4096);
        return is_string($response) ? json_decode($response, true) : null;
    }
}
