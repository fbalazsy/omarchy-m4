#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA (block decoder from inspect_strip.py @ b92f048); J616 variant, lane SF 2026-10-21.
# Lists the parameter-block contents of the tmst / vrq2 presets (as stored, nothing edited) in readable form.
# Parameter names are the AU's own (kAudioUnitProperty_ParameterInfo); filter `type` codes are NOT
# decoded (the AU exposes no value strings for them) and are printed as numbers.
import json
import plistlib
import sys
from pathlib import Path


def main(decoded, out):
    decoded = Path(decoded)
    lines = ['# Stored parameters of the tmst / vrq2 presets (AID35 `_3` = generic strip)', '',
             'Layout of a `tmst` block (AudioUnit parameter IDs): 0 Bypass, 1 GroupBypassSwitches, 2 GroupLockSwitches, 3 SystemVolume, 4 InputGain, '
             '5 MaxAllowedFilterOrder, ..., 13 OutputGain; then 50 filters of 10 parameters from ID 14 + 10·n: '
             '`state` (0–7), `type` (0–24), `gain/range/reference` (dB), `frequency 0..3` (Hz or quality/order depending on type), '
             '`filter channels` (bit mask), `phase channels`, `group`. Filters with `state` 0 are not listed (they are inert: the measured '
             'responses contain none of their gains). `type` codes are numbers only.', '']
    for name in ('Tone1', 'Tone2', 'analytical'):
        blocks = json.loads((decoded / (name + '.json')).read_text())
        params = {p['id']: p['value'] for p in blocks[0]['params']}
        lines += [f'## {name}', '', 'Global: ' + ', '.join(f'{i}={params[i]:.6g}' for i in (3, 4, 13) if i in params), '',
                  '| filter | state | type | gain (dB) | f0 | f1 | f2 | f3 | channels mask | phase ch | group |', '|---|---|---|---|---|---|---|---|---|---|---|']
        for n in range(50):
            b = 14 + 10 * n
            if params.get(b, 0) == 0:
                continue
            v = [params.get(b + k, float('nan')) for k in range(10)]
            lines.append(f'| {n} | {v[0]:g} | {v[1]:g} | {v[2]:.4g} | {v[3]:.6g} | {v[4]:.6g} | {v[5]:.6g} | {v[6]:.6g} | {int(v[7])} (0b{int(v[7]):b}) | {int(v[8])} | {int(v[9])} |')
        lines.append('')
    lines += ['## VarEQ (vrq2)', '', 'AU parameters: 1–4 `Max Gain for EQ1..4`, 5 `Current Gain` (wired to graph `mvsc`), 7 `PreGain (dB)`. '
              'The EQ curves themselves live in the preset blob (1744 B), which is not decoded here; the measured response per Current Gain is in `response-tables.md` / `q1-inventory.md`.', '']
    blocks = json.loads((decoded / 'VarEQ.json').read_text())
    lines.append('Stored global block (scope 0): ' + ', '.join(f"{p['id']}={p['value']:.6g}" for p in blocks[0]['params']) +
                 '. Further blocks (not decoded, purpose unknown): ' +
                 ', '.join(f"scope {b['scope']} element {b['element']} n={len(b['params'])}" for b in blocks[1:]) + '.')
    Path(out).write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main(*sys.argv[1:3])
