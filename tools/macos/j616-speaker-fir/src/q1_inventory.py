#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA (in-memory AudioUnit rendering approach, via measure-au);
# J616 variant, lane SF 2026-10-21. Q1 evidence: for every stage of the AID35 speaker graph
# (builtin_speaker_out.dspg), which strip files carry it, and whether its response is static
# (level- and volume-independent), measured by rendering the real AudioUnit in memory.
# Never plays audio; the only host parameters set are named in the output.
import argparse
import hashlib
import json
import plistlib
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import j616_common as C  # noqa: E402

BANDS = [(40, 80), (80, 160), (160, 320), (320, 640), (640, 1280), (1280, 2560), (2560, 5120), (5120, 10240), (10240, 20000)]
LEVELS = (0.0003, 0.003, 0.03, 0.2, 0.9)       # uniform-noise peak amplitude: -70 .. -1 dBFS
# graph order (stage-order.txt); channels = what the box carries in the graph
GRAPH = [('NFP', 'nfnp', None), ('Norm', 'ldnm', 2), ('VirtBass', 'vben', 2), ('Tone1', 'tmst', 2),
         ('LeftGain', 'dbgn', None), ('RightGain', 'dbgn', None), ('LeftBalance', 'dbgn', None), ('RightBalance', 'dbgn', None),
         ('XTC', 'axtc', 2), ('Tone2', 'tmst', 2), ('VarEQ', 'vrq2', 2), ('Comp', 'mstk', 2),
         ('analytical', 'tmst', 6), ('ControlFreakLF', 'cfrk', 4), ('ControlFreakHF', 'cfrk', 2),
         ('SpeakerProtect', 'spp3', 6), ('Ramp', 'dbgn', None)]


class Rig:
    def __init__(self, helper, presets, work):
        self.helper, self.presets, self.work = helper, presets, work
        work.mkdir(exist_ok=True)

    def run(self, stage, sub, ch, lane=0, amp='0.0001', inp='impulse', params=()):
        raw = self.work / 'o.f32'
        args = [str(self.helper), 'aufx', sub, str(self.presets / (stage + '.aupreset')), str(raw), str(ch), str(lane), amp, str(inp)] + list(params)
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if p.returncode:
            raise RuntimeError((args, p.returncode, p.stderr))
        return np.frombuffer(raw.read_bytes(), dtype='<f4').reshape(ch, C.FRAMES).astype(np.float64)

    def impulse(self, stage, sub, ch, lane, amp, params=(), at=None):
        d = self.run(stage, sub, ch, lane, amp if at is None else f'{amp}@{at}', params=params)
        start = C.IMPULSE if at is None else at
        return d[:, start:] / np.float32(float(amp))

    def noise(self, stage, sub, ch, level, params=(), seed=7):
        r = np.random.default_rng(seed)
        x = np.zeros((ch, C.FRAMES), dtype='<f4')
        x[:, 4096:60000] = ((2 * r.random((ch, 60000 - 4096)) - 1) * level).astype('<f4')
        f = self.work / 'noise-in.f32'
        f.write_bytes(x.tobytes())
        return x.astype(np.float64), self.run(stage, sub, ch, 0, '0.0001', f, params)


def band_gain_db(x, y, lo=20000, n=32768):
    w = np.hanning(n)
    X, Y = np.fft.rfft(x[lo:lo + n] * w), np.fft.rfft(y[lo:lo + n] * w)
    f = np.fft.rfftfreq(n, 1 / C.RATE)
    return [float(10 * np.log10((np.sum(abs(Y[(f >= a) & (f < b)]) ** 2) + 1e-300) / (np.sum(abs(X[(f >= a) & (f < b)]) ** 2) + 1e-300))) for a, b in BANDS]


def level_sweep(rig, stage, sub, ch, params=()):
    rows = {}
    for level in LEVELS:
        x, y = rig.noise(stage, sub, ch, level, params)
        total = float(10 * np.log10(np.sum(y[0][20000:52768] ** 2) / np.sum(x[0][20000:52768] ** 2)))
        rows[str(level)] = band_gain_db(x[0], y[0]) + [total]
    spread = float(np.max(np.ptp(np.array(list(rows.values())), axis=0)))
    return {'gain_db_by_band_then_total': rows, 'max_spread_db': spread}


def line_db(y, f, n=32768, lo=20000):
    t = np.arange(n) / C.RATE
    w = np.hanning(n)
    z = np.sum(y[lo:lo + n] * w * np.exp(-2j * np.pi * f * t)) * 2 / np.sum(w)
    return float(20 * np.log10(max(abs(z), 1e-300)))


def dbgn_values(effect):
    data = effect['aupreset']['data']
    scope, element, count = struct.unpack_from('>III', data, 0)
    return {i: v for i, v in (struct.unpack_from('>If', data, 12 + 8 * n) for n in range(count))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tunings', type=Path, default=Path('/System/Library/Audio/Tunings/AID35'))
    ap.add_argument('--helper', type=Path, required=True)
    ap.add_argument('--presets', type=Path, required=True, help='decoded/ from inspect_strip_j616.py')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rig = Rig(a.helper, a.presets, a.work)
    strips = {n: plistlib.loads((a.tunings / 'DSP/Strips' / n).read_bytes())['strips'][0]['effects']
              for n in ('builtin_speaker_out_general.austrip', 'builtin_speaker_out_general_3.austrip', 'builtin_speaker_out_general_0.austrip')}
    eff = {k: {e['displayname']: e for e in v} for k, v in strips.items()}
    dspg = (a.tunings / 'DSP/Graphs/builtin_speaker_out.dspg').read_text()
    boxes = {m.group(1): m.group(2) for m in re.finditer(r'^box (\w+) \(aufx (\w{4})\)', dspg, re.M)}
    out = {'stages': {}, 'host_parameters_touched': {}}
    for name, sub, ch in GRAPH:
        g, t3, t0 = (eff[k][name] for k in ('builtin_speaker_out_general.austrip', 'builtin_speaker_out_general_3.austrip', 'builtin_speaker_out_general_0.austrip'))
        p3 = t3['aupreset']
        row = {'au': ('aufx/' + sub + '/appl') if sub not in ('dbgn', 'nfnp') else 'abox ' + sub + ' (DSPGraph internal box, not an AudioUnit)',
               'graph_box_type': boxes.get(name), 'preset_name': p3.get('name'), 'channels_in_graph': ch,
               'in_generic': True, 'in_3': True, 'generic_equals_3': plistlib.dumps(g['aupreset']) == plistlib.dumps(p3),
               'data_equals_0': plistlib.dumps(t0['aupreset']) == plistlib.dumps(p3), 'preset_name_in_0': t0['aupreset'].get('name'),
               'data_bytes': len(p3.get('data', b''))}
        out['stages'][name] = row
    # ---- tmst stages: volume independence + linearity -------------------------------------
    sysvol = (1.0, 0.5, 0.0, -20.0, -100.0)
    for name, ch, lanes in (('Tone1', 2, (0,)), ('Tone2', 2, (0,)), ('analytical', 6, (0, 2))):
        r = out['stages'][name]
        ref = {l: rig.impulse(name, 'tmst', ch, l, '0.0001', ['3=1.0'])[l] for l in lanes}
        diffs = {}
        for v in sysvol:
            diffs[str(v)] = max(float(np.max(np.abs(rig.impulse(name, 'tmst', ch, l, '0.0001', [f'3={v}'])[l] - ref[l]))) for l in lanes)
        big = {l: rig.impulse(name, 'tmst', ch, l, '0.5', ['3=1.0'])[l] for l in lanes}
        r['static'] = {'volume_param': 'SystemVolume (param 3), wired to graph mvsc', 'max_abs_diff_vs_sysvol_1.0': diffs,
                       'amplitude_0.5_vs_1e-4_max_rel_diff': max(float(np.max(np.abs(big[l] - ref[l]))) / float(np.max(np.abs(ref[l]))) for l in lanes),
                       'level_sweep': level_sweep(rig, name, 'tmst', ch, ['3=1.0'])}
        out['host_parameters_touched'][name] = 'SystemVolume (param 3) swept over %s' % (sysvol,)
    # ---- VarEQ ----------------------------------------------------------------------------
    r = out['stages']['VarEQ']
    cg = {}
    for v in (1.0, 0.75, 0.5, 0.25, 0.0):
        h = rig.impulse('VarEQ', 'vrq2', 2, 0, '0.0001', [f'5={v}'])[0]
        cg[str(v)] = dict(zip(map(str, C.TABLE_HZ), C.response_db(h, C.RATE)))
    hb = rig.impulse('VarEQ', 'vrq2', 2, 0, '0.5', ['5=1.0'])[0]
    ha = rig.impulse('VarEQ', 'vrq2', 2, 0, '0.0001', ['5=1.0'])[0]
    r['static'] = {'volume_param': 'Current Gain (param 5), wired to graph mvsc', 'response_db_by_current_gain': cg,
                   'amplitude_0.5_vs_1e-4_max_rel_diff': float(np.max(np.abs(hb - ha)) / np.max(np.abs(ha))),
                   'level_sweep': level_sweep(rig, 'VarEQ', 'vrq2', 2, ['5=1.0'])}
    out['host_parameters_touched']['VarEQ'] = 'Current Gain (param 5) swept 1.0 .. 0.0'
    # ---- XTC ------------------------------------------------------------------------------
    r = out['stages']['XTC']
    tab = {}
    for sv in ('preset', -100.0, -50.0, -31.75, 0.0):
        params = [] if sv == 'preset' else [f'16={sv}']
        h = rig.impulse('XTC', 'axtc', 2, 0, '0.0001', params, at=30000)
        n = np.arange(20000)
        ll = [np.sum(h[0][:20000] * np.exp(-2j * np.pi * f * n / C.RATE)) for f in C.TABLE_HZ]
        lr = [np.sum(h[1][:20000] * np.exp(-2j * np.pi * f * n / C.RATE)) for f in C.TABLE_HZ]
        db = lambda z: float(20 * np.log10(max(abs(z), 1e-300)))
        tab[str(sv)] = {'L->L': dict(zip(map(str, C.TABLE_HZ), map(db, ll))),
                        'L->R': dict(zip(map(str, C.TABLE_HZ), map(db, lr))),
                        'mono_in_phase_L=R (LL+LR)': dict(zip(map(str, C.TABLE_HZ), (db(a + b) for a, b in zip(ll, lr)))),
                        'side_L=-R (LL-LR)': dict(zip(map(str, C.TABLE_HZ), (db(a - b) for a, b in zip(ll, lr))))}
    first = rig.impulse('XTC', 'axtc', 2, 0, '0.0001', [])  # impulse at frame 4096: inside the 100 ms start-up crossfade
    r['static'] = {'volume_param': 'System Volume (param 16, dB); Wet/Dry taper points at -50 and 0 dB are both 40 %',
                   'settled_2x2_response_db': tab,
                   'impulse_at_4096_peak_LL': float(np.max(np.abs(first[0]))), 'impulse_at_30000_peak_LL': float(np.max(np.abs(rig.impulse('XTC', 'axtc', 2, 0, '0.0001', [], at=30000)[0]))),
                   'level_sweep': level_sweep(rig, 'XTC', 'axtc', 2)}
    out['host_parameters_touched']['XTC'] = 'System Volume (param 16) swept -100 .. 0 dB (rendered from frame 30000 to skip the 100 ms start-up crossfade)'
    # ---- dynamic / nonlinear stages --------------------------------------------------------
    for name, sub, ch in (('Norm', 'ldnm', 2), ('Comp', 'mstk', 2)):
        out['stages'][name]['static'] = {'level_sweep': level_sweep(rig, name, sub, ch)}
    r = out['stages']['VirtBass']
    harm = {}
    for amp in (0.02, 0.1, 0.5):
        t = np.arange(C.FRAMES) / C.RATE
        x = np.zeros((2, C.FRAMES), dtype='<f4')
        x[:, 4096:60000] = (amp * np.sin(2 * np.pi * 60.0 * t[4096:60000])).astype('<f4')
        f = a.work / 'sine-in.f32'
        f.write_bytes(x.tobytes())
        y = rig.run('VirtBass', 'vben', 2, 0, '0.0001', f)
        harm[str(amp)] = {f'H{k}_db_re_fundamental_in': line_db(y[0], 60.0 * k) - 20 * np.log10(amp) for k in range(1, 6)}
    ha = rig.impulse('VirtBass', 'vben', 2, 0, '0.00001')[0]
    r['static'] = {'level_sweep': level_sweep(rig, 'VirtBass', 'vben', 2), 'harmonics_60Hz_sine': harm,
                   'small_signal_impulse_response_db': dict(zip(map(str, C.TABLE_HZ), C.response_db(ha, C.RATE)))}
    cf = {}
    for name in ('ControlFreakLF', 'ControlFreakHF'):
        ch = 4 if name.endswith('LF') else 2
        rows = {}
        for g in (0, 12, 24):
            for amp in (0.05, 0.9):
                t = np.arange(C.FRAMES) / C.RATE
                x = np.zeros((ch, C.FRAMES), dtype='<f4')
                x[:, 4096:60000] = (amp * np.sin(2 * np.pi * 100.0 * t[4096:60000])).astype('<f4')
                f = a.work / 'sine-in.f32'
                f.write_bytes(x.tobytes())
                y = rig.run(name, 'cfrk', ch, 0, '0.0001', f, [f'2={g}'])
                rows[f'InputGain +{g} dB, amp {amp}'] = float(10 * np.log10(np.sum(y[0][20000:52768] ** 2) / np.sum(x[0][20000:52768].astype(float) ** 2)))
        out['stages'][name]['static'] = {'level_sweep': level_sweep(rig, name, 'cfrk', ch), 'boosted_input_gain_db_measured': rows}
        out['host_parameters_touched'][name] = 'InputGain (param 2) +0/+12/+24 dB, only to demonstrate the limiter'
    # SpeakerProtect: not measurable offline (needs telemetry input + calibration properties)
    r = out['stages']['SpeakerProtect']
    x, y = rig.noise('SpeakerProtect', 'spp3', 6, 0.003)
    r['static'] = {'standalone_gain_db': float(10 * np.log10(np.sum(y[0][20000:52768] ** 2) / np.sum(x[0][20000:52768] ** 2))),
                   'note': 'instantiated without the telemetry input and the 64001/64005/64008/64009 properties it sits at its fail-safe -20.0 dB; not Apple\'s runtime state, not used'}
    # ---- dbgn / nfnp boxes: stored values -------------------------------------------------------
    for name in ('LeftGain', 'RightGain', 'LeftBalance', 'RightBalance', 'Ramp'):
        out['stages'][name]['stored_params'] = {'_3': dbgn_values(eff['builtin_speaker_out_general_3.austrip'][name]),
                                                '_0': dbgn_values(eff['builtin_speaker_out_general_0.austrip'][name])}
    (a.out / 'q1-inventory.json').write_text(json.dumps(out, indent=2, default=float) + '\n')
    print('wrote', a.out / 'q1-inventory.json')


if __name__ == '__main__':
    main()
