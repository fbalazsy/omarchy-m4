#!/usr/bin/python3 -I
"""N2/K2 (N3: entries only; the rescue kernel's tree is the package linux-m4-rescue's): the module trees of every boot entry survive a linux-m4
upgrade, with REAL pacman.

Fake `linux-m4` packages of two kvers are installed with `pacman -U` into the container's /usr/lib/modules under test-only kver names (their own
database, their own hook directory holding only m4-support's two keep hooks, rewritten to run the source m4-boot with test paths); the second
transaction replaces the first. Kills (SIGKILL of pacman) are done by an extra hook before the keep hooks (after Pre) and before the restore hook
(after the removals). Needs root, pacman and bsdtar; skipped otherwise.   python3 -B -I test_keep_modules.py"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPORT = Path(os.environ.get('M4_SUPPORT_TREE', HERE.parent/'support'))
M4BOOT = SUPPORT/'usr/bin/m4-boot'
HOOKS = SUPPORT/'usr/share/libalpm/hooks'
MODULES = Path('/usr/lib/modules')
A, B, Z = '9.9.9-m4.testA', '9.9.9-m4.testB', '9.9.9-m4.testZ'
OWN = (A, B, Z, '.m4-keep')


def can_run():
    return os.geteuid() == 0 and shutil.which('pacman') and shutil.which('depmod')


@unittest.skipUnless(can_run(), 'needs root, pacman and depmod')
class KeepModules(unittest.TestCase):
    def setUp(self):
        for n in OWN:
            self.assertFalse((MODULES/n).exists(), '%s exists: a previous run did not clean up' % n)
        self.dir = Path(tempfile.mkdtemp(prefix='keep-'))
        self.addCleanup(self.cleanup)
        d = self.dir
        for sub in ('db', 'cache', 'hooks', 'boot/m4/entries', 'pkgs'):
            (d/sub).mkdir(parents=True)
        self.conf = d/'pacman.conf'
        self.conf.write_text('[options]\nRootDir = /\nDBPath = %s/db\nCacheDir = %s/cache\nLogFile = %s/pacman.log\nHookDir = %s/hooks\n'
                             'SigLevel = Never\nArchitecture = auto\n' % (d, d, d, d))
        self.rescue = d/'rescue-kver'
        self.kept = d/'kept-trees'
        self.rescue.write_text(Z + '\n')
        self.pacman = 'pacman --config %s' % self.conf
        self.boot = ['--boot', str(d/'boot'), '--rescue-kver-file', str(self.rescue), '--kept-file', str(self.kept), '--pacman', self.pacman,
                     '--running', Z]
        prefix = '/usr/bin/python3 -I %s %s ' % (M4BOOT, ' '.join('"%s"' % a if ' ' in a else a for a in self.boot))
        for name in ('15-m4-keep-modules-pre.hook', '15-m4-keep-modules-post.hook'):
            text = (HOOKS/name).read_text()
            self.assertIn('Exec = /usr/bin/m4-boot ', text)
            (d/'hooks'/name).write_text(text.replace('Exec = /usr/bin/m4-boot ', 'Exec = ' + prefix))
        # libalpm always adds /usr/share/libalpm/hooks: mask every hook of the container (systemd, mkinitcpio, depmod, ...) by name, as an admin would
        for f in os.listdir('/usr/share/libalpm/hooks'):
            if f.endswith('.hook') and not (d/'hooks'/f).exists():
                os.symlink('/dev/null', d/'hooks'/f)
        self.pkg = {A: self.package(A, 1), B: self.package(B, 2)}

    def cleanup(self):
        for n in OWN:
            shutil.rmtree(MODULES/n, ignore_errors=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def package(self, kver, rel):
        root = self.dir/('pkg-' + kver)
        tree = root/'usr/lib/modules'/kver
        (tree/'kernel/drivers').mkdir(parents=True)
        (tree/'vmlinuz').write_bytes(('IMAGE ' + kver).encode() * 50)
        (tree/'kernel/drivers/m.ko.zst').write_bytes(b'module ' + kver.encode())
        (tree/'modules.builtin').write_text('kernel/drivers/builtin.ko\n')
        (tree/'pkgbase').write_text('linux-m4\n')
        (root/'.PKGINFO').write_text('pkgname = linux-m4\npkgbase = linux-m4\npkgver = 7.1.13-%d\npkgdesc = test\nurl = x\nbuilddate = 1\npackager = t\n'
                                     'size = 1000\narch = aarch64\nlicense = GPL\n' % rel)
        out = self.dir/'pkgs'/('linux-m4-7.1.13-%d-aarch64.pkg.tar.zst' % rel)
        subprocess.run(['tar', '--zstd', '-C', str(root), '-cf', str(out), '.PKGINFO', 'usr'], check=True)
        return out

    def pacman_U(self, pkg, *extra):
        (self.dir/'db/db.lck').unlink(missing_ok=True)
        return subprocess.run(['pacman', '--config', str(self.conf), '--noconfirm', *extra, '-U', str(pkg)], capture_output=True, text=True, timeout=300)

    def m4boot(self, *argv):
        return subprocess.run(['python3', '-I', str(M4BOOT), *self.boot, *argv], capture_output=True, text=True, timeout=120)

    def entry(self, kver, name=None):
        d = self.dir/'boot/m4/entries'/(name or kver)
        d.mkdir(parents=True)
        (d/'entry.conf').write_text('abi=1\nkver=%s\ncmdline=root=x\n' % kver)

    def install_a(self):
        p = self.pacman_U(self.pkg[A])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((MODULES/A/'vmlinuz').is_file())
        return (MODULES/A/'vmlinuz').read_bytes()

    def hook_kill(self, name, when, order='20'):
        (self.dir/'hooks'/('%s-kill-%s.hook' % (order, name))).write_text(
            '[Trigger]\nType = Package\nOperation = Upgrade\nTarget = linux-m4\n\n[Action]\nDescription = kill pacman\nWhen = %s\n'
            "Exec = /bin/sh -c 'kill -9 $PPID'\n" % when)

    def installed_kver(self):
        p = subprocess.run(['pacman', '--config', str(self.conf), '-Qi', 'linux-m4'], capture_output=True, text=True)
        return [l.split(':', 1)[1].strip() for l in p.stdout.splitlines() if l.startswith('Version')]

    # ---- the three outcomes the brief asks for ----------------------------------------------------------------------------------
    def test_an_entrys_tree_survives_the_upgrade(self):
        self.entry(A)
        before = self.install_a()
        p = self.pacman_U(self.pkg[B])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.installed_kver(), ['7.1.13-2'])
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        self.assertTrue((MODULES/A/'kernel/drivers/m.ko.zst').is_file())
        self.assertTrue((MODULES/A/'modules.dep').exists(), 'depmod -a ran on the restored tree')
        self.assertTrue((MODULES/B/'vmlinuz').is_file())
        self.assertFalse((MODULES/'.m4-keep').exists(), 'the transient copies are gone')
        self.assertEqual(self.kept.read_text().split(), [A])
        owner = subprocess.run(['pacman', '--config', str(self.conf), '-Qqo', str(MODULES/A/'vmlinuz')], capture_output=True, text=True)
        self.assertNotEqual(owner.returncode, 0, 'the kept tree is no package\'s any more')

    def test_the_tree_of_a_boot_entry_survives_the_upgrade(self):
        self.entry(A, name='entry-of-a')                  # entry name differs from the kver: entry.conf's kver= decides
        before = self.install_a()
        self.assertEqual(self.pacman_U(self.pkg[B]).returncode, 0)
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        self.assertEqual(self.kept.read_text().split(), [A])

    def test_a_tree_nobody_needs_goes_with_the_package(self):
        self.install_a()
        self.assertEqual(self.pacman_U(self.pkg[B]).returncode, 0)
        self.assertFalse((MODULES/A/'vmlinuz').exists())
        self.assertFalse((MODULES/A/'kernel').exists())
        self.assertFalse(self.kept.exists())
        self.assertTrue((MODULES/B/'vmlinuz').is_file())

    # ---- kills ------------------------------------------------------------------------------------------------------------------
    def test_a_kill_after_the_pre_hook_then_a_rerun(self):
        self.entry(A)
        before = self.install_a()
        self.hook_kill('pre', 'PreTransaction')           # sorts after 15-m4-keep-modules-pre
        p = self.pacman_U(self.pkg[B])
        self.assertEqual(p.returncode, -9, p.stdout + p.stderr)
        self.assertEqual(self.installed_kver(), ['7.1.13-1'])
        self.assertTrue((MODULES/'.m4-keep'/A/'vmlinuz').is_file(), 'the Pre hook had run')
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        (self.dir/'hooks/20-kill-pre.hook').unlink()
        p = self.pacman_U(self.pkg[B])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        self.assertFalse((MODULES/'.m4-keep').exists())

    def test_a_kill_after_the_removals_before_the_restore_then_a_rerun(self):
        self.entry(A)
        before = self.install_a()
        self.hook_kill('post', 'PostTransaction', order='14')     # after the transaction's file operations, before 15-m4-keep-modules-post
        p = self.pacman_U(self.pkg[B])
        self.assertEqual(p.returncode, -9, p.stdout + p.stderr)
        self.assertFalse((MODULES/A/'vmlinuz').exists(), 'pacman removed the old build\'s files')
        self.assertTrue((MODULES/'.m4-keep'/A/'vmlinuz').is_file())
        (self.dir/'hooks/14-kill-post.hook').unlink()
        p = self.pacman_U(self.pkg[B])                        # the rerun (a reinstall of the same package)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        self.assertTrue((MODULES/A/'modules.dep').exists())
        self.assertFalse((MODULES/'.m4-keep').exists())
        self.assertEqual(self.kept.read_text().split(), [A])

    def test_m4_boot_heals_a_killed_transaction_without_pacman(self):
        self.entry(A)
        before = self.install_a()
        self.hook_kill('post', 'PostTransaction', order='14')
        self.assertEqual(self.pacman_U(self.pkg[B]).returncode, -9)
        p = self.m4boot('keep', 'restore')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        self.assertFalse((MODULES/'.m4-keep').exists())

    # ---- the first transaction: the pre hook of the NEW m4-support is not on the disk yet -----------------------------------------
    def test_the_helper_covers_a_single_transaction_that_brings_its_own_hooks(self):
        helper = HERE.parent.parent/'cleanup/keep-before-upgrade.sh'
        if not helper.is_file():
            self.skipTest('no helper next to the tests')
        (self.dir/'hooks/15-m4-keep-modules-pre.hook').unlink()          # what the disk has before m4-support 1.0-2 is installed
        self.entry(A)
        before = self.install_a()
        # the m4-support package: m4-boot and the rescue kver file (the kver the helper must keep)
        tree = self.dir/'support-pkg'
        (tree/'usr/bin').mkdir(parents=True)
        (tree/'usr/lib/m4').mkdir(parents=True)
        shutil.copy2(M4BOOT, tree/'usr/bin/m4-boot')
        (tree/'usr/lib/m4/rescue-kver').write_text(A + '\n')
        pkg = self.dir/'m4-support-test.pkg.tar.zst'
        subprocess.run(['tar', '--zstd', '-C', str(tree), '-cf', str(pkg), 'usr'], check=True)
        # without the helper the tree is lost: the control
        p = self.pacman_U(self.pkg[B])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((MODULES/A/'vmlinuz').exists(), 'control: no Pre hook, no helper, the tree went with the package')
        # now with the helper (the options are the test's paths; the helper itself passes --rescue-kver-file)
        self.assertEqual(self.pacman_U(self.pkg[A], '--overwrite', '/usr/lib/modules/*').returncode, 0)
        # the helper supplies the package's own --rescue-kver-file: take that pair out of the test's options
        opts, skip = [], False
        for a in self.boot:
            if skip:
                skip = False
            elif a == '--rescue-kver-file':
                skip = True
            else:
                opts.append(a)
        r = subprocess.run(['sh', str(helper), str(pkg), *opts], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((MODULES/'.m4-keep'/A/'vmlinuz').is_file())
        p = self.pacman_U(self.pkg[B])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((MODULES/A/'vmlinuz').read_bytes(), before)
        self.assertFalse((MODULES/'.m4-keep').exists())

    # ---- prune ------------------------------------------------------------------------------------------------------------------
    def test_prune_drops_a_kept_tree_that_is_neither_rescue_nor_an_entry(self):
        self.entry(A)
        self.install_a()
        self.assertEqual(self.pacman_U(self.pkg[B]).returncode, 0)
        self.assertTrue((MODULES/A/'vmlinuz').is_file())
        p = self.m4boot('prune')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((MODULES/A/'vmlinuz').is_file(), 'the entry still names it')
        shutil.rmtree(self.dir/'boot/m4/entries'/A)
        p = self.m4boot('prune')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((MODULES/A).exists())
        self.assertFalse(self.kept.read_text().split())
        self.assertTrue((MODULES/B/'vmlinuz').is_file(), 'a package\'s tree is never pruned')

    def test_prune_never_drops_the_rescue_kvers_tree_or_a_tree_a_package_owns_again(self):
        self.entry(A)
        self.install_a()
        self.assertEqual(self.pacman_U(self.pkg[B]).returncode, 0)
        self.rescue.write_text(A + '\n')
        shutil.rmtree(self.dir/'boot/m4/entries'/A)             # no entry any more, but A is the rescue kver: never pruned
        self.assertEqual(self.m4boot('prune').returncode, 0)
        self.assertTrue((MODULES/A/'vmlinuz').is_file())
        self.rescue.write_text(Z + '\n')
        # linux-m4 of kver A is installed again (a downgrade; the kept files are unowned, so pacman needs --overwrite): the tree is a package's again,
        # the record goes, the files stay
        p = self.pacman_U(self.pkg[A], '--overwrite', '/usr/lib/modules/*')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.m4boot('prune').returncode, 0)
        self.assertTrue((MODULES/A/'vmlinuz').is_file())
        self.assertFalse(self.kept.read_text().split())

    def test_the_rescue_kver_alone_is_no_longer_kept_by_the_hooks(self):
        # N3: linux-m4-rescue owns that tree; the upgrade hooks keep entries' trees only (control: the tree goes with the package here)
        self.rescue.write_text(A + '\n')
        self.install_a()
        self.assertEqual(self.pacman_U(self.pkg[B]).returncode, 0)
        self.assertFalse((MODULES/A/'vmlinuz').exists())
        self.assertFalse((MODULES/'.m4-keep').exists())

    def test_prune_leaves_the_running_kernels_tree(self):
        (MODULES/Z).mkdir()
        (MODULES/Z/'vmlinuz').write_bytes(b'running')
        self.kept.write_text(Z + '\n')                       # recorded as kept, no entry, no package: but it is the running kernel (--running Z)
        self.assertEqual(self.m4boot('prune').returncode, 0)
        self.assertTrue((MODULES/Z/'vmlinuz').is_file())

    def test_the_listing_names_a_missing_rescue_tree(self):
        p = self.m4boot('keep', 'list')
        self.assertIn('MODULE TREE MISSING', p.stdout)
        self.assertIn(Z, p.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=1)
