#!/usr/bin/python3 -B
"""m4-als-brightness: ambient-light screen and keyboard brightness (user service, on by default).

Reads the AOP light sensor (IIO device `aop-sensors-als`, `in_illuminance_input` in lux), smooths it, maps it to a
screen brightness (logarithmic between two lux points) and switches the keyboard backlight on in the dark and off
again in the light, with hysteresis on both. Never starts the AOP: when the IIO device is not there (a kernel without
the AOP at boot, or `blacklist aop`) it exits 0 after a wait and does nothing.

The package enables it for every user (graphical-session.target.wants); optional key=value overrides in
/etc/m4/als-brightness.conf, see DEFAULTS.
Off:      systemctl --user mask --now m4-als-brightness.service      (back on: systemctl --user unmask m4-als-brightness.service)

Brightness is written through logind (`org.freedesktop.login1.Session.SetBrightness`, no root needed for the active
session); a writable sysfs file is the fallback. A manual change of the screen brightness pauses the automatic
control for `override_s` seconds. The keyboard backlight is only touched when the light crosses a threshold: the idle
dimmer (`m4-kbd-idle`) is never fought.
"""
import math
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path('/etc/m4/als-brightness.conf')
IIO_ROOT = Path('/sys/bus/iio/devices')
BACKLIGHT_ROOT = Path('/sys/class/backlight')
LEDS_ROOT = Path('/sys/class/leds')
IIO_NAME = 'aop-sensors-als'
KBD_LED = 'apple::kbd_backlight'
DEFAULTS = dict(
    poll_s=2.0,            # sensor poll interval
    smooth=0.25,           # exponential smoothing factor in the log domain (0..1)
    lux_dark=3.0,          # at or below this: min_percent
    lux_bright=800.0,      # at or above this: max_percent
    min_percent=12.0,
    max_percent=100.0,
    hysteresis_percent=5.0,  # the target must differ from the applied level by this much ...
    hold_s=6.0,            # ... for this long before the screen moves
    step_percent=4.0,      # at most this much per poll while moving
    override_s=600.0,      # pause after a manual change
    manual_delta_percent=3.0,
    kbd_on_lux=12.0,       # keyboard backlight on below this ...
    kbd_off_lux=40.0,      # ... and off again above this
    kbd_percent=40.0,
    wait_device_s=600.0,   # how long to wait for the sensor before giving up (exit 0): the AOP registers it during boot,
                           # a kernel without the AOP at boot (the rollbacks) never does
)


def parse_config(text):
    """key=value lines (# comments); unknown keys and bad numbers are ignored. Returns a dict of floats."""
    out = {}
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if '=' not in line:
            continue
        key, value = (x.strip() for x in line.split('=', 1))
        if key in DEFAULTS:
            try:
                number = float(value)
            except ValueError:
                continue
            if math.isfinite(number):
                out[key] = number
    return out


def load_config(path=None):
    cfg = dict(DEFAULTS)
    try:
        cfg.update(parse_config((path or CONFIG).read_text()))
    except OSError:
        pass
    cfg['smooth'] = min(1.0, max(0.01, cfg['smooth']))
    cfg['min_percent'] = min(100.0, max(1.0, cfg['min_percent']))
    cfg['max_percent'] = min(100.0, max(cfg['min_percent'], cfg['max_percent']))
    cfg['lux_bright'] = max(cfg['lux_dark'] + 1.0, cfg['lux_bright'])
    cfg['poll_s'] = max(0.5, cfg['poll_s'])
    return cfg


def target_percent(lux, cfg):
    """Screen brightness percent for a lux value: logarithmic between lux_dark and lux_bright."""
    lo, hi = cfg['lux_dark'], cfg['lux_bright']
    if lux <= lo:
        return cfg['min_percent']
    if lux >= hi:
        return cfg['max_percent']
    x = (math.log(1.0 + lux - lo)) / math.log(1.0 + hi - lo)
    return cfg['min_percent'] + x * (cfg['max_percent'] - cfg['min_percent'])


class Control:
    """Pure decision logic: feed (now, lux, screen_percent_now, kbd_value_now); get the actions to take."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.smoothed = None          # log(1 + lux)
        self.applied = None           # last screen percent this service wrote
        self.pending_since = None
        self.paused_until = 0.0
        self.kbd_state = None         # True = on by us, False = off, None = unknown

    @property
    def lux(self):
        return None if self.smoothed is None else math.exp(self.smoothed) - 1.0

    def update(self, now, lux, screen_percent, kbd_value):
        """Returns dict(screen=<percent or None>, kbd=<percent or None>)."""
        cfg = self.cfg
        lux = max(0.0, float(lux))
        x = math.log(1.0 + lux)
        self.smoothed = x if self.smoothed is None else self.smoothed + cfg['smooth']*(x - self.smoothed)
        out = dict(screen=None, kbd=None)
        smooth_lux = self.lux
        # keyboard: act only on a threshold crossing, and only if the LED is not already where we want it
        if smooth_lux < cfg['kbd_on_lux'] and self.kbd_state is not True:
            self.kbd_state = True
            if kbd_value is not None and kbd_value == 0:
                out['kbd'] = cfg['kbd_percent']
        elif smooth_lux > cfg['kbd_off_lux'] and self.kbd_state is not False:
            self.kbd_state = False
            if kbd_value is not None and kbd_value > 0:
                out['kbd'] = 0.0
        # screen
        if screen_percent is None:
            return out
        if self.applied is not None and abs(screen_percent - self.applied) >= cfg['manual_delta_percent']:
            self.paused_until = now + cfg['override_s']     # the user moved it
            self.applied = None
            self.pending_since = None
        if now < self.paused_until:
            return out
        target = target_percent(smooth_lux, cfg)
        current = screen_percent
        if abs(target - current) < cfg['hysteresis_percent']:
            self.pending_since = None
            return out
        if self.pending_since is None:
            self.pending_since = now
        if now - self.pending_since < cfg['hold_s'] and self.applied is not None:
            return out
        step = cfg['step_percent']
        nxt = current + max(-step, min(step, target - current))
        nxt = min(cfg['max_percent'], max(cfg['min_percent'], nxt))
        self.applied = nxt
        out['screen'] = nxt
        return out


class System:
    """Sysfs and logind access; the paths are attributes so that tests can point them at a temporary tree."""
    iio_root = IIO_ROOT
    backlight_root = BACKLIGHT_ROOT
    leds_root = LEDS_ROOT

    def __init__(self):
        self.bus = []

    def als_path(self):
        try:
            entries = sorted(self.iio_root.iterdir())
        except OSError:
            return None
        for entry in entries:
            try:
                if (entry/'name').read_text().strip() == IIO_NAME:
                    return entry/'in_illuminance_input'
            except OSError:
                continue
        return None

    def read_lux(self, path):
        try:
            return float(path.read_text().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _read_int(path):
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return None

    def screen(self):
        """(name, percent, max) of the first backlight device, or None."""
        try:
            entries = sorted(self.backlight_root.iterdir())
        except OSError:
            return None
        for entry in entries:
            value, top = self._read_int(entry/'brightness'), self._read_int(entry/'max_brightness')
            if value is not None and top:
                return entry.name, 100.0*value/top, top
        return None

    def keyboard(self):
        path = self.leds_root/KBD_LED
        value, top = self._read_int(path/'brightness'), self._read_int(path/'max_brightness')
        if value is None or not top:
            return None
        return KBD_LED, value, top

    def set_brightness(self, subsystem, name, value):
        """logind first (no root), then the sysfs file. Returns True when one of them took it."""
        value = int(value)
        try:
            p = subprocess.run(['/usr/bin/busctl', '--system', 'call', 'org.freedesktop.login1',
                                '/org/freedesktop/login1/session/auto', 'org.freedesktop.login1.Session',
                                'SetBrightness', 'ssu', subsystem, name, str(value)],
                               capture_output=True, timeout=10)
            if p.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        base = self.backlight_root if subsystem == 'backlight' else self.leds_root
        try:
            (base/name/'brightness').write_text('%d\n' % value)
            return True
        except OSError:
            return False


def run(system, cfg, say, sleep=time.sleep, clock=time.monotonic, once=False):
    deadline = clock() + cfg['wait_device_s']
    path = system.als_path()
    while path is None:
        if clock() >= deadline:
            say('m4-als-brightness: no %s IIO device (the AOP was not started at boot); nothing to do' % IIO_NAME)
            return 0
        sleep(5.0)
        path = system.als_path()
    say('m4-als-brightness: sensor %s' % path)
    control = Control(cfg)
    while True:
        lux = system.read_lux(path)
        if lux is not None:
            screen, kbd = system.screen(), system.keyboard()
            action = control.update(clock(), lux, screen[1] if screen else None, kbd[1] if kbd else None)
            if action['screen'] is not None and screen:
                value = round(action['screen']*screen[2]/100.0)
                if system.set_brightness('backlight', screen[0], value):
                    say('m4-als-brightness: lux %.0f -> screen %.0f %%' % (control.lux, action['screen']))
            if action['kbd'] is not None and kbd:
                value = round(action['kbd']*kbd[2]/100.0)
                if system.set_brightness('leds', kbd[0], value):
                    say('m4-als-brightness: lux %.0f -> keyboard %.0f %%' % (control.lux, action['kbd']))
        if once:
            return 0
        sleep(cfg['poll_s'])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in ('--once',):
        print('usage: m4-als-brightness.py [--once]')
        return 1
    try:
        return run(System(), load_config(), lambda text: print(text, flush=True), once=bool(argv))
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
