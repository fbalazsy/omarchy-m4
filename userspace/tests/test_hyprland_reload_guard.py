#!/usr/bin/python3 -I
"""The pacman hooks that pause Hyprland's config auto-reload while /usr/share/m4/hyprland.lua is replaced (pacman removes the file
first; a reload in that moment fails with "cannot open" and puts up the error bar).   python3 -B -I test_hyprland_reload_guard.py"""
import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

TREES = Path(__file__).resolve().parents[1]
SUPPORT = TREES/'support'
SCRIPT = SUPPORT/'usr/lib/m4/hyprland-reload-guard'
HOOKS = SUPPORT/'usr/share/libalpm/hooks'
PAUSE = HOOKS/'20-m4-hyprland-reload-pause.hook'
RESUME = HOOKS/'80-m4-hyprland-reload-resume.hook'


def load():
    loader = importlib.machinery.SourceFileLoader('hypr_guard', str(SCRIPT))
    spec = importlib.util.spec_from_loader('hypr_guard', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Guard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.m = load()
        self.m.RUN = Path(self.dir.name)/'run/user'
        self.m.STATE = Path(self.dir.name)/'run/m4/hyprland-reload-guard'
        self.calls = []
        self.disabled = {}

    def tearDown(self):
        self.dir.cleanup()

    def instance(self, uid, sig, disabled):
        d = self.m.RUN/str(uid)/'hypr'/sig
        d.mkdir(parents=True)
        (d/'.socket.sock').write_text('')
        self.disabled[sig] = disabled

    def guard(self, mode, fail=None, ignore_set=False):
        def run(argv, **kw):
            self.calls.append(argv)
            if fail:
                raise fail
            sig = next(a.split('=', 1)[1] for a in argv if a.startswith('HYPRLAND_INSTANCE_SIGNATURE='))
            if argv[-3:-1] == ['-j', 'getoption']:
                out = json.dumps({'option': argv[-1], 'bool': self.disabled[sig], 'set': True})
            else:
                if not ignore_set:
                    self.disabled[sig] = 'disable_autoreload = true' in argv[-1]
                out = 'ok'
            return types.SimpleNamespace(returncode=0, stdout=out)
        users = {1000: 'owner', 960: 'sddm'}
        return self.m.main(['hyprland-reload-guard', mode], run=run,
                           getpwuid=lambda uid: types.SimpleNamespace(pw_name=users[uid]))

    def records(self):
        return sorted(p.name for p in self.m.STATE.glob('*')) if self.m.STATE.is_dir() else []

    def test_pause_then_resume_restores_auto_reload_without_a_reload(self):
        self.instance(1000, 'abc_1', False)
        self.assertEqual(self.guard('pause'), 0)
        self.assertTrue(self.disabled['abc_1'])
        self.assertEqual(self.records(), ['abc_1'])
        first = self.calls[0]
        self.assertEqual(first[:5], ['/usr/bin/runuser', '-u', 'owner', '--', '/usr/bin/env'])
        self.assertIn('XDG_RUNTIME_DIR=%s' % (self.m.RUN/'1000'), first)
        self.assertIn('HYPRLAND_INSTANCE_SIGNATURE=abc_1', first)
        self.assertEqual(self.guard('resume'), 0)
        self.assertFalse(self.disabled['abc_1'])
        self.assertEqual(self.records(), [])
        self.assertNotIn('reload', [c[-1] for c in self.calls])

    def test_an_instance_already_paused_is_left_alone(self):
        # the user's own setting, or Omarchy's guard paused it first: resume must not turn auto-reload on
        self.instance(1000, 'abc_1', True)
        self.instance(960, 'grt_2', False)
        self.guard('pause')
        self.assertEqual(self.records(), ['grt_2'])
        self.guard('resume')
        self.assertEqual(self.disabled, {'abc_1': True, 'grt_2': False})

    def test_a_pause_that_did_not_take_is_not_recorded(self):
        self.instance(1000, 'abc_1', False)
        self.guard('pause', ignore_set=True)
        self.assertEqual(self.records(), [])

    def test_resume_skips_an_instance_that_has_gone(self):
        self.instance(1000, 'abc_1', False)
        self.guard('pause')
        (self.m.RUN/'1000/hypr/abc_1/.socket.sock').unlink()
        n = len(self.calls)
        self.assertEqual(self.guard('resume'), 0)
        self.assertEqual(self.calls[n:], [])
        self.assertEqual(self.records(), [])

    def test_no_running_hyprland_does_nothing(self):
        self.assertEqual(self.guard('pause'), 0)
        self.assertEqual(self.guard('resume'), 0)
        self.assertEqual(self.calls, [])

    def test_a_stuck_hyprctl_never_fails_the_transaction(self):
        self.instance(1000, 'abc_1', False)
        self.assertEqual(self.guard('pause', fail=subprocess.TimeoutExpired('hyprctl', 5)), 0)
        self.assertEqual(self.records(), [])


class Hooks(unittest.TestCase):
    def test_hooks_wrap_the_transaction_that_replaces_the_main_config(self):
        for hook, when, mode in ((PAUSE, 'PreTransaction', 'pause'), (RESUME, 'PostTransaction', 'resume')):
            lines = hook.read_text().splitlines()
            for line in ('Type = Path', 'Operation = Upgrade', 'Target = usr/share/m4/hyprland.lua', 'When = %s' % when,
                         'Exec = /usr/bin/python3 -IB /usr/lib/m4/hyprland-reload-guard %s' % mode):
                self.assertIn(line, lines)
        self.assertTrue((SUPPORT/'usr/share/m4/hyprland.lua').is_file())

    def test_pause_runs_after_omarchys_own_pause(self):
        # when both run, Omarchy must record the user's original value (ours then sees it paused and records nothing)
        self.assertGreater(PAUSE.name, '10-omarchy-hyprland-reload-pause.hook')

    def test_the_guard_never_reloads(self):
        code = [l for l in SCRIPT.read_text().split('"""')[2].splitlines() if not l.lstrip().startswith('#')]
        self.assertFalse([l for l in code if "'reload'" in l or '"reload"' in l])


if __name__ == '__main__':
    unittest.main()
