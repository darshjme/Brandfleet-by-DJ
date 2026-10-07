import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp, hashPassword } from '../server.mjs';
import { createWebmail, FLEET_ORIGIN, MAIL_ORIGIN, MAILBOX } from '../webmail.mjs';
import { submitWebmailGrant } from '../public/webmail-core.js';

const password = 'synthetic-dashboard-fixture-password';
const grant = { grant: 'g'.repeat(43), expiresIn: 30, audience: MAIL_ORIGIN };
test('webmail route requires dashboard auth, CSRF, exact Origin and fixed principal before issue', async t => {
  let issues = 0;
  const webmail = createWebmail({ principal: 'admin', issuerKey: 'i'.repeat(43), probe: async () => ({ available: true, mailbox: MAILBOX, audience: MAIL_ORIGIN }), issue: async () => { issues++; return { ...grant, password: 'must-never-leave-private-broker' }; } });
  const server = createApp({ username: 'admin', passwordHash: hashPassword(password), secureCookie: false, webmail });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  t.after(() => new Promise(r => { server.closeAllConnections(); server.close(r); }));
  const base = `http://127.0.0.1:${server.address().port}`, path = `${base}/api/webmail/open`;
  assert.equal((await fetch(path, { method: 'POST', body: '{}' })).status, 401);
  const login = await fetch(`${base}/api/login`, { method: 'POST', body: JSON.stringify({ username: 'admin', password }) });
  const session = await login.json();
  const headers = { Cookie: login.headers.get('set-cookie').split(';')[0], 'Content-Type': 'application/json', Origin: FLEET_ORIGIN };
  assert.equal((await fetch(path, { method: 'POST', headers, body: '{}' })).status, 403);
  headers['X-CSRF-Token'] = session.csrf;
  for (const Origin of ['', 'null', 'https://attacker.invalid']) assert.equal((await fetch(path, { method: 'POST', headers: { ...headers, Origin }, body: '{}' })).status, 403);
  assert.equal((await fetch(path, { method: 'POST', headers, body: JSON.stringify({ mailbox: 'other@example.invalid' }) })).status, 403);
  assert.equal(issues, 0);
  const response = await fetch(path, { method: 'POST', headers, body: '{}' });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.mailbox, MAILBOX); assert.equal(payload.action, `${MAIL_ORIGIN}/`);
  assert(!JSON.stringify(payload).includes('password')); assert.equal(issues, 1);
  assert.match(response.headers.get('content-security-policy'), /form-action 'self' https:\/\/mail\.example\.com;/);
  // no-referrer would turn a cross-origin navigation POST's Origin into null.
  assert.equal(response.headers.get('referrer-policy'), 'strict-origin');
  assert.equal((await fetch(`${base}/api/webmail`, { headers })).status, 200);
  assert.equal((await fetch(`${base}/webmail-core.js`)).status, 200);
});

test('browser keeps fixed same-tab POST connected through navigation and clears proof on pagehide', () => {
  const elements = [], state = { submitted: 0, removed: 0, appended: 0 };
  let onPageHide;
  const doc = { defaultView: { addEventListener(event, fn, options) { assert.equal(event, 'pagehide'); assert.equal(options.once, true); onPageHide = fn; } }, body: { append(form) { state.appended++; form.connected = true; } }, createElement(tag) { const e = { tag, children: [], append(child) { this.children.push(child); }, submit() { assert.equal(this.connected, true); state.submitted++; }, remove() { this.connected = false; state.removed++; } }; elements.push(e); return e; } };
  const data = { action: `${MAIL_ORIGIN}/`, expiresIn: 30, mailbox: MAILBOX, fields: { _task: 'login', _action: 'login', _brandfleet_grant: grant.grant } };
  submitWebmailGrant(data, doc);
  assert.equal(state.submitted, 1); assert.equal(state.removed, 0); assert.equal(state.appended, 1);
  assert.equal(elements[0].method, 'POST'); assert.equal(elements[0].target, '_self'); assert.equal(elements[0].action, `${MAIL_ORIGIN}/`);
  assert.deepEqual(elements[0].children.map(e => e.name), ['_task', '_action', '_brandfleet_grant']);
  for (const invalid of [{ ...data, action: 'https://attacker.invalid/' }, { ...data, fields: { ...data.fields, _pass: 'bad' } }, { ...data, mailbox: 'other@example.invalid' }, { ...data, expiresIn: 300 }]) assert.throws(() => submitWebmailGrant(invalid, doc));
  assert.equal(state.submitted, 1);
  assert.equal(elements[0].connected, true); onPageHide(); assert.equal(state.removed, 1);
});

test('failed native form submission removes one-use proof and listener', () => {
  let removed = 0, listenerRemoved = 0;
  const doc = { defaultView: { addEventListener() {}, removeEventListener(event) { assert.equal(event, 'pagehide'); listenerRemoved++; } }, body: { append() {} }, createElement(tag) { return { append() {}, submit() { throw Error('navigation blocked'); }, remove() { removed++; } }; } };
  assert.throws(() => submitWebmailGrant({ action: `${MAIL_ORIGIN}/`, expiresIn: 30, mailbox: MAILBOX, fields: { _task: 'login', _action: 'login', _brandfleet_grant: grant.grant } }, doc), /navigation blocked/);
  assert.equal(removed, 1); assert.equal(listenerRemoved, 1);
});

test('configured identity mismatch and broker failure disable one-click availability', async () => {
  let calls = 0;
  const sso = createWebmail({ principal: 'owner', issuerKey: 'i'.repeat(43), probe: async () => { throw Error('offline'); }, issue: async () => { calls++; return grant; } });
  assert.equal((await sso.status('owner')).available, false);
  assert.equal((await sso.status('other')).available, false);
  await assert.rejects(sso.open('other', FLEET_ORIGIN, {}), e => e.status === 403);
  assert.equal(calls, 0);
});
