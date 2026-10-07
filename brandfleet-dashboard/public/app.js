import { APPS, viewerPath, safeReturnPath } from '/android-viewer-core.js';
import { submitWebmailGrant } from '/webmail-core.js';
const $ = selector => document.querySelector(selector);
const all = selector => [...document.querySelectorAll(selector)];
let session = null, inventory = null, jobs = [], page = 'overview', selectedAction = null, search = '', filter = 'all', refreshInProgress = false, webmail = null, webmailOpening = false;
function node(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function append(parent, ...children) { children.filter(Boolean).forEach(c => parent.append(c)); return parent; }
function button(label, cls, handler, disabled = false) { const b = node('button', cls, label); b.type = 'button'; b.disabled = disabled; if (handler) b.addEventListener('click', handler); return b; }
function link(label, url, cls = '') { const a = node('a', cls, label); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; }
function badge(value) { return node('span', `status ${value || 'unknown'}`, value || 'unknown'); }
function bytes(value) { if (value === null || value === undefined) return '—'; if (value === 0) return '0 B'; const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; const i = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1); return `${(value / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${units[i]}`; }
function percent(value) { return typeof value === 'number' ? `${value.toFixed(1)}%` : '—'; }
function date(value) { const d = new Date(value); return Number.isNaN(d.getTime()) ? 'Not observed' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
function componentLabel(c) { return c.status === 'unknown' ? 'Not observed' : c.status === 'unavailable' ? 'Not connected' : c.status; }
function initial(name) { return name.split(/[\s.-]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'B'; }
function toast(text) { $('#toast').textContent = text; $('#toast').classList.remove('hidden'); setTimeout(() => $('#toast').classList.add('hidden'), 5000); }
async function api(path, method = 'GET', data) {
  const r = await fetch(`/api${path}`, { method, headers: { ...(method !== 'GET' ? { 'Content-Type': 'application/json', 'X-CSRF-Token': session?.csrf || '' } : {}) }, body: data === undefined ? undefined : JSON.stringify(data), credentials: 'same-origin' });
  const result = await r.json(); if (!r.ok) { if (r.status === 401 && path !== '/login') showLogin(); throw new Error(result.error || 'Request failed'); } return result;
}
function showLogin() { session = null; webmail = null; $('#open-inbox').disabled = true; $('#app-view').classList.add('hidden'); $('#login-view').classList.remove('hidden'); $('#password').value = ''; }
function showApp() { const returnTo = safeReturnPath(new URL(location.href).searchParams.get('returnTo')); if (returnTo) { location.replace(returnTo); return; } $('#login-view').classList.add('hidden'); $('#app-view').classList.remove('hidden'); $('#owner-name').textContent = session.username; refreshWebmail(); }
async function refreshWebmail() {
  try { webmail = await api('/webmail'); } catch { webmail = null; }
  const inbox = $('#open-inbox'); inbox.disabled = webmailOpening || webmail?.available !== true;
  all('[data-webmail-open]').forEach(b => { b.disabled = webmailOpening || webmail?.available !== true; });
  inbox.title = webmail?.available === true ? `Sign in to ${webmail.mailbox}` : 'One-click inbox is unavailable; use the existing webmail login.';
}
async function openInbox() {
  if (webmailOpening) return;
  webmailOpening = true;
  const inbox = $('#open-inbox'); inbox.disabled = true;
  all('[data-webmail-open]').forEach(b => { b.disabled = true; });
  try { submitWebmailGrant(await api('/webmail/open', 'POST', {}), document); }
  catch (error) { toast(error.message); }
  finally { webmailOpening = false; inbox.disabled = webmail?.available !== true; all('[data-webmail-open]').forEach(b => { b.disabled = webmail?.available !== true; }); }
}
$('#open-inbox').addEventListener('click', openInbox);
$('#login-form').addEventListener('submit', async e => { e.preventDefault(); const submit = e.target.querySelector('button[type=submit]'); submit.disabled = true; $('#login-error').textContent = ''; try { session = await api('/login', 'POST', { username: $('#username').value, password: $('#password').value }); $('#password').value = ''; showApp(); await refresh(); } catch (err) { $('#login-error').textContent = err.message; } finally { submit.disabled = false; } });
$('#logout').addEventListener('click', async () => { try { await api('/logout', 'POST', {}); } catch {} showLogin(); });
$('#refresh').addEventListener('click', () => { refreshWebmail(); refresh(); });
$('#mobile-menu').addEventListener('click', () => $('.sidebar').classList.toggle('open'));
all('[data-page]').forEach(b => b.addEventListener('click', () => setPage(b.dataset.page)));
function setPage(next) { page = next; all('[data-page]').forEach(b => b.classList.toggle('active', b.dataset.page === page)); $('.sidebar').classList.remove('open'); render(); }
async function refresh() {
  if (!session || refreshInProgress) return; refreshInProgress = true; $('#refresh').disabled = true;
  try { inventory = await api('/inventory'); $('#inventory-error').classList.add('hidden'); try { jobs = (await api('/jobs')).jobs; } catch { jobs = inventory.brands.flatMap(b => b.jobs); } render(); }
  catch (err) { $('#inventory-error').textContent = err.message; $('#inventory-error').classList.remove('hidden'); $('#mode-badge').textContent = 'Connection unavailable'; $('#create-brand').disabled = true; inventory = null; $('#content').replaceChildren(empty('Inventory unavailable', 'Controls remain disabled until a fresh inventory can be loaded.')); }
  finally { refreshInProgress = false; $('#refresh').disabled = false; }
}
const titles = {
  overview: ['YOUR FLEET, AT A GLANCE', 'A home for every brand.', 'Keep your applications, devices and operations in view.', 'Overview'],
  brands: ['YOUR BRAND ENVIRONMENTS', 'Every project, its own space.', 'Website hosting, Android devices and marketing workflows together.', 'Brands & devices'],
  operations: ['RECENT OPERATIONS', 'Know what happened.', 'Lifecycle requests and their reported progress.', 'Operations'],
  workspace: ['YOUR TEAM WORKSPACE', 'Work together, in one place.', 'Files, conversations, calendars and projects for your team.', 'Team workspace'],
  services: ['SHARED SERVICES', 'The foundation underneath.', 'Email and workspaces serving your domains.', 'Shared services'],
  mail: ['MAIL ADMINISTRATION', 'A mailbox for every domain.', 'Manage domains, mailboxes, aliases and quotas.', 'Mail administration'],
  infrastructure: ['YOUR PHYSICAL & VIRTUAL RESOURCES', 'See the whole system.', 'Measured capacity across the systems supporting your fleet.', 'Infrastructure'],
};
function render() {
  const [eyebrow, title, description, crumb] = titles[page]; $('.page-heading .eyebrow').textContent = eyebrow; $('#page-title').textContent = title; $('#page-description').textContent = description; $('#page-crumb').textContent = crumb;
  if (!inventory) return;
  $('#brand-count').textContent = inventory.brands.length; $('#mode-badge').textContent = inventory.mode === 'read-only' ? 'Read-only inventory' : inventory.mode.replace(/-/g, ' '); $('#create-brand').disabled = !inventory.capabilities.create;
  const time = Date.parse(inventory.updatedAt), stale = !Number.isFinite(time) || Date.now() - time > 120000;
  $('#stale-notice').classList.toggle('hidden', !stale); $('#stale-notice').textContent = 'This inventory is older than two minutes or has no timestamp. Refresh before relying on its status.';
  $('#freshness').textContent = `Last measured ${date(inventory.updatedAt)}${inventory.source ? ` · ${inventory.source}` : ''}`;
  const content = $('#content'); content.replaceChildren();
  if (page === 'overview') renderOverview(content);
  if (page === 'brands') renderBrands(content);
  if (page === 'operations') content.append(operationsPanel());
  if (page === 'workspace') renderWorkspace(content);
  if (page === 'mail') {
    const inbox = node('section', 'panel');
    const open = button('Open my inbox', 'button primary', openInbox, webmail?.available !== true); open.dataset.webmailOpen = '';
    inbox.append(node('h2', '', 'Your inbox'), node('p', '', webmail?.available ? `Open ${webmail.mailbox} directly. Other mailbox and Google sign-ins stay separate.` : 'Use your existing webmail sign-in while one-click access is unavailable.'), open, link('Manual webmail ↗', 'https://mail.example.com/', 'button secondary'));
    content.append(inbox); window.renderMailAdmin(content, api);
  }
  if (page === 'services') { const grid = node('div', 'service-grid'); inventory.services.forEach(s => grid.append(serviceCard(s))); content.append(grid.children.length ? grid : empty('No shared services reported', 'The inventory has not reported email or workspace services.')); }
  if (page === 'infrastructure') { const grid = node('div', 'infra-grid'); inventory.infrastructure.forEach(h => grid.append(hostCard(h))); content.append(grid.children.length ? grid : empty('No resource measurements', 'The infrastructure adapter has not supplied measurements.')); }
}
function empty(title, detail) { return append(node('div', 'empty'), node('strong', '', title), node('span', '', detail)); }
function sectionHeading(title, description, action) { const h = node('div', 'section-top'); append(h, append(node('div'), node('h2', '', title), description ? node('p', '', description) : null)); if (action) h.append(action); return h; }
function metric(label, value, detail, icon) { return append(node('div', 'metric'), append(node('div', 'metric-top'), node('span', '', label), node('span', 'metric-icon', icon)), node('div', 'metric-number', value), node('div', 'metric-note', detail)); }
function renderOverview(content) {
  const brands = inventory.brands, running = brands.filter(b => ['running', 'healthy', 'ready'].includes(b.status)).length, devices = brands.filter(b => ['running', 'healthy', 'ready'].includes(b.android.status)).length, activeJobs = jobs.filter(j => ['running', 'queued', 'pending'].includes(j.status)).length;
  content.append(append(node('div', 'metrics'), metric('Brand environments', brands.length, 'Reported by your inventory', '▧'), metric('Environments running', running, `${brands.length - running} in other states`, '◉'), metric('Android devices online', devices, 'Runtime state reported by the controller', '▯'), metric('Active operations', activeJobs, 'Queued or in progress', '↻')));
  content.append(sectionHeading('Your brand environments', 'Independent systems. Connected workflows.', button('View all brands ↗', 'text-button', () => setPage('brands'))));
  const grid = node('div', 'brand-grid'); brands.slice(0, 8).forEach(b => grid.append(brandCard(b))); content.append(brands.length ? grid : empty('Your fleet is taking shape', 'Environments appear here once the inventory reports them.'));
  const summary = node('div', 'summary-grid'); summary.append(sharedServicesPanel(), infrastructurePanel()); content.append(summary);
  if (inventory.notes.length) { const n = node('div', 'notice'); n.style.marginTop = '22px'; inventory.notes.forEach(note => n.append(node('p', '', note))); content.append(n); }
}
function renderBrands(content) {
  const bar = node('div', 'filter-bar'), input = node('input'); input.placeholder = 'Search brand or domain…'; input.setAttribute('aria-label', 'Search brands'); input.value = search;
  const select = node('select'); select.setAttribute('aria-label', 'Filter status'); [['all', 'All environments'], ['running', 'Running'], ['pilot', 'Pilot'], ['stopped', 'Stopped'], ['error', 'Needs attention']].forEach(([v, t]) => { const o = node('option', '', t); o.value = v; select.append(o); }); select.value = filter;
  const grid = node('div', 'brand-grid'); function update() { grid.replaceChildren(); const filtered = inventory.brands.filter(b => `${b.name} ${b.domain}`.toLowerCase().includes(search.toLowerCase()) && (filter === 'all' || b.status === filter || filter === 'error' && ['degraded', 'unavailable'].includes(b.status))); filtered.forEach(b => grid.append(brandCard(b))); if (!filtered.length) grid.append(empty('No matching environments', 'Try another search or status filter.')); }
  input.addEventListener('input', () => { search = input.value; update(); }); select.addEventListener('change', () => { filter = select.value; update(); }); append(bar, input, select); content.append(bar, grid); update();
}
function brandCard(b) {
  const card = node('article', 'brand-card');
  card.append(append(node('div', 'brand-card-top'), node('span', 'brand-icon', initial(b.name)), append(node('div', 'brand-info'), node('h3', '', b.name), node('div', 'brand-domain', b.domain || 'No domain configured')), badge(b.status)));
  card.append(node('div', 'brand-phase', b.phase || 'Environment status'));
  const c = node('div', 'components'); [['Website', '⌘', b.website], ['Android device', '▯', b.android], ['Automation', '✦', b.automation]].forEach(([label, icon, component]) => c.append(append(node('div', 'component-row'), node('span', 'component-icon', icon), node('span', '', label), node('span', `component-status ${component.status}`, componentLabel(component))))); card.append(c);
  const r = node('div', 'brand-resources'); [['CPU ', percent(b.resources.cpuPercent)], ['RAM ', bytes(b.resources.memoryBytes)]].forEach(([l, v]) => r.append(append(node('span'), node('span', '', l), node('strong', '', v)))); card.append(r);
  if (viewerPath(b.id) && b.android.screenUrl) card.append(socialLauncher(b, true));
  card.append(append(node('div', 'brand-footer'), button('Open environment ↗', 'text-button', () => showDetail(b.id)), node('span', 'footer-note', b.actions.length ? 'Controls connected' : 'View only'))); return card;
}
function socialLauncher(b, compact = false) {
  const section = node('div', compact ? 'brand-social-launch' : 'detail-section direct-social-launch');
  section.append(node(compact ? 'strong' : 'h3', '', 'Open & control Android apps'));
  const dock = node('div', 'social-launch-buttons');
  (compact ? APPS.slice(0, 3) : APPS).forEach(app => {
    const a = node('a', 'button secondary', app.name); a.href = viewerPath(b.id, app.id); a.title = `Open ${app.name} on ${b.name} and control it in your browser`; dock.append(a);
  });
  const screen = node('a', 'button primary', 'Open live device'); screen.href = viewerPath(b.id); dock.append(screen); section.append(dock);
  if (!compact) section.append(node('p', '', 'The app opens on this brand’s Android device. Use the live screen, keyboard and navigation controls in the same browser tab.'));
  return section;
}
function sharedServicesPanel() {
  const p = node('section', 'panel'); p.append(append(node('div', 'panel-title'), node('h2', '', 'Shared services'), node('span', '', 'Email & workspace')));
  inventory.services.slice(0, 4).forEach(s => { const row = node('div', 'service-row'); append(row, node('span', 'service-icon', s.type.toLowerCase().includes('mail') ? '✉' : '▤'), append(node('div', 'service-info'), node('strong', '', s.name), node('p', '', s.type || s.detail)), badge(s.status)); if (s.url) row.append(link('↗', s.url)); p.append(row); }); if (!inventory.services.length) p.append(empty('No services reported', 'Waiting for the service inventory.')); return p;
}
function infrastructurePanel() { const p = node('section', 'panel'); p.append(append(node('div', 'panel-title'), node('h2', '', 'Infrastructure capacity'), node('span', '', 'Measured usage'))); inventory.infrastructure.slice(0, 3).forEach(h => { const r = node('div', 'host-row'); r.append(append(node('div', 'host-heading'), node('strong', '', h.name), badge(h.status)), node('div', 'host-details', `${h.type}${h.address ? ` · ${h.address}` : ''}`)); const meter = node('div', 'meter'), fill = node('div', 'meter-fill'), total = h.resources.memoryLimitBytes, used = h.resources.memoryBytes; if (total && used !== null) { const ratio = Math.min(100, used / total * 100); fill.style.width = `${ratio}%`; if (ratio > 85) fill.classList.add('high'); } meter.append(fill); r.append(append(node('div', 'host-meter'), meter, node('span', 'meter-label', `${bytes(used)} / ${bytes(total)}`))); p.append(r); }); if (!inventory.infrastructure.length) p.append(empty('No host measurements', 'Waiting for resource data.')); return p; }
function renderWorkspace(content) {
  const w = inventory.workspace;
  if (!w) return content.append(empty('Workspace measurement unavailable', 'Refresh to load the installed workspace applications.'));
  const hero = node('section', 'panel workspace-hero');
  const copy = node('div', 'workspace-hero-copy');
  copy.append(node('span', 'eyebrow', w.deployment === 'production' ? 'YOUR LIVE WORKSPACE' : w.deployment === 'preview' ? 'PRIVATE WORKSPACE PREVIEW' : 'WORKSPACE STATUS'), node('h2', '', 'One home for your team.'), node('p', '', 'Start a conversation, plan a project or open a shared file. Your existing workspace sign-in and domain permissions apply.'), append(node('div', 'workspace-meta'), badge(w.status), node('span', '', w.version ? `Nextcloud ${w.version}` : 'Version not measured'), node('span', '', `Measured ${date(w.measuredAt)}`)));
  hero.append(copy);
  if (w.url) hero.append(link('Open workspace ↗', w.url.replace(/\/$/, '') + '/apps/dashboard/', 'button primary'));
  content.append(hero);
  const grid = node('div', 'workspace-app-grid');
  const icons = { files: '▤', spreed: '◌', calendar: '▦', contacts: '◎', deck: '▥', webmail: '✉' };
  w.apps.forEach(a => {
    const card = node('section', `panel workspace-app ${a.id}`);
    card.append(append(node('div', 'workspace-app-top'), node('span', 'workspace-app-icon', icons[a.id] || '✦'), badge(a.status)), node('h2', '', a.name), node('p', '', a.detail));
    const bottom = node('div', 'workspace-app-bottom');
    bottom.append(node('span', '', a.version ? `v${a.version}` : a.status === 'unknown' ? 'Not measured' : ''));
    bottom.append(a.url && ['ready', 'running'].includes(a.status) ? link('Open ↗', a.url, 'button secondary') : button('Unavailable', 'button secondary', null, true));
    card.append(bottom); grid.append(card);
  });
  content.append(grid.children.length ? grid : empty('Installed apps could not be measured', 'The dashboard will show the workspace launcher after a successful service measurement.'));
  const notes = node('div', 'workspace-notes');
  const tenant = node('section', 'panel'); tenant.append(node('span', 'eyebrow', 'DOMAIN ACCESS'), node('h2', '', 'A private space for each tenant.'), node('p', '', w.tenantIsolation.detail || 'Tenant permission validation is not available.'));
  if (w.tenantIsolation.status === 'verified') tenant.append(node('p', 'workspace-note-small', `Last permission test ${date(w.tenantIsolation.verifiedAt)}. Access follows the workspace account and domain group.`));
  const google = node('section', 'panel'); google.append(node('span', 'eyebrow', 'EXISTING GOOGLE WORKSPACE'), node('h2', '', 'Your Google mail stays with Google.'), node('p', '', w.retainedGoogleDomains.length ? `${w.retainedGoogleDomains.join(' and ')} keep their existing Google mail routing and accounts. Local mail domains are managed separately in Mail administration.` : 'Google mail ownership has not been reported. No domain routing is changed by this launcher.'));
  notes.append(tenant, google); content.append(notes);
}
function serviceCard(s) { return append(node('section', 'panel large-service'), append(node('div', 'service-header'), node('span', 'service-icon', s.type.toLowerCase().includes('mail') ? '✉' : '▤'), badge(s.status)), node('h2', '', s.name), node('span', 'eyebrow', s.type), node('p', '', s.detail || 'No additional service detail reported.'), s.url ? link('Open service ↗', s.url, 'button secondary') : button('Service link unavailable', 'button secondary', null, true)); }
function hostCard(h) { const c = node('section', 'panel infra-card'); c.append(append(node('div', 'infra-header'), node('h2', '', h.name), badge(h.status)), node('p', '', `${h.type}${h.address ? ` · ${h.address}` : ''}`)); const values = node('div', 'infra-measurements'); [['CPU usage', percent(h.resources.cpuPercent)], ['Allocated cores', h.resources.cpuCores ?? '—'], ['Memory usage', bytes(h.resources.memoryBytes)]].forEach(([l, v]) => values.append(append(node('div'), node('label', '', l), node('strong', '', v)))); c.append(values, node('p', '', `Memory limit ${bytes(h.resources.memoryLimitBytes)} · Disk ${bytes(h.resources.diskBytes)} / ${bytes(h.resources.diskLimitBytes)}`)); if (h.detail) c.append(node('p', '', h.detail)); if (h.url) c.append(link('Open management ↗', h.url, 'button secondary')); return c; }
function operationsPanel(brandId) { const list = brandId ? jobs.filter(j => j.brandId === brandId) : jobs, panel = node('section', 'panel'); if (!list.length) return empty('No operations reported', 'Lifecycle operations appear here when the controller reports them.'); const table = node('table', 'operation-list'), head = node('thead'), hr = node('tr'); ['Operation', 'Environment', 'Status', 'Started', 'Detail'].forEach(t => hr.append(node('th', '', t))); head.append(hr); table.append(head); const body = node('tbody'); [...list].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)).forEach(j => { const row = node('tr'); row.append(node('td', '', j.action || 'Operation'), node('td', '', inventory.brands.find(b => b.id === j.brandId)?.name || j.brandId || '—'), append(node('td'), badge(j.status)), node('td', '', date(j.createdAt)), node('td', 'operation-message', j.message || 'No additional detail')); body.append(row); }); table.append(body); panel.append(table); return panel; }
function showDetail(id) {
  const b = inventory?.brands.find(b => b.id === id); if (!b) return; const content = $('#detail-content'); content.replaceChildren(); const top = node('div', 'detail-top');
  top.append(append(node('div', 'dialog-header'), node('span', 'eyebrow', b.phase || 'BRAND ENVIRONMENT'), button('×', 'icon-button', () => $('#detail-dialog').close())));
  top.append(append(node('div', 'detail-heading'), node('span', 'brand-icon', initial(b.name)), append(node('div'), node('h2', '', b.name), node('p', '', b.domain || 'No domain configured')), badge(b.status))); content.append(top);
  const body = node('div', 'detail-body'), components = node('div', 'detail-components');
  if (viewerPath(b.id) && b.android.screenUrl) body.append(socialLauncher(b));
  [['Website & application', b.website, b.website.url || b.url, 'Open website ↗'], ['Android device', b.android, b.android.screenUrl, 'Open remote screen ↗'], ['Marketing automation', b.automation, b.automation.url, 'Open automation ↗']].forEach(([title, component, url, label]) => { const box = node('div', 'detail-component'); box.append(node('h3', '', title), badge(component.status), node('p', '', component.detail || 'No component detail reported.')); if (title === 'Android device' && viewerPath(b.id) && url) { const view = node('a', 'button secondary', 'Open live device'); view.href = viewerPath(b.id); box.append(view); } else if (url) box.append(link(label, url, 'button secondary')); else box.append(button(title.includes('Android') ? 'Screen unavailable' : 'Link unavailable', 'button secondary', null, true)); if (component.terminalUrl) box.append(link('Open terminal ↗', component.terminalUrl, 'button secondary')); components.append(box); }); body.append(components);
  const values = node('div', 'detail-keyvalues'); [['CPU usage', percent(b.resources.cpuPercent)], ['CPU limit', b.resources.cpuCores === null ? '—' : `${b.resources.cpuCores} cores`], ['Memory', `${bytes(b.resources.memoryBytes)} / ${bytes(b.resources.memoryLimitBytes)}`], ['Storage', `${bytes(b.resources.diskBytes)} / ${bytes(b.resources.diskLimitBytes)}`], ['Android address', b.android.privateIp || 'Not assigned'], ['Runtime', b.website.runtime || 'Not observed']].forEach(([l, v]) => values.append(append(node('div'), node('span', '', l), node('strong', '', v)))); body.append(values);
  const controls = node('div', 'detail-section'); controls.append(node('h3', '', 'Environment controls'), node('p', '', b.actions.length ? 'Disruptive operations affect this environment. Other brands remain independently managed.' : 'This environment has no lifecycle controls enabled. Existing deployments remain view-only.')); const cb = node('div', 'detail-controls'); ['start', 'stop', 'restart', 'clone', 'archive', 'remove'].forEach(a => cb.append(button(a[0].toUpperCase() + a.slice(1), `button ${a === 'remove' ? 'danger' : 'secondary'}`, () => openAction(b, a), !b.actions.includes(a)))); controls.append(cb); body.append(controls);
  const evidence = b.packageEvidence, installed = node('div', 'detail-section'); installed.append(node('h3', '', 'Android applications · verified history'));
  if (evidence?.evidencePresent) {
    installed.append(node('p', '', `${evidence.packageInstallationAccepted ? 'Six-package rollout accepted' : 'Package verification incomplete'} · Last verified ${date(evidence.lastVerified)}. These are recorded results; refresh does not inspect live packages.`));
    const list = node('div', 'package-evidence-list');
    const labels = { 'com.figma.mirror': 'Figma Mirror companion', 'com.whatsapp': 'WhatsApp', 'com.twitter.android': 'X', 'com.linkedin.android': 'LinkedIn', 'com.tailscale.ipn': 'Tailscale', 'com.instagram.android': 'Instagram' };
    evidence.packages.forEach(p => { const row = node('div', 'package-evidence-row'); row.append(append(node('div'), node('strong', '', labels[p.package] || p.app), node('span', '', p.package)), append(node('div'), node('strong', '', p.versionName || `Build ${p.versionCode ?? 'unknown'}`), node('span', '', `Build ${p.versionCode ?? 'unknown'} · UID ${p.uid ?? 'unknown'}`)), badge(p.installedAPKHashesVerified ? 'verified' : 'unknown')); list.append(row); }); installed.append(list);
    installed.append(node('p', '', `At verification: ${evidence.accountLoginAttempted === false ? 'account login not attempted' : evidence.accountLoginAttempted === true ? 'account login attempted' : 'account state unknown'} · ${evidence.VPNActivated === false ? 'VPN not activated' : evidence.VPNActivated === true ? 'VPN activation recorded' : 'VPN state unknown'}. Apps were force-stopped after verification; connection state can change separately.`));
    installed.append(node('p', '', 'WhatsApp has an unsupported-ROM warning. Figma Mirror is a companion app. Installation evidence does not establish account login or publishing compatibility.'));
  } else installed.append(node('p', '', 'No completed per-device package verification is available. Account connections and VPN state are unknown.'));
  body.append(installed);
  const social = node('div', 'detail-section'); social.append(node('h3', '', 'Social accounts')); const pills = node('div', 'social-pills'); b.social.forEach(s => pills.append(append(node('div', 'social-pill'), s.url ? link(s.name, s.url) : node('strong', '', s.name), node('span', '', s.status || 'Not connected')))); social.append(b.social.length ? pills : node('p', '', 'No social account connections have been reported. Account login and publishing integrations are configured separately.')); body.append(social);
  const backup = node('div', 'detail-section'); backup.append(node('h3', '', 'Source & recovery'), node('p', '', `${b.backup.status || 'Backup status unknown'} · ${date(b.backup.lastAt)}`)); if (b.backup.detail) backup.append(node('p', '', b.backup.detail)); if (b.backup.repositoryUrl) backup.append(link('Open source repository ↗', b.backup.repositoryUrl, 'button secondary')); body.append(backup);
  const ops = node('div', 'detail-section'); ops.append(node('h3', '', 'Recent operations'), operationsPanel(id)); body.append(ops); content.append(body); $('#detail-dialog').showModal();
}
function openAction(b, action) { selectedAction = { id: b.id, action }; $('#action-title').textContent = `${action[0].toUpperCase() + action.slice(1)} ${b.name}?`; const descriptions = { start: 'Start this environment using its connected lifecycle controller.', stop: 'The website and social device in this environment may become unavailable until restarted.', restart: 'The website and social device in this environment may briefly become unavailable.', clone: 'Create a separate environment. Social login sessions must remain separate.', archive: 'Request an archive through the controller. Review the resulting job to confirm completion.', remove: 'Request removal of this environment. Its controller must enforce backup and data-retention rules before deletion.' }; $('#action-description').textContent = descriptions[action]; const confirm = ['stop', 'restart', 'archive', 'remove'].includes(action); $('#confirm-label').textContent = `Type ${b.id} to confirm the affected environment`; $('#confirm-label').classList.toggle('hidden', !confirm); $('#confirm-input').classList.toggle('hidden', !confirm); $('#confirm-input').value = ''; $('#action-error').textContent = ''; $('#action-submit').className = `button ${action === 'remove' ? 'danger' : 'primary'}`; $('#detail-dialog').close(); $('#action-dialog').showModal(); }
$('#action-form').addEventListener('submit', async e => { e.preventDefault(); if (!selectedAction) return; const b = selectedAction, disruptive = ['stop', 'restart', 'archive', 'remove'].includes(b.action); if (disruptive && $('#confirm-input').value !== b.id) { $('#action-error').textContent = 'Enter the environment id exactly to confirm.'; return; } $('#action-submit').disabled = true; $('#action-error').textContent = ''; try { const r = await api(`/brands/${encodeURIComponent(b.id)}/actions/${b.action}`, 'POST', { confirm: disruptive ? b.id : undefined }); $('#action-dialog').close(); toast(r.message || 'Operation submitted.'); await refresh(); } catch (err) { $('#action-error').textContent = err.message; } finally { $('#action-submit').disabled = false; } });
$('#create-brand').addEventListener('click', () => { if (!inventory?.capabilities.create) return; const select = $('#brand-profile'); select.replaceChildren(); inventory.capabilities.profiles.forEach(p => { const o = node('option', '', `${p.name}${p.description ? ` — ${p.description}` : ''}`); o.value = p.id; select.append(o); }); $('#create-error').textContent = ''; $('#create-dialog').showModal(); });
$('#create-form').addEventListener('submit', async e => { e.preventDefault(); const submit = e.target.querySelector('button[type=submit]'); submit.disabled = true; $('#create-error').textContent = ''; try { const r = await api('/brands', 'POST', { name: $('#brand-name').value, domain: $('#brand-domain').value, profile: $('#brand-profile').value }); $('#create-dialog').close(); e.target.reset(); toast(r.message || 'Provisioning requested.'); await refresh(); } catch (err) { $('#create-error').textContent = err.message; } finally { submit.disabled = false; } });
all('.close-dialog').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
all('dialog').forEach(d => d.addEventListener('click', e => { if (e.target === d) { const rect = d.getBoundingClientRect(); if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) d.close(); } }));
(async () => { try { session = await api('/session'); showApp(); $('#content').append(node('div', 'loading', 'Loading your measured inventory…')); await refresh(); } catch { showLogin(); } })();
setInterval(() => { if (session && page !== 'mail' && !document.hidden && !all('dialog').some(d => d.open)) refresh(); }, 30000);
