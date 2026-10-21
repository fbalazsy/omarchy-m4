#!/usr/bin/python3 -B
"""Present-rate probe for the real display driver. Attended, user session, read-only.

Plays a generated test clip with mpv at a few window sizes, asks mpv (JSON IPC) how fast the compositor presents,
and writes one JSON report to ~/M4-Outbox. Needs mpv and ffmpeg (both on the Omarchy image). It changes no setting,
loads no module and needs no root.

  python3 -B /usr/lib/m4/tests/dcp/fpsprobe.py            # 640x360, 1280x720, 1920x1080, 6 s each
  python3 -B /usr/lib/m4/tests/dcp/fpsprobe.py 800x450 3  # one size, 3 s

The kernel side (the appledrm driver) logs M4_DCP_SWAP_STATS once per 2 s of presented frames; the report
carries the last windows (swap rate, gap between completions, submit-to-complete latency). Compare them with the
player's numbers: equal rates mean the compositor presents what the driver completes; a driver rate near the
player's and a latency near 1.5 frames points at the driver's completion timing.

How to read it (verdict in the report):
  full_rate        every size is near the monitor's rate: nothing to fix.
  render_bound     the rate falls as the window grows: the compositor/GPU cannot draw a full frame in time
                   (GPU work, damage from a software cursor, scale 2), not the display driver.
  half_rate_locked the rate sits near half the monitor's rate for every size: swaps complete one refresh late
                   (display-driver pacing, flip-event timing or VRR timestamps).
  mixed            neither pattern; send the report.
"""
from pathlib import Path
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import time

OUTBOX = Path.home()/'M4-Outbox'
SIZES = ('640x360', '1280x720', '1920x1080')
PROPS = ('estimated-display-fps', 'display-fps', 'vsync-jitter', 'vo-delayed-frame-count', 'mistimed-frame-count',
         'frame-drop-count', 'decoder-frame-drop-count')
SETTLE_S = 2.5
SAMPLE_EVERY_S = 0.5


def hypr_monitor(run=subprocess.run):
    """Refresh, scale, vrr and cursor facts of the first monitor, or {} when hyprctl is unavailable."""
    try:
        p = run(['hyprctl', 'monitors', '-j'], capture_output=True, text=True, timeout=10)
        row = json.loads(p.stdout)[0]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {}
    return {k: row.get(k) for k in ('name', 'width', 'height', 'refreshRate', 'scale', 'vrr', 'activelyTearing',
                                    'currentFormat') if k in row}


def kernel_facts(run=subprocess.run):
    """Cursor-plane and swap markers from this boot's kernel log (best effort)."""
    try:
        p = run(['journalctl', '-k', '-b', '-q', '--no-pager', '-o', 'cat'], capture_output=True, text=True, timeout=30)
        text = p.stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    stats = []
    for match in re.finditer(r'M4_DCP_SWAP_STATS window_ms=(\d+) swaps=(\d+) rate_x10=(\d+) gap_us_avg=(\d+) gap_us_max=(\d+) '
                             r'lat_us_avg=(\d+) lat_us_max=(\d+) vrr_timestamps=(\d)', text):
        window, swaps, rate10, gap_avg, gap_max, lat_avg, lat_max, vrr = (int(x) for x in match.groups())
        stats.append(dict(window_ms=window, swaps=swaps, rate_hz=rate10/10, gap_us_avg=gap_avg, gap_us_max=gap_max,
                          lat_us_avg=lat_avg, lat_us_max=lat_max, vrr_timestamps=bool(vrr)))
    return dict(cursor_plane='M4_DCP_CURSOR_PLANE enabled' in text,
                vrr_modeset=len(re.findall(r'select_mode\(\).*vrr:1', text)) > 0,
                swap_failed=len(re.findall(r'M4_DCP_SWAP_(?:FAILED|REFUSED)', text)),
                swap_stats_windows=len(stats), swap_stats_last=stats[-6:])


def summarize(samples):
    """samples: chronological list of dicts of PROPS values (numbers or None). Per property: median, p10, p90, last."""
    out = {}
    for name in PROPS:
        series = [s[name] for s in samples if isinstance(s.get(name), (int, float))]
        if not series:
            continue
        values = sorted(series)
        top = len(values)-1
        out[name] = dict(median=round(statistics.median(values), 3), p10=round(values[int(top*0.1)], 3),
                         p90=round(values[int(top*0.9+0.5)], 3), last=series[-1], n=len(values))
    return out


def verdict(results, refresh):
    """results: list of dict(size, area, fps). refresh: the monitor's refresh rate (Hz) or None."""
    rates = [r['fps'] for r in results if isinstance(r.get('fps'), (int, float)) and r['fps'] > 0]
    if len(rates) < 1 or not refresh:
        return 'unknown'
    top = max(rates)
    if top >= 0.9*refresh:
        return 'full_rate'
    ordered = sorted((r for r in results if r.get('fps')), key=lambda r: r['area'])
    if len(ordered) >= 3:
        falling = all(ordered[i]['fps'] >= ordered[i+1]['fps']-1.0 for i in range(len(ordered)-1))
        if falling and ordered[0]['fps']-ordered[-1]['fps'] >= 0.12*refresh:
            return 'render_bound'
    if all(0.42*refresh <= v <= 0.62*refresh for v in rates) and max(rates)-min(rates) <= 0.1*refresh:
        return 'half_rate_locked'
    return 'mixed'


class Mpv:
    """One mpv process with a JSON IPC socket."""
    def __init__(self, clip, size, seconds, runner=subprocess.Popen):
        self.dir = tempfile.mkdtemp(prefix='fpsprobe-')
        self.sock_path = os.path.join(self.dir, 'ipc')
        width, height = size.split('x')
        argv = ['mpv', '--no-config', '--vo=gpu-next', '--gpu-api=opengl', '--opengl-es=yes', '--hwdec=no',
                '--video-sync=display-resample', '--no-audio', '--loop-file=inf', '--no-osc', '--no-input-default-bindings',
                '--geometry=%sx%s' % (width, height), '--really-quiet', '--input-ipc-server='+self.sock_path, clip]
        self.proc = runner(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sock = None

    def connect(self, wait=10.0):
        end = time.monotonic()+wait
        while time.monotonic() < end:
            try:
                self.sock = socket.socket(socket.AF_UNIX)
                self.sock.settimeout(2.0)
                self.sock.connect(self.sock_path)
                return True
            except OSError:
                time.sleep(0.2)
        return False

    def get(self, name):
        self.sock.sendall((json.dumps(dict(command=['get_property', name]))+'\n').encode())
        buffer = b''
        while True:
            buffer += self.sock.recv(65536)
            for line in buffer.split(b'\n'):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if 'error' in row:
                    return row.get('data') if row['error'] == 'success' else None
            if len(buffer) > 1 << 20:
                return None

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            os.unlink(self.sock_path)
            os.rmdir(self.dir)
        except OSError:
            pass


def make_clip(path, size, fps=120, seconds=4):
    argv = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=%s:rate=%d' % (size, fps), '-t', str(seconds),
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(path)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120).returncode == 0


def probe_one(size, seconds, clip_dir, factory=Mpv, clip_maker=make_clip, sleep=time.sleep):
    width, height = (int(x) for x in size.split('x'))
    clip = Path(clip_dir)/('probe-%s.mkv' % size)
    if not clip.exists() and not clip_maker(clip, size):
        return dict(size=size, area=width*height, error='ffmpeg could not make the test clip')
    player = factory(str(clip), size, seconds)
    try:
        if not player.connect():
            return dict(size=size, area=width*height, error='mpv did not start (no IPC socket)')
        sleep(SETTLE_S)
        samples = []
        for _ in range(max(1, int(seconds/SAMPLE_EVERY_S))):
            samples.append({name: player.get(name) for name in PROPS})
            sleep(SAMPLE_EVERY_S)
    finally:
        player.close()
    summary = summarize(samples)
    fps = summary.get('estimated-display-fps', {}).get('median')
    return dict(size=size, area=width*height, fps=fps, summary=summary, samples=len(samples))


def main(argv=None, probe=probe_one, monitor=hypr_monitor, kernel=kernel_facts, now=time.strftime):
    argv = sys.argv[1:] if argv is None else argv
    sizes = (argv[0],) if argv else SIZES
    seconds = float(argv[1]) if len(argv) > 1 else 6.0
    if not all(re.fullmatch(r'\d{2,5}x\d{2,5}', s) for s in sizes) or not 1 <= seconds <= 60:
        print('usage: fpsprobe.py [WxH [seconds]]')
        return 2
    if os.geteuid() == 0:
        print('run as the desktop user, inside the desktop session (not with sudo)')
        return 2
    before = monitor()
    results = []
    with tempfile.TemporaryDirectory(prefix='fpsprobe-clips-') as clip_dir:
        for size in sizes:
            row = probe(size, seconds, clip_dir)
            results.append(row)
            print('%-10s %s' % (size, ('%.1f fps' % row['fps']) if row.get('fps') else row.get('error', 'no data')))
    refresh = (before.get('refreshRate') if before else None)
    report = dict(schema=1, utc=now('%Y%m%dT%H%M%SZ', time.gmtime()), monitor=before, kernel=kernel(), results=results,
                  verdict=verdict(results, refresh), note=__doc__.split('How to read it')[1].strip() if __doc__ else '')
    OUTBOX.mkdir(mode=0o700, exist_ok=True)
    path = OUTBOX/('fpsprobe-%s.json' % report['utc'])
    path.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    print('monitor refresh: %s Hz   verdict: %s' % (refresh, report['verdict']))
    print('saved: '+str(path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
