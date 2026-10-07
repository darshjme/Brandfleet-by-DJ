import assert from 'node:assert/strict';
import test from 'node:test';
import { createWebmail, FLEET_ORIGIN, MAIL_ORIGIN, MAILBOX } from '../dashboard-webmail.mjs';

const config = { principal: 'fixture-owner', issuerKey: 'i'.repeat(43) };
const granted = { grant: 'g'.repeat(43), expiresIn: 30, audience: MAIL_ORIGIN, password: 'should-never-leave-private-service', redirect: 'https://attacker.invalid' };

test('one-click grant has fixed POST destination and no persistent credentials', async () => {
  let call;
  const sso = createWebmail({ ...config, issue: async (...args) => { call = args; return granted; } });
  const result = await sso.open('fixture-owner', FLEET_ORIGIN, {});
  assert.deepEqual(call, [config.issuerKey, { principal: 'fixture-owner', audience: MAIL_ORIGIN }]);
  assert.equal(result.action, `${MAIL_ORIGIN}/`);
  assert.equal(result.mailbox, MAILBOX);
  assert.equal(result.fields._brandfleet_grant, granted.grant);
  assert.equal(result.fields._action, 'login');
  assert.equal(JSON.stringify(result).includes('password'), false);
  assert.equal(JSON.stringify(result).includes('attacker'), false);
});

test('status and grant are confined to configured existing dashboard principal', async () => {
  const sso = createWebmail({ ...config, issue: async () => { throw Error('must not call'); }, probe: async () => ({ available: true, mailbox: MAILBOX, audience: MAIL_ORIGIN }) });
  assert.equal((await sso.status('fixture-owner')).available, true);
  assert.equal((await sso.status('other')).available, false);
  assert.equal((await sso.status('other')).mailbox, '');
  await assert.rejects(sso.open('', FLEET_ORIGIN, {}), error => error.status === 403);
  await assert.rejects(sso.open('other', FLEET_ORIGIN, {}), error => error.status === 403);
  assert.equal((await createWebmail().status('fixture-owner')).available, false);
});

test('unreachable or incorrectly scoped broker never advertises working one-click login', async () => {
  for (const probe of [async () => { throw Error('offline'); }, async () => ({ available: true, mailbox: 'other@example.invalid', audience: MAIL_ORIGIN })]) {
    const sso = createWebmail({ ...config, probe });
    assert.equal((await sso.status('fixture-owner')).available, false);
  }
});

test('exact origin and empty fixed-input request required before any broker call', async () => {
  let calls = 0;
  const sso = createWebmail({ ...config, issue: async () => { calls++; return granted; } });
  for (const origin of ['', 'null', 'http://fleet.example.com', 'https://fleet.example.com.attacker.invalid']) await assert.rejects(sso.open('fixture-owner', origin, {}));
  for (const body of [null, [], { mailbox: MAILBOX }, { returnTo: 'https://attacker.invalid' }]) await assert.rejects(sso.open('fixture-owner', FLEET_ORIGIN, body));
  assert.equal(calls, 0);
});

test('invalid upstream grant/audience/TTL cannot become browser login proof', async () => {
  for (const invalid of [{ ...granted, grant: 'bad' }, { ...granted, expiresIn: 300 }, { ...granted, audience: 'other' }]) {
    const sso = createWebmail({ ...config, issue: async () => invalid });
    await assert.rejects(sso.open('fixture-owner', FLEET_ORIGIN, {}), /unavailable/);
  }
});
