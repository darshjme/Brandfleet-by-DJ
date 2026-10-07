<?php
class rcube_plugin { public function add_hook($name, $callback) {} public function include_script($name) {} }
class rcmail {
    public static $instance;
    public $cleared = 0;
    public static function get_instance() { return self::$instance ?? (self::$instance = new self()); }
    public function kill_session() { $this->cleared++; }
}
require __DIR__ . '/../brandfleet_sso.php';
class FixturePlugin extends brandfleet_sso {
    public $calls = 0;
    public $response = ['mailbox' => 'owner@profile-brand.example.com', 'password' => 'synthetic-fixture-password', 'imapHost' => 'ssl://mail.example.com:993'];
    protected function redeem($grant) {
        $this->calls++;
        return $this->response;
    }
}
function check($condition) { if (!$condition) { throw new Exception('Plugin fixture check failed'); } }
$plugin = new FixturePlugin();
$_POST = []; $_SERVER = [];
check($plugin->authenticate(['valid'=>false]) === ['valid'=>false]);
check($plugin->calls === 0);
$_POST = ['_brandfleet_grant' => str_repeat('g',43), '_url' => '_task=settings'];
$_SERVER = ['REQUEST_METHOD'=>'POST','HTTP_ORIGIN'=>'https://fleet.example.com','HTTP_HOST'=>'mail.example.com'];
check($plugin->startup(['action'=>''])['action'] === 'login');
$result = $plugin->authenticate(['valid'=>false]);
check($result['valid'] === true && $result['abort'] === false && $result['cookiecheck'] === false);
check($result['user'] === 'owner@profile-brand.example.com' && $result['host'] === 'ssl://mail.example.com:993');
check($plugin->calls === 1);
check(rcmail::get_instance()->cleared === 1 && !isset($_POST['_url']));
check($plugin->login_after(['_task'=>'settings','_action'=>'compose']) === ['_task'=>'mail','_mbox'=>'INBOX']);
foreach (['HTTP_ORIGIN'=>'https://attacker.invalid','REQUEST_METHOD'=>'GET','HTTP_HOST'=>'other.invalid'] as $field=>$value) {
    $old = $_SERVER[$field]; $_SERVER[$field] = $value;
    $result = $plugin->authenticate(['valid'=>true]);
    check($result['valid'] === false && $result['abort'] === true && $plugin->calls === 1);
    $_SERVER[$field] = $old;
}
$_POST['_brandfleet_grant']='bad';
check($plugin->authenticate(['valid'=>true])['valid'] === false && $plugin->calls === 1);
$_POST['_brandfleet_grant']=str_repeat('g',43);
foreach (['mailbox'=>'other@example.invalid','imapHost'=>'ssl://attacker.invalid:993','password'=>''] as $field=>$value) {
    $plugin->response[$field]=$value;
    check($plugin->authenticate(['valid'=>true])['valid'] === false);
    check(rcmail::get_instance()->cleared === 1);
    $plugin->response=['mailbox'=>'owner@profile-brand.example.com','password'=>'synthetic-fixture-password','imapHost'=>'ssl://mail.example.com:993'];
}
echo "Roundcube plugin fixture checks passed\n";
