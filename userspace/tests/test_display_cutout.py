#!/usr/bin/python3 -I
"""The J616s panel's notch for Omarchy's bar (/usr/share/omarchy-platform/display-cutouts.json, the format omarchy-mac ships and
Omarchy's bar reads: BarModel.js parseCutouts).   python3 -B -I test_display_cutout.py   (tree: ../support)"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUTOUTS = ROOT/'support/usr/share/omarchy-platform/display-cutouts.json'


class Cutout(unittest.TestCase):
    def test_the_j616s_panel_with_its_measured_notch(self):
        panels = json.loads(CUTOUTS.read_text())['panels']
        self.assertEqual(panels, [{'connector': 'eDP', 'width': 3456, 'height': 2234, 'top': 64}])

    def test_every_entry_passes_the_bar_parser_rules(self):
        for p in json.loads(CUTOUTS.read_text())['panels']:
            self.assertIsInstance(p['connector'], str)
            self.assertTrue(p['connector'])
            self.assertTrue(p['width'] > 0 and p['height'] > 0)
            self.assertTrue(0 < p['top'] < p['height'])


if __name__ == '__main__':
    unittest.main()
