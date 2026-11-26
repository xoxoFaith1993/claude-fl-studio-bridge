"""Vibe check: model 'ears' for what measurements miss - what genre does it read as, how polished is the
production, how close is it to a reference. Everything runs locally; models live in <data_dir>/models.

  vibe.py song.mp3 [more files...] [--ref reference.mp3] [--watch Electronic---Vaporwave,...] [--json out.json]

Ears:
  quality  Meta Audiobox Aesthetics (1-10, trained on human ratings): PQ production quality,
           PC production complexity, CE content enjoyment, CU content usefulness.
  styles   Essentia Discogs-EffNet (400 Discogs styles, sigmoid 0-1): top styles + watched ones.
  close    Discogs-EffNet 1280-d style embeddings (~10 s chunks): cosine of the song means, and the
           average best match of each chunk in the reference. (LAION CLAP larger_clap_music was tried and
           its text side was degenerate - all prompts ~0.999 alike - so it is not used.)
Results are cached per file (path + size + mtime) in library/_vibe_cache.
Model licences: Audiobox Aesthetics CC-BY-4.0; the Essentia Discogs-EffNet model is CC BY-NC-SA 4.0
(NON-COMMERCIAL) - it is downloaded only if you opt in (get_models.py --accept-noncommercial).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'server'))
import config as _bridge_config  # noqa: E402

_CFG = _bridge_config.get('reference_ears') or {}
ROOT = _CFG.get('data_dir') or os.path.join(_bridge_config.DATA_DIR, 'reference-ears')
MODELS = os.path.join(ROOT, 'models')
CACHE = os.path.join(ROOT, 'library', '_vibe_cache')
os.environ.setdefault('HF_HOME', os.path.join(MODELS, 'hf'))
import numpy as np  # noqa: E402
EFFNET = os.path.join(MODELS, 'essentia', 'discogs-effnet-bsdynamic-1')
VERSION = 4


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load(path, sr):
    raw = subprocess.run(['ffmpeg', '-v', 'quiet', '-i', path, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.float32).copy()


# ------------------------------------------------------------------ quality (Audiobox Aesthetics)
_aes = None


def quality(x16):
    global _aes
    import torch
    if _aes is None:
        from audiobox_aesthetics.infer import AesPredictor
        _aes = AesPredictor(checkpoint_pth=None, data_col='path')
    wav = torch.from_numpy(x16)[None, :]
    out = {}
    step = 16000 * 300                                  # 5-minute slices keep memory sane
    rows, w = [], []
    for i in range(0, len(x16), step):
        seg = wav[:, i:i + step]
        if seg.shape[1] < 16000 * 2:
            continue
        rows.append(_aes.forward([{'path': seg, 'sample_rate': 16000}])[0])
        w.append(seg.shape[1])
    for k in rows[0]:
        out[k] = round(float(np.average([r[k] for r in rows], weights=w)), 2)
    return out


# ------------------------------------------------------------------ styles (Discogs-EffNet, ONNX)
_eff = None


def effnet_mel(x16):
    """Essentia TensorflowInputMusiCNN: 512/256 hann frames, POWER spectrum, 96 slaney-normalised mel bands,
    log10(1+1e4 x). Chosen by testing on known-genre tracks: magnitude or unnormalised bands gave
    Noise/Speedcore nonsense."""
    import librosa
    S = np.abs(librosa.stft(x16, n_fft=512, hop_length=256, window='hann', center=False)) ** 2
    fb = librosa.filters.mel(sr=16000, n_fft=512, n_mels=96, fmin=0, fmax=8000, htk=False, norm='slaney')
    mel = fb @ S
    return np.log10(1 + 10000 * mel).T.astype(np.float32)          # frames x 96


def styles(x16, watch=()):
    global _eff
    import onnxruntime as ort
    if _eff is None:
        if not os.path.exists(EFFNET + '.onnx'):
            raise RuntimeError('style model not downloaded (optional, non-commercial licence): '
                               'run get_models.py --accept-noncommercial')
        meta = json.load(open(EFFNET + '.json'))
        sess = ort.InferenceSession(EFFNET + '.onnx', providers=['CPUExecutionProvider'])
        _eff = (sess, meta['classes'], sess.get_inputs()[0].name)
    sess, classes, iname = _eff
    m = effnet_mel(x16)
    patches = [m[i:i + 128] for i in range(0, len(m) - 128, 62)]
    probs, embs = [], []
    for i in range(0, len(patches), 64):
        out = sess.run(None, {iname: np.stack(patches[i:i + 64])})
        probs.append(out[0])
        embs.append(out[1])
    p = np.concatenate(probs).mean(0)
    e = np.concatenate(embs)                                   # one per ~2 s patch
    per = 5                                                    # ~10 s chunks
    chunks = unit(np.stack([e[i:i + per].mean(0) for i in range(0, len(e), per)]))
    order = np.argsort(-p)
    top = [(classes[i], round(float(p[i]), 3)) for i in order[:8]]
    watched = {w: round(float(p[classes.index(w)]), 3) for w in watch if w in classes}
    groups = {}
    for c, v in zip(classes, p):
        g = c.split('---')[0]
        groups[g] = max(groups.get(g, 0), float(v))
    return {'top': top, 'watch': watched, 'genres': {k: round(v, 3) for k, v in
                                                       sorted(groups.items(), key=lambda kv: -kv[1])[:5]}},         unit(chunks.mean(0)), chunks


def unit(v):
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


# ------------------------------------------------------------------ driver

def key(path):
    st = os.stat(path)
    return hashlib.sha1(('%s|%d|%d|%d' % (os.path.abspath(path).lower(), st.st_size, int(st.st_mtime), VERSION))
                        .encode()).hexdigest()[:16]


def analyse(path, watch=()):
    os.makedirs(CACHE, exist_ok=True)
    k = key(path)
    jf, nf = os.path.join(CACHE, k + '.json'), os.path.join(CACHE, k + '.npz')
    if os.path.exists(jf) and os.path.exists(nf):
        r = json.load(open(jf, encoding='utf-8'))
        z = np.load(nf)
        if all(w in r['styles']['watch'] for w in watch):
            return r, z['mean'], z['chunks']
    t0 = time.time()
    log('vibe:', os.path.basename(path))
    x16 = load(path, 16000)
    r = {'file': path, 'seconds': round(len(x16) / 16000, 1)}
    r['quality'] = quality(x16)
    r['styles'], mean, chunks = styles(x16, watch)
    r['took'] = round(time.time() - t0, 1)
    json.dump(r, open(jf, 'w', encoding='utf-8'), indent=1)
    np.savez(nf, mean=mean, chunks=chunks)
    return r, mean, chunks


def closeness(a_mean, a_chunks, b_mean, b_chunks):
    """Song-level cosine + how much of A has a close match somewhere in B (best chunk match, averaged)."""
    return round(float(a_mean @ b_mean), 3), round(float((a_chunks @ b_chunks.T).max(1).mean()), 3)


def report(results, ref=None):
    lines = ['| track | PQ | PC | CE | CU | Discogs top styles |' + (' ref close (song / best-chunk) |' if ref else ''),
             '|---|---|---|---|---|---|' + ('---|' if ref else '')]
    for name, (r, m, c) in results.items():
        q, s = r['quality'], r['styles']
        top = ', '.join('%s %.2f' % (t.split('---')[-1], v) for t, v in s['top'][:3])
        row = '| %s | %.1f | %.1f | %.1f | %.1f | %s |' % (name, q['PQ'], q['PC'], q['CE'], q['CU'], top)
        if ref:
            row += ' %.2f / %.2f |' % closeness(m, c, ref[1], ref[2])
        lines.append(row)
    watch = sorted({w for r, _, _ in results.values() for w in r['styles']['watch']})
    if watch:
        lines += ['', '| track | ' + ' | '.join(w.split('---')[-1] for w in watch) + ' |',
                  '|---|' + '---|' * len(watch)]
        for name, (r, _, _) in results.items():
            lines.append('| %s | ' % name + ' | '.join('%.3f' % r['styles']['watch'].get(w, 0) for w in watch) + ' |')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+', help='audio files, optionally "label=path"')
    ap.add_argument('--ref')
    ap.add_argument('--watch', default='')
    ap.add_argument('--json')
    a = ap.parse_args()
    watch = [w for w in a.watch.split(',') if w]
    res = {}
    for f in a.files:
        label, path = (f.split('=', 1) if '=' in f and not os.path.exists(f) else (os.path.splitext(os.path.basename(f))[0], f))
        res[label[:40]] = analyse(path, watch)
    ref = analyse(a.ref, watch) if a.ref else None
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print(report(res, ref))
    if a.json:
        json.dump({k: v[0] for k, v in res.items()}, open(a.json, 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main()
