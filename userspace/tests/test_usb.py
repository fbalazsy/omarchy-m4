#!/usr/bin/python3 -I
"""USB boot-time start: `m4-test usb2` tests a right-hand USB-C port that the device tree enables and the kernel binds at boot
(no attach shim, no m4_admit gate, no stage markers). The preflight reads a fake sysfs, device tree and modprobe.d; plugs,
unplugs and replugs are simulated on the prompts; mount and the kernel log are replaced.   python3 -B -I test_usb.py"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

TREES = Path(__file__).resolve().parents[1]
TEST = TREES/'tools/usr/lib/m4/tests/usb/usb-test.py'
M4_TEST = TREES/'tools/usr/bin/m4-test'
BOOT = '00000000-0000-0000-0000-000000000002'


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeSystem:
    """A fake /proc/modules, /sys, /proc/device-tree, modprobe.d, /proc/cmdline and mount table under one directory. Every
    sysfs entry links into a fake /sys/devices tree, as the real one does: USB buses under their controller, typec ports
    under their PD controller."""
    SPMI_BUS = {'right': 0, 'left-front': 1, 'left-back': 2}   # bus numbers follow probe order: one per port here

    def __init__(self, m, root, bound=True, dt_status=None, role_switch_prop=True, cmdline='root=x quiet', stick=False,
                 ports=('right',)):
        self.m, self.root = m, Path(root)
        r = self.root
        for d in ('bus/platform/devices', 'bus/spmi/devices', 'drivers', 'dt', 'typec', 'usb_role', 'usb-devices', 'block',
                  'devices', 'etc-modprobe.d', 'usr-modprobe.d', 'state', 'outbox'):
            (r/d).mkdir(parents=True, exist_ok=True)
        (r/'modules').write_text(''.join('%s 49152 0 - Live 0x0\n' % name for name in m.MODULES) if bound else
                                 'usbcore 212992 3 - Live 0x0\n')
        for driver in ('phy-apple-t6040-usb2', 'dwc3-apple', 'sn201202x'):
            (r/'drivers'/driver).mkdir()
        for port in ports:
            self.add_port(port, bound, dt_status, role_switch_prop)
        (r/'cmdline').write_text(cmdline + '\n')
        (r/'mounts').write_text('/dev/nvme0n1p4 / ext4 rw 0 0\n')
        if stick:
            self.plug()

    def pd_name(self, port):
        return '%d-%s' % (self.SPMI_BUS[port], self.m.PORTS[port]['pd'][-1:].rjust(2, '0'))

    def add_port(self, port, bound, dt_status, role_switch_prop):
        m, r = self.m, self.root
        p = m.PORTS[port]
        for module, bus, name, driver in m.drivers(port):
            if bus == 'spmi':
                dev = r/'devices/platform'/p['spmi']/('spmi-%d' % self.SPMI_BUS[port])/self.pd_name(port)
                dev.mkdir(parents=True)
                os.symlink('../../../../../firmware/devicetree/base/' + name, dev/'of_node')
                os.symlink(dev, r/'bus/spmi/devices'/self.pd_name(port))
            else:
                dev = r/'devices/platform'/name
                dev.mkdir(parents=True, exist_ok=True)
                os.symlink(dev, r/'bus/platform/devices'/name)
            if bound:
                os.symlink(r/'drivers'/driver, dev/'driver')
        for rel, compatible in p['dt']:
            d = r/'dt'/rel
            d.mkdir(parents=True, exist_ok=True)
            (d/'compatible').write_bytes(b'apple,t6040-x\0' + compatible.encode() + b'\0')
            if dt_status is not None:
                (d/'status').write_bytes(dt_status.encode() + b'\0')
        if role_switch_prop:
            (r/'dt'/m.dwc3_node(port)/m.ROLE_SWITCH_PROP).write_bytes(b'')
        if bound:
            typec = r/'devices/platform'/p['spmi']/('spmi-%d' % self.SPMI_BUS[port])/self.pd_name(port)/'typec'/self.typec(port)
            typec.mkdir(parents=True)
            (typec/'data_role').write_text('[host] device\n')
            os.symlink(typec, r/'typec'/self.typec(port))
            (r/'usb_role'/m.role_switch(port)).mkdir()

    def typec(self, port):
        return 'port%d' % self.SPMI_BUS[port]

    def controller(self, port):
        return self.root/'devices/platform'/self.m.PORTS[port]['dwc3']/('xhci-hcd.%d.auto' % self.SPMI_BUS[port])

    def bus(self, port):
        return 1 + 2*self.SPMI_BUS[port]

    def plug(self, port='right'):
        r, b = self.root, self.bus(port)
        (r/'typec'/(self.typec(port)+'-partner')).mkdir(exist_ok=True)
        for name in ('usb%d' % b, 'usb%d' % (b+1)):
            d = self.controller(port)/name
            d.mkdir(parents=True, exist_ok=True)
            if not (r/'usb-devices'/name).is_symlink():
                os.symlink(d, r/'usb-devices'/name)
        dev = self.controller(port)/('usb%d' % b)/('%d-1' % b)
        dev.mkdir(exist_ok=True)
        (dev/'speed').write_text('480\n')
        if not (r/'usb-devices'/dev.name).is_symlink():
            os.symlink(dev, r/'usb-devices'/dev.name)
        target = dev/('%d-1:1.0' % b)/'host0/target0:0:0/0:0:0:0'
        target.mkdir(parents=True, exist_ok=True)
        block = 'sd' + 'abc'[self.SPMI_BUS[port]]
        (r/'block'/block).mkdir(exist_ok=True)
        (r/'block'/block/(block+'1')).mkdir(exist_ok=True)
        if not (r/'block'/block/'device').is_symlink():
            os.symlink(target, r/'block'/block/'device')

    def unplug(self, keep_roots=False, port='right'):
        r, b = self.root, self.bus(port)
        shutil.rmtree(r/'typec'/(self.typec(port)+'-partner'), ignore_errors=True)
        shutil.rmtree(r/'block'/('sd' + 'abc'[self.SPMI_BUS[port]]), ignore_errors=True)
        names = ['%d-1' % b] + ([] if keep_roots else ['usb%d' % b, 'usb%d' % (b+1)])
        for name in names:
            (r/'usb-devices'/name).unlink(missing_ok=True)
        shutil.rmtree(self.controller(port)/('usb%d' % b)/('%d-1' % b), ignore_errors=True)
        if not keep_roots:
            shutil.rmtree(self.controller(port), ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.m = m = load('usbtest_%s' % self._testMethodName, TEST)
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

        def state_dir(host):            # the real one also checks that the state path is root's
            path = host.state_parent/host.boot_id()/m.STATE_NAME
            path.mkdir(parents=True, exist_ok=True)
            return path
        m.state_dir = state_dir

    def tearDown(self):
        self.dir.cleanup()

    def system(self, **kw):
        m, r = self.m, self.root
        self.fake = FakeSystem(m, r, **kw)
        test = self

        class Host(m.Host):
            proc_modules = r/'modules'
            bus_root = r/'bus'
            dt_root = r/'dt'
            typec_root = r/'typec'
            usb_role_root = r/'usb_role'
            usb_root = r/'usb-devices'
            block_root = r/'block'
            mounts_path = r/'mounts'
            modprobe_dirs = (r/'etc-modprobe.d', r/'usr-modprobe.d')
            cmdline_path = r/'cmdline'
            state_parent = r/'state'
            outbox = r/'outbox'
            owner_uid = os.getuid()

            def __init__(self):
                self.lines, self.steps, self.mounts, self.released = [], [], [], []
                self.kmsg = ['<6>[    1.000000] Booting Linux']
                self.auto = []
                self.events = {}        # step label -> action run at the first sleep after that step
                self.pending = []
                self.slept = 0.0

            def euid(self):
                return 0

            def boot_id(self):
                return BOOT

            def release(self):
                return '7.1.13-m4.19'

            def utc(self):
                return '20261016T190000Z'

            def monotonic(self):
                return 1000.0 + self.slept

            def sleep(self, seconds):
                self.slept += seconds
                while self.pending:
                    self.pending.pop(0)()

            def out(self, text=''):
                self.lines.append(text)

            def chown(self, path):
                pass

            def run(self, argv, timeout=20):
                return 0, '', ''

            def step(self, label, **info):
                self.steps.append(label)
                if label in self.events:
                    self.pending.append(self.events.pop(label))

            def dmesg(self):
                return list(self.kmsg)

            def tainted(self):
                return 0

            def automounters(self):
                return list(self.auto)

            def mount_ro(self, device, target):
                self.mounts.append(device)
                return 0, ''

            def umount(self, target):
                return 0, ''

            def release_automount(self, device, target):
                self.released.append((device, target))
                (test.root/'mounts').write_text('/dev/nvme0n1p4 / ext4 rw 0 0\n')
                return 0, ''

            def list_dir(self, path):
                return 3
        return Host()

    def phy_start(self, host, at):
        host.kmsg.append('<6>[%12.6f] phy-apple-t6040-usb2 392a90000.phy: USB2 host sequence complete' % at)

    def result(self):
        return json.loads((self.root/'state'/BOOT/'usb'/(self.m.RESULT_NAME % 'right')).read_text())

    def verdict(self, host):
        line = [x for x in host.lines if x.startswith('M4_USB_PASS') or x.startswith('M4_USB_FAIL')][-1]
        return line.split(' ', 1)[0], json.loads(line.split(' ', 1)[1])


class Preflight(Base):
    def test_a_bound_stack_on_an_enabled_tree_passes(self):
        host = self.system()
        facts = self.m.preflight(host, 'right')
        self.assertEqual(facts['unbound'], [])
        self.assertEqual(facts['port']['name'], 'port0')
        self.assertTrue(facts['role_switch'])
        self.assertEqual(facts['binding']['sn201202x'], dict(loaded=True, device='0-0c', driver='sn201202x'))

    def test_a_disabled_node_means_an_old_tree(self):
        host = self.system(dt_status='disabled')
        with self.assertRaises(self.m.Refused) as ctx:
            self.m.preflight(host, 'right')
        self.assertIn('predates the USB boot-time start', str(ctx.exception))

    def test_okay_status_is_enabled_and_the_role_switch_is_required(self):
        self.m.preflight(self.system(dt_status='okay'), 'right')
        self.dir.cleanup()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        with self.assertRaises(self.m.Refused) as ctx:
            self.m.preflight(self.system(role_switch_prop=False), 'right')
        self.assertIn('no usb-role-switch', str(ctx.exception))

    def test_not_bound_with_a_kill_switch_is_refused_with_the_reason(self):
        host = self.system(bound=False)
        (self.root/'cmdline').write_text('root=x m4.safe=previous-boot-incomplete\n')
        with self.assertRaises(self.m.Refused) as ctx:
            self.m.preflight(host, 'right')
        self.assertIn('m4.safe (rescue) boot', str(ctx.exception))
        (self.root/'cmdline').write_text('root=x modprobe.blacklist=foo,dwc3-apple\n')
        self.assertIn('modprobe.blacklist=foo,dwc3-apple', self.m.kill_switch(host))
        (self.root/'cmdline').write_text('root=x\n')
        (self.root/'etc-modprobe.d/usb-off.conf').write_text('# off for a test\nblacklist dwc3_apple\n')
        self.assertIn('usb-off.conf blacklists dwc3_apple', self.m.kill_switch(host))

    def test_a_commented_blacklist_line_is_not_a_kill_switch(self):
        host = self.system()
        (self.root/'etc-modprobe.d/x.conf').write_text('# blacklist dwc3_apple\nblacklist apple_wdt\n')
        self.assertIsNone(self.m.kill_switch(host))

    def test_not_bound_without_a_kill_switch_is_a_failed_boot_time_start(self):
        host = self.system(bound=False)
        self.assertEqual(self.m.run(host, watch=0), 2)
        kind, line = self.verdict(host)
        self.assertEqual((kind, line['classification']), ('M4_USB_FAIL', 'stack_not_bound'))
        self.assertIn('phy_apple_t6040_usb2 (not loaded)', ' '.join(host.lines))
        self.assertTrue(any(p.name.startswith('m4-usb-') for p in (self.root/'outbox').iterdir()))
        self.assertNotIn('wait-partner', host.steps)


class Runs(Base):
    def test_a_stick_in_at_boot_then_hotplug_passes(self):
        host = self.system(stick=True)
        self.phy_start(host, 4.2)
        host.events = {'wait-unplug': self.fake.unplug, 'wait-replug': self.fake.plug}
        self.assertEqual(self.m.run(host, watch=0), 0)
        kind, line = self.verdict(host)
        self.assertEqual((kind, line['classification']), ('M4_USB_PASS', 'passed'))
        self.assertEqual(line['stick']['at_start'], True)
        self.assertEqual(line['stick']['read_by'], 'test')
        self.assertEqual(host.mounts, ['/dev/sda1'])
        self.assertEqual(line['hotplug']['reenumerated'], True)
        self.assertEqual(line['port'], 'right')
        self.assertEqual(host.steps[:2], ['m4-test usb2 begin', 'mount-ro'])
        self.assertNotIn('wait-partner', host.steps)
        result = self.result()
        self.assertEqual(result['boot_log']['phy_starts'], 1)
        self.assertEqual(result['boot_log']['first_start_s'], 4.2)
        self.assertFalse((self.root/'state'/self.m.ATTEMPT_NAME).exists())
        self.assertEqual(list((self.root/'outbox').iterdir()), [])

    def test_a_stick_plugged_in_when_asked(self):
        host = self.system()
        host.events = {'wait-partner': self.fake.plug, 'wait-unplug': self.fake.unplug, 'wait-replug': self.fake.plug}
        self.assertEqual(self.m.run(host, watch=0), 0)
        kind, line = self.verdict(host)
        self.assertEqual(line['classification'], 'passed')
        self.assertFalse(line['stick']['at_start'])
        self.assertLess(host.steps.index('wait-partner'), host.steps.index('mount-ro'))

    def test_no_stick_is_a_pass_of_the_binding_only(self):
        host = self.system()
        self.assertEqual(self.m.run(host, watch=0), 0)
        self.assertEqual(self.verdict(host)[1]['classification'], 'passed_bound_no_stick')

    def test_no_hotplug_option(self):
        host = self.system(stick=True)
        self.assertEqual(self.m.run(host, watch=0, hotplug=False), 0)
        self.assertEqual(self.verdict(host)[1]['classification'], 'passed_no_hotplug')
        self.assertNotIn('wait-unplug', host.steps)

    def test_an_automounted_stick_is_listed_and_released_before_the_unplug(self):
        host = self.system(stick=True)
        host.auto = [4242]
        (self.root/'mounts').write_text('/dev/nvme0n1p4 / ext4 rw 0 0\n/dev/sda1 /run/media/youruser/STICK vfat rw 0 0\n')
        host.events = {'wait-unplug': self.fake.unplug, 'wait-replug': self.fake.plug}
        self.assertEqual(self.m.run(host, watch=0), 0)
        kind, line = self.verdict(host)
        self.assertEqual((line['classification'], line['stick']['read_by']), ('passed', 'automount'))
        self.assertEqual(host.mounts, [])
        self.assertEqual(host.released, [('/dev/sda1', '/run/media/youruser/STICK')])
        self.assertLess(host.steps.index('release-automount'), host.steps.index('wait-unplug'))

    def test_the_host_not_stopping_after_the_unplug_fails(self):
        host = self.system(stick=True)
        host.events = {'wait-unplug': lambda: self.fake.unplug(keep_roots=True)}
        self.assertEqual(self.m.run(host, watch=0), 2)
        self.assertEqual(self.verdict(host)[1]['classification'], 'hotplug_stop_failed')

    def test_no_reenumeration_after_the_replug_fails(self):
        host = self.system(stick=True)

        def partner_only():
            (self.root/'typec/port0-partner').mkdir()
        host.events = {'wait-unplug': self.fake.unplug, 'wait-replug': partner_only}
        self.assertEqual(self.m.run(host, watch=0), 2)
        self.assertEqual(self.verdict(host)[1]['classification'], 'hotplug_restart_failed')

    def test_an_atc2_dart_fault_in_the_boot_log_fails_and_other_probe_failures_do_not(self):
        host = self.system(stick=True)
        host.kmsg.append('<3>[    9.000000] leds_pwm leds: probe with driver leds_pwm failed with error -22')
        host.events = {'wait-unplug': self.fake.unplug, 'wait-replug': self.fake.plug}
        self.assertEqual(self.m.run(host, watch=0), 0)
        self.dir.cleanup()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        host = self.system(stick=True)
        host.kmsg.append('<3>[    9.000000] apple-dart 392f00000.iommu: translation fault: status:0x1 stream 0')
        self.assertEqual(self.m.run(host, watch=0, hotplug=False), 2)
        self.assertEqual(self.verdict(host)[1]['classification'], 'dart_fault')

    def test_an_unfinished_earlier_run_is_reported_and_kept(self):
        host = self.system()
        (self.root/'state').mkdir(exist_ok=True)
        (self.root/'state'/self.m.ATTEMPT_NAME).write_text(json.dumps(dict(boot_id='old', utc='20261016T180000Z')))
        self.assertEqual(self.m.run(host, watch=0), 0)
        self.assertTrue(any('never finished' in x for x in host.lines))
        self.assertTrue(any(p.name.startswith('usb-test-interrupted-') for p in (self.root/'state').iterdir()))

    def test_check_mode_reads_only(self):
        host = self.system(stick=True)
        self.phy_start(host, 4.2)
        self.assertEqual(self.m.run(host, check=True), 0)
        text = '\n'.join(host.lines)
        self.assertIn('1 host start(s), the first at 4.2 s', text)
        self.assertIn('check: every USB-C port in the tree is up (right)', text)
        self.assertEqual(host.steps, [])
        self.assertFalse((self.root/'state'/BOOT).exists())


class TwoPorts(Base):
    """The left-front port (ATC1) next to the right-hand one: each run sees only its own port's devices and log lines."""
    def test_the_left_front_port_runs_on_its_own_devices(self):
        host = self.system(ports=('right', 'left-front'))
        f = self.fake
        host.events = {'wait-partner': lambda: f.plug('left-front'), 'wait-unplug': lambda: f.unplug(port='left-front'),
                       'wait-replug': lambda: f.plug('left-front')}
        self.assertEqual(self.m.run(host, watch=0, port='left-front'), 0)
        kind, line = self.verdict(host)
        self.assertEqual((kind, line['port'], line['classification']), ('M4_USB_PASS', 'left-front', 'passed'))
        self.assertEqual(line['typec']['name'], 'port1')
        self.assertEqual(host.mounts, ['/dev/sdb1'])
        self.assertTrue(any('LEFT-FRONT USB-C port' in x for x in host.lines))
        self.assertTrue((self.root/'state'/BOOT/'usb'/'usb-test-left-front.json').exists())

    def test_a_stick_on_one_port_is_not_the_other_ports(self):
        host = self.system(ports=('right', 'left-front'))
        self.fake.plug('left-front')
        self.assertIsNone(self.m.storage(host, 'right'))
        self.assertEqual(self.m.usb_roots(host, 'right'), [])
        self.assertFalse(self.m.typec_partner(host, 'right'))
        self.assertEqual(self.m.storage(host, 'left-front'), 'sdb')
        self.assertTrue(self.m.typec_partner(host, 'left-front'))

    def test_log_lines_count_for_the_port_they_name(self):
        host = self.system(ports=('right', 'left-front'))
        ids = self.m.port_ids(host)
        self.assertIn('sn201202x 1-0a:', ids['left-front'])
        log = ['<3>[    5.000000] sn201202x 1-0a: timed out waiting for the select interrupt',
               '<3>[    6.000000] apple-dart 38af00000.iommu: translation fault: status:0x1 stream 0',
               '<6>[    7.000000] phy-apple-t6040-usb2 392a90000.phy: USB2 host sequence complete',
               '<3>[    8.000000] xhci-hcd xhci-hcd.0.auto: HC died; cleaning up',
               '<1>[    9.000000] Unable to handle kernel paging request at virtual address 0']
        right, left = self.m.summarize(log, 'right', ids), self.m.summarize(log, 'left-front', ids)
        self.assertEqual((right['pd'], right['dart'], right['phy_starts']), ([], [], 1))
        self.assertEqual((len(left['pd']), len(left['dart']), left['phy_starts']), (1, 1, 0))
        self.assertEqual((len(right['xhci']), len(left['xhci'])), (1, 1))      # an xHCI line that names no controller
        self.assertEqual((len(right['oops']), len(left['oops'])), (1, 1))

    def test_a_left_pd_failure_does_not_fail_the_right_port(self):
        host = self.system(ports=('right', 'left-front'))
        host.kmsg.append('<3>[    5.000000] sn201202x 1-0a: probe with driver sn201202x failed with error -110')
        self.assertEqual(self.m.run(host, watch=0), 0)
        self.assertEqual(self.verdict(host)[1]['classification'], 'passed_bound_no_stick')

    def test_a_tree_without_the_left_front_port_is_refused(self):
        host = self.system()
        self.assertEqual(self.m.run(host, watch=0, port='left-front'), 1)
        self.assertTrue(any("this kernel's tree has no left-front port" in x for x in host.lines))

    def test_check_mode_reports_every_port_in_the_tree(self):
        host = self.system(ports=('right', 'left-front'))
        self.fake.plug('left-front')
        self.assertEqual(self.m.run(host, check=True), 0)
        text = '\n'.join(host.lines)
        self.assertIn('left-front: sn201202x', text)
        self.assertIn('left-front: host                   running: 3-1, usb3, usb4', text)
        self.assertIn('right: host                        stopped', text)
        self.assertIn('check: every USB-C port in the tree is up (right, left-front)', text)

    def test_the_port_option(self):
        m = load('usbtest_port', TEST)
        seen = []
        m.run = lambda host, **kw: seen.append(kw['port']) or 0
        lines = []

        class H(m.Host):
            def euid(self):
                return 0

            def out(self, text=''):
                lines.append(text)
        self.assertEqual(m.main(['--port', 'left-front', '--no-hotplug'], H()), 0)
        self.assertEqual(m.main([], H()), 0)
        self.assertEqual(seen[:2], ['left-front', 'right'])
        self.assertEqual(m.main(['--port', 'left-back'], H()), 0)
        self.assertEqual(m.main(['--port', 'left-middle'], H()), 1)
        self.assertEqual(m.main(['--port'], H()), 1)


class LeftBack(Base):
    """The left-back port (ATC0, the DFU port): hpm0 sits on the left-front port's SPMI bus, at the right-hand port's SID."""
    ALL = ('right', 'left-front', 'left-back')

    def test_the_left_back_port_runs_on_its_own_devices(self):
        host = self.system(ports=self.ALL)
        f = self.fake
        host.events = {'wait-partner': lambda: f.plug('left-back'), 'wait-unplug': lambda: f.unplug(port='left-back'),
                       'wait-replug': lambda: f.plug('left-back')}
        self.assertEqual(self.m.run(host, watch=0, port='left-back'), 0)
        kind, line = self.verdict(host)
        self.assertEqual((kind, line['port'], line['classification']), ('M4_USB_PASS', 'left-back', 'passed'))
        self.assertEqual(line['typec']['name'], 'port2')
        self.assertEqual(host.mounts, ['/dev/sdc1'])
        self.assertTrue(any('LEFT-BACK USB-C port' in x for x in host.lines))
        self.assertTrue((self.root/'state'/BOOT/'usb'/'usb-test-left-back.json').exists())

    def test_the_same_sid_on_two_buses_stays_apart(self):
        host = self.system(ports=self.ALL)
        ids = self.m.port_ids(host)
        self.assertIn('sn201202x 0-0c:', ids['right'])
        self.assertIn('sn201202x 2-0c:', ids['left-back'])
        log = ['<3>[    5.000000] sn201202x 2-0c: timed out waiting for the select interrupt',
               '<3>[    6.000000] apple-dart 382f80000.iommu: translation fault: status:0x1 stream 1']
        right, back = self.m.summarize(log, 'right', ids), self.m.summarize(log, 'left-back', ids)
        self.assertEqual((right['pd'], right['dart']), ([], []))
        self.assertEqual((len(back['pd']), len(back['dart'])), (1, 1))
        self.assertEqual(self.m.summarize(log, 'left-front', ids)['pd'], [])

    def test_check_mode_reports_all_three_ports(self):
        host = self.system(ports=self.ALL)
        self.fake.plug('left-back')
        self.assertEqual(self.m.run(host, check=True), 0)
        text = '\n'.join(host.lines)
        self.assertIn('left-back: sn201202x', text)
        self.assertIn('left-back: host                    running: 5-1, usb5, usb6', text)
        self.assertIn('check: every USB-C port in the tree is up (right, left-front, left-back)', text)

    def test_a_tree_without_the_left_back_port_is_refused(self):
        host = self.system(ports=('right', 'left-front'))
        self.assertEqual(self.m.run(host, watch=0, port='left-back'), 1)
        self.assertTrue(any("this kernel's tree has no left-back port" in x for x in host.lines))


class EmptyReaderSlot(Base):
    """A hub's card reader without a card shows block devices of size 0: none of them is the stick."""
    def test_an_empty_slot_is_not_the_stick(self):
        host = self.system()
        self.fake.plug()
        (self.root/'block'/'sda'/'size').write_text('0\n')
        self.assertIsNone(self.m.storage(host, 'right'))
        (self.root/'block'/'sda'/'size').write_text('240328704\n')
        self.assertEqual(self.m.storage(host, 'right'), 'sda')

    def test_the_run_waits_for_a_stick_behind_the_hub(self):
        host = self.system()
        self.fake.plug()
        size = self.root/'block'/'sda'/'size'
        size.write_text('0\n')
        host.events = {'wait-stick': lambda: size.write_text('240328704\n')}
        self.assertEqual(self.m.run(host, watch=0, hotplug=False), 0)
        self.assertEqual(self.verdict(host)[1]['classification'], 'passed_no_hotplug')
        self.assertEqual(host.mounts, ['/dev/sda1'])
        self.assertIn('wait-stick', host.steps)
        self.assertFalse(any('already enumerated' in x for x in host.lines))

    def test_no_stick_behind_the_hub_says_so(self):
        host = self.system()
        self.fake.plug()
        (self.root/'block'/'sda'/'size').write_text('0\n')
        self.assertEqual(self.m.run(host, watch=0, hotplug=False), 0)
        self.assertEqual(self.verdict(host)[1]['classification'], 'passed_partner_without_storage')
        self.assertEqual(host.mounts, [])


class KernelLog(Base):
    def test_journal_rows_use_the_kernel_stamp_and_merge_without_doubles(self):
        m = self.m
        journal = '\n'.join(json.dumps(e) for e in (
            dict(MESSAGE='phy-apple-t6040-usb2 392a90000.phy: USB2 host sequence complete', _SOURCE_MONOTONIC_TIMESTAMP='132218574',
                 __MONOTONIC_TIMESTAMP='132220012'),
            dict(MESSAGE='rotated out of the ring', __MONOTONIC_TIMESTAMP='500000'),
            dict(MESSAGE=list(b'smc: \tElec Cause 0x0'), _SOURCE_MONOTONIC_TIMESTAMP='254856000')))
        rows = m.journal_rows(journal + '\nnot json\n')
        self.assertEqual(rows[0], (132.218574, 'phy-apple-t6040-usb2 392a90000.phy: USB2 host sequence complete'))
        self.assertEqual(rows[2], (254.856, 'smc: \tElec Cause 0x0'))
        dmesg = ['<6>[  132.218574] phy-apple-t6040-usb2 392a90000.phy: USB2 host sequence complete',
                 '<6>[  254.856000] smc: \tElec Cause 0x0']
        merged = m.merge_kernel_log(dmesg, rows)
        self.assertEqual(len(merged), 3)
        self.assertEqual(m.summarize(merged)['phy_starts'], 1)
        self.assertTrue(merged[0].endswith('rotated out of the ring'))

    def test_step_lines_are_not_errors(self):
        s = self.m.summarize(['<2>[   10.000000] M4_USB_STEP mount-ro error'])
        self.assertIsNone(self.m.log_errors(s))


class Dispatcher(unittest.TestCase):
    def test_m4_test_runs_the_plain_test_and_has_no_attach_stage_left(self):
        text = M4_TEST.read_text()
        self.assertIn("'usb2': ('usb/usb-test.py', '')", text)
        self.assertNotIn("'usb-pd'", text)
        self.assertNotIn('usb/attach.py', text)
        self.assertNotIn('m4_usb_attach', text)

    def test_main_refuses_without_root_and_bad_options(self):
        m = load('usbtest_main', TEST)
        lines = []

        class H(m.Host):
            def euid(self):
                return 1000

            def out(self, text=''):
                lines.append(text)
        self.assertEqual(m.main([], H()), 1)
        self.assertIn('run with sudo', lines)
        self.assertEqual(m.main(['--watch', '9999'], H()), 1)
        self.assertEqual(m.main(['--bogus'], H()), 1)
        self.assertEqual(m.main(['--help'], H()), 0)


if __name__ == '__main__':
    unittest.main()
