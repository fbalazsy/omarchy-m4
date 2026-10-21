#!/usr/bin/python3 -B
"""Passive keyboard-backlight check (`m4-test kbd-backlight`).

The keyboard light is an ordinary LED class device (apple::kbd_backlight, driver leds_pwm on pwm_apple).
This script owns nothing: it looks at the device, reads the driver's completion witness when the private
leds_pwm build provides one, and writes the *current* level back once as a set/readback round trip.
It never raises the light, never loads or unloads a module and leaves no lease behind.

Exit 0: PASS_KBD_BACKLIGHT_PASSIVE. Exit 1: KBD_BACKLIGHT_ABSENT or KBD_BACKLIGHT_FAIL (JSON on stdout).
"""
from pathlib import Path
import glob, json, os, sys

WITNESS = ('schema', 'sequence', 'brightness', 'callback_error', 'hardware_error', 'period', 'duty_cycle',
           'enable', 'polarity')
PERIOD_NS = 40000      # device tree: pwms = <&pwm0 0 40000>
CLOCK_HZ = 24000000    # device tree: /clock-kbd-pwm clock-frequency


def expected_duty(brightness, maximum, period=PERIOD_NS):
    """Duty the Apple PWM reports back for a LED level (ticks are rounded down, get_state rounds up)."""
    requested = period * brightness // maximum
    ticks = CLOCK_HZ * requested // 1000000000
    return -(-ticks * 1000000000 // CLOCK_HZ)


def read_int(path):
    return int(Path(path).read_text().strip())


def read_witness(led):
    path = Path(led) / 'm4_pwm_state'
    if not path.exists():
        return None
    words = path.read_text().split()
    if len(words) != len(WITNESS) or not all(w.lstrip('-').isdigit() for w in words):
        raise ValueError('malformed completion witness: ' + repr(words)[:200])
    return dict(zip(WITNESS, map(int, words)))


def witness_problems(w, brightness, maximum):
    """Return a list of reasons the witness does not describe a healthy LED at `brightness`."""
    problems = []
    if w['schema'] != 1:
        problems.append('schema')
    if w['callback_error'] != 0 or w['hardware_error'] != 0:
        problems.append('driver error')
    if w['polarity'] != 0:
        problems.append('polarity')
    if w['brightness'] != brightness:
        problems.append('witness brightness %d != sysfs %d' % (w['brightness'], brightness))
    if w['period'] != PERIOD_NS:
        problems.append('period %d' % w['period'])
    step = PERIOD_NS // maximum + 1
    if abs(w['duty_cycle'] - expected_duty(brightness, maximum)) > step:
        problems.append('duty %d does not match level %d' % (w['duty_cycle'], brightness))
    if w['enable'] not in (0, 1) or (w['enable'] == 0 and w['duty_cycle'] != 0):
        problems.append('enable/duty combination')
    return problems


def find_led(sysfs):
    # Same lookup Omarchy's omarchy-brightness-keyboard uses: first *kbd_backlight* in /sys/class/leds.
    found = sorted(glob.glob(str(Path(sysfs) / 'class/leds/*kbd_backlight*')))
    return Path(found[0]) if found else None


def check(sysfs='/sys', root='/', write=None):
    """Run the check against a (possibly fake) sysfs root. Returns the report dict."""
    sysfs, root = Path(sysfs), Path(root)
    report = dict(status='KBD_BACKLIGHT_FAIL', passed=False, safe_to_continue=True, hardware_command_issued=False,
                  checks={}, problems=[])
    boot_id = root / 'proc/sys/kernel/random/boot_id'
    if boot_id.exists():
        report['boot_id'] = boot_id.read_text().strip()
    report['modules'] = {m: (sysfs / 'module' / m).exists() for m in ('pwm_apple', 'leds_pwm')}
    report['pwm_chips'] = sorted(p.name for p in (sysfs / 'class/pwm').glob('pwmchip*')) if (sysfs / 'class/pwm').is_dir() else []
    led = find_led(sysfs)
    if led is None:
        report.update(status='KBD_BACKLIGHT_ABSENT', led=None)
        report['problems'].append('no /sys/class/leds/*kbd_backlight* device; modules/PWM state recorded above')
        return report
    report['led'] = led.name
    checks = report['checks']
    try:
        maximum, brightness = read_int(led / 'max_brightness'), read_int(led / 'brightness')
        checks['max_brightness_positive'] = maximum > 0
        checks['brightness_in_range'] = 0 <= brightness <= maximum
        report.update(max_brightness=maximum, brightness=brightness)
        provider = (led / 'device').resolve()
        report['provider'] = provider.name
        w = read_witness(led)
        report['witness_available'] = w is not None
        if w is not None:
            report['witness'] = w
            problems = witness_problems(w, brightness, maximum) if maximum > 0 else ['max_brightness is not positive']
            checks['witness_consistent'] = not problems
            report['problems'] += problems
        if write is None:
            write = os.geteuid() == 0
        if write and all(checks.values()):
            # One round trip at the level the LED already has: no visible change, same code path as a key press.
            (led / 'brightness').write_text('%d\n' % brightness)
            report['hardware_command_issued'] = True
            again = read_int(led / 'brightness')
            checks['round_trip_level'] = again == brightness
            if w is not None:
                after = read_witness(led)
                report['witness_after'] = after
                problems = witness_problems(after, again, maximum)
                checks['round_trip_witness'] = not problems and after['sequence'] >= w['sequence']
                report['problems'] += problems
        else:
            report['round_trip'] = 'skipped'
        state = sorted(glob.glob(str(root / 'var/lib/systemd/backlight/*kbd_backlight*')))
        report['systemd_backlight_state_files'] = [Path(p).name for p in state]
    except (OSError, ValueError) as error:
        checks['readable'] = False
        report['problems'].append(str(error)[:300])
    report['passed'] = bool(checks) and all(checks.values())
    if report['passed']:
        report['status'] = 'PASS_KBD_BACKLIGHT_PASSIVE'
    return report


def main(argv):
    write = None
    if '--read-only' in argv:
        write = False
    report = check(write=write)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
