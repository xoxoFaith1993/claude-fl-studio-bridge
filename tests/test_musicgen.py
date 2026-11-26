"""Offline test of the local copilot's theory fixer (no Ollama needed): a deliberately messy idea in, clean notes out.

  python tests\\test_musicgen.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
import musicgen  # noqa: E402

messy = {
    'title': 'Test', 'bpm': 72, 'key': 'A minor', 'bars': 4,
    'chords': ['Am', 'F#', 'C', 'E'],                       # F# is not in A minor -> replaced
    'melody': [[{'note': 'A#4', 'beats': 2}, {'note': 'C5', 'beats': 2}],   # A# is out of key
               [{'note': 'F5', 'beats': 1}, {'note': 'G5', 'beats': 1}, {'note': 'A5', 'beats': 2}],
               [{'note': 'E5', 'beats': 4}],
               [{'note': 'D#5', 'beats': 3}, {'note': 'A4', 'beats': 1}]],   # D# rubs against E chord
}
song = musicgen.arrange(messy)
assert song['bars'] == 4 and song['length_beats'] == 16
assert musicgen.check(song['notes']) == 0, 'semitone clashes left'
assert any('not in key' in f for f in song['fixes']), song['fixes']
assert all(0 <= n['pitch'] <= 127 and n['length'] > 0 for n in song['notes'])
assert musicgen.parse_key('F# minor') == (6, True) and musicgen.note_num('C4') == 60
print('all musicgen tests passed (%d notes, %d fixes)' % (len(song['notes']), len(song['fixes'])))
