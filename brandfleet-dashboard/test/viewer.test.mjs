import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import { APPS, ScreenLoop, ViewLease, viewerPath, safeReturnPath, screenPoint, launchApp, controlAfterCapture, frameFingerprint } from '../public/android-viewer-core.js';
const flush = () => new Promise(resolve => setImmediate(resolve));
test('default browser timers are not invoked with the screen-loop instance as receiver', async () => {
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  const calls = [];
  globalThis.setTimeout = function(fn, delay) { assert(this === undefined || this === globalThis); calls.push(['schedule', delay]); return 17; };
  globalThis.clearTimeout = function(timer) { assert(this === undefined || this === globalThis); calls.push(['cancel', timer]); };
  try {
    const loop = new ScreenLoop({ capture: async () => 'frame', frame: () => {}, status: () => {} });
    loop.start(); await flush(); loop.pause();
    assert.deepEqual(calls, [['cancel', null], ['schedule', 700], ['cancel', 17]]);
  } finally { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear; }
});
function clock() { const tasks = []; return { tasks, schedule: (fn, delay) => { const t = { fn, delay }; tasks.push(t); return t; }, cancel: t => { if (t) t.cancelled = true; }, next: () => { const t = tasks.find(t => !t.cancelled && !t.ran); assert(t); t.ran = true; t.fn(); return t; } }; }
test('direct app links and safe login return forbid arbitrary URLs/packages', () => {
  assert.equal(viewerPath('bf-brand-one', 'instagram'), '/android/view/bf-brand-one?app=instagram');
  assert.equal(safeReturnPath('/android/view/bf-brand-one?app=whatsapp'), '/android/view/bf-brand-one?app=whatsapp');
  for (const path of ['https://outside.example/', '//outside.example', '/android/view/bf-x?app=tailscale', '/android/view/bf-x?app=x&url=evil', '/android/view/bf-x/../../etc', '/android/view/%62f-x']) assert.equal(safeReturnPath(path), '');
  assert.equal(viewerPath('../evil', 'instagram'), ''); assert(!APPS.some(a => a.id === 'tailscale'));
});
test('one-click launch awaits actual app result before refreshing controllable screen', async () => {
  const events = [];
  const result = await launchApp('bf-brand-one', 'linkedin', async (path, data) => { events.push({ path, data }); return { ok: true, app: 'linkedin', package: 'com.linkedin.android', foregroundConfirmed: true }; }, () => events.push('fresh-frame'));
  assert.equal(result.foregroundConfirmed, true);
  assert.deepEqual(events, [{ path: '/api/brands/bf-brand-one/social/launch', data: { app: 'linkedin' } }, 'fresh-frame']);
});
test('failed/mismatched launch never reports success or triggers false app refresh', async () => {
  let refreshed = false, called = false;
  await assert.rejects(launchApp('bf-brand-one', 'whatsapp', async () => { throw Error('device offline'); }, () => { refreshed = true; }), /device offline/);
  await assert.rejects(launchApp('bf-brand-one', 'instagram', async () => ({ ok: true, app: 'instagram', package: 'untrusted.package' }), () => { refreshed = true; }), /did not confirm/);
  await assert.rejects(launchApp('bf-brand-one', 'tailscale', async () => { called = true; }, () => {}), /supported/);
  assert.equal(refreshed, false); assert.equal(called, false);
});
test('pause aborts capture, discards late frame and prevents idle polling', async () => {
  const timer = clock(), frames = []; let finish, signal;
  const loop = new ScreenLoop({ ...timer, capture: s => { signal = s; return new Promise(resolve => { finish = resolve; }); }, frame: frame => frames.push(frame), status: () => {} });
  loop.start(); loop.refresh(); loop.refresh(); assert.equal(timer.tasks.length, 0);
  loop.pause(); assert.equal(signal.aborted, true); finish('late-private-frame'); await flush();
  assert.deepEqual(frames, []); assert.equal(timer.tasks.filter(t => !t.cancelled).length, 0);
});
test('screen stays singleflight across resume and retries errors with bounded backoff', async () => {
  const timer = clock(), frames = [], states = []; let resolve, concurrent = 0, peak = 0, calls = 0;
  const loop = new ScreenLoop({ ...timer, capture: async () => { calls++; concurrent++; peak = Math.max(peak, concurrent); try { if (calls === 1) return await new Promise(r => { resolve = r; }); if (calls === 2) throw Error('unavailable'); return 'current'; } finally { concurrent--; } }, frame: frame => frames.push(frame), status: (state, err) => states.push([state, err?.message]) });
  loop.start(); loop.pause(); loop.start(); resolve('old-brand'); await flush(); timer.next(); await flush();
  assert.deepEqual(frames, []); assert.equal(timer.tasks.at(-1).delay, 2000);
  timer.next(); await flush(); assert.deepEqual(frames, ['current']); assert.equal(peak, 1); assert.equal(timer.tasks.at(-1).delay, 700); assert.deepEqual(states.map(s => s[0]), ['error', 'connected']); loop.pause();
});
test('frame decoding after pause cannot mark connection live', async () => {
  const timer = clock(), states = []; let finishFrame;
  const loop = new ScreenLoop({ ...timer, capture: async () => 'frame', frame: () => new Promise(r => { finishFrame = r; }), status: s => states.push(s) });
  loop.start(); await flush(); loop.pause(); finishFrame(); await flush(); assert.deepEqual(states, []); assert.equal(timer.tasks.length, 0);
});
test('browser ownership prevents competing views and takeover cannot release new owner', () => {
  const values = new Map(); const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) }; let at = 100;
  const first = new ViewLease(storage, 'first', () => at), second = new ViewLease(storage, 'second', () => at);
  assert.equal(first.claim('bf-brand-one'), true); assert.equal(second.claim('bf-brand-one'), false);
  assert.equal(second.claim('bf-brand-one', true), true); assert.equal(first.owned(), false); first.release(); assert.equal(second.owned(), true);
  assert.equal(first.claim('bf-profile-brand'), true); at += 6001; assert.equal(first.claim('bf-brand-one'), true);
});
test('touch coordinates bounded and unloaded frame cannot accept gestures', () => {
  const rect = { left: 10, top: 20, width: 100, height: 200 };
  assert.deepEqual(screenPoint({ clientX: 60, clientY: 120 }, rect, 540, 960), [270, 480]);
  assert.deepEqual(screenPoint({ clientX: -40, clientY: 900 }, rect, 540, 960), [0, 959]);
  assert.equal(screenPoint({ clientX: 0, clientY: 0 }, rect, 0, 0), null);
});

test('control drains capture without aborting it or accepting its late frame', async () => {
  const timer = clock(), events = []; let finish, signal;
  const loop = new ScreenLoop({ ...timer, capture: s => { signal = s; return new Promise(resolve => { finish = resolve; }); }, frame: () => events.push('frame'), status: () => {} });
  loop.start(); const control = controlAfterCapture(loop, () => true, async () => events.push('control'));
  assert.equal(loop.active, false); assert.equal(signal.aborted, false); assert.deepEqual(events, []);
  finish('old-frame'); await control;
  assert.deepEqual(events, ['control']); assert.equal(timer.tasks.length, 0);
});

function browserFixture({ signals = AbortSignal, honorAbort = false, timer, fingerprint = blob => frameFingerprint(blob, bytes => webcrypto.subtle.digest('SHA-256', bytes)) } = {}) {
  const elements = new Map(), listeners = new Map(), requests = [], values = new Map(); let heartbeat, screenLoop;
  const element = () => ({ children: [], dataset: {}, textContent: '', hidden: false, disabled: false, value: '', classList: { toggle() {}, add() {}, remove() {} }, addEventListener(type, fn) { this[type] = fn; }, append(...items) { this.children.push(...items); }, focus() {}, removeAttribute() {}, decode: async () => {} });
  const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const document = { hidden: false, getElementById: get, createElement: element, createTextNode: text => text, querySelectorAll: query => query === '[data-app]' ? get('apps').children : [], addEventListener: (type, fn) => listeners.set(type, fn) };
  const window = { addEventListener: (type, fn) => listeners.set(type, fn) };
  const location = { pathname: '/android/view/bf-brand-one', href: 'https://fleet.example/android/view/bf-brand-one', assign() {}, reload() {} };
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const fetch = (path, options) => {
    const record = { path, options }; requests.push(record);
    if (path === '/api/session') return Promise.resolve({ ok: true, json: async () => ({ csrf: 'fixture-csrf' }) });
    if (path === '/api/inventory') return Promise.resolve({ ok: true, json: async () => ({ brands: [{ id: 'bf-brand-one', name: 'Fixture', domain: 'fixture.example', website: { url: 'https://fixture.example' }, android: { screenUrl: '/android/view/bf-brand-one', status: 'RUNNING' } }] }) });
    return new Promise((resolve, reject) => { record.resolve = resolve; if (honorAbort) options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }); });
  };
  const BrowserScreenLoop = class extends ScreenLoop { constructor(options) { super({ ...options, ...timer }); screenLoop = this; } };
  const context = vm.createContext({ APPS, ScreenLoop: BrowserScreenLoop, ViewLease, viewerPath, screenPoint, launchApp, controlAfterCapture, frameFingerprint: fingerprint, document, window, location, localStorage: storage, crypto: { randomUUID: () => 'fixture-owner' }, fetch, AbortSignal: signals, DOMException, URL, Date, Map, history: { replaceState() {} }, setInterval: fn => { heartbeat = fn; return 1; }, clearInterval() {} });
  const source = readFileSync(new URL('../public/android-control.js', import.meta.url), 'utf8').replace(/^import[^\n]*\n/, '');
  vm.runInContext(source, context);
  return { context, document, listeners, requests, get, heartbeat: () => heartbeat(), loop: () => screenLoop, finishScreen: (record, body = 'fixture') => record.resolve({ ok: true, blob: async () => new Blob([body]) }), close: () => listeners.get('pagehide')() };
}

test('actual viewer launch excludes captures through heartbeat and resumes after completion', async () => {
  const browser = browserFixture(); await flush();
  const first = browser.requests.find(r => r.path.endsWith('/screen'));
  assert(first); const launch = browser.context.openApp(APPS[1]); browser.heartbeat(); await flush();
  assert.equal(first.options.signal.aborted, false); assert.equal(browser.requests.filter(r => r.path.endsWith('/social/launch')).length, 0);
  browser.finishScreen(first); await flush();
  const post = browser.requests.find(r => r.path.endsWith('/social/launch')); assert(post);
  browser.heartbeat(); assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 1);
  post.resolve({ ok: true, json: async () => ({ ok: true, app: 'linkedin', package: 'com.linkedin.android', foregroundConfirmed: true }) }); await launch;
  assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 2);
  const last = browser.requests.at(-1); browser.close(); browser.finishScreen(last); await flush();
});

test('actual viewer does not dispatch or resume after user pause while control drains', async () => {
  const browser = browserFixture(); await flush(); const first = browser.requests.find(r => r.path.endsWith('/screen'));
  const input = browser.context.sendInput({ action: 'key', key: 4 }); browser.get('pause').click(); browser.heartbeat();
  browser.finishScreen(first); assert.equal(await input, false);
  assert.equal(browser.requests.filter(r => r.path.endsWith('/input')).length, 0);
  assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 1); browser.close();
});

test('actual viewer preserves hidden and changed-tab ownership while a control drains', async () => {
  for (const state of ['hidden', 'elsewhere']) {
    const browser = browserFixture(); await flush(); const first = browser.requests.find(r => r.path.endsWith('/screen'));
    const input = browser.context.sendInput({ action: 'key', key: 4 });
    if (state === 'hidden') { browser.document.hidden = true; browser.listeners.get('visibilitychange')(); }
    else { new ViewLease(browser.context.localStorage, 'other-tab').claim('bf-brand-one', true); browser.listeners.get('storage')({ key: 'brandfleet-view:bf-brand-one' }); }
    browser.finishScreen(first); assert.equal(await input, false);
    assert.equal(browser.requests.filter(r => r.path.endsWith('/input')).length, 0);
    assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 1); browser.close();
  }
});

test('actual viewer resumes on control failure but does not overlap another control', async () => {
  const browser = browserFixture(); await flush(); const first = browser.requests.find(r => r.path.endsWith('/screen'));
  const input = browser.context.sendInput({ action: 'key', key: 4 }); await browser.context.openApp(APPS[0]); browser.heartbeat();
  browser.finishScreen(first); await flush(); const post = browser.requests.find(r => r.path.endsWith('/input')); assert(post);
  browser.heartbeat(); assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 1);
  post.resolve({ ok: false, status: 409, json: async () => ({ error: 'Fixture device busy' }) }); assert.equal(await input, false);
  assert.equal(browser.requests.filter(r => r.path.endsWith('/social/launch')).length, 0);
  assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 2);
  assert.equal(browser.get('control-status').textContent, 'Fixture device busy');
  const last = browser.requests.at(-1); browser.close(); browser.finishScreen(last); await flush();
});

test('actual viewer capture keeps its deadline when a cancellation signal is provided', async () => {
  const deadlines = [];
  const signals = { timeout: () => { const deadline = new AbortController(); deadlines.push(deadline); return deadline.signal; }, any: list => AbortSignal.any(list) };
  const browser = browserFixture({ signals, honorAbort: true }); await flush();
  const first = browser.requests.find(r => r.path.endsWith('/screen'));
  const input = browser.context.sendInput({ action: 'key', key: 4 });
  deadlines[2].abort(new DOMException('Fixture deadline', 'TimeoutError')); await flush();
  assert.equal(first.options.signal.aborted, true); assert.equal(first.options.signal.reason.name, 'TimeoutError');
  const post = browser.requests.find(r => r.path.endsWith('/input')); assert(post);
  post.resolve({ ok: true }); assert.equal(await input, true); browser.close(); await flush();
});

test('actual viewer control completion cannot resume a view hidden after dispatch', async () => {
  const browser = browserFixture(); await flush(); const first = browser.requests.find(r => r.path.endsWith('/screen'));
  const input = browser.context.sendInput({ action: 'key', key: 4 }); browser.finishScreen(first); await flush();
  const post = browser.requests.find(r => r.path.endsWith('/input')); assert(post);
  browser.document.hidden = true; browser.listeners.get('visibilitychange')();
  post.resolve({ ok: true }); assert.equal(await input, true);
  assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 1); browser.close();
});

test('frame fingerprint distinguishes same-length images and safely falls back without WebCrypto', async () => {
  const digest = bytes => webcrypto.subtle.digest('SHA-256', bytes), first = new Blob(['PNG-fixture-A']), second = new Blob(['PNG-fixture-B']);
  const a = await frameFingerprint(first, digest);
  assert.equal(a, await frameFingerprint(new Blob(['PNG-fixture-A']), digest));
  assert.notEqual(a, await frameFingerprint(second, digest));
  assert.equal(await frameFingerprint(first, async () => { throw Error('WebCrypto unavailable'); }), null);
  let read = false;
  assert.equal(await frameFingerprint({ size: 8000001, arrayBuffer: () => { read = true; } }, digest), null);
  assert.equal(read, false);
});

test('stable images back off at three duplicates, cap at 2800ms and changed images restore fast cadence', async () => {
  const timer = clock(); let changed = true;
  const loop = new ScreenLoop({ ...timer, capture: async () => 'PNG', frame: () => changed, status: () => {} });
  loop.start(); await loop.drained;
  const delays = [timer.tasks.at(-1).delay]; changed = false;
  for (let i = 0; i < 6; i++) { timer.next(); await loop.drained; delays.push(timer.tasks.at(-1).delay); }
  assert.deepEqual(delays, [700, 700, 700, 1400, 2800, 2800, 2800]);
  changed = true; timer.next(); await loop.drained; assert.equal(timer.tasks.at(-1).delay, 700);
  loop.pause();
});

test('urgent refresh, control completion and resume reset idle cadence without competing captures', async () => {
  const timer = clock(); let calls = 0, finish, peak = 0, concurrent = 0;
  const loop = new ScreenLoop({ ...timer, capture: async () => { calls++; concurrent++; peak = Math.max(peak, concurrent); try { return finish ? await new Promise(resolve => { finish = resolve; }) : 'PNG'; } finally { concurrent--; } }, frame: () => false, status: () => {} });
  loop.start(); await loop.drained;
  for (let i = 0; i < 4; i++) { timer.next(); await loop.drained; }
  assert.equal(timer.tasks.at(-1).delay, 2800);
  finish = true; loop.refresh(); loop.refresh(); const pending = finish;
  assert.equal(concurrent, 1); pending('PNG'); await loop.drained; finish = null;
  assert.equal(timer.tasks.at(-1).delay, 0); timer.next(); await loop.drained;
  assert.equal(timer.tasks.at(-1).delay, 700);
  await controlAfterCapture(loop, () => true, async () => {}); const before = calls; loop.start(); await loop.drained;
  assert.equal(calls, before + 1); assert.equal(timer.tasks.at(-1).delay, 700);
  loop.pause(); loop.start(); await loop.drained; assert.equal(timer.tasks.at(-1).delay, 700); assert.equal(peak, 1); loop.pause();
});

test('actual viewer decodes identical PNG once, notices changed bytes and falls back to normal rendering', async () => {
  const timer = clock(), browser = browserFixture({ timer }); let decodes = 0;
  browser.get('screen').decode = async () => { decodes++; };
  const waitFor = async predicate => { for (let n = 0; n < 100; n++) { if (predicate()) return; await flush(); } assert(predicate(), 'Fixture did not settle'); };
  await flush();
  for (let i = 0; i < 5; i++) {
    browser.finishScreen(browser.requests.at(-1), 'same-PNG');
    await waitFor(() => timer.tasks.length === i + 1);
    if (i < 4) timer.next();
  }
  assert.equal(decodes, 1); assert.equal(timer.tasks.at(-1).delay, 2800); assert.match(browser.get('frame-time').textContent, /Unchanged · checked/);
  timer.next(); browser.finishScreen(browser.requests.at(-1), 'next-PNG');
  await waitFor(() => timer.tasks.length === 6);
  assert.equal(decodes, 2); assert.equal(timer.tasks.at(-1).delay, 700); browser.close();

  const fallbackTimer = clock(), fallback = browserFixture({ timer: fallbackTimer, fingerprint: async () => null }); let fallbackDecodes = 0;
  fallback.get('screen').decode = async () => { fallbackDecodes++; }; await flush();
  for (let i = 0; i < 5; i++) {
    fallback.finishScreen(fallback.requests.at(-1), 'same-PNG'); await waitFor(() => fallbackTimer.tasks.length === i + 1);
    assert.equal(fallbackTimer.tasks.at(-1).delay, 700); if (i < 4) fallbackTimer.next();
  }
  assert.equal(fallbackDecodes, 5); fallback.close();
});

test('hidden pause while fingerprinting discards work and does not renew capture or idle state', async () => {
  let finishFingerprint;
  const timer = clock(), browser = browserFixture({ timer, fingerprint: () => new Promise(resolve => { finishFingerprint = resolve; }) });
  let decodes = 0; browser.get('screen').decode = async () => { decodes++; }; await flush();
  browser.finishScreen(browser.requests.at(-1)); await flush();
  browser.document.hidden = true; browser.listeners.get('visibilitychange')();
  finishFingerprint('same-hash'); await flush(); browser.heartbeat();
  assert.equal(decodes, 0); assert.equal(timer.tasks.length, 0); assert.equal(browser.requests.filter(r => r.path.endsWith('/screen')).length, 1); browser.close();
});

test('pause then resume during async fingerprint cannot paint an old generation', async () => {
  const fingerprints = [], timer = clock(), browser = browserFixture({ timer, fingerprint: () => new Promise(resolve => fingerprints.push(resolve)) });
  let decodes = 0; browser.get('screen').decode = async () => { decodes++; }; await flush();
  browser.finishScreen(browser.requests.at(-1), 'old-PNG'); await flush();
  browser.document.hidden = true; browser.listeners.get('visibilitychange')();
  browser.document.hidden = false; browser.listeners.get('visibilitychange')();
  fingerprints[0]('old-hash'); await flush();
  assert.equal(decodes, 0); assert.equal(browser.get('screen').src, undefined); assert.equal(timer.tasks.at(-1).delay, 0);
  timer.next(); browser.finishScreen(browser.requests.at(-1), 'current-PNG'); await flush();
  fingerprints[1]('current-hash'); await flush();
  assert.equal(decodes, 1); assert.equal(browser.get('connection').textContent, 'Live view'); assert.equal(timer.tasks.at(-1).delay, 700); browser.close();
});

test('failed decode cannot make the previous image falsely count as already rendered', async () => {
  const timer = clock(), browser = browserFixture({ timer, fingerprint: async blob => blob.text() }); let decodes = 0;
  browser.get('screen').decode = async () => { if (++decodes === 2) throw Error('Fixture decode failed'); }; await flush();
  browser.finishScreen(browser.requests.at(-1), 'previous-PNG'); await flush();
  timer.next(); browser.finishScreen(browser.requests.at(-1), 'failed-PNG'); await flush();
  assert.equal(timer.tasks.at(-1).delay, 2000);
  timer.next(); browser.finishScreen(browser.requests.at(-1), 'previous-PNG'); await flush();
  assert.equal(decodes, 3); assert.equal(browser.get('connection').textContent, 'Live view'); assert.equal(timer.tasks.at(-1).delay, 700); browser.close();
});

test('bounded stable-PNG model reduces capture demand and redundant decoding', async t => {
  async function sample(adaptive) {
    let at = 0, timer = null, captures = 0, decodes = 0, previous = null;
    const blob = new Blob([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), 'synthetic-static-frame']);
    const loop = new ScreenLoop({
      capture: async () => { captures++; at += 1000; return blob; },
      frame: async image => {
        const signature = await frameFingerprint(image, bytes => webcrypto.subtle.digest('SHA-256', bytes));
        if (adaptive && signature === previous) return false;
        previous = signature; decodes++; return true;
      },
      status: () => {}, schedule: (fn, delay) => (timer = { fn, at: at + delay }), cancel: task => { if (task === timer) timer = null; },
    });
    loop.start(); await loop.drained;
    while (timer && timer.at < 60000) { const task = timer; at = task.at; task.fn(); await loop.drained; }
    loop.pause(); return { captures, decodes };
  }
  const normal = await sample(false), adaptive = await sample(true);
  assert(adaptive.captures < normal.captures * .65);
  assert.equal(adaptive.decodes, 1); assert.equal(normal.decodes, normal.captures);
  t.diagnostic(JSON.stringify({ model: 'synthetic unchanged PNG, 1s capture, request starts within virtual 60s; not production performance', normal, adaptive }));
});
