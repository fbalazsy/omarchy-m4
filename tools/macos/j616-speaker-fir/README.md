# J616 owner-FIR pipeline (variant of Kb2uka's J713 tool)

This is a **J616 (MacBook Pro 16" M4 Pro, Mac16,7) variant** of `audio/j713/owner-fir/` from
<https://github.com/Kb2uka/Omarchy-M4-Native> (MIT, Copyright (c) 2026 KB2UKA), commit
`b92f048f7f41227e600431926395e3dfe2d7f070`. The original files are kept unchanged under
`upstream-reference/` (never executed); the licence is in `LICENSE.txt`. Files marked "J616 variant" in
their header are new or generalised; the numerical routines in `src/j616_common.py` are copied unchanged
from `analyze_transfer.py` / `qualify_memory.py`.

The upstream tool refuses anything but its pinned J713/AID37 inputs, and this variant does not loosen it:
it has its **own pins** (`manifest.json`), recorded from what this Mac measured on 2026-10-21
(macOS 26.6.2 / 25G83, AudioDSP `b8711ac5…`, AID35 speakerID 3). It reads installed system files, renders
AudioUnits into memory (no output unit, no audio device, no volume change) and writes into a **new** directory.

## What differs from the J713 tool

| | J713 tool | this J616 variant |
|---|---|---|
| inputs | AID37 strip | AID35 `builtin_speaker_out_general_3.austrip` (= the generic strip) + `.propstrip` + `.dspg` |
| chain | HP-only subsets of Tone1/Tone2 (filter 0 / 5) + full Tone3 → 4 lanes | the **full stored** Tone1 + Tone2 + VarEQ + `analytical` presets per output channel LW1 LW2 LT RW1 RW2 RT |
| helpers | `measure-transfer` (4 ch, Tone3 only) + `measure-memory` (HP subsets) | one `measure-au` (any AU, 2 or 6 channels, host parameters `ID=VALUE`, file or impulse input) |
| volume | `SystemVolume` pinned to 1.0 | tmst `SystemVolume` = 1.0 (no effect, shown), VarEQ `Current Gain` swept; primary set = 1.0 |
| normalisation | impulse amplitude only | the same (÷ float32(1e-4) on every lane; no per-lane level change) |
| tail | `select_length` bound 1e-6 | the same |
| asserts | native chain compare recorded, not asserted | asserted < 2e-3 relative peak (woofer lanes 6e-4…1e-3, tweeters ~1e-5); every value recorded |
| Bypass control | asserted once | retried (≤ 8) because AudioDSP applies `Bypass` asynchronously on 25G83; at least one exact identity required; not part of the FIR values |
| determinism | exact output hashes | exact hashes when they match, else `--reference-dir` (≤ 1e-6 of peak): AudioDSP is not bit-reproducible on a busy machine |
| Python | bundled CPython 3.13.9 + NumPy 2.5.3 wheel | tested with Homebrew CPython 3.13.2 + NumPy 2.5.3 (same wheel version) |

## Files

| File | What |
|---|---|
| `manifest.json` | the pins (inputs, extracted presets, helper, sources, output hashes) |
| `build-helpers.sh` | builds `bin/measure-au` with `/usr/bin/clang` (arm64, macOS 15 target); the build is reproducible (two builds, same hash) |
| `bin/measure-au` | the built helper (not shipped: build it with `build-helpers.sh`; the manifest pins the owner-Mac build) |
| `src/measure-au.c` | helper source (derived from Kb2uka's `measure-memory.c`; render loop, block size, frame count, impulse position, stream format unchanged) |
| `src/inspect_strip_j616.py` | extracts all 17 effect presets from the strip, byte-for-byte (`plistlib`), decodes the tmst/vrq2 parameter blocks |
| `src/run_j616.py` | the generator: pins → stage impulse responses (two amplitudes) → chain → native-chain verification → WAVs |
| `src/j616_common.py` | validation, tail selection, FFT convolution, WAV writer (copied from upstream) + a WAV reader and response evaluation |
| `src/make_tables.py` | response tables for any mono float32 WAV (J616 set and asahi-audio J316) |
| `src/q1_inventory.py`, `src/render_q1_md.py` | the Q1 evidence (level and volume sweeps, XTC 2×2, dynamics) |
| `src/decode_filters.py` | readable listing of the stored tmst filters |
| `src/make_receipt.py` | writes `receipt.json` |
| `upstream-reference/` | Kb2uka's original files, unchanged (README, manifest, sources, build script) |

## Run

On your own MacBook Pro J616s, in macOS (no sudo; nothing is played):

```sh
python3.13 -m venv v && v/bin/pip install 'numpy==2.5.3'
bash build-helpers.sh /absolute/new/dir && mkdir -p bin && cp /absolute/new/dir/measure-au /absolute/new/dir/au-inspect bin/
v/bin/python -I -B src/run_j616.py --output /absolute/new/owner-firs [--reference-dir /previous/run]
```

The helper is built from source here (this copy ships no binaries). The manifest pins the helper built on the owner's Mac and
macOS 26.6.2 (25G83)'s inputs: with another clang or macOS build the run refuses, and `--record-pins` writes
`manifest.proposed.json` for you to review instead. Then copy `j616-woofer-L-48000.wav` and `j616-tweeter-L-48000.wav` to
Linux and run `sudo m4-speakers install DIR` (m4-support); the WAVs are Apple-derived: keep them on your own machine.

`--record-pins` writes `manifest.proposed.json` instead of checking the pins (review before adopting).
The inputs must be this Mac's own installed files; this variant qualifies one unit only.

## Limits

* One unit, one macOS build: no claim for other Macs or builds.
* The FIRs omit Norm, VirtBass, XTC, Comp, ControlFreakLF/HF, SpeakerProtect and the master-volume gain
  boxes; see the kit README for what each of those does.
* Nothing here was played on any speaker; the receipt says so (`audio_played: false`).
* The generated WAVs and Apple's tuning data have no established redistribution licence; keep them local.
