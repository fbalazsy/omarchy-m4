"""Host facts of the attended M4 tests and the AOP boot start: bounded reads, the boot's CPU/kernel identity, kernel module files.

It verifies nothing against a manifest: the package manager owns the files and the kernel's module
loader validates what is loaded. It keeps what the tests used it for: refusals with one exception type, bounded reads, and a stable record of
which CPUs and which kernel a run started on, so a test can say "the CPU set changed during the run".
"""
from pathlib import Path
import hashlib
import json
import os
import platform
import stat
import subprocess

MODULES = Path('/usr/lib/modules')
ONLINE = Path('/sys/devices/system/cpu/online')
POSSIBLE = Path('/sys/devices/system/cpu/possible')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
CMDLINE = Path('/proc/cmdline')


def need(ok, text):
    if not ok:
        raise RuntimeError(text)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=1024*1024, runtime=False):
    """Bytes of a regular file of at most `limit` bytes. `runtime` is accepted for the old call sites (files under /run)."""
    path = Path(path)
    st = path.lstat()
    need(stat.S_ISREG(st.st_mode), 'not a regular file: %s' % path)
    need(st.st_size <= limit, 'oversized file: %s' % path)
    return path.read_bytes()


def boot_id():
    return BOOT_ID.read_text().strip()


def release():
    return platform.release()


def safe_mode():
    """True on a rescue boot (m4.safe on the command line): hardware-touching tests refuse."""
    try:
        words = CMDLINE.read_text().split()
    except OSError:
        return False
    return any(w == 'm4.safe' or w.startswith('m4.safe=') for w in words)


def kernel_digest(rel=None):
    """sha256 of the running kernel's image file (what markers of attended tests are bound to)."""
    path = MODULES/(rel or release())/'vmlinuz'
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def boot_identity():
    """The CPU/kernel identity of this boot: the same value for the whole boot unless a CPU went away."""
    need(platform.system() == 'Linux' and platform.machine() == 'aarch64', 'aarch64 Linux required')
    online = ONLINE.read_text().strip()
    possible = POSSIBLE.read_text().strip()
    return dict(release=release(), online=online, possible=possible, cpu_count=os.cpu_count())


def require():
    return boot_identity()


def same_cpu_identity(value, expected):
    if type(value) is not dict or type(expected) is not dict:
        return False
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':')) == json.dumps(expected, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        return False


def module_path(name, rel=None):
    """Path of module NAME in the running kernel's tree (`modinfo -n`), or None."""
    p = subprocess.run(['/usr/bin/modinfo', '-k', rel or release(), '-n', name], capture_output=True, text=True, timeout=15)
    out = p.stdout.strip()
    return Path(out) if p.returncode == 0 and out.startswith('/') else None


def read_module(name, limit=8*1024*1024):
    """Decompressed bytes of module NAME from the running kernel's tree."""
    path = module_path(name)
    need(path is not None, 'module %s is not in the kernel tree' % name)
    raw = path.read_bytes()
    tools = {'.zst': ['/usr/bin/zstd', '-dc'], '.xz': ['/usr/bin/xz', '-dc'], '.gz': ['/usr/bin/gzip', '-dc']}
    if path.suffix in tools:
        p = subprocess.run(tools[path.suffix], input=raw, capture_output=True, timeout=30)
        need(p.returncode == 0, 'cannot decompress %s' % path)
        raw = p.stdout
    need(0 < len(raw) <= limit, 'module %s is %d bytes' % (name, len(raw)))
    return raw
