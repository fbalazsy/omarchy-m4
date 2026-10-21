#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA (receipt idea from m4-j713-fir-owner.py @ b92f048); J616 variant, lane SF 2026-10-21.
# Writes receipt.json: macOS build, AudioDSP bundle versions/hashes, AID35 input hashes, tool
# commits and output hashes. Contains no per-unit data (no serial, UDID, ECID, MAC, hostname).
import argparse
import hashlib
import json
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

import numpy as np


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kit', type=Path, required=True)
    ap.add_argument('--bundle', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--reference-commit', required=True)
    ap.add_argument('--asahi-audio-commit', required=True)
    ap.add_argument('--asahi-audio-tag-v4_1-commit', required=True)
    a = ap.parse_args()
    report = json.loads((a.kit / 'run-report.json').read_text())
    manifest = json.loads((a.bundle / 'manifest.json').read_text())
    dsp = Path('/System/Library/Components/AudioDSP.component')
    info = plistlib.loads((dsp / 'Contents/Info.plist').read_bytes())
    version = plistlib.loads((dsp / 'Contents/version.plist').read_bytes())
    tb = plistlib.loads(Path('/System/Library/Frameworks/AudioToolbox.framework/Versions/A/Resources/Info.plist').read_bytes())
    cs = subprocess.run(['codesign', '-dvvv', str(dsp)], capture_output=True, text=True).stderr.splitlines()
    pick = lambda key: next((l.split('=', 1)[1] for l in cs if l.startswith(key + '=')), None)
    receipt = {
        'schema': 1,
        'board': 'apple,j616s (Mac16,7)',
        'method': 'in-memory offline AudioUnit rendering (RegisterAudioUnits_Internal + AudioUnitRender, no output unit, no audio device, no volume change); impulse normalisation by the impulse amplitude only; finite-tail selection; NumPy FFT convolution; float32 IEEE mono WAV',
        'macos': {'ProductVersion': run('sw_vers', '-productVersion'), 'BuildVersion': run('sw_vers', '-buildVersion'),
                  'machine': platform.machine(), 'hw_model': run('sysctl', '-n', 'hw.model'),
                  'kernel': run('uname', '-v')},
        'audiodsp_component': {
            'path': str(dsp), 'CFBundleIdentifier': info.get('CFBundleIdentifier'),
            'CFBundleShortVersionString': info.get('CFBundleShortVersionString'), 'CFBundleVersion': info.get('CFBundleVersion'),
            'version.plist': {k: version.get(k) for k in ('CFBundleShortVersionString', 'CFBundleVersion', 'BuildVersion', 'ProjectName', 'SourceVersion') if k in version},
            'Info.plist_sha256': sha(dsp / 'Contents/Info.plist'),
            'binary_sha256': report['inputs']['AudioDSP']['sha256'], 'binary_size': report['inputs']['AudioDSP']['size'],
            'codesign': {'Identifier': pick('Identifier'), 'Format': pick('Format'), 'CDHash': pick('CDHash')}},
        'audiotoolbox_framework': {'CFBundleShortVersionString': tb.get('CFBundleShortVersionString'), 'CFBundleVersion': tb.get('CFBundleVersion'),
                                   'note': 'shared-cache resident; version from its Info.plist'},
        'aid35_inputs': {k: {'sha256': v['sha256'], 'size': v['size'], 'path': v['path']} for k, v in report['inputs'].items() if k != 'AudioDSP'},
        'aid35_effects_used': report['presets'],
        'aid35_all_effects': report['strip_effects'],
        'tool': {'reference': {'repo': 'https://github.com/Kb2uka/Omarchy-M4-Native', 'path': 'audio/j713/owner-fir', 'commit': a.reference_commit, 'license': 'MIT'},
                 'j616_variant': {'helper_sha256': report['helper']['sha256'], 'manifest_sha256': hashlib.sha256((a.bundle / 'manifest.json').read_bytes()).hexdigest(),
                                  'sources': {p.name: sha(p) for p in sorted((a.bundle / 'src').iterdir()) if p.is_file()}},
                 'python': platform.python_version(), 'numpy': np.__version__,
                 'clang': run('/usr/bin/clang', '--version').splitlines()[0]},
        'j316_reference': {'repo': 'https://github.com/AsahiLinux/asahi-audio', 'path': 'firs/j316', 'commit_main_at_measurement': a.asahi_audio_commit,
                           'tag_v4.1_commit': a.asahi_audio_tag_v4_1_commit,
                           'note': 'firs/j316/{woofers,tweeters}-{44,48,96,192}.wav are byte-identical at v4.1 and at main'},
        'chain': {'order_per_output_channel': 'Tone1 -> Tone2 -> VarEQ(CurrentGain) -> analytical[output channel]',
                  'primary_set': 'VarEQ CurrentGain = 1.0 (preset default); SystemVolume = 1.0 on the tmst stages (same pin as the J713 tool)',
                  'variants': ['cg0.75', 'cg0.50', 'cg0.25', 'cg0.00', 'no-vareq'],
                  'excluded': 'Norm, VirtBass, LeftGain/RightGain/Balance (0 dB at 100 % volume), XTC, Comp, ControlFreakLF/HF, SpeakerProtect, Ramp'},
        'normalisation': {'method': 'each stage response = rendered output / impulse amplitude; no per-lane peak or level normalisation',
                          'impulse_amplitude_float32': 0.0001, 'divisor_applied_to_every_lane': 1 / float(np.float32(0.0001)),
                          'gain_removed_db_every_lane': 20 * float(np.log10(1 / float(np.float32(0.0001))))},
        'verification': {'worst_native_chain_relative_peak_error': report['worst_native_chain_relative_peak_error'],
                         'max_two_amplitude_relative_peak_error': max(v['two_amplitude_relative_peak_error'] for v in report['stage_meta'].values()),
                         'lane_equality_primary': report['lane_equality']['cg1.00']},
        'outputs': report['files'],
        'hardware_playback_performed': False, 'audio_played': False,
    }
    a.out.write_text(json.dumps(receipt, indent=2, sort_keys=False) + '\n')


if __name__ == '__main__':
    main()
