"""Offline test of the Muse Sounds module: MusicXML writing, tempo maps, clip cutting and SFZ (no MuseScore needed).

  python tests\\test_muse.py
"""
import json
import os
import sys
import tempfile
import wave
import xml.dom.minidom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import muse  # noqa: E402

spec = json.load(open(os.path.join(ROOT, 'examples', 'muse_chorale.json'), encoding='utf-8'))
score = muse.Score(spec['bpm'], spec['time_signature'], [[8, 60]], 0, spec['name'])
assert score.bars_needed(spec['parts']) == 4
x = score.xml(spec['parts'])
xml.dom.minidom.parseString(x.encode('utf-8'))                       # well-formed
assert x.count('<measure ') == 12 and 'strings.cello' in x and '<sound tempo="60"/>' in x

# legato line with a bar-crossing note: slurs + ties, durations per bar add up
line = {'instrument': 'clarinet', 'notes': [{'pitch': 62, 'start': 0, 'length': 1}, {'pitch': 64, 'start': 1, 'length': 5},
                                            {'pitch': 65, 'start': 6, 'length': 2}]}
s2 = muse.Score(90, (4, 4))
x2 = s2.xml([line])
d = xml.dom.minidom.parseString(x2.encode('utf-8'))
assert '<slur type="start"' in x2 and '<slur type="stop"' in x2 and '<tie type="start"/>' in x2
for m in d.getElementsByTagName('measure'):
    assert sum(int(n.getElementsByTagName('duration')[0].firstChild.data) for n in m.getElementsByTagName('note')) == 16
assert '<step>E</step><octave>4</octave>' in x2 and '<chromatic>-2</chromatic>' in x2   # D4 concert -> E4 written

# tempo map -> seconds
assert abs(score.seconds(8) - 8 * 60 / 72) < 1e-9 and abs(score.seconds(12) - (8 * 60 / 72 + 4 * 60 / 60)) < 1e-9

# cut + sfz on a fake 16-bit render
tmp = tempfile.mkdtemp()
wav = os.path.join(tmp, 'r.wav')
with wave.open(wav, 'wb') as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(b'\x00\x00' * 2 * 8000 * 20)
clips = muse.cut(wav, score, [(0, 4), (8, 12)], tmp, 'Violin')
assert len(clips) == 2 and abs(clips[0]['seconds'] - (4 * 60 / 72 + 0.5)) < 0.01
keys = muse.write_sfz(os.path.join(tmp, 'v.sfz'), clips)
sfz = open(os.path.join(tmp, 'v.sfz')).read()
assert keys[1]['key'] == 61 and 'sample=Violin 2.wav' in sfz
print('all muse tests passed')
