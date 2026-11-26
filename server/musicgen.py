"""Local copilot: ask a local Ollama model for a chord progression + melody, then fix it with music theory.

The model is good at mood, chord choice and melodic shape; it is unreliable at theory. So:
  - chords are parsed to plain triads and kept only if they belong to the key (natural or harmonic minor)
  - chords are voiced with smooth voice leading under the melody
  - melody notes are snapped to the scale, kept in range, and on strong beats moved to chord tones
  - no melody note may sit a semitone away from a sounding chord tone (no clashing notes), and
    check() refuses the result if any rub is left
Used by the fl_local_idea tool; works offline, no Claude needed for the idea itself.
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

import ollama_ctl  # noqa: E402
OLLAMA = ollama_ctl.OLLAMA
MODEL = os.environ.get('FL_OLLAMA_MODEL') or config.get('local_ai').get('idea_model')
NOTE = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
MAJOR, MINOR, HARM = [0, 2, 4, 5, 7, 9, 11], [0, 2, 3, 5, 7, 8, 10], [0, 2, 3, 5, 7, 8, 11]

PROMPT = '''You are a film-score composer. Write a short piano piece idea for this request: "{req}".
Reply with JSON only, in this shape:
{{"title": str, "bpm": int, "key": "A minor", "bars": 8,
  "chords": [one chord symbol per bar, e.g. "Am", "F", "C", "G", "Dm", "E"],
  "melody": [[bar 1 notes], [bar 2 notes], ...]}}
Each bar's notes are objects {{"note": "E5", "beats": 1}} whose beats add up to 4 (4/4 time).
Use simple triad chords from the key. Make the melody singable: mostly steps, a few leaps, a clear high point
around bar 5-6, and end on the tonic. Notes between C4 and C6.'''


def ask(req, model=MODEL, seed=None):
    import ollama_ctl
    if not ollama_ctl.ensure_running():
        raise RuntimeError('The local AI (Ollama) is not running and could not be started. It is optional: install '
                           'Ollama from ollama.com and run "ollama pull %s".' % model)
    if not ollama_ctl.has_model(model):
        raise RuntimeError('Ollama has no "%s" model yet. Run: ollama pull %s' % (model, model))
    body = {'model': model, 'stream': False, 'format': 'json', 'keep_alive': '10m',
            'options': {'temperature': 0.8, **({'seed': seed} if seed is not None else {})},
            'messages': [{'role': 'user', 'content': PROMPT.format(req=req)}]}
    r = urllib.request.Request(OLLAMA + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=300) as f:
        return json.loads(json.load(f)['message']['content'])


# ---------------------------------------------------------------- theory helpers
def parse_key(k):
    m = re.match(r'\s*([A-Ga-g])([#b]?)\s*(major|minor|maj|min|m)?', k or 'C major')
    tonic = (NOTE[m.group(1).upper()] + {'#': 1, 'b': -1, '': 0}[m.group(2)]) % 12
    minor = (m.group(3) or 'major').lower() in ('minor', 'min', 'm')
    return tonic, minor


def scale_pcs(tonic, minor):
    return {(tonic + i) % 12 for i in (MINOR if minor else MAJOR)}


def parse_chord(sym):
    m = re.match(r'\s*([A-G])([#b]?)(m(?!aj))?(dim)?', sym or '')
    if not m:
        return None
    root = (NOTE[m.group(1)] + {'#': 1, 'b': -1, '': 0}[m.group(2)]) % 12
    third = 3 if (m.group(3) or m.group(4)) else 4
    fifth = 6 if m.group(4) else 7
    return root, [root, (root + third) % 12, (root + fifth) % 12]


def note_num(name):
    m = re.match(r'\s*([A-G])([#b]?)(-?\d)', name or '')
    if not m:
        return None
    return 12 * (int(m.group(3)) + 1) + NOTE[m.group(1)] + {'#': 1, 'b': -1, '': 0}[m.group(2)]


def nearest(p, pcs, prefer_up=False):
    for d in sorted(range(-6, 7), key=lambda d: (abs(d), -d if prefer_up else d)):
        if (p + d) % 12 in pcs:
            return p + d
    return p


def clashes(p, chord_pcs):
    return any((p - c) % 12 in (1, 11) for c in chord_pcs)


# ---------------------------------------------------------------- fix and arrange
def arrange(idea):
    """Turn the model's idea into checked notes: list of {pitch, start, length, velocity} in beats."""
    tonic, minor = parse_key(idea.get('key'))
    ok_pcs = scale_pcs(tonic, minor) | ({(tonic + 11) % 12} if minor else set())  # raised 7th for V in minor
    mel_pcs = scale_pcs(tonic, minor)
    report = []

    chords = []
    for i, sym in enumerate(idea.get('chords') or []):
        c = parse_chord(sym)
        if not c or not set(c[1]) <= ok_pcs:
            report.append('bar %d: chord %r not in key -> tonic' % (i + 1, sym))
            c = (tonic, [tonic, (tonic + (3 if minor else 4)) % 12, (tonic + 7) % 12])
        chords.append(c)
    chords = chords[:16] or [(tonic, [tonic, (tonic + (3 if minor else 4)) % 12, (tonic + 7) % 12])] * 4
    bars = len(chords)

    notes = []
    # chords: bass root (octave 2-3) + close triad voiced near the previous one, kept below the melody
    prev = [55, 60, 64]
    for b, (root, pcs) in enumerate(chords):
        bass = 36 + (root - 36) % 12
        cands = []
        for base in range(48, 61):
            if base % 12 in pcs:
                v = [base] + [base + ((pc - base) % 12) for pc in pcs if pc != base % 12]
                v.sort()
                if v[-1] <= 67:
                    cands.append(v)
        voicing = min(cands, key=lambda v: sum(abs(a - b) for a, b in zip(v, prev)))
        prev = voicing
        t = b * 4
        notes.append({'pitch': bass, 'start': t, 'length': 4, 'velocity': 0.62})
        notes.append({'pitch': bass + 12, 'start': t + 2, 'length': 2, 'velocity': 0.45})
        for j, p in enumerate(voicing):
            notes.append({'pitch': p, 'start': t + 0.04 * j, 'length': 3.9 - 0.04 * j, 'velocity': 0.5})

    # melody
    mel = idea.get('melody') or []
    # move the whole tune by octaves so it sits above the chords (around D5); single notes keep their shape
    raw = [note_num(n.get('note')) for bar in mel if isinstance(bar, list) for n in bar if isinstance(n, dict)]
    raw = sorted(p for p in raw if p is not None)
    shift = 12 * round((74 - raw[len(raw) // 2]) / 12) if raw else 0
    floor = max(n['pitch'] for n in notes if n['velocity'] == 0.5) + 2
    last = None
    for b in range(bars):
        root, pcs = chords[b]
        top = max(n['pitch'] for n in notes if n['start'] >= b * 4 and n['start'] < b * 4 + 0.2)
        bar = mel[b] if b < len(mel) and isinstance(mel[b], list) else []
        beats = [max(0.25, float(n.get('beats', 1))) for n in bar if isinstance(n, dict)]
        names = [n.get('note') for n in bar if isinstance(n, dict)]
        if not beats or sum(beats) <= 0:
            names, beats = [None], [4.0]
        scale = 4.0 / sum(beats)
        t = 0.0
        for name, d in zip(names, beats):
            d = round(d * scale * 4) / 4 or 0.25
            if t >= 4:
                break
            d = min(d, 4 - t)
            p = note_num(name)
            p = p + shift if p is not None else (last if last is not None else 74)
            orig = p
            # downbeats and long notes must be chord tones; other notes any scale tone that does not rub
            strong = t == 0.0 or d >= 2
            p = nearest(p, set(pcs) if strong else mel_pcs)
            if clashes(p, pcs):
                p = nearest(p, set(pcs))
            while p < max(floor, top + 2):
                p += 12
            while p > 88:
                p -= 12
            if last is not None and abs(p - last) > 9:  # tame wide leaps
                p2 = p - 12 if p > last else p + 12
                if max(floor, top + 2) <= p2 <= 88:
                    p = p2
            if p % 12 != orig % 12:
                report.append('bar %d beat %g: %s -> %s' % (b + 1, t + 1, name, NAMES[p % 12] + str(p // 12 - 1)))
            vel = 0.78 if t == 0 else 0.7
            notes.append({'pitch': p, 'start': b * 4 + t, 'length': d * 0.97, 'velocity': vel})
            last = p
            t += d
    return {'title': idea.get('title', 'Untitled'), 'bpm': int(idea.get('bpm') or 80), 'key': idea.get('key'),
            'chords': (idea.get('chords') or [])[:bars], 'bars': bars, 'length_beats': bars * 4,
            'notes': notes, 'fixes': report}


def check(notes):
    """Count semitone rubs between notes that sound at the same time (should be 0)."""
    bad = 0
    for i, a in enumerate(notes):
        for b in notes[i + 1:]:
            if a['start'] < b['start'] + b['length'] and b['start'] < a['start'] + a['length']:
                if abs(a['pitch'] - b['pitch']) % 12 in (1, 11):
                    bad += 1
    return bad
