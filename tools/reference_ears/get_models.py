"""Download the optional models for Reference Ears, showing each licence first. Nothing here is bundled.

  python get_models.py --list                       # what exists, sizes, licences, what's already downloaded
  python get_models.py listen                       # Qwen3-Omni GGUF for the listening AI (~19.9 GB, Apache-2.0)
  python get_models.py style --accept-noncommercial # Essentia Discogs-EffNet (18 MB, CC BY-NC-SA 4.0: NON-COMMERCIAL)
  python get_models.py quality                      # Audiobox Aesthetics checkpoint (CC-BY-4.0), via its package

Demucs (MIT code; its htdemucs_6s weights are published by the authors without a stated licence) and Basic Pitch
(Apache-2.0, model inside the pip package) download or ship by themselves on first use.
Files go to <data_dir>/models (reference_ears.data_dir in config.json). Big downloads: put data_dir on a drive
with room (the listening model alone is about 20 GB).
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'server'))
import config  # noqa: E402

CFG = config.get('reference_ears') or {}
ROOT = CFG.get('data_dir') or os.path.join(config.DATA_DIR, 'reference-ears')
MODELS = os.path.join(ROOT, 'models')
HF = 'https://huggingface.co/ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF/resolve/main/'
ESS = 'https://essentia.upf.edu/models/feature-extractors/discogs-effnet/'

SETS = {
    'listen': dict(licence='Apache-2.0 (Qwen3-Omni-30B-A3B-Instruct; GGUF by ggml-org)', noncommercial=False,
                   files=[(HF + (CFG.get('omni_model') or 'Qwen3-Omni-30B-A3B-Instruct-Q4_K_M.gguf'), None),
                          (HF + (CFG.get('omni_mmproj') or 'mmproj-Qwen3-Omni-30B-A3B-Instruct-Q8_0.gguf'), None)],
                   note='also needs llama.cpp (MIT): a Windows release zip (e.g. the Vulkan build) unpacked into '
                        'reference_ears.llama_dir, containing llama-mtmd-cli.exe'),
    'style': dict(licence='CC BY-NC-SA 4.0 - NON-COMMERCIAL use only (Music Technology Group, UPF; essentia.upf.edu/models)',
                  noncommercial=True,
                  files=[(ESS + 'discogs-effnet-bsdynamic-1.onnx', 'essentia'), (ESS + 'discogs-effnet-bsdynamic-1.json', 'essentia')]),
}


def target(url, sub):
    d = os.path.join(MODELS, sub) if sub else MODELS
    return os.path.join(d, url.rsplit('/', 1)[1])


def download(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + '.part'
    print('downloading', url)
    with urllib.request.urlopen(url) as r, open(part, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        got = 0
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print('\r  %5.1f%% of %.2f GB' % (100 * got / total, total / 1e9), end='', flush=True)
    print()
    os.replace(part, path)


def main(args):
    if not args or '--list' in args:
        print('models folder:', MODELS)
        for name, s in SETS.items():
            have = all(os.path.exists(target(u, d)) for u, d in s['files'])
            print('- %-8s %s  [%s]' % (name, s['licence'], 'downloaded' if have else 'not downloaded'))
            if s.get('note'):
                print('           ' + s['note'])
        print('- quality  CC-BY-4.0 (Meta Audiobox Aesthetics): pip install audiobox_aesthetics; checkpoint downloads on first use')
        return
    if 'quality' in args:
        print('Audiobox Aesthetics (CC-BY-4.0): install with  pip install audiobox_aesthetics  - its checkpoint is '
              'fetched from facebook/audiobox-aesthetics on first use.')
    for name in [a for a in args if a in SETS]:
        s = SETS[name]
        print('\n%s: licence %s' % (name, s['licence']))
        if s['noncommercial'] and '--accept-noncommercial' not in args:
            print('  skipped: this model may only be used non-commercially. Re-run with --accept-noncommercial if '
                  'that fits your use.')
            continue
        for url, sub in s['files']:
            p = target(url, sub)
            if os.path.exists(p):
                print('  already there:', p)
            else:
                download(url, p)
        if s.get('note'):
            print('  note: ' + s['note'])


if __name__ == '__main__':
    main(sys.argv[1:])
