"""Run-state helpers of the attended tests: the root-only /run/m4/state directory (this boot) and bounded command output."""
from pathlib import Path
import json
import os
import stat
import subprocess
import tempfile
import uuid

import m4host

STATE = Path('/run/m4/state')


def boot():
    return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))


def state():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = STATE.lstat()
    m4host.need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o700, 'unsafe run-state directory')


def save(result, destination='result.json'):
    state()
    raw = (json.dumps(result, indent=2) + '\n').encode()
    fd, name = tempfile.mkstemp(prefix='.result-', dir=STATE)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, STATE/destination)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(m4host.read(path, runtime=True))


def bounded_text(value, limit):
    tail = value[-limit:]
    while len(json.dumps(tail).encode()) > limit:
        tail = tail[len(tail)//2+1:]
    return tail, tail != value


def command(argv, timeout=30, stdout_limit=48000):
    try:
        p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        stdout, a = bounded_text(p.stdout, stdout_limit)
        stderr, b = bounded_text(p.stderr, 8000)
        return {'returncode': p.returncode, 'stdout': stdout, 'stderr': stderr, 'truncated': a or b}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'returncode': -1, 'error': str(error)}
