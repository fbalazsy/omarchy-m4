#!/usr/bin/python3 -I
"""N3 lane U: A (sddm configuration order), B (the greeter path, now on m4-aurora-mesa's files), G (the KMS card), E (linux-modules-cleanup).   python3 -B -I test_n3_fixes.py

The owner's real files are in tests/inputs/n3 (copied from /reports/n3/inputs): Omarchy's 10-theme.conf and 10-wayland.conf, sddm's default.conf and
kernel-modules-hook's linux-modules-cleanup.service."""
import configparser
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SUPPORT = ROOT/'support'
INPUTS = HERE/'inputs/n3'
M4 = SUPPORT/'usr/lib/m4'
AURORA = ROOT.parent/'packages/m4-aurora-mesa'     # the package's session-env, greeter-env and sddm-gpu-aurora.conf
AURORA_PREFIX = '/opt/aurora-mesa'


def kv(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            out[k] = v
    return out


def sddm_merge(files):
    """What sddm does: the files in the order given, a later key overrides an earlier one."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    for f in files:
        cp.read(f)
    return cp


# the integrator's no-R-series gate (review/check-forbidden.py), patterns as of N3; short patterns are applied to text files only
FORBIDDEN = {
    'opt-m4': rb'/opt/m4-', 'peripheral1': rb'm4-kernel-peripheral1', 'r42': rb'(?<![A-Za-z0-9_])r42(?![A-Za-z0-9_])',
    'r-series-name': rb'm4-r[45][0-9](?![0-9])', 'r-series-tag': rb'(?<![A-Za-z])R(?:4[2-9]|5[0-9])(?![0-9A-Za-z])',
    'm4-input1': rb'm4\.input1', 'm4-peripheral1': rb'm4\.peripheral1', 'm4-directdesktop': rb'm4\.directdesktop', 'm4-r31direct': rb'm4\.r31direct',
    'm4-standalone': rb'm4\.standalone', 'systemd-mask-m4': rb'systemd\.mask=m4-', 'state-dir': rb'/var/lib/m4-r42-direct',
    'rdinit': rb'(?<![A-Za-z])rdinit=', 'fw-path-opt': rb'firmware_class\.path=/opt', 'protection': rb'protection-poweroff',
    'optin': rb'optin[_-]guard|m4-optin', 'm4-kernel-tool': rb'(?<![A-Za-z0-9_-])m4-kernel(?![A-Za-z0-9_-])',
    'level1': rb'M4L1ID01|level1\.conf|m4-level1|m4_level1', 'dev-overlay': rb'm4-dev-overlay|DEV-OVERLAY', 'trial': rb'trial-request|firstboot',
}


class NoRSeriesStrings(unittest.TestCase):
    def test_no_file_name_or_text_of_the_package_tree_names_the_r_series(self):
        hits = []
        for p in sorted(SUPPORT.rglob('*')):
            if p.is_symlink() or not p.is_file() or '__pycache__' in p.parts:     # a link's target belongs to another package
                continue
            rel = str(p.relative_to(SUPPORT)).encode()
            data = p.read_bytes()
            binary = b'\0' in data[:8192]
            for name, pattern in FORBIDDEN.items():
                if re.search(pattern, rel) or (not binary and re.search(pattern, data)):
                    hits.append((str(p.relative_to(SUPPORT)), name))
        self.assertEqual(hits, [])


class A_ConfigurationOrder(unittest.TestCase):
    def seeded(self, ours):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        conf = Path(d)/'sddm.conf.d'
        conf.mkdir()
        shutil.copy(INPUTS/'etc__sddm.conf.d__10-theme.conf', conf/'10-theme.conf')
        shutil.copy(INPUTS/'etc__sddm.conf.d__10-wayland.conf', conf/'10-wayland.conf')
        shutil.copy(SUPPORT/'etc/sddm.conf.d/zz-m4.conf', conf/ours)
        return conf

    def merged(self, conf):
        files = [INPUTS/'usr__lib__sddm__sddm.conf.d__default.conf'] + sorted(conf.iterdir(), key=lambda p: p.name)
        return sddm_merge(files)

    def test_our_file_sorts_after_every_owner_file_and_wins(self):
        conf = self.seeded('zz-m4.conf')
        self.assertEqual(sorted(p.name for p in conf.iterdir())[-1], 'zz-m4.conf')
        c = self.merged(conf)
        self.assertEqual(c['Wayland']['CompositorCommand'], '/usr/lib/m4/sddm-compositor')
        self.assertEqual(c['Wayland']['SessionDir'], '/usr/share/m4/wayland-sessions')
        self.assertEqual(c['X11']['SessionDir'], '/usr/share/m4/no-x11-sessions')
        self.assertEqual(c['General']['DisplayServer'], 'wayland')
        self.assertEqual(c['Theme']['Current'], 'omarchy', 'the owner\'s theme choice is not touched without the GPU')

    def test_the_n2_name_loses_to_omarchys_file(self):
        # the control that reproduces the black boots: 10-m4.conf sorts before 10-wayland.conf ('m' < 'w')
        c = self.merged(self.seeded('10-m4.conf'))
        self.assertEqual(c['Wayland']['CompositorCommand'], 'start-hyprland -- --config /usr/share/sddm/hyprland.lua')

    def test_the_package_ships_exactly_that_one_sddm_file_and_backs_it_up(self):
        names = sorted(p.name for p in (SUPPORT/'etc/sddm.conf.d').iterdir())
        self.assertEqual(names, ['zz-m4.conf'])
        pkgbuild = (ROOT.parent/'packages/m4-support/PKGBUILD').read_text()
        self.assertIn('etc/sddm.conf.d/zz-m4.conf', pkgbuild)
        self.assertNotIn('etc/sddm.conf.d/10-m4.conf', pkgbuild)

    def test_the_gpu_configuration_has_only_keys_nobody_else_sets_and_sorts_before_ours(self):
        gpu = sddm_merge([AURORA/'sddm-gpu-aurora.conf'])        # linked as zz-m4-gpu.conf by gpu-load
        ours = sddm_merge([SUPPORT/'etc/sddm.conf.d/zz-m4.conf'])
        for section in gpu.sections():
            for key in gpu[section]:
                self.assertFalse(ours.has_option(section, key), (section, key))
        self.assertEqual(sorted(['zz-m4-gpu.conf', 'zz-m4.conf'])[0], 'zz-m4-gpu.conf')

    def test_the_session_directory_has_exactly_one_session_for_the_greeter_theme(self):
        # the qualified theme refuses a login when the session model holds anything but one session
        self.assertEqual([p.name for p in (SUPPORT/'usr/share/m4/wayland-sessions').iterdir()], ['m4-hyprland.desktop'])


def run_wrapper(case, ready, inherited, sysfs=None):
    d = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, d)
    lib = d/'lib'
    lib.mkdir()
    for n in ('env-keys', 'kms-card.sh'):
        shutil.copy(M4/n, lib/n)
    shutil.copy(AURORA/'greeter-env', lib/'greeter-env')
    out = d/'env.out'
    hypr = d/'start-hyprland'
    hypr.write_text('#!/bin/sh\nenv > %s\necho "ARGS $*" >> %s\n' % (out, out))
    hypr.chmod(0o755)
    flag = d/'gpu-ready'
    if ready:
        flag.write_text('ok\n')
    script = (M4/'sddm-compositor').read_text().replace('/usr/lib/m4', str(lib)).replace('/run/m4/gpu-ready', str(flag)) \
        .replace('/usr/bin/start-hyprland', str(hypr))
    sh = d/'compositor'
    sh.write_text(script)
    env = dict(inherited)
    env['PATH'] = '/usr/bin:/bin'
    if sysfs:
        env.update(M4_SYS_DRM=str(sysfs[0]), M4_DEV_DRI=str(sysfs[1]))
    else:
        env.update(M4_SYS_DRM=str(d/'nosys'), M4_DEV_DRI=str(d/'nodev'))
    p = subprocess.run(['/bin/sh', str(sh)], env=env, capture_output=True, text=True, timeout=30)
    case.assertEqual(p.returncode, 0, p.stderr)
    text = out.read_text()
    args = [l for l in text.splitlines() if l.startswith('ARGS ')][0]
    got = dict(l.split('=', 1) for l in text.splitlines() if '=' in l and not l.startswith('ARGS '))
    return got, args


def kms_tree(case, cards, bypath=True):
    d = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, d)
    sys_drm, dev = d/'sys/class/drm', d/'dev/dri'
    (d/'drivers').mkdir()
    sys_drm.mkdir(parents=True)
    (dev/'by-path').mkdir(parents=True)
    for name, driver, devname in cards:
        (d/'drivers'/driver).mkdir(exist_ok=True)
        hw = d/'sys/devices/platform'/devname
        hw.mkdir(parents=True)
        os.symlink(d/'drivers'/driver, hw/'driver')
        c = sys_drm/name
        c.mkdir()
        os.symlink(hw, c/'device')
        (sys_drm/(name + ('-eDP-1' if driver == 'apple-drm' else '-Unknown-1'))).mkdir()    # a connector entry: not a card
        (dev/name).write_text('')
        if bypath:
            os.symlink(dev/name, dev/'by-path'/('platform-%s-card' % devname))
    return sys_drm, dev


class B_GreeterOnAurora(unittest.TestCase):
    """The session and greeter environments are m4-aurora-mesa's files (single source of truth); m4-support only links to them."""
    def test_the_environment_files_link_to_m4_aurora_mesa(self):
        for n in ('session-env', 'greeter-env'):
            self.assertTrue((M4/n).is_symlink(), n)
            self.assertEqual(os.readlink(M4/n), AURORA_PREFIX + '/' + n)
        depends = (ROOT.parent/'packages/m4-support/PKGBUILD').read_text().split('depends=(', 1)[1].split(')', 1)[0].split()
        self.assertIn('m4-aurora-mesa', depends)
        self.assertNotIn('m4-mesa', depends)
        self.assertFalse((SUPPORT/'usr/share/m4/sddm-gpu.conf').exists())
        self.assertFalse((SUPPORT/'usr/lib/modprobe.d/m4-gpu.conf').exists())

    def test_the_aurora_environment_points_at_aurora_mesa_only_and_keeps_1queue(self):
        for n in ('session-env', 'greeter-env'):
            env = kv(AURORA/n)
            for k in ('LD_LIBRARY_PATH', 'GBM_BACKENDS_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES', 'VK_DRIVER_FILES'):
                self.assertTrue(env[k].startswith(AURORA_PREFIX + '/'), (n, k))
            self.assertIn('1queue', env['ASAHI_MESA_DEBUG'].split(','), n)    # the 25G83 firmware asserts on a second render queue
            self.assertFalse([k for k in env if 'G16X' in k or k.startswith('AGX_APPLE9_')], n)

    def test_cleared_keys_cover_every_key_the_greeter_and_the_session_set(self):
        keys = [l for l in (M4/'env-keys').read_text().splitlines() if l and not l.startswith('#')]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLessEqual(set(kv(AURORA/'greeter-env')) - {'XDG_CURRENT_DESKTOP'}, set(keys))
        self.assertLessEqual(set(kv(AURORA/'session-env')), set(keys))
        # the preload/audit/backend and Mesa selectors an inherited environment may carry
        self.assertLessEqual({'LD_PRELOAD', 'LD_AUDIT', 'GBM_BACKEND', 'EGL_PLATFORM', 'QT_QUICK_BACKEND', 'MESA_LOADER_DRIVER_OVERRIDE',
                              'GALLIUM_DRIVER', 'LIBGL_ALWAYS_SOFTWARE', 'DRI_PRIME', '__GLX_VENDOR_LIBRARY_NAME', 'LIBGL_DRIVERS_PATH',
                              'DRIRC_CONFIGDIR', 'MESA_SHADER_CACHE_DIR', 'MESA_GL_VERSION_OVERRIDE', 'AQ_DRM_DEVICES'}, set(keys))
        self.assertFalse([k for k in keys if 'G16X' in k or k.startswith('AGX_APPLE9_')])

    def test_the_greeter_uses_the_same_mesa_as_its_compositor(self):
        cp = sddm_merge([AURORA/'sddm-gpu-aurora.conf'])
        greeter = dict(e.split('=', 1) for e in cp['General']['GreeterEnvironment'].split(','))
        ours = kv(AURORA/'greeter-env')
        for k in ('LD_LIBRARY_PATH', 'GBM_BACKENDS_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES', 'QT_QPA_PLATFORM', 'QSG_RHI_BACKEND'):
            self.assertEqual(greeter[k], ours[k], k)
        # GreeterEnvironment is a comma-separated list: the ASAHI_MESA_DEBUG separators are colons there
        self.assertEqual(greeter['ASAHI_MESA_DEBUG'].replace(':', ','), ours['ASAHI_MESA_DEBUG'])
        self.assertEqual(cp['Theme']['ThemeDir'], '/usr/share/m4/sddm-themes')
        self.assertEqual(cp['Theme']['Current'], 'omarchy-lock')
        theme = SUPPORT/'usr/share/m4/sddm-themes/omarchy-lock'
        self.assertTrue((theme/'Main.qml').is_file() and (theme/'metadata.desktop').is_file() and (theme/'background.jpg').is_file())
        self.assertIn('Theme-Id=omarchy-lock', (theme/'metadata.desktop').read_text())

    POISON = dict(MESA_LOADER_DRIVER_OVERRIDE='zink', LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', DRI_PRIME='1', LD_PRELOAD='/x.so',
                  LD_AUDIT='/a.so', GBM_BACKEND='nvidia', EGL_PLATFORM='x11', QT_QUICK_BACKEND='software', MESA_SHADER_CACHE_DIR='/home/x/c',
                  ASAHI_G17P_RT_DISABLE='0', MESA_GL_VERSION_OVERRIDE='4.6', LIBGL_DRIVERS_PATH='/usr/lib/dri', AQ_DRM_DEVICES='/dev/dri/card0',
                  __GLX_VENDOR_LIBRARY_NAME='nvidia', QT_QPA_PLATFORM='xcb', MESA_GLTHREAD='true', HOME='/var/lib/sddm', LANG='C.UTF-8')

    def test_with_the_gpu_the_inherited_keys_are_cleared_and_the_values_applied(self):
        got, args = run_wrapper(self, True, self.POISON)
        want = kv(AURORA/'greeter-env')
        for k, v in want.items():
            self.assertEqual(got.get(k), v, k)
        for k in ('MESA_LOADER_DRIVER_OVERRIDE', 'LIBGL_ALWAYS_SOFTWARE', 'GALLIUM_DRIVER', 'DRI_PRIME', 'LD_PRELOAD', 'LD_AUDIT', 'GBM_BACKEND',
                  'EGL_PLATFORM', 'QT_QUICK_BACKEND', 'MESA_SHADER_CACHE_DIR', 'ASAHI_G17P_RT_DISABLE', 'MESA_GL_VERSION_OVERRIDE',
                  '__GLX_VENDOR_LIBRARY_NAME', 'AQ_DRM_DEVICES'):
            self.assertNotIn(k, got, k)
        self.assertEqual(got['HOME'], '/var/lib/sddm', 'what is not a renderer key is left alone')
        self.assertEqual(got['LANG'], 'C.UTF-8')
        self.assertIn('-- --config /usr/share/m4/greeter.lua', args)

    def test_without_the_gpu_the_greeter_is_software_and_stale_keys_are_gone(self):
        got, args = run_wrapper(self, False, self.POISON)
        self.assertEqual(got['LIBGL_ALWAYS_SOFTWARE'], '1')
        self.assertEqual(got['GALLIUM_DRIVER'], 'llvmpipe')
        self.assertEqual(got['AQ_NO_MODIFIERS'], '1')
        for k in ('LD_LIBRARY_PATH', 'QT_QPA_PLATFORM', 'MESA_LOADER_DRIVER_OVERRIDE', 'DRI_PRIME', 'LD_PRELOAD', 'MESA_SHADER_CACHE_DIR',
                  'LIBGL_DRIVERS_PATH', 'GBM_BACKENDS_PATH', 'MESA_GLTHREAD', '__EGL_VENDOR_LIBRARY_FILENAMES'):
            self.assertNotIn(k, got, k)


class B_RenderNodeAccess(unittest.TestCase):
    """R53's m4_gpu_greeter_access.py put an ACL for uid 960 on the private render node after R53 had removed `uaccess`. N1 has no private node:
    the standard udev rules give every renderD* group `render` and mode 0666, `uaccess` is only on card*, and sddm is in `render` and `video`."""
    def rules(self, name):
        p = Path('/usr/lib/udev/rules.d')/name
        if not p.is_file():
            self.skipTest('no %s here' % p)
        return p.read_text()

    def test_render_nodes_are_world_accessible_by_the_standard_rule(self):
        text = self.rules('50-udev-default.rules')
        self.assertRegex(text, r'SUBSYSTEM=="drm", KERNEL=="renderD\*", GROUP="render", MODE="0666"')

    def test_uaccess_is_not_put_on_render_nodes(self):
        text = self.rules('70-uaccess.rules')
        drm = [l for l in text.splitlines() if 'SUBSYSTEM=="drm"' in l]
        self.assertTrue(drm)
        for l in drm:
            self.assertNotIn('renderD', l)

    def test_the_package_ships_no_udev_rule_that_changes_the_render_node(self):
        rules = [p for p in SUPPORT.rglob('*.rules')]
        for p in rules:
            self.assertNotIn('renderD', p.read_text(), str(p))

    def test_the_sddm_user_is_in_render_and_video(self):
        text = (SUPPORT/'usr/lib/sysusers.d/m4-greeter.conf').read_text() if (SUPPORT/'usr/lib/sysusers.d/m4-greeter.conf').is_file() else \
            '\n'.join(p.read_text() for p in (SUPPORT/'usr/lib/sysusers.d').iterdir())
        self.assertIn('m sddm render', text)
        self.assertIn('m sddm video', text)

    def test_a_fresh_install_gets_the_greeter_uid_the_driver_accepts(self):
        # the driver accepts trial_render_greeter_uid 960 only (g16_reuse_principal::valid); sysusers lets the file that sorts first win
        conf = SUPPORT/'usr/lib/sysusers.d/m4.conf'
        self.assertIn('u sddm 960 "SDDM Greeter Account" /var/lib/sddm -', conf.read_text().splitlines())
        self.assertLess(conf.name, 'sddm.conf')

    @unittest.skipUnless(shutil.which('systemd-sysusers'), 'needs systemd-sysusers')
    def test_sysusers_creates_sddm_with_uid_960_beside_the_packages_own_line(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'etc').mkdir()
            (root/'etc/passwd').write_text('')
            (root/'etc/group').write_text('')
            conf = root/'usr/lib/sysusers.d'
            conf.mkdir(parents=True)
            shutil.copy(SUPPORT/'usr/lib/sysusers.d/m4.conf', conf/'m4.conf')
            (conf/'sddm.conf').write_text('u sddm - "SDDM Greeter Account" /var/lib/sddm -\n')    # the sddm package's line
            p = subprocess.run(['systemd-sysusers', '--root', d, '--dry-run'], capture_output=True, text=True, timeout=60)
            self.assertIn("Creating user 'sddm' (SDDM Greeter Account) with UID 960 and GID 960.", p.stdout + p.stderr)


class G_KmsCard(unittest.TestCase):
    """AQ_DRM_DEVICES is the display driver's card, never the GPU's, whatever the numbers are."""
    def tree(self, cards, bypath=True):
        return kms_tree(self, cards, bypath)

    def card(self, sys_drm, dev):
        env = dict(os.environ, M4_SYS_DRM=str(sys_drm), M4_DEV_DRI=str(dev))
        p = subprocess.run(['/bin/sh', '-c', '. %s; m4_kms_card' % (M4/'kms-card.sh')], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_display_only(self):
        s, d = self.tree([('card0', 'apple-drm', '2c2000000.display-subsystem')])
        self.assertEqual(self.card(s, d), str(d/'by-path/platform-2c2000000.display-subsystem-card'))

    def test_simpledrm_only(self):
        s, d = self.tree([('card0', 'simple-framebuffer', 'simple-framebuffer.0')])
        self.assertEqual(self.card(s, d), str(d/'by-path/platform-simple-framebuffer.0-card'))

    def test_simpledrm_gpu_and_display_in_the_order_of_the_n2_journal(self):
        s, d = self.tree([('card0', 'simple-framebuffer', 'simple-framebuffer.0'), ('card1', 'm4-gpu', 'm4-gpu'),
                          ('card2', 'apple-drm', 'display-subsystem')])
        got = self.card(s, d)
        self.assertEqual(got, str(d/'by-path/platform-display-subsystem-card'))
        self.assertNotIn('card0', os.path.realpath(got))
        self.assertEqual(os.path.realpath(got), os.path.realpath(d/'card2'))

    def test_the_gpu_is_never_chosen_even_alone_or_first(self):
        s, d = self.tree([('card0', 'm4-gpu', 'm4-gpu')])
        self.assertEqual(self.card(s, d), '')
        s, d = self.tree([('card0', 'm4-gpu', 'm4-gpu'), ('card1', 'asahi', 'asahi-gpu'), ('card2', 'apple-drm', 'display-subsystem')])
        self.assertEqual(os.path.realpath(self.card(s, d)), os.path.realpath(d/'card2'))

    def test_without_a_by_path_link_the_card_node_is_used(self):
        s, d = self.tree([('card0', 'simple-framebuffer', 'simple-framebuffer.0'), ('card1', 'm4-gpu', 'm4-gpu'),
                          ('card2', 'apple-drm', 'display-subsystem')], bypath=False)
        self.assertEqual(self.card(s, d), str(d/'card2'))

    def test_a_stale_by_path_link_to_another_card_is_not_trusted(self):
        s, d = self.tree([('card0', 'simple-framebuffer', 'simple-framebuffer.0'), ('card2', 'apple-drm', 'display-subsystem')])
        os.unlink(d/'by-path/platform-display-subsystem-card')
        os.symlink(d/'card0', d/'by-path/platform-display-subsystem-card')
        self.assertEqual(self.card(s, d), str(d/'card2'))

    def test_the_apple_card_wins_when_both_display_drivers_are_present(self):
        s, d = self.tree([('card0', 'simple-framebuffer', 'simple-framebuffer.0'), ('card1', 'apple-drm', 'display-subsystem')], bypath=False)
        self.assertEqual(self.card(s, d), str(d/'card1'))

    def test_the_wrapper_exports_the_chosen_card_and_keeps_no_modifiers(self):
        s, d = self.tree([('card0', 'simple-framebuffer', 'simple-framebuffer.0'), ('card1', 'm4-gpu', 'm4-gpu'),
                          ('card2', 'apple-drm', 'display-subsystem')], bypath=False)
        got, _ = run_wrapper(self, True, {'AQ_DRM_DEVICES': '/dev/dri/card0'}, sysfs=(s, d))
        self.assertEqual(got['AQ_DRM_DEVICES'], str(d/'card2'))
        self.assertEqual(got['AQ_NO_MODIFIERS'], '1')

    def test_no_shipped_file_hardcodes_a_card_number(self):
        for path in (M4/'sddm-compositor', M4/'session-env.sh', AURORA/'session-env', AURORA/'greeter-env', AURORA/'sddm-gpu-aurora.conf'):
            self.assertNotRegex(path.read_text(), r'/dev/dri/card\d', str(path))
        self.assertNotIn('AQ_DRM_DEVICES', kv(AURORA/'session-env'))
        self.assertNotIn('AQ_DRM_DEVICES', kv(AURORA/'greeter-env'))


class SessionEnvSh(unittest.TestCase):
    def run_script(self, ready, inherited, sysfs):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        lib = d/'lib'
        lib.mkdir()
        for n in ('env-keys', 'kms-card.sh'):
            shutil.copy(M4/n, lib/n)
        shutil.copy(AURORA/'session-env', lib/'session-env')
        flag = d/'gpu-ready'
        if ready:
            flag.write_text('ok\n')
        text = (M4/'session-env.sh').read_text().replace('/usr/lib/m4', str(lib)).replace('/run/m4/gpu-ready', str(flag))
        script = d/'s.sh'
        script.write_text(text + '\nenv\n')
        env = dict(inherited, PATH='/usr/bin:/bin', M4_SYS_DRM=str(sysfs[0]), M4_DEV_DRI=str(sysfs[1]))
        p = subprocess.run(['/bin/sh', str(script)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return dict(l.split('=', 1) for l in p.stdout.splitlines() if '=' in l)

    def tree(self):
        return kms_tree(self, [('card0', 'simple-framebuffer', 'simple-framebuffer.0'), ('card1', 'm4-gpu', 'm4-gpu'),
                               ('card2', 'apple-drm', 'display-subsystem')], bypath=False)

    def test_gpu_up_clears_the_inherited_keys_applies_the_session_values_and_picks_the_card(self):
        s, d = self.tree()
        got = self.run_script(True, dict(B_GreeterOnAurora.POISON, AQ_DRM_DEVICES='/dev/dri/card0'), (s, d))
        for k, v in kv(AURORA/'session-env').items():
            self.assertEqual(got.get(k), v, k)
        self.assertEqual(got['AQ_DRM_DEVICES'], str(d/'card2'))
        for k in ('MESA_LOADER_DRIVER_OVERRIDE', 'LIBGL_ALWAYS_SOFTWARE', 'GALLIUM_DRIVER', 'DRI_PRIME', 'MESA_SHADER_CACHE_DIR', 'MESA_GL_VERSION_OVERRIDE'):
            self.assertNotIn(k, got, k)
        self.assertEqual(got['HOME'], '/var/lib/sddm')

    def test_without_the_gpu_nothing_is_changed(self):
        s, d = self.tree()
        got = self.run_script(False, {'MESA_LOADER_DRIVER_OVERRIDE': 'zink', 'AQ_DRM_DEVICES': '/dev/dri/card0'}, (s, d))
        self.assertEqual(got.get('MESA_LOADER_DRIVER_OVERRIDE'), 'zink')
        self.assertEqual(got.get('AQ_DRM_DEVICES'), '/dev/dri/card0')


class GpuLoadSddmLink(unittest.TestCase):
    """gpu-load: asahi (with t6040_attach=1 only while the GPU node is not a device), the render node of the asahi driver, then the
    greeter configuration and `ok aurora`."""
    def run_load(self, modprobe_ok, stale, gpu_device=False, mesa=True):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        for sub in ('run', 'etc/sddm.conf.d', 'sys/class/drm', 'sys/module', 'sys/drivers/asahi', 'sys/bus/platform/devices', 'lib',
                    'opt/aurora-mesa'):
            (d/sub).mkdir(parents=True)
        if mesa:
            for n in ('session-env', 'greeter-env', 'sddm-gpu-aurora.conf'):
                shutil.copy(AURORA/n, (d/'opt/aurora-mesa'/n))
                if n != 'sddm-gpu-aurora.conf':
                    os.symlink(d/'opt/aurora-mesa'/n, d/'lib'/n)
        if gpu_device:      # 7.1.12-m4.10: the kernel made the GPU node a device
            (d/'sys/bus/platform/devices/206400000.gpu/of_node').mkdir(parents=True)
            (d/'sys/bus/platform/devices/206400000.gpu/of_node/compatible').write_bytes(b'apple,agx-t6040\0apple,agx-g17p\0')
        if stale:
            os.symlink('/opt/aurora-mesa/sddm-gpu-aurora.conf', d/'etc/sddm.conf.d/zz-m4-gpu.conf')
        bindir = d/'bin'
        bindir.mkdir()
        node = ('[ "$1" = -r ] && exit 0\nmkdir -p {d}/sys/module/asahi {d}/sys/devices/gpu {d}/sys/class/drm/renderD128\n'
                'ln -s {d}/sys/drivers/asahi {d}/sys/devices/gpu/driver\nln -s {d}/sys/devices/gpu {d}/sys/class/drm/renderD128/device\n').format(d=d)
        # modprobe_ok None: `modprobe -b` of a blacklisted module, status 0 and nothing loaded
        (bindir/'modprobe').write_text('#!/bin/sh\necho "$@" >> %s/modprobe.log\n' % d + (
            node if modprobe_ok else 'exit 0\n' if modprobe_ok is None else '[ "$1" = -r ] || exit 1\n'))
        (bindir/'modprobe').chmod(0o755)
        text = (M4/'gpu-load').read_text()
        self.assertIn('\nO=/opt/aurora-mesa\n', text)
        text = text.replace('\nO=/opt/aurora-mesa\n', '\nO=%s\n' % (d/'opt/aurora-mesa')).replace('/run/m4', str(d/'run')) \
            .replace('/etc/sddm.conf.d', str(d/'etc/sddm.conf.d')).replace('/usr/bin/modprobe', str(bindir/'modprobe')) \
            .replace('/sys/class/drm', str(d/'sys/class/drm')).replace('/sys/module/asahi', str(d/'sys/module/asahi')) \
            .replace('/sys/bus/platform/devices', str(d/'sys/bus/platform/devices')) \
            .replace('/usr/lib/m4/', str(d/'lib') + '/')
        script = d/'gpu-load'
        script.write_text(text)
        p = subprocess.run(['/bin/sh', str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return d

    def modprobe_calls(self, d):
        log = d/'modprobe.log'
        return log.read_text().splitlines() if log.exists() else []

    def test_the_gpu_configuration_is_linked_only_when_the_driver_is_up(self):
        d = self.run_load(True, stale=False)
        link = d/'etc/sddm.conf.d/zz-m4-gpu.conf'
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(d/'opt/aurora-mesa/sddm-gpu-aurora.conf'))
        self.assertEqual((d/'run/gpu-ready').read_text(), 'ok aurora\n')      # m4-boot's boot-ok keys on "aurora"
        self.assertFalse((d/'run/gpu-failed').exists())
        self.assertEqual(self.modprobe_calls(d), ['-r asahi', '-bv --ignore-install asahi t6040_attach=1'])

    def test_a_gpu_the_kernel_made_a_device_is_never_reloaded(self):
        d = self.run_load(True, stale=False, gpu_device=True)
        self.assertEqual(self.modprobe_calls(d), ['-bv asahi'])     # no unload, no t6040_attach (-EBUSY on an enabled node)
        self.assertEqual((d/'run/gpu-ready').read_text(), 'ok aurora\n')

    def test_a_blacklisted_asahi_leaves_the_software_greeter(self):
        d = self.run_load(None, stale=True, gpu_device=True)
        self.assertIn('asahi is not loaded', (d/'run/gpu-failed').read_text())
        self.assertFalse(os.path.lexists(d/'etc/sddm.conf.d/zz-m4-gpu.conf'))

    def test_a_failed_load_removes_a_stale_link(self):
        d = self.run_load(False, stale=True)
        self.assertFalse(os.path.lexists(d/'etc/sddm.conf.d/zz-m4-gpu.conf'))
        self.assertTrue((d/'run/gpu-failed').exists())
        self.assertFalse((d/'run/gpu-ready').exists())

    def test_without_aurora_mesa_the_driver_is_not_loaded(self):
        d = self.run_load(True, stale=False, mesa=False)
        self.assertEqual(self.modprobe_calls(d), [])
        self.assertIn('m4-aurora-mesa', (d/'run/gpu-failed').read_text())
        self.assertFalse((d/'run/gpu-ready').exists())

    def test_the_g16_driver_stays_blacklisted(self):
        self.assertIn('blacklist m4_gpu_driver', (SUPPORT/'usr/lib/modprobe.d/m4.conf').read_text().splitlines())

    def test_a_boot_that_never_runs_the_unit_removes_the_link_through_tmpfiles(self):
        text = (SUPPORT/'usr/lib/tmpfiles.d/m4-sddm.conf').read_text()
        self.assertIn('r /etc/sddm.conf.d/zz-m4-gpu.conf', text)


class E_ModulesCleanup(unittest.TestCase):
    """The real unit's ExecStart and ours, run against the same fixture with a fake pacman and rsync."""
    KVERS = {'running': '7.1.13-m4.4', 'owned': '7.2.10-1-aarch64-ARCH', 'entry': '7.1.13-m4.2', 'entry_named_differently': '7.1.13-m4.9',
             'rescue': '7.1.13-m4', 'recorded': '7.1.13-m4.3', 'stray': '6.1.0-old'}

    @staticmethod
    def systemd_split(line):
        """systemd's command-line splitting for the cases the unit uses: whitespace separates words, '...' quotes, \\' is a quote inside quotes."""
        words, cur, quote, i, started = [], '', False, 0, False
        while i < len(line):
            c = line[i]
            if c == '\\' and i + 1 < len(line):
                cur += line[i + 1]
                i += 2
                started = True
                continue
            if c == "'":
                quote = not quote
                started = True
            elif c.isspace() and not quote:
                if started:
                    words.append(cur)
                    cur, started = '', False
            else:
                cur += c
                started = True
            i += 1
        if started:
            words.append(cur)
        return words

    def real_command(self):
        text = (INPUTS/'usr__lib__systemd__system__linux-modules-cleanup.service').read_text()
        line = [l for l in text.splitlines() if l.startswith('ExecStart=')][0][len('ExecStart='):]
        return self.systemd_split(line.replace('$$', '$'))

    def fixture(self, with_entries=True):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        mods = d/'modules'
        for v in self.KVERS.values():
            (mods/v).mkdir(parents=True)
            (mods/v/'vmlinuz').write_text(v)
            (mods/v/'modules.dep').write_text('')
        (mods/'.old').mkdir()
        entries = d/'entries'
        entries.mkdir()
        if with_entries:
            for name, kver in (('7.1.13-m4.2', self.KVERS['entry']), ('previous', self.KVERS['entry_named_differently'])):
                (entries/name).mkdir()
                (entries/name/'entry.conf').write_text('abi=1\nkver=%s\ncmdline=root=x\n' % kver)
        (d/'rescue-kver').write_text(self.KVERS['rescue'] + '\n')
        (d/'kept-trees').write_text(self.KVERS['recorded'] + '\n')
        bindir = d/'bin'
        bindir.mkdir()
        (d/'owned').write_text(str(mods/self.KVERS['owned']) + '\n')
        (bindir/'pacman').write_text('#!/bin/sh\n[ "$1" = -Qo ] || exit 2\ngrep -qxF -- "$2" %s/owned\n' % d)
        (bindir/'rsync').write_text('#!/bin/sh\nfor last; do :; done\nfor a; do case $a in -*) ;; *) src=$a; break;; esac; done\ncp -a "$src" "$last"\n')
        for n in ('pacman', 'rsync'):
            (bindir/n).chmod(0o755)
        return d

    def trees(self, d):
        return sorted(p.name for p in (d/'modules').iterdir() if p.name != '.old' and p.is_dir())

    def old(self, d):
        return sorted(p.name for p in (d/'modules/.old').iterdir())

    def run_real(self, d):
        argv = [w.replace('/usr/lib/modules', str(d/'modules')).replace('%v', self.KVERS['running']) for w in self.real_command()]
        self.assertEqual(argv[:2], ['/bin/bash', '-exc'], argv[:2])
        env = dict(os.environ, PATH='%s:/usr/bin:/bin' % (d/'bin'))
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)

    def run_ours(self, d):
        env = dict(os.environ, PATH='%s:/usr/bin:/bin' % (d/'bin'), M4_MODULES_DIR=str(d/'modules'), M4_ENTRIES_DIR=str(d/'entries'),
                   M4_RESCUE_KVER_FILE=str(d/'rescue-kver'), M4_KEPT_TREES=str(d/'kept-trees'))
        return subprocess.run(['/bin/sh', str(M4/'modules-cleanup'), self.KVERS['running']], env=env, capture_output=True, text=True, timeout=60)

    def test_the_real_unit_deletes_the_entry_and_rescue_trees_which_is_the_bug(self):
        d = self.fixture()
        p = self.run_real(d)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.trees(d), sorted([self.KVERS['running'], self.KVERS['owned']]))
        self.assertIn(self.KVERS['rescue'], self.old(d))

    def test_ours_removes_only_what_the_real_logic_removes_minus_the_trees_this_system_boots(self):
        d = self.fixture()
        p = self.run_ours(d)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.trees(d), sorted([self.KVERS[k] for k in ('running', 'owned', 'entry', 'entry_named_differently', 'rescue', 'recorded')]))
        self.assertEqual(self.old(d), [self.KVERS['stray']], 'the stray tree went the way it always did: copied to .old, then removed')

    def test_without_entries_the_rescue_and_recorded_trees_are_still_skipped_and_the_rest_matches_the_real_logic(self):
        real, ours = self.fixture(with_entries=False), self.fixture(with_entries=False)
        self.run_real(real)
        self.assertEqual(self.run_ours(ours).returncode, 0)
        gone_real = set(self.KVERS.values()) - set(self.trees(real))
        gone_ours = set(self.KVERS.values()) - set(self.trees(ours))
        self.assertEqual(gone_ours, gone_real - {self.KVERS['rescue'], self.KVERS['recorded']})
        self.assertEqual(gone_ours, {self.KVERS['stray'], self.KVERS['entry'], self.KVERS['entry_named_differently']})

    def test_a_tree_that_cannot_be_copied_aside_is_not_removed(self):
        d = self.fixture()
        (d/'bin/rsync').write_text('#!/bin/sh\nexit 23\n')
        p = self.run_ours(d)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(self.KVERS['stray'], self.trees(d))

    def test_the_drop_in_resets_exec_start_then_runs_the_helper_with_the_running_kernel(self):
        conf = (SUPPORT/'usr/lib/systemd/system/linux-modules-cleanup.service.d/10-m4-keep-entries.conf').read_text()
        lines = [l for l in conf.splitlines() if l.startswith('ExecStart')]
        self.assertEqual(lines, ['ExecStart=', 'ExecStart=/usr/lib/m4/modules-cleanup %v'])

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'needs systemd-analyze')
    def test_systemd_accepts_the_unit_with_the_drop_in(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        unit_dir = d/'usr/lib/systemd/system'
        (unit_dir/'linux-modules-cleanup.service.d').mkdir(parents=True)
        shutil.copy(INPUTS/'usr__lib__systemd__system__linux-modules-cleanup.service', unit_dir/'linux-modules-cleanup.service')
        shutil.copy(SUPPORT/'usr/lib/systemd/system/linux-modules-cleanup.service.d/10-m4-keep-entries.conf', unit_dir/'linux-modules-cleanup.service.d/10-m4-keep-entries.conf')
        for sub in ('usr/bin', 'usr/lib/m4', 'bin'):
            (d/sub).mkdir(parents=True, exist_ok=True)
        (d/'usr/lib/m4/modules-cleanup').write_text('#!/bin/sh\n')
        (d/'usr/lib/m4/modules-cleanup').chmod(0o755)
        (d/'bin/bash').write_text('#!/bin/sh\n')
        (d/'bin/bash').chmod(0o755)
        p = subprocess.run(['systemd-analyze', '--root=%s' % d, 'verify', '--man=no', 'linux-modules-cleanup.service'], capture_output=True, text=True,
                           timeout=60)
        self.assertNotRegex(p.stderr, r'(?i)unknown|invalid|failed to parse|bad message')


if __name__ == '__main__':
    unittest.main(verbosity=1)
