import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { readFileSync, lstatSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import { createWebmail } from './webmail.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const MAX_BODY = 16384;
const ACTIONS = new Set(['start', 'stop', 'restart', 'clone', 'archive', 'remove']);
const MAIL_ACTIONS = new Set(['list', 'domain_create', 'domain_update', 'mailbox_create', 'mailbox_update', 'alias_create', 'alias_update','quarantine_list','quarantine_release','quarantine_delete']);
const ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/;
const launchPackages = { instagram: 'com.instagram.android', linkedin: 'com.linkedin.android', whatsapp: 'com.whatsapp', x: 'com.twitter.android', figma: 'com.figma.mirror' };
export function normalizeLaunch(raw, id, app) {
  if (raw?.ok !== true || raw.app !== app || raw.package !== launchPackages[app]) throw new Error('Invalid app launch result');
  const foregroundPackage = typeof raw.foregroundPackage === 'string' && /^[a-zA-Z0-9_.]{1,150}$/.test(raw.foregroundPackage) ? raw.foregroundPackage : '';
  return { ok: true, app, package: launchPackages[app], launchedAt: string(raw.launchedAt, 80), foregroundConfirmed: raw.foregroundConfirmed === true && foregroundPackage === launchPackages[app], foregroundPackage, viewerUrl: `/android/view/${id}` };
}
const states = new Set(['running', 'stopped', 'pending', 'error', 'ready', 'unavailable', 'unknown', 'degraded', 'healthy', 'pilot', 'archived']);
const string = (v, max = 300) => typeof v === 'string' ? v.slice(0, max) : '';
const number = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
const state = v => states.has(v) ? v : 'unknown';
export function safeUrl(value) {
  if (typeof value !== 'string' || value.length > 2000) return '';
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; }
}
export function hashPassword(password) {
  const salt = randomBytes(16).toString('hex');
  return `scrypt:${salt}:${scryptSync(password, salt, 64).toString('hex')}`;
}
function verifyPassword(password, encoded) {
  const parts = encoded.split(':');
  if (parts.length !== 3 || parts[0] !== 'scrypt' || !/^[a-f0-9]{32}$/.test(parts[1]) || !/^[a-f0-9]{128}$/.test(parts[2])) return false;
  return timingSafeEqual(scryptSync(password, parts[1], 64), Buffer.from(parts[2], 'hex'));
}
function resource(v = {}) {
  return { cpuPercent: number(v.cpuPercent), cpuCores: number(v.cpuCores), memoryBytes: number(v.memoryBytes), memoryLimitBytes: number(v.memoryLimitBytes), diskBytes: number(v.diskBytes), diskLimitBytes: number(v.diskLimitBytes) };
}
function component(v = {}) {
  return { status: state(v.status), detail: string(v.detail), url: safeUrl(v.url), screenUrl: safeUrl(v.screenUrl), terminalUrl: safeUrl(v.terminalUrl), runtime: string(v.runtime, 80), containerId: string(v.containerId, 80), privateIp: string(v.privateIp, 80), lastCheckedAt: string(v.lastCheckedAt, 80) };
}
const workspaceApps = { files: '/apps/files/', spreed: '/apps/spreed/', calendar: '/apps/calendar/', contacts: '/apps/contacts/', deck: '/apps/deck/', webmail: '/' };
const deployment = v => ['production', 'preview', 'private'].includes(v) ? v : 'unknown';
export function normalizeWorkspace(raw = {}) {
  return { status: state(raw.status), measuredAt: string(raw.measuredAt, 80), deployment: deployment(raw.deployment), url: safeUrl(raw.url), previewUrl: safeUrl(raw.previewUrl), version: string(raw.version, 80),
    apps: Array.isArray(raw.apps) ? raw.apps.slice(0, 20).filter(a => Object.hasOwn(workspaceApps, a.id) && a.path === workspaceApps[a.id]).map(a => ({ id: a.id, name: string(a.name, 80), path: workspaceApps[a.id], status: state(a.status), version: string(a.version, 80), url: safeUrl(a.url), detail: string(a.detail, 400) })) : [],
    tenantIsolation: { status: raw.tenantIsolation?.status === 'verified' ? 'verified' : 'unknown', verifiedAt: string(raw.tenantIsolation?.verifiedAt, 80), detail: string(raw.tenantIsolation?.detail, 400) },
    retainedGoogleDomains: Array.isArray(raw.retainedGoogleDomains) ? [...new Set(raw.retainedGoogleDomains.filter(d => ['google-one.example.com', 'google-two.example.com'].includes(d)))] : [],
  };
}
const socialPackages = new Set(['com.figma.mirror', 'com.whatsapp', 'com.twitter.android', 'com.linkedin.android', 'com.tailscale.ipn', 'com.instagram.android']);
export function normalizePackageEvidence(raw = {}) {
  const stamp = typeof raw.lastVerified === 'string' ? Date.parse(raw.lastVerified) : NaN;
  const lastVerified = Number.isFinite(stamp) && stamp <= Date.now() + 300000 ? string(raw.lastVerified, 80) : '';
  const seen = new Set();
  const packages = Array.isArray(raw.packages) ? raw.packages.slice(0, 20).filter(p => p && socialPackages.has(p.package) && !seen.has(p.package) && seen.add(p.package)).map(p => ({ app: string(p.app, 80), package: p.package, versionCode: Number.isSafeInteger(p.versionCode) && p.versionCode >= 0 ? p.versionCode : null, versionName: string(p.versionName, 100), uid: Number.isSafeInteger(p.uid) && p.uid >= 0 ? p.uid : null, installedAPKHashesVerified: p.installedAPKHashesVerified === true, forceStopped: p.forceStopped === true, publisherVerification: string(p.publisherVerification, 250) })) : [];
  const evidencePresent = raw.evidencePresent === true && !!lastVerified;
  return { evidencePresent, packageInstallationAccepted: evidencePresent && raw.historicalEvidence === true && raw.packageInstallationAccepted === true && packages.length === 6 && packages.every(p => p.installedAPKHashesVerified), lastVerified, packages: evidencePresent ? packages : [], backendContinuity: raw.backendContinuity === true, accountLoginAttempted: typeof raw.accountLoginAttempted === 'boolean' ? raw.accountLoginAttempted : null, VPNActivated: typeof raw.VPNActivated === 'boolean' ? raw.VPNActivated : null, historicalEvidence: true };
}
export function normalizeInventory(raw = {}) {
  return {
    updatedAt: string(raw.updatedAt, 80), source: string(raw.source, 120), mode: string(raw.mode, 40) || 'read-only',
    notes: Array.isArray(raw.notes) ? raw.notes.slice(0, 20).map(n => string(n, 600)) : [],
    infrastructure: Array.isArray(raw.infrastructure) ? raw.infrastructure.slice(0, 50).map(h => ({ id: string(h.id, 80), name: string(h.name, 120), type: string(h.type, 60), address: string(h.address, 120), status: state(h.status), detail: string(h.detail), resources: resource(h.resources), url: safeUrl(h.url) })) : [],
    brands: Array.isArray(raw.brands) ? raw.brands.slice(0, 500).filter(b => ID.test(b.id)).map(b => ({
      id: b.id, name: string(b.name, 120), domain: string(b.domain, 253), url: safeUrl(b.url), status: state(b.status), phase: string(b.phase, 80), description: string(b.description), resources: resource(b.resources),
      website: component(b.website), android: component(b.android), automation: component(b.automation), packageEvidence: normalizePackageEvidence(b.packageEvidence),
      social: Array.isArray(b.social) ? b.social.slice(0, 20).map(s => ({ name: string(s.name, 80), status: string(s.status, 80), url: safeUrl(s.url) })) : [],
      backup: { status: string(b.backup?.status, 80), lastAt: string(b.backup?.lastAt, 80), repositoryUrl: safeUrl(b.backup?.repositoryUrl), detail: string(b.backup?.detail) },
      actions: Array.isArray(b.actions) ? b.actions.filter(a => ACTIONS.has(a)) : [],
      jobs: Array.isArray(b.jobs) ? b.jobs.slice(0, 50).map(normalizeJob) : [],
    })) : [],
    services: Array.isArray(raw.services) ? raw.services.slice(0, 50).map(s => ({ id: string(s.id, 80), name: string(s.name, 120), type: string(s.type, 80), status: state(s.status), detail: string(s.detail), url: safeUrl(s.url), deployment: deployment(s.deployment), measuredAt: string(s.measuredAt,80), resources: resource(s.resources) })) : [],
    workspace: normalizeWorkspace(raw.workspace),
    capabilities: { create: raw.capabilities?.create === true, profiles: Array.isArray(raw.capabilities?.profiles) ? raw.capabilities.profiles.slice(0, 20).filter(p => ID.test(p.id)).map(p => ({ id: p.id, name: string(p.name, 120), description: string(p.description) })) : [] },
  };
}
function normalizeJob(j = {}) {
  return { id: string(j.id, 100), brandId: string(j.brandId, 80), action: string(j.action, 80), status: string(j.status, 60), createdAt: string(j.createdAt, 80), updatedAt: string(j.updatedAt, 80), message: string(j.message, 600) };
}
export function normalizeMail(raw = {}) {
  const s = raw.state || {};
  return { ok: raw.ok === true, changed: raw.changed === true, state: {
    outbound_enabled: s.outbound_enabled === true,
    public_mail_enabled: raw.public_mail_enabled === true,
    domains: Array.isArray(s.domains) ? s.domains.slice(0, 500).map(d => ({ domain: string(d.domain,253), active: d.active === true, limits: Object.fromEntries(['aliases','mailboxes','default_quota_bytes','max_quota_bytes','total_quota_bytes'].map(k=>[k,number(d.limits?.[k])])), dkim: { selector: string(d.dkim?.selector,100), dns_name: string(d.dkim?.dns_name,300), dns_value: string(d.dkim?.dns_value,2000) } })) : [],
    mailboxes: Array.isArray(s.mailboxes) ? s.mailboxes.slice(0,5000).map(m => ({ email: string(m.email,320), active: m.active === true, quota_bytes: number(m.quota_bytes), protocols: Object.fromEntries(['imap','pop3','smtp','sieve'].map(k=>[k,m.protocols?.[k]===true])),tls_enforce_in:m.tls_enforce_in===true,tls_enforce_out:m.tls_enforce_out===true })) : [],
    aliases: Array.isArray(s.aliases) ? s.aliases.slice(0,5000).map(a => ({ address: string(a.address,320), destinations: Array.isArray(a.destinations) ? a.destinations.slice(0,100).map(d => string(d,320)) : [], active: a.active === true, sender_allowed: a.sender_allowed === true })) : [],
  } };
}
export function normalizeQuarantine(raw = {}) {
  return {ok:raw.ok===true,changed:raw.changed===true,total:number(raw.total),outbound_enabled:raw.outbound_enabled===true,notifications_enabled:raw.notifications_enabled===true,notification_schedule_seconds:number(raw.notification_schedule_seconds),notification_throttle_seconds:number(raw.notification_throttle_seconds),max_age_days:number(raw.max_age_days),messages:Array.isArray(raw.messages)?raw.messages.slice(0,1000).map(m=>({id:string(m.id,64),received:string(m.received,80),recipient:string(m.recipient,320),sender:string(m.sender,320),subject:string(m.subject,500),category:string(m.category,80),score:number(m.score),notified:m.notified===true,released:m.released===true,size:number(m.size)})):[]};
}
function cookies(req) {
  const result = {}; for (const pair of (req.headers.cookie || '').split(';')) { const i = pair.indexOf('='); if (i > 0) result[pair.slice(0, i).trim()] = pair.slice(i + 1).trim(); } return result;
}
async function body(req) {
  let total = 0, chunks = []; for await (const chunk of req) { total += chunk.length; if (total > MAX_BODY) throw Object.assign(new Error('Request is too large'), { status: 413 }); chunks.push(chunk); }
  try { const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'); if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(); return parsed; } catch { throw Object.assign(new Error('Invalid JSON body'), { status: 400 }); }
}
export function createApp(config = {}) {
  const passwordHash = config.passwordHash || process.env.BRANDFLEET_PASSWORD_HASH || '';
  if (!/^scrypt:[a-f0-9]{32}:[a-f0-9]{128}$/.test(passwordHash)) throw new Error('Set BRANDFLEET_PASSWORD_HASH; generate it using node server.mjs hash-password');
  const username = config.username || process.env.BRANDFLEET_USERNAME || 'admin';
  const inventoryFile = config.inventoryFile || process.env.BRANDFLEET_INVENTORY_FILE || '/var/lib/brandfleet/inventory.json';
  const controllerUrl = config.controllerUrl ?? process.env.BRANDFLEET_CONTROLLER_URL ?? '';
  const controllerToken = config.controllerToken ?? process.env.BRANDFLEET_CONTROLLER_TOKEN ?? '';
  const secureCookie = config.secureCookie ?? process.env.BRANDFLEET_SECURE_COOKIE !== '0';
  const sessions = new Map(), attempts = new Map();
  const controllerEnabled = !!controllerUrl && !!controllerToken;
  if (controllerUrl) { const u = new URL(controllerUrl); if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password) throw new Error('Invalid controller URL'); }
  const screenGateway = config.screenGateway || 'http://127.0.0.1:9002';
  let webmailConfig = {};
  try {
    const file = config.webmailConfigFile || '/etc/brandfleet/webmail-sso-issuer.json', info = lstatSync(file);
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600) throw Error('Invalid private webmail configuration');
    webmailConfig = JSON.parse(readFileSync(file, 'utf8'));
    if (Object.keys(webmailConfig).sort().join(',') !== 'issuerKey,principal') throw Error('Invalid private webmail configuration');
  } catch { webmailConfig = {}; }
  const webmail = config.webmail || createWebmail(webmailConfig);
  const security = { 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-src https:; object-src 'none'; base-uri 'none'; form-action 'self' https://mail.example.com; frame-ancestors 'none'", 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin', 'X-Frame-Options': 'DENY', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()', 'Cache-Control': 'no-store' };
  function send(res, status, value, headers = {}) { res.writeHead(status, { ...security, 'Content-Type': 'application/json; charset=utf-8', ...headers }); res.end(JSON.stringify(value)); }
  const session = req => { const key = cookies(req).brandfleet_session; const s = sessions.get(key); if (s && s.expiresAt > Date.now()) return s; if (key) sessions.delete(key); return null; };
  const sessionCookie = (value, ttl) => `brandfleet_session=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${ttl}${secureCookie ? '; Secure' : ''}`;
  async function controller(path, method = 'GET', data) {
    const response = await fetch(`${controllerUrl.replace(/\/$/, '')}${path}`, { method, headers: { Authorization: `Bearer ${controllerToken}`, 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined, signal: AbortSignal.timeout(path === '/mail-admin' ? 110000 : path.endsWith('/social/launch') ? 30000 : 12000), redirect: 'error' });
    const text = await response.text(); if (text.length > 4e6) throw new Error('Controller response too large');
    let payload; try { payload = JSON.parse(text); } catch { throw new Error('Controller returned invalid JSON'); }
    if (!response.ok) {
      // Only fixed launcher conditions become UI retry guidance. Never reflect
      // a controller diagnostic, command, package argument or private payload.
      if (path.endsWith('/social/launch') && [409, 503].includes(response.status)) {
        const message = response.status === 409
          ? 'This Android device is busy. Wait a moment and try opening the app again.'
          : 'The Android app is temporarily unavailable. Wait a moment and try opening it again.';
        throw Object.assign(new Error(message), { status: response.status });
      }
      throw Object.assign(new Error('Controller rejected this request. Check the operation log.'), { status: response.status >= 400 && response.status < 500 ? response.status : 502 });
    }
    return payload;
  }
  async function inventory() {
    const raw = controllerEnabled ? await controller('/inventory') : JSON.parse(await readFile(inventoryFile, 'utf8'));
    if (!raw || typeof raw !== 'object' || !Array.isArray(raw.brands)) throw new Error('Invalid inventory schema');
    const result = normalizeInventory(raw); if (!controllerEnabled) { result.mode = 'read-only'; result.capabilities.create = false; result.brands.forEach(b => { b.actions = []; }); }
    const measuredAt = Date.parse(result.updatedAt);
    if (!Number.isFinite(measuredAt) || Date.now() - measuredAt > 120000 || measuredAt - Date.now() > 60000) { result.capabilities.create = false; result.brands.forEach(b => { b.actions = []; }); }
    return result;
  }
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost'), path = url.pathname;
      if (path === '/healthz' && req.method === 'GET') return send(res, 200, { ok: true });
      if (path === '/api/login' && req.method === 'POST') {
        const ip = req.socket.remoteAddress || 'unknown'; const now = Date.now(); const attemptsForIp = (attempts.get(ip) || []).filter(t => now - t < 15 * 60e3);
        if (attemptsForIp.length >= 10) return send(res, 429, { error: 'Too many login attempts. Try again in 15 minutes.' });
        const data = await body(req); if (typeof data.username !== 'string' || typeof data.password !== 'string' || data.password.length > 1024) return send(res, 400, { error: 'Enter your username and password.' });
        const validPassword = verifyPassword(data.password, passwordHash);
        if (data.username !== username || !validPassword) { attemptsForIp.push(now); attempts.set(ip, attemptsForIp); return send(res, 401, { error: 'Username or password is incorrect.' }); }
        attempts.delete(ip);
        const key = randomBytes(32).toString('hex'), csrf = randomBytes(32).toString('hex');
        sessions.set(key, { username, csrf, expiresAt: now + 8 * 3600e3 });
        return send(res, 200, { username, csrf, expiresAt: now + 8 * 3600e3 }, { 'Set-Cookie': sessionCookie(key, 8 * 3600) });
      }
      const view = path.match(/^\/android\/view\/(bf-[a-z0-9][a-z0-9-]{0,40})$/);
      if (view && req.method === 'GET') {
        if (!session(req)) {
          const app = url.searchParams.get('app');
          const returnTo = `${path}${Object.hasOwn(launchPackages, app) ? `?app=${app}` : ''}`;
          return send(res, 303, { error: 'Sign in to the dashboard first.' }, { Location: `/?returnTo=${encodeURIComponent(returnTo)}` });
        }
        const content = await readFile(join(here, 'public', 'android-control.html'));
        res.writeHead(200, { ...security, 'Content-Type': 'text/html; charset=utf-8' }); return res.end(content);
      }
      if (path.startsWith('/api/')) {
        const s = session(req); if (!s) return send(res, 401, { error: 'Sign in to continue.' });
        if (!['GET', 'HEAD'].includes(req.method)) { const csrf = req.headers['x-csrf-token']; if (typeof csrf !== 'string' || csrf !== s.csrf) return send(res, 403, { error: 'Invalid request token. Refresh and try again.' }); }
        if (path === '/api/webmail' && req.method === 'GET') return send(res, 200, await webmail.status(s.username));
        if (path === '/api/webmail/open' && req.method === 'POST') return send(res, 200, await webmail.open(s.username, req.headers.origin, await body(req)));
        if (path === '/api/mail-admin' && ['GET','POST'].includes(req.method)) {
          if (!controllerEnabled) return send(res, 503, { error: 'Mail administration is unavailable.' });
          const data = req.method === 'GET' ? { action: 'list' } : await body(req);
          if (!MAIL_ACTIONS.has(data.action)) return send(res, 400, { error: 'Unsupported mail operation.' });
          const fields = ['action','domain','email','password','quota_bytes','active','address','destinations','sender_allowed','protocols','tls_enforce_in','tls_enforce_out','limits','id'];
          const payload = Object.fromEntries(fields.filter(k => Object.hasOwn(data,k)).map(k => [k,data[k]]));
          const raw=await controller('/mail-admin','POST',payload);
          return send(res, 200, data.action.startsWith('quarantine_')?normalizeQuarantine(raw):normalizeMail(raw));
        }
        const launch = path.match(/^\/api\/brands\/(bf-[a-z0-9][a-z0-9-]{0,40})\/social\/launch$/);
        if (launch && req.method === 'POST') {
          const data = await body(req);
          if (!Object.hasOwn(launchPackages, data.app) || Object.keys(data).some(k => k !== 'app')) return send(res, 400, { error: 'Choose a supported Android app.' });
          if (!controllerEnabled) return send(res, 503, { error: 'App launch is unavailable until the controller is connected.' });
          const raw = await controller(`/brands/${launch[1]}/social/launch`, 'POST', { app: data.app });
          return send(res, 200, normalizeLaunch(raw, launch[1], data.app));
        }
        const device = path.match(/^\/api\/brands\/(bf-[a-z0-9][a-z0-9-]{0,40})\/device\/(screen|input)$/);
        if (device && ((device[2] === 'screen' && req.method === 'GET') || (device[2] === 'input' && req.method === 'POST'))) {
          const [, id, action] = device;
          const payload = action === 'input' ? await body(req) : undefined;
          const upstream = await fetch(`${screenGateway}/${action}/${id}`, { method: req.method, headers: { 'Content-Type': 'application/json' }, body: payload ? JSON.stringify(payload) : undefined, signal: AbortSignal.timeout(10000), redirect: 'error' });
          if (!upstream.ok) return send(res, upstream.status, { error: 'Android device is unavailable.' });
          if (action === 'screen') {
            const pixels = Buffer.from(await upstream.arrayBuffer());
            if (pixels.length > 8e6 || !pixels.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return send(res, 502, { error: 'Invalid device display.' });
            res.writeHead(200, { ...security, 'Content-Type': 'image/png' }); return res.end(pixels);
          }
          return send(res, 200, { ok: true });
        }
        if (path === '/api/session' && req.method === 'GET') return send(res, 200, { username: s.username, csrf: s.csrf, expiresAt: s.expiresAt });
        if (path === '/api/logout' && req.method === 'POST') { sessions.delete(cookies(req).brandfleet_session); return send(res, 200, { ok: true }, { 'Set-Cookie': sessionCookie('', 0) }); }
        if (path === '/api/inventory' && req.method === 'GET') { try { return send(res, 200, await inventory()); } catch { return send(res, 503, { error: 'Live inventory is unavailable. Existing services continue running; dashboard controls are disabled.' }); } }
        if (path === '/api/jobs' && req.method === 'GET') { if (!controllerEnabled) return send(res, 200, { jobs: [] }); const raw = await controller('/jobs'); return send(res, 200, { jobs: (Array.isArray(raw.jobs) ? raw.jobs : []).slice(0, 100).map(normalizeJob) }); }
        if (req.method === 'POST' && path === '/api/brands') {
          if (!controllerEnabled) return send(res, 503, { error: 'Provisioning is unavailable until the controller is connected.' });
          const current = await inventory(); if (!current.capabilities.create) return send(res, 409, { error: 'Provisioning is not enabled for this installation.' });
          const data = await body(req); const name = typeof data.name === 'string' ? data.name.trim() : '', domain = typeof data.domain === 'string' ? data.domain.trim().toLowerCase() : '';
          if (!name || name.length > 120 || !/^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(domain) || !current.capabilities.profiles.some(p => p.id === data.profile)) return send(res, 400, { error: 'Choose a supported profile and enter a valid name and domain.' });
          const raw = await controller('/brands', 'POST', { name, domain, profile: data.profile, requestId: randomBytes(16).toString('hex') });
          return send(res, 202, { job: normalizeJob(raw.job), message: 'Provisioning request accepted.' });
        }
        const match = path.match(/^\/api\/brands\/([a-zA-Z0-9][a-zA-Z0-9_-]{0,79})\/actions\/([a-z]+)$/);
        if (req.method === 'POST' && match) {
          const [, id, action] = match; if (!ACTIONS.has(action)) return send(res, 400, { error: 'Unsupported action.' });
          if (!controllerEnabled) return send(res, 503, { error: 'Device controls are unavailable until the controller is connected.' });
          const data = await body(req), current = await inventory(), brand = current.brands.find(b => b.id === id);
          if (!brand) return send(res, 404, { error: 'Brand was not found.' });
          if (!brand.actions.includes(action)) return send(res, 409, { error: 'This action is unavailable for this environment.' });
          if (['remove', 'archive', 'stop', 'restart'].includes(action) && data.confirm !== id) return send(res, 400, { error: 'Confirm the affected environment before proceeding.' });
          const raw = await controller(`/brands/${encodeURIComponent(id)}/actions/${action}`, 'POST', { requestId: randomBytes(16).toString('hex'), confirm: data.confirm === id ? id : undefined });
          return send(res, 202, { job: normalizeJob(raw.job), message: 'Operation request accepted.' });
        }
        return send(res, 404, { error: 'Endpoint not found.' });
      }
      if (req.method !== 'GET' && req.method !== 'HEAD') return send(res, 405, { error: 'Method not allowed.' });
      const staticFiles = { '/': ['index.html', 'text/html'], '/index.html': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'], '/webmail-core.js': ['webmail-core.js', 'text/javascript'], '/mail-admin.js': ['mail-admin.js', 'text/javascript'], '/style.css': ['style.css', 'text/css'], '/android-control.js': ['android-control.js', 'text/javascript'], '/android-viewer-core.js': ['android-viewer-core.js', 'text/javascript'], '/android-control.css': ['android-control.css', 'text/css'] };
      const asset = staticFiles[path]; if (!asset) return send(res, 404, { error: 'Not found.' });
      const content = await readFile(join(here, 'public', asset[0])); res.writeHead(200, { ...security, 'Content-Type': `${asset[1]}; charset=utf-8` }); res.end(req.method === 'HEAD' ? undefined : content);
    } catch (error) { send(res, error.status || 502, { error: error.status ? error.message : 'The operation could not be completed. Check the controller and try again.' }); }
  });
  server.requestTimeout = 120000; server.headersTimeout = 15000;
  return server;
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv[2] === 'hash-password') {
    let input = ''; for await (const c of process.stdin) input += c; const password = input.replace(/[\r\n]+$/, '');
    if (password.length < 16) throw new Error('Use at least 16 characters');
    process.stdout.write(`${hashPassword(password)}\n`);
  } else {
    const server = createApp(); const port = Number(process.env.PORT || 8170), host = process.env.HOST || '127.0.0.1';
    server.listen(port, host, () => process.stdout.write(`BrandFleet listening on ${host}:${port}\n`));
  }
}
