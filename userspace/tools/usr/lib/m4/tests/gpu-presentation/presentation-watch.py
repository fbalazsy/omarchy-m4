#!/usr/bin/python3 -B
"""Bounded, read-only greeter/desktop output-scale and cursor observations."""
import hashlib
import json
import math
import os
import platform
import signal
import socket
import stat
import struct
import sys
import time
import uuid
from pathlib import Path

BASE = Path('/usr/lib/m4')
DURATION = 90
INTERVAL = 0.2
MAX_BYTES = 512 * 1024
QUERY = b'[[BATCH]]j/monitors all;j/cursorpos'
STOP = False
MAX_COMPOSITORS = 16


def need(value, reason):
    if not value:
        raise ValueError(reason)


def stop(_number, _frame):
    global STOP
    STOP = True


def owned_directory(path, uid):
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022,
         'unsafe compositor runtime directory')


def sockets():
    result = []
    for uid in (960, 1000):
        root = Path('/run/user') / str(uid)
        try:
            owned_directory(root, uid)
            owned_directory(root / 'hypr', uid)
            children = list((root / 'hypr').iterdir())
            need(len(children) <= 16, 'too many compositor instance directories')
            for child in children:
                if not child.is_dir():
                    continue
                owned_directory(child, uid)
                path = child / '.socket.sock'
                if not path.exists():
                    continue
                info = path.lstat()
                need(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid, 'unexpected compositor socket')
                result.append((uid, path))
        except FileNotFoundError:
            continue
    need(len(result) <= 4, 'too many compositor sockets')
    return result


def numeric(value, lower, upper):
    return type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper


def observation(raw):
    parts = raw.decode().strip().split('\n\n\n')
    need(len(parts) == 2, 'unexpected compositor batch response')
    monitors, cursor = map(json.loads, parts)
    need(isinstance(monitors, list) and len(monitors) <= 4, 'unexpected monitor list')
    outputs = []
    for item in monitors:
        need(isinstance(item, dict) and isinstance(item.get('name'), str) and len(item['name']) <= 64,
             'unexpected monitor identity')
        fields = {'id': (-1, 64), 'width': (0, 16384), 'height': (0, 16384),
                  'x': (-65536, 65536), 'y': (-65536, 65536), 'scale': (0.1, 16),
                  'refreshRate': (0, 1000), 'transform': (0, 7)}
        need(all(numeric(item.get(key), *bounds) for key, bounds in fields.items()),
             'unexpected monitor geometry')
        flags = ('dpmsStatus', 'disabled', 'hardwareCursorsInUse')
        need(all(type(item.get(key)) is bool for key in flags), 'unexpected monitor state')
        outputs.append({key: item[key] for key in ('name', *fields, *flags)})
    need(isinstance(cursor, dict) and set(cursor) == {'x', 'y'} and
         all(numeric(value, -65536, 65536) for value in cursor.values()), 'unexpected cursor coordinates')
    return dict(monitors=outputs, cursor=cursor)


def query(uid, path):
    started = time.monotonic_ns()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(0.15)
        client.connect(str(path))
        pid, peer_uid, _gid = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        need(peer_uid == uid and pid > 0 and os.readlink('/proc/' + str(pid) + '/exe') == '/usr/bin/Hyprland',
             'compositor peer identity differs')
        client.sendall(QUERY)
        raw = bytearray()
        while True:
            remaining = 0.15 - (time.monotonic_ns() - started) / 1e9
            need(remaining > 0, 'compositor query deadline')
            client.settimeout(remaining)
            chunk = client.recv(8192)
            if not chunk:
                break
            raw.extend(chunk)
            need(len(raw) <= 65536, 'compositor response exceeds bound')
    return dict(uid=uid, pid=pid, request_ns=started, response_ns=time.monotonic_ns(), **observation(raw))


def record_cursor(states, row):
    key = (row['uid'], row['pid'])
    if key not in states and len(states) >= MAX_COMPOSITORS:
        return False
    value = states.setdefault(key, dict(samples=0, positions=set(), changes=0, last=None))
    position = (row['cursor']['x'], row['cursor']['y'])
    value['samples'] += 1
    value['positions'].add(position)
    if value['last'] is not None and value['last'] != position:
        value['changes'] += 1
    value['last'] = position
    return True


def cursor_summary(states, untracked):
    return dict(motion_observed=any(value['changes'] for value in states.values()),
                untracked_samples=untracked, compositor_limit=MAX_COMPOSITORS,
                by_compositor=[dict(uid=uid, pid=pid, samples=value['samples'],
                                    distinct_positions=len(value['positions']), position_changes=value['changes'])
                               for (uid, pid), value in sorted(states.items())],
                input_latency_measured=False, frame_pacing_measured=False,
                scope='Changes between 200ms samples of the same compositor PID; no injected input or photon timing.')


def capture():
    sys.path.insert(0, str(BASE))
    import m4host
    m4host.require()
    need(os.geteuid() == 0 and platform.system() == 'Linux' and platform.machine() == 'aarch64',
         'native root observer required')
    boot = str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    output = Path('/run/m4') / ('presentation-' + boot)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)
    started = time.monotonic_ns()
    deadline = started + DURATION * 1_000_000_000
    count, errors, size = 0, 0, 0
    cursors, untracked_cursors = {}, 0
    digest = hashlib.sha256()
    reason = 'deadline'
    fd = os.open(output / 'observations.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        while not STOP and time.monotonic_ns() < deadline:
            tick = time.monotonic_ns()
            try:
                for uid, path in sockets():
                    try:
                        row = query(uid, path)
                    except (OSError, ValueError) as error:
                        row = dict(uid=uid, request_ns=tick, error=str(error)[:256])
                        errors += 1
                    raw = (json.dumps(row, separators=(',', ':')) + '\n').encode()
                    if size + len(raw) > MAX_BYTES:
                        reason = 'byte_limit'
                        break
                    stream.write(raw)
                    digest.update(raw)
                    size += len(raw)
                    count += 'error' not in row
                    if 'error' not in row and not record_cursor(cursors, row):
                        untracked_cursors += 1
            except (OSError, ValueError) as error:
                errors += 1
                reason = 'socket_inventory_refused: ' + str(error)[:256]
                break
            if reason != 'deadline':
                break
            time.sleep(max(0, min(INTERVAL - (time.monotonic_ns() - tick) / 1e9,
                                  (deadline - time.monotonic_ns()) / 1e9)))
    if STOP:
        reason = 'owner_stop'
    result = dict(probe='presentation_observation', boot_id=boot, kernel=platform.release(),
                  started_ns=started, finished_ns=time.monotonic_ns(), observations=count, query_errors=errors,
                  stop_reason=reason, trace_bytes=size, trace_sha256=digest.hexdigest(),
                  safe_to_continue=True, functional_qualified=False, hardware_writes=0,
                  scale_policy_changed=False, input_injected=False,
                  cursor_motion=cursor_summary(cursors, untracked_cursors),
                  scope='200ms IPC samples of actual scale/cursor; query times are not input latency or display FPS.')
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return dict(directory=str(output), **result)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        need(not sys.argv[1:], 'no arguments; fixed bounded observation only')
        result = capture()
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(probe='presentation_observation', status='REFUSED', error=str(error)[:512],
                              safe_to_continue=True, hardware_writes=0)))
        raise SystemExit(1)
    print(json.dumps(result))
