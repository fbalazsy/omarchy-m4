#!/usr/bin/python3 -B
"""usage: display-pacing.py   (run it as `sudo m4-test display-pacing`; no options)

m4-test display-pacing: compare appledrm's page-flip event modes under the CPU-drawn (wl_shm) workload.

Root, attended, about 5 minutes, one run per boot, not within 3 minutes of power-on. It does not load or unload
anything. It writes three runtime parameters of the loaded appledrm (swap_stats=Y, flip_event_mode=0/1/4) and puts
every one back, also on SIGTERM and on any error. The workload is display-pacing-shm.py run as the desktop user:
only CPU-drawn clients, so Hyprland stays the only GPU client.

Modes: 0 = estimated time, sequence 0; 1 = raw completion time; 4 = vblank clock (time of the
firmware's swap_complete_intent_gated callback when it arrives, else the completion time; sequence counted in frame
periods). Result: /run/m4/state/<boot>/display-pacing.json and one printed table.
Exit: 0 ran, 1 not run (nothing changed), 2 ran and could not restore a parameter.
"""
from pathlib import Path
import json
import os
import pwd
import signal
import subprocess
import sys
import time

BASE = Path('/usr/lib/m4')
PARAMS = Path('/sys/module/appledrm/parameters')
STATE = Path('/run/m4/state')
MODES = (0, 1, 4)
PHASES = 'frameprobe,mpv_shm_120fps_1280x720,cursor'
PHASE_S = 10
MIN_UPTIME_S = 180
REF_PHASE = 'mpv_shm_120fps_1280x720'


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def uptime():
    return float(Path('/proc/uptime').read_text().split()[0])


def fail(msg, code=1):
    print('display-pacing: ' + msg, file=sys.stderr)
    return code


def has_vblank_clock():
    """The display module's file carries the vblank-clock mode strings. An unresolvable module does not refuse on its own."""
    sys.path.insert(0, '/usr/lib/m4')
    import m4host
    try:
        return b'intent_lead_us_avg' in m4host.read_module('appledrm', limit=16*1024*1024)
    except (RuntimeError, OSError):
        return True


def hypr_signature(uid):
    base = Path('/run/user/%d/hypr' % uid)
    best = None
    for d in base.glob('*/.socket.sock'):
        if best is None or d.stat().st_mtime > best.stat().st_mtime:
            best = d
    return best.parent.name if best else None


def read_param(name):
    return (PARAMS/name).read_text().strip()


def write_param(name, value):
    (PARAMS/name).write_text(str(value)+'\n')


def summarize(report, ref=REF_PHASE):
    """report: {mode: {phase: summary}} -> verdict dict. the frameprobe phase's presented_per_s (the compositor's
    own presentation feedback) is used when present, else the kernel swap rate of the reference phase."""
    def rate(mode, phase):
        row = report.get(mode, {}).get(phase) or {}
        fp = row.get('frameprobe') or {}
        if fp.get('presented_per_s'):
            return fp['presented_per_s']
        return row.get('rate_fps_median')
    if any((((report.get(m, {}).get('frameprobe') or {}).get('frameprobe')) or {}).get('presented_per_s') for m in report):
        ref = 'frameprobe'
    base, vbl = rate('0', ref), rate('4', ref)
    raw = rate('1', ref)
    intent = sum((report.get('4', {}).get(p) or {}).get('intent_n', 0) for p in report.get('4', {}))
    clock = any((report.get('4', {}).get(p) or {}).get('frame_ns', 0) for p in report.get('4', {}))
    if not clock:
        verdict = 'module_without_vblank_clock'
    elif not base or not vbl:
        verdict = 'no_data'
    elif vbl >= 1.15*base:
        verdict = 'vblank_clock_faster'
    elif vbl <= 0.9*base:
        verdict = 'vblank_clock_slower'
    else:
        verdict = 'no_change'
    return dict(verdict=verdict, ref_phase=ref, rate_mode0=base, rate_mode1=raw, rate_mode4=vbl,
                intent_callbacks_seen_in_mode4=intent,
                note=('the firmware never sent swap_complete_intent_gated: mode 4 used completion time plus the '
                      'frame-period sequence only' if clock and not intent else ''))


def run_mode(mode, user, sig, outdir, runner=subprocess.run):
    out = outdir/('shm-flip%s.json' % mode)
    env = ['env', 'XDG_RUNTIME_DIR=/run/user/%d' % user.pw_uid, 'HYPRLAND_INSTANCE_SIGNATURE=' + sig,
           'WAYLAND_DISPLAY=wayland-1', 'HOME=' + user.pw_dir, 'PATH=/usr/bin:/bin']
    argv = ['/usr/bin/runuser', '-u', user.pw_name, '--', *env, '/usr/bin/python3', '-B',
            str(BASE/'dcp/display-pacing-shm.py'), '--phase-s', str(PHASE_S), '--phases', PHASES, '--out', str(out)]
    p = runner(argv, capture_output=True, text=True, timeout=240)
    sys.stdout.write(p.stdout)
    if p.returncode or not out.exists():
        return None, 'workload rc=%s: %s' % (p.returncode, (p.stderr or p.stdout).strip()[-300:])
    return json.loads(out.read_text()), ''


def main(argv, runner=subprocess.run):
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    again = '--again' in argv
    if os.geteuid() != 0:
        return fail('run it through m4-test (root)')
    if not PARAMS.is_dir() or not all((PARAMS/n).exists() for n in ('swap_stats', 'flip_event_mode')):
        return fail('appledrm with the flip_event_mode parameter is not loaded (this needs the vblank-clock module)')
    if not has_vblank_clock():
        return fail('the appledrm module has no vblank-clock mode (intent_lead_us_avg)')
    if uptime() < MIN_UPTIME_S:
        return fail('wait: the test starts nothing within %d s of power-on (%.0f s so far)' % (MIN_UPTIME_S, uptime()))
    boot = boot_id()
    state = STATE/boot
    state.mkdir(parents=True, exist_ok=True)
    marker = state/'display-pacing.done'
    if marker.exists() and not again:
        return fail('already ran in this boot (%s); --again to repeat' % marker)
    user = pwd.getpwuid(1000)
    sig = hypr_signature(user.pw_uid)
    if not sig:
        return fail('no Hyprland session of %s found' % user.pw_name)

    saved = {n: read_param(n) for n in ('swap_stats', 'flip_event_mode')}
    restore_failed = []

    def restore(*_):
        for n, v in saved.items():
            try:
                write_param(n, v)
            except OSError as exc:
                restore_failed.append('%s: %s' % (n, exc))
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(1)))
    outdir = Path('/run/m4/display-pacing-' + boot[:8])
    outdir.mkdir(mode=0o700, exist_ok=True)
    os.chown(outdir, user.pw_uid, user.pw_gid)
    result = dict(schema=1, boot_id=boot, utc=time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()),
                  kernel=os.uname().release, params_before=saved, modes={}, errors=[])
    try:
        marker.write_text(result['utc']+'\n')
        write_param('swap_stats', 'Y')
        for mode in MODES:
            write_param('flip_event_mode', mode)
            time.sleep(1)
            report, err = run_mode(mode, user, sig, outdir, runner)
            if report is None:
                result['errors'].append('mode %d: %s' % (mode, err))
                continue
            result['modes'][str(mode)] = {
                name: dict(ph['summary'], **{k: ph[k] for k in ('frameprobe', 'frameprobe_error') if k in ph},
                           window_size=(ph.get('mpv') or {}).get('window_size'))
                for name, ph in report['phases'].items()}
    finally:
        restore()
    result['params_after'] = {n: read_param(n) for n in saved}
    result.update(summarize(result['modes']))
    result['restore_failed'] = restore_failed
    text = json.dumps(result, indent=1, sort_keys=True)+'\n'
    (state/'display-pacing.json').write_text(text)
    # native bug 6: /run is lost at the reboot that follows; keep a copy where it survives
    kept = Path('/var/lib/m4/results')/('display-pacing-%s-%s.json' % (result['utc'], boot[:8]))
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(text)
        print('kept: %s' % kept)
    except OSError as error:
        result['errors'].append('could not keep the result: %s' % error)
    for mode, phases in result['modes'].items():
        for name, s in phases.items():
            fp = s.get('frameprobe') or {}
            print('mode %s %-26s rate=%-6s lat_us=%-6s gap_us_max=%-8s intent_n=%s%s' % (
                mode, name, s.get('rate_fps_median'), s.get('lat_us_median'), s.get('gap_us_max'), s.get('intent_n'),
                ('  presented/s=%s seq_zero=%s seq_step1=%s hist=%s' % (
                    fp.get('presented_per_s'), fp.get('seq_zero'), fp.get('seq_step1'),
                    fp.get('interval_hist_refresh_units'))) if fp else ''))
    print('verdict: %s (mode0 %s, mode1 %s, mode4 %s fps on %s)' % (
        result['verdict'], result['rate_mode0'], result['rate_mode1'], result['rate_mode4'], REF_PHASE))
    if result['note']:
        print(result['note'])
    print('result: %s' % (state/'display-pacing.json'))
    if restore_failed:
        return fail('could not restore: ' + '; '.join(restore_failed), 2)
    return 0 if not result['errors'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
