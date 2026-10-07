import concurrent.futures
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile

spec = importlib.util.spec_from_file_location('broker', Path(__file__).parents[1] / 'broker.py')
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.clock = 100.0
        self.config = {'principal': 'fixture-owner', 'mailbox': broker.MAILBOX,
                       'password': 'synthetic-fixture-password', 'issuerKey': 'i' * 43, 'redeemerKey': 'r' * 43}
        self.store = broker.Grants(self.config, now=lambda: self.clock)

    def issue(self):
        return self.store.issue(self.config['issuerKey'], {'principal': 'fixture-owner', 'audience': broker.AUDIENCE})['grant']

    def redeem(self, grant, audience=broker.AUDIENCE):
        return self.store.redeem(self.config['redeemerKey'], {'grant': grant, 'audience': audience})

    def test_fixed_mailbox_and_single_use(self):
        grant = self.issue()
        result = self.redeem(grant)
        self.assertEqual(result['mailbox'], broker.MAILBOX)
        self.assertEqual(result['imapHost'], 'ssl://mail.example.com:993')
        with self.assertRaises(broker.Rejected):
            self.redeem(grant)

    def test_expiry_boundary(self):
        grant = self.issue()
        self.clock += 30
        with self.assertRaises(broker.Rejected):
            self.redeem(grant)

    def test_wrong_audience_does_not_consume_valid_grant(self):
        grant = self.issue()
        with self.assertRaises(broker.Rejected):
            self.redeem(grant, 'https://attacker.invalid')
        self.assertEqual(self.redeem(grant)['mailbox'], broker.MAILBOX)

    def test_role_separation_anonymous_and_field_allowlist(self):
        body = {'principal': 'fixture-owner', 'audience': broker.AUDIENCE}
        for key in ['', self.config['redeemerKey']]:
            with self.assertRaises(broker.Rejected):
                self.store.issue(key, body)
        for invalid in [{**body, 'mailbox': 'other@example.invalid'}, {**body, 'principal': 'other'}, {**body, 'audience': 'other'}]:
            with self.assertRaises(broker.Rejected):
                self.store.issue(self.config['issuerKey'], invalid)
        grant = self.issue()
        with self.assertRaises(broker.Rejected):
            self.store.redeem(self.config['issuerKey'], {'grant': grant, 'audience': broker.AUDIENCE})

    def test_atomic_redeem_under_concurrency(self):
        grant = self.issue()
        def attempt(_):
            try:
                self.redeem(grant)
                return 1
            except broker.Rejected:
                return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(16))), 1)

    def test_grant_capacity_and_expired_cleanup(self):
        for _ in range(128):
            self.issue()
        with self.assertRaises(broker.Rejected):
            self.issue()
        self.clock += 31
        self.issue()
        self.assertEqual(len(self.store.pending), 1)

    def test_configuration_cannot_expand_mailbox_or_share_keys(self):
        for override in [{'mailbox': 'other@example.invalid'}, {'redeemerKey': self.config['issuerKey']}, {'issuerKey': 'short'}]:
            with self.assertRaises(ValueError):
                broker.Grants({**self.config, **override})

    def test_status_requires_issuer_and_does_not_create_login_grants(self):
        result = self.store.status(self.config['issuerKey'], {'principal': 'fixture-owner', 'audience': broker.AUDIENCE})
        self.assertTrue(result['available'])
        self.assertNotIn('password', result)
        self.assertEqual(self.store.pending, {})
        with self.assertRaises(broker.Rejected):
            self.store.status('', {'principal': 'fixture-owner', 'audience': broker.AUDIENCE})

    def test_certificate_rotation_reloads_only_after_file_change(self):
        contexts = []
        class Context:
            def __init__(self, *_):
                contexts.append(self)
            def load_cert_chain(self, *_):
                pass
        with tempfile.TemporaryDirectory() as temp, patch.object(broker.ssl, 'SSLContext', Context):
            cert, key = Path(temp) / 'cert', Path(temp) / 'key'
            cert.write_text('fixture-cert'); key.write_text('fixture-key')
            tls = broker.RotatingTLS(cert, key)
            original = tls.current()
            self.assertEqual(len(contexts), 1)
            cert.write_text('renewed-fixture-cert')
            self.assertIsNot(tls.current(), original)
            self.assertEqual(len(contexts), 2)


if __name__ == '__main__':
    unittest.main()
