# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA (the numerical routines below are copied unchanged from
#   Omarchy-M4-Native audio/j713/owner-fir/src/{analyze_transfer,qualify_memory}.py @ b92f048)
# J616 variant (macOS-side helper lane SF, 2026-10-21): the same validation, tail selection,
# float32 rounding and IEEE-float WAV serialisation, generalised from 4 to N channels.
import array
import cmath
import hashlib
import math
import struct
import sys

import numpy as np

FRAMES = 73728
IMPULSE = 4096
RATE = 48000
AMP = struct.unpack('<f', struct.pack('<f', 0.0001))[0]
AMP_BITS = struct.unpack('<I', struct.pack('<f', AMP))[0]
AMP_ULP = struct.unpack('<f', struct.pack('<I', AMP_BITS + 1))[0] - AMP


def load(row, path, channels):
    """analyze_transfer.load, with the channel count as an argument."""
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row['sha256']
    assert len(raw) == row['bytes'] == 4 * FRAMES * channels
    assert row['frames'] == FRAMES and row['impulse_frame'] == IMPULSE
    assert row['channels'] == channels and row['sample_rate'] == RATE
    assert row['impulses'] == 1
    assert row['callbacks'] == FRAMES // 256
    data = array.array('f')
    data.frombytes(raw)
    if sys.byteorder != 'little':
        data.byteswap()
    assert all(math.isfinite(x) for x in data)
    return [list(data[n * FRAMES:(n + 1) * FRAMES]) for n in range(channels)]


def validate_lane(data, lane, bypass):
    """analyze_transfer.validate_lane, unchanged."""
    assert all(x == 0 for ch in data for x in ch[:IMPULSE]), 'pre-impulse output'
    assert all(x == 0 for n, ch in enumerate(data) if n != lane for x in ch), 'cross-lane output'
    if bypass:
        assert abs(data[lane][IMPULSE] - AMP) <= AMP_ULP, 'bypass amplitude'
        assert all(x == 0 for x in data[lane][IMPULSE + 1:]), 'bypass tail'
    else:
        assert any(x != 0 for x in data[lane][IMPULSE:]), 'empty active response'


def select_length(response, bound=1e-6):
    """analyze_transfer.select_length, unchanged."""
    full_l1 = math.fsum(abs(x) for x in response)
    assert full_l1 > 0
    candidates = []
    for length in (1024, 2048, 4096, 8192, 16384, 32768, 65536):
        tail_l1 = math.fsum(abs(x) for x in response[length:])
        candidates.append({'taps': length, 'tail_l1': tail_l1,
                           'tail_l1_relative': tail_l1 / full_l1,
                           'tail_peak': max(map(abs, response[length:]), default=0)})
    eligible = [r for r in candidates if r['tail_l1_relative'] <= bound]
    assert eligible, 'measured tail not negligible within candidate length'
    return eligible[0]['taps'], candidates


def wav_float32(path, samples):
    """analyze_transfer.wav_float32, unchanged."""
    data = struct.pack('<' + 'f' * len(samples), *samples)
    fmt = struct.pack('<HHIIHH', 3, 1, RATE, RATE * 4, 4, 32)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt
    chunks += b'fact' + struct.pack('<II', 4, len(samples))
    chunks += b'data' + struct.pack('<I', len(data)) + data
    path.write_bytes(b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks)


def fft_convolve(a, b):
    """qualify_memory.fft_convolve, unchanged."""
    size = len(a) + len(b) - 1
    fftsize = 1 << (size - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(a, fftsize) * np.fft.rfft(b, fftsize), fftsize)[:size]


def float_coeff(values):
    """qualify_memory.float_coeff, unchanged."""
    return np.asarray(values, dtype='<f4').astype(np.float64)


def compare(actual, predicted):
    """qualify_memory.compare, unchanged."""
    peak = float(np.max(np.abs(actual)))
    assert peak > 0
    diff = np.asarray(actual, dtype=float) - predicted[:len(actual)]
    result = {'peak_output': peak, 'peak_error': float(np.max(np.abs(diff))),
              'relative_peak_error': float(np.max(np.abs(diff))) / peak,
              'rms_error': float(np.sqrt(np.mean(diff * diff)))}
    result['strict_1e4_relative_pass'] = result['relative_peak_error'] < 1e-4
    return result


# ---- J616 additions (not in Kb2uka's tool) --------------------------------------------

TABLE_HZ = (20, 40, 60, 80, 100, 150, 200, 300, 500, 1000, 2000, 5000, 10000, 16000)


def read_wav_float32(path):
    """Minimal RIFF reader for mono IEEE-float32 WAVs (Kb2uka's writer and asahi-audio's files)."""
    raw = path.read_bytes()
    assert raw[:4] == b'RIFF' and raw[8:12] == b'WAVE'
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        tag, size = raw[pos:pos + 4], struct.unpack_from('<I', raw, pos + 4)[0]
        body = raw[pos + 8:pos + 8 + size]
        if tag == b'fmt ':
            fmt = struct.unpack_from('<HHIIHH', body)
        elif tag == b'data':
            data = body
        pos += 8 + size + (size & 1)
    assert fmt and data is not None
    assert fmt[0] == 3 and fmt[1] == 1 and fmt[5] == 32, 'expected mono IEEE float32'
    return fmt[2], np.frombuffer(data, dtype='<f4').astype(np.float64)


def response_db(h, rate, freqs=TABLE_HZ):
    """Magnitude of the DTFT of h at the given frequencies, in dB (direct sum, like run's
    transfer-analysis.json in analyze_transfer.py)."""
    n = np.arange(len(h))
    out = []
    for f in freqs:
        value = np.sum(h * np.exp(-2j * np.pi * f * n / rate))
        out.append(20 * math.log10(max(abs(value), 1e-300)))
    return out


def band_mean_power_db(h, rate, lo, hi, points=400):
    """Mean of |H|^2 over a log-spaced grid lo..hi (a pink-noise-weighted level), in dB."""
    freqs = np.geomspace(lo, hi, points)
    n = np.arange(len(h))
    power = [abs(np.sum(h * np.exp(-2j * np.pi * f * n / rate))) ** 2 for f in freqs]
    return 10 * math.log10(float(np.mean(power)))
