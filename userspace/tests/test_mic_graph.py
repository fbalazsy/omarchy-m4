"""The built-in microphones' filter graph (93-m4-mic.conf -> j616-mic.json)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SUPPORT = os.path.join(HERE, "..", "support")
CONF = os.path.join(SUPPORT, "usr/share/wireplumber/wireplumber.conf.d/93-m4-mic.conf")
GRAPH = os.path.join(SUPPORT, "usr/share/m4/microphones/j616-mic.json")


class MicGraph(unittest.TestCase):
    def test_the_rule_points_at_a_packaged_graph_and_hides_the_raw_node(self):
        conf = open(CONF).read()
        path = re.search(r'filter-path = "([^"]+)"', conf).group(1)
        self.assertEqual(path, "/usr/share/m4/microphones/j616-mic.json")
        self.assertTrue(os.path.exists(os.path.join(SUPPORT, path.lstrip("/"))))
        self.assertIn('alsa.driver_name = "snd_soc_aop"', conf)
        self.assertIn("hide-parent = true", conf)

    def test_the_graph_captures_the_aop_node_and_offers_one_mono_source(self):
        graph = open(GRAPH).read()
        self.assertIn('"target.object": "alsa_input.platform-audio.2.auto.multichannel-input"', graph)
        self.assertIn('"node.name": "audio_effect.j616-mic"', graph)
        self.assertIn('"node.name": "effect_output.j616-mic"', graph)
        self.assertIn('"media.class": "Audio/Source"', graph)
        self.assertEqual(graph.count('"type": "builtin"'), 7)
        self.assertIn('"plugin": "https://chadmed.au/triforce"', graph)
        # the builtin mixer caps Gain at 10: the +20 dB over J316 is three stages, not one
        gains = [float(g) for g in re.findall(r'"Gain 1": ([0-9.]+)', graph)]
        self.assertEqual(gains[-3:], [10.0, 10.0, 5.8])


if __name__ == "__main__":
    unittest.main()
