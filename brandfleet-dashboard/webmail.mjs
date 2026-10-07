import https from 'node:https';

export const MAIL_ORIGIN = 'https://mail.example.com';
export const FLEET_ORIGIN = 'https://fleet.example.com';
export const MAILBOX = 'owner@profile-brand.example.com';

function privateRequest(path, key, data) {
  return new Promise((resolve, reject) => {
    const content = JSON.stringify(data);
    const req = https.request({ host: '10.77.2.50', port: 8087, servername: 'mail.example.com', path, method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(content) } }, response => {
      let result = ''; response.setEncoding('utf8');
      response.on('data', chunk => { result += chunk; if (result.length > 4096) req.destroy(Error('Login response too large')); });
      response.on('end', () => { try { if (response.statusCode !== 200) throw Error('Login service unavailable'); resolve(JSON.parse(result)); } catch { reject(Error('Login service unavailable')); } });
    });
    const timeout = setTimeout(() => req.destroy(Error('Login service unavailable')), 8000);
    req.on('close', () => clearTimeout(timeout));
    req.on('error', () => reject(Error('Login service unavailable')));
    req.end(content);
  });
}

export const privateIssue = (key, data) => privateRequest('/issue', key, data);
export const privateStatus = (key, data) => privateRequest('/status', key, data);

export function createWebmail({ principal = '', issuerKey = '', issue = privateIssue, probe = privateStatus } = {}) {
  const ready = /^[A-Za-z0-9_.@-]{1,120}$/.test(principal) && typeof issuerKey === 'string' && issuerKey.length >= 40;
  return {
    async status(username) {
      let available = false;
      if (ready && username === principal) {
        try { const live = await probe(issuerKey, { principal: username, audience: MAIL_ORIGIN }); available = live.available === true && live.mailbox === MAILBOX && live.audience === MAIL_ORIGIN; } catch {}
      }
      return { available, mailbox: available ? MAILBOX : '', url: MAIL_ORIGIN };
    },
    async open(username, origin, body) {
      if (!ready || username !== principal) throw Object.assign(Error('One-click webmail is unavailable for this account.'), { status: 403 });
      if (origin !== FLEET_ORIGIN || !body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).length) throw Object.assign(Error('Invalid webmail login request.'), { status: 403 });
      const result = await issue(issuerKey, { principal: username, audience: MAIL_ORIGIN });
      if (!/^[A-Za-z0-9_-]{43}$/.test(result.grant) || result.expiresIn !== 30 || result.audience !== MAIL_ORIGIN) throw Error('Login service unavailable');
      return { action: `${MAIL_ORIGIN}/`, fields: { _task: 'login', _action: 'login', _brandfleet_grant: result.grant }, expiresIn: 30, mailbox: MAILBOX };
    },
  };
}
