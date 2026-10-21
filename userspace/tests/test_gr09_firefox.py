#!/usr/bin/python3 -I
"""GR-09: Firefox on the aurora GPU with VA-API decoding on the AVD, packaged in m4-support: default prefs in
/usr/lib/firefox/defaults/pref/m4-firefox.js and MOZ_DISABLE_RDD_SANDBOX from the Hyprland session environment
(session-env.sh) instead of a launcher.   python3 -B -I test_gr09_firefox.py   (tree: ../support)"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT/'support'
M4 = SUPPORT/'usr/lib/m4'
PREFS = SUPPORT/'usr/lib/firefox/defaults/pref/m4-firefox.js'
AURORA = ROOT.parent/'packages/m4-aurora-mesa'


class DefaultPrefs(unittest.TestCase):
    def prefs(self):
        out = {}
        for line in PREFS.read_text().splitlines():
            if not line.strip() or line.startswith('//'):
                continue
            m = re.fullmatch(r'pref\("([\w.-]+)", (true|false|-?\d+|"[^"]*")\);', line)
            self.assertIsNotNone(m, line)   # a malformed line makes Firefox drop the rest of the file
            self.assertNotIn(m.group(1), out, 'set twice')
            out[m.group(1)] = json.loads(m.group(2))
        return out

    def test_gpu_webrender_vaapi_av1_with_the_software_fallback_kept(self):
        p = self.prefs()
        want = {
            'gfx.webrender.all': True, 'gfx.webrender.software': False, 'layers.acceleration.force-enabled': True,
            'widget.dmabuf.force-enabled': True,                 # VA-API frames reach WebRender as dmabufs
            'media.ffmpeg.vaapi.enabled': True, 'media.hardware-video-decoding.enabled': True,
            'media.hardware-video-decoding.force-enabled': True,  # gfxinfo knows neither the GPU nor the decoder
            'media.av1.enabled': True,                            # the AVD decodes AV1 (m4.13+)
            'webgl.disabled': True,
        }
        for k, v in want.items():
            self.assertEqual(p.get(k), v, k)
        # without the AVD (kill switch, unsupported profile) Firefox must still decode in software; 4K60 AV1 users set it in user.js
        self.assertNotIn('media.ffmpeg.disable-software-fallback', p)

    def test_defaults_not_locks_and_no_profile_or_ui_prefs(self):
        text = PREFS.read_text()
        self.assertNotIn('lockPref', text)
        self.assertNotIn('user_pref', text)
        self.assertEqual([k for k in self.prefs() if k.startswith(('browser.', 'security.sandbox'))], [])

    def test_firefox_reads_the_directory_and_omarchy_owns_the_policies(self):
        # Firefox loads every *.js in $GRE/defaults/pref; Omarchy installs and purges /usr/lib/firefox/distribution
        # (install/helpers/browser-policy.sh), so nothing of ours goes there.
        self.assertEqual(PREFS.parent, SUPPORT/'usr/lib/firefox/defaults/pref')
        self.assertFalse((SUPPORT/'usr/lib/firefox/distribution').exists())
        self.assertEqual(PREFS.suffix, '.js')

    def test_no_launcher_or_desktop_override_is_shipped(self):
        self.assertEqual(sorted(p.name for p in SUPPORT.rglob('*firefox*')), ['firefox', 'm4-firefox.js'])
        self.assertFalse(list(SUPPORT.rglob('applications/firefox.desktop')))


class SessionSandboxSwitch(unittest.TestCase):
    def run_script(self, gpu_ready, avd_loaded):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        lib = d/'lib'
        lib.mkdir()
        for n in ('env-keys', 'kms-card.sh'):
            shutil.copy(M4/n, lib/n)
        shutil.copy(AURORA/'session-env', lib/'session-env')
        flag, avd = d/'gpu-ready', d/'module-apple_avd'
        if gpu_ready:
            flag.write_text('ok\n')
        if avd_loaded:
            avd.mkdir()
        text = (M4/'session-env.sh').read_text()
        self.assertIn('/sys/module/apple_avd', text)
        text = (text.replace('/usr/lib/m4', str(lib)).replace('/run/m4/gpu-ready', str(flag))
                .replace('/sys/module/apple_avd', str(avd)))
        (d/'s.sh').write_text(text + '\nenv\n')
        env = {'PATH': '/usr/bin:/bin', 'M4_SYS_DRM': str(d/'none'), 'M4_DEV_DRI': str(d/'none')}
        p = subprocess.run(['/bin/sh', str(d/'s.sh')], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return dict(l.split('=', 1) for l in p.stdout.splitlines() if '=' in l)

    def test_gpu_and_decoder_up_switch_the_rdd_sandbox_off(self):
        self.assertEqual(self.run_script(True, True).get('MOZ_DISABLE_RDD_SANDBOX'), '1')

    def test_no_decoder_keeps_the_sandbox(self):
        self.assertNotIn('MOZ_DISABLE_RDD_SANDBOX', self.run_script(True, False))

    def test_no_gpu_changes_nothing(self):
        self.assertNotIn('MOZ_DISABLE_RDD_SANDBOX', self.run_script(False, True))


if __name__ == '__main__':
    unittest.main()
