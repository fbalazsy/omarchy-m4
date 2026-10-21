#!/usr/bin/env python3
"""Boot milestones and segments, kernel start -> Hyprland desktop, from the journal.

Usage: boot-phases.py [-b ID]... [boot.jsonl]... [--json OUT]
  -b ID       read `journalctl -b ID -o json` (0 = this boot, -1 = the one before; works as the normal user)
  boot.jsonl  a saved `journalctl -b ID -o json` dump
With no source it reads -b 0. All times are seconds on the kernel-start monotonic clock. Read-only; standard library only.

Flow on aurora kernels: kernel -> appledrm takes the panel over from simpledrm -> basic.target -> m4-gpu (asahi) ->
m4-display -> SDDM -> autologin (or the greeter and a typed login) -> uwsm starts Hyprland ->
omarchy-shell (the bar) -> graphical-session.target.
"""
import json, re, subprocess, sys

MILESTONES = [  # (key, transport/identifier filter, regex) - first match wins
    ('run_init', 'kernel', r'^Run /init as init process'),
    ('root_mounted', 'kernel', r'EXT4-fs \(nvme0n1p\d+\): mounted filesystem'),
    ('pid1_banner', 'systemd', r'running in system mode'),
    ('journald_started', 'systemd-journald', r'^Journal started'),
    ('display_takeover', 'kernel', r'\[drm\] Initialized apple '),  # appledrm registered, simpledrm evicted
    ('display_fbcon', 'kernel', r'appledrmfb frame buffer device'),
    ('basic_target', 'systemd', r'^Reached target Basic System'),
    ('gpu_ready', None, r'^M4_GPU_READY'),
    ('display_ready', None, r'^M4_DISPLAY_READY'),
    ('graphical_target', 'systemd', r'^Reached target Graphical Interface'),
    ('startup_finished', 'systemd', r'^Startup finished in .*\(kernel\)'),  # PID 1's line, not the user manager's
    ('sddm_unit_started', 'systemd', r'^Started Simple Desktop Display Manager'),
    ('sddm_start', 'sddm', r'^Initializing'),
    ('greeter_session', 'sddm', r'^Greeter session started successfully'),
    ('greeter_gl', 'sddm-greeter-qt6', r'^OpenGL VENDOR'),
    ('autologin', 'sddm-helper', r'pam_unix\(sddm-autologin:session\): session opened'),
    ('login', 'sddm', r'^(Message received from greeter: Login|Authentication for user .* successful)'),
    ('user_session_started', 'sddm', r'^Session started true'),
    ('hyprland_start', None, r'^Welcome to Hyprland!'),
    ('hyprland_modeset', 'kernel', r'M4_DCP_MODESET rc=0'),  # the compositor's first modeset (fbcon keeps the boot mode)
    ('hyprland_ready', 'systemd', r'^Started Main service for Hyprland'),
    ('shell_loaded', 'omarchy-shell', r'Configuration Loaded'),
    ('graphical_session', 'systemd', r'^Reached target Current graphical user session'),
    ('wifi_connected', 'wpa_supplicant', r'CTRL-EVENT-CONNECTED'),
]
SEGMENTS = [  # (name, start, end, owner)
    ('kernel: start -> /init', None, 'run_init', 'kernel'),
    ('early userspace: /init -> journald', 'run_init', 'journald_started', 'initrd + PID 1'),
    ('sysinit: journald -> basic.target', 'journald_started', 'basic_target', 'systemd'),
    ('m4-gpu: basic.target -> GPU ready', 'basic_target', 'gpu_ready', 'm4-gpu.service'),
    ('m4-display: GPU ready -> display ready', 'gpu_ready', 'display_ready', 'm4-display.service'),
    ('SDDM unit: display ready -> sddm.service started', 'display_ready', 'sddm_unit_started',
     'systemd ordering (systemd-user-sessions waits for network.target)'),
    ('SDDM exec: sddm.service started -> sddm initializing', 'sddm_unit_started', 'sddm_start', 'sddm process start'),
    ('SDDM: start -> login (autologin or typed)', 'sddm_start', 'login', 'sddm (+ the user on the greeter)'),
    ('SDDM: login -> user session started', 'login', 'user_session_started', 'sddm-helper, PAM, logind'),
    ('session: uwsm + Hyprland init -> Hyprland ready', 'user_session_started', 'hyprland_ready', 'uwsm + Hyprland'),
    ('desktop: Hyprland ready -> graphical-session.target', 'hyprland_ready', 'graphical_session', 'uwsm + autostart'),
]


def parse(lines):
    rows = []
    for line in lines:
        e = json.loads(line)
        m = e.get('MESSAGE', '')
        if isinstance(m, list):
            m = bytes(m).decode('utf-8', 'replace')
        if not isinstance(m, str):
            continue
        t = e.get('_SOURCE_MONOTONIC_TIMESTAMP') or e.get('__MONOTONIC_TIMESTAMP')
        rows.append((int(t) / 1e6, e.get('_TRANSPORT', ''), e.get('SYSLOG_IDENTIFIER', ''), m))
    rows.sort(key=lambda r: r[0])
    return rows


def milestones(rows):
    out = {}
    for key, ident, rx in MILESTONES:
        r = re.compile(rx)
        for t, transport, sid, msg in rows:
            if ident == 'kernel' and transport != 'kernel':
                continue
            if ident not in (None, 'kernel') and sid != ident:
                continue
            if r.search(msg):
                out[key] = round(t, 6)
                break
    return out


def segments(ms):
    out = []
    for name, a, b, owner in SEGMENTS:
        ta = 0.0 if a is None else ms.get(a)
        tb = ms.get(b)
        out.append(dict(segment=name, owner=owner, start=ta, end=tb,
                        seconds=round(tb - ta, 3) if ta is not None and tb is not None else None))
    return out


def summarize(rows, label):
    ms = milestones(rows)
    kernel = next((m.group(1) for _, tr, _, msg in rows if tr == 'kernel'
                   for m in [re.match(r'Linux version (\S+)', msg)] if m), None)
    return dict(schema=2, label=label, kernel=kernel, milestones=ms, segments=segments(ms),
                login_mode='autologin' if 'autologin' in ms else 'greeter' if 'greeter_session' in ms else None,
                display_takeover_s=ms.get('display_takeover'), greeter_visible_s=ms.get('greeter_gl'),
                desktop_ready_s=ms.get('hyprland_ready'), shell_loaded_s=ms.get('shell_loaded'))


def report(r):
    print(f"# {r['label']}  kernel {r['kernel']}  login {r['login_mode']}")
    for s in r['segments']:
        sec = '   n/a' if s['seconds'] is None else f"{s['seconds']:6.3f}"
        print(f"{sec}  {s['segment']}")
    print(f"display takeover (appledrm): {r['display_takeover_s']}  |  greeter GL: {r['greeter_visible_s']}  |  "
          f"Hyprland ready: {r['desktop_ready_s']}  |  bar loaded: {r['shell_loaded_s']}")


def main():
    args = sys.argv[1:]
    out = None
    if '--json' in args:
        i = args.index('--json'); out = args[i + 1]; del args[i:i + 2]
    results = []
    while args or not results:
        a = args.pop(0) if args else '-b'
        if a == '-b':
            boot = args.pop(0) if args else '0'
            text = subprocess.run(['journalctl', '-b', boot, '-o', 'json', '--no-pager'], check=True,
                                  capture_output=True, text=True).stdout
            results.append(summarize(parse(text.splitlines()), f'boot {boot}'))
        else:
            with open(a, encoding='utf-8') as f:
                results.append(summarize(parse(f), a))
    for r in results:
        report(r)
    if out:
        with open(out, 'w') as f:
            json.dump(results if len(results) > 1 else results[0], f, indent=1); f.write('\n')


if __name__ == '__main__':
    main()
