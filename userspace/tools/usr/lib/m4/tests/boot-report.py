#!/usr/bin/python3 -B
"""m4-test boot: how this boot went: systemd-analyze, the M4 units, the GPU/display outcome, the boot counter and the loader's reason."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '/usr/lib/m4')
import m4state  # noqa: E402

UNITS = ('m4-boot-pending', 'm4-boot-ok', 'm4-gpu', 'm4-display', 'm4-kbd-idle', 'm4-bt-keyshare', 'm4-bt-connect', 'display-manager')


def text(argv, timeout=20):
    return m4state.command(argv, timeout=timeout, stdout_limit=6000).get('stdout', '')


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def main():
    units = {}
    for u in UNITS:
        out = text(['/usr/bin/systemctl', 'show', '-p', 'ActiveState', '-p', 'Result', '-p', 'ExecMainStatus', u + '.service'])
        units[u] = dict(l.split('=', 1) for l in out.splitlines() if '=' in l)
    result = dict(probe='boot', analyze=text(['/usr/bin/systemd-analyze', 'time']).strip(),
                  blame=text(['/usr/bin/systemd-analyze', 'blame']).splitlines()[:12], units=units,
                  gpu=dict(ready=read('/run/m4/gpu-ready'), failed=read('/run/m4/gpu-failed')),
                  display=dict(ready=read('/run/m4/display-ready'), failed=read('/run/m4/display-failed')),
                  boot_status=text(['/usr/bin/m4-boot', 'status']).splitlines(), passed=True)
    m4state.save(result, 'boot.json')
    print(json.dumps(result, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
