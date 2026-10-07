export const APPS = Object.freeze([
  { id: 'instagram', name: 'Instagram', icon: '◎', package: 'com.instagram.android' },
  { id: 'linkedin', name: 'LinkedIn', icon: 'in', package: 'com.linkedin.android' },
  { id: 'whatsapp', name: 'WhatsApp', icon: '◉', package: 'com.whatsapp' },
  { id: 'x', name: 'X', icon: '𝕏', package: 'com.twitter.android' },
  { id: 'figma', name: 'Figma Mirror', icon: '◈', package: 'com.figma.mirror' },
]);
export const managedId = id => typeof id === 'string' && /^bf-[a-z0-9][a-z0-9-]{0,40}$/.test(id);
export function viewerPath(id, app = '') {
  if (!managedId(id)) return '';
  return `/android/view/${id}${APPS.some(a => a.id === app) ? `?app=${app}` : ''}`;
}
export function safeReturnPath(value) {
  if (typeof value !== 'string') return '';
  const match = value.match(/^\/android\/view\/(bf-[a-z0-9][a-z0-9-]{0,40})(?:\?app=(instagram|linkedin|whatsapp|x|figma))?$/);
  return match ? viewerPath(match[1], match[2]) : '';
}
export function screenPoint(event, rect, width, height) {
  if (!width || !height || !rect.width || !rect.height) return null;
  return [Math.max(0, Math.min(width - 1, Math.round((event.clientX - rect.left) / rect.width * width))), Math.max(0, Math.min(height - 1, Math.round((event.clientY - rect.top) / rect.height * height)))];
}

// Keep only a fingerprint, never another copy of the device image. Unavailable
// WebCrypto leaves the normal cadence and rendering behavior intact.
export async function frameFingerprint(blob, digest = bytes => globalThis.crypto.subtle.digest('SHA-256', bytes)) {
  if (!Number.isSafeInteger(blob?.size) || blob.size < 1 || blob.size > 8e6) return null;
  try {
    const hash = new Uint8Array(await digest(await blob.arrayBuffer()));
    return `${blob.size}:${Array.from(hash, byte => byte.toString(16).padStart(2, '0')).join('')}`;
  } catch { return null; }
}

// One pending request per view. Pause cancels IO and discards even a late completed frame.
export class ScreenLoop {
  constructor({ capture, frame, status, interval = 700, schedule = (fn, delay) => setTimeout(fn, delay), cancel = timer => clearTimeout(timer) }) {
    Object.assign(this, { capture, frame, status, interval, schedule, cancel });
    this.active = false; this.timer = null; this.request = null; this.generation = 0; this.failures = 0; this.unchanged = 0; this.urgent = false; this.drained = Promise.resolve();
  }
  start() { if (this.active) return; this.active = true; this.failures = 0; this.generation++; this.refresh(); }
  pause() { this.active = false; this.generation++; this.cancel(this.timer); this.timer = null; this.request?.abort(); this.urgent = false; }
  quiesce() {
    // A cancelled browser fetch can leave the gateway capture holding its lock.
    // For control, let the current bounded request finish instead of aborting it.
    this.active = false; this.generation++; this.cancel(this.timer); this.timer = null; this.urgent = false;
    return this.drained;
  }
  refresh() {
    if (!this.active) return;
    this.unchanged = 0;
    this.cancel(this.timer); this.timer = null;
    if (this.request) { this.urgent = true; return; }
    this.tick();
  }
  async tick() {
    if (!this.active || this.request) return;
    const generation = this.generation, request = new AbortController(); this.request = request;
    let drained; this.drained = new Promise(resolve => { drained = resolve; });
    try {
      const frame = await this.capture(request.signal);
      if (this.active && generation === this.generation) {
        const changed = await this.frame(frame, () => this.active && generation === this.generation);
        if (this.active && generation === this.generation) { this.failures = 0; this.unchanged = changed === false ? this.unchanged + 1 : 0; this.status('connected'); }
      }
    } catch (error) {
      if (this.active && generation === this.generation && error.name !== 'AbortError') { this.failures++; this.status('error', error); }
    } finally {
      if (this.request === request) this.request = null;
      drained();
      if (this.active) {
        // Three consecutive unchanged frames allow a short idle backoff. Inputs,
        // launches and resume call start/refresh, restoring immediate capture.
        const idleDelay = this.unchanged < 3 ? this.interval : Math.min(2800, this.interval * 2 ** Math.min(2, this.unchanged - 2));
        const delay = this.urgent ? 0 : this.failures ? Math.min(10000, 1000 * 2 ** Math.min(this.failures, 4)) : idleDelay;
        this.urgent = false; this.timer = this.schedule(() => { this.timer = null; this.tick(); }, delay);
      }
    }
  }
}

export async function controlAfterCapture(loop, allowed, operation) {
  await loop.quiesce();
  if (!allowed()) throw Error('This view paused or changed tabs. Resume here and retry.');
  return operation();
}

// Browser-local ownership avoids competing captures in the user's existing device tabs.
// The gateway separately constrains requests from other browsers.
export class ViewLease {
  constructor(storage, owner, now = Date.now) { Object.assign(this, { storage, owner, now }); this.key = ''; }
  read() { try { return JSON.parse(this.storage.getItem(this.key) || 'null'); } catch { return null; } }
  claim(id, take = false) {
    if (this.key && this.key !== `brandfleet-view:${id}`) this.release();
    this.key = `brandfleet-view:${id}`;
    const current = this.read();
    if (!take && current?.owner !== this.owner && current?.expires > this.now()) return false;
    try { this.storage.setItem(this.key, JSON.stringify({ owner: this.owner, expires: this.now() + 6000 })); } catch { return true; }
    return this.owned();
  }
  owned() { const current = this.read(); return !current || current.owner === this.owner || current.expires <= this.now(); }
  release() { if (this.read()?.owner === this.owner) { try { this.storage.removeItem(this.key); } catch {} } }
}

export async function launchApp(id, app, request, refresh) {
  if (!managedId(id) || !APPS.some(a => a.id === app)) throw Error('Choose a supported Android app.');
  const result = await request(`/api/brands/${id}/social/launch`, { app });
  if (result.ok !== true || result.app !== app || result.package !== APPS.find(a => a.id === app).package) throw Error('The device did not confirm this app launch.');
  refresh(); return result;
}
