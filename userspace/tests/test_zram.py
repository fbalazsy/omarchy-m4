#!/usr/bin/python3 -I
"""STATUS G-29: swap on zram. The drop-in configures zram0 with an algorithm the kernel builds, and outranks Omarchy's.
python3 -B -I test_zram.py"""
import configparser
import unittest
from pathlib import Path

SUPPORT = Path(__file__).resolve().parents[1]/'support'
DROPIN = SUPPORT/'usr/lib/systemd/zram-generator.conf.d/95-m4.conf'
SYSCTL = SUPPORT/'usr/lib/sysctl.d/99-m4-zram.conf'


class Zram(unittest.TestCase):
    def test_zram0_is_an_lz4_swap_capped_at_8_gib(self):
        conf = configparser.ConfigParser()
        conf.read_string(DROPIN.read_text())
        self.assertEqual(conf.sections(), ['zram0'])
        self.assertEqual(dict(conf['zram0']), {'zram-size': 'min(ram / 4, 8192)', 'compression-algorithm': 'lz4',
                                               'swap-priority': '100', 'fs-type': 'swap'})

    def test_the_dropin_sorts_after_omarchys_zstd_one(self):
        # the kernel has CONFIG_ZRAM_BACKEND_LZ4 only; drop-ins later in name order win per key
        self.assertGreater(DROPIN.name, '90-omarchy.conf')
        self.assertFalse((SUPPORT/'usr/lib/systemd/zram-generator.conf').exists())

    def test_reclaim_sysctls(self):
        lines = [l.split('=') for l in SYSCTL.read_text().splitlines() if l.strip() and not l.startswith('#')]
        self.assertEqual({k.strip(): v.strip() for k, v in lines}, {'vm.swappiness': '180', 'vm.watermark_boost_factor': '0',
                                                                    'vm.watermark_scale_factor': '125', 'vm.page-cluster': '0'})


if __name__ == '__main__':
    unittest.main()
