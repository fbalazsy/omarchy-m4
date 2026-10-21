#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# J616 variant, lane SF 2026-10-21 (no counterpart in Kb2uka's tool; the noise stimulus and compare()
# follow his qualify_memory.py). For each FIR stage: native render of noise vs the convolution with the
# stage's own impulse response, at three input levels. Shows that the woofer-lane mismatch in
# run-report.json is level independent (float32 round-off inside the IIR cascade), not nonlinearity.
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import j616_common as C  # noqa: E402


def noise(ch, amp, seed=616):
    rng = random.Random(seed)
    d = []
    for _ in range(ch):
        d.extend([0.] * 4096 + [amp * (2 * rng.random() - 1) for _ in range(8192)] + [0.] * (C.FRAMES - 12288))
    return np.array(d, dtype='<f4').reshape(ch, C.FRAMES)


def main(helper, decoded, work):
    helper, decoded, work = Path(helper), Path(decoded), Path(work)
    work.mkdir(exist_ok=True)

    def run(stage, sub, ch, lane, amp, inp, params):
        out = work / 'o.f32'
        r = subprocess.run([str(helper), 'aufx', sub, str(decoded / (stage + '.aupreset')), str(out), str(ch), str(lane), amp, str(inp)] + params,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return np.frombuffer(out.read_bytes(), dtype='<f4').reshape(ch, C.FRAMES).astype(np.float64)

    print('stage / input lane / noise amplitude -> relative peak error of (native render) vs (convolution with the stage impulse response)')
    for stage, sub, ch, params, lanes in (('Tone1', 'tmst', 2, ['3=1.0'], (0,)), ('Tone2', 'tmst', 2, ['3=1.0'], (0,)),
                                          ('VarEQ', 'vrq2', 2, ['5=1.0'], (0,)), ('analytical', 'tmst', 6, ['3=1.0'], (0, 2))):
        for lane in lanes:
            h = run(stage, sub, ch, lane, '0.0001', 'impulse', params)[lane][C.IMPULSE:] / np.float32(0.0001)
            for amp in (0.125, 0.0125, 0.00125):
                x = noise(ch, amp)
                (work / 'n.f32').write_bytes(x.tobytes())
                y = run(stage, sub, ch, lane, '0.0001', work / 'n.f32', params)
                print(f'{stage:11s} lane {lane}  amp {amp:<8g} -> {C.compare(y[lane], C.fft_convolve(x[lane].astype(np.float64), h))["relative_peak_error"]:.3e}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
