#!/usr/bin/python3 -B
"""m4-test bluetooth: read-only state of the Bluetooth controller and the paired devices as BlueZ reports them.

Passes when bluetoothd answers and a controller is present and powered. It never reads link keys: the shared key file is only stat()ed."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, '/usr/lib/m4')
import m4state  # noqa: E402

SHARED = Path('/var/lib/m4-shared/bluetooth')


def ctl(*args):
    return m4state.command(['/usr/bin/bluetoothctl', *args], timeout=10, stdout_limit=8000)


def main():
    result = dict(probe='bluetooth')
    show = ctl('show')
    fields = {}
    for line in show.get('stdout', '').splitlines():
        key, _, value = line.strip().partition(': ')
        if value and key in ('Alias', 'Powered', 'Discoverable', 'Pairable', 'Discovering', 'Modalias'):
            fields[key] = value
    devices = [l.split(' ', 2)[1:] for l in ctl('devices').get('stdout', '').splitlines() if l.startswith('Device ')]
    connected = [d[0] for d in devices if 'Connected: yes' in ctl('info', d[0]).get('stdout', '')]
    result.update(controller=fields, devices=len(devices), connected=connected,
                  hci=sorted(p.name for p in Path('/sys/class/bluetooth').glob('hci*')) if Path('/sys/class/bluetooth').is_dir() else [],
                  shared_key_files=sorted(p.name for p in SHARED.glob('*.json')) if SHARED.is_dir() else None,
                  passed=fields.get('Powered') == 'yes')
    if not result['passed']:
        result['reason'] = 'no powered controller (is bluetooth.service running and hci_bcm4377 loaded?)'
    m4state.save(result, 'bluetooth.json')
    print(json.dumps(result, indent=1))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main() if os.geteuid() == 0 else 2)
