#!/usr/bin/python3 -I
"""The owner's real libalpm hooks (dumped read-only from the disk: tests/hooks/) against the two transactions m4-migrate runs: the removal of the Asahi
boot chain and `pacman -U` of the N1 packages. A small model of libalpm's trigger matching (fnmatch without FNM_PATHNAME, `!` negation, Install/Upgrade/Remove
operations, /etc/pacman.d/hooks overriding /usr/share/libalpm/hooks by file name).   python3 -B -I test_pacman_hooks.py"""
import fnmatch
import glob
import os
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREES = Path(os.environ.get('M4_TREES', HERE.parent))
OWNER = HERE/'hooks'
MINE = {'90-mkinitcpio-install.hook': TREES/'support/etc/pacman.d/hooks/90-mkinitcpio-install.hook',
        '90-m4-boot.hook': TREES/'support/usr/share/libalpm/hooks/90-m4-boot.hook'}
GUARD = OWNER/'omarchy-update-pacman-guard'


def parse(path):
    hook = dict(triggers=[], action={})
    section = None
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            section = line.strip('[]')
            if section == 'Trigger':
                hook['triggers'].append(dict(Operation=[], Target=[], Type=None))
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if section == 'Trigger':
            t = hook['triggers'][-1]
            if key in ('Operation', 'Target'):
                t[key].append(value)
            else:
                t[key] = value
        else:
            hook['action'][key] = value
    return hook


def path_match(trigger, files):
    pos = [t for t in trigger['Target'] if not t.startswith('!')]
    neg = [t[1:] for t in trigger['Target'] if t.startswith('!')]
    return [f for f in files if any(fnmatch.fnmatchcase(f, p) for p in pos) and not any(fnmatch.fnmatchcase(f, n) for n in neg)]


def fires(hook, transaction):
    """transaction: list of (operation, package name, [paths])."""
    out = []
    for t in hook['triggers']:
        for op, name, files in transaction:
            if op not in t['Operation']:
                continue
            if t['Type'] == 'Package' and any(fnmatch.fnmatchcase(name, x) for x in t['Target']):
                out.append((name, 'package'))
            elif t['Type'] == 'Path':
                for f in path_match(t, files):
                    out.append((name, f))
    return out


def effective(hooks_dirs):
    """name -> parsed hook; later directories (the /etc override) replace earlier ones of the same file name."""
    names = {}
    for d in hooks_dirs:
        for f in sorted(Path(d).glob('*.hook')):
            names[f.name] = f
    return {n: parse(f) for n, f in names.items()}


def listing(pkg):
    p = subprocess.run(['tar', '--zstd', '-tf', str(pkg)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return [l.strip() for l in p.stdout.splitlines() if l.strip() and not l.startswith('.')]


def package(name):
    for pattern in (os.environ.get('M4_PKG_DIR', '') + '/' + name + '-*.pkg.tar.zst', str(TREES.parent/'packages'/(name + '-*.pkg.tar.zst')),
                    '/reports/n1/userspace/packages/' + name + '-*.pkg.tar.zst', '/reports/n1/kernel/packages/' + name + '-[0-9]*.pkg.tar.zst'):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


class Hooks(unittest.TestCase):
    def setUp(self):
        self.stock = effective([OWNER])                                         # the owner's dump (hooks/ holds only the files of /usr/share/libalpm/hooks)
        self.mine = effective([OWNER, TREES/'support/etc/pacman.d/hooks'])      # + the override, same file name wins
        self.mine['90-m4-boot.hook'] = parse(MINE['90-m4-boot.hook'])

    def removal(self):
        asahi = ['usr/lib/modules/7.1.13-asahi3/', 'usr/lib/modules/7.1.13-asahi3/vmlinuz', 'usr/lib/modules/7.1.13-asahi3/pkgbase',
                 'usr/lib/modules/7.1.13-asahi3/kernel/drivers/nvme/host/nvme-apple.ko', 'usr/lib/modules/7.1.13-asahi3/dtbs/apple/t6040-j616s.dtb']
        return [('Remove', 'm1n1', ['usr/lib/asahi-boot/m1n1.bin', 'usr/bin/m1n1-tool']), ('Remove', 'uboot-asahi', ['usr/lib/asahi-boot/u-boot.bin']),
                ('Remove', 'asahi-meta', ['usr/share/doc/asahi-meta/README']), ('Remove', 'linux-asahi', asahi)]

    def install(self):
        tx = []
        for name in ('linux-m4', 'm4-support', 'm4-firmware', 'm4-tools'):
            pkg = package(name)
            if pkg is None:
                self.skipTest('package %s not found' % name)
            tx.append(('Install', name, listing(pkg)))
        tx.append(('Upgrade', 'quickshell', ['usr/bin/quickshell', 'usr/lib/qt6/qml/Quickshell/']))   # replaces the owner's newer build with -U
        tx.append(('Install', 'm4-mesa', ['usr/lib/m4-mesa/lib64/libgallium-26.3.0-devel.so']))
        return tx

    # ---- the removal transaction ---------------------------------------------------------------------------------
    def test_removing_the_asahi_boot_chain_runs_only_harmless_hooks(self):
        tx = self.removal()
        hit = {n: fires(h, tx) for n, h in self.mine.items()}
        self.assertEqual(hit['00-omarchy-update-guard.hook'], [], 'the guard watches Upgrade only')
        self.assertEqual(hit['95-m1n1-install.hook'], [], 'update-m1n1 triggers on Install/Upgrade of dtbs and asahi-boot files only')
        self.assertEqual(hit['90-mkinitcpio-install.hook'], [])
        removed = hit['60-mkinitcpio-remove.hook']
        self.assertTrue(removed and all(n == 'linux-asahi' and p.endswith('/vmlinuz') for n, p in removed), removed)
        self.assertEqual(self.mine['60-mkinitcpio-remove.hook']['action']['When'], 'PreTransaction')
        self.assertIn('NeedsTargets', self.mine['60-mkinitcpio-remove.hook']['action'])    # it acts on the removed kernel's files only
        self.assertTrue(hit['60-depmod.hook'])

    # ---- the install transaction ---------------------------------------------------------------------------------
    def test_m1n1_hook_never_matches_the_n1_packages(self):
        for op, name, files in self.install():
            self.assertEqual(fires(self.mine['95-m1n1-install.hook'], [(op, name, files)]), [], name)
        self.assertTrue(any('/dtb/apple/t6040-j616s.dtb' in f for f in self.install()[0][2]), 'dtb/, not dtbs/')

    def test_stock_mkinitcpio_hook_would_fire_and_the_override_does_not(self):
        tx = self.install()
        self.assertTrue(fires(self.stock['90-mkinitcpio-install.hook'], tx), 'negative control: the stock hook triggers on linux-m4')
        self.assertEqual(fires(self.mine['90-mkinitcpio-install.hook'], tx), [])
        self.assertEqual(self.mine['90-mkinitcpio-install.hook']['action']['Exec'], '/usr/bin/true')

    def test_depmod_runs_and_precedes_the_m4_boot_hook(self):
        tx = self.install()
        self.assertTrue(any(p == 'usr/lib/modules/7.1.13-m4/' for _n, p in fires(self.mine['60-depmod.hook'], tx)))
        self.assertTrue(fires(self.mine['90-m4-boot.hook'], tx))
        post = sorted(n for n, h in self.mine.items() if h['action'].get('When') == 'PostTransaction' and fires(h, tx))
        self.assertLess(post.index('60-depmod.hook'), post.index('90-m4-boot.hook'))

    def test_modules_backup_hooks_act_on_the_running_kernel_not_on_linux_m4(self):
        tx = self.install()
        for name in ('10-linux-modules-pre.hook', '10-linux-modules-post.hook'):
            self.assertTrue(fires(self.mine[name], tx), name)
            self.assertIn('${KVER:-$(uname -r)}', self.mine[name]['action']['Exec'])      # the booted kernel's module tree, backed up and restored

    def test_the_omarchy_guard_lets_u_and_r_through_and_stops_a_sysupgrade(self):
        tx = self.install()
        self.assertTrue(fires(self.mine['00-omarchy-update-guard.hook'], tx), 'it does fire (an Upgrade is in the transaction)')

        def guard(cmdline):
            env = {'PATH': '/usr/bin:/bin', 'OMARCHY_PACMAN_CMDLINE': cmdline}
            return subprocess.run(['bash', str(GUARD)], env=env, capture_output=True, text=True, timeout=30).returncode
        self.assertEqual(guard('pacman -U --noconfirm --needed --overwrite /etc/pacman.d/hooks/90-mkinitcpio-install.hook /var/lib/m4-repo/linux-m4-7.1.13-1-aarch64.pkg.tar.zst'), 0)
        for name in ('m1n1', 'uboot-asahi', 'asahi-meta', 'linux-asahi'):
            self.assertEqual(guard('pacman -R --noconfirm ' + name), 0, name)
        self.assertNotEqual(guard('pacman -Syu'), 0, 'negative control: a direct system upgrade is stopped')


if __name__ == '__main__':
    unittest.main(verbosity=1)
