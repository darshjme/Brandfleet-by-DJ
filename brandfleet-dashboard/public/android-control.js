import { APPS, ScreenLoop, ViewLease, viewerPath, screenPoint, launchApp, controlAfterCapture, frameFingerprint } from '/android-viewer-core.js';

const $ = id => document.getElementById(id), id = location.pathname.split('/').pop();
const img = $('screen'), connection = $('connection'), launchStatus = $('launch-status');
let csrf = '', ready = false, wantLive = true, appBusy = false, inputBusy = false, frameURL = '', previousFingerprint = null, pointer = null;
let storage;
try { storage = localStorage; } catch { const values = new Map(); storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) }; }
const lease = new ViewLease(storage, crypto.randomUUID());
function message(el, text, error = false) { el.textContent = text; el.classList.toggle('error', error); }
async function request(path, data, signal) {
  const deadline = AbortSignal.timeout(path.endsWith('/social/launch') ? 35000 : 15000);
  const response = await fetch(path, { method: data ? 'POST' : 'GET', cache: 'no-store', headers: data ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {}, body: data ? JSON.stringify(data) : undefined, signal: signal ? AbortSignal.any([signal, deadline]) : deadline, redirect: 'error' });
  if (!response.ok) {
    if (response.status === 401) throw Error('Session expired. Open the fleet dashboard to sign in.');
    const result = await response.json().catch(() => ({}));
    throw Error(result.error || 'The device could not complete this request.');
  }
  return response;
}
function setConnection(state, error) {
  $('connection-dot').className = state;
  connection.textContent = state === 'connected' ? 'Live view' : state === 'paused' ? 'View paused' : state === 'elsewhere' ? 'Active in another tab' : state === 'hidden' ? 'Paused in background' : 'Reconnecting';
  if (error) connection.textContent = error.message;
}
function clearFrame() { if (frameURL) URL.revokeObjectURL(frameURL); frameURL = ''; previousFingerprint = null; img.removeAttribute('src'); img.classList.remove('ready'); $('screen-empty').hidden = false; }
const loop = new ScreenLoop({
  capture: async signal => {
    if (!ready || document.hidden || !lease.claim(id)) { pause('elsewhere'); throw new DOMException('View paused', 'AbortError'); }
    return (await request(`/api/brands/${id}/device/screen`, null, signal)).blob();
  },
  frame: async (blob, stillCurrent) => {
    const fingerprint = await frameFingerprint(blob);
    if (!stillCurrent()) return;
    if (fingerprint !== null && fingerprint === previousFingerprint) { $('frame-time').textContent = `Unchanged · checked ${new Date().toLocaleTimeString()}`; return false; }
    previousFingerprint = null; // Failed decoding must be retried, even for the previous image.
    if (frameURL) URL.revokeObjectURL(frameURL);
    frameURL = URL.createObjectURL(blob); const currentURL = frameURL; img.src = currentURL;
    await img.decode(); if (frameURL !== currentURL || !stillCurrent()) return;
    img.classList.add('ready'); $('screen-empty').hidden = true;
    $('frame-time').textContent = `Frame ${new Date().toLocaleTimeString()}`;
    previousFingerprint = fingerprint;
    return true;
  },
  status: setConnection,
});
function pause(state = 'paused') { loop.pause(); lease.release(); pointer = null; setConnection(state); }
function resume(take = false) {
  if (!ready || !wantLive || document.hidden || appBusy || inputBusy) return;
  const owned = lease.claim(id, take); $('take-control').hidden = owned;
  if (owned) { setConnection('connecting'); loop.start(); } else pause('elsewhere');
}
function controlAllowed() { return ready && wantLive && !document.hidden && lease.owned(); }
function canControl() {
  if (!ready || document.hidden || !lease.owned() || !loop.active) { message($('control-status'), 'Resume this view or take control here first.', true); return false; }
  return true;
}
async function sendInput(data) {
  if (!canControl() || inputBusy || appBusy) return false;
  inputBusy = true;
  message($('control-status'), 'Sending to the device…');
  try { await controlAfterCapture(loop, controlAllowed, () => request(`/api/brands/${id}/device/input`, data)); message($('control-status'), 'Sent to the device.'); if (data.action === 'key' && [3, 187].includes(data.key)) { document.querySelectorAll('[data-app]').forEach(button => button.classList.remove('active')); document.title = `${$('brand-name').textContent} · Android · BrandFleet`; } return true; }
  catch (error) { message($('control-status'), error.message, true); return false; }
  finally { inputBusy = false; resume(); }
}
async function openApp(app) {
  if (!ready || appBusy || inputBusy || document.hidden) return;
  wantLive = true; $('pause').textContent = 'Pause view'; lease.claim(id, true);
  appBusy = true; $('brand-select').disabled = true;
  document.querySelectorAll('[data-app]').forEach(button => { button.disabled = true; button.classList.toggle('active', button.dataset.app === app.id); });
  message(launchStatus, `Opening ${app.name} on ${$('brand-name').textContent}…`);
  try {
    const result = await controlAfterCapture(loop, controlAllowed, () => launchApp(id, app.id, async (path, data) => (await request(path, data)).json(), () => {}));
    message(launchStatus, result.foregroundConfirmed ? `${app.name} opened at ${new Date().toLocaleTimeString()}. Use the screen below.` : `${app.name} launch requested. Check the device screen for its current state.`);
    history.replaceState(null, '', viewerPath(id)); // Reloading the view never repeats an app launch.
    document.title = `${$('brand-name').textContent} · ${app.name} · BrandFleet`;
    img.focus({ preventScroll: true });
  } catch (error) { message(launchStatus, `${app.name} could not open: ${error.message}`, true); document.querySelectorAll('[data-app]').forEach(button => button.classList.remove('active')); }
  finally { appBusy = false; $('brand-select').disabled = false; document.querySelectorAll('[data-app]').forEach(button => { button.disabled = false; }); resume(); }
}
for (const app of APPS) {
  const button = document.createElement('button'), icon = document.createElement('span');
  button.className = 'app-button'; button.dataset.app = app.id; button.disabled = true; icon.textContent = app.icon;
  button.append(icon, document.createTextNode(app.name)); button.addEventListener('click', () => openApp(app)); $('apps').append(button);
}
$('brand-select').addEventListener('change', event => { const path = viewerPath(event.target.value); if (path) location.assign(path); });
$('pause').addEventListener('click', () => { wantLive = !wantLive; $('pause').textContent = wantLive ? 'Pause view' : 'Resume view'; if (wantLive) resume(); else { pause(); $('take-control').hidden = true; } });
$('take-control').addEventListener('click', () => { wantLive = true; $('pause').textContent = 'Pause view'; resume(true); });
document.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click', () => sendInput({ action: 'key', key: Number(button.dataset.key) })));
$('text-form').addEventListener('submit', async event => {
  event.preventDefault(); const text = $('text'), value = text.value;
  if (!value) return;
  if (value.length > 200 || !/^[\x20-\x7E]*$/.test(value)) { message($('control-status'), 'Use basic characters on one line. For other text, use the Android keyboard on screen.', true); return; }
  $('send').disabled = true;
  try { if (await sendInput({ action: 'text', text: value }) && text.value === value) text.value = ''; }
  finally { $('send').disabled = false; }
});
$('fullscreen').addEventListener('click', async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.querySelector('.display-panel').requestFullscreen(); } catch { message($('control-status'), 'Full screen is unavailable in this browser.', true); } });
img.addEventListener('pointerdown', event => { if (!canControl() || appBusy || inputBusy) return; const point = screenPoint(event, img.getBoundingClientRect(), img.naturalWidth, img.naturalHeight); if (!point) return; pointer = { id: event.pointerId, point, at: Date.now() }; img.setPointerCapture(event.pointerId); event.preventDefault(); img.focus({ preventScroll: true }); });
img.addEventListener('pointerup', event => {
  if (!pointer || pointer.id !== event.pointerId) return;
  const start = pointer; pointer = null; const end = screenPoint(event, img.getBoundingClientRect(), img.naturalWidth, img.naturalHeight); if (!end) return;
  const distance = Math.abs(start.point[0] - end[0]) + Math.abs(start.point[1] - end[1]);
  sendInput(distance < 20 ? { action: 'tap', x: end[0], y: end[1] } : { action: 'swipe', x: start.point[0], y: start.point[1], x2: end[0], y2: end[1], duration: Math.min(1500, Math.max(150, Date.now() - start.at)) });
});
img.addEventListener('pointercancel', () => { pointer = null; });
img.addEventListener('keydown', event => {
  const keys = { ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22, Enter: 66, Backspace: 67, Tab: 61, Escape: 4 };
  if (event.ctrlKey || event.metaKey || event.altKey || !Object.hasOwn(keys, event.key)) return;
  // Tab remains a browser navigation key; use the explicit Android Tab button.
  if (event.key === 'Tab') return;
  event.preventDefault(); sendInput({ action: 'key', key: keys[event.key] });
});
document.addEventListener('visibilitychange', () => { if (document.hidden) pause('hidden'); else resume(); });
window.addEventListener('storage', event => { if (event.key !== lease.key || !ready) return; if (!lease.owned()) { pause('elsewhere'); $('take-control').hidden = false; } });
const heartbeat = setInterval(() => { if (ready && wantLive && !document.hidden) { if (!lease.claim(id)) { pause('elsewhere'); $('take-control').hidden = false; } else if (!loop.active) resume(); } }, 2000);
window.addEventListener('pagehide', () => { ready = false; clearInterval(heartbeat); pause(); clearFrame(); });
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

async function connect() {
  try {
    const session = await (await request('/api/session')).json(); csrf = session.csrf;
    if (typeof csrf !== 'string' || !csrf) throw Error('Open the fleet dashboard to sign in.');
    const inventory = await (await request('/api/inventory')).json();
    const brands = inventory.brands.filter(brand => viewerPath(brand.id) && brand.android.screenUrl);
    const brand = brands.find(brand => brand.id === id); if (!brand) throw Error('This Android device is not present in the measured fleet.');
    for (const item of brands) { const option = document.createElement('option'); option.value = item.id; option.textContent = `${item.name} · ${item.domain || item.android.status}`; $('brand-select').append(option); }
    $('brand-select').value = id; $('brand-select').disabled = false;
    $('brand-name').textContent = brand.name; $('domain').textContent = `${brand.domain || brand.id} · Android ${brand.android.status}`;
    document.title = `${brand.name} · Android · BrandFleet`;
    const website = brand.website.url || brand.url; if (website) { $('website').href = website; $('website').hidden = false; }
    ready = true; $('pause').disabled = false; document.querySelectorAll('[data-app]').forEach(button => { button.disabled = false; });
    const app = APPS.find(app => app.id === new URL(location.href).searchParams.get('app')); history.replaceState(null, '', viewerPath(id)); if (app) await openApp(app); else resume();
  } catch (error) { setConnection('error', error); message(launchStatus, error.message, true); $('screen-empty').textContent = error.message; }
}
connect();
