#!/usr/bin/python3 -B
"""m4-test avd: attended test of the T6040 hardware video decoder (AVD).

  sudo m4-test avd                 preflight + 1080p-class H.264 decode (GStreamer v4l2codecs) + 60 s kernel-log watch
  sudo m4-test avd --no-decode     preflight + watch only: this test submits no decode job
  sudo m4-test avd --check         read only: the decoder's driver and device-tree nodes, firmware, clip, tools
  options: --watch N (seconds, 0..3600, default 60)   --again (after an interrupted attempt or a failed run in this boot)

The decoder is a normal driver since m4.17: the device tree enables the AVD, its DART and its AVD_SYS power state, and
apple-avd (bound through its apple,t6041-avd entry) loads at boot like any other driver. The T6040 entry offers only the
proven H.264 and VP9 (no module parameter since linux-m4 7.1.12-11). The decoder MCU boots at the first decode job of a boot (apple-avd's runtime
resume), not at boot.

Kill switch: `blacklist apple_avd` in a file under /etc/modprobe.d, then reboot. m4.safe (rescue) boots never load it
(m4-support's modprobe install rule). Offering another codec is a kernel change (the T6040 variant's codec list).

What a run does, in this order. Each step is recorded durably BEFORE it runs (steps.jsonl, fsync, sync, a CRIT kmsg line):
  1. preflight: apple_avd loaded, 281080000.avd bound to it, the V4L2 decoder node, the device-tree nodes, the firmware,
     the clip, GStreamer's v4l2slh264dec
  2. gst-launch-1.0 filesrc ! h264parse ! v4l2slh264dec ! videoconvert ! filesink: every frame's SHA-256 must equal the
     reference
  3. a kernel-log watch (DART faults, decoder timeouts, oops)

After a failure, reboot before using the decoder again: apple-avd has no positive stop. /var/lib/m4/avd-boot*.json are
leftovers of the boot start of m4-tools 1.0-9 and earlier, safe to delete.
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
OUTBOX_PREFIX = 'm4-avd-'
STATE_NAME = 'avd'
STEPS_NAME = 'steps.jsonl'
RESULT_NAME = 'avd-test.json'
ATTEMPT_NAME = 'avd-test-attempt.json'
WATCH_DEFAULT_S = 60
WATCH_POLL_S = 5.0
GST_TIMEOUT_S = 90
TAINT_DIE = 1 << 7

AVD_NAME = 'apple_avd'
AVD_DEVICE = '281080000.avd'                     # the decoder's platform device (named after its first reg)
AVD_DRIVER = 'avd'                               # apple-avd's platform driver name
# The device-tree nodes (t6040.dtsi): path under /proc/device-tree and the generic compatible each driver binds.
DT_NODES = (('power-management@502800000/power-controller@170', 'apple,t8103-pmgr-pwrstate'),
            ('iommu@281020000', 'apple,t8110-dart'),
            ('avd@280000000', 'apple,t8132-avd'))
FIRMWARE = Path('/usr/lib/firmware/apple/avd-fw-v5-t1.bin')
FIRMWARE_NAME = 'apple/avd-fw-v5-t1.bin'
FIRMWARE_PACKAGE = 'avd-fw'                    # N1/N2 pin: the file is Asahi's avd-fw package's, and that package verifies clean (its sha256 is recorded)
FIRMWARE_MAX = 0x14000                         # the decoder's `code` window; the driver refuses larger images
CLIP_DIR = 'tests/avd/clips'
CLIP_JSON, CLIP_NAME = 'ip-1088.json', 'ip-1088.h264'
FRAME_BYTES_CHECK = 1920*1088*3//2
# A kernel log line is an error for this test when it is a kernel-wide failure, or it names the AVD / its DART and says it failed.
# (Other devices' faults, e.g. the AOP DART, are not attributed to the decoder.)
WATCH_GLOBAL = re.compile(r'\bOops\b|Internal error|Unable to handle|kernel BUG|BUG: |\bSError\b|nobody cared')
# The decoder's platform device is named after its first reg (281080000.avd), not the node (avd@280000000): its timeouts
# ("avd 281080000.avd: Frame processing timed out!", "failed to reset") were missed until 2026-10-15 (gaps G2).
WATCH_DEVICE = re.compile(r'281020000\.iommu|28[01][0-9a-f]{6}\.avd|apple-avd|apple_avd')
WATCH_FAULT = re.compile(r'fault|error|fail|timed out|timeout|no handler for IRQ|\bH\d+ \d+ error', re.I)
DMESG_LINE = re.compile(r'^(?:<(\d+)>)?\[\s*([0-9.]+)\]\s?(.*)$')
JOURNAL_LINE = re.compile(r'^\[\s*([0-9]+\.[0-9]+)\]\s+\S+\s+kernel:\s?(.*)$')
BLACKLIST_LINE = re.compile(r'^\s*blacklist\s+apple[_-]avd\s*$')


class Refused(RuntimeError):
    pass


class Verdict(RuntimeError):
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
    platform_devices = Path('/sys/bus/platform/devices')
    video_root = Path('/sys/class/video4linux')
    media_root = Path('/sys/bus/media/devices')   # media devices register on the media bus; there is no media class
    modprobe_dirs = (Path('/etc/modprobe.d'), Path('/run/modprobe.d'), Path('/usr/lib/modprobe.d'))
    cmdline_path = Path('/proc/cmdline')
    run_dir = Path('/run')
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

    def run(self, argv, timeout=20, env=None):
        try:
            p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, env=env)
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
                os.write(fd, ('<2>M4_AVD_STEP %s\n' % label).encode())
            finally:
                os.close(fd)
        except OSError:
            pass
        self.run(['/usr/bin/sync'], timeout=30)

    def dmesg(self):
        """The kernel log: dmesg -r merged with this boot's journal (the ring rotates; the journal keeps it)."""
        rc, out, err = self.run(['/usr/bin/dmesg', '-r'], timeout=20)
        rc2, journal, err2 = self.run(['/usr/bin/journalctl', '-k', '-b', '-q', '--no-pager', '-o', 'short-monotonic'],
                                      timeout=30)
        need(rc == 0 or rc2 == 0, 'kernel log unavailable: dmesg: %s; journalctl: %s' % (
            err.strip()[-150:], err2.strip()[-150:]))
        return merge_kernel_log(out.splitlines() if rc == 0 else [], journal.splitlines() if rc2 == 0 else [])

    def tainted(self):
        try:
            return int(self.tainted_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def make_workdir(self):
        d = Path(tempfile.mkdtemp(prefix='m4-avd-', dir=str(self.run_dir)))
        os.chmod(d, 0o700)
        return d


# Files ---------------------------------------------------------------------------------------------

def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(directory, name, raw):
    """Write marker -> fsync file -> rename -> fsync directory."""
    fd, temporary = tempfile.mkstemp(prefix='.avd-', dir=directory)
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
         path.parent.lstat().st_uid == 0, 'unsafe current-boot AVD state directory')
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


def merge_kernel_log(dmesg_lines, journal_lines):
    rows, seen, last = [], Counter(), 0.0
    for line in dmesg_lines:
        prio, stamp, text = parse_line(line)
        if stamp is None:
            stamp = last
        else:
            last = stamp
            seen[(round(stamp, 6), text.rstrip())] += 1
        rows.append((stamp, line))
    for line in journal_lines:
        m = JOURNAL_LINE.match(line)
        if not m:
            continue
        stamp, text = float(m.group(1)), m.group(2)
        key = (round(stamp, 6), text.rstrip())
        if seen[key] > 0:
            seen[key] -= 1
            continue
        rows.append((stamp, '[%12.6f] %s' % (stamp, text)))
    rows.sort(key=lambda row: row[0])
    return [line for _, line in rows]


def watch_errors(lines):
    found = []
    for t in texts(lines):
        if 'M4_AVD_STEP' in t:
            continue
        if WATCH_GLOBAL.search(t) or (WATCH_DEVICE.search(t) and WATCH_FAULT.search(t)):
            found.append(t)
    return found[:20]


# Facts ---------------------------------------------------------------------------------------------

def loaded_modules(host):
    try:
        text = host.proc_modules.read_text()
    except OSError as error:
        raise Refused('module list unavailable: '+str(error))
    return {line.split()[0]: line.split() for line in text.splitlines() if line.split()}


def prop_bytes(path, limit=4096):
    try:
        with path.open('rb') as f:
            return f.read(limit+1)[:limit]
    except OSError:
        return None


def dt_facts(host):
    rows = {}
    for rel, compatible in DT_NODES:
        d = host.dt_root/rel
        status = prop_bytes(d/'status')
        compat = prop_bytes(d/'compatible')
        rows[rel] = dict(present=d.is_dir(), status=None if status is None else status.rstrip(b'\0').decode('latin1'),
                         compatible=None if compat is None else [c.decode('latin1') for c in compat.split(b'\0') if c],
                         expected=compatible)
    return rows


def check_dt(facts):
    """The decoder, its DART and its power state are present, enabled and carry the compatible their driver binds."""
    for rel, compatible in DT_NODES:
        row = facts[rel]
        need(row['present'], 'device-tree node missing: /'+rel)
        need(row['status'] in (None, 'okay', 'ok'), 'device-tree node /%s is %r: this kernel\'s tree predates the m4.17 '
             'decoder integration' % (rel, row['status']))
        need(compatible in (row['compatible'] or []), 'device-tree node /%s has compatible %s, without %s' % (
            rel, row['compatible'], compatible))


def kill_switch(host):
    """Why apple_avd is kept from loading, or None: an m4.safe boot, modprobe.blacklist= on the command line, or a
    `blacklist apple_avd` line in a modprobe.d directory."""
    try:
        words = host.cmdline_path.read_text().split()
    except OSError:
        words = []
    if any(w == 'm4.safe' or w.startswith('m4.safe=') for w in words):
        return 'this is an m4.safe (rescue) boot, which never loads the decoder'
    for w in words:
        if w.startswith('modprobe.blacklist=') and AVD_NAME in w.split('=', 1)[1].replace('-', '_').split(','):
            return 'the kernel command line has %s' % w
    for directory in host.modprobe_dirs:
        try:
            files = sorted(directory.glob('*.conf'))
        except OSError:
            continue
        for f in files:
            try:
                if any(BLACKLIST_LINE.match(line) for line in f.read_text().splitlines()):
                    return '%s blacklists apple_avd (the kill switch)' % f
            except OSError:
                continue
    return None


def binding(host):
    """The decoder's driver state: (driver name or None, apple_avd loaded)."""
    loaded = AVD_NAME in loaded_modules(host)
    link = host.platform_devices/AVD_DEVICE/'driver'
    try:
        driver = Path(os.readlink(link)).name
    except OSError:
        driver = None
    return driver, loaded


def check_bound(host):
    driver, loaded = binding(host)
    if not loaded or driver != AVD_DRIVER:
        why = kill_switch(host)
        if not loaded:
            raise Refused('apple_avd is not loaded%s' % ('; ' + why if why else
                          ' (the decoder loads at boot since m4.17: check `journalctl -k -b | grep -i avd`)'))
        raise Refused('the decoder %s is not bound to apple-avd (driver: %s)%s' % (AVD_DEVICE, driver, '; ' + why if why else ''))
    video, media = video_nodes(host)
    need(video, 'apple-avd is bound, but there is no V4L2 video node named "avd"')
    need(media, 'apple-avd is bound, but there is no media controller node')
    return dict(driver=driver, video_nodes=video, media_nodes=media)


def firmware_facts(host):
    try:
        data = FIRMWARE.read_bytes()
    except OSError as error:
        raise Refused('AVD firmware %s: %s (the avd-fw package provides it)' % (FIRMWARE, error.strerror))
    need(0 < len(data) <= FIRMWARE_MAX, 'AVD firmware size %d is not within the 0x14000-byte code window' % len(data))
    rc, out, err = host.run(['/usr/bin/pacman', '-Qqo', str(FIRMWARE)])
    need(rc == 0 and out.split() == [FIRMWARE_PACKAGE],
         'AVD firmware %s is not owned by the %s package (pacman -Qo: %s)' % (FIRMWARE, FIRMWARE_PACKAGE, (out + err).strip()[-200:] or 'nothing'))
    rc, out, err = host.run(['/usr/bin/pacman', '-Qkk', FIRMWARE_PACKAGE], timeout=60)
    need(rc == 0, 'pacman -Qkk %s is not clean (%d): %s' % (FIRMWARE_PACKAGE, rc, (out + err).strip()[-200:]))
    return dict(path=str(FIRMWARE), bytes=len(data), sha256=sha(data), package=FIRMWARE_PACKAGE)


def clip_facts(host):
    ref = json.loads((host.base/CLIP_DIR/CLIP_JSON).read_bytes())
    data = (host.base/CLIP_DIR/CLIP_NAME).read_bytes()
    need(len(data) <= 4*1024*1024, 'test clip is larger than 4 MiB')
    need(sha(data) == ref.get('sha256'), 'test clip differs from its reference')
    frames = ref.get('frames')
    need(isinstance(frames, list) and frames and ref.get('width') == 1920 and ref.get('height') == 1088,
         'clip reference is not the 1920x1088 clip')
    for f in frames:
        need(isinstance(f.get('sha256_i420'), str) and f.get('nv12_bytes') == FRAME_BYTES_CHECK,
             'clip reference frame record is malformed')
    return ref, data


def tool_facts(host, what='v4l2slh264dec'):
    # v4l2codecs registers v4l2slh264dec only for a stateless decoder that exists when the registry is scanned. The decoder
    # is bound at boot (m4.17), so the element itself is checked; 'v4l2codecs' checks only the plugin.
    rc, out, err = host.run(['/usr/bin/gst-inspect-1.0', what], timeout=20)
    return dict(gst_v4l2slh264dec=rc == 0, gst_detail=(out+err).strip().splitlines()[:1])


def video_nodes(host):
    """(video node names whose V4L2 name is 'avd', media node names)."""
    video = []
    try:
        for d in sorted(host.video_root.iterdir()):
            try:
                if (d/'name').read_text().strip() == 'avd':
                    video.append(d.name)
            except OSError:
                continue
    except OSError:
        pass
    media = []
    try:
        media = sorted(d.name for d in host.media_root.iterdir())
    except OSError:
        pass
    return video, media


# Orchestration -------------------------------------------------------------------------------------

def earlier_failure(host):
    """None, or why the decoder may be faulted in this boot: an earlier m4-test avd in this boot failed."""
    path = host.state_parent/host.boot_id()/STATE_NAME/RESULT_NAME
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if old.get('passed') is True:
        return None
    return 'an earlier m4-test avd in this boot failed (%s)' % (old.get('reason') or 'no reason recorded')


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


def preflight(host, decode):
    """Every read-only refusal. Returns the facts the result records."""
    taint = host.tainted()
    need(taint is not None and not taint & TAINT_DIE, 'the kernel is tainted by an oops: reboot first')
    facts = dict(taint_before=taint)
    facts['binding'] = check_bound(host)
    facts['device_tree'] = dt_facts(host)
    check_dt(facts['device_tree'])
    facts['firmware'] = firmware_facts(host)
    if decode:
        tools = tool_facts(host)
        need(tools['gst_v4l2slh264dec'], 'GStreamer has no v4l2slh264dec for the decoder (gst-plugins-bad-libs installed? %s)'
             % tools['gst_detail'])
    return facts


def split_frames(path, size):
    data = path.read_bytes()
    need(len(data) > 0 and len(data) % size == 0, 'decoder output is %d bytes, not a whole number of %d-byte frames' % (
        len(data), size))
    return [data[i:i+size] for i in range(0, len(data), size)]


def stage_decode(host, ref, clip_data, steps, result):
    work = host.make_workdir()
    clip = work/CLIP_NAME
    clip.write_bytes(clip_data)
    out = work/'out.i420'
    argv = ['/usr/bin/gst-launch-1.0', '-q', 'filesrc', 'location='+str(clip), '!', 'h264parse', '!', 'v4l2slh264dec',
            '!', 'videoconvert', '!', 'video/x-raw,format=I420', '!', 'filesink', 'location='+str(out)]
    tools = tool_facts(host)
    if not tools['gst_v4l2slh264dec']:
        raise Verdict('GStreamer registered no v4l2slh264dec for the bound decoder: %s' % tools['gst_detail'])
    host.step('gst-launch decode %s (the first decode job of a boot boots the MCU: reset, DART empty-domain attach, '
              'firmware copy, mailbox)' % CLIP_NAME, argv=argv)
    rc, so, se = host.run(argv, timeout=GST_TIMEOUT_S)
    steps.append(dict(step='gst-launch', returncode=rc, output=(so+se).strip()[-300:]))
    result.update(returncode=rc, output=(so+se).strip()[-300:], frames_expected=len(ref['frames']))
    if rc != 0:
        raise Verdict('gst-launch failed (rc %s): %s' % (rc, (so+se).strip()[-200:]))
    if not out.exists():
        raise Verdict('gst-launch produced no output')
    frames = split_frames(out, FRAME_BYTES_CHECK)
    got = [sha(f) for f in frames]
    want = [f['sha256_i420'] for f in ref['frames']]
    result['frames'] = len(frames)
    result['sha256_i420'] = got
    result['mismatch'] = [i for i in range(max(len(got), len(want))) if i >= len(got) or i >= len(want) or got[i] != want[i]]
    out.unlink(missing_ok=True)
    clip.unlink(missing_ok=True)
    if len(frames) != len(want):
        raise Verdict('decoded %d frames, the clip has %d' % (len(frames), len(want)))
    if result['mismatch']:
        raise Verdict('frame(s) %s differ from the reference (VideoToolbox-verified)' % result['mismatch'])
    return result


def watch_log(host, say, before, seconds):
    start = host.monotonic()
    seen = 0
    while True:
        elapsed = host.monotonic() - start
        found = watch_errors(delta(before, host.dmesg()))
        if not found and (host.tainted() or 0) & TAINT_DIE:
            found = ['kernel taint: the kernel oopsed']
        if found or elapsed >= seconds:
            return round(elapsed, 1), found
        if int(elapsed//60) > seen:
            seen = int(elapsed//60)
            say('  %d s, no decoder or DART error' % int(elapsed))
        host.sleep(min(WATCH_POLL_S, max(0.0, seconds - elapsed)))


def check_mode(host, say):
    """Read only. Prints one line per fact; returns 0 when `m4-test avd` would start."""
    ok = True

    def row(name, good, detail=''):
        nonlocal ok
        ok = ok and good
        say('%-4s %-26s %s' % ('ok' if good else 'FAIL', name, detail))

    def attempt(name, function):
        try:
            detail = function()
            row(name, True, detail if isinstance(detail, str) else '')
        except (Refused, ValueError, OSError, KeyError) as error:
            row(name, False, str(error))

    def bound():
        b = check_bound(host)
        return 'apple-avd bound to %s; video %s, media %s' % (AVD_DEVICE, ','.join(b['video_nodes']), ','.join(b['media_nodes']))

    def nodes():
        check_dt(dt_facts(host))
        return 'enabled: ' + ', '.join(c for _, c in DT_NODES)

    def firmware():
        f = firmware_facts(host)
        return '%d B  %s' % (f['bytes'], f['sha256'][:16])

    def clip():
        ref, data = clip_facts(host)
        return '%s %dx%d, %d frames, %d B' % (CLIP_NAME, ref['width'], ref['height'], len(ref['frames']), len(data))

    def tools():
        f = tool_facts(host)
        need(f['gst_v4l2slh264dec'], 'GStreamer has no v4l2slh264dec for the decoder (gst-plugins-bad-libs)')
        return f['gst_detail'][0] if f['gst_detail'] else ''

    def clean():
        t = host.tainted()
        need(t is not None and not t & TAINT_DIE, 'kernel is tainted by an oops (taint %s): reboot' % t)
        return 'taint %s' % t

    def boot():
        why = earlier_failure(host)
        need(why is None, (why or '') + ': reboot first (or --again)')
        return 'no failed run in this boot'

    attempt('decoder driver', bound)
    attempt('device-tree nodes', nodes)
    attempt('firmware', firmware)
    attempt('test clip', clip)
    attempt('gstreamer', tools)
    attempt('kernel clean', clean)
    attempt('this boot', boot)
    path = host.state_parent/ATTEMPT_NAME
    row('previous attempt', not path.exists(), 'none' if not path.exists() else 'an attempt never finished: see steps; use --again')
    say('check: %s' % ('m4-test avd would start' if ok else 'm4-test avd would refuse'))
    return 0 if ok else 1


def run(host, watch=WATCH_DEFAULT_S, decode=True, again=False, check=False):
    say = host.out
    if check:
        return check_mode(host, say)
    parent = host.state_parent
    result = dict(schema=2, mode='avd-test', boot_id=host.boot_id(), kernel=host.release(), utc=host.utc(),
                  passed=False, decode_requested=decode, watch_seconds_requested=watch)
    try:
        facts = preflight(host, decode)
        ref, clip_data = clip_facts(host)
        why = earlier_failure(host)
        if why and not again:
            raise Refused(why + ': reboot first (the decoder may be faulted), or use --again')
    except (Refused, ValueError, OSError, KeyError) as error:
        say('m4-test avd refused: %s' % error)
        return 1
    attempt_path = parent/ATTEMPT_NAME
    old = None
    if attempt_path.exists():
        try:
            old = json.loads(attempt_path.read_text())
        except (OSError, ValueError):
            old = {}
        say('The previous attempt (boot %s, %s) never finished: %s.' % (old.get('boot_id'), old.get('utc'),
            'it ended with ' + str(old['ended']) if old.get('ended') else 'the Mac probably reset during it'))
        for line in last_steps(host, str(old.get('boot_id', ''))):
            say('  last step: '+line)
        if not again:
            say('Send these lines back. To run the test anyway: m4-test avd --again')
            return 3
    archive = Archive(host)
    steps = []
    try:
        if old is not None:
            atomic(parent, 'avd-test-interrupted-%s.json' % host.utc(), dump(old))
            attempt_path.unlink(missing_ok=True)
            fsync_dir(parent)
        host.step('m4-test avd begin', decode=decode, watch=watch)
        atomic(parent, ATTEMPT_NAME, dump(dict(boot_id=host.boot_id(), utc=host.utc(), kernel=host.release())))
        result.update(preflight=facts, firmware=facts['firmware'])
        binding = facts['binding']
        say('m4-test avd: apple-avd is bound (video %s, media %s)' % (','.join(binding['video_nodes']),
                                                                        ','.join(binding['media_nodes'])))
        before = host.dmesg()
        if decode:
            say('Decoding %s (%d frames) with GStreamer v4l2slh264dec ...' % (CLIP_NAME, len(ref['frames'])))
            result['decode'] = {}
            stage_decode(host, ref, clip_data, steps, result['decode'])
            say('  %d frames, every SHA-256 equals the reference' % result['decode']['frames'])
        say('Watching the kernel log for decoder and DART errors for %d s ...' % watch)
        host.step('watch %d s' % watch)
        result['watch_seconds'], found = watch_log(host, say, before, watch)
        result['watch_errors'] = found
        if found:
            raise Verdict('error during the watch: %s' % found[0])
        attempt_path.unlink(missing_ok=True)
        fsync_dir(parent)
        result['passed'] = True
        say('PASS: %s and the decoder ran %d s without a decoder or DART error.' % (
            'decoded %d frames exactly' % result['decode']['frames'] if decode else 'no decode job (--no-decode)',
            int(result['watch_seconds'])))
    except (Verdict, Refused) as error:     # a need() after the start is a failed test, not a reset
        result['reason'] = str(error)
        attempt_path.unlink(missing_ok=True)
        fsync_dir(parent)
        say('FAIL: %s' % error)
        say('The decoder has no positive stop: reboot before using it again.')
    except BaseException as error:  # unexpected error or Ctrl-C: the attempt record stays, marked with how it ended
        result['reason'] = '%s: %s' % (type(error).__name__, error)
        try:
            atomic(parent, ATTEMPT_NAME, dump(dict(json.loads(attempt_path.read_text()), ended=result['reason'])))
        except (OSError, ValueError):
            pass
        say('FAIL: %s' % result['reason'])
    result['steps'] = steps
    result['taint_after'] = host.tainted()
    try:
        rc, out, _ = host.run(['/usr/bin/systemctl', 'is-system-running'], timeout=10)
        result['system_after'] = out.strip()
        archive.save(RESULT_NAME, result)
        atomic(parent, RESULT_NAME, dump(result))
        if not result['passed']:
            archive.open_outbox()
            if archive.directory is not None:
                say('Send back the folder: %s' % archive.directory)
            elif archive.outbox_error:
                say('The outbox copy failed (%s); the result is in %s' % (archive.outbox_error, archive.state))
    except (OSError, Refused) as error:
        say('result not saved: %s' % error)
    return 0 if result['passed'] else 2


USAGE = 'usage: avd-test.py [--check] [--no-decode] [--watch 0..3600] [--again] | --help'


def main(argv=None, host=None):
    argv = sys.argv[1:] if argv is None else argv
    host = host or Host()
    if any(a in ('-h', '--help') for a in argv):
        host.out(__doc__)
        host.out(USAGE)
        return 0
    watch, decode, again, check, i = WATCH_DEFAULT_S, True, False, False, 0
    try:
        while i < len(argv):
            if argv[i] == '--watch':
                watch = int(argv[i+1])
                i += 2
            elif argv[i] == '--no-decode':
                decode = False
                i += 1
            elif argv[i] == '--again':
                again = True
                i += 1
            elif argv[i] in ('--check', 'check'):
                check = True
                i += 1
            else:
                raise ValueError(argv[i])
        if not 0 <= watch <= 3600:
            raise ValueError('watch')
    except (ValueError, IndexError):
        host.out(USAGE)
        return 1
    if host.euid() != 0:
        host.out('run with sudo')
        return 1
    return run(host, watch=watch, decode=decode, again=again, check=check)


if __name__ == '__main__':
    sys.exit(main())
