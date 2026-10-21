#!/usr/bin/python3 -I
"""The initramfs MODULES list against lane K's kernel: every listed module is a module there, and everything nvme_apple needs that is not a symbol
dependency (apple-sart, apple-rtkit, apple-mailbox, the apple PM domains, the DART, ext4) is built in, because the initramfs inserts only the
listed modules.   python3 -B -I test_initramfs_modules.py

Evidence order: (1) the effective config, from `make O=<tmp> m4_defconfig` of K's tree (M4_KERNEL_SRC, default /work/n1/kernel/linux-m4); (2) K's package
(`modules.builtin`, M4_KERNEL_PKG or /reports/n1/kernel/packages/linux-m4-*.pkg.tar.zst). A missing source is a skip, never a pass."""
import glob
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

TREES = Path(os.environ.get('M4_TREES', Path(__file__).resolve().parents[1]))
SRC = Path(os.environ.get('M4_KERNEL_SRC', '/work/n1/kernel/linux-m4'))
MODULES = {'nvme_core': 'CONFIG_NVME_CORE', 'nvme': 'CONFIG_BLK_DEV_NVME', 'nvme_apple': 'CONFIG_NVME_APPLE'}
BUILT_IN = ('CONFIG_APPLE_SART', 'CONFIG_APPLE_RTKIT', 'CONFIG_APPLE_MAILBOX', 'CONFIG_APPLE_PMGR_PWRSTATE', 'CONFIG_APPLE_DART', 'CONFIG_EXT4_FS',
            'CONFIG_PM_GENERIC_DOMAINS', 'CONFIG_BLOCK')
PACKAGE_BUILTIN = ('kernel/drivers/soc/apple/apple-sart.ko', 'kernel/drivers/soc/apple/apple-rtkit.ko', 'kernel/drivers/soc/apple/apple-mailbox.ko',
                   'kernel/drivers/iommu/apple-dart.ko', 'kernel/fs/ext4/ext4.ko')   # tristate drivers; APPLE_PMGR_PWRSTATE is a bool (no entry)


def hooks_modules(path):
    m = re.search(r'(?m)^MODULES=\(([^)]*)\)', path.read_text())
    return m.group(1).split()


def effective_config():
    if not (SRC/'Makefile').is_file():
        return None
    out = tempfile.mkdtemp()
    p = subprocess.run(['make', '-s', 'O=' + out, 'ARCH=arm64', '-C', str(SRC), 'm4_defconfig'], capture_output=True, text=True, timeout=300)
    cfg = Path(out)/'.config'
    if p.returncode != 0 or not cfg.is_file():
        return None
    return dict(re.findall(r'(?m)^(CONFIG_[A-Z0-9_]+)=(.*)$', cfg.read_text()))


class InitramfsModules(unittest.TestCase):
    def test_both_images_load_the_three_nvme_modules(self):
        for conf in (TREES/'support/etc/m4/mkinitcpio.conf', TREES/'support/usr/share/m4/rescue-mkinitcpio.conf'):
            self.assertEqual(hooks_modules(conf), ['nvme_core', 'nvme', 'nvme_apple'], conf)
        self.assertEqual(hooks_modules(TREES/'support/etc/m4/mkinitcpio.conf'), hooks_modules(TREES/'support/usr/share/m4/rescue-mkinitcpio.conf'),
                         'the rescue and the real image must load the same modules (QEMU boots the identical initramfs)')

    def test_effective_config_modules_and_builtins(self):
        cfg = effective_config()
        if cfg is None:
            self.skipTest('no kernel tree / make for m4_defconfig (M4_KERNEL_SRC)')
        for module, symbol in MODULES.items():
            self.assertEqual(cfg.get(symbol), 'm', '%s (%s) must be a module for mkinitcpio to include it' % (module, symbol))
        for symbol in BUILT_IN:
            self.assertEqual(cfg.get(symbol), 'y', '%s must be built in: the initramfs does not insert it' % symbol)

    def test_package_modules_builtin_lists_the_tristate_dependencies(self):
        pkgs = sorted(glob.glob(os.environ.get('M4_KERNEL_PKG', '/reports/n1/kernel/packages/linux-m4-[0-9]*.pkg.tar.zst')))
        if not pkgs:
            self.skipTest('no linux-m4 package')
        p = subprocess.run(['tar', '--zstd', '-xOf', pkgs[-1], '--wildcards', 'usr/lib/modules/*/modules.builtin'], capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr)
        listed = set(p.stdout.split())
        for entry in PACKAGE_BUILTIN:
            self.assertIn(entry, listed, entry)
        # and none of the three loaded modules is built in (then `modprobe`/mkinitcpio would not find a module file)
        for name in ('nvme-core.ko', 'nvme.ko', 'nvme-apple.ko'):
            self.assertFalse([l for l in listed if l.endswith('/' + name)], name)


if __name__ == '__main__':
    unittest.main(verbosity=1)
