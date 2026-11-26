"""Tests the whole protocol without FL Studio or MIDI ports.

Loads the real FL script with fake FL modules and wires it straight to the bridge.
Run: python tests/test_loopback.py
"""

import importlib.util
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
# FL's real midi.py (constants only) if FL is installed; otherwise a stand-in, so no FL is needed
_FL_LIB = os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Image-Line', 'Shared', 'Python', 'Lib')
if os.path.isdir(_FL_LIB) and not os.environ.get('FL_TEST_NO_FL'):
    sys.path.insert(0, _FL_LIB)
try:
    import midi  # noqa: F401,E402
except ImportError:
    _midi = types.ModuleType('midi')
    _midi.__getattr__ = lambda name: sum(ord(c) for c in name) if name.isupper() or '_' in name else (_ for _ in ()).throw(AttributeError(name))
    sys.modules['midi'] = _midi

import server  # noqa: E402

# --- fake FL modules -------------------------------------------------------
state = {'playing': False, 'tempo': 140000, 'loop': 0, 'grid': {}, 'vol': {i: 0.8 for i in range(127)}, 'mute': set()}
sent_to_bridge = []

device = types.ModuleType('device')
device.midiOutSysex = lambda data: server.bridge._on_sysex(bytes(data))
device.getPortNumber = lambda: 10
transport = types.ModuleType('transport')
transport.isPlaying = lambda: state['playing']
transport.isRecording = lambda: False
transport.start = lambda: state.__setitem__('playing', not state['playing'])
transport.stop = lambda: state.__setitem__('playing', False)
transport.getLoopMode = lambda: state['loop']
transport.setLoopMode = lambda: state.__setitem__('loop', 1 - state['loop'])
transport.getSongPos = lambda: 0.0
transport.getSongLength = lambda mode: 8
mixer = types.ModuleType('mixer')
mixer.getCurrentTempo = lambda: state['tempo']
mixer.trackCount = lambda: 127
mixer.trackNumber = lambda: 0
mixer.getTrackName = lambda i: 'Master' if i == 0 else 'Insert %d' % i
mixer.getTrackVolume = lambda i: state['vol'][i]
mixer.setTrackVolume = lambda i, v: state['vol'].__setitem__(i, v)
mixer.getTrackPan = lambda i: 0.0
mixer.isTrackMuted = lambda i: i in state['mute']
mixer.muteTrack = lambda i: state['mute'].symmetric_difference_update({i})
mixer.isTrackSolo = lambda i: False
mixer.isTrackArmed = lambda i: False
mixer.getTrackColor = lambda i: 0x5F7581
channels = types.ModuleType('channels')
channels.channelCount = lambda: 2
channels.channelNumber = lambda: 0
channels.getChannelName = lambda i: ['Kick', 'Snäre'][i]
channels.getGridBit = lambda c, p: state['grid'].get((c, p), 0)
channels.setGridBit = lambda c, p, v: state['grid'].__setitem__((c, p), v)
patterns = types.ModuleType('patterns')
patterns.patternNumber = lambda: 1
patterns.patternCount = lambda: 1
general = types.ModuleType('general')
general.getVersion = lambda: 2


def _rec(ev, val, flags):
    import midi
    if ev == midi.REC_Tempo:
        state['tempo'] = val


general.processRECEvent = _rec
ui = types.ModuleType('ui')
ui.isMetronomeEnabled = lambda: False
playlist = types.ModuleType('playlist')
for m in (device, transport, mixer, channels, patterns, general, ui, playlist):
    sys.modules[m.__name__] = m

spec = importlib.util.spec_from_file_location('flscript', os.path.join(ROOT, 'fl_script', 'device_ClaudeBridge.py'))
flscript = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flscript)


class FakeOut:
    def send_sysex(self, data):
        ev = types.SimpleNamespace(sysex=bytes(data), handled=False)
        flscript.OnSysEx(ev)
        assert ev.handled


server.bridge.out = FakeOut()
server.bridge.inp = object()


def call(name, **args):
    replies = []
    orig = server.send
    server.send = replies.append
    try:
        server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
    finally:
        server.send = orig
    res = replies[0]['result']
    text = res['content'][0]['text']
    if res['isError']:
        raise AssertionError(name + ' failed: ' + text)
    return json.loads(text)


st = call('fl_status')
assert st['tempo_bpm'] == 140.0 and st['playing'] is False, st
assert call('fl_transport', action='play')['playing'] is True
assert call('fl_transport', action='play')['playing'] is True  # play twice stays playing
assert call('fl_transport', action='none', loop_mode='song')['loop_mode'] == 'song'
assert call('fl_set_tempo', bpm=92.5)['tempo_bpm'] == 92.5
assert call('fl_mixer_set', track=3, volume=0.5, mute=True)['muted'] is True
assert call('fl_mixer_set', track=3, mute=True)['muted'] is True  # already muted stays muted
m = call('fl_mixer_get', start=0, count=5)
assert m['tracks'][3]['volume'] == 0.5 and m['tracks'][0]['color'] == '#5F7581', m
assert call('fl_steps', channel=0, steps='x---x---x---x---')['steps'] == 'x---x---x---x---'
assert call('fl_exec', code='channels.getChannelName(1)') == 'Snäre'
assert call('fl_exec', code='result = sum(range(5000))\nprint("hi")') == {'result': 12497500, 'printed': 'hi'}
assert call('fl_exec', code='"long " * 3000').startswith('long long')  # multi-chunk reply
big = 'x = ' + repr('y' * 5000) + '\nresult = len(x)'
assert call('fl_exec', code=big) == 5000  # multi-chunk command
assert 'start' in call('fl_api', module='transport')
mid = call('fl_write_midi', notes=[{'pitch': 60, 'start': 0, 'length': 1}, {'pitch': 64, 'start': 1, 'length': 0.5}],
           filename='test_loopback.mid')
assert os.path.getsize(mid['path']) > 30
os.remove(mid['path'])

# error comes back as an MCP error result, not a crash
replies = []
server.send = replies.append
server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'fl_exec', 'arguments': {'code': '1/0'}}})
assert replies[0]['result']['isError'] and 'ZeroDivisionError' in replies[0]['result']['content'][0]['text']
print('all loopback tests passed')
