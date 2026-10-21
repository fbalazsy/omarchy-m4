#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA (pipeline design: native AudioUnit transfer rendering, impulse
#   normalisation, finite-tail selection, NumPy FFT convolution, float32 IEEE WAV; from
#   Omarchy-M4-Native audio/j713/owner-fir @ b92f048)
# J616 variant (macOS-side helper lane SF, 2026-10-21). Differences from the J713 tool, all
# deliberate and listed in pipeline/README.md:
#   * inputs are the AID35 tuning files of the J616 (Mac16,7) and the 25G83 AudioDSP;
#   * the chain is the FULL stored Tone1 + Tone2 + VarEQ + `analytical` presets (not the
#     HP-only subsets J713 used), per output channel LW1 LW2 LT RW1 RW2 RT;
#   * VarEQ is volume dependent (graph parameter mvsc -> CurrentGain), so the chain is
#     rendered for several CurrentGain values; the primary set pins CurrentGain = 1.0, the
#     same convention as the J713 tool's SystemVolume = 1.0;
#   * pins are recorded in manifest.json from what this Mac measured.
# It reads installed system files, renders AudioUnits into memory (no output unit, no audio
# device, no volume change) and writes into a NEW output directory.
import argparse
import hashlib
import json
import platform
import plistlib
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import j616_common as C  # noqa: E402
import inspect_strip_j616 as strip_tool  # noqa: E402

AUDIODSP = Path('/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP')
OUT_CH = ['LW1', 'LW2', 'LT', 'RW1', 'RW2', 'RT']          # `analytical` channel order (ToneJoin)
SIDE = [0, 0, 0, 1, 1, 1]                                    # input lane (L/R) feeding each
NAMES = {0: 'woofer-L', 1: 'woofer-L', 2: 'tweeter-L', 3: 'woofer-R', 4: 'woofer-R', 5: 'tweeter-R'}
CG_PRIMARY = 1.0
CG_VARIANTS = (0.75, 0.5, 0.25, 0.0)
AMPS = ('0.0001', '0.0002')
STAGES = {  # name: (AU subtype, channels, fixed parameters set after AudioUnitInitialize)
    'Tone1': ('tmst', 2, ['3=1.0']),
    'Tone2': ('tmst', 2, ['3=1.0']),
    'analytical': ('tmst', 6, ['3=1.0']),
}


def require(cond, msg):
    if not cond:
        raise RuntimeError(msg)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Renderer:
    def __init__(self, helper, presets, work):
        self.helper, self.presets, self.work = helper, presets, work
        (work / 'render').mkdir()
        self.n = 0

    def run(self, stage, sub, ch, lane, amp, params, inp='impulse', tag=''):
        """One render. Note: AudioDSP on this Mac (25G83) is not bit-reproducible between identical
        renders when the machine is busy: denormal-range tails (< 1e-18 absolute) and occasional
        1-ULP float32 differences (about 1e-7 of the peak, mostly `analytical` lanes 0/2) appear.
        They are far below anything audible and below the 1e-6 L1 tail bound, but they can flip
        coefficient LSBs, so output hashes are NOT stable across runs (see --reference-dir)."""
        self.n += 1
        name = f'{self.n:04d}-{stage}-{tag}'
        raw = self.work / 'render' / (name + '.f32')
        args = [str(self.helper), 'aufx', sub, str(self.presets / (stage + '.aupreset')), str(raw),
                str(ch), str(lane), amp, str(inp)] + list(params)
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
        (self.work / 'render' / (name + '.stderr')).write_text(proc.stderr)
        require(proc.returncode == 0, f'{name} failed rc={proc.returncode}: {proc.stderr}')
        row = json.loads(proc.stdout)
        row.update(name=name, file=raw.name, sha256=sha(raw), bytes=raw.stat().st_size)
        return row, raw


def stage_response(R, stage, sub, ch, lane, params, tag):
    """Two-amplitude impulse response of one stage on one input lane, validated like
    analyze_transfer.py (pre-impulse silence, no cross-lane output, 2-amplitude agreement)."""
    got = {}
    for amp in AMPS:
        row, raw = R.run(stage, sub, ch, lane, amp, params, tag=f'{tag}-lane{lane}-amp{amp}')
        data = C.load(row, raw, ch)
        C.validate_lane(data, lane, False)
        got[amp] = (row, data)
    low, high = got['0.0001'][1], got['0.0002'][1]
    h = [x / C.AMP for x in low[lane][C.IMPULSE:]]
    diff = [hi / (2 * C.AMP) - lo / C.AMP for hi, lo in zip(high[lane][C.IMPULSE:], low[lane][C.IMPULSE:])]
    peak = max(map(abs, h))
    rel = max(map(abs, diff)) / peak
    require(rel < 1e-5, f'{stage} lane {lane}: two-amplitude mismatch {rel}')
    # every other output lane must be silent (checked per lane by validate_lane); remember rows
    return h, {'two_amplitude_relative_peak_error': rel, 'peak': peak,
               'rows': [got[a][0]['name'] for a in AMPS], 'latency': got['0.0001'][0]['latency'],
               'tail': got['0.0001'][0]['tail']}


def bypass_control(R, stage, sub, ch, lane):
    """Harness identity control (as in run-transfer.py): with Bypass = 1 the AU must return the
    input impulse within one float ULP and nothing else. On this Mac (25G83) the Bypass
    parameter is applied asynchronously by AudioDSP: about 1 render in 8 comes back with the
    crossfade not yet applied (the effect still audible) or with a 1-ULP different identity,
    while impulse renders without Bypass are bit-identical over 25 repeats. The control is
    therefore retried (at most 8 times); every attempt is recorded and at least one must be an
    exact identity. It plays no part in the FIR values."""
    attempts = []
    for attempt in range(8):
        row, raw = R.run(stage, sub, ch, lane, '0.0001', STAGES[stage][2] + ['0=1'], tag=f'bypass-lane{lane}-try{attempt}')
        data = C.load(row, raw, ch)
        try:
            C.validate_lane(data, lane, True)
            attempts.append({'identity': True, 'ulps': (data[lane][C.IMPULSE] - C.AMP) / C.AMP_ULP})
            return attempts
        except AssertionError as error:
            attempts.append({'identity': False, 'reason': str(error), 'sample': data[lane][C.IMPULSE]})
    raise RuntimeError(f'{stage} lane {lane}: Bypass never gave an identity: {attempts}')


def noise_file(path, channels, amplitude, seed):
    rng = random.Random(seed)
    data = []
    for _ in range(channels):
        data.extend([0.] * 4096 + [amplitude * (2 * rng.random() - 1) for _ in range(8192)] + [0.] * (C.FRAMES - 12288))
    path.write_bytes(struct.pack('<' + 'f' * len(data), *data))


def read_f32(path, ch):
    return np.frombuffer(path.read_bytes(), dtype='<f4').reshape(ch, C.FRAMES).astype(np.float64)


def write_f32(path, arr):
    path.write_bytes(np.asarray(arr, dtype='<f4').tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tunings', default='/System/Library/Audio/Tunings/AID35', type=Path)
    ap.add_argument('--bundle', default=HERE.parent, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--work', type=Path, help='keep the working files here (new directory)')
    ap.add_argument('--reference-dir', type=Path, help='directory with a previous run\'s WAVs: pass if every coefficient is within 1e-6 of that file\'s peak')
    ap.add_argument('--record-pins', action='store_true', help='write manifest.proposed.json instead of verifying pins')
    args = ap.parse_args()
    bundle = args.bundle.resolve(strict=True)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    out = args.output
    require(out.is_absolute() and not out.exists(), '--output must be a new absolute directory')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'native arm64 macOS required')

    # ---- inputs: exact pins -------------------------------------------------------------
    inputs = {}
    audiodsp_hash = sha(AUDIODSP)
    inputs['AudioDSP'] = {'path': str(AUDIODSP), 'sha256': audiodsp_hash, 'size': AUDIODSP.stat().st_size}
    for name in ('builtin_speaker_out_general.austrip', 'builtin_speaker_out_general_3.austrip',
                 'builtin_speaker_out_general.propstrip'):
        p = args.tunings / 'DSP/Strips' / name
        inputs[name] = {'path': str(p), 'sha256': sha(p), 'size': p.stat().st_size}
    p = args.tunings / 'DSP/Graphs/builtin_speaker_out.dspg'
    inputs['builtin_speaker_out.dspg'] = {'path': str(p), 'sha256': sha(p), 'size': p.stat().st_size}
    require(inputs['builtin_speaker_out_general.austrip']['sha256'] ==
            inputs['builtin_speaker_out_general_3.austrip']['sha256'],
            'generic and _3 strips differ: this tool is pinned to speakerID 3 == generic')
    pins = manifest.get('pins')
    if not args.record_pins:
        require(pins, 'manifest has no pins: run once with --record-pins and review')
        for name, rec in inputs.items():
            q = pins['inputs'][name]
            require(rec['sha256'] == q['sha256'] and rec['size'] == q['size'], 'Unsupported input: ' + name)
    helper = bundle / 'bin' / 'measure-au'
    require(helper.is_file(), 'build the helper first (build-helpers.sh)')
    helper_hash = sha(helper)
    if not args.record_pins:
        require(helper_hash == pins['helper']['sha256'], 'helper hash differs from the manifest')

    keep = args.work is not None
    with tempfile.TemporaryDirectory(prefix='.j616-fir-', dir=out.parent) as temporary:
        work = args.work.resolve() if keep else Path(temporary)
        if keep:
            work.mkdir()
        presets = work / 'decoded'
        # the third argument of the upstream tool read a copy named builtin_speaker_out_general.austrip
        (work / 'source').mkdir()
        (work / 'source/builtin_speaker_out_general_3.austrip').write_bytes(
            (args.tunings / 'DSP/Strips/builtin_speaker_out_general_3.austrip').read_bytes())
        summary = strip_tool.main(work / 'source', presets)
        by_name = {e['name']: e for e in summary['effects']}
        used = ['Tone1', 'Tone2', 'VarEQ', 'analytical']
        for name in used:
            if not args.record_pins:
                require(by_name[name]['aupreset_sha256'] == pins['presets'][name]['sha256'], 'preset pin: ' + name)
        R = Renderer(helper, presets, work)

        # ---- per-stage impulse responses ----------------------------------------------
        stage_h, stage_meta, bypass_ulps = {}, {}, {}
        for stage in ('Tone1', 'Tone2'):
            sub, ch, params = STAGES[stage]
            for lane in (0, 1):
                h, meta = stage_response(R, stage, sub, ch, lane, params, f'{stage}')
                stage_h[(stage, lane)], stage_meta[(stage, lane)] = h, meta
                bypass_ulps[(stage, lane)] = bypass_control(R, stage, sub, ch, lane)
        sub, ch, params = STAGES['analytical']
        for lane in range(6):
            h, meta = stage_response(R, 'analytical', sub, ch, lane, params, 'analytical')
            stage_h[('analytical', lane)], stage_meta[('analytical', lane)] = h, meta
            bypass_ulps[('analytical', lane)] = bypass_control(R, 'analytical', sub, ch, lane)
        cgs = (CG_PRIMARY,) + CG_VARIANTS
        for cg in cgs:
            for lane in (0, 1):
                h, meta = stage_response(R, 'VarEQ', 'vrq2', 2, lane, [f'5={cg}'], f'VarEQ-cg{cg}')
                stage_h[(f'VarEQ{cg}', lane)], stage_meta[(f'VarEQ{cg}', lane)] = h, meta
        # L/R symmetry of the per-side stages is recorded, not assumed
        symmetry = {s: bool(np.array_equal(np.array(stage_h[(s, 0)]), np.array(stage_h[(s, 1)])))
                    for s in ['Tone1', 'Tone2'] + [f'VarEQ{cg}' for cg in cgs]}

        def stage_fir(key, lane):
            h = stage_h[(key, lane)]
            length, tails = C.select_length(h)
            return C.float_coeff(h[:length]), length, tails

        # ---- chains -------------------------------------------------------------------
        variants = {'cg1.00': CG_PRIMARY, 'cg0.75': 0.75, 'cg0.50': 0.5, 'cg0.25': 0.25, 'cg0.00': 0.0, 'no-vareq': None}
        firs, chain_meta = {}, {}
        for vname, cg in variants.items():
            for k in range(6):
                side = SIDE[k]
                t1, l1, _ = stage_fir('Tone1', side)
                t2, l2, _ = stage_fir('Tone2', side)
                an, la, _ = stage_fir('analytical', k)
                pre = C.fft_convolve(t1, t2)
                if cg is not None:
                    ve, lv, _ = stage_fir(f'VarEQ{cg}', side)
                    pre = C.fft_convolve(pre, ve)
                else:
                    lv = None
                full = C.fft_convolve(pre, an)
                length, tails = C.select_length(full)
                firs[(vname, k)] = C.float_coeff(full[:length])
                chain_meta[(vname, k)] = {'taps': length, 'tail_candidates': tails,
                                          'stage_taps': {'Tone1': l1, 'Tone2': l2, 'VarEQ': lv, 'analytical': la},
                                          'l1': float(np.sum(np.abs(firs[(vname, k)])))}

        # ---- native chain verification (noise through the real AudioUnits) ------------------
        verification = []
        noise = work / 'render' / 'noise-stereo.f32'
        noise_file(noise, 2, 0.125, 616)
        x = read_f32(noise, 2)
        def native(stage, sub, ch, src, params, tag):
            row, raw = R.run(stage, sub, ch, 0, '0.0001', params, inp=src, tag=tag)
            return raw
        a = native('Tone1', 'tmst', 2, noise, STAGES['Tone1'][2], 'chain-Tone1')
        b = native('Tone2', 'tmst', 2, a, STAGES['Tone2'][2], 'chain-Tone2')
        bx = read_f32(b, 2)
        for vname, cg in variants.items():
            if cg is not None:
                c = native('VarEQ', 'vrq2', 2, b, [f'5={cg}'], f'chain-VarEQ-{vname}')
                cx = read_f32(c, 2)
            else:
                cx = bx
            fan = np.stack([cx[SIDE[k]] for k in range(6)])
            fanf = work / 'render' / f'fan-{vname}.f32'
            write_f32(fanf, fan)
            d = native('analytical', 'tmst', 6, fanf, STAGES['analytical'][2], f'chain-analytical-{vname}')
            dx = read_f32(d, 6)
            for k in range(6):
                result = C.compare(dx[k], C.fft_convolve(x[SIDE[k]], firs[(vname, k)]))
                result.update(variant=vname, channel=OUT_CH[k])
                verification.append(result)
        worst = max(v['relative_peak_error'] for v in verification)
        # Kb2uka's tool records `strict_1e4_relative_pass` without asserting it. Here the woofer
        # lanes measure 6e-4..1e-3 (tweeters ~1e-5): the error is level independent (checked at three
        # input levels) and sits where the woofer filters resonate, i.e. float32 round-off noise inside
        # Apple's IIR cascade, not a different transfer function; we assert < 2e-3 (-54 dB) and
        # record every value.
        require(worst < 2e-3, f'native chain differs from the FIR prediction: {worst}')

        # ---- write outputs ------------------------------------------------------------
        files = {}
        stage_out = work / 'out'
        stage_out.mkdir()
        for vname in variants:
            sub = stage_out if vname == 'cg1.00' else stage_out / 'variants' / vname
            sub.mkdir(parents=True, exist_ok=True)
            for k in (0, 2, 3, 5):                      # LW1, LT, RW1, RT
                fname = f'j616-{NAMES[k]}-48000.wav'
                C.wav_float32(sub / fname, firs[(vname, k)])
                files[str((sub / fname).relative_to(stage_out))] = {
                    'sha256': sha(sub / fname), 'size': (sub / fname).stat().st_size, 'taps': len(firs[(vname, k)])}
        # LW2 / RW2 are separate lanes of `analytical`; record equality with LW1 / RW1 per variant
        lane_equal = {v: {'LW2==LW1': bool(np.array_equal(firs[(v, 1)], firs[(v, 0)])),
                          'RW2==RW1': bool(np.array_equal(firs[(v, 4)], firs[(v, 3)])),
                          'LT_sha_eq_RT': bool(np.array_equal(firs[(v, 2)], firs[(v, 5)])),
                          'LW1==RW1': bool(np.array_equal(firs[(v, 0)], firs[(v, 3)]))} for v in variants}
        metrics = {}
        for vname in variants:
            for k in range(6):
                h = firs[(vname, k)]
                metrics[f'{vname}/{OUT_CH[k]}'] = {
                    'taps': len(h), 'peak': float(np.max(np.abs(h))),
                    'peak_index': int(np.argmax(np.abs(h))),
                    'response_db': dict(zip(map(str, C.TABLE_HZ), C.response_db(h, C.RATE))),
                    'sha256_of_wav': hashlib.sha256(_wav_bytes(h)).hexdigest()}
        report = {'inputs': inputs, 'helper': {'sha256': helper_hash, 'size': helper.stat().st_size},
                  'presets': {n: {'aupreset_sha256': by_name[n]['aupreset_sha256'], 'preset_name': by_name[n]['preset_name'],
                                  'data_sha256': by_name[n]['data_sha256']} for n in used},
                  'strip_effects': {e['name']: {k: e[k] for k in ('preset_name', 'type', 'subtype', 'manufacturer', 'data_bytes', 'data_sha256', 'aupreset_sha256')} for e in summary['effects']},
                  'stage_meta': {f'{k[0]}/lane{k[1]}': v for k, v in stage_meta.items()},
                  'bypass_identity_attempts': {f'{k[0]}/lane{k[1]}': v for k, v in bypass_ulps.items()},
                  'stage_lr_identical': symmetry, 'lane_equality': lane_equal,
                  'chain_meta': {f'{k[0]}/{OUT_CH[k[1]]}': {kk: vv for kk, vv in v.items() if kk != 'tail_candidates'} for k, v in chain_meta.items()},
                  'native_chain_verification': verification, 'worst_native_chain_relative_peak_error': worst,
                  'metrics': metrics, 'files': files}
        (stage_out / 'run-report.json').write_text(json.dumps(report, indent=2) + '\n')
        out.mkdir(mode=0o700)
        shutil.copytree(stage_out, out, dirs_exist_ok=True)
        if args.record_pins:
            proposed = dict(manifest)
            proposed['pins'] = {'inputs': inputs, 'helper': report['helper'],
                                'presets': {n: {'sha256': by_name[n]['aupreset_sha256']} for n in used},
                                'outputs': files}
            (out / 'manifest.proposed.json').write_text(json.dumps(proposed, indent=2, sort_keys=True) + '\n')
        else:
            for name, rec in pins['outputs'].items():
                if files[name]['sha256'] == rec['sha256']:
                    continue
                require(args.reference_dir is not None, 'output differs from the pinned hash (AudioDSP renders are not bit-reproducible on a busy machine; rerun with --reference-dir): ' + name)
                _, ref = C.read_wav_float32(args.reference_dir / name)
                _, new = C.read_wav_float32(out / name)
                require(len(ref) == len(new) and float(np.max(np.abs(ref - new))) <= 1e-6 * float(np.max(np.abs(ref))),
                        'output differs from the reference beyond 1e-6 of its peak: ' + name)
                print('note: %s differs from the pinned hash but is within 1e-6 of the reference peak (max %.2e)' % (name, float(np.max(np.abs(ref - new)))))
    print('J616 FIR set ready:', out)


def _wav_bytes(samples):
    data = struct.pack('<' + 'f' * len(samples), *samples)
    fmt = struct.pack('<HHIIHH', 3, 1, C.RATE, C.RATE * 4, 4, 32)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt
    chunks += b'fact' + struct.pack('<II', 4, len(samples))
    chunks += b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks


if __name__ == '__main__':
    main()
