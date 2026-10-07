#!/usr/bin/python3
"""Fixed-mailbox, short-lived Roundcube login grants. No request logging."""
import argparse
import hashlib
import hmac
import http.server
import json
from pathlib import Path
import re
import secrets
import ssl
import stat
import threading
import time

AUDIENCE = 'https://mail.example.com'
MAILBOX = 'owner@profile-brand.example.com'
GRANT = re.compile(r'^[A-Za-z0-9_-]{43}$')


class Rejected(Exception):
    def __init__(self, status=403):
        self.status = status


class Grants:
    def __init__(self, config, now=time.monotonic, token=lambda: secrets.token_urlsafe(32)):
        required = {'principal', 'mailbox', 'password', 'issuerKey', 'redeemerKey'}
        if set(config) != required or config['mailbox'] != MAILBOX:
            raise ValueError('Invalid fixed-mailbox configuration')
        if not all(isinstance(v, str) and v for v in config.values()):
            raise ValueError('Missing private configuration')
        if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,120}', config['principal']):
            raise ValueError('Invalid principal')
        if any(len(config[k]) < 40 for k in ['issuerKey', 'redeemerKey']):
            raise ValueError('Private service keys must be random and independent')
        if hmac.compare_digest(config['issuerKey'], config['redeemerKey']):
            raise ValueError('Service keys must differ')
        self.config, self.now, self.token = config, now, token
        self.pending, self.lock = {}, threading.Lock()

    def authorize(self, key, role):
        if not isinstance(key, str) or not hmac.compare_digest(key, self.config[role + 'Key']):
            raise Rejected(403)

    def issue(self, key, body):
        self.authorize(key, 'issuer')
        if (not isinstance(body, dict) or set(body) != {'principal', 'audience'}
                or body['principal'] != self.config['principal'] or body['audience'] != AUDIENCE):
            raise Rejected(403)
        with self.lock:
            now = self.now()
            self.pending = {k: v for k, v in self.pending.items() if v > now}
            if len(self.pending) >= 128:
                raise Rejected(429)
            grant = self.token()
            if not GRANT.fullmatch(grant):
                raise ValueError('Invalid generated grant')
            self.pending[hashlib.sha256(grant.encode()).digest()] = now + 30
        return {'grant': grant, 'expiresIn': 30, 'audience': AUDIENCE}

    def redeem(self, key, body):
        self.authorize(key, 'redeemer')
        if (not isinstance(body, dict) or set(body) != {'grant', 'audience'}
                or body['audience'] != AUDIENCE or not isinstance(body['grant'], str)
                or not GRANT.fullmatch(body['grant'])):
            raise Rejected(403)
        digest = hashlib.sha256(body['grant'].encode()).digest()
        with self.lock:
            expires = self.pending.pop(digest, 0)  # Consume atomically before any IMAP operation.
        if expires <= self.now():
            raise Rejected(410)
        return {'mailbox': MAILBOX, 'password': self.config['password'], 'imapHost': 'ssl://mail.example.com:993'}

    def status(self, key, body):
        self.authorize(key, 'issuer')
        if not isinstance(body, dict) or body != {'principal': self.config['principal'], 'audience': AUDIENCE}:
            raise Rejected(403)
        return {'available': True, 'mailbox': MAILBOX, 'audience': AUDIENCE}


def handler(store):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, value):
            data = json.dumps(value, separators=(',', ':')).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.send(404, {'error': 'Unavailable'})

        def do_POST(self):
            try:
                if self.path not in ['/issue', '/redeem', '/status']:
                    raise Rejected(404)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048 or self.headers.get('Content-Type') != 'application/json':
                    raise Rejected(400)
                auth = self.headers.get('Authorization', '')
                if not auth.startswith('Bearer '):
                    raise Rejected(403)
                body = json.loads(self.rfile.read(length))
                operation = {'/issue': store.issue, '/redeem': store.redeem, '/status': store.status}[self.path]
                result = operation(auth[7:], body)
                self.send(200, result)
            except Rejected as error:
                self.send(error.status, {'error': 'Login grant unavailable'})
            except (ValueError, TypeError, json.JSONDecodeError):
                self.send(400, {'error': 'Invalid request'})
            except Exception:
                self.send(503, {'error': 'Login service unavailable'})
    return Handler


def read_private(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Private configuration must be root-owned0600')
    return json.loads(path.read_text())


class RotatingTLS:
    """Accept independently renewed certificates without a broker restart."""
    def __init__(self, cert, key):
        self.cert, self.key, self.stamp, self.context = cert, key, None, None
        self.lock = threading.Lock()
        self.current()  # Refuse startup with missing/invalid certificate or key.

    def current(self):
        stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size, p.stat().st_ino) for p in (self.cert, self.key))
        with self.lock:
            if stamp != self.stamp:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(self.cert, self.key)
                self.context, self.stamp = context, stamp
            return self.context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--cert', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True)
    args = parser.parse_args()
    store = Grants(read_private(args.config))
    tls = RotatingTLS(args.cert, args.key)
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            sock, addr = super().get_request()
            sock.settimeout(8)
            try:
                return tls.current().wrap_socket(sock, server_side=True), addr
            except Exception:
                sock.close()
                raise
    server = Server(('10.77.2.50', 8087), handler(store))
    server.serve_forever()


if __name__ == '__main__':
    main()
