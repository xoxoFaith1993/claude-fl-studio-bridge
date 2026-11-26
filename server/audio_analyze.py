"""Mix analysis of a rendered song (numpy only): loudness (BS.1770 / LUFS), true peak, dynamics,
per-section levels, frequency balance and stereo width.

  python audio_analyze.py song.wav [bpm] [section bars like "1-8:intro,9-16:drop,..."]
"""
import json
import math
import sys
import wave

import numpy as np

BANDS = [('sub', 20, 60), ('bass', 60, 120), ('low-mid', 120, 500), ('mid', 500, 2000),
         ('upper-mid', 2000, 5000), ('presence', 5000, 10000), ('air', 10000, 20000)]


def _is_float(path):
    """True for IEEE-float WAVs (format tag 3, or WAVE_FORMAT_EXTENSIBLE with the float sub-format)."""
    import struct
    with open(path, 'rb') as f:
        head = f.read(4096)
    i = head.find(b'fmt ')
    if i < 0:
        return False
    tag = struct.unpack('<H', head[i + 8:i + 10])[0]
    if tag == 0xFFFE:                                   # extensible: sub-format GUID starts at offset 32
        tag = struct.unpack('<H', head[i + 8 + 24:i + 8 + 26])[0]
    return tag == 3


def _load_float(path):
    import struct
    with open(path, 'rb') as f:
        data = f.read()
    i = data.find(b'fmt ')
    ch, sr = struct.unpack('<HI', data[i + 10:i + 16])
    bits = struct.unpack('<H', data[i + 22:i + 24])[0]
    j = data.find(b'data', i)
    n = struct.unpack('<I', data[j + 4:j + 8])[0]
    x = np.frombuffer(data[j + 8:j + 8 + n], '<f4' if bits == 32 else '<f8').astype(np.float64)
    x = x[:len(x) // ch * ch].reshape(-1, ch)
    if ch == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2], sr


def load(path):
    if _is_float(path):
        return _load_float(path)
    with wave.open(path, 'rb') as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, '<i2').astype(np.float64) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        x = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        x = np.where(x & 0x800000, x - 0x1000000, x).astype(np.float64) / 8388608
    elif sw == 4:
        x = np.frombuffer(raw, '<i4').astype(np.float64) / 2147483648
    else:
        raise ValueError('unsupported sample width %d' % sw)
    x = x.reshape(-1, ch)
    if ch == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2], sr


def biquad_response(b, a, freqs, sr):
    z = np.exp(-1j * 2 * np.pi * freqs / sr)
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def k_weighting(sr):
    """BS.1770-4 K-weighting biquads for any sample rate (same formulas as pyloudnorm)."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    b2 = [1, -2, 1]
    a2 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return (b1, a1), (b2, a2)


def k_filter(x, sr):
    n = len(x)
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1 / sr)
    (b1, a1), (b2, a2) = k_weighting(sr)
    H = biquad_response(b1, a1, f, sr) * biquad_response(b2, a2, f, sr)
    return np.fft.irfft(X * H[:, None], n=n, axis=0)


def lufs_blocks(y, sr, block=0.4, overlap=0.75):
    step = int(sr * block * (1 - overlap))
    size = int(sr * block)
    out = []
    for s in range(0, len(y) - size + 1, step):
        ms = (y[s:s + size] ** 2).mean(axis=0).sum()
        out.append(-0.691 + 10 * math.log10(ms) if ms > 0 else -200)
    return np.array(out)


def integrated(blocks):
    b = blocks[blocks > -70]
    if not len(b):
        return -200
    rel = 10 * math.log10((10 ** (b / 10)).mean()) - 10
    b = b[b > rel]
    return 10 * math.log10((10 ** (b / 10)).mean())


def true_peak(x, sr, factor=4, chunk=2 ** 16):
    peak = 0.0
    for c in range(x.shape[1]):
        for s in range(0, len(x), chunk):
            seg = x[max(0, s - 64):s + chunk + 64, c]
            n = len(seg)
            S = np.fft.rfft(seg)
            up = np.fft.irfft(S, n * factor) * factor
            peak = max(peak, np.abs(up[64 * factor:-64 * factor] if n > 256 else up).max())
    return 20 * math.log10(peak) if peak > 0 else -200


def db(v):
    return 20 * math.log10(v) if v > 0 else -200


def band_balance(x, sr):
    """Energy per band (dB relative to total) from an averaged spectrum, plus side/mid ratio per band."""
    mid = x.mean(axis=1)
    side = (x[:, 0] - x[:, 1]) / 2
    n = 8192
    win = np.hanning(n)
    pm = np.zeros(n // 2 + 1)
    ps = np.zeros(n // 2 + 1)
    for s in range(0, len(mid) - n, n // 2):
        pm += np.abs(np.fft.rfft(mid[s:s + n] * win)) ** 2
        ps += np.abs(np.fft.rfft(side[s:s + n] * win)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = pm[(f >= 20) & (f < 20000)].sum() or 1e-30
    out = {}
    for name, lo, hi in BANDS:
        m = (f >= lo) & (f < hi)
        e, es = pm[m].sum(), ps[m].sum()
        out[name] = {'share_db': round(10 * math.log10(e / tot), 1) if e > 0 else -99,
                     'side_vs_mid_db': round(10 * math.log10(es / e), 1) if e > 0 and es > 0 else -99}
    return out


def analyse(path, bpm=None, sections=None):
    x, sr = load(path)
    y = k_filter(x, sr)
    blocks = lufs_blocks(y, sr)
    st = lufs_blocks(y, sr, block=3.0, overlap=0.9)
    st_g = st[st > -70]
    res = {'file': path, 'seconds': round(len(x) / sr, 2), 'sample_rate': sr,
           'integrated_lufs': round(integrated(blocks), 1),
           'short_term_max_lufs': round(float(st.max()), 1),
           'loudness_range_lu': round(float(np.percentile(st_g, 95) - np.percentile(st_g, 10)), 1) if len(st_g) else None,
           'sample_peak_dbfs': round(db(np.abs(x).max()), 2),
           'true_peak_dbtp': round(true_peak(x, sr), 2),
           'rms_dbfs': round(db(math.sqrt((x ** 2).mean())), 1),
           'stereo_correlation': round(float(np.corrcoef(x[:, 0], x[:, 1])[0, 1]), 3),
           'balance': band_balance(x, sr)}
    res['crest_db'] = round(res['sample_peak_dbfs'] - res['rms_dbfs'], 1)
    res['plr_db'] = round(res['true_peak_dbtp'] - res['integrated_lufs'], 1)
    if bpm and sections:
        bar = 4 * 60 / bpm
        res['sections'] = []
        for (a, b, name) in sections:
            s, e = int((a - 1) * bar * sr), min(len(x), int(b * bar * sr))
            if s >= e:
                continue
            seg, yseg = x[s:e], y[s:e]
            bl = lufs_blocks(yseg, sr)
            res['sections'].append({
                'bars': '%g-%g' % (a, b), 'name': name,
                'lufs': round(integrated(bl), 1) if len(bl) else None,
                'peak_dbfs': round(db(np.abs(seg).max()), 1),
                'rms_dbfs': round(db(math.sqrt((seg ** 2).mean())), 1),
                'balance': {k: v['share_db'] for k, v in band_balance(seg, sr).items()},
                'low_end_side_db': band_balance(seg, sr)['bass']['side_vs_mid_db']})
    return res


if __name__ == '__main__':
    path = sys.argv[1]
    bpm = float(sys.argv[2]) if len(sys.argv) > 2 else None
    secs = []
    if len(sys.argv) > 3:
        for part in sys.argv[3].split(','):
            rng, name = part.split(':')
            a, b = rng.split('-')
            secs.append((float(a), float(b), name))
    print(json.dumps(analyse(path, bpm, secs), indent=1))
