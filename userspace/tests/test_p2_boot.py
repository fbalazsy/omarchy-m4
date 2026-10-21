#!/usr/bin/python3 -I
"""P2 boot time: the systemd mount rate-limit override, the console size, tools/boot-phases.py.   python3 -B -I test_p2_boot.py   (tree: ../support)"""
import configparser
import importlib.util
import json
import os
import unittest
from pathlib import Path

TREES = Path(os.environ.get('M4_TREES', Path(__file__).resolve().parents[1]))
CONF = TREES/'support/usr/lib/systemd/system.conf.d/30-m4-mount-ratelimit.conf'


class MountRateLimit(unittest.TestCase):
    def manager(self):
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(CONF.read_text())
        self.assertEqual(parser.sections(), ['Manager'])
        return parser['Manager']

    def test_only_the_manager_environment_is_set(self):
        # ManagerEnvironment= reaches PID 1 itself; DefaultEnvironment= would only reach the services
        self.assertEqual(list(self.manager()), ['ManagerEnvironment'])

    def test_the_burst_is_raised_and_the_interval_kept(self):
        assignments = dict(item.split('=', 1) for item in self.manager()['ManagerEnvironment'].split())
        self.assertEqual(set(assignments), {'SYSTEMD_DEFAULT_MOUNT_RATE_LIMIT_BURST'})
        burst = int(assignments['SYSTEMD_DEFAULT_MOUNT_RATE_LIMIT_BURST'])
        self.assertGreaterEqual(burst, 20)   # early boot makes more than the default 5 mount-table changes
        self.assertLessEqual(burst, 1000)    # still a limit: a mount storm can't spin PID 1

    def test_the_package_copies_the_directory(self):
        # PKGBUILD copies userspace/support/usr as a whole, so the drop-in needs no install line
        self.assertTrue(CONF.is_relative_to(TREES/'support/usr'))



class ConsoleSize(unittest.TestCase):
    """systemd (v257+) asks /dev/console for its size with CSI 18 t and waits up to 333 ms for an answer before trying
    DSR (terminal-util.c terminal_get_size); the Linux VT never answers CSI 18 t, so PID 1 stalls before its banner on
    every boot. systemd.tty.rows/columns.console on the command line skip the query (reset_dev_console_fd)."""
    def args(self):
        return dict(a.split('=', 1) for a in (TREES/'support/usr/share/m4/cmdline').read_text().split() if '=' in a)

    def test_the_size_is_on_the_command_line(self):
        a = self.args()
        # fbcon on the 3456x2234 panel with the 8x16 font: "Console: switching to colour frame buffer device 432x139"
        self.assertEqual((a.get('systemd.tty.rows.console'), a.get('systemd.tty.columns.console')), ('139', '432'))

    def test_the_console_is_still_tty0(self):
        self.assertEqual(self.args().get('console'), 'tty0')


class BootPhases(unittest.TestCase):
    """tools/boot-phases.py on a synthetic aurora autologin boot (journalctl -o json lines)."""
    TOOL = Path(__file__).resolve().parents[2]/'tools/boot-phases.py'

    def setUp(self):
        if not self.TOOL.exists():
            self.skipTest('tools/boot-phases.py is not next to this tree')
        spec = importlib.util.spec_from_file_location('boot_phases', self.TOOL)
        self.bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bp)

    @staticmethod
    def line(t, ident, msg, transport='journal', as_bytes=False):
        e = {'_TRANSPORT': transport, 'SYSLOG_IDENTIFIER': ident, 'MESSAGE': list(msg.encode()) if as_bytes else msg}
        # kernel lines carry the kernel's own clock; the rest only the journal's receive time
        e['_SOURCE_MONOTONIC_TIMESTAMP' if transport == 'kernel' else '__MONOTONIC_TIMESTAMP'] = str(int(t * 1e6))
        return json.dumps(e)

    def autologin_boot(self):
        L = self.line
        return [
            L(0.0, 'kernel', 'Linux version 7.1.12-m4.17 (m4@m4-n1)', 'kernel'),
            L(0.32, 'kernel', 'Run /init as init process', 'kernel'),
            L(0.92, 'systemd-journald', 'Journal started', 'driver'),
            L(1.57, 'kernel', '[drm] Initialized apple 1.0.0 for display-subsystem on minor 1', 'kernel'),
            L(2.49, 'systemd', 'Reached target Basic System.'),
            L(2.58, 'gpu-load', 'M4_GPU_READY (aurora, renderD128)', 'stdout'),
            L(4.27, 'systemd', 'Startup finished in 529ms.'),   # the user manager's line
            L(4.30, 'systemd', 'Reached target Basic System.'),  # the user manager again: first match keeps PID 1's
            L(4.32, 'display-load', 'M4_DISPLAY_READY', 'stdout'),
            L(4.32, 'systemd', 'Started Simple Desktop Display Manager.'),
            L(4.32, 'systemd', 'Reached target Graphical Interface.'),
            L(4.32, 'systemd', 'Startup finished in 558ms (kernel) + 3.760s (userspace) = 4.318s.'),
            L(4.68, 'sddm', 'Initializing...'),
            L(4.84, 'sddm', 'Authentication for user  "alice"  successful'),
            L(4.84, 'sddm-helper', 'pam_unix(sddm-autologin:session): session opened for user alice(uid=1000)',
              'syslog'),
            L(5.20, 'sddm', 'Session started true'),
            L(6.04, 'uwsm_hyprland.desktop', 'Welcome to Hyprland!', 'stdout', as_bytes=True),
            L(6.47, 'kernel', 'apple-dcp 402e00000.dcp: M4_DCP_MODESET rc=0 valid_mode=1', 'kernel'),
            L(6.65, 'systemd', 'Started Main service for Hyprland, An intelligent dynamic tiling Wayland compositor.'),
            L(6.94, 'omarchy-shell', '  INFO: Configuration Loaded', 'stdout'),
            L(7.12, 'systemd', 'Reached target Current graphical user session.'),
        ]

    def test_the_autologin_flow_is_measured_end_to_end(self):
        r = self.bp.summarize(self.bp.parse(self.autologin_boot()), 'synthetic')
        self.assertEqual((r['kernel'], r['login_mode']), ('7.1.12-m4.17', 'autologin'))
        self.assertEqual((r['display_takeover_s'], r['desktop_ready_s'], r['shell_loaded_s']), (1.57, 6.65, 6.94))
        ms = r['milestones']
        self.assertEqual((ms['basic_target'], ms['startup_finished'], ms['hyprland_start']), (2.49, 4.32, 6.04))
        self.assertIsNone(r['greeter_visible_s'])
        self.assertEqual([s['segment'] for s in r['segments'] if s['seconds'] is None], [])
        # the chain is contiguous: kernel start -> graphical-session.target
        self.assertAlmostEqual(sum(s['seconds'] for s in r['segments']), 7.12, places=3)

    def test_a_greeter_login_is_the_login_milestone(self):
        L = self.line
        r = self.bp.summarize(self.bp.parse([L(4.0, 'sddm', 'Greeter session started successfully'),
                                             L(9.0, 'sddm', 'Message received from greeter: Login'),
                                             L(9.1, 'sddm', 'Authentication for user  "alice"  successful')]), 'g')
        self.assertEqual((r['login_mode'], r['milestones']['login']), ('greeter', 9.0))


if __name__ == '__main__':
    unittest.main()
