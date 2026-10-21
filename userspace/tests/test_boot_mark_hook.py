#!/usr/bin/python3 -I
"""m4-boot-mark (initramfs late hook, N1-REV-16) and the private Mesa's baked paths (N1-REV-14).   python3 -B -I test_boot_mark_hook.py"""
import glob
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TREES = Path(os.environ.get('M4_TREES', Path(__file__).resolve().parents[1]))
HOOK = TREES/'support/usr/lib/initcpio/hooks/m4-boot-mark'
SHELL = ['/usr/lib/initcpio/busybox', 'ash'] if Path('/usr/lib/initcpio/busybox').exists() else ['/bin/sh']


class BootMark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'new_root'
        (self.root/'boot/m4').mkdir(parents=True)
        (self.root/'boot/m4/loader.conf').write_text('abi=1\ndefault=7.1.13-m4.2\n')
        self.dt = Path(self.tmp.name)/'boot-entry'
        self.cmdline = Path(self.tmp.name)/'cmdline'
        self.cmdline.write_text('root=UUID=x rw quiet\n')
        self.pending = self.root/'boot/m4/boot-pending'

    def run_hook(self):
        script = ('msg() { echo "MSG $1"; }\n. %s\nm4_mark_root=%s\nm4_mark_dt=%s\nm4_mark_cmdline=%s\nrun_latehook\n'
                  % (HOOK, self.root, self.dt, self.cmdline))
        p = subprocess.run(SHELL + ['-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_writes_the_booted_entry_from_the_device_tree(self):
        self.dt.write_bytes(b'7.1.13-m4\0')
        out = self.run_hook()
        self.assertEqual(self.pending.read_text(), '7.1.13-m4\n')
        self.assertIn('boot-pending written (7.1.13-m4)', out)
        self.assertEqual([p.name for p in (self.root/'boot/m4').iterdir() if p.name.startswith('.')], [])

    def test_falls_back_to_the_default_of_loader_conf(self):
        self.run_hook()           # no /chosen property: QEMU, or a loader without it
        self.assertEqual(self.pending.read_text(), '7.1.13-m4.2\n')

    def test_rescue_boot_writes_nothing(self):
        self.cmdline.write_text('root=UUID=x rw m4.safe=1\n')
        self.dt.write_bytes(b'rescue\0')
        out = self.run_hook()
        self.assertFalse(self.pending.exists())
        self.assertIn('safe mode', out)

    def test_no_loader_conf_means_no_mark(self):
        (self.root/'boot/m4/loader.conf').unlink()
        out = self.run_hook()
        self.assertFalse(self.pending.exists())
        self.assertIn('nothing marked', out)

    def test_an_unwritable_boot_directory_is_reported_not_fatal(self):
        (self.root/'boot/m4/.boot-pending.tmp').mkdir()       # the temp file cannot be created: stands for a read-only root
        out = self.run_hook()
        self.assertFalse(self.pending.exists())
        self.assertIn('nothing marked', out)

    def test_a_hostile_entry_name_is_not_written(self):
        self.dt.write_bytes(b'../../etc/x y\0')
        self.run_hook()
        self.assertEqual(self.pending.read_text(), 'unknown\n')

    def test_the_hook_is_in_both_images_after_the_clock_and_before_shutdown(self):
        for conf in (TREES/'support/etc/m4/mkinitcpio.conf', TREES/'support/usr/share/m4/rescue-mkinitcpio.conf'):
            hooks = re.search(r'(?m)^HOOKS=\(([^)]*)\)', conf.read_text()).group(1).split()
            self.assertLess(hooks.index('m4-clock'), hooks.index('m4-boot-mark'), conf)
            self.assertLess(hooks.index('m4-boot-mark'), hooks.index('shutdown'), conf)
        self.assertTrue((TREES/'support/usr/lib/initcpio/install/m4-boot-mark').is_file())

    def test_the_systemd_unit_stays_as_a_harmless_repeat(self):
        self.assertTrue((TREES/'support/usr/lib/systemd/system/m4-boot-pending.service').is_file())


class DefaultRoot(unittest.TestCase):
    """N1-REV-17: with NO override the hook marks the root at /sysroot, where mkinitcpio's init mounts it (it defines no rootmnt)."""
    def test_the_default_root_is_sysroot(self):
        script = 'msg() { :; }\n. %s\nm4_mark_cmdline=/nonexistent\nrun_latehook\n' % HOOK
        p = subprocess.run(['/bin/sh', '-x', '-c', script], capture_output=True, text=True, timeout=30, env={'PATH': '/usr/bin:/bin'})
        self.assertIn('root=/sysroot', p.stderr)
        self.assertNotIn('/new_root', p.stderr)

    def test_a_fake_sysroot_gets_the_mark_without_any_override(self):
        # the real /sysroot path, no override: only where /sysroot is absent or an empty directory (a build container, never a booted system)
        sysroot = Path('/sysroot')
        if os.geteuid() != 0 or (sysroot.exists() and (not sysroot.is_dir() or any(sysroot.iterdir()))):
            self.skipTest('needs root and an absent or empty /sysroot')
        existed = sysroot.exists()
        try:
            os.makedirs('/sysroot/boot/m4')
        except OSError as e:
            self.skipTest('cannot create /sysroot: %s' % e)
        try:
            Path('/sysroot/boot/m4/loader.conf').write_text('abi=1\ndefault=7.1.13-m4\n')
            script = ('msg() { echo "MSG $1"; }\n. %s\n'
                      'unset m4_mark_root m4_mark_dt rootmnt\nm4_mark_cmdline=/nonexistent\nrun_latehook\n' % HOOK)
            p = subprocess.run(['/bin/sh', '-c', script], capture_output=True, text=True, timeout=30, env={'PATH': '/usr/bin:/bin'})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(Path('/sysroot/boot/m4/boot-pending').read_text(), '7.1.13-m4\n', p.stdout + p.stderr)
        finally:
            shutil.rmtree('/sysroot', ignore_errors=True)
            if existed:
                os.mkdir('/sysroot')


class RealInitramfs(unittest.TestCase):
    """Build the initramfs with the real mkinitcpio and the real linux-m4 modules, unpack it, and check the init script's root mount point."""
    def kernel_package(self):
        for pattern in (os.environ.get('M4_KERNEL_PKG', ''), '/work/n1/kernel/packages/linux-m4-[0-9]*.pkg.tar.zst', '/reports/n1/kernel/packages/linux-m4-[0-9]*.pkg.tar.zst'):
            found = sorted(glob.glob(pattern)) if pattern else []
            if found:
                return found[-1]
        return None

    def test_init_mounts_the_root_where_the_hook_looks(self):
        pkg = self.kernel_package()
        if not (shutil.which('mkinitcpio') and shutil.which('lsinitcpio') and shutil.which('depmod') and pkg and os.geteuid() == 0):
            self.skipTest('needs root, mkinitcpio, depmod and a linux-m4 package')
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run(['tar', '--zstd', '-xf', pkg, '-C', str(d), 'usr/lib/modules'], check=True, timeout=300)
            (d/'lib').symlink_to('usr/lib')
            kver = sorted(p.name for p in (d/'usr/lib/modules').iterdir())[0]
            subprocess.run(['depmod', '-b', str(d/'usr'), kver], check=True, timeout=120)
            hookdir = d/'hookdir'
            for sub in ('install', 'hooks'):
                shutil.copytree('/usr/lib/initcpio/' + sub, hookdir/sub)
                for f in (TREES/'support/usr/lib/initcpio'/sub).iterdir():
                    shutil.copy2(f, hookdir/sub/f.name)
            out = d/'initramfs.img'
            conf = TREES/'support/etc/m4/mkinitcpio.conf'
            p = subprocess.run(['mkinitcpio', '-n', '-D', str(hookdir), '-r', str(d), '-k', kver, '-c', str(conf), '-g', str(out)],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(p.returncode, 0, p.stdout[-600:] + p.stderr[-600:])
            self.assertLess(out.stat().st_size, 16 * 1024 * 1024)
            unpacked = d/'unpacked'
            unpacked.mkdir()
            subprocess.run(['lsinitcpio', '-x', str(out)], cwd=unpacked, check=True, capture_output=True, timeout=120)
            init = (unpacked/'init').read_text()
            self.assertIn('"$mount_handler" /sysroot', init)
            self.assertNotIn('/new_root', init)
            self.assertNotRegex(init, r'(?m)^\s*rootmnt=')
            hook = (unpacked/'hooks/m4-boot-mark').read_text()
            self.assertIn('${rootmnt:-/sysroot}', hook)
            self.assertTrue([n for n in (unpacked/'lib/modules').rglob('nvme-apple.ko*')] or [n for n in unpacked.rglob('nvme-apple.ko*')],
                            'the real nvme_apple module is in the image')


if __name__ == '__main__':
    unittest.main(verbosity=1)
