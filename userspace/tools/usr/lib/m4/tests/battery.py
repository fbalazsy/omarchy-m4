#!/usr/bin/python3 -B
"""m4-test battery: read-only readout of the SMC battery (power_supply macsmc-battery). Passes when the fields read and are sane."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '/usr/lib/m4')
import m4state  # noqa: E402

SUPPLY = Path('/sys/class/power_supply/macsmc-battery')
FIELDS = ('status', 'capacity', 'voltage_now', 'current_now', 'energy_now', 'energy_full', 'energy_full_design', 'cycle_count',
          'temp', 'health', 'charge_control_start_threshold', 'charge_control_end_threshold', 'present', 'technology')


def main():
    result = dict(probe='battery', supply=str(SUPPLY))
    if not SUPPLY.is_dir():
        result.update(passed=False, reason='no macsmc-battery power supply (is macsmc_power loaded?)')
    else:
        values = {}
        for name in FIELDS:
            try:
                values[name] = (SUPPLY/name).read_text().strip()
            except OSError:
                values[name] = None
        try:
            capacity = int(values['capacity'])
        except (TypeError, ValueError):
            capacity = -1
        result.update(values=values, passed=0 <= capacity <= 100 and values['status'] is not None)
        if not result['passed']:
            result['reason'] = 'capacity or status unreadable'
    m4state.save(result, 'battery.json')
    print(json.dumps(result, indent=1))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
