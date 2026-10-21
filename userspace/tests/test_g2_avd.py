#!/usr/bin/python3 -I
"""gaps G2, m4.17: `m4-test avd` tests a decoder that the device tree enables and apple-avd binds at boot. No attach shim,
no boot unit, no boot marker. The preflight reads a fake sysfs and device tree; the decode and watch are replaced.
python3 -B -I test_g2_avd.py"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

TREES = Path(__file__).resolve().parents[1]
TOOLS = TREES/'tools'
TEST = TOOLS/'usr/lib/m4/tests/avd/avd-test.py'


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeSystem:
    """A fake /proc/modules, /sys, /proc/device-tree, modprobe.d and /proc/cmdline under one temporary directory."""
    def __init__(self, m, root, loaded=True, bound='avd', video=True, media=True, dt_status=None, cmdline='root=x quiet'):
        self.root = Path(root)
        m.Host.proc_modules = self.root/'modules'
        m.Host.platform_devices = self.root/'platform'
        m.Host.video_root = self.root/'video4linux'
        m.Host.media_root = self.root/'media'
        m.Host.dt_root = self.root/'dt'
        m.Host.modprobe_dirs = (self.root/'etc-modprobe.d', self.root/'usr-modprobe.d')
        m.Host.cmdline_path = self.root/'cmdline'
        (self.root/'modules').write_text(('apple_avd 131072 0 - Live 0x0\n' if loaded else '') + 'videodev 212992 3 - Live 0x0\n')
        device = self.root/'platform'/m.AVD_DEVICE
        device.mkdir(parents=True)
        if bound:
            (self.root/'drivers'/bound).mkdir(parents=True)
            os.symlink(self.root/'drivers'/bound, device/'driver')
        (self.root/'video4linux').mkdir()
        if video:
            (self.root/'video4linux/video0').mkdir()
            (self.root/'video4linux/video0/name').write_text('avd\n')
        (self.root/'media').mkdir()
        if media:
            (self.root/'media/media0').mkdir()
        for rel, compatible in m.DT_NODES:
            d = self.root/'dt'/rel
            d.mkdir(parents=True)
            (d/'compatible').write_bytes(b'apple,t6040-x\0' + compatible.encode() + b'\0')
            if dt_status is not None:
                (d/'status').write_bytes(dt_status.encode() + b'\0')
        for d in m.Host.modprobe_dirs:
            d.mkdir()
        (self.root/'cmdline').write_text(cmdline + '\n')


class Preflight(unittest.TestCase):
    def setUp(self):
        self.m = load('avdtest_g2', TEST)
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def system(self, **kw):
        FakeSystem(self.m, self.dir.name, **kw)
        return self.m.Host()

    def refused(self, host):
        with self.assertRaises(self.m.Refused) as ctx:
            self.m.check_bound(host)
        return str(ctx.exception)

    def test_a_bound_decoder_with_its_nodes_passes(self):
        host = self.system()
        facts = self.m.check_bound(host)
        self.assertEqual(facts, dict(driver='avd', video_nodes=['video0'], media_nodes=['media0']))
        self.m.check_dt(self.m.dt_facts(host))

    def test_not_loaded_says_why_when_a_kill_switch_is_set(self):
        host = self.system(loaded=False, bound=None)
        self.assertIn('apple_avd is not loaded', self.refused(host))
        self.assertIn('loads at boot since m4.17', self.refused(host))
        (Path(self.dir.name)/'cmdline').write_text('root=x m4.safe=previous-boot-incomplete\n')
        self.assertIn('m4.safe (rescue) boot', self.refused(host))
        (Path(self.dir.name)/'cmdline').write_text('root=x modprobe.blacklist=foo,apple-avd\n')
        self.assertIn('modprobe.blacklist=foo,apple-avd', self.refused(host))
        (Path(self.dir.name)/'cmdline').write_text('root=x\n')
        (Path(self.dir.name)/'etc-modprobe.d/avd-off.conf').write_text('# off for a GPU test\nblacklist apple_avd\n')
        self.assertIn('avd-off.conf blacklists apple_avd', self.refused(host))

    def test_a_commented_blacklist_line_is_not_a_kill_switch(self):
        host = self.system(loaded=False, bound=None)
        (Path(self.dir.name)/'etc-modprobe.d/x.conf').write_text('# blacklist apple_avd\n')
        self.assertIsNone(self.m.kill_switch(host))

    def test_loaded_but_unbound_or_without_nodes_is_refused(self):
        self.assertIn('not bound to apple-avd (driver: None)', self.refused(self.system(bound=None)))
        self.dir.cleanup()
        self.dir = tempfile.TemporaryDirectory()
        self.assertIn('no V4L2 video node', self.refused(self.system(video=False)))
        self.dir.cleanup()
        self.dir = tempfile.TemporaryDirectory()
        self.assertIn('no media controller node', self.refused(self.system(media=False)))

    def test_a_disabled_node_means_an_old_tree(self):
        host = self.system(dt_status='disabled')
        with self.assertRaises(self.m.Refused) as ctx:
            self.m.check_dt(self.m.dt_facts(host))
        self.assertIn('predates the m4.17', str(ctx.exception))

    def test_okay_status_and_a_missing_generic_compatible(self):
        host = self.system(dt_status='okay')
        self.m.check_dt(self.m.dt_facts(host))
        facts = self.m.dt_facts(host)
        facts[self.m.DT_NODES[2][0]]['compatible'] = ['apple,t6041-avd']
        with self.assertRaises(self.m.Refused) as ctx:
            self.m.check_dt(facts)
        self.assertIn('without apple,t8132-avd', str(ctx.exception))

    def test_the_real_host_has_every_attribute_the_preflight_reads(self):
        host = self.m.Host()
        for name in ('proc_modules', 'platform_devices', 'video_root', 'media_root', 'dt_root', 'modprobe_dirs', 'cmdline_path'):
            self.assertTrue(hasattr(host, name), name)
        self.assertIn(Path('/etc/modprobe.d'), host.modprobe_dirs)


class Host:
    def __init__(self, parent):
        self.state_parent = Path(parent)
        self.lines, self.steps = [], []

    def out(self, line=''):
        self.lines.append(line)

    def boot_id(self):
        return '00000000-0000-0000-0000-000000000000'

    def utc(self):
        return '20261016T150000Z'

    def release(self):
        return '7.1.13-m4.17'

    def tainted(self):
        return 0

    def step(self, label, **info):
        self.steps.append(label)

    def dmesg(self):
        return []

    def run(self, argv, timeout=20, env=None):
        return 0, 'running\n', ''


class Archive:
    def __init__(self, host):
        self.directory, self.outbox_error, self.saved, self.opened = None, None, {}, False
        self.state = host.state_parent

    def save(self, name, value):
        self.saved[name] = value

    def open_outbox(self):
        self.opened = True


class Run(unittest.TestCase):
    def setUp(self):
        self.m = load('avdtest_g2run', TEST)
        self.dir = tempfile.TemporaryDirectory()
        self.host = Host(self.dir.name)
        self.decoded, self.watched = [], []
        m = self.m
        m.preflight = lambda host, decode: dict(taint_before=0, firmware=dict(sha256='f'*64),
                                                binding=dict(driver='avd', video_nodes=['video0'], media_nodes=['media0']))
        m.clip_facts = lambda host: (dict(frames=[{}]*3, width=1920, height=1088), b'clip')
        m.Archive = Archive

        def decode(host, ref, clip_data, steps, result):
            self.decoded.append(True)
            result['frames'] = 3
        m.stage_decode = decode

        def watch(host, say, before, seconds):
            self.watched.append(seconds)
            return seconds, []
        m.watch_log = watch

    def tearDown(self):
        self.dir.cleanup()

    def p(self, name):
        return self.host.state_parent/name

    def result(self):
        return json.loads(self.p(self.m.RESULT_NAME).read_text())

    def test_a_pass_decodes_watches_and_leaves_no_attempt_and_no_marker(self):
        self.assertEqual(self.m.run(self.host), 0)
        self.assertEqual((self.decoded, self.watched), ([True], [60]))
        self.assertTrue(self.result()['passed'])
        self.assertFalse(self.p(self.m.ATTEMPT_NAME).exists())
        self.assertEqual(sorted(p.name for p in self.host.state_parent.iterdir()), [self.m.RESULT_NAME])
        self.assertTrue(any(line.startswith('PASS: decoded 3 frames exactly') for line in self.host.lines))

    def test_no_decode_only_watches(self):
        self.assertEqual(self.m.run(self.host, decode=False, watch=5), 0)
        self.assertEqual((self.decoded, self.watched), ([], [5]))

    def test_a_decode_failure_fails_and_opens_the_outbox(self):
        def bad(host, ref, clip_data, steps, result):
            raise self.m.Verdict('frame(s) [1] differ from the reference')
        self.m.stage_decode = bad
        archive = []
        real = self.m.Archive

        def keep(host):
            a = real(host)
            archive.append(a)
            return a
        self.m.Archive = keep
        self.assertEqual(self.m.run(self.host), 2)
        self.assertFalse(self.result()['passed'])
        self.assertIn('differ from the reference', self.result()['reason'])
        self.assertTrue(archive[0].opened)
        self.assertFalse(self.p(self.m.ATTEMPT_NAME).exists())
        self.assertTrue(any('reboot before using it again' in line for line in self.host.lines))

    def test_an_error_line_in_the_watch_fails(self):
        self.m.watch_log = lambda host, say, before, seconds: (2.0, ['avd 281080000.avd: Frame processing timed out!'])
        self.assertEqual(self.m.run(self.host), 2)
        self.assertIn('timed out', self.result()['reason'])

    def failed_earlier(self):
        boot = self.host.state_parent/self.host.boot_id()/self.m.STATE_NAME
        boot.mkdir(parents=True)
        (boot/self.m.RESULT_NAME).write_text(json.dumps(dict(passed=False, reason='gst-launch failed')))

    def test_an_earlier_failure_in_this_boot_refuses_unless_again(self):
        self.failed_earlier()
        self.assertEqual(self.m.run(self.host), 1)
        self.assertEqual(self.decoded, [])
        self.assertTrue(any('earlier m4-test avd in this boot failed (gst-launch failed)' in l for l in self.host.lines))
        self.assertEqual(self.m.run(self.host, again=True), 0)

    def test_an_unfinished_attempt_is_reported_and_needs_again(self):
        self.p(self.m.ATTEMPT_NAME).write_text(json.dumps(dict(boot_id='b', utc='u')))
        self.m.last_steps = lambda host, boot_id, count=8: []
        self.assertEqual(self.m.run(self.host), 3)
        self.assertEqual(self.decoded, [])
        self.assertEqual(self.m.run(self.host, again=True), 0)
        self.assertTrue(list(self.host.state_parent.glob('avd-test-interrupted-*.json')))

    def test_a_preflight_refusal_touches_nothing(self):
        def refuse(host, decode):
            raise self.m.Refused('apple_avd is not loaded; /etc/modprobe.d/x.conf blacklists apple_avd (the kill switch)')
        self.m.preflight = refuse
        self.assertEqual(self.m.run(self.host), 1)
        self.assertEqual(list(self.host.state_parent.iterdir()), [])
        self.assertTrue(any('kill switch' in line for line in self.host.lines))


class CheckMode(unittest.TestCase):
    def test_check_reports_each_fact_and_refuses_on_a_failed_one(self):
        m = load('avdtest_g2check', TEST)
        with tempfile.TemporaryDirectory() as d:
            host = Host(d)
            m.check_bound = lambda host: dict(driver='avd', video_nodes=['video0'], media_nodes=['media0'])
            m.dt_facts = lambda host: {}
            m.check_dt = lambda facts: None
            m.firmware_facts = lambda host: dict(bytes=10, sha256='a'*64)
            m.clip_facts = lambda host: (dict(width=1920, height=1088, frames=[{}]), b'x')
            m.tool_facts = lambda host, what='v4l2slh264dec': dict(gst_v4l2slh264dec=True, gst_detail=['Factory Details:'])
            self.assertEqual(m.check_mode(host, host.out), 0)
            self.assertIn('check: m4-test avd would start', host.lines[-1])

            def unbound(host):
                raise m.Refused('apple_avd is not loaded')
            m.check_bound = unbound
            host.lines.clear()
            self.assertEqual(m.check_mode(host, host.out), 1)
            self.assertTrue(any(line.startswith('FAIL decoder driver') for line in host.lines))


class Interface(unittest.TestCase):
    def setUp(self):
        self.m = load('avdtest_g2if', TEST)

    def test_no_boot_path_marker_shim_or_codec_option_remains(self):
        for name in ('boot', 'write_boot_marker', 'disable_boot_start', 'avd_sources', 'stage_attach', 'load_pinned',
                     'wait_after_desktop', 'BOOT_MARKER', 'SOURCES_NAME', 'SHIM_NAME'):
            self.assertFalse(hasattr(self.m, name), name)
        text = TEST.read_text()
        for word in ('m4-avd.service', 'm4_avd_attach', 'avd-off', 'insmod', '--codecs'):
            self.assertNotIn(word, text, word)

    def test_arguments(self):
        class H(Host):
            def __init__(self):
                super().__init__('/nonexistent')

            def euid(self):
                return 0
        seen = []
        self.m.run = lambda host, **kw: seen.append(kw) or 0
        self.assertEqual(self.m.main([], H()), 0)
        self.assertEqual(seen[-1], dict(watch=60, decode=True, again=False, check=False))
        self.assertEqual(self.m.main(['--watch', '5', '--no-decode', '--again'], H()), 0)
        self.assertEqual(seen[-1], dict(watch=5, decode=False, again=True, check=False))
        for bad in (['boot'], ['--codecs', '0x7'], ['--watch', '4000']):
            self.assertEqual(self.m.main(bad, H()), 1, bad)

    def test_the_boot_unit_is_gone(self):
        self.assertFalse((TOOLS/'usr/lib/systemd/system/m4-avd.service').exists())
        self.assertFalse((TOOLS/'usr/lib/systemd/system/graphical.target.wants/m4-avd.service').exists())


if __name__ == '__main__':
    unittest.main()
