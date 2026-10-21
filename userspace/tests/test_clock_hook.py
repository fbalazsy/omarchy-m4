#!/usr/bin/python3 -I
"""m4-clock initramfs hook: seconds from the PMU nvmem counter (ADT spmi-abbeyL1: info-rtc 0x2002, info-clock_offset 0x2100, 48-bit LE, 32768 Hz).
Runs the hook's own function under busybox ash when mkinitcpio-busybox is there (else /bin/sh).   python3 -B -I test_clock_hook.py"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TREES = Path(os.environ.get('M4_TREES', Path(__file__).resolve().parents[1]))
HOOK = TREES/'support/usr/lib/initcpio/hooks/m4-clock'
SHELL = ['/usr/lib/initcpio/busybox', 'ash'] if Path('/usr/lib/initcpio/busybox').exists() else ['/bin/sh']


def nvmem(counter, offset):
    buf = bytearray(0x2200)
    buf[0x2002:0x2008] = counter.to_bytes(6, 'little')
    buf[0x2100:0x2106] = (offset % (1 << 48)).to_bytes(6, 'little')
    return bytes(buf)


def setUpModule():
    # the hook reads the counter with dd + xxd (both are busybox applets in the initramfs); a build host without xxd and busybox cannot run it
    if SHELL == ['/bin/sh'] and shutil.which('xxd') is None:
        raise unittest.SkipTest('neither mkinitcpio-busybox nor xxd is installed here')


class ClockHook(unittest.TestCase):
    def seconds(self, data):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/'nvmem'
            f.write_bytes(data)
            script = 'msg() { echo "$1"; }\n. %s\nm4_clock_seconds %s && echo "seconds=$m4_s"\n' % (HOOK, f)
            p = subprocess.run(SHELL + ['-c', script], capture_output=True, text=True, timeout=30)
            return p.returncode, p.stdout.strip()

    def test_counter_and_negative_offset_give_the_wall_clock(self):
        t, off = 1790000000, -1000                      # 2026-09; the offset is a signed 48-bit value
        rc, out = self.seconds(nvmem((t << 16) - 2 * off, off))
        self.assertIn('seconds=%d' % t, out, (rc, out))

    def test_positive_offset(self):
        t, off = 1800000000, 12345
        rc, out = self.seconds(nvmem((t << 16) - 2 * off, off))
        self.assertIn('seconds=%d' % t, out, (rc, out))

    def run_hook(self, data):
        """run_hook() with the nvmem glob pointed at a synthetic file and `date` recording what it would set."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/'nvmem'
            f.write_bytes(data)
            script = ('msg() { echo "MSG $1"; }\ndate() { echo "DATE $*"; return 0; }\nsleep() { :; }\n. %s\nm4_clock_glob=%s\nrun_hook\n' % (HOOK, f))
            return subprocess.run(SHELL + ['-c', script], capture_output=True, text=True, timeout=30).stdout

    def test_the_hook_sets_the_clock_only_for_a_plausible_time(self):
        t = 1790000000
        out = self.run_hook(nvmem(t << 16, 0))
        self.assertIn('clock set from the PMU (%d)' % t, out)
        for bad in (1000, 4_200_000_000):             # before 2025 / after 2100
            out = self.run_hook(nvmem(bad << 16, 0))
            self.assertNotIn('clock set', out, bad)
            self.assertIn('implausible', out)

    def test_no_nvmem_device_leaves_the_clock_alone(self):
        script = 'msg() { echo "MSG $1"; }\ndate() { echo DATE; }\nsleep() { :; }\n. %s\nm4_clock_glob=/nonexistent/nvmem\nrun_hook\n' % HOOK
        out = subprocess.run(SHELL + ['-c', script], capture_output=True, text=True, timeout=30).stdout
        self.assertNotIn('DATE', out)
        self.assertIn('no PMU nvmem device', out)


if __name__ == '__main__':
    unittest.main(verbosity=1)
