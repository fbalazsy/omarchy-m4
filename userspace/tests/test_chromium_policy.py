#!/usr/bin/python3 -I
"""m4-chromium-policy: AcceleratedVideoDecoder off in Chromium-family flags files, once per file, as omarchy-mac's setup-user.
   python3 -B -I test_chromium_policy.py   (tree: ../support)"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'support/usr/lib/m4/chromium-policy'
START = ROOT/'support/usr/lib/m4/session-start'

loader = importlib.machinery.SourceFileLoader('chromium_policy', str(SCRIPT))
spec = importlib.util.spec_from_loader('chromium_policy', loader)
policy = importlib.util.module_from_spec(spec)
loader.exec_module(policy)

OMARCHY_FLAGS = ('--ozone-platform=wayland\n--enable-features=TouchpadOverscrollHistoryNavigation,AcceleratedVideoDecoder\n'
                 '--disable-smooth-scrolling')


class Flags(unittest.TestCase):
    def test_a_new_line_after_a_file_without_final_newline(self):
        out = policy.with_feature_off(OMARCHY_FLAGS)
        self.assertEqual(out, OMARCHY_FLAGS + '\n--disable-features=AcceleratedVideoDecoder\n')

    def test_joins_an_existing_list_and_keeps_comments(self):
        out = policy.with_feature_off('--disable-features=Foo,Bar # mine\n--x\n')
        self.assertEqual(out, '--disable-features=Foo,Bar,AcceleratedVideoDecoder # mine\n--x\n')

    def test_already_off_is_left_byte_for_byte(self):
        self.assertIsNone(policy.with_feature_off('--disable-features=AcceleratedVideoDecoder\n'))
        self.assertIsNone(policy.with_feature_off('--disable-features=A,AcceleratedVideoDecoder,B\n'))

    def test_a_feature_with_a_longer_name_does_not_count(self):
        out = policy.with_feature_off('--disable-features=AcceleratedVideoDecoderX\n')
        self.assertEqual(out, '--disable-features=AcceleratedVideoDecoderX,AcceleratedVideoDecoder\n')

    def test_an_empty_list(self):
        self.assertEqual(policy.with_feature_off('--disable-features=\n'), '--disable-features=AcceleratedVideoDecoder\n')


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home/'.config').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_once_per_file_then_the_owner_decides(self):
        flags = self.home/'.config/chromium-flags.conf'
        flags.write_text(OMARCHY_FLAGS)
        flags.chmod(0o600)
        policy.run(self.home, self.home/'.config')
        self.assertTrue(flags.read_text().endswith('--disable-features=AcceleratedVideoDecoder\n'))
        self.assertEqual(flags.stat().st_mode & 0o777, 0o600)
        state = json.loads((self.home/'.local/state/m4/chromium-policy.json').read_text())
        self.assertEqual(state['handled'], [str(flags)])
        flags.write_text(OMARCHY_FLAGS + '\n')          # the owner takes the line out again
        self.assertEqual(policy.run(self.home, self.home/'.config'), 'nothing to do')
        self.assertEqual(flags.read_text(), OMARCHY_FLAGS + '\n')

    def test_every_chromium_family_file_and_both_config_dirs(self):
        xdg = self.home/'xdg'
        xdg.mkdir()
        for p in (self.home/'.config/brave-flags.conf', xdg/'chromium-flags.conf'):
            p.write_text('--x\n')
        (self.home/'.config/code-flags.conf').write_text('--x\n')      # Electron apps are not browsers
        policy.run(self.home, xdg)
        self.assertIn('AcceleratedVideoDecoder', (self.home/'.config/brave-flags.conf').read_text())
        self.assertIn('AcceleratedVideoDecoder', (xdg/'chromium-flags.conf').read_text())
        self.assertEqual((self.home/'.config/code-flags.conf').read_text(), '--x\n')

    def test_no_flags_files_no_state(self):
        self.assertEqual(policy.run(self.home, self.home/'.config'), 'nothing to do')
        self.assertFalse((self.home/'.local/state/m4').exists())

    def test_root_is_refused(self):
        if os.geteuid() == 0:
            self.skipTest('runs as root')
        env = dict(os.environ, HOME='relative')
        r = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


class Session(unittest.TestCase):
    def test_session_start_runs_it_without_blocking(self):
        self.assertIn('/usr/lib/m4/chromium-policy || true', START.read_text())
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        pkgbuild = (ROOT.parent/'packages/m4-support/PKGBUILD').read_text()
        self.assertIn('chromium-policy', pkgbuild)


if __name__ == '__main__':
    unittest.main()
