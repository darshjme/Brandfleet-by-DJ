import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApp, hashPassword, normalizeInventory, normalizeMail, normalizeWorkspace, normalizePackageEvidence, safeUrl } from '../server.mjs';

const password = 'test-only-password-12345';
const hash = hashPassword(password);
const sample = {
  updatedAt: new Date().toISOString(), token: 'never-display-token', mode: 'pilot-controls',
  brands: [{ id: 'pilot1', name: '<img src=x onerror=alert(1)>', domain: 'pilot.example.com', status: 'pilot', actions: ['start', 'stop', 'restart', 'exec'], secret: 'never-display-password', website: { status: 'pending', url: 'javascript:alert(1)' }, android: { status: 'running', screenUrl: 'https://screen.example.com', adbPassword: 'never-display-adb' }, resources: { memoryBytes: 1024 } }],
  infrastructure: [], services: [], capabilities: { create: true, profiles: [{ id: 'android-pilot', name: 'Android pilot' }] },
};
async function startServer(server) { await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); return `http://127.0.0.1:${server.address().port}`; }
async function stopServer(server) { await new Promise(resolve => server.close(resolve)); }
async function fixture(t, extra = {}) { const dir = await mkdtemp(join(tmpdir(), 'brandfleet-test-')); const path = join(dir, 'inventory.json'); await writeFile(path, JSON.stringify(sample)); const server = createApp({ passwordHash: hash, secureCookie: false, inventoryFile: path, ...extra }); const base = await startServer(server); t.after(async () => { await stopServer(server); await rm(dir, { recursive: true, force: true }); }); return { base, path, server }; }
async function login(base) { const res = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password }) }); assert.equal(res.status, 200); return { cookie: res.headers.get('set-cookie').split(';')[0], ...(await res.json()) }; }
test('password is required and links/inventory are sanitized', () => {
  assert.throws(() => createApp({ passwordHash: '' }), /BRANDFLEET_PASSWORD_HASH/);
  assert.equal(safeUrl('javascript:alert(1)'), ''); assert.equal(safeUrl('https://user:password@example.com'), ''); assert.equal(safeUrl('https://example.com'), 'https://example.com/');
  const output = normalizeInventory(sample), text = JSON.stringify(output); assert.equal(output.brands[0].resources.memoryLimitBytes, null); assert.deepEqual(output.brands[0].actions, ['start', 'stop', 'restart']); assert.equal(output.brands[0].website.url, ''); assert(!text.includes('never-display'));
});
test('public shell and health work, API requires authentication, paths cannot traverse', async t => {
  const { base } = await fixture(t); assert.equal((await fetch(`${base}/healthz`)).status, 200); const shell = await fetch(base); assert.equal(shell.status, 200); assert.match(shell.headers.get('content-security-policy'), /frame-ancestors 'none'/); assert.equal((await fetch(`${base}/api/inventory`)).status, 401); assert.equal((await fetch(`${base}/server.mjs`)).status, 404); assert.equal((await fetch(`${base}/%2e%2e%2fserver.mjs`)).status, 404);
});
test('auth rejects invalid passwords, session uses secure cookies and inventory remains read-only', async t => {
  const { base } = await fixture(t, { secureCookie: true }); const bad = await fetch(`${base}/api/login`, { method: 'POST', body: JSON.stringify({ username: 'admin', password: 'incorrect' }) }); assert.equal(bad.status, 401);
  const res = await fetch(`${base}/api/login`, { method: 'POST', body: JSON.stringify({ username: 'admin', password }) }); assert.equal(res.status, 200); const cookie = res.headers.get('set-cookie'); assert.match(cookie, /HttpOnly/); assert.match(cookie, /SameSite=Strict/); assert.match(cookie, /Secure/); const auth = cookie.split(';')[0];
  const inv = await (await fetch(`${base}/api/inventory`, { headers: { Cookie: auth } })).json(); assert.equal(inv.mode, 'read-only'); assert.equal(inv.capabilities.create, false); assert.deepEqual(inv.brands[0].actions, []); assert(!JSON.stringify(inv).includes('never-display'));
});
test('CSRF token is mandatory, missing lifecycle controller refuses mutation, logout invalidates session', async t => {
  const { base } = await fixture(t); const auth = await login(base); const headers = { Cookie: auth.cookie, 'Content-Type': 'application/json' }; assert.equal((await fetch(`${base}/api/brands/pilot1/actions/start`, { method: 'POST', headers, body: '{}' })).status, 403);
  headers['X-CSRF-Token'] = auth.csrf; const action = await fetch(`${base}/api/brands/pilot1/actions/start`, { method: 'POST', headers, body: '{}' }); assert.equal(action.status, 503); assert.equal((await fetch(`${base}/api/logout`, { method: 'POST', headers, body: '{}' })).status, 200); assert.equal((await fetch(`${base}/api/session`, { headers: { Cookie: auth.cookie } })).status, 401);
});
test('inventory read failures report unavailable instead of fabricated data', async t => {
  const { base, path } = await fixture(t); const auth = await login(base); await writeFile(path, '{invalid'); const r = await fetch(`${base}/api/inventory`, { headers: { Cookie: auth.cookie } }); assert.equal(r.status, 503); const result = await r.json(); assert.match(result.error, /unavailable/); assert.equal(result.brands, undefined);
  await writeFile(path, JSON.stringify({ resources: [] })); assert.equal((await fetch(`${base}/api/inventory`, { headers: { Cookie: auth.cookie } })).status, 503);
});
test('private controller receives authenticated allowlisted actions and disruptive confirmation', async t => {
  const requests = [];
  const controller = http.createServer(async (req, res) => { assert.equal(req.headers.authorization, 'Bearer private-test-token'); let raw = ''; for await (const c of req) raw += c; requests.push({ path: req.url, method: req.method, body: raw ? JSON.parse(raw) : null }); res.setHeader('Content-Type', 'application/json'); if (req.url === '/inventory') return res.end(JSON.stringify(sample)); if (req.url === '/jobs') return res.end(JSON.stringify({ jobs: [{ id: 'j1', brandId: 'pilot1', action: 'start', status: 'queued', password: 'never-display-job' }] })); return res.end(JSON.stringify({ job: { id: 'j2', status: 'queued', secret: 'never-display-response' } })); });
  const controllerBase = await startServer(controller); t.after(() => stopServer(controller)); const { base } = await fixture(t, { controllerUrl: controllerBase, controllerToken: 'private-test-token' }); const auth = await login(base); const headers = { Cookie: auth.cookie, 'Content-Type': 'application/json', 'X-CSRF-Token': auth.csrf };
  const exec = await fetch(`${base}/api/brands/pilot1/actions/exec`, { method: 'POST', headers, body: '{}' }); assert.equal(exec.status, 400); const missingConfirm = await fetch(`${base}/api/brands/pilot1/actions/stop`, { method: 'POST', headers, body: '{}' }); assert.equal(missingConfirm.status, 400);
  const r = await fetch(`${base}/api/brands/pilot1/actions/stop`, { method: 'POST', headers, body: JSON.stringify({ confirm: 'pilot1', command: 'touch /tmp/injection' }) }); assert.equal(r.status, 202); const payload = await r.json(); assert(!JSON.stringify(payload).includes('never-display')); const actionRequest = requests.find(r => r.method === 'POST'); assert.equal(actionRequest.path, '/brands/pilot1/actions/stop'); assert.equal(actionRequest.body.confirm, 'pilot1'); assert.equal(actionRequest.body.command, undefined); assert.match(actionRequest.body.requestId, /^[a-f0-9]{32}$/);
  const jobs = await (await fetch(`${base}/api/jobs`, { headers })).json(); assert(!JSON.stringify(jobs).includes('never-display-job'));
});
test('provisioning validates domain/profile and never forwards arbitrary command fields', async t => {
  let received; const controller = http.createServer(async (req, res) => { res.setHeader('Content-Type', 'application/json'); if (req.url === '/inventory') return res.end(JSON.stringify(sample)); let raw = ''; for await (const c of req) raw += c; received = JSON.parse(raw); res.end(JSON.stringify({ job: { id: 'create1', status: 'queued' } })); }); const controllerBase = await startServer(controller); t.after(() => stopServer(controller));
  const { base } = await fixture(t, { controllerUrl: controllerBase, controllerToken: 'private-test-token' }); const auth = await login(base), headers = { Cookie: auth.cookie, 'Content-Type': 'application/json', 'X-CSRF-Token': auth.csrf };
  assert.equal((await fetch(`${base}/api/brands`, { method: 'POST', headers, body: JSON.stringify({ name: 'Brand', domain: 'x;sh', profile: 'android-pilot' }) })).status, 400);
  assert.equal((await fetch(`${base}/api/brands`, { method: 'POST', headers, body: JSON.stringify({ name: 'Brand', domain: 'new.example.com', profile: 'unknown' }) })).status, 400);
  assert.equal((await fetch(`${base}/api/brands`, { method: 'POST', headers, body: JSON.stringify({ name: 'Brand', domain: 'NEW.example.com', profile: 'android-pilot', command: 'anything' }) })).status, 202); assert.equal(received.domain, 'new.example.com'); assert.equal(received.command, undefined);
});
test('stale controller inventory disables lifecycle and provisioning', async t => {
  const stale = { ...sample, updatedAt: '2020-01-01T00:00:00Z' }; let postCount = 0; const controller = http.createServer((req, res) => { if (req.method === 'POST') postCount++; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(stale)); }); const controllerBase = await startServer(controller); t.after(() => stopServer(controller)); const { base } = await fixture(t, { controllerUrl: controllerBase, controllerToken: 'private-test-token' }); const auth = await login(base), headers = { Cookie: auth.cookie, 'X-CSRF-Token': auth.csrf };
  const inv = await (await fetch(`${base}/api/inventory`, { headers })).json(); assert.deepEqual(inv.brands[0].actions, []); assert.equal(inv.capabilities.create, false); assert.equal((await fetch(`${base}/api/brands/pilot1/actions/start`, { method: 'POST', headers, body: '{}' })).status, 409); assert.equal(postCount, 0);
});
test('repeated incorrect logins are rate limited', async t => {
  const { base } = await fixture(t); for (let i = 0; i < 10; i++) assert.equal((await fetch(`${base}/api/login`, { method: 'POST', body: JSON.stringify({ username: 'admin', password: 'incorrect' }) })).status, 401);
  assert.equal((await fetch(`${base}/api/login`, { method: 'POST', body: JSON.stringify({ username: 'admin', password }) })).status, 429);
});
test('mail administration requires authentication and CSRF, strips arbitrary input and private output', async t => {
  const requests=[];
  const sampleMail={ok:true,changed:true,backup:'/private/backup',state:{outbound_enabled:false,domains:[{domain:'example.com',active:true,dkim:{dns_name:'s._domainkey.example.com',dns_value:'public-dkim',private_key:'never-display-key'}}],mailboxes:[{email:'dev@example.com',active:true,quota_bytes:1073741824,password:'never-display-password',password_hash:'never-display-hash'}],aliases:[]}};
  assert(!JSON.stringify(normalizeMail(sampleMail)).includes('never-display'));
  const controller=http.createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;requests.push({path:req.url,data:JSON.parse(raw)});res.setHeader('Content-Type','application/json');res.end(JSON.stringify(sampleMail));});
  const controllerBase=await startServer(controller);t.after(()=>stopServer(controller));const{base}=await fixture(t,{controllerUrl:controllerBase,controllerToken:'private-test-token'});
  assert.equal((await fetch(`${base}/api/mail-admin`)).status,401);const auth=await login(base);const headers={Cookie:auth.cookie,'Content-Type':'application/json'};
  assert.equal((await fetch(`${base}/api/mail-admin`,{method:'POST',headers,body:'{"action":"domain_create"}'})).status,403);
  headers['X-CSRF-Token']=auth.csrf;
  assert.equal((await fetch(`${base}/api/mail-admin`,{method:'POST',headers,body:'{"action":"fixture_cleanup"}'})).status,400);
  const response=await fetch(`${base}/api/mail-admin`,{method:'POST',headers,body:JSON.stringify({action:'mailbox_create',email:'dev@example.com',password:'new-test-password',command:'touch /tmp/never',path:'/etc/shadow'})});assert.equal(response.status,200);const data=await response.json();assert(!JSON.stringify(data).includes('never-display'));assert.equal(data.backup,undefined);
  assert.deepEqual(requests,[{path:'/mail-admin',data:{action:'mailbox_create',email:'dev@example.com',password:'new-test-password'}}]);
  assert.equal((await fetch(`${base}/api/mail-admin`,{headers})).status,200);assert.deepEqual(requests[1].data,{action:'list'});
});

test('Android screen and input require session, fixed managed ID, CSRF and PNG response', async t => {
  const requests = [];
  const gateway = http.createServer(async (req, res) => {
    let data=''; for await (const c of req) data+=c;
    requests.push({path:req.url, method:req.method, body:data});
    if(req.method==='GET') {res.setHeader('Content-Type','image/png');return res.end(Buffer.from([137,80,78,71,13,10,26,10,0]));}
    res.setHeader('Content-Type','application/json');res.end('{"ok":true}');
  });
  const gatewayBase=await startServer(gateway);t.after(()=>stopServer(gateway));
  const {base}=await fixture(t,{screenGateway:gatewayBase});
  assert.equal((await fetch(`${base}/api/brands/bf-test/device/screen`)).status,401);
  const signin=await fetch(`${base}/android/view/bf-test?app=instagram`,{redirect:'manual'});assert.equal(signin.status,303);assert.equal(signin.headers.get('location'),'/?returnTo=%2Fandroid%2Fview%2Fbf-test%3Fapp%3Dinstagram');
  const auth=await login(base),headers={Cookie:auth.cookie,'Content-Type':'application/json'};
  assert.equal((await fetch(`${base}/api/brands/bf-test/device/input`,{method:'POST',headers,body:'{"action":"key","key":3}'})).status,403);
  const image=await fetch(`${base}/api/brands/bf-test/device/screen`,{headers});assert.equal(image.status,200);assert.equal(image.headers.get('content-type'),'image/png');assert.equal((await image.arrayBuffer()).byteLength,9);
  headers['X-CSRF-Token']=auth.csrf;
  assert.equal((await fetch(`${base}/api/brands/bf-test/device/input`,{method:'POST',headers,body:'{"action":"key","key":3}'})).status,200);
  assert.deepEqual(requests.map(r=>r.path),['/screen/bf-test','/input/bf-test']);
  assert.equal((await fetch(`${base}/api/brands/legacy-live/device/screen`,{headers})).status,404);
  assert.equal((await fetch(`${base}/android/view/bf-test`,{headers})).status,200);
});

test('mail protocol settings survive normalization and quarantine strips content/recipient override',async t=>{
 const mail=normalizeMail({ok:true,state:{domains:[{domain:'example.com',limits:{mailboxes:10,max_quota_bytes:1073741824}}],mailboxes:[{email:'dev@example.com',protocols:{imap:false,smtp:true,pop3:false,sieve:true},tls_enforce_in:true,tls_enforce_out:false,password:'private'}]}});
 assert.equal(mail.state.mailboxes[0].protocols.imap,false);assert.equal(mail.state.mailboxes[0].protocols.smtp,true);assert.equal(mail.state.mailboxes[0].tls_enforce_in,true);assert.equal(mail.state.domains[0].limits.mailboxes,10);
 const requests=[];const controller=http.createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;requests.push(JSON.parse(raw));res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,total:1,messages:[{id:'a'.repeat(64),recipient:'saved@example.com',subject:'Test',raw_message:'never-display',private_path:'/private'}]}));});const controllerBase=await startServer(controller);t.after(()=>stopServer(controller));const{base}=await fixture(t,{controllerUrl:controllerBase,controllerToken:'private-test-token'});const auth=await login(base),headers={Cookie:auth.cookie,'Content-Type':'application/json','X-CSRF-Token':auth.csrf};
 const response=await fetch(`${base}/api/mail-admin`,{method:'POST',headers,body:JSON.stringify({action:'quarantine_release',id:'a'.repeat(64),recipient:'override@example.com',command:'delete-all'})});assert.equal(response.status,200);const output=await response.json();assert.equal(output.messages[0].recipient,'saved@example.com');assert(!JSON.stringify(output).includes('never-display'));assert.deepEqual(requests,[{action:'quarantine_release',id:'a'.repeat(64)}]);
});


test('workspace launcher sanitizes apps, retains measured failure and the Google domain exceptions', () => {
  const workspace = normalizeWorkspace({ status:'running', deployment:'production', version:'32.0.15', password:'never-display', url:'https://workspace.example.com', retainedGoogleDomains:['google-one.example.com','google-two.example.com','unexpected.example'], tenantIsolation:{status:'verified',verifiedAt:'2026-10-06T06:55:00Z',privateToken:'never-display'}, apps:[{id:'files',path:'/apps/files/',name:'Files',status:'ready',version:'2.4.0',url:'https://workspace.example.com/apps/files/',secret:'never-display'},{id:'spreed',path:'/apps/spreed/',name:'Talk',status:'unknown',url:'javascript:alert(1)'},{id:'exec',path:'/admin',status:'ready'},{id:'deck',path:'/../../etc/shadow',status:'ready'}] });
  assert.equal(workspace.deployment,'production'); assert.deepEqual(workspace.apps.map(a=>a.id),['files','spreed']);assert.equal(workspace.apps[1].status,'unknown');assert.equal(workspace.apps[1].url,'');assert.deepEqual(workspace.retainedGoogleDomains,['google-one.example.com','google-two.example.com']);assert(!JSON.stringify(workspace).includes('never-display'));assert.equal(normalizeWorkspace().status,'unknown');assert.deepEqual(normalizeWorkspace().apps,[]);
});

test('package history is allowlisted, dated, complete-only and strips private download and login material', () => {
  const packages = ['com.figma.mirror', 'com.whatsapp', 'com.twitter.android', 'com.linkedin.android', 'com.tailscale.ipn', 'com.instagram.android'].map(packageName => ({ package: packageName, app: 'Known app', versionName: '1.0', versionCode: 123, uid: 10080, installedAPKHashesVerified: true, downloadURL: 'https://private/?token=never-display-token', password: 'never-display-password' }));
  const raw = { evidencePresent: true, historicalEvidence: true, packageInstallationAccepted: true, lastVerified: '2026-10-06T08:19:43Z', packages, accountLoginAttempted: false, VPNActivated: false, session: 'never-display-session' };
  const clean = normalizePackageEvidence(raw); assert.equal(clean.packageInstallationAccepted, true); assert.equal(clean.historicalEvidence, true); assert.equal(clean.accountLoginAttempted, false); assert.equal(clean.VPNActivated, false); assert(!JSON.stringify(clean).includes('never-display'));
  assert.equal(normalizePackageEvidence({ ...raw, packages: packages.slice(0, 5) }).packageInstallationAccepted, false);
  assert.equal(normalizePackageEvidence({ ...raw, lastVerified: 'bad-date' }).evidencePresent, false);
  assert.deepEqual(normalizePackageEvidence({ ...raw, lastVerified: 'bad-date' }).packages, []);
  assert.equal(normalizePackageEvidence({ ...raw, historicalEvidence: false }).packageInstallationAccepted, false);
  assert.equal(normalizePackageEvidence({ ...raw, packages: [...packages, { package: 'untrusted.anything', uid: 0 }] }).packages.length, 6);
  assert.equal(normalizeInventory({ brands: [{ id: 'bf-test', packageEvidence: raw }] }).brands[0].packageEvidence.packages.length, 6);
});

test('social launch authenticates/CSRF, pins app-only payload and sanitizes result', async t => {
  const requests = [];
  const controller = http.createServer(async (req, res) => {
    assert.equal(req.headers.authorization, 'Bearer launch-test-token'); let raw = ''; for await (const chunk of req) raw += chunk;
    const data = JSON.parse(raw); requests.push({ path: req.url, data });
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ ok: true, app: data.app, package: 'com.instagram.android', launchedAt: new Date().toISOString(), foregroundConfirmed: true, foregroundPackage: 'com.instagram.android', viewerUrl: 'https://untrusted.example/?token=never-display', session: 'never-display-secret', command: 'never-display-command' }));
  });
  const controllerBase = await startServer(controller); t.after(() => stopServer(controller)); const { base } = await fixture(t, { controllerUrl: controllerBase, controllerToken: 'launch-test-token' });
  const path = `${base}/api/brands/bf-brand-one/social/launch`;
  assert.equal((await fetch(path, { method: 'POST', body: '{}' })).status, 401);
  const auth = await login(base), headers = { Cookie: auth.cookie, 'Content-Type': 'application/json' };
  assert.equal((await fetch(path, { method: 'POST', headers, body: '{"app":"instagram"}' })).status, 403); headers['X-CSRF-Token'] = auth.csrf;
  for (const body of [{ app: 'tailscale' }, { app: 'instagram', package: 'arbitrary' }, { app: 'instagram', command: 'shell' }]) assert.equal((await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) })).status, 400);
  const response = await fetch(path, { method: 'POST', headers, body: '{"app":"instagram"}' }); assert.equal(response.status, 200); const result = await response.json();
  assert.equal(result.viewerUrl, '/android/view/bf-brand-one'); assert.equal(result.foregroundConfirmed, true); assert(!JSON.stringify(result).includes('never-display'));
  assert.deepEqual(requests, [{ path: '/brands/bf-brand-one/social/launch', data: { app: 'instagram' } }]);
});
test('launch failures/mismatched package never pretend success', async t => {
  let status = 409;
  const controller = http.createServer((req, res) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(status === 200 ? { ok: true, app: 'whatsapp', package: 'arbitrary' } : { error: 'private diagnostics' })); });
  const controllerBase = await startServer(controller); t.after(() => stopServer(controller)); const { base } = await fixture(t, { controllerUrl: controllerBase, controllerToken: 'launch-test-token' }); const auth = await login(base), headers = { Cookie: auth.cookie, 'X-CSRF-Token': auth.csrf }; const url = `${base}/api/brands/bf-brand-one/social/launch`;
  for (const [code, expected] of [[409, 'This Android device is busy. Wait a moment and try opening the app again.'], [503, 'The Android app is temporarily unavailable. Wait a moment and try opening it again.']]) {
    status = code;
    const failed = await fetch(url, { method: 'POST', headers, body: '{"app":"whatsapp"}' }); assert.equal(failed.status, code);
    const result = await failed.json(); assert.equal(result.error, expected); assert(!JSON.stringify(result).includes('private diagnostics'));
  }
  status = 200; assert.equal((await fetch(url, { method: 'POST', headers, body: '{"app":"whatsapp"}' })).status, 502);
});
test('viewer login return stays same-origin and serves dock/module under CSP', async t => {
  const { base } = await fixture(t); const redirect = await fetch(`${base}/android/view/bf-test?app=evil&returnTo=https://outside.example`, { redirect: 'manual' }); assert.equal(redirect.headers.get('location'), '/?returnTo=%2Fandroid%2Fview%2Fbf-test');
  const auth = await login(base), headers = { Cookie: auth.cookie };
  const view = await fetch(`${base}/android/view/bf-test`, { headers }); assert.equal(view.status, 200); const html = await view.text(); assert.match(html, /id="brand-select"/); assert.match(html, /type="module" src="\/android-control.js"/); assert.match(html, /Recents/); assert.match(html, /maxlength="200"/);
  const core = await fetch(`${base}/android-viewer-core.js`); assert.equal(core.status, 200); assert.match(core.headers.get('content-type'), /javascript/); assert.match(core.headers.get('content-security-policy'), /script-src 'self'/);
  const shell = await (await fetch(base)).text(); assert.match(shell, /type="module" src="\/app.js"/);
});
