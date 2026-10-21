#!/usr/bin/python3 -I
"""Boot on a machine WITHOUT the M4 hardware (the VM case): no unit of m4-support may loop, wait for a device, or hold graphical.target back.
Rule: a hardware unit is gated on the J616s device tree (ConditionFirmware=device-tree-compatible(apple,j616s)) so it is skipped at once; nothing restarts for
missing hardware; every restart has a start limit; waits are bounded below the unit's timeout.   python3 -B -I test_nohardware_boot.py
The conditions are evaluated with `systemd-analyze condition` on this host (skip when it is not available or this host is a J616s)."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

TREES = Path(os.environ.get('M4_TREES', Path(__file__).resolve().parents[1]))
UNITS = TREES/'support/usr/lib/systemd/system'
UNGATED_BY_DESIGN = {      # run on every machine, and end in milliseconds
    'm4-boot-pending.service': 'writes one file, conditions on /boot/m4 and an enabled display manager',
    'm4-boot-ok.service': 'Type=simple, only while the pending mark exists',
    'm4-bt-keyshare.service': 'a file import, condition on the shared key directory',
}


def unit(path):
    sections, current = {}, None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            current = sections.setdefault(line.strip('[]'), [])
        elif current is not None and '=' in line:
            current.append(tuple(x.strip() for x in line.split('=', 1)))
    return sections


def values(u, section, key):
    return [v for k, v in u.get(section, []) if k == key]


class NoHardwareBoot(unittest.TestCase):
    def setUp(self):
        self.units = {p.name: unit(p) for p in sorted(UNITS.glob('*.service'))}

    def test_every_unit_is_gated_or_ends_at_once_by_design(self):
        for name, u in self.units.items():
            gated = any(v.startswith('device-tree-compatible(apple,j616s)') for v in values(u, 'Unit', 'ConditionFirmware'))
            self.assertTrue(gated or name in UNGATED_BY_DESIGN, '%s runs on a machine without the hardware and is not on the by-design list' % name)

    def test_restart_only_with_a_start_limit_and_never_for_missing_hardware(self):
        for name, u in self.units.items():
            if values(u, 'Service', 'Restart'):
                self.assertTrue(values(u, 'Unit', 'StartLimitIntervalSec') and values(u, 'Unit', 'StartLimitBurst'), name)
                self.assertTrue(any(v.startswith('device-tree-compatible') for v in values(u, 'Unit', 'ConditionFirmware')), name)
            if name not in UNGATED_BY_DESIGN:
                self.assertTrue(values(u, 'Unit', 'StartLimitIntervalSec'), name)

    def test_nothing_waits_for_a_device_inside_a_unit_that_holds_a_target(self):
        for name, u in self.units.items():
            for pre in values(u, 'Service', 'ExecStartPre'):
                self.assertNotRegex(pre, r'sleep|while |until ', name)      # no polling loops in unit lines
            timeout = values(u, 'Service', 'TimeoutStartSec')
            if timeout:
                self.assertLessEqual(int(timeout[0].rstrip('s')), 150, name)

    def test_no_watchdog_unit(self):
        self.assertNotIn('m4-watchdog.service', self.units)

    def test_the_gate_really_skips_on_a_host_without_the_j616s(self):
        if shutil.which('systemd-analyze') is None:
            self.skipTest('no systemd-analyze')
        try:
            compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes()
        except OSError:
            compatible = b''
        if b'apple,j616s' in compatible:
            self.skipTest('this host is a J616s')
        probe = subprocess.run(['systemd-analyze', 'condition', 'ConditionPathExists=/'], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest('systemd-analyze condition is not available: ' + probe.stderr.strip()[-80:])
        for name, u in self.units.items():
            if name in UNGATED_BY_DESIGN:
                continue
            conds = ['%s=%s' % (k, v) for k, v in u.get('Unit', []) if k.startswith('Condition')]
            skipped = False
            for c in conds:
                r = subprocess.run(['systemd-analyze', 'condition', c], capture_output=True, text=True)
                if r.returncode != 0:
                    skipped = True
            self.assertTrue(skipped, '%s would start on a host without the J616s' % name)


if __name__ == '__main__':
    unittest.main(verbosity=1)
