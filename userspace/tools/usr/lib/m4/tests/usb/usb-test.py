#!/usr/bin/python3 -B
"""m4-test usb2: attended test of a USB-C port's USB2 host with hotplug, bound at boot: the right-hand port (ATC2, since
m4.19), the left-front port (ATC1, since m4.21) or the left-back port (ATC0, the DFU port, from the kernel that adds its
device-tree nodes).

  sudo m4-test usb2                preflight, then a USB stick (already in, or plugged in when asked) read read-only, then an
                                   unplug and a replug, on the right-hand port
  sudo m4-test usb2 --port left-front    the same on the left-front port (--port left-back: the left-back port)
  sudo m4-test usb2 --no-hotplug   preflight and the stick only
  sudo m4-test usb2 --check        read only, every port the device tree has: drivers, nodes, the USB-C port, kill switch,
                                   this boot's kernel log
  options: --watch N (seconds of kernel-log watch at the end, 0..600, default 10)

Each port is a normal driver stack since the kernel that enables its device-tree nodes at boot:
- the USB2 PHY (phy-apple-t6040-usb2);
- the dwc3 (dwc3-apple, which registers a USB role switch);
- the PD controller (sn201202x, tipd).
They autoload, and tipd starts the PHY and the xHCI host on every attach and stops them on every detach. This test loads
and unloads no module and writes no register. It reads sysfs and the kernel log, mounts the stick read-only (unless an
automounter already mounted it), and asks for plugs.

Kill switch: `blacklist dwc3_apple` in a file under /etc/modprobe.d, then reboot. m4.safe (rescue) boots never load the
stack (m4-support's modprobe install rules).

What a run does, in this order. Each step is recorded durably BEFORE it runs (steps.jsonl, fsync, sync, a CRIT kmsg line):
  1. preflight: the three drivers bound, the device-tree nodes enabled, the USB-C port and the role switch registered,
     no USB, DART or PD error in this boot's kernel log
  2. the stick: already enumerated (a stick that was in at boot), or plugged into the port when asked; it is mounted
     read-only and its top-level entries are counted. A stick an automounter (udiskie) already mounted is only listed.
  3. hotplug: a stick an automounter mounted is unmounted first (udisksctl), then unplug (the host must stop) and replug
     (the stick must enumerate again)
  4. a kernel-log watch; the result goes to /var/lib/m4/<boot>/usb/usb-test-<port>.json, and on a failure also to a
     folder in ~/M4-Outbox

Kernel-log lines are counted for the port they name (its dwc3, PHY, DARTs, SPMI controller or PD controller); xHCI lines
that name no controller count for every port, an oops for all.

/var/lib/m4/usb-stage-*.json are leftovers of the attach stages of m4-tools 1.0-11 and earlier, safe to delete.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import os
import pwd
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

BASE = Path('/usr/lib/m4')
STATE_PARENT = Path('/var/lib/m4')
OUTBOX = Path(next((u.pw_dir for u in pwd.getpwall() if u.pw_uid == 1000), '/root'))/'M4-Outbox'   # the desktop user's
OUTBOX_PREFIX = 'm4-usb-'
STATE_NAME = 'usb'
STEPS_NAME = 'steps.jsonl'
RESULT_NAME = 'usb-test-%s.json'        # per port
ATTEMPT_NAME = 'usb-test-attempt.json'
WATCH_DEFAULT_S = 10
WATCH_MAX_S = 600
TAINT_DIE = 1 << 7

PARTNER_WAIT_S = 60.0       # for the owner to plug the stick in
STICK_WAIT_S = 60.0         # from the attach to a block device behind the new root hub
AUTOMOUNT_WAIT_S = 10.0     # an automounter that is running gets this long to mount a new stick before the test mounts it
HOTPLUG_WAIT_S = 60.0       # for the owner to unplug, and to plug back in
HOTPLUG_STOP_S = 10.0       # from the detach to the root hubs gone (500 ms debounce + teardown)
POLL_S = 1.0

MODULES = ('phy_apple_t6040_usb2', 'dwc3_apple', 'sn201202x')
ROLE_SWITCH_PROP = 'usb-role-switch'
PWRSTATE, PMGR = 'apple,t8103-pmgr-pwrstate', 'apple,t8103-pmgr'
# Each port's stack (t6040.dtsi): its platform devices (named after their first reg), the PD controller's device-tree node,
# its SPMI controller and DARTs, and the device-tree nodes (path under /proc/device-tree, a compatible each must carry).
PORTS = {
    'right': dict(label='RIGHT', dwc3='392280000.usb', phy='392a90000.phy', pd='spmi@509198000/usb-pd@c',
                  spmi='509198000.spmi', darts=('392f00000', '392f80000'),
                  dt=(('power-management@502800000/power-controller@3e0', PWRSTATE),     # atc2_common
                      ('power-management@508300000', PMGR),
                      ('power-management@508300000/power-controller@d8', PWRSTATE),     # atc2_usb_aon
                      ('power-management@508300000/power-controller@140', PWRSTATE),    # atc2_usb
                      ('power-management@508300000/power-controller@160', PWRSTATE),    # atc2_phymxwrap
                      ('power-management@508300000/power-controller@b0', PWRSTATE),     # atc2_common_dp
                      ('power-management@508300000/power-controller@70', PWRSTATE),     # nub_spmi_a1
                      ('iommu@392f00000', 'apple,t6041-dart'),
                      ('iommu@392f80000', 'apple,t6041-dart'),
                      ('phy@392a90000', 'apple,t6040-atcphy'),
                      ('usb@392280000', 'apple,t6040-dwc3'),
                      ('spmi@509198000', 'apple,t6041-spmi'),
                      ('spmi@509198000/usb-pd@c', 'apple,sn201202x'))),
    'left-front': dict(label='LEFT-FRONT', dwc3='38a280000.usb', phy='38aa90000.phy', pd='spmi@509188000/usb-pd@a',
                       spmi='509188000.spmi', darts=('38af00000', '38af80000'),
                       dt=(('power-management@502800000/power-controller@3b8', PWRSTATE),     # atc1_common
                           ('power-management@508300000', PMGR),
                           ('power-management@508300000/power-controller@d0', PWRSTATE),     # atc1_usb_aon
                           ('power-management@508300000/power-controller@120', PWRSTATE),    # atc1_usb
                           ('power-management@508300000/power-controller@158', PWRSTATE),    # atc1_phymxwrap
                           ('power-management@508300000/power-controller@a8', PWRSTATE),     # atc1_common_dp
                           ('power-management@508300000/power-controller@68', PWRSTATE),     # nub_spmi_a0
                           ('iommu@38af00000', 'apple,t6041-dart'),
                           ('iommu@38af80000', 'apple,t6041-dart'),
                           ('phy@38aa90000', 'apple,t6040-atcphy'),
                           ('usb@38a280000', 'apple,t6040-dwc3'),
                           ('spmi@509188000', 'apple,t6041-spmi'),
                           ('spmi@509188000/usb-pd@a', 'apple,sn201202x'))),
    'left-back': dict(label='LEFT-BACK', dwc3='382280000.usb', phy='382a90000.phy', pd='spmi@509188000/usb-pd@c',
                      spmi='509188000.spmi', darts=('382f00000', '382f80000'),
                      dt=(('power-management@502800000/power-controller@390', PWRSTATE),     # atc0_common
                          ('power-management@508300000', PMGR),
                          ('power-management@508300000/power-controller@c8', PWRSTATE),     # atc0_usb_aon
                          ('power-management@508300000/power-controller@118', PWRSTATE),    # atc0_usb
                          ('power-management@508300000/power-controller@150', PWRSTATE),    # atc0_phymxwrap
                          ('power-management@508300000/power-controller@a0', PWRSTATE),     # atc0_common_dp
                          ('power-management@508300000/power-controller@68', PWRSTATE),     # nub_spmi_a0
                          ('iommu@382f00000', 'apple,t6041-dart'),
                          ('iommu@382f80000', 'apple,t6041-dart'),
                          ('phy@382a90000', 'apple,t6040-atcphy'),
                          ('usb@382280000', 'apple,t6040-dwc3'),
                          ('spmi@509188000', 'apple,t6041-spmi'),
                          ('spmi@509188000/usb-pd@c', 'apple,sn201202x'))),
}
DEFAULT_PORT = 'right'


def drivers(port):
    """The stack's drivers in bind order: (module, sysfs bus, device, driver name). The PD controller's SPMI device is found
    by its device-tree node (the bus number depends on probe order)."""
    p = PORTS[port]
    return (('phy_apple_t6040_usb2', 'platform', p['phy'], 'phy-apple-t6040-usb2'),
            ('dwc3_apple', 'platform', p['dwc3'], 'dwc3-apple'),
            ('sn201202x', 'spmi', p['pd'], 'sn201202x'))


def dwc3_node(port):
    return 'usb@' + PORTS[port]['dwc3'].split('.')[0]


def role_switch(port):
    return PORTS[port]['dwc3'] + '-role-switch'

DMESG_LINE = re.compile(r'^(?:<(\d+)>)?\[\s*([0-9.]+)\]\s?(.*)$')
RE_OOPS = re.compile(r'Oops|Internal error|Unable to handle kernel|kernel BUG|Call trace:|SError|'
                     r'synchronous external abort|Kernel panic')
RE_DART = re.compile(r'(apple-dart|\.iommu).*(fault|error)', re.I)
RE_XHCI = re.compile(r'HC died|host system error|xHCI host controller not responding|failed to initialize core|'
                     r'dwc3.*(timed out|timeout)|Error while assigning device slot|probe .*failed with error|'
                     r'unable to enumerate', re.I)
RE_USB_LINE = re.compile(r'xhci|dwc3|\busb', re.I)     # RE_XHCI counts only on these (a whole-boot scan)
RE_PD = re.compile(r'(sn201202x|tps6598x|\.spmi).*(failed|timed out|timeout|error)', re.I)
RE_ROLE = re.compile(r'USB role (start|exit) unconfirmed|Role unavailable: reset assertion unconfirmed')
RE_PHY_DONE = re.compile(r'USB2 host sequence complete')
RE_PHY_FAIL = re.compile(r'phy-apple-t6040-usb2.*(failed|timed out|timeout|refus)', re.I)
RE_NO_DEVICE_MODE = re.compile(r'No device mode on this port')
BLACKLIST_LINE = re.compile(r'^\s*blacklist\s+(\S+)\s*$')


class Refused(RuntimeError):
    pass


def need(ok, message):
    if not ok:
        raise Refused(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(value):
    return (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()


class Host:
    """Every fact and side effect goes through here; tests replace this class's attributes."""
    base = BASE
    state_parent = STATE_PARENT
    outbox = OUTBOX
    trusted_root = Path('/')
    dt_root = Path('/proc/device-tree')
    proc_modules = Path('/proc/modules')
    bus_root = Path('/sys/bus')
    typec_root = Path('/sys/class/typec')
    usb_role_root = Path('/sys/class/usb_role')
    usb_root = Path('/sys/bus/usb/devices')
    block_root = Path('/sys/block')
    mounts_path = Path('/proc/self/mounts')
    modprobe_dirs = (Path('/etc/modprobe.d'), Path('/run/modprobe.d'), Path('/usr/lib/modprobe.d'))
    cmdline_path = Path('/proc/cmdline')
    tainted_path = Path('/proc/sys/kernel/tainted')
    kmsg_path = '/dev/kmsg'
    owner_uid = 1000

    def __init__(self):
        pass

    def euid(self):
        return os.geteuid()

    def release(self):
        return platform.release()

    def boot_id(self):
        return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))

    def utc(self):
        return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def out(self, text=''):
        print(text, flush=True)

    def chown(self, path):
        os.chown(path, self.owner_uid, self.owner_uid)

    def run(self, argv, timeout=20):
        try:
            p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return -1, '', str(error)
        return p.returncode, p.stdout[-262144:], p.stderr[-8000:]

    def step(self, label, **info):
        """Durable "about to do <label>": appended to the per-boot steps file (fsync), sync(1), journal flushed, and a CRIT
        line to /dev/kmsg (journald syncs on CRIT: the line survives a reset). Best effort: never blocks the step."""
        line = json.dumps(dict(utc=self.utc(), uptime=round(self.monotonic(), 1), step=label, **info), sort_keys=True)
        try:
            fd = os.open(state_dir(self)/STEPS_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, (line+'\n').encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, Refused):
            pass
        for argv in (['/usr/bin/sync'], ['/usr/bin/journalctl', '--sync']):
            self.run(argv, timeout=30)
        try:
            fd = os.open(self.kmsg_path, os.O_WRONLY)
            try:
                os.write(fd, ('<2>M4_USB_STEP %s\n' % label).encode())
            finally:
                os.close(fd)
        except OSError:
            pass
        self.run(['/usr/bin/sync'], timeout=30)

    def dmesg(self):
        """This boot's kernel log: dmesg -r merged with the journal (the ring rotates; the journal keeps it)."""
        rc, out, err = self.run(['/usr/bin/dmesg', '-r'], timeout=20)
        rc2, journal, err2 = self.run(['/usr/bin/journalctl', '-k', '-b', '-q', '--no-pager', '-o', 'json',
                                       '--output-fields=MESSAGE,_SOURCE_MONOTONIC_TIMESTAMP'], timeout=30)
        need(rc == 0 or rc2 == 0, 'kernel log unavailable: dmesg: %s; journalctl: %s' % (
            err.strip()[-150:], err2.strip()[-150:]))
        return merge_kernel_log(out.splitlines() if rc == 0 else [], journal_rows(journal) if rc2 == 0 else [])

    def tainted(self):
        try:
            return int(self.tainted_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def automounters(self):
        """Running automounters (udiskie --automount mounts a new stick read-write within about a second)."""
        found = []
        for d in Path('/proc').glob('[0-9]*'):
            try:
                if (d/'comm').read_text().strip() in ('udiskie',):
                    found.append(int(d.name))
            except OSError:
                pass
        return found

    def mount_ro(self, device, target):
        rc, out, err = self.run(['/usr/bin/mount', '-o', 'ro,nosuid,nodev,noexec', device, target], timeout=60)
        return rc, (out+err)[-1000:]

    def umount(self, target):
        rc, out, err = self.run(['/usr/bin/umount', target], timeout=60)
        return rc, (out+err)[-1000:]

    def release_automount(self, device, target):
        """Unmount a stick another program mounted: through udisks when it is there (udiskie's own state stays right),
        else umount(8)."""
        if Path('/usr/bin/udisksctl').exists():
            rc, out, err = self.run(['/usr/bin/udisksctl', 'unmount', '--block-device', device, '--no-user-interaction'],
                                    timeout=60)
            if rc == 0:
                return 0, (out+err)[-1000:]
        return self.umount(target)

    def list_dir(self, path):
        return len(os.listdir(path))


# Files ---------------------------------------------------------------------------------------------

def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(directory, name, raw):
    """Write -> fsync file -> rename -> fsync directory."""
    fd, temporary = tempfile.mkstemp(prefix='.usb-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, directory/name)
        fsync_dir(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def trusted_dir(host, path):
    node = path
    while True:
        s = node.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe state path '+str(node))
        if node == host.trusted_root:
            return
        need(node != node.parent, 'state path escaped the trusted root')
        node = node.parent


def state_dir(host):
    parent = host.state_parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    trusted_dir(host, parent)
    need(parent.stat().st_dev == host.trusted_root.stat().st_dev, 'state is not on the native root')
    boot_dir = parent/host.boot_id()
    made = not boot_dir.exists()
    boot_dir.mkdir(mode=0o700, exist_ok=True)
    if made:
        fsync_dir(parent)
    path = boot_dir/STATE_NAME
    made = not path.exists()
    path.mkdir(mode=0o700, exist_ok=True)
    if made:
        fsync_dir(boot_dir)
    s = path.lstat()
    need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o700 and
         path.parent.lstat().st_uid == 0, 'unsafe current-boot USB state directory')
    return path


class Archive:
    """State directory plus (on failure only) a copy in the user's outbox; the outbox copy is best effort."""
    def __init__(self, host):
        self.host = host
        self.state = state_dir(host)
        self.stamp = host.utc()
        self.directory = None
        self.outbox_error = None
        self.saved = {}

    def save(self, name, raw):
        if isinstance(raw, (dict, list)):
            raw = dump(raw)
        elif isinstance(raw, str):
            raw = raw.encode()
        atomic(self.state, name, raw)
        self.saved[name] = raw
        if self.directory is not None:
            self._publish(name, raw)
        return sha(raw)

    def open_outbox(self):
        """Create the outbox folder (once) and copy what is already saved. Called for a failure only."""
        if self.directory is not None or self.outbox_error is not None:
            return
        host = self.host
        try:
            parent = host.outbox
            s = parent.lstat()
            need(parent.resolve() == parent and s.st_uid == host.owner_uid and not s.st_mode & 0o022, 'unsafe outbox')
            self.directory = parent/(OUTBOX_PREFIX+host.boot_id()+'-'+self.stamp+'-'+uuid.uuid4().hex[:6])
            self.directory.mkdir(mode=0o700)
            host.chown(self.directory)
        except (OSError, Refused) as error:
            self.directory, self.outbox_error = None, str(error)
            return
        steps = self.state/STEPS_NAME
        if steps.exists():
            self.saved[STEPS_NAME] = steps.read_bytes()
        for name, raw in list(self.saved.items()):
            self._publish(name, raw)

    def _publish(self, name, raw):
        try:
            fd = os.open(self.directory/name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
                self.host.chown(self.directory/name)
        except OSError as error:
            self.outbox_error = name+': '+str(error)


# Kernel log ----------------------------------------------------------------------------------------

def parse_line(line):
    match = DMESG_LINE.match(line)
    if not match:
        return None, None, line
    prio, stamp, text = match.groups()
    return (int(prio) if prio is not None else None), float(stamp), text


def delta(before, after):
    if after[:len(before)] == before:
        return after[len(before):]
    stamps = [parse_line(line)[1] for line in before if parse_line(line)[1] is not None]
    last = max(stamps) if stamps else -1.0
    known = Counter(before)
    out = []
    for line in after:
        stamp = parse_line(line)[1] or -1.0
        if stamp > last:
            out.append(line)
        elif stamp == last and known[line] <= 0:
            out.append(line)
        elif known[line] > 0:
            known[line] -= 1
    return out


def texts(lines):
    return [parse_line(line)[2] for line in lines]


def journal_rows(text):
    """(kernel stamp in s, message) per line of `journalctl -k -o json`. The stamp is _SOURCE_MONOTONIC_TIMESTAMP, the
    kernel's own: -o short-monotonic prints the time journald read the line (1-3 ms later, early-boot lines over a second
    later) and expands tabs, so its lines never matched dmesg's and every line counted twice (2026-10-16)."""
    rows = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
            message = entry['MESSAGE']
            if isinstance(message, list):           # journald exports non-UTF-8 messages as a byte array
                message = bytes(message).decode('utf-8', 'replace')
            stamp = int(entry.get('_SOURCE_MONOTONIC_TIMESTAMP') or entry['__MONOTONIC_TIMESTAMP'])/1e6
        except (ValueError, KeyError, TypeError):
            continue
        for part in str(message).split('\n'):
            rows.append((stamp, part))
    return rows


def merge_kernel_log(dmesg_lines, journal):
    """dmesg's lines plus the journal's (kernel stamp, message) rows that the ring no longer holds."""
    rows, seen, last = [], Counter(), 0.0
    for line in dmesg_lines:
        prio, stamp, text = parse_line(line)
        if stamp is None:
            stamp = last
        else:
            last = stamp
            seen[(round(stamp, 6), text.rstrip())] += 1
        rows.append((stamp, line))
    for stamp, text in journal:
        key = (round(stamp, 6), text.rstrip())
        if seen[key] > 0:
            seen[key] -= 1
            continue
        rows.append((stamp, '[%12.6f] %s' % (stamp, text)))
    rows.sort(key=lambda row: row[0])
    return [line for _, line in rows]


def owner(x, ids):
    """The port a kernel-log line names (by its devices), or None."""
    for port, names in ids.items():
        if any(n in x for n in names):
            return port
    return None


def summarize(lines, port=None, ids=None):
    """The USB facts of a stretch of kernel log: errors by kind, host starts, the first start's timestamp. With a port, only
    the lines that name that port's devices count (`ids`: port_ids()); xHCI lines naming no controller and an oops count
    for every port. Without one, every line counts."""
    s = dict(oops=[], dart=[], xhci=[], pd=[], role=[], phy=[], phy_starts=0, first_start_s=None, no_device_mode=0)
    for line in lines:
        _, stamp, x = parse_line(line)
        if 'M4_USB_STEP' in x:
            continue
        whose = owner(x, ids) if port is not None else None
        mine = port is None or whose == port
        if RE_PHY_DONE.search(x) and mine:
            s['phy_starts'] += 1
            if s['first_start_s'] is None and stamp is not None:
                s['first_start_s'] = stamp
        if RE_NO_DEVICE_MODE.search(x) and mine:
            s['no_device_mode'] += 1
        for key, regex in (('oops', RE_OOPS), ('dart', RE_DART), ('xhci', RE_XHCI), ('pd', RE_PD), ('role', RE_ROLE),
                           ('phy', RE_PHY_FAIL)):
            if not regex.search(x) or len(s[key]) >= 20:
                continue
            if key == 'xhci' and not RE_USB_LINE.search(x):
                continue
            counts = key == 'oops' or mine or (key == 'xhci' and whose is None)
            if counts:
                s[key].append(x)
    return s


def log_errors(s):
    """The first error line of a summary, by kind, or None."""
    for key in ('oops', 'dart', 'role', 'phy', 'xhci', 'pd'):
        if s[key]:
            return key, s[key][0]
    return None


# Facts ---------------------------------------------------------------------------------------------

def prop_bytes(path, limit=4096):
    try:
        with path.open('rb') as f:
            return f.read(limit+1)[:limit]
    except OSError:
        return None


def loaded_modules(host):
    try:
        text = host.proc_modules.read_text()
    except OSError as error:
        raise Refused('module list unavailable: '+str(error))
    return {line.split()[0]: line.split() for line in text.splitlines() if line.split()}


def dt_facts(host, port):
    rows = {}
    for rel, compatible in PORTS[port]['dt']:
        d = host.dt_root/rel
        status = prop_bytes(d/'status')
        compat = prop_bytes(d/'compatible')
        rows[rel] = dict(present=d.is_dir(), status=None if status is None else status.rstrip(b'\0').decode('latin1'),
                         compatible=None if compat is None else [c.decode('latin1') for c in compat.split(b'\0') if c],
                         expected=compatible)
    rows[dwc3_node(port)]['role_switch'] = (host.dt_root/dwc3_node(port)/ROLE_SWITCH_PROP).exists()
    return rows


def port_in_tree(host, port):
    return (host.dt_root/dwc3_node(port)).is_dir()


def check_dt(facts, port):
    """Every node of the path is present, enabled and carries its compatible; the dwc3 has the role switch."""
    for rel, compatible in PORTS[port]['dt']:
        row = facts[rel]
        need(row['present'], 'device-tree node missing: /%s (this kernel\'s tree has no %s port)' % (rel, port))
        need(row['status'] in (None, 'okay', 'ok'), 'device-tree node /%s is %r: this kernel\'s tree predates the USB '
             'boot-time start (its usb2 test is the attach stages of m4-tools 1.0-11)' % (rel, row['status']))
        need(compatible in (row['compatible'] or []), 'device-tree node /%s has compatible %s, without %s' % (
            rel, row['compatible'], compatible))
    need(facts[dwc3_node(port)]['role_switch'], 'the dwc3 node has no usb-role-switch: a device tree from before m4.18')


def kill_switch(host):
    """Why the stack is kept from loading, or None: an m4.safe boot, modprobe.blacklist= on the command line, or a
    `blacklist` line for one of its modules in a modprobe.d directory."""
    try:
        words = host.cmdline_path.read_text().split()
    except OSError:
        words = []
    if any(w == 'm4.safe' or w.startswith('m4.safe=') for w in words):
        return 'this is an m4.safe (rescue) boot, which never loads the USB stack'
    for w in words:
        if w.startswith('modprobe.blacklist=') and set(MODULES) & set(w.split('=', 1)[1].replace('-', '_').split(',')):
            return 'the kernel command line has %s' % w
    for directory in host.modprobe_dirs:
        try:
            files = sorted(directory.glob('*.conf'))
        except OSError:
            continue
        for f in files:
            try:
                for line in f.read_text().splitlines():
                    m = BLACKLIST_LINE.match(line)
                    if m and m.group(1).replace('-', '_') in MODULES:
                        return '%s blacklists %s (the kill switch)' % (f, m.group(1))
            except OSError:
                continue
    return None


def device_path(host, bus, name):
    """The sysfs device of a driver row. SPMI devices are found by their device-tree node."""
    if bus != 'spmi':
        return host.bus_root/bus/'devices'/name
    try:
        entries = sorted(host.bus_root.joinpath('spmi', 'devices').iterdir())
    except OSError:
        return None
    for d in entries:
        try:
            if os.readlink(d/'of_node').rstrip('/').endswith('/'+name):
                return d
        except OSError:
            continue
    return None


def driver_of(path):
    if path is None:
        return None
    try:
        return Path(os.readlink(path/'driver')).name
    except OSError:
        return None


def binding(host, port):
    """module -> dict(loaded, device, driver) for the three drivers of the port's stack."""
    loaded = loaded_modules(host)
    rows = {}
    for module, bus, name, _ in drivers(port):
        path = device_path(host, bus, name)
        rows[module] = dict(loaded=module in loaded, device=None if path is None else path.name, driver=driver_of(path))
    return rows


def unbound(rows, port):
    """The rows whose driver is not bound, as text, in bind order."""
    return ['%s (%s)' % (module, 'not loaded' if not rows[module]['loaded'] else
                         'no device' if rows[module]['device'] is None else 'device %s unbound' % rows[module]['device'])
            for module, _, _, driver in drivers(port) if rows[module]['driver'] != driver]


def pd_device(host, port):
    """The name of the port's PD controller SPMI device (e.g. 0-0c), or None."""
    path = device_path(host, 'spmi', PORTS[port]['pd'])
    return None if path is None else path.name


def port_ids(host):
    """port -> the strings its kernel-log lines carry: dwc3, PHY, DARTs, SPMI controller, PD controller device."""
    ids = {}
    for port, p in PORTS.items():
        names = [p['dwc3'], p['phy'], p['spmi']] + [d + '.iommu' for d in p['darts']]
        pd = pd_device(host, port)
        if pd:
            names += ['sn201202x %s:' % pd, 'tps6598x %s:' % pd]
        ids[port] = tuple(names)
    return ids


def typec_port(host, port):
    """The port's typec port (the one under its PD controller's device): dict(name, partner, roles) or None."""
    pd = pd_device(host, port)
    if pd is None:
        return None
    try:
        names = sorted(p.name for p in host.typec_root.iterdir())
    except OSError:
        return None
    ports = [n for n in names if not any(k in n for k in ('-partner', '-cable', '-plug'))
             and '/%s/' % pd in os.path.realpath(host.typec_root/n)]
    if not ports:
        return None
    d = host.typec_root/ports[0]
    row = dict(name=ports[0], partner=(host.typec_root/(ports[0]+'-partner')).is_dir())
    for attr in ('data_role', 'power_role', 'power_operation_mode', 'usb_typec_revision', 'usb_power_delivery_revision'):
        v = prop_bytes(d/attr)
        if v is not None:
            row[attr] = v.decode('latin1').strip()
    return row


def typec_partner(host, port):
    row = typec_port(host, port)
    return bool(row and row['partner'])


def usb_devices(host, port):
    """The USB devices behind the port's controller (their sysfs path runs through its dwc3 device)."""
    rows = {}
    try:
        names = sorted(p.name for p in host.usb_root.iterdir())
    except OSError:
        return rows
    for name in names[:128]:
        if ':' in name or '/%s/' % PORTS[port]['dwc3'] not in os.path.realpath(host.usb_root/name):
            continue
        d = host.usb_root/name
        rows[name] = {k: (prop_bytes(d/k) or b'').decode('latin1').strip() for k in
                      ('idVendor', 'idProduct', 'manufacturer', 'product', 'speed', 'version') if (d/k).exists()}
    return rows


def usb_roots(host, port):
    return [n for n in usb_devices(host, port) if n.startswith('usb')]


def has_medium(host, name):
    """False for a block device without a medium: an empty card-reader slot (a hub's, say) has size 0."""
    try:
        return int((host.block_root/name/'size').read_text().strip() or '0') > 0
    except (OSError, ValueError):
        return True


def storage(host, port):
    """The first block device with a medium whose sysfs path runs through a (non root-hub) USB device of the port, or
    None."""
    devs = [n for n in usb_devices(host, port) if not n.startswith('usb')]
    if not devs:
        return None
    try:
        names = sorted(p.name for p in host.block_root.iterdir())
    except OSError:
        return None
    for name in names:
        try:
            real = os.path.realpath(host.block_root/name/'device')
        except OSError:
            continue
        if any('/'+u+'/' in real+'/' for u in devs) and has_medium(host, name):
            return name
    return None


def partition(host, block):
    parts = sorted(p.name for p in (host.block_root/block).glob(block+'*')) if (host.block_root/block).is_dir() else []
    return '/dev/'+(parts[0] if parts else block)


def mounted_at(host, devices):
    try:
        rows = [line.split() for line in host.mounts_path.read_text().splitlines()]
    except OSError:
        return None
    for row in rows:
        if len(row) > 1 and row[0] in devices:
            return row[1].replace('\\040', ' ')
    return None


# Phases --------------------------------------------------------------------------------------------

def preflight(host, port):
    """Refusals and the port stack's facts. A stack that is not bound without a kill switch is a failed boot-time start: it
    is returned as a verdict for the result, not refused."""
    taint = host.tainted()
    need(taint is not None and not taint & TAINT_DIE, 'the kernel is tainted by an oops: reboot first')
    facts = dict(taint_before=taint, device_tree=dt_facts(host, port))
    check_dt(facts['device_tree'], port)
    facts['binding'] = binding(host, port)
    missing = unbound(facts['binding'], port)
    why = kill_switch(host)
    if missing and why:
        raise Refused('the USB stack is not bound (%s): %s' % (', '.join(missing), why))
    facts['port'] = typec_port(host, port)
    facts['role_switch'] = (host.usb_role_root/role_switch(port)).is_dir()
    facts['unbound'] = missing
    return facts


def wait_for(host, test, seconds):
    waited = 0.0
    while not test():
        if waited >= seconds:
            return False, waited
        host.sleep(POLL_S)
        waited += POLL_S
    return True, waited


def stick_phase(host, say, state, port):
    """The stick: already enumerated, or plugged in now. Read it read-only (or list it where an automounter put it)."""
    out = dict(at_start=False, partner=False, block=None, device=None, read_by=None, entries=None, error=None,
               automount=None)
    label = PORTS[port]['label']
    block = storage(host, port)
    if block:
        out.update(at_start=True, partner=True, block=block)
        say('M4_USB a USB stick is already enumerated (%s)' % block)
    else:
        say('M4_USB plug the USB stick into the %s USB-C port now (waiting up to %d s)' % (label, PARTNER_WAIT_S))
        host.step('wait-partner', seconds=PARTNER_WAIT_S, port=port)
        seen, _ = wait_for(host, lambda: typec_partner(host, port), PARTNER_WAIT_S)
        if not seen:
            out['error'] = 'no USB-C device was plugged into the %s port' % port
            return out
        out['partner'] = True
        host.step('wait-stick', seconds=STICK_WAIT_S)
        seen, _ = wait_for(host, lambda: storage(host, port) is not None, STICK_WAIT_S)
        if not seen:
            out['error'] = 'a USB-C device is attached, but no USB storage device enumerated within %d s' % STICK_WAIT_S
            return out
        out['block'] = storage(host, port)
        say('M4_USB the stick enumerated (%s)' % out['block'])
    device = partition(host, out['block'])
    out['device'] = device
    names = (device, '/dev/'+out['block'])
    if host.automounters():
        wait_for(host, lambda: mounted_at(host, names) is not None, AUTOMOUNT_WAIT_S)
    where = mounted_at(host, names)
    if where:
        out['automount'] = where
        host.step('list-automount', path=where)
        try:
            out.update(entries=host.list_dir(where), read_by='automount')
        except OSError as error:
            out['error'] = 'listing %s: %s' % (where, error)
        return out
    target = state/'stick-mount'
    target.mkdir(mode=0o700, exist_ok=True)
    host.step('mount-ro', device=device)
    rc, text = host.mount_ro(device, str(target))
    if rc != 0:
        out['error'] = 'mount -o ro %s: %s' % (device, text.strip()[-300:])
        return out
    try:
        out.update(entries=host.list_dir(target), read_by='test')
    except OSError as error:
        out['error'] = str(error)
    host.step('umount')
    rc, text = host.umount(str(target))
    if rc != 0:
        out['error'] = 'umount: '+text.strip()[-300:]
    return out


def hotplug_phase(host, say, stick, port):
    """On the unplug tipd sets USB_ROLE_NONE and dwc3-apple stops the host (the xHCI root hubs go away); on the replug it
    sets USB_ROLE_HOST, the PHY and the host start again, and the stick has to enumerate again. A step the owner did not
    do is reported, not failed."""
    out = dict(released=None, unplugged=False, host_stopped=False, stop_s=None, replugged=False, reenumerated=False,
               block=None)
    if stick.get('automount'):
        host.step('release-automount', path=stick['automount'])
        rc, text = host.release_automount(stick['device'], stick['automount'])
        out['released'] = rc == 0
        if rc != 0:
            say('M4_USB the stick is mounted at %s and could not be unmounted (%s): eject it in the file manager first' % (
                stick['automount'], text.strip()[-120:]))
    say('M4_USB hotplug: unplug the stick now, or the hub it is in (the test waits for the USB-C port to be empty; up to '
        '%d s)' % HOTPLUG_WAIT_S)
    host.step('wait-unplug', seconds=HOTPLUG_WAIT_S)
    gone, _ = wait_for(host, lambda: not typec_partner(host, port), HOTPLUG_WAIT_S)
    if not gone:
        say('M4_USB hotplug: the USB-C port still has a device in it (a hub?): hotplug not tried')
        return out
    out['unplugged'] = True
    stopped, waited = wait_for(host, lambda: not usb_roots(host, port), HOTPLUG_STOP_S)
    if not stopped:
        say('M4_USB hotplug: the USB host is still running %d s after the unplug' % HOTPLUG_STOP_S)
        return out
    out.update(host_stopped=True, stop_s=waited)
    say('M4_USB hotplug: the host stopped; plug it back in now (waiting up to %d s)' % HOTPLUG_WAIT_S)
    host.step('wait-replug', seconds=HOTPLUG_WAIT_S)
    back, _ = wait_for(host, lambda: typec_partner(host, port), HOTPLUG_WAIT_S)
    if not back:
        say('M4_USB hotplug: no replug')
        return out
    out['replugged'] = True
    again, _ = wait_for(host, lambda: storage(host, port) is not None, STICK_WAIT_S)
    if again:
        out.update(reenumerated=True, block=storage(host, port))
        say('M4_USB hotplug: the stick enumerated again (%s)' % out['block'])
    else:
        say('M4_USB hotplug: the stick did not enumerate again within %d s' % STICK_WAIT_S)
    return out


def classify(facts, boot, run_log):
    """-> (passed, classification). `boot` summarizes the kernel log up to the preflight, `run_log` the test's part."""
    for s in (boot, run_log):
        if s['oops']:
            return False, 'kernel_oops'
    if facts['unbound']:
        return False, 'stack_not_bound'
    if not facts['port']:
        return False, 'no_typec_port'
    if not facts['role_switch']:
        return False, 'no_role_switch'
    for s in (boot, run_log):
        found = log_errors(s)
        if found:
            return False, {'dart': 'dart_fault', 'role': 'role_switch_failed', 'phy': 'phy_failed', 'xhci': 'xhci_errors',
                           'pd': 'pd_errors'}[found[0]]
    stick = facts.get('stick') or {}
    if not stick.get('block'):
        return True, 'passed_bound_no_stick' if not stick.get('partner') else 'passed_partner_without_storage'
    if stick.get('read_by') is None:
        return True, 'passed_stick_not_read'
    plug = facts.get('hotplug')
    if plug is None:
        return True, 'passed_no_hotplug'
    if not plug['unplugged']:
        return True, 'passed_hotplug_not_tried'
    if not plug['host_stopped']:
        return False, 'hotplug_stop_failed'
    if not plug['replugged']:
        return True, 'passed_replug_not_tried'
    if not plug['reenumerated']:
        return False, 'hotplug_restart_failed'
    return True, 'passed'


def watch_log(host, before, seconds):
    waited = 0.0
    while waited < seconds:
        if summarize(delta(before, host.dmesg()))['oops'] or (host.tainted() or 0) & TAINT_DIE:
            break
        step = min(POLL_S*5, seconds - waited)
        host.sleep(step)
        waited += step
    return waited


def last_steps(host, boot_id, count=8):
    path = host.state_parent/boot_id/STATE_NAME/STEPS_NAME
    try:
        lines = path.read_text().splitlines()[-count:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
            rows.append('%s  +%ss  %s' % (row.get('utc'), row.get('uptime'), row.get('step')))
        except ValueError:
            rows.append(line[:120])
    return rows


def check_mode(host, say):
    """Read only, every port the device tree has. Prints one line per fact; returns 0 when every such port's stack is
    bound and this boot's log is clean."""
    ok = True

    def row(name, good, detail=''):
        nonlocal ok
        ok = ok and good
        say('%-4s %-34s %s' % ('ok' if good else 'FAIL', name, detail))

    why = kill_switch(host)
    row('kill switch', why is None, why or 'none')
    t = host.tainted()
    row('kernel clean', t is not None and not t & TAINT_DIE, 'taint %s' % t)
    try:
        log = host.dmesg()
    except Refused as error:
        log = None
        row('kernel log', False, str(error))
    ids = port_ids(host)
    ports = [port for port in PORTS if port_in_tree(host, port)]
    if not ports:
        row('ports', False, 'no USB-C port in this kernel\'s device tree')
    for port in ports:
        try:
            check_dt(dt_facts(host, port), port)
            row(port+': device-tree nodes', True, '%d enabled, the dwc3 has its role switch' % len(PORTS[port]['dt']))
        except Refused as error:
            row(port+': device-tree nodes', False, str(error))
        rows = binding(host, port)
        for module, _, _, driver in drivers(port):
            r = rows[module]
            row('%s: %s' % (port, module), r['driver'] == driver, '%s -> %s' % (r['device'], r['driver']))
        typec = typec_port(host, port)
        row(port+': USB-C port', typec is not None, json.dumps(typec, sort_keys=True) if typec else 'no typec port')
        row(port+': role switch', (host.usb_role_root/role_switch(port)).is_dir(), role_switch(port))
        devices = usb_devices(host, port)
        say('     %-34s %s' % (port+': host', 'running: '+', '.join(sorted(devices)) if usb_roots(host, port) else
                                 'stopped (nothing attached)'))
        if log is not None:
            s = summarize(log, port, ids)
            found = log_errors(s)
            row(port+': kernel log', found is None, ('%s: %s' % found) if found else
                'no USB, DART or PD error; %d host start(s)%s' % (
                    s['phy_starts'], ', the first at %.1f s' % s['first_start_s'] if s['first_start_s'] is not None else ''))
    say('check: %s' % ('every USB-C port in the tree is up (%s)' % ', '.join(ports) if ok else
                       'something is missing: see the FAIL rows'))
    return 0 if ok else 1


def run(host, watch=WATCH_DEFAULT_S, hotplug=True, check=False, port=DEFAULT_PORT):
    say = host.out
    if check:
        return check_mode(host, say)
    parent = host.state_parent
    result_name = RESULT_NAME % port
    result = dict(schema=2, mode='usb-test', port=port, boot_id=host.boot_id(), kernel=host.release(), utc=host.utc(),
                  passed=False, classification='not_run', hotplug_requested=hotplug, watch_seconds_requested=watch)
    try:
        facts = preflight(host, port)
    except (Refused, ValueError, OSError, KeyError) as error:
        say('m4-test usb2 refused: %s' % error)
        return 1
    attempt_path = parent/ATTEMPT_NAME
    if attempt_path.exists():
        try:
            old = json.loads(attempt_path.read_text())
        except (OSError, ValueError):
            old = {}
        say('Note: the previous run (boot %s, %s) never finished%s.' % (old.get('boot_id'), old.get('utc'),
            ': it ended with ' + str(old['ended']) if old.get('ended') else '; the Mac probably reset during it'))
        for line in last_steps(host, str(old.get('boot_id', ''))):
            say('  last step: '+line)
        try:
            atomic(parent, 'usb-test-interrupted-%s.json' % host.utc(), dump(old))
        except OSError:
            pass
    archive = Archive(host)
    log = []
    try:
        atomic(parent, ATTEMPT_NAME, dump(dict(boot_id=host.boot_id(), utc=host.utc(), kernel=host.release(), port=port)))
        host.step('m4-test usb2 begin', port=port, hotplug=hotplug, watch=watch)
        before = host.dmesg()
        ids = port_ids(host)
        boot = summarize(before, port, ids)
        result.update(preflight=facts, boot_log=boot)
        if facts['unbound']:
            say('M4_USB the %s port\'s USB stack is not bound: %s' % (port, ', '.join(facts['unbound'])))
        else:
            say('M4_USB the %s port\'s stack is bound at boot (%d host start(s) so far%s)' % (
                port, boot['phy_starts'], ', the first at %.1f s' % boot['first_start_s'] if boot['first_start_s'] else ''))
            facts['stick'] = stick_phase(host, say, archive.state, port)
            facts['usb'] = usb_devices(host, port)
            if hotplug and facts['stick'].get('block'):
                facts['hotplug'] = hotplug_phase(host, say, facts['stick'], port)
            if watch:
                host.step('watch %d s' % watch)
                result['watch_seconds'] = watch_log(host, before, watch)
        log = delta(before, host.dmesg())
        run_log = summarize(log, port, port_ids(host))
        result['run_log'] = run_log
        result['passed'], result['classification'] = classify(facts, boot, run_log)
        attempt_path.unlink(missing_ok=True)
        fsync_dir(parent)
    except Refused as error:     # a need() after the start is a failed test, not a reset
        result['reason'] = str(error)
        result['classification'] = 'error'
        attempt_path.unlink(missing_ok=True)
        fsync_dir(parent)
    except BaseException as error:  # unexpected error or Ctrl-C: the attempt record stays, marked with how it ended
        result['reason'] = '%s: %s' % (type(error).__name__, error)
        result['classification'] = 'interrupted'
        try:
            atomic(parent, ATTEMPT_NAME, dump(dict(json.loads(attempt_path.read_text()), ended=result['reason'])))
        except (OSError, ValueError):
            pass
    result['taint_after'] = host.tainted()
    try:
        archive.save('usb-test-%s-dmesg-delta.txt' % port, '\n'.join(log[-4000:])+'\n')
        archive.save(result_name, result)
        atomic(parent, result_name, dump(result))
        if not result['passed']:
            archive.open_outbox()
    except (OSError, Refused) as error:
        say('result not saved: %s' % error)
    stick = facts.get('stick') or {}
    say('M4_USB_%s %s' % ('PASS' if result['passed'] else 'FAIL', json.dumps(dict(
        port=port, classification=result['classification'], typec=facts.get('port'),
        usb_devices=sorted(facts.get('usb') or {}),
        stick={k: stick.get(k) for k in ('at_start', 'block', 'read_by', 'entries', 'error')} if stick else None,
        **({'hotplug': facts['hotplug']} if 'hotplug' in facts else {}),
        **({'reason': result['reason']} if 'reason' in result else {})), sort_keys=True)))
    if result['classification'] == 'kernel_oops':
        say('The kernel reported an oops: reboot before anything else.')
    if archive.directory is not None:
        say('Send back the folder: %s' % archive.directory)
    elif archive.outbox_error:
        say('The outbox copy failed (%s); the result is in %s' % (archive.outbox_error, archive.state))
    else:
        say('Result: %s' % (archive.state/result_name))
    return 0 if result['passed'] else 2


USAGE = 'usage: usb-test.py [--check] [--port %s] [--no-hotplug] [--watch 0..600] | --help' % '|'.join(PORTS)


def main(argv=None, host=None):
    argv = sys.argv[1:] if argv is None else argv
    host = host or Host()
    if any(a in ('-h', '--help') for a in argv):
        host.out(__doc__)
        host.out(USAGE)
        return 0
    watch, hotplug, check, port, i = WATCH_DEFAULT_S, True, False, DEFAULT_PORT, 0
    try:
        while i < len(argv):
            if argv[i] == '--port':
                port = argv[i+1]
                if port not in PORTS:
                    raise ValueError(port)
                i += 2
            elif argv[i] == '--watch':
                watch = int(argv[i+1])
                i += 2
            elif argv[i] == '--no-hotplug':
                hotplug = False
                i += 1
            elif argv[i] in ('--check', 'check'):
                check = True
                i += 1
            else:
                raise ValueError(argv[i])
        if not 0 <= watch <= WATCH_MAX_S:
            raise ValueError('watch')
    except (ValueError, IndexError):
        host.out(USAGE)
        return 1
    if host.euid() != 0:
        host.out('run with sudo')
        return 1
    return run(host, watch=watch, hotplug=hotplug, check=check, port=port)


if __name__ == '__main__':
    sys.exit(main())
