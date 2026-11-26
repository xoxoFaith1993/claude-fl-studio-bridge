"""Optional orchestral route: render parts with Muse Sounds (MuseScore Studio) and bring them into FL Studio.

  notes (beats) -> MusicXML (slurs, dynamics, tempo marks) -> MuseScore Studio CLI with Muse Sounds -> WAV per part
  -> optional: cut into phrase clips + an SFZ map for sfizz, so FL triggers each clip with one note

Why MusicXML and not MIDI: Muse Sounds play real legato only under slurs and shape phrases from dynamics and
hairpins - notation that MIDI can't carry. Every part is written as the whole piece from bar 1 (rests where it is
silent), so each render lines up with FL's timeline from the first beat. The render follows the written tempo map
exactly, so cut clips on that same timeline; don't re-align them by onset detection (soft bowed entries swell in,
which puts a detected onset 100-400 ms late).

Why sfizz for clips: FL's Sampler plays a long audio file to its end once triggered, even after Stop; sfizz obeys
note-off, so a clip stops when its note ends or FL stops.

Needs MuseScore Studio 4 (GPL-3, musescore.org) with Muse Sounds installed through Muse Hub (free, not open
source). Standard library only.
"""
import json
import os
import shutil
import subprocess

import config

DIV = 4                                         # divisions per quarter note (16ths)
STEP = ['C', 'C', 'D', 'D', 'E', 'F', 'F', 'G', 'G', 'A', 'A', 'B']
ALTER = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
TYPES = {1: ('16th', 0), 2: ('eighth', 0), 3: ('eighth', 1), 4: ('quarter', 0), 6: ('quarter', 1),
         8: ('half', 0), 12: ('half', 1), 16: ('whole', 0)}
DYNAMICS = ('ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff')

# instrument: (MusicXML instrument-sound, name, clef, (diatonic, chromatic, octave-change) written-vs-concert or None)
# Transposing instruments are written at their written pitch; <transpose> tells MuseScore how they sound.
INSTRUMENTS = {
    'violin': ('strings.violin', 'Violin', ('G', 2), None),
    'viola': ('strings.viola', 'Viola', ('C', 3), None),
    'cello': ('strings.cello', 'Violoncello', ('F', 4), None),
    'contrabass': ('strings.contrabass', 'Contrabass', ('F', 4), (0, 0, -1)),
    'flute': ('wind.flutes.flute', 'Flute', ('G', 2), None),
    'oboe': ('wind.reed.oboe', 'Oboe', ('G', 2), None),
    'clarinet': ('wind.reed.clarinet.bflat', 'Clarinet in B-flat', ('G', 2), (-1, -2, 0)),
    'bass clarinet': ('wind.reed.clarinet.bass', 'Bass Clarinet', ('G', 2), (-1, -2, -1)),
    'bassoon': ('wind.reed.bassoon', 'Bassoon', ('F', 4), None),
    'soprano sax': ('wind.reed.saxophone.soprano', 'Soprano Saxophone', ('G', 2), (-1, -2, 0)),
    'alto sax': ('wind.reed.saxophone.alto', 'Alto Saxophone', ('G', 2), (-5, -9, 0)),
    'tenor sax': ('wind.reed.saxophone.tenor', 'Tenor Saxophone', ('G', 2), (-1, -2, -1)),
    'baritone sax': ('wind.reed.saxophone.baritone', 'Baritone Saxophone', ('G', 2), (-5, -9, -1)),
    'horn': ('brass.french-horn', 'Horn in F', ('G', 2), (-4, -7, 0)),
    'trumpet': ('brass.trumpet', 'Trumpet in B-flat', ('G', 2), (-1, -2, 0)),
    'trombone': ('brass.trombone', 'Trombone', ('F', 4), None),
    'bass trombone': ('brass.trombone.bass', 'Bass Trombone', ('F', 4), None),
    'tuba': ('brass.tuba', 'Tuba', ('F', 4), None),
}


def _semis(x):
    """Semitones from concert to written pitch for a <transpose> (diatonic, chromatic, octave-change)."""
    return -x[1] - 12 * x[2] if x else 0


def musescore_exe():
    cfg = config.get('muse') or {}
    for p in (cfg.get('musescore_exe'), shutil.which('MuseScore4'), shutil.which('mscore'),
              os.path.join(os.environ.get('ProgramFiles', ''), 'MuseScore 4', 'bin', 'MuseScore4.exe'),
              os.path.join(os.environ.get('ProgramFiles', ''), 'MuseScore Studio 4', 'bin', 'MuseScore4.exe')):
        if p and os.path.isfile(p):
            return p
    return None


def out_dir():
    d = (config.get('muse') or {}).get('out_dir') or os.path.join(config.DATA_DIR, 'muse')
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- MusicXML
class Score:
    """bpm, time signature (beats, beat_type), optional tempo_map [(beat, bpm), ...] for rubato."""

    def __init__(self, bpm=90, time_sig=(4, 4), tempo_map=None, key_fifths=0, title='Untitled'):
        self.bpm, self.ts, self.key, self.title = float(bpm), tuple(time_sig), int(key_fifths), title
        self.bar16 = int(self.ts[0] * 16 / self.ts[1])
        tm = sorted((int(round(b * DIV)), float(v)) for b, v in (tempo_map or []))
        self.marks = {t: v for t, v in tm}

    # tempo map -> seconds (for cutting clips on the shared timeline)
    def seconds(self, beat):
        t16, sec, bpm, last = int(round(beat * DIV)), 0.0, self.bpm, 0
        for at, v in sorted(self.marks.items()):
            if at >= t16:
                break
            sec += (at - last) / DIV * 60.0 / bpm
            bpm, last = v, at
        return sec + (t16 - last) / DIV * 60.0 / bpm

    def _cuts(self, t, d):
        """Cut a span at bar lines and tempo marks (so every mark sits on an event start)."""
        out = []
        while d > 0:
            b, off = divmod(t, self.bar16)
            nxt = [m - b * self.bar16 for m in self.marks if b * self.bar16 < m < (b + 1) * self.bar16 and m - b * self.bar16 > off]
            c = min(d, min(nxt + [self.bar16]) - off)
            out.append((b, off, c))
            t += c
            d -= c
        return out

    @staticmethod
    def _split(d):
        out = []
        for p in (16, 12, 8, 6, 4, 3, 2, 1):
            while d >= p:
                out.append(p)
                d -= p
        return out

    def _events(self, part, bars):
        notes = sorted((int(round(n['start'] * DIV)), max(1, int(round(n['length'] * DIV))), int(n['pitch']))
                       for n in part['notes'])
        merged = []
        for t, d, p in notes:                               # one line per part: trim overlaps
            if merged and t == merged[-1][0]:               # same start: keep the top note only
                if p > merged[-1][2]:
                    merged[-1] = (t, d, p)
                continue
            if merged and t < merged[-1][0] + merged[-1][1]:
                merged[-1] = (merged[-1][0], max(1, t - merged[-1][0]), merged[-1][2])
            merged.append((t, d, p))
        legato = part.get('legato', True)
        ev = [[] for _ in range(bars)]
        cur = 0
        for i, (t, d, p) in enumerate(merged):
            if t > cur:
                for b, off, c in self._cuts(cur, t - cur):
                    for piece in self._split(c):
                        ev[b].append(dict(off=off, dur=piece, midi=None))
                        off += piece
            gap_before = i == 0 or t - (merged[i - 1][0] + merged[i - 1][1]) > 0
            gap_after = i == len(merged) - 1 or merged[i + 1][0] - (t + d) > 0
            pieces = []
            for b, off, c in self._cuts(t, d):
                for piece in self._split(c):
                    pieces.append((b, off, piece))
                    off += piece
            for k, (b, off, piece) in enumerate(pieces):
                ev[b].append(dict(off=off, dur=piece, midi=p, tie_stop=k > 0, tie_start=k < len(pieces) - 1,
                                  slur_start=legato and gap_before and not gap_after and k == 0,
                                  slur_stop=legato and gap_after and not gap_before and k == len(pieces) - 1,
                                  phrase_start=gap_before and k == 0))
            cur = t + d
        if cur < bars * self.bar16:
            for b, off, c in self._cuts(cur, bars * self.bar16 - cur):
                for piece in self._split(c):
                    ev[b].append(dict(off=off, dur=piece, midi=None))
                    off += piece
        return ev

    def part_xml(self, part, bars):
        inst = INSTRUMENTS[part['instrument']]
        xp = inst[3]
        shift = _semis(xp)
        dyn = part.get('dynamic', 'mf')
        out = []
        for b, events in enumerate(self._events(part, bars)):
            out.append('<measure number="%d">' % (b + 1))
            if b == 0:
                tr = ('<transpose><diatonic>%d</diatonic><chromatic>%d</chromatic>%s</transpose>'
                      % (xp[0], xp[1], '<octave-change>%d</octave-change>' % xp[2] if xp[2] else '')) if xp else ''
                dk = (shift * 7) % 12                       # written key = concert key moved by the transposition
                dk = dk - 12 if dk > 6 else dk
                fifths = self.key + dk
                fifths = fifths - 12 if fifths > 7 else fifths + 12 if fifths < -7 else fifths
                out.append('<attributes><divisions>%d</divisions><key><fifths>%d</fifths></key><time><beats>%d'
                           '</beats><beat-type>%d</beat-type></time>%s<clef><sign>%s</sign><line>%d</line></clef>'
                           '</attributes>' % (DIV, fifths, self.ts[0], self.ts[1], tr, inst[2][0], inst[2][1]))
                out.append('<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>'
                           '<per-minute>%g</per-minute></metronome></direction-type><sound tempo="%g"/></direction>'
                           % (self.bpm, self.bpm))
            for e in sorted(events, key=lambda e: e['off']):
                at = b * self.bar16 + e['off']
                if at in self.marks and at:                 # invisible tempo change (rubato)
                    out.append('<direction placement="above"><direction-type><words></words></direction-type>'
                               '<sound tempo="%g"/></direction>' % self.marks[at])
                if e['midi'] is not None and e.get('phrase_start'):
                    out.append('<direction placement="below"><direction-type><dynamics><%s/></dynamics>'
                               '</direction-type></direction>' % (dyn if dyn in DYNAMICS else 'mf'))
                typ, dots = TYPES[e['dur']]
                if e['midi'] is None:
                    out.append('<note><rest/><duration>%d</duration><type>%s</type>%s</note>'
                               % (e['dur'], typ, '<dot/>' * dots))
                    continue
                w = e['midi'] + shift
                pitch = '<pitch><step>%s</step>%s<octave>%d</octave></pitch>' % (
                    STEP[w % 12], '<alter>1</alter>' if ALTER[w % 12] else '', w // 12 - 1)
                ties = ('<tie type="stop"/>' if e['tie_stop'] else '') + ('<tie type="start"/>' if e['tie_start'] else '')
                nots = ''.join(['<tied type="stop"/>' if e['tie_stop'] else '',
                                '<tied type="start"/>' if e['tie_start'] else '',
                                '<slur type="start" number="1"/>' if e['slur_start'] else '',
                                '<slur type="stop" number="1"/>' if e['slur_stop'] else ''])
                out.append('<note>%s<duration>%d</duration>%s<type>%s</type>%s%s</note>'
                           % (pitch, e['dur'], ties, typ, '<dot/>' * dots,
                              '<notations>%s</notations>' % nots if nots else ''))
            out.append('</measure>')
        return ''.join(out)

    def bars_needed(self, parts):
        end = max((int(round((n['start'] + n['length']) * DIV)) for p in parts for n in p['notes']), default=0)
        return max(1, -(-end // self.bar16))

    def xml(self, parts, bars=None):
        bars = bars or self.bars_needed(parts)
        pl = ''.join('<score-part id="P%d"><part-name>%s</part-name><score-instrument id="P%d-I1"><instrument-name>'
                     '%s</instrument-name><instrument-sound>%s</instrument-sound></score-instrument></score-part>'
                     % (i + 1, p.get('name') or INSTRUMENTS[p['instrument']][1], i + 1,
                        INSTRUMENTS[p['instrument']][1], INSTRUMENTS[p['instrument']][0]) for i, p in enumerate(parts))
        body = ''.join('<part id="P%d">%s</part>' % (i + 1, self.part_xml(p, bars)) for i, p in enumerate(parts))
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML '
                '4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">\n<score-partwise version="4.0"><work>'
                '<work-title>%s</work-title></work><part-list>%s</part-list>%s</score-partwise>\n'
                % (self.title.replace('&', 'and').replace('<', ''), pl, body))


# ---------------------------------------------------------------- render, cut, SFZ
def render(xml_path, audio_path, timeout=900):
    """MusicXML -> .mscz -> audio with Muse Sounds (MuseScore picks Muse Sounds for each instrument)."""
    exe = musescore_exe()
    if not exe:
        raise RuntimeError('MuseScore Studio 4 not found. It is optional: install it from musescore.org (and Muse '
                           'Sounds through Muse Hub), or set "muse": {"musescore_exe": ...} in config.json.')
    mscz = os.path.splitext(audio_path)[0] + '.mscz'
    for args in (['-o', mscz, xml_path], ['-o', audio_path, mscz, '--sound-profile', 'MuseSounds']):
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if r.returncode != 0 or not os.path.exists(args[1]):
            raise RuntimeError('MuseScore failed (%s): %s' % (r.returncode, (r.stderr or r.stdout)[-600:]))
    return audio_path


def _read_wav(path):
    """(fmt chunk bytes, sample rate, bytes per frame, audio bytes) for any PCM or float WAV (copied, not decoded)."""
    import struct
    data = open(path, 'rb').read()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('%s is not a WAV file' % path)
    pos, fmt, audio = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack('<I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            fmt = body
        elif cid == b'data':
            audio = body
        pos += 8 + size + (size & 1)
    if fmt is None or audio is None:
        raise ValueError('%s has no fmt/data chunk' % path)
    ch, sr = struct.unpack('<HI', fmt[2:8])
    block = struct.unpack('<H', fmt[12:14])[0]
    return fmt, sr, block or ch * 2, audio


def _write_wav(path, fmt, audio):
    import struct
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 4 + 8 + len(fmt) + 8 + len(audio)) + b'WAVE')
        f.write(b'fmt ' + struct.pack('<I', len(fmt)) + fmt)
        f.write(b'data' + struct.pack('<I', len(audio)) + audio)


def cut(wav_path, score, phrases, folder, prefix, release=0.5):
    """Cut phrase clips [(start_beat, end_beat), ...] out of a render on the shared timeline (works for 16/24-bit
    and float WAVs: bytes are copied as they are). Each clip starts on its phrase's first beat."""
    fmt, sr, frame, audio = _read_wav(wav_path)
    n = len(audio) // frame
    out = []
    for k, (b0, b1) in enumerate(phrases):
        a, b = int(score.seconds(b0) * sr), int((score.seconds(b1) + release) * sr)
        clip = audio[a * frame:min(n, b) * frame]
        name = '%s %d.wav' % (prefix, k + 1)
        _write_wav(os.path.join(folder, name), fmt, clip)
        out.append({'file': name, 'start_beat': b0, 'seconds': round(len(clip) / frame / sr, 2)})
    return out


def write_sfz(path, clips, first_key=60, release=0.4):
    """One region per clip on consecutive keys from first_key. Written aside then swapped in, because sfizz
    watches the file and would otherwise reload it half-written."""
    lines = ['// generated by the Claude FL Studio bridge (muse.py)', '<control>', 'default_path=./', '<global>',
             'loop_mode=no_loop', 'amp_veltrack=0', 'ampeg_attack=0', 'ampeg_release=%s' % release, 'trigger=attack']
    keys = []
    for i, c in enumerate(clips):
        key = first_key + i
        keys.append(dict(c, key=key))
        lines += ['', '<region>', 'sample=%s' % c['file'], 'key=%d' % key, 'pitch_keycenter=%d' % key,
                  'volume=%s' % c.get('volume_db', 0)]
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, path)
    return keys


def run(spec):
    """The fl_muse_render tool. spec: name, bpm, time_signature [4,4], tempo_map [[beat, bpm]...], key_fifths,
    parts [{instrument, name, dynamic, legato, notes [{pitch, start, length}], phrases [[start, end]...]}],
    mode 'stems' (one WAV per part from bar 1) or 'clips' (cut phrases + SFZ per part)."""
    name = spec.get('name') or 'Muse render'
    score = Score(spec.get('bpm', 90), spec.get('time_signature', (4, 4)), spec.get('tempo_map'),
                  spec.get('key_fifths', 0), name)
    parts = spec['parts']
    for p in parts:
        if p['instrument'] not in INSTRUMENTS:
            raise ValueError('unknown instrument %r; known: %s' % (p['instrument'], ', '.join(INSTRUMENTS)))
    bars = score.bars_needed(parts)
    folder = os.path.join(spec.get('out_dir') or out_dir(), ''.join(c for c in name if c not in '<>:"/\\|?*'))
    os.makedirs(folder, exist_ok=True)
    result = {'folder': folder, 'bars': bars, 'parts': []}
    for p in parts:
        label = ''.join(c for c in (p.get('name') or INSTRUMENTS[p['instrument']][1]) if c not in '<>:"/\\|?*').strip() or 'part'
        xml = os.path.join(folder, label + '.musicxml')
        with open(xml, 'w', encoding='utf-8') as f:
            f.write(score.xml([p], bars))
        wav = render(xml, os.path.join(folder, label + '.wav'))
        entry = {'part': label, 'musicxml': xml, 'wav': wav}
        if spec.get('mode', 'stems') == 'clips':
            phrases = p.get('phrases') or [[0, bars * score.bar16 / DIV]]
            clips = cut(wav, score, phrases, folder, label)
            entry['sfz'] = os.path.join(folder, label + '.sfz')
            entry['clips'] = write_sfz(entry['sfz'], clips)
        result['parts'].append(entry)
    result['how_to_use'] = ('stems: put each WAV on its own playlist track at bar 1 (fl_browser_load with '
                            'where=playlist_audio_track, view scrolled to bar 1). clips: load sfizz on a channel, '
                            'open its SFZ with fl_file_dialog, then trigger each clip with its key (clips[].key) '
                            'at its start_beat in the piano roll.')
    with open(os.path.join(folder, 'render.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=1)
    return result
