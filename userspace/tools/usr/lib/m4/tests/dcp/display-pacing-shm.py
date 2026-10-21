#!/usr/bin/python3 -B
"""Frame-pacing probe without new GPU clients (Linux side, 2026-10-13). User session, no root.

Same question as the macOS side's display-pacing.py, but every workload is a CPU-drawn (wl_shm) client, so the
only GPU client stays Hyprland. fpsprobe.py uses mpv --vo=gpu-next, a new GPU client; after the 2026-10-13
Firefox WebRender power-off (a rejected GPU submission stalled the compositor) that is not run here.

Needs: appledrm swap_stats=Y (root sets it: echo Y | sudo tee /sys/module/appledrm/parameters/swap_stats),
mpv and ffmpeg, the desktop session. Reads flip_event_mode/swap_timestamps and records them; changes no setting.

Phases (PHASE_S each): idle, cursor sweep (Hyprland IPC movecursor at ~250 Hz), mpv wl_shm 120 fps 640x360,
mpv wl_shm 120 fps 1280x720, mpv wl_shm 60 fps 1280x720. Output: ~/M4-Outbox/display-pacing-shm-<utc>.json.

Phase `frameprobe` (frameprobe-src/, a C wl_shm client with wp_presentation feedback) is the primary
pacing signal; mpv's wl_shm output is client-bound (Hyprland drew 32-34 fps for a 120 fps clip).
Options (used by `m4-test display-pacing`, which runs this as the desktop user once per flip_event_mode):
  --phase-s N        seconds per phase (default 12)
  --phases a,b,...   run only these phases (names as in the report)
  --out FILE         write the report there instead of ~/M4-Outbox (the file must not exist)
The stats parser also reads the fields intent_n / intent_lead_us_avg / intent_lead_us_max / frame_ns.
"""
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PARAMS = Path('/sys/module/appledrm/parameters')
OUTBOX = Path.home()/'M4-Outbox'
PHASE_S = 12
SETTLE_S = 2.0
STATS = re.compile(r'M4_DCP_SWAP_STATS window_ms=(\d+) swaps=(\d+) rate_x10=(\d+) gap_us_avg=(\d+) gap_us_max=(\d+) '
                   r'lat_us_avg=(\d+) lat_us_max=(\d+) vrr_timestamps=(-?\d+) commit_us_avg=(\d+) commit_us_max=(\d+) '
                   r'flush_masks=(\d+)/(\d+)/(\d+)'
                   r'(?: intent_n=(\d+) intent_lead_us_avg=(\d+) intent_lead_us_max=(\d+) frame_ns=(\d+))?')
FIELDS = ('window_ms', 'swaps', 'rate_x10', 'gap_us_avg', 'gap_us_max', 'lat_us_avg', 'lat_us_max', 'vrr_timestamps',
          'commit_us_avg', 'commit_us_max', 'flush0', 'flush1', 'flush2', 'intent_n', 'intent_lead_us_avg',
          'intent_lead_us_max', 'frame_ns')
MPV_PROPS = ('display-fps', 'estimated-display-fps', 'estimated-vf-fps', 'container-fps', 'vsync-jitter',
             'frame-drop-count', 'decoder-frame-drop-count', 'vo-delayed-frame-count', 'mistimed-frame-count', 'osd-dimensions')


def param(name):
    try:
        return (PARAMS/name).read_text().strip()
    except OSError:
        return None


def run(argv, timeout=30):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def journal_cursor():
    _, out, _ = run(['journalctl', '-k', '-n', '0', '--show-cursor', '--no-pager'])
    m = re.search(r'-- cursor: (\S+)', out)
    return m.group(1) if m else None


def stats_since(cursor):
    argv = ['journalctl', '-k', '--no-pager', '-o', 'cat'] + (['--after-cursor', cursor] if cursor else ['-n', '200'])
    _, out, _ = run(argv)
    rows = []
    for line in out.splitlines():
        m = STATS.search(line)
        if m:
            rows.append(dict(zip(FIELDS, (int(v or 0) for v in m.groups()))))
    return rows


def summarise(rows):
    if len(rows) > 1:
        rows = rows[1:]                      # the first window straddles the phase start
    if not rows:
        return {'windows': 0}
    med = lambda k: statistics.median(r[k] for r in rows)
    return {'windows': len(rows), 'rate_fps_median': med('rate_x10')/10, 'rate_fps_max': max(r['rate_x10'] for r in rows)/10,
            'gap_us_median': med('gap_us_avg'), 'gap_us_max': max(r['gap_us_max'] for r in rows),
            'lat_us_median': med('lat_us_avg'), 'lat_us_max': max(r['lat_us_max'] for r in rows),
            'commit_us_median': med('commit_us_avg'), 'cursor_flushes': sum(r['flush1'] + r['flush2'] for r in rows),
            'vrr_timestamps': sorted({r['vrr_timestamps'] for r in rows}),
            'intent_n': sum(r['intent_n'] for r in rows), 'frame_ns': max(r['frame_ns'] for r in rows), 'intent_lead_us_max': max(r['intent_lead_us_max'] for r in rows),
            'gap_us_p90': sorted(r['gap_us_avg'] for r in rows)[int(0.9*(len(rows)-1))]}


def hypr_socket():
    sig = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
    base = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid()))/'hypr'/(sig or '')
    return base/'.socket.sock'


def hypr(cmd, path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(str(path))
        s.sendall(cmd.encode())
        return s.recv(4096).decode(errors='replace')


def monitor():
    _, out, _ = run(['hyprctl', 'monitors', '-j'])
    row = json.loads(out)[0]
    return {k: row.get(k) for k in ('name', 'width', 'height', 'refreshRate', 'scale', 'vrr', 'currentFormat')}


def phase_idle(seconds, **_):
    time.sleep(seconds)
    return {}


def phase_cursor(seconds, mon, **_):
    path, w, h = hypr_socket(), int(mon['width']/mon['scale']), int(mon['height']/mon['scale'])
    start = hypr('j/cursorpos', path)
    moves, t_end, i = 0, time.monotonic() + seconds, 0
    while time.monotonic() < t_end:
        x = 100 + (i*7) % (w - 200)
        y = 100 + (i*3) % (h - 200)
        hypr('dispatch movecursor %d %d' % (x, y), path)
        moves += 1
        i += 1
        time.sleep(0.004)
    try:
        pos = json.loads(start)
        hypr('dispatch movecursor %d %d' % (pos['x'], pos['y']), path)
    except (ValueError, KeyError):
        pass
    return {'cursor_moves': moves, 'moves_per_s': round(moves/seconds, 1)}


def make_clip(tmp, size, fps):
    clip = Path(tmp)/('clip-%s-%d.mp4' % (size, fps))
    if not clip.exists():
        run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=%s:rate=%d' % (size, fps), '-t', '6',
             '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(clip)], timeout=120)
    return clip


def mpv_get(sock, prop):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(sock)
        s.sendall((json.dumps({'command': ['get_property', prop]}) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    for line in data.decode().splitlines():
        reply = json.loads(line)
        if 'error' in reply:
            return reply.get('data') if reply['error'] == 'success' else None
    return None


def phase_mpv(seconds, tmp, size, fps, **_):
    clip = make_clip(tmp, size, fps)
    sock = str(Path(tmp)/'mpv.sock')
    w, h = size.split('x')
    argv = ['mpv', '--no-config', '--vo=wlshm', '--hwdec=no', '--ao=null', '--audio=no', '--loop-file=inf',
            '--osd-level=0', '--wayland-app-id=m4-pacing', '--geometry=%sx%s' % (w, h), '--input-ipc-server=' + sock, str(clip)]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        time.sleep(0.5)
        path = hypr_socket()
        for cmd in ('dispatch setfloating class:m4-pacing', 'dispatch resizewindowpixel exact %s %s,class:m4-pacing' % (w, h),
                    'dispatch centerwindow'):
            try:
                hypr(cmd, path)
            except OSError:
                pass
        time.sleep(seconds)
        props = {}
        for p in MPV_PROPS:
            try:
                props[p] = mpv_get(sock, p)
            except (OSError, ValueError):
                props[p] = None
        try:
            clients = json.loads(run(['hyprctl', 'clients', '-j'])[1])
            win = next((c for c in clients if c.get('class') == 'm4-pacing'), {})
            props['window_size'] = win.get('size')
        except ValueError:
            pass
        return {'clip': {'size': size, 'fps': fps}, 'mpv': props, 'mpv_alive': proc.poll() is None}
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def phase_frameprobe(seconds, tmp, **_):
    """a trivial wl_shm client that redraws on every frame callback and records wp_presentation feedback
    (frameprobe-src next to this file). Builds it with cc + pkg-config wayland-client; skipped, with the reason, if
    that is missing. Its JSON is the best pacing signal: the compositor's own presentation timestamps and sequence."""
    src = Path(__file__).resolve().parent/'frameprobe-src'
    exe = Path(tmp)/'frameprobe'
    if not exe.exists():
        rc, flags, _ = run(['pkg-config', '--cflags', '--libs', 'wayland-client'])
        if rc:
            return {'frameprobe_error': 'pkg-config wayland-client missing'}
        cmd = ['cc', '-O2', '-o', str(exe), str(src/'frameprobe.c'), str(src/'xdg-shell-protocol.c'),
               str(src/'presentation-time-protocol.c'), '-I' + str(src), *flags.split(), '-lm']
        try:
            rc, out, err = run(cmd, timeout=120)
        except OSError as exc:
            return {'frameprobe_error': 'no C compiler: %s' % exc}
        if rc:
            return {'frameprobe_error': 'build failed: ' + err.strip()[-300:]}
    rc, out, err = run([str(exe), str(max(1, seconds - 3)), '256', '256'], timeout=seconds + 30)
    if rc:
        return {'frameprobe_error': 'rc %s: %s' % (rc, err.strip()[-200:])}
    try:
        return {'frameprobe': json.loads(out.strip().splitlines()[-1])}
    except (ValueError, IndexError):
        return {'frameprobe_error': 'unparsable output: ' + out.strip()[-200:]}


PHASES = (('idle', phase_idle, {}), ('cursor', phase_cursor, {}), ('frameprobe', phase_frameprobe, {}),
          ('mpv_shm_120fps_640x360', phase_mpv, {'size': '640x360', 'fps': 120}),
          ('mpv_shm_120fps_1280x720', phase_mpv, {'size': '1280x720', 'fps': 120}),
          ('mpv_shm_60fps_1280x720', phase_mpv, {'size': '1280x720', 'fps': 60}))


def main(argv):
    global PHASE_S, PHASES
    out_file = None
    it = iter(argv)
    for arg in it:
        if arg == '--phase-s':
            PHASE_S = int(next(it))
        elif arg == '--phases':
            names = next(it).split(',')
            unknown = [n for n in names if n not in {p[0] for p in PHASES}]
            if unknown:
                print('unknown phases: %s' % ','.join(unknown))
                return 2
            PHASES = tuple(p for p in PHASES if p[0] in names)
        elif arg == '--out':
            out_file = Path(next(it))
        else:
            print(__doc__)
            return 2
    if param('swap_stats') != 'Y':
        print('swap_stats is %r: turn it on first:\n  echo Y | sudo tee %s' % (param('swap_stats'), PARAMS/'swap_stats'))
        return 1
    mon = monitor()
    report = {'schema': 1, 'tool': 'display-pacing-shm', 'utc': time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()),
              'kernel': os.uname().release, 'monitor': mon,
              'params': {k: param(k) for k in ('flip_event_mode', 'swap_timestamps', 'swap_stats', 'cursor_plane')},
              'phase_s': PHASE_S, 'phases': {}}
    print('monitor %s @ %s Hz, flip_event_mode=%s swap_timestamps=%s' % (mon.get('name'), mon.get('refreshRate'),
          report['params']['flip_event_mode'], report['params']['swap_timestamps']))
    with tempfile.TemporaryDirectory(prefix='m4-pacing-') as tmp:
        for name, fn, kw in PHASES:
            if fn is phase_mpv:
                make_clip(tmp, kw['size'], kw['fps'])
            time.sleep(SETTLE_S)
            cursor = journal_cursor()
            extra = fn(PHASE_S, mon=mon, tmp=tmp, **kw)
            time.sleep(2.5)                  # let the last 2 s window land in the journal
            rows = stats_since(cursor)
            report['phases'][name] = {'summary': summarise(rows), 'windows': rows, **extra}
            s = report['phases'][name]['summary']
            print('%-26s windows=%-2s rate=%-6s lat_us=%-6s gap_max_us=%-7s %s' % (
                name, s.get('windows'), s.get('rate_fps_median'), s.get('lat_us_median'), s.get('gap_us_max'),
                json.dumps({k: extra.get('mpv', {}).get(k) for k in ('estimated-display-fps', 'frame-drop-count',
                                                                       'vo-delayed-frame-count', 'window_size')})
                if 'mpv' in extra else json.dumps({k: v for k, v in extra.items()})))
    OUTBOX.mkdir(exist_ok=True)
    out = out_file or OUTBOX/('display-pacing-shm-%s-flip%s.json' % (report['utc'], report['params']['flip_event_mode']))
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f, indent=1)
    print('report:', out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
