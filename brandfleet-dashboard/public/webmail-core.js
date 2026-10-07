export function submitWebmailGrant(raw, document) {
  if (!raw || raw.action !== 'https://mail.example.com/' || raw.expiresIn !== 30
      || raw.mailbox !== 'owner@profile-brand.example.com' || !raw.fields || Object.keys(raw.fields).sort().join(',') !== '_action,_brandfleet_grant,_task'
      || raw.fields._task !== 'login' || raw.fields._action !== 'login'
      || typeof raw.fields._brandfleet_grant !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(raw.fields._brandfleet_grant)) throw Error('The webmail login handoff is unavailable. Try again.');
  const form = document.createElement('form');
  form.method = 'POST'; form.action = raw.action; form.target = '_self'; form.hidden = true;
  for (const [name, value] of Object.entries(raw.fields)) {
    const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
  }
  document.body.append(form);
  // A navigation submission can be queued. Keep its form connected until the
  // browser leaves, then remove the one-use proof before any history caching.
  const cleanup = () => form.remove();
  document.defaultView?.addEventListener('pagehide', cleanup, { once: true });
  try { form.submit(); } catch (error) {
    document.defaultView?.removeEventListener('pagehide', cleanup);
    cleanup();
    throw error;
  }
}
