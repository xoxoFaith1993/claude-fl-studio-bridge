"""Reference Ears - local "ears" for song references: Claude hears a reference song through measurements.

  python refears.py "<Artist - Title | YouTube link | audio file>" [--start 3:10 --end 3:40]
          [--focus guitar|vocals|bass|piano|other|drums] [--no-ai] [--no-split] [--ask "question for the listening AI"]

What it does (everything runs on your own PC; nothing is uploaded):
  1. fetch     a local audio file, or (optional) yt-dlp finds and downloads the song
  2. split     Demucs (htdemucs_6s) -> drums, bass, vocals, guitar, piano, other (synths/organ/strings)
  3. measure   tempo, key, chords, sections, loudness, frequency balance, which parts play where
  4. lead      for a window/focus part: notes (+ .mid), bends/slides, vibrato, scale, phrasing, tone (Basic Pitch)
  5. listen    optional local listening AI: Qwen3-Omni through llama.cpp describes genre/feel/tone in words
  6. vibe      optional style + quality models (vibe.py)
  7. report    report.md + report.json + PNG plots in <data_dir>/library/<song>/

Settings come from the FL bridge's config.json ("reference_ears"); see README.md. Measured numbers are reliable
most of the time; the listening AI's words are a second opinion.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'server'))
import config as _bridge_config  # noqa: E402  (the FL bridge's settings; standard library only)

CFG = _bridge_config.get('reference_ears') or {}
ROOT = CFG.get('data_dir') or os.path.join(_bridge_config.DATA_DIR, 'reference-ears')
LIB = os.path.join(ROOT, 'library')
MODELS = os.path.join(ROOT, 'models')
LLAMA = CFG.get('llama_dir') or os.path.join(ROOT, 'llama')
OMNI = os.path.join(MODELS, CFG.get('omni_model') or 'Qwen3-Omni-30B-A3B-Instruct-Q4_K_M.gguf')
OMNI_MM = os.path.join(MODELS, CFG.get('omni_mmproj') or 'mmproj-Qwen3-Omni-30B-A3B-Instruct-Q8_0.gguf')
os.environ.setdefault('TORCH_HOME', os.path.join(MODELS, 'torch'))
os.environ.setdefault('HF_HOME', os.path.join(MODELS, 'hf'))
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')

import numpy as np            # noqa: E402
import soundfile as sf        # noqa: E402
import librosa                # noqa: E402

SR = 44100
AN_SR = 22050
NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
STEMS = ['drums', 'bass', 'vocals', 'guitar', 'piano', 'other']
STEM_WORDS = {'other': 'other (synths/organ/strings/pads)'}
BANDS = [('sub', 20, 60), ('bass', 60, 250), ('low-mid', 250, 800), ('mid', 800, 2500),
         ('presence', 2500, 6000), ('air', 6000, 20000)]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def mmss(t):
    t = max(0.0, float(t))
    return '%d:%04.1f' % (t // 60, t % 60)


def parse_time(s):
    if s is None:
        return None
    parts = [float(p) for p in str(s).split(':')]
    return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0]


def safe(s):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', s).strip(' .')[:120] or 'reference'


def db(x):
    return float(20 * np.log10(max(float(x), 1e-9)))


# ------------------------------------------------------------------ 1. fetch

def get_audio(query):
    if os.path.isfile(query):
        folder = os.path.join(LIB, safe(os.path.splitext(os.path.basename(query))[0]))
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, os.path.basename(query))
        if os.path.abspath(dst) != os.path.abspath(query):
            shutil.copy2(query, dst)
        return folder, dst, os.path.basename(query), None
    folder = os.path.join(LIB, safe(query if not query.startswith('http') else 'link ' + query[-11:]))
    os.makedirs(folder, exist_ok=True)
    have = [f for f in os.listdir(folder) if f.lower().endswith(('.mp3', '.wav', '.flac', '.m4a'))] \
        if os.path.isdir(folder) else []
    meta = os.path.join(folder, 'source.json')
    if have and os.path.exists(meta):
        info = json.load(open(meta, encoding='utf-8'))
        return folder, info['path'], info['title'], info.get('url')
    if (CFG.get('fetch') or 'yt-dlp') != 'yt-dlp':
        raise SystemExit('Downloading is turned off (reference_ears.fetch = "files"): give an audio file instead.')
    log('fetching with yt-dlp:', query)
    import fetch
    try:
        info = fetch.fetch(query, folder)
    except Exception as e:
        raise SystemExit('Could not fetch it: %s. Give a local audio file instead.' % e)
    json.dump(info, open(meta, 'w', encoding='utf-8'), indent=1)
    return folder, info['path'], info['title'], info.get('url')


# ------------------------------------------------------------------ 2. split

def split(folder, stereo):
    sdir = os.path.join(folder, 'stems')
    paths = {s: os.path.join(sdir, s + '.wav') for s in STEMS}
    if all(os.path.exists(p) for p in paths.values()):
        return {s: sf.read(p, always_2d=True)[0].T for s, p in paths.items()}
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    torch.set_num_threads(os.cpu_count() or 4)
    log('splitting into parts (Demucs, CPU)...')
    t0 = time.time()
    model = get_model('htdemucs_6s')
    model.eval()
    wav = torch.tensor(stereo, dtype=torch.float32)
    ref = wav.mean(0)
    mu, sd = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((wav - mu) / sd)[None], device='cpu', split=True, overlap=0.25,
                          progress=False)[0]
    out = (out * sd + mu).numpy()
    os.makedirs(sdir, exist_ok=True)
    stems = {}
    for name, data in zip(model.sources, out):
        sf.write(paths[name], data.T, SR, subtype='PCM_16')
        stems[name] = data
    log('  split in %.0f s' % (time.time() - t0))
    return stems


# ------------------------------------------------------------------ 3. measure

KK_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def key_of(chroma_mean):
    scores = []
    for t in range(12):
        for prof, q in ((KK_MAJ, 'major'), (KK_MIN, 'minor')):
            scores.append((float(np.corrcoef(chroma_mean, np.roll(prof, t))[0, 1]), t, q))
    scores.sort(reverse=True)
    return scores


def chord_templates():
    out = []
    for r in range(12):
        for q, iv in (('', (0, 4, 7)), ('m', (0, 3, 7)), ('dim', (0, 3, 6))):
            v = np.zeros(12)
            v[[(r + i) % 12 for i in iv]] = 1
            v[r] += 0.3                                   # the root a bit stronger
            out.append((NAMES[r] + q, v / np.linalg.norm(v)))
    return out


def band_balance(y, sr):
    S = np.abs(librosa.stft(y, n_fft=4096, hop_length=2048)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=4096)
    tot = S.sum() + 1e-12
    return {n: round(100 * float(S[(f >= lo) & (f < hi)].sum() / tot), 1) for n, lo, hi in BANDS}


def width(st):
    if st.shape[0] < 2:
        return 0.0
    l, r = st[0], st[1]
    c = np.corrcoef(l, r)[0, 1] if l.std() > 1e-6 and r.std() > 1e-6 else 1.0
    return round(float(1 - c), 2)                         # 0 = mono, ~1 = very wide


def loudness(st):
    import pyloudnorm as pyln
    try:
        lufs = pyln.Meter(SR).integrated_loudness(st.T)
    except Exception:
        lufs = float('nan')
    peak = db(np.abs(st).max())
    rms = db(np.sqrt(np.mean(st ** 2)))
    return {'lufs': round(float(lufs), 1), 'peak_db': round(peak, 1), 'crest_db': round(peak - rms, 1)}


def measure(mix, stems):
    mono = librosa.resample(mix.mean(0), orig_sr=SR, target_sr=AN_SR)
    dur = len(mono) / AN_SR
    out = {'duration': round(dur, 1), 'loudness': loudness(mix), 'balance': band_balance(mono, AN_SR),
           'width': width(mix)}

    tempo, beats = librosa.beat.beat_track(y=mono, sr=AN_SR, hop_length=512)
    tempo = float(np.atleast_1d(tempo)[0])
    out['tempo'] = {'bpm': round(tempo, 1), 'half': round(tempo / 2, 1), 'double': round(tempo * 2, 1)}
    beat_t = librosa.frames_to_time(beats, sr=AN_SR, hop_length=512)

    # harmony from everything except drums and vocals
    if stems:
        harm = sum(stems[s] for s in ('bass', 'guitar', 'piano', 'other')).mean(0)
        harm = librosa.resample(harm, orig_sr=SR, target_sr=AN_SR)
    else:
        harm = librosa.effects.harmonic(mono)
    chroma = librosa.feature.chroma_cqt(y=harm, sr=AN_SR, hop_length=512)
    ks = key_of(chroma.mean(1))
    out['key'] = {'best': '%s %s' % (NAMES[ks[0][1]], ks[0][2]), 'score': round(ks[0][0], 2),
                  'runner_up': '%s %s' % (NAMES[ks[1][1]], ks[1][2]), 'runner_score': round(ks[1][0], 2)}
    out['_tonic'] = ks[0][1]
    out['_mode'] = ks[0][2]

    # chords per beat -> per bar (4 beats)
    bchroma = librosa.util.sync(chroma, beats, aggregate=np.median)
    temps = chord_templates()
    bars = []
    for i in range(0, bchroma.shape[1] - 1, 4):
        v = bchroma[:, i:i + 4].sum(1)
        if np.linalg.norm(v) < 1e-6:
            continue
        v = v / np.linalg.norm(v)
        name, score = max(((n, float(v @ t)) for n, t in temps), key=lambda x: x[1])
        t0 = beat_t[i] if i < len(beat_t) else (beat_t[-1] if len(beat_t) else 0)
        bars.append((float(t0), name, round(score, 2)))
    out['_bars'] = bars

    # sections
    mfcc = librosa.feature.mfcc(y=mono, sr=AN_SR, hop_length=512, n_mfcc=13)
    feat = np.vstack([librosa.util.normalize(librosa.util.sync(chroma, beats), axis=0),
                      librosa.util.normalize(librosa.util.sync(mfcc, beats), axis=1)])
    k = int(np.clip(round(dur / 22), 4, 12))
    bounds = librosa.segment.agglomerative(feat, k) if feat.shape[1] > k else np.arange(feat.shape[1])
    bt = np.concatenate([[0.0], beat_t])
    starts = sorted(set(float(bt[min(b, len(bt) - 1)]) for b in bounds))
    starts = [s for i, s in enumerate(starts) if i == 0 or s - starts[i - 1] > 4.0]
    ends = starts[1:] + [dur]
    means = []
    for s, e in zip(starts, ends):
        cols = ((bt >= s) & (bt < e))[:feat.shape[1]]
        means.append(feat[:, cols].mean(1) if cols.any() else feat.mean(1))
    labels, protos = [], []
    for m in means:
        for j, p in enumerate(protos):
            if np.dot(m, p) / (np.linalg.norm(m) * np.linalg.norm(p) + 1e-9) > 0.93:
                labels.append(chr(65 + j))
                break
        else:
            protos.append(m)
            labels.append(chr(65 + len(protos) - 1))
    stem_rms = {}
    if stems:
        for s in STEMS:
            y = stems[s].mean(0)
            hop = SR // 10
            stem_rms[s] = np.sqrt(np.convolve(y ** 2, np.ones(hop) / hop, mode='same')[::hop])
    mix_env = np.sqrt(np.convolve(mix.mean(0) ** 2, np.ones(SR // 10) / (SR // 10), mode='same')[::SR // 10])
    secs = []
    for (s, e, lab) in zip(starts, ends, labels):
        a, b = int(s * 10), max(int(e * 10), int(s * 10) + 1)
        sec = {'start': round(s, 1), 'end': round(e, 1), 'label': lab, 'level_db': round(db(mix_env[a:b].mean()), 1)}
        if stems:
            act = {}
            for st, env in stem_rms.items():
                ref = np.percentile(env, 95) + 1e-9
                act[st] = round(float(env[a:b].mean() / ref), 2)
            sec['parts'] = [p for p, v in sorted(act.items(), key=lambda x: -x[1]) if v > 0.18]
        ch = [n for t, n, sc in bars if s <= t < e]
        seq = [c for i, c in enumerate(ch) if i == 0 or c != ch[i - 1]]
        sec['chords'] = seq[:10]
        secs.append(sec)
    out['sections'] = secs

    if stems:
        tot = sum(float(np.mean(stems[s] ** 2)) for s in STEMS) + 1e-12
        parts = {}
        for s in STEMS:
            y = stems[s]
            share = float(np.mean(y ** 2)) / tot
            env = stem_rms[s]
            parts[s] = {'share_pct': round(100 * share, 1),
                        'active_pct': round(100 * float((env > np.percentile(env, 95) * 0.18).mean()), 0),
                        'width': width(y),
                        'brightness_hz': int(np.median(librosa.feature.spectral_centroid(
                            y=librosa.resample(y.mean(0), orig_sr=SR, target_sr=AN_SR)[:AN_SR * 240], sr=AN_SR)))}
        out['parts'] = parts
    return out


# ------------------------------------------------------------------ 4. lead line

SCALES = {'natural minor': [0, 2, 3, 5, 7, 8, 10], 'harmonic minor': [0, 2, 3, 5, 7, 8, 11],
          'dorian': [0, 2, 3, 5, 7, 9, 10], 'phrygian': [0, 1, 3, 5, 7, 8, 10],
          'minor pentatonic': [0, 3, 5, 7, 10], 'blues': [0, 3, 5, 6, 7, 10],
          'major': [0, 2, 4, 5, 7, 9, 11], 'mixolydian': [0, 2, 4, 5, 7, 9, 10],
          'major pentatonic': [0, 2, 4, 7, 9], 'lydian': [0, 2, 4, 6, 7, 9, 11]}


def nname(m):
    m = int(round(m))
    return NAMES[m % 12] + str(m // 12 - 1)


def lead(y_st, t_off, tonic, folder, tag):
    """Lead line of one part in a window: Basic Pitch (polyphonic) -> top line ('skyline') -> phrasing + tone."""
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict
    tmp = os.path.join(folder, '_lead_tmp.wav')
    sf.write(tmp, y_st.T, SR, subtype='PCM_16')
    try:
        _, _, ev = predict(tmp, ICASSP_2022_MODEL_PATH, onset_threshold=0.5, frame_threshold=0.3,
                           minimum_note_length=58, minimum_frequency=80)
    finally:
        os.remove(tmp)
    ft = 256 / AN_SR                                       # Basic Pitch frame time (bends are per frame)
    allnotes = [[float(s) + t_off, float(e - s), int(p), float(a), list(b or [])] for s, e, p, a, b in ev]
    res = {'notes_all_voices': len(allnotes)}
    if not allnotes:
        res['summary'] = 'no clear notes in this part/window'
        return res
    amax = max(n[3] for n in allnotes)
    cand = [n for n in allnotes if n[3] >= 0.25 * amax]

    def overlap(a, b):
        return max(0.0, min(a[0] + a[1], b[0] + b[1]) - max(a[0], b[0]))
    notes = [n for n in cand if not any(m[2] > n[2] and m[3] >= 0.5 * n[3] and overlap(m, n) > 0.5 * n[1]
                                        for m in cand)]
    notes.sort(key=lambda n: n[0])
    if notes:                                   # gaps in the lead fall to rhythm/bass notes: drop those
        mid = float(np.median([n[2] for n in notes]))
        notes = [n for n in notes if n[2] >= mid - 12]
    res['notes'] = len(notes)
    if len(notes) < 3:
        res['summary'] = 'too few clear lead notes in this part/window'
        return res

    # bends + vibrato from the per-note pitch-bend curves (1/3 semitone units)
    bends, vib = [], []
    for n in notes:
        b = np.array(n[4], dtype=float) / 3.0
        if len(b) < 3:
            continue
        rng = b.max() - b.min()
        if rng >= 0.75:
            up = b.argmax() > b.argmin()
            bends.append((n[0], n[2], round(float(rng) * (1 if up else -1), 1)))
        if n[1] >= 0.25 and len(b) >= 20:
            k = max(3, int(0.12 / ft))
            trend = np.convolve(b, np.ones(k) / k, mode='same')
            d = (b - trend)[k:-k]
            if len(d) >= 10:
                rate = np.sum(np.diff(np.sign(d)) != 0) / 2 / (len(d) * ft)
                depth = float(np.std(d) * 2.83 * 100 / 2)
                if 3.5 <= rate <= 9 and depth >= 12:
                    vib.append((rate, depth))
    legato = sum(1 for a, b in zip(notes, notes[1:]) if 0 <= b[0] - (a[0] + a[1]) < 0.03)
    pcs = np.zeros(12)
    for n in notes:
        pcs[n[2] % 12] += n[1]
    pcs /= pcs.sum()
    fits = []
    for nm, iv in SCALES.items():
        inside = sum(pcs[(tonic + x) % 12] for x in iv)
        fits.append((round(float(inside) - 0.012 * len(iv), 3), nm, round(100 * float(inside))))
    fits.sort(reverse=True)
    ints = [b[2] - a[2] for a, b in zip(notes, notes[1:]) if b[0] - (a[0] + a[1]) < 0.3]
    steps = sum(1 for x in ints if abs(x) <= 2)
    phrases, cur = [], [notes[0]]
    for a, b in zip(notes, notes[1:]):
        if b[0] - (a[0] + a[1]) > 0.3:
            phrases.append(cur)
            cur = []
        cur.append(b)
    phrases.append(cur)
    grams = {}
    for i in range(len(ints) - 2):
        g = tuple(ints[i:i + 3])
        grams[g] = grams.get(g, 0) + 1
    motifs = sorted(((c, g) for g, c in grams.items() if c >= 2 and any(g)), reverse=True)[:3]
    dur = notes[-1][0] + notes[-1][1] - notes[0][0]
    lo, hi = min(n[2] for n in notes), max(n[2] for n in notes)
    held = sorted(notes, key=lambda n: -n[1])[:5]

    # tone: brightness, balance, dynamics, ambience (distortion is left to the listening AI - it can't be
    # measured reliably from a separated part with several stacked guitars)
    y = librosa.resample(y_st.mean(0), orig_sr=SR, target_sr=AN_SR)
    hop = 256
    env = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
    envd = 20 * np.log10(env + 1e-9)
    tails = []
    for a, b in zip(notes, notes[1:] + [None]):
        e_i = int((a[0] + a[1] - t_off) / ft)
        room = (b[0] - (a[0] + a[1])) if b else 1.0
        if room < 0.35 or e_i >= len(envd):
            continue
        top = envd[max(0, e_i - 3):e_i + 1].max()
        k = e_i
        while k < len(envd) - 1 and envd[k] > top - 25 and (k - e_i) * ft < room:
            k += 1
        if envd[k] <= top - 25:
            tails.append((k - e_i) * ft * 60 / 25)
    rt = float(np.median(tails)) if tails else None
    space = ('no clear tails to judge' if rt is None else 'dry' if rt < 0.4 else 'short room' if rt < 1.1
             else 'medium hall/plate' if rt < 2.2 else 'long/washy reverb or delay')

    res.update({
        'span': '%s-%s' % (mmss(notes[0][0]), mmss(notes[-1][0] + notes[-1][1])),
        'range': '%s to %s (%d semitones)' % (nname(lo), nname(hi), hi - lo),
        'density': '%.1f notes/s' % (len(notes) / max(dur, 0.1)),
        'scale_fit': ['%s %s (%d%% of note time)' % (NAMES[tonic], nm, pct) for _, nm, pct in fits[:3]],
        'pitch_classes': {NAMES[i]: round(100 * float(pcs[i])) for i in np.argsort(-pcs)[:7] if pcs[i] > 0.02},
        'steps_vs_leaps': '%d%% stepwise' % (100 * steps / max(len(ints), 1)),
        'biggest_leaps': sorted(set(abs(x) for x in ints), reverse=True)[:3],
        'legato_connected': '%d%% of notes run straight into the next' % (100 * legato / max(len(notes) - 1, 1)),
        'phrases': len(phrases),
        'phrase_lengths_notes': [len(p) for p in phrases][:12],
        'phrase_contours': [('rising' if p[-1][2] > p[0][2] + 1 else 'falling' if p[-1][2] < p[0][2] - 1 else 'level')
                            for p in phrases if len(p) > 2][:12],
        'repeated_motifs': ['%s x%d' % (' '.join('%+d' % x for x in g), c) for c, g in motifs],
        'longest_notes': ['%s %.2fs @%s' % (nname(n[2]), n[1], mmss(n[0])) for n in held],
        'bends': '%d of %d notes bent' % (len(bends), len(notes)),
        'bend_examples': ['%s %s %+.1f semitones' % (mmss(t), nname(p), s) for t, p, s in bends[:8]],
        'vibrato': ('none detected' if not vib else 'on %d long notes, ~%.1f Hz, +/-%.0f cents'
                    % (len(vib), np.mean([v[0] for v in vib]), np.mean([v[1] for v in vib]))),
        'first_notes': ' '.join(nname(n[2]) for n in notes[:24]),
        'brightness_hz': int(np.median(librosa.feature.spectral_centroid(y=y, sr=AN_SR))),
        'balance': band_balance(y, AN_SR),
        'dynamics_crest_db': round(db(np.abs(y).max()) - db(np.sqrt(np.mean(y ** 2))), 1),
        'space': space + ('' if rt is None else ' (~%.1f s rough decay)' % rt),
        'width': width(y_st),
    })

    try:
        import mido
        mf = mido.MidiFile(ticks_per_beat=480)
        tr = mido.MidiTrack()
        mf.tracks.append(tr)
        tr.append(mido.MetaMessage('set_tempo', tempo=500000))
        evs = []
        for n in notes:
            evs.append((n[0] - notes[0][0], 1, n[2]))
            evs.append((n[0] - notes[0][0] + n[1], 0, n[2]))
        evs.sort()
        last = 0
        for t, on, p in evs:
            tick = int(round(t * 960))
            tr.append(mido.Message('note_on' if on else 'note_off', note=p, velocity=90, time=tick - last))
            last = tick
        mf.save(os.path.join(folder, 'notes_%s.mid' % tag))
        res['midi'] = os.path.join(folder, 'notes_%s.mid' % tag)
    except Exception as e:
        res['midi'] = 'failed: %s' % e
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(14, 5), dpi=100)
        for n in allnotes:
            ax.plot([n[0], n[0] + n[1]], [n[2], n[2]], color='0.75', lw=2)
        for n in notes:
            b = np.array(n[4], dtype=float) / 3.0
            if len(b):
                tt = n[0] + np.arange(len(b)) * ft
                ax.plot(tt, n[2] + b, color='#C8283C', lw=1.4)
            ax.plot([n[0], n[0] + n[1]], [n[2], n[2]], color='black', lw=3, alpha=0.5)
        ax.set_ylim(lo - 2, hi + 3)
        ax.set_yticks(range(lo - 1, hi + 3))
        ax.set_yticklabels([nname(m) for m in range(lo - 1, hi + 3)], fontsize=7)
        ax.grid(alpha=0.3)
        ax.set_xlabel('time (s)')
        ax.set_title('lead line - %s (black = lead notes, red = bends/vibrato, grey = other voices)' % tag)
        fig.tight_layout()
        p = os.path.join(folder, 'lead_%s.png' % tag)
        fig.savefig(p)
        plt.close(fig)
        res['plot'] = p
    except Exception as e:
        res['plot'] = 'failed: %s' % e
    return res


# ------------------------------------------------------------------ 5. listen (local AI)

PROMPT = ("You are an experienced music producer. Listen carefully and describe this audio for another producer: "
          "genre and era, instruments you hear, the tone of each (distortion, brightness, effects like reverb/delay), "
          "playing techniques (bends, vibrato, slides, palm mutes, legato), rhythm and groove, mood, and what makes it "
          "distinctive. Be specific and concise. If unsure about something, say so.")


def listen(wav, ask=None, title=None):
    cli = os.path.join(LLAMA, 'llama-mtmd-cli.exe')
    if not (os.path.exists(cli) and os.path.exists(OMNI) and os.path.exists(OMNI_MM)):
        return '(listening AI not set up - optional, see README.md: llama.cpp + Qwen3-Omni)'
    prompt = (('The track is "%s". ' % title) if title else '') + PROMPT + ((' Also answer: ' + ask) if ask else '')
    cmd = [cli, '-m', OMNI, '--mmproj', OMNI_MM, '--audio', wav, '-p', prompt] + list(CFG.get('llama_args') or []) + \
          ['-c', '16384', '-n', '600', '--temp', '0.3']
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900)
    txt = r.stdout.strip()
    if r.returncode or not txt:
        return '(listening AI failed: %s)' % (r.stderr.strip().splitlines() or ['?'])[-1][:200]
    log('  listened in %.0f s' % (time.time() - t0))
    return txt


def clip(data, a, b, path):
    seg = data[:, int(a * SR):int(b * SR)]
    sf.write(path, seg.T, SR, subtype='PCM_16')
    return path


# ------------------------------------------------------------------ 6. report

def report(r):
    L = []
    w = L.append
    w('# Reference: %s' % r['title'])
    if r.get('url'):
        w('Source: %s' % r['url'])
    m = r['measure']
    w('\n## Overview (measured)')
    w('- Length %s | tempo **%s BPM** (could also be felt as %s or %s) | key **%s** (runner-up %s)'
      % (mmss(m['duration']), m['tempo']['bpm'], m['tempo']['half'], m['tempo']['double'], m['key']['best'],
         m['key']['runner_up']))
    lo = m['loudness']
    w('- Loudness %s LUFS, peak %s dBFS, crest %s dB | stereo width %s (0 mono .. 1 wide)'
      % (lo['lufs'], lo['peak_db'], lo['crest_db'], m['width']))
    w('- Frequency balance %%: %s' % ', '.join('%s %s' % kv for kv in m['balance'].items()))
    if m.get('parts'):
        w('\n## Parts (from the split)')
        w('| part | share of energy | playing % of song | width | brightness |')
        w('|---|---|---|---|---|')
        for s, p in sorted(m['parts'].items(), key=lambda x: -x[1]['share_pct']):
            w('| %s | %s%% | %s%% | %s | %s Hz |' % (STEM_WORDS.get(s, s), p['share_pct'], int(p['active_pct']),
                                                  p['width'], p['brightness_hz']))
    w('\n## Sections (letters = similar material)')
    for s in m['sections']:
        w('- **%s** %s-%s  level %s dB  parts: %s  chords: %s'
          % (s['label'], mmss(s['start']), mmss(s['end']), s['level_db'], ', '.join(s.get('parts', [])) or '-',
             ' '.join(s['chords']) or '-'))
    for tag, ld in r.get('lead', {}).items():
        w('\n## Lead line: %s' % tag)
        if 'summary' in ld:
            w('- ' + ld['summary'])
            continue
        for k in ('span', 'range', 'density', 'first_notes', 'scale_fit', 'pitch_classes', 'steps_vs_leaps',
                  'biggest_leaps', 'legato_connected', 'phrases', 'phrase_lengths_notes', 'phrase_contours',
                  'repeated_motifs', 'longest_notes', 'bends', 'bend_examples', 'vibrato', 'brightness_hz', 'balance', 'dynamics_crest_db', 'space', 'width', 'midi',
                  'plot'):
            if k in ld:
                v = ld[k]
                w('- %s: %s' % (k.replace('_', ' '), ', '.join(map(str, v)) if isinstance(v, list) else v))
    for tag, txt in r.get('ai', {}).items():
        w('\n## Listening AI (%s) - second opinion' % tag)
        w(txt)
    w('\n_Measured with Reference Ears in %.0f s. Tempo can be half/double, key can be the relative major/minor, '
      'chords are rough on dense or distorted mixes._' % r['seconds'])
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('song')
    ap.add_argument('--start')
    ap.add_argument('--end')
    ap.add_argument('--focus', choices=STEMS + ['mix'])
    ap.add_argument('--no-split', action='store_true', help='skip the Demucs split (faster, fewer details)')
    ap.add_argument('--no-ai', action='store_true', help='skip the listening AI')
    ap.add_argument('--ask', help='extra question for the listening AI')
    ap.add_argument('--no-vibe', action='store_true', help='skip the vibe check (quality + style models)')
    ap.add_argument('--json-only', action='store_true', help='print report.json instead of the markdown')
    ap.add_argument('--vibe-ref', help='reference audio file for the vibe closeness score')
    a = ap.parse_args()
    t0 = time.time()
    folder, path, title, url = get_audio(a.song)
    log('analysing', title)
    data, sr = librosa.load(path, sr=SR, mono=False)
    data = np.atleast_2d(data)
    if data.shape[0] == 1:
        data = np.vstack([data, data])
    stems = None if a.no_split else split(folder, data)
    m = measure(data, stems)
    tonic = m.pop('_tonic')
    m.pop('_mode')
    bars = m.pop('_bars')
    r = {'title': title, 'url': url, 'file': path, 'folder': folder, 'measure': m,
         'chords_per_bar': [(round(t, 1), n) for t, n, _ in bars], 'lead': {}, 'ai': {}}
    s = parse_time(a.start)
    e = parse_time(a.end)
    if s is not None or a.focus:
        s = s or 0.0
        e = e or min(s + 45, m['duration'])
        cand = [a.focus] if a.focus else ['guitar', 'vocals', 'other', 'piano']
        src = {'mix': data} | (stems or {})
        if not stems:
            cand = ['mix']
        best = max(cand, key=lambda c: float(np.mean(src[c][:, int(s * SR):int(e * SR)] ** 2)))
        tag = '%s %s-%s' % (best, mmss(s), mmss(e))
        log('reading the', tag, 'line...')
        r['lead'][tag] = lead(src[best][:, int(s * SR):int(e * SR)], s, tonic, folder,
                              '%s_%d-%d' % (best, s, e))
        if not a.no_ai:
            r['ai']['window %s-%s, full mix' % (mmss(s), mmss(e))] = listen(
                clip(data, s, min(e, s + 30), os.path.join(folder, 'clip_mix.wav')), a.ask, title)
            if stems and best != 'mix':
                r['ai']['window, %s alone' % best] = listen(
                    clip(src[best], s, min(e, s + 30), os.path.join(folder, 'clip_%s.wav' % best)), a.ask, title)
    elif not a.no_ai:
        loud = max(m['sections'], key=lambda x: x['level_db'])
        mid = (loud['start'] + loud['end']) / 2
        r['ai']['loudest section %s' % loud['label']] = listen(
            clip(data, max(0, mid - 15), mid + 15, os.path.join(folder, 'clip_mix.wav')), a.ask, title)
    vibe_md = ''
    if not a.no_vibe:
        try:
            import vibe
            log('vibe check...')
            v, vm, vc = vibe.analyse(path)
            r['vibe'] = v
            vibe_md = '\n\n## Vibe check (style + quality models - see READING_THE_VIBE.md)\n' + \
                vibe.report({'this song': (v, vm, vc)}, vibe.analyse(a.vibe_ref) if a.vibe_ref else None)
        except Exception as ex:          # the rest of the report still matters
            log('vibe check failed:', ex)
    r['seconds'] = time.time() - t0
    md = report(r) + vibe_md
    open(os.path.join(folder, 'report.md'), 'w', encoding='utf-8').write(md)
    json.dump(r, open(os.path.join(folder, 'report.json'), 'w', encoding='utf-8'), indent=1, default=str)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # titles in Japanese etc.
    except Exception:
        pass
    print(json.dumps(r, default=str) if a.json_only else md)
    if not a.json_only:
        print('\n(report + files in %s)' % folder)


if __name__ == '__main__':
    main()
