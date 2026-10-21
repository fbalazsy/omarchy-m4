#!/usr/bin/python3 -B
"""m4-test charge: attended round trip of the battery charge limit (80 % and back to the original), read back each time.

The distribution's macsmc-battery-charge-control-end-threshold units persist every change to /etc/udev/macsmc-battery.conf, so this test
puts the original thresholds back before it exits (also on SIGTERM) and compares that file before and after. Run as root; one run per boot."""
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, '/usr/lib/m4')
import m4host  # noqa: E402
import m4state  # noqa: E402

BATTERY = Path('/sys/class/power_supply/macsmc-battery')
PLATFORM = Path('/sys/bus/platform/devices/macsmc-power')
PERSISTENCE = Path('/etc/udev/macsmc-battery.conf')
FIELDS = ('charge_control_start_threshold', 'charge_control_end_threshold')
SETTLE_S = 3.0


def pair():
    return tuple(int((BATTERY/f).read_text().strip()) for f in FIELDS)


def put(values):
    """Write a (start, end) pair; the order that keeps start <= end at every step."""
    start, end = values
    now_start, now_end = pair()
    order = (1, 0) if end >= now_end else (0, 1)
    for index in order:
        (BATTERY/FIELDS[index]).write_text('%d\n' % values[index])
    return pair()


def persisted():
    try:
        return PERSISTENCE.read_bytes()
    except OSError:
        return None


def main():
    m4host.need(os.geteuid() == 0 and len(sys.argv) == 1, 'run as root without arguments (m4-test charge)')
    m4host.need(BATTERY.is_dir(), 'no macsmc-battery power supply (is macsmc_power loaded?)')
    identity = m4host.boot_identity()
    result = dict(probe='charge', boot_id=m4state.boot(), method=(PLATFORM/'charge_limit_method').read_text().strip()
                  if (PLATFORM/'charge_limit_method').exists() else None, steps=[], passed=False)
    original = pair()
    before = persisted()
    result['original'] = original
    target = (100, 100) if original[1] != 100 else (75, 80)
    restored = []

    def restore(*_):
        if not restored:
            restored.append(put(original))

    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(1)))
    try:
        for want in (target, original):
            got = put(want)
            result['steps'].append(dict(wrote=want, read=got, ok=got == want))
            time.sleep(1)
    finally:
        restore()
    time.sleep(SETTLE_S)
    after = persisted()
    result.update(final=pair(), restored=pair() == original, persisted_unchanged=before == after,
                  cpu_identity_unchanged=m4host.same_cpu_identity(m4host.boot_identity(), identity))
    result['passed'] = all(s['ok'] for s in result['steps']) and result['restored'] and result['persisted_unchanged']
    m4state.save(result, 'charge.json')
    print(json.dumps(result, indent=1, default=str))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print('m4-test charge refused: %s' % error, file=sys.stderr)
        sys.exit(1)
