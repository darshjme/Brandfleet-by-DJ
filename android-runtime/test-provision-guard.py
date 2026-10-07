#!/usr/bin/python3
"""Exercise the real provisioner admission gate without touching runtime paths."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


class ProvisionAdmission(unittest.TestCase):
    def invoke(self, image=None, no_start=None):
        with tempfile.TemporaryDirectory(prefix='bf-admission-') as directory:
            base = Path(directory)
            marker = base / 'grep-called'
            # Passing the existing slot check stops the script before any writes,
            # image tools, or host-device checks. A rejection must precede it.
            grep = base / 'grep'
            grep.write_text('#!/bin/bash\nprintf called >> ' + shlex.quote(str(marker)) + '\nexit 0\n')
            grep.chmod(0o700)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith('BRANDFLEET_ANDROID_')}
            env['PATH'] = str(base)
            if image is not None:
                env['BRANDFLEET_ANDROID_IMAGE'] = image
            if no_start is not None:
                env['BRANDFLEET_ANDROID_NO_START'] = no_start
            result = subprocess.run(
                ['/bin/bash', str(Path(__file__).with_name('provision-android.sh')),
                 'bf-admission-test', '1', '1024', '1'],
                env=env, capture_output=True, text=True, timeout=10)
            return result, marker.exists()

    def test_nondefault_cannot_auto_start_or_reach_runtime_checks(self):
        for no_start in (None, '0'):
            with self.subTest(no_start=no_start):
                result, runtime_reached = self.invoke(
                    'docker.io/redroid/redroid@sha256:' + '1' * 64, no_start)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Unqualified Android image', result.stderr)
                self.assertFalse(runtime_reached)

    def test_nondefault_can_prepare_offline(self):
        result, runtime_reached = self.invoke(
            'docker.io/redroid/redroid@sha256:' + '1' * 64, '1')
        self.assertTrue(runtime_reached)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Binder/IP slot already reserved', result.stderr)

    def test_default_creation_path_remains_available(self):
        result, runtime_reached = self.invoke()
        self.assertTrue(runtime_reached)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Binder/IP slot already reserved', result.stderr)


if __name__ == '__main__':
    unittest.main()
