#!/usr/bin/python3 -I
"""m4-speakers tests: the owner-FIR install validates the WAVs, writes /etc/m4/speakers + the WirePlumber override, removes both."""
import importlib.machinery
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]/'support/usr/bin/m4-speakers'
loader = importlib.machinery.SourceFileLoader('m4speakers', str(SOURCE))
spec = importlib.util.spec_from_loader('m4speakers', loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


def wav(frames=8192, fmt=3, channels=1, rate=48000, bits=32):
    data = b'\0' * (frames * 4)
    fmt_chunk = struct.pack('<HHIIHH', fmt, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt_chunk + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body


class SpeakersTool(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root, self.src = Path(d.name)/'root', Path(d.name)/'kit'
        self.src.mkdir()
        m.ROOT = self.root
        for name in m.FILES:
            (self.src/name).write_bytes(wav())

    def test_install_writes_the_firs_and_the_override_then_remove_undoes_both(self):
        m.install(self.src)
        for dest in m.FILES.values():
            self.assertEqual((self.root/m.SPEAKERS/dest).read_bytes(), wav())
            self.assertEqual((self.root/m.SPEAKERS/dest).stat().st_mode & 0o777, 0o644)
        rule = (self.root/m.OVERRIDE).read_text()
        self.assertIn('/usr/share/m4/speakers/j616-graph-macos.json', rule)
        self.assertIn('hw:AppleJ616,1', rule)
        m.remove()
        self.assertFalse((self.root/m.OVERRIDE).exists())
        self.assertEqual(list((self.root/m.SPEAKERS).iterdir()), [])

    def test_the_graph_reads_the_installed_names(self):
        graph = (SOURCE.parents[1]/'share/m4/speakers/j616-graph-macos.json').read_text()
        for dest in m.FILES.values():
            self.assertIn('/etc/m4/speakers/' + dest, graph)

    def test_a_wrong_wav_installs_nothing(self):
        for bad in (wav(frames=4096), wav(fmt=1, bits=16), wav(channels=2), wav(rate=44100), b'not a wav'):
            (self.src/'j616-tweeter-L-48000.wav').write_bytes(bad)
            with self.assertRaises(SystemExit):
                m.install(self.src)
            self.assertFalse((self.root/m.OVERRIDE).exists())
            self.assertFalse((self.root/m.SPEAKERS).exists())


if __name__ == '__main__':
    unittest.main()
