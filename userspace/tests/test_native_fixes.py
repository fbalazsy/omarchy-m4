#!/usr/bin/python3 -I
"""One test per native bug of /reports/r53/native-return-1/README.md ("Our bugs found natively"), run against the shipped trees.

  python3 -I test_native_fixes.py            (trees: ../support, ../tools, ../migrate; override with M4_TREES=<dir with the three>)
Bugs 3, 4, 7 and 11 are fixed by removing the code that had them: the test is that it is not shipped."""
import errno
import importlib.machinery
import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path

TREES = Path(os.environ.get('M4_TREES', Path(__file__).resolve().parents[1]))
SUPPORT, TOOLS, MIGRATE = TREES/'support', TREES/'tools', TREES/'migrate'
TESTS = TOOLS/'usr/lib/m4/tests'


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    sys.path.insert(0, str(SUPPORT/'usr/lib/m4'))
    sys.path.insert(0, str(SUPPORT/'usr/lib/m4/aop'))
    loader.exec_module(module)
    return module


def shipped_text():
    """Every text file of the three trees except m4-migrate (it names the R-series in its removal list)."""
    for tree in (SUPPORT, TOOLS):
        for p in tree.rglob('*'):
            if p.is_file() and not p.is_symlink() and p.stat().st_size < 4_000_000:
                try:
                    yield p, p.read_text()
                except (UnicodeDecodeError, OSError):
                    continue


class AvdWatchSeesTheDecoderDevice(unittest.TestCase):
    """gaps G2 (2026-10-15): the decoder's own lines name its platform device 281080000.avd, not the node avd@280000000."""
    def test_decoder_timeouts_are_errors_and_normal_lines_are_not(self):
        m = load('avdtest_g2', TESTS/'avd/avd-test.py')
        bad = lambda l: bool(m.WATCH_GLOBAL.search(l) or (m.WATCH_DEVICE.search(l) and m.WATCH_FAULT.search(l)))
        for line in ('avd 281080000.avd: Frame processing timed out! Vp: 0 (00)', 'avd 281080000.avd: failed to reset: -16',
                     'avd 281080000.avd: reset/boot failed: -16'):
            self.assertTrue(bad(line), line)
        for line in ('avd 281080000.avd: booting hw version: 40010', 'platform 281080000.avd: Adding to iommu group 6'):
            self.assertFalse(bad(line), line)


class Bug02AvdProbeOrder(unittest.TestCase):
    def test_media_bus_path_and_element_check_after_probe(self):
        text = (TESTS/'avd/avd-test.py').read_text()
        self.assertIn("/sys/bus/media/devices", text)
        self.assertNotIn("/sys/class/media", text)
        m = load('avdtest', TESTS/'avd/avd-test.py')
        seen = []

        class Host:
            def run(self, argv, timeout=0, env=None):
                seen.append(argv)
                return 0, 'Plugin Details', ''
        row = m.tool_facts(Host(), 'v4l2codecs')
        self.assertTrue(row['gst_v4l2slh264dec'])
        self.assertEqual(seen[0][-1], 'v4l2codecs')
        # m4.17: the decoder is bound at boot, so the preflight and the decode stage require the element itself
        self.assertIn("raise Verdict('GStreamer registered no v4l2slh264dec for the bound decoder", text)
        self.assertEqual(text.count("tool_facts(host, 'v4l2codecs')"), 0)


class Bug03And04And07And11RemovedCode(unittest.TestCase):
    def test_overlay_guard_kernel_tool_and_flag_reads_are_not_shipped(self):
        patterns = {'dev overlay': r'DEV-OVERLAY|m4-dev-overlay|m4\.devoverlay', 'opt-in guard': r'optin_guard|OPT_IN_FLAGS|m4-optin-guard',
                    'm4-kernel': r'\bm4-kernel\b|M4-Kernel/src', 'boot flag reads': r"/etc/m4-(?:gpu|display|bluetooth|aop|idle|kbd|sensors)[a-z-]*"}
        for label, pattern in patterns.items():
            hits = [str(p) for p, text in shipped_text() if re.search(pattern, text)]
            self.assertEqual(hits, [], label)

    def test_no_boot_time_flag_directory_is_read(self):
        # the only /etc/m4/ files read are the opt-outs that are plain ConditionPathExists / script checks
        allowed = {'aop-off', 'kbd-idle-off', 'idle-off', 'sensors-off', 'als-brightness.conf', 'bluetooth.conf', 'mkinitcpio.conf', 'cmdline',
                   'greeter-kb-layout',
                   'cpufreq.conf',                     # m4-cpufreq.service's optional caps (gaps G1)
                   'gpu-idle.conf',                     # gpu-idle-power's optional AC/battery idle hold (G-13)
                   'speakers'}                          # the owner's local FIRs for j616-graph-macos.json (never packaged)
        found = set()
        for p, text in shipped_text():
            found |= set(re.findall(r'/etc/m4/([A-Za-z0-9._-]+)', text))
        self.assertLessEqual(found, allowed | {'als-brightness.conf'}, sorted(found - allowed))


class Bug05AlsWait(unittest.TestCase):
    def test_waits_longer_than_the_sensor_needs(self):
        m = load('als', SUPPORT/'usr/lib/m4/als-brightness.py')
        # the sensor appears about 131 s after the display manager ; the old 120 s limit never saw it
        self.assertGreaterEqual(m.load_config(Path('/nonexistent'))['wait_device_s'], 600)


class Bug06DisplayPacingResult(unittest.TestCase):
    def test_result_is_kept_outside_run(self):
        text = (TESTS/'dcp/display-pacing.py').read_text()
        self.assertIn("/var/lib/m4/results", text)
        self.assertIn("display-pacing-%s-%s.json", text)
        compile(text, 'display-pacing.py', 'exec')


class Bug08UsbUnload(unittest.TestCase):
    def test_the_usb_test_loads_and_unloads_nothing(self):
        # native fix 8 (the attach stages left their modules loaded after a failed node) cannot recur: since the USB boot-time
        # start the usb2 test only reads a stack the kernel bound at boot
        self.assertFalse((TESTS/'usb/attach.py').exists())
        code = (TESTS/'usb/usb-test.py').read_text()
        self.assertNotRegex(code, r"'/usr/bin/(insmod|rmmod|modprobe)'")
        self.assertNotIn('memfd_create', code)


class Bug10CompatLockView(unittest.TestCase):
    def test_compat_package_pins_no_lockview(self):
        root = Path('/reports/n1/packaging/pkgbuilds/m4-omarchy-qt612-compat')
        if not root.is_dir():
            self.skipTest('lane S package sources not mounted')
        text = ''.join(p.read_text() for p in root.rglob('*') if p.is_file())
        self.assertNotIn('LockView', text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
