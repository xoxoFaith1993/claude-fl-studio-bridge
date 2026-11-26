"""FL Studio MCP server: gives Claude tools to control FL Studio through the Claude Bridge script.

Speaks MCP (JSON-RPC 2.0, one JSON object per line) over stdin/stdout. No packages needed.
"""

import contextlib
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from bridge import Bridge, BridgeError  # noqa: E402
import browser  # noqa: E402
import pianoroll  # noqa: E402
import bbcso  # noqa: E402
import kits  # noqa: E402
import flhands  # noqa: E402
import ollama_eyes  # noqa: E402
import playlist_place  # noqa: E402
import automation_clip  # noqa: E402
import render  # noqa: E402
import knobtable  # noqa: E402
import overlay  # noqa: E402

# tools that only read or play: no 'hands off' banner for these
QUIET = {'fl_status', 'fl_doctor', 'fl_reference', 'fl_muse_render', 'fl_mixer_get', 'fl_channels_get', 'fl_patterns_get', 'fl_browser_list', 'fl_bbcso_list',
         'fl_plugin_params', 'fl_mixer_effects', 'fl_windows', 'fl_snapshot', 'fl_api', 'fl_write_midi',
         'fl_read_window', 'fl_transport'}

PROTOCOL_VERSION = '2025-06-18'
VERSION = '0.1.0'
MIDI_DIR = config.get('midi_dir')

bridge = Bridge()


def fl(body, **args):
    """Run a code body inside FL with ARGS available. Returns the value of `result`."""
    code = 'ARGS = %s\n%s' % (ascii(args), body) if args else body
    try:
        value, output = bridge.run(code)
    except BridgeError as e:
        # a modal FL dialog blocks the script; say which one so it can be answered with the window tools
        try:
            ds = flhands.dialogs()
        except Exception:
            ds = []
        if ds and 'No reply' in str(e):
            msg = 'FL is blocked by a dialog: %s. Use fl_windows / fl_snapshot / fl_click / fl_key to answer it.' % (
                ', '.join('"%s" (%s, hwnd %s)' % (d['title'], d['class'], d['hwnd']) for d in ds))
            import doctor
            seen = doctor.read_dialog(ds[0])          # optional local vision model says what the popup is
            raise BridgeError(msg + (' The local model reads it as: ' + seen if seen else ''))
        raise BridgeError(str(e) + '\n\nRun fl_doctor for a step-by-step check of the setup.')
    return value if not output else {'result': value, 'printed': output}


# ---------------------------------------------------------------- FL-side code

HELPERS = r'''
def _try(f, *a):
    try:
        return f(*a)
    except Exception as e:
        return None
def _hex(c):
    return None if c is None else '#%06X' % (c & 0xFFFFFF)
def _toggle_to(mod, is_name, toggle_name, i, want):
    if want is not None and bool(getattr(mod, is_name)(i)) != bool(want):
        getattr(mod, toggle_name)(i)
'''

STATUS = HELPERS + r'''
t = mixer.getCurrentTempo()
result = {
    'api_version': _try(general.getVersion),
    'playing': _try(transport.isPlaying),
    'recording': _try(transport.isRecording),
    'tempo_bpm': (t / 1000.0 if t and t > 1000 else t),
    'loop_mode': {0: 'pattern', 1: 'song'}.get(_try(transport.getLoopMode)),
    'song_position_0_to_1': _try(transport.getSongPos),
    'song_length_bars': _try(transport.getSongLength, midi.SONGLENGTH_BARS),
    'current_pattern': _try(patterns.patternNumber),
    'pattern_count': _try(patterns.patternCount),
    'channel_count': _try(channels.channelCount),
    'selected_channel': _try(channels.channelNumber),
    'mixer_track_count': _try(mixer.trackCount),
    'selected_mixer_track': _try(mixer.trackNumber),
    'metronome_on': _try(ui.isMetronomeEnabled),
    'modules_missing': [n for n in ('plugins', 'arrangement') if globals().get(n) is None],
}
'''

TRANSPORT = r'''
a = ARGS['action']
if a == 'play':
    if not transport.isPlaying():
        transport.start()
elif a == 'pause':
    if transport.isPlaying():
        transport.start()
elif a == 'stop':
    transport.stop()
elif a == 'toggle_play':
    transport.start()
elif a == 'record':
    transport.record()
if ARGS.get('loop_mode') is not None:
    want = 1 if ARGS['loop_mode'] == 'song' else 0
    if transport.getLoopMode() != want:
        transport.setLoopMode()
if ARGS.get('position') is not None:
    transport.setSongPos(ARGS['position'])
result = {'playing': transport.isPlaying(), 'recording': transport.isRecording(),
          'loop_mode': 'song' if transport.getLoopMode() else 'pattern'}
'''

SET_TEMPO = r'''
if hasattr(mixer, 'setCurrentTempo'):
    mixer.setCurrentTempo(int(round(ARGS['bpm'] * 1000)), False)
else:
    general.processRECEvent(midi.REC_Tempo, int(round(ARGS['bpm'] * 1000)), midi.REC_Control | midi.REC_UpdateControl)
t = mixer.getCurrentTempo()
result = {'tempo_bpm': t / 1000.0 if t > 1000 else t}
'''

MIXER_GET = HELPERS + r'''
start = ARGS['start']
end = min(mixer.trackCount(), start + ARGS['count'])
rows = []
for i in range(start, end):
    rows.append({
        'track': i,
        'name': _try(mixer.getTrackName, i),
        'volume': _try(mixer.getTrackVolume, i),
        'pan': _try(mixer.getTrackPan, i),
        'muted': _try(mixer.isTrackMuted, i),
        'solo': _try(mixer.isTrackSolo, i),
        'armed': _try(mixer.isTrackArmed, i),
        'color': _hex(_try(mixer.getTrackColor, i)),
    })
result = {'track_count': mixer.trackCount(), 'tracks': rows,
          'note': 'volume is 0..1 where 0.8 = 0 dB; pan is -1 (left) .. 1 (right); track 0 is Master'}
'''

MIXER_SET = HELPERS + r'''
i = ARGS['track']
if ARGS.get('name') is not None: mixer.setTrackName(i, ARGS['name'])
if ARGS.get('volume') is not None: mixer.setTrackVolume(i, ARGS['volume'])
if ARGS.get('pan') is not None: mixer.setTrackPan(i, ARGS['pan'])
if ARGS.get('color') is not None: mixer.setTrackColor(i, int(ARGS['color'].lstrip('#'), 16))
_toggle_to(mixer, 'isTrackMuted', 'muteTrack', i, ARGS.get('mute'))
_toggle_to(mixer, 'isTrackSolo', 'soloTrack', i, ARGS.get('solo'))
_toggle_to(mixer, 'isTrackArmed', 'armTrack', i, ARGS.get('arm'))
if ARGS.get('select'):
    mixer.setTrackNumber(i)
result = {'track': i, 'name': mixer.getTrackName(i), 'volume': mixer.getTrackVolume(i),
          'pan': mixer.getTrackPan(i), 'muted': mixer.isTrackMuted(i), 'solo': _try(mixer.isTrackSolo, i)}
'''

CHANNELS_GET = HELPERS + r'''
rows = []
for i in range(channels.channelCount()):
    rows.append({
        'channel': i,
        'name': _try(channels.getChannelName, i),
        'volume': _try(channels.getChannelVolume, i),
        'pan': _try(channels.getChannelPan, i),
        'muted': _try(channels.isChannelMuted, i),
        'solo': _try(channels.isChannelSolo, i),
        'selected': _try(channels.isChannelSelected, i),
        'mixer_track': _try(channels.getTargetFxTrack, i),
        'color': _hex(_try(channels.getChannelColor, i)),
    })
result = {'channels': rows, 'note': 'volume is 0..1 (0.78 is the default); pan -1..1'}
'''

CHANNEL_SET = HELPERS + r'''
i = ARGS['channel']
if ARGS.get('name') is not None: channels.setChannelName(i, ARGS['name'])
if ARGS.get('volume') is not None: channels.setChannelVolume(i, ARGS['volume'])
if ARGS.get('pan') is not None: channels.setChannelPan(i, ARGS['pan'])
if ARGS.get('color') is not None: channels.setChannelColor(i, int(ARGS['color'].lstrip('#'), 16))
if ARGS.get('mixer_track') is not None:
    if hasattr(channels, 'setTargetFxTrack'):
        channels.setTargetFxTrack(i, ARGS['mixer_track'])
    else:
        general.processRECEvent(channels.getRecEventId(i) + midi.REC_Chan_FXTrack, ARGS['mixer_track'],
                                midi.REC_Control | midi.REC_UpdateControl)
_toggle_to(channels, 'isChannelMuted', 'muteChannel', i, ARGS.get('mute'))
_toggle_to(channels, 'isChannelSolo', 'soloChannel', i, ARGS.get('solo'))
if ARGS.get('select'):
    channels.deselectAll()
    channels.selectChannel(i, 1)
result = {'channel': i, 'name': channels.getChannelName(i), 'volume': channels.getChannelVolume(i),
          'pan': channels.getChannelPan(i), 'muted': channels.isChannelMuted(i),
          'mixer_track': _try(channels.getTargetFxTrack, i)}
'''

STEPS = r'''
i = ARGS['channel']
if ARGS.get('pattern') is not None:
    patterns.jumpToPattern(ARGS['pattern'])
steps = ARGS.get('steps')
if steps is not None:
    for pos in range(len(steps)):
        channels.setGridBit(i, pos, 1 if steps[pos] else 0)
n = ARGS.get('length') or (len(steps) if steps is not None else 16)
grid = ''.join('x' if channels.getGridBit(i, p) else '-' for p in range(n))
result = {'pattern': patterns.patternNumber(), 'channel': i, 'name': channels.getChannelName(i), 'steps': grid}
'''

PATTERNS_GET = HELPERS + r'''
rows = []
for p in range(1, max(patterns.patternCount(), patterns.patternNumber()) + 1):
    rows.append({'pattern': p, 'name': _try(patterns.getPatternName, p),
                 'length_beats': _try(patterns.getPatternLength, p),
                 'color': _hex(_try(patterns.getPatternColor, p))})
result = {'current': patterns.patternNumber(), 'patterns': rows}
'''

PATTERN_SET = r'''
p = ARGS['pattern']
patterns.jumpToPattern(p)
if ARGS.get('name') is not None: patterns.setPatternName(p, ARGS['name'])
if ARGS.get('color') is not None: patterns.setPatternColor(p, int(ARGS['color'].lstrip('#'), 16))
result = {'current': patterns.patternNumber(), 'name': patterns.getPatternName(p)}
'''

PLUGIN_BASE = HELPERS + r'''
idx, slot = ARGS['index'], ARGS['slot']
if slot >= 0:
    if not mixer.isTrackPluginValid(idx, slot):
        raise RuntimeError('No effect in mixer track %s slot %s' % (idx, slot))
    _base = midi.REC_Plug_First + mixer.getTrackPluginId(idx, slot) + midi.REC_PluginBase
else:
    _base = channels.getRecEventId(idx) + midi.REC_Chan_Plugin_First
def _pname(p):
    n = _try(mixer.getEventIDName, _base + p) or ''
    return '' if 'Unknown event' in n else n
def _pval(p):
    return mixer.getEventValue(_base + p) / float(midi.FromMIDI_Max)
def _pstr(p):
    return _try(mixer.getEventIDValueString, _base + p, mixer.getEventValue(_base + p))
'''

PLUGINS_MOD = r'''
idx, slot = ARGS['index'], ARGS['slot']
if not plugins.isValid(idx, slot):
    raise RuntimeError('No plugin there (index %s, slot %s)' % (idx, slot))
def _pname(p):
    return (plugins.getParamName(p, idx, slot) or '').strip()
def _pval(p):
    return plugins.getParamValue(p, idx, slot)
def _pstr(p):
    try:
        return plugins.getParamValueString(p, idx, slot)
    except Exception:
        return None
'''

PLUGIN_PARAMS_V2 = PLUGINS_MOD + r'''
flt = (ARGS.get('filter') or '').lower()
rows, skipped = [], 0
count = plugins.getParamCount(idx, slot)
for p in range(count):
    name = _pname(p)
    if not name or (flt and flt not in name.lower()):
        continue
    if skipped < ARGS['offset']:
        skipped += 1
        continue
    rows.append({'param': p, 'name': name, 'value': round(_pval(p), 4), 'display': _pstr(p)})
    if len(rows) >= ARGS['limit']:
        break
result = {'plugin': plugins.getPluginName(idx, slot), 'param_count': count, 'params': rows,
          'more_may_exist': len(rows) >= ARGS['limit'], 'note': 'values are 0..1'}
'''

PLUGIN_SET_V2 = PLUGINS_MOD + r'''
p = ARGS['param']
plugins.setParamValue(max(0.0, min(1.0, ARGS['value'])), p, idx, slot)
result = {'plugin': plugins.getPluginName(idx, slot), 'param': p, 'name': _pname(p),
          'value': round(_pval(p), 4), 'display': _pstr(p)}
'''

PLUGIN_PRESET = PLUGINS_MOD + r'''
if ARGS['direction'] == 'next':
    plugins.nextPreset(idx, slot)
else:
    plugins.prevPreset(idx, slot)
result = {'plugin': plugins.getPluginName(idx, slot), 'name': plugins.getName(idx, slot),
          'preset_count': plugins.getPresetCount(idx, slot)}
'''

PLUGIN_PARAMS = PLUGIN_BASE + r'''
flt = (ARGS.get('filter') or '').lower()
rows, skipped, misses, p = [], 0, 0, 0
while p < 4096 and misses < 128 and len(rows) < ARGS['limit']:
    name = _pname(p)
    if not name:
        misses += 1
    else:
        misses = 0
        if not flt or flt in name.lower():
            if skipped < ARGS['offset']:
                skipped += 1
            else:
                rows.append({'param': p, 'name': name, 'value': round(_pval(p), 4), 'display': _pstr(p)})
    p += 1
result = {'params': rows, 'more_may_exist': len(rows) >= ARGS['limit'], 'note': 'values are 0..1'}
'''

PLUGIN_SET = PLUGIN_BASE + r'''
p = ARGS['param']
if not _pname(p):
    raise RuntimeError('No parameter %s on that plugin' % p)
general.processRECEvent(_base + p, int(round(max(0.0, min(1.0, ARGS['value'])) * midi.FromMIDI_Max)),
                        midi.REC_MIDIController | midi.REC_UpdateControl)
result = {'param': p, 'name': _pname(p), 'value': round(_pval(p), 4), 'display': _pstr(p)}
'''

MIXER_FX = HELPERS + r'''
rows = []
for t in ARGS['tracks']:
    for slot in range(10):
        if plugins is not None and t < mixer.trackCount() and _try(plugins.isValid, t, slot):
            rows.append({'track': t, 'track_name': mixer.getTrackName(t), 'slot': slot,
                         'plugin': plugins.getPluginName(t, slot)})
        elif plugins is None and _try(mixer.isTrackPluginValid, t, slot):
            eid = midi.REC_Plug_First + mixer.getTrackPluginId(t, slot) + midi.REC_PluginBase
            n = _try(mixer.getEventIDName, eid) or ''
            rows.append({'track': t, 'track_name': mixer.getTrackName(t), 'slot': slot,
                         'plugin': n.split(' - ')[0] if ' - ' in n else n})
result = rows
'''

SAVE = r'''
transport.globalTransport(midi.FPT_SaveNew if ARGS['new_version'] else midi.FPT_Save, 1)
result = {'title': ui.getProgTitle(), 'project_title': general.getProjectTitle()}
'''

WINDOWS = {'playlist': 'widPlaylist', 'channel_rack': 'widChannelRack', 'piano_roll': 'widPianoRoll',
           'mixer': 'widMixer', 'browser': 'widBrowser'}

SHOW_WINDOW = r'''
w = getattr(midi, ARGS['wid'])
if ARGS['show']:
    ui.showWindow(w)
    ui.setFocused(w)
else:
    ui.hideWindow(w)
result = {'visible': ui.getVisible(w)}
'''

API_LIST = r'''
m = globals().get(ARGS['module'])
if m is None:
    raise RuntimeError('No module ' + ARGS['module'])
result = sorted(n for n in dir(m) if not n.startswith('_'))
'''


# ---------------------------------------------------------------- tool handlers

_has_plugins = None


def has_plugins():
    global _has_plugins
    if _has_plugins is None:
        _has_plugins = bool(fl('result = plugins is not None and hasattr(plugins, "getParamName")'))
    return _has_plugins


def plugin_params(a):
    idx, slot = plugin_target(a)
    code = PLUGIN_PARAMS_V2 if has_plugins() else PLUGIN_PARAMS
    return fl(code, index=idx, slot=slot, filter=a.get('filter'), offset=a.get('offset', 0), limit=a.get('limit', 60))


def plugin_set(a):
    idx, slot = plugin_target(a)
    code = PLUGIN_SET_V2 if has_plugins() else PLUGIN_SET
    return fl(code, index=idx, slot=slot, param=a['param'], value=a['value'])

def plugin_target(a):
    if a.get('mixer_track') is not None:
        return a['mixer_track'], a.get('slot', 0)
    return a.get('channel', 0), -1


def parse_steps(steps):
    if isinstance(steps, str):
        return [0 if c in '-._0 ' else 1 for c in steps.replace('|', '')]
    return [1 if s else 0 for s in steps]


def play_notes(a):
    notes = a['notes']
    ch = a.get('midi_channel', 0)
    if a.get('channel') is not None:
        fl('channels.deselectAll()\nchannels.selectChannel(ARGS["c"], 1)\nresult = True', c=a['channel'])
    events = []
    for n in notes:
        start = n.get('start_ms', 0) / 1000.0
        dur = n.get('duration_ms', 250) / 1000.0
        events.append((start, 1, n['note'], n.get('velocity', 100)))
        events.append((start + dur, 0, n['note'], 0))
    events.sort(key=lambda e: (e[0], e[1]))
    if events and events[-1][0] > 60:
        raise BridgeError('Note sequence is longer than 60 seconds; use fl_write_midi for long parts.')
    t0 = time.time()
    try:
        for when, on, note, vel in events:
            delay = t0 + when - time.time()
            if delay > 0:
                time.sleep(delay)
            bridge.send_note(ch, note, vel, on=bool(on))
    finally:
        for n in notes:
            bridge.send_note(ch, n['note'], 0, on=False)
    return {'played_notes': len(notes)}


def write_midi(a):
    ppq = 96
    bpm = a.get('bpm', 120)
    events = []
    for n in a['notes']:
        start = int(round(n['start'] * ppq))
        end = start + max(1, int(round(n.get('length', 1) * ppq)))
        ch = n.get('midi_channel', 0) & 0x0F
        events.append((start, 1, bytes([0x90 | ch, n['pitch'] & 0x7F, n.get('velocity', 100) & 0x7F])))
        events.append((end, 0, bytes([0x80 | ch, n['pitch'] & 0x7F, 0])))
    events.sort(key=lambda e: (e[0], e[1]))

    def vlq(v):
        out = [v & 0x7F]
        v >>= 7
        while v:
            out.insert(0, (v & 0x7F) | 0x80)
            v >>= 7
        return bytes(out)

    tempo = int(60000000 / bpm)
    track = b'\x00\xff\x51\x03' + tempo.to_bytes(3, 'big')
    name = a.get('track_name', 'Claude').encode('ascii', 'replace')[:60]
    track += b'\x00\xff\x03' + vlq(len(name)) + name
    last = 0
    for tick, _, msg in events:
        track += vlq(tick - last) + msg
        last = tick
    track += b'\x00\xff\x2f\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, ppq) + b'MTrk' + struct.pack('>I', len(track)) + track
    os.makedirs(MIDI_DIR, exist_ok=True)
    fname = a.get('filename') or 'claude_%s.mid' % time.strftime('%Y%m%d_%H%M%S')
    if not fname.lower().endswith('.mid'):
        fname += '.mid'
    path = os.path.join(MIDI_DIR, os.path.basename(fname))
    with open(path, 'wb') as f:
        f.write(data)
    return {'path': path, 'notes': len(a['notes']),
            'how_to_use': 'Drag the file onto a channel or into the piano roll in FL Studio.'}


# ---------------------------------------------------------------- hands-off window tools (no screen control)

# "What would you like to record?" buttons, as y positions in a dialog of record_dialog_height px (FL 2026)
RECORD_CHOICES = config.cal('record_dialog_rows')
RECORD_DIALOG_H = float(config.cal('record_dialog_height'))


def transport(a):
    if a.get('action') != 'record':
        return fl(TRANSPORT, **a)
    try:
        bridge.run('transport.record()\nresult = 1', timeout=2.5)
    except BridgeError:
        pass
    d = None
    for _ in range(10):
        d = flhands.find('Recording', kind='dialog')
        if d:
            break
        time.sleep(0.1)
    if d:
        w, h = d['rect'][2] - d['rect'][0], d['rect'][3] - d['rect'][1]
        y = RECORD_CHOICES[a.get('record_what', 'notes_automation')] / RECORD_DIALOG_H * h
        flhands.click(d, w / 2, y)
        time.sleep(0.8)
    rest = dict(a, action='none')
    rest.pop('record_what', None)
    return fl(TRANSPORT, **rest)


def target_arg(a):
    t = a.get('target')
    if t is None:
        return 'main'
    return int(t) if isinstance(t, (int, float)) or str(t).isdigit() else t


def snapshot(a):
    crop = a.get('crop')
    info = flhands.snap(target_arg(a), crop=tuple(crop) if crop else None, scale=int(a.get('scale') or 1))
    with open(info['path'], 'rb') as f:
        import base64
        info['_image'] = base64.b64encode(f.read()).decode('ascii')
    return info


def fl_key(a):
    t = target_arg(a)
    if a.get('text'):
        flhands.type_text(t, a['text'])
    if a.get('key'):
        flhands.key(t, a['key'])
    return {'ok': True}


def project(a):
    act = a['action']
    if act == 'new':
        path = flhands.new_project(a['name'], a.get('template') or 'Empty',
                                   a.get('folder'))
        r = flhands.open_project(fl, path, a.get('unsaved', 'refuse'))
        if r.get('bridge'):                      # templates open in pattern mode: songs need song mode
            r['song_mode'] = flhands.song_mode(fl) == 1
            fl('transport.globalTransport(midi.FPT_Save, 1)\nresult = 1')
        return r
    if act == 'open':
        return flhands.open_project(fl, a['path'], a.get('unsaved', 'refuse'))
    if act == 'close':
        return {'closed': flhands.close_fl(fl, a.get('unsaved', 'refuse'))}
    if act == 'start':
        ok = flhands.launch(a.get('path'))
        return {'started': ok, 'bridge': flhands.wait_bridge(fl) if ok else False}
    if act == 'templates':
        return sorted(os.path.splitext(f)[0] for _, _, fs in os.walk(flhands.TEMPLATES) for f in fs
                      if f.endswith('.flp'))
    raise ValueError('action must be new, open, close, start or templates')


def automation(a):
    act = a['action']
    if act == 'create_volume':
        return automation_clip.create_volume_clip(fl, a.get('mixer_track', 0))
    if act == 'create_pan':
        return automation_clip.create_pan_clip(fl, a.get('mixer_track', 0))
    if act == 'sidechain':
        keys = ('bars', 'duck_db', 'every_beats', 'release_beats', 'start_bar', 'offset_beats', 'hold_beats',
                'attack_beats', 'top_db', 'release_curve', 'hits')
        rows = automation_clip.pump(fl, a['track'], **{k: a[k] for k in keys if a.get(k) is not None})
        return {'points': [{'kind': k, 'beat': b, 'db': d} for k, b, d in rows]}
    clip = automation_clip.AutoClip(fl, a['track'])
    if act == 'add_point':
        return {'db': clip.add(a['beat'], a['db'])}
    if act == 'set_mode':
        automation_clip.set_mode(clip, a['beat'], clip.y(automation_clip.db_to_value(a['db'])), a['mode'])
        return {'ok': True}
    raise ValueError('unknown action')


def reference(a):
    """Optional Reference Ears: run tools/reference_ears/refears.py in its own Python environment."""
    import base64
    import subprocess
    re_cfg = config.get('reference_ears') or {}
    py = re_cfg.get('python')
    if not py or not os.path.isfile(py):
        raise BridgeError('Reference Ears is optional and not set up: see tools/reference_ears/README.md, then set '
                          '"reference_ears": {"python": ...} in %s' % config.CONFIG_FILE)
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'reference_ears',
                          'refears.py')
    cmd = [py, script, a['song'], '--json-only']
    for k in ('start', 'end', 'focus', 'ask', 'vibe_ref'):
        if a.get(k):
            cmd += ['--' + k.replace('_', '-'), str(a[k])]
    for k in ('no_ai', 'no_split', 'no_vibe'):
        if a.get(k):
            cmd.append('--' + k.replace('_', '-'))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=int(re_cfg.get('timeout_seconds') or 1800), creationflags=0x08000000)
    lines = [l for l in r.stdout.splitlines() if l.startswith('{')]
    if r.returncode or not lines:
        raise BridgeError('Reference Ears failed: %s' % ((r.stderr or r.stdout).strip().splitlines() or ['?'])[-1])
    rep = json.loads(lines[-1])
    md = os.path.join(rep['folder'], 'report.md')
    out = {'report': open(md, encoding='utf-8').read() if os.path.exists(md) else None, 'folder': rep['folder'],
           'midi': [ld.get('midi') for ld in rep.get('lead', {}).values() if ld.get('midi')]}
    plots = [ld.get('plot') for ld in rep.get('lead', {}).values() if str(ld.get('plot', '')).endswith('.png')]
    if plots and os.path.exists(plots[0]):
        with open(plots[0], 'rb') as f:
            out['_image'] = base64.b64encode(f.read()).decode('ascii')
    return out


def local_idea(a):
    """Optional local copilot: the local model writes the idea, musicgen fixes the theory, the notes go to FL."""
    import musicgen
    model = a.get('model') or musicgen.MODEL
    idea = musicgen.ask(a['request'], model, a.get('seed'))
    song = musicgen.arrange(idea)
    rubs = musicgen.check(song['notes'])
    out = {'title': song['title'], 'key': song['key'], 'bpm': song['bpm'], 'chords': song['chords'],
           'bars': song['bars'], 'notes': len(song['notes']), 'theory_fixes': song['fixes'], 'clashes_left': rubs,
           'model': model}
    if rubs:
        out['written'] = False
        out['why'] = 'semitone clashes were left after fixing, so nothing was written (try another seed)'
        return out
    if not a.get('write', True):
        out['written'] = False
        out['notes_list'] = song['notes']
        return out
    ch = a.get('channel')
    if ch is None:
        ch = fl('result = channels.selectedChannel()')
    if a.get('set_tempo', True):
        fl(SET_TEMPO, bpm=song['bpm'])
    r = pianoroll.write(fl, {'channel': ch, 'notes': song['notes'], 'mode': a.get('mode', 'replace')})
    out.update({'written': True, 'channel': ch, 'piano_roll': r})
    if a.get('play'):
        fl(TRANSPORT, action='play', loop_mode='pattern')
    return out


NUM = {'type': 'number'}
INT = {'type': 'integer'}
BOOL = {'type': 'boolean'}
STR = {'type': 'string'}

TOOLS = [
    ('fl_doctor', 'Check every link from Claude to FL Studio and say what is wrong and how to fix it: settings, FL program, virtual MIDI ports, FL scripts installed and up to date, FL running (one copy), the bridge answering, a popup blocking FL (read by the local vision model if available), FL features, screen scaling, banner hooks, optional local AI (Ollama + models). Read-only. Use it first when any tool fails or times out, and give the user the fix lines in plain words.',
     {}, [], lambda a: __import__('doctor').diagnose(bridge)),
    ('fl_status', 'Get FL Studio state: playing, tempo, loop mode, song position, counts of patterns/channels/mixer tracks. Use first to check the connection.',
     {}, [], lambda a: fl(STATUS)),
    ('fl_transport', 'Control playback: play, pause, stop, toggle_play, record. Optionally switch loop_mode (pattern/song) or jump to position (0..1 of song). record answers FL\'s "What would you like to record?" box itself (record_what, default notes_automation).',
     {'action': {'type': 'string', 'enum': ['play', 'pause', 'stop', 'toggle_play', 'record', 'none']},
      'loop_mode': {'type': 'string', 'enum': ['pattern', 'song']}, 'position': NUM,
      'record_what': {'type': 'string', 'enum': list(RECORD_CHOICES)}},
     ['action'], transport),
    ('fl_set_tempo', 'Set the project tempo in BPM.', {'bpm': NUM}, ['bpm'], lambda a: fl(SET_TEMPO, bpm=a['bpm'])),
    ('fl_mixer_get', 'List mixer tracks with name, volume, pan, mute, solo, color. Track 0 is Master.',
     {'start': INT, 'count': INT}, [], lambda a: fl(MIXER_GET, start=a.get('start', 0), count=a.get('count', 20))),
    ('fl_mixer_set', 'Change a mixer track. volume 0..1 (0.8 = 0 dB), pan -1..1, color as "#RRGGBB". Only given fields change.',
     {'track': INT, 'name': STR, 'volume': NUM, 'pan': NUM, 'mute': BOOL, 'solo': BOOL, 'arm': BOOL, 'color': STR, 'select': BOOL},
     ['track'], lambda a: fl(MIXER_SET, **a)),
    ('fl_channels_get', 'List channel rack channels (instruments/samples) with name, volume, pan, mute, mixer track.',
     {}, [], lambda a: fl(CHANNELS_GET)),
    ('fl_channel_set', 'Change a channel rack channel. volume 0..1, pan -1..1, mixer_track routes it to that insert, select makes it the only selected channel.',
     {'channel': INT, 'name': STR, 'volume': NUM, 'pan': NUM, 'mute': BOOL, 'solo': BOOL, 'color': STR, 'mixer_track': INT, 'select': BOOL},
     ['channel'], lambda a: fl(CHANNEL_SET, **a)),
    ('fl_steps', 'Read or write step-sequencer steps for a channel in the current (or given) pattern. steps is a string like "x---x---x---x---" (x = on) or a list of 0/1. Omit steps to read.',
     {'channel': INT, 'steps': {'type': ['string', 'array']}, 'pattern': INT, 'length': INT},
     ['channel'], lambda a: fl(STEPS, channel=a['channel'], pattern=a.get('pattern'), length=a.get('length'),
                              steps=parse_steps(a['steps']) if a.get('steps') is not None else None)),
    ('fl_patterns_get', 'List patterns (1-based) with names and lengths, and which one is current.',
     {}, [], lambda a: fl(PATTERNS_GET)),
    ('fl_pattern_set', 'Switch to a pattern (1-based) and optionally rename or recolor it.',
     {'pattern': INT, 'name': STR, 'color': STR}, ['pattern'], lambda a: fl(PATTERN_SET, **a)),
    ('fl_plugin_params', 'List a plugin\'s parameters (0..1 values). Target a channel\'s instrument with channel, or a mixer effect with mixer_track + slot (0-9). Use filter to search names.',
     {'channel': INT, 'mixer_track': INT, 'slot': INT, 'filter': STR, 'offset': INT, 'limit': INT}, [],
     plugin_params),
    ('fl_mixer_effects', 'List effects loaded in mixer track slots. tracks: list of track numbers (default 0-20).',
     {'tracks': {'type': 'array', 'items': INT}}, [],
     lambda a: fl(MIXER_FX, tracks=a.get('tracks') or list(range(21)))),
    ('fl_plugin_set', 'Set one plugin parameter (value 0..1). Same targeting as fl_plugin_params.',
     {'param': INT, 'value': NUM, 'channel': INT, 'mixer_track': INT, 'slot': INT}, ['param', 'value'],
     plugin_set),
    ('fl_plugin_preset', 'Step a plugin to its next or previous preset (FL 2024+). Same targeting as fl_plugin_params.',
     {'direction': {'type': 'string', 'enum': ['next', 'prev']}, 'channel': INT, 'mixer_track': INT, 'slot': INT}, ['direction'],
     lambda a: fl(PLUGIN_PRESET, index=plugin_target(a)[0], slot=plugin_target(a)[1], direction=a['direction'])),
    ('fl_browser_list', 'List items in the FL Browser (FL 2024+). path is a list of folder names from the top, e.g. ["Plugin database", "Generators"]; empty lists the top level.',
     {'path': {'type': 'array', 'items': STR}, 'limit': INT}, [],
     lambda a: fl(browser.LIST, path=a.get('path') or [], limit=a.get('limit', 300))),
    ('fl_browser_load', 'Load a Browser item (plugin .fst from Plugin database, sample, preset) into FL (FL 2024+). path is the full list of names, e.g. ["Plugin database", "Generators", "sfizz.fst"]. where: new_channel (default), selected_channel (replaces the instrument/sample on the selected channel), playlist_track; for audio also playlist_audio_clip, playlist_audio_track. menu_item overrides with a 1-based position in the popup menu of the item.',
     {'path': {'type': 'array', 'items': STR}, 'where': {'type': 'string', 'enum': browser.WHERE}, 'menu_item': INT}, ['path'],
     lambda a: browser.load(fl, a)),
    ('fl_play_notes', 'Play notes live into FL (heard on the selected channel; recorded into the piano roll if FL is recording). notes: [{note, velocity, start_ms, duration_ms}]. channel selects that channel first. Max 60 s.',
     {'notes': {'type': 'array', 'items': {'type': 'object'}}, 'channel': INT, 'midi_channel': INT}, ['notes'], play_notes),
    ('fl_write_midi', 'Write a .mid file the user can drag into FL\'s piano roll. notes: [{pitch, start, length, velocity}] with start/length in beats (quarter notes).',
     {'notes': {'type': 'array', 'items': {'type': 'object'}}, 'bpm': NUM, 'filename': STR, 'track_name': STR}, ['notes'], write_midi),
    ('fl_piano_roll_write', 'Write notes straight into the piano roll (FL 2024+). notes: [{pitch, start, length, velocity}] with start/length in beats (quarter notes), velocity 0..1 or 1..127. channel picks whose piano roll (0-based); mode replace (default, clears first) or add. Runs the Claude Notes script through the piano roll Tools menu in the background (no manual first run needed).',
     {'notes': {'type': 'array', 'items': {'type': 'object'}}, 'channel': INT, 'mode': {'type': 'string', 'enum': ['replace', 'add']}}, ['notes'],
     lambda a: pianoroll.write(fl, a)),
    ('fl_piano_roll_read', 'Read the notes in a channel piano roll (FL 2024+); start/length in beats.',
     {'channel': INT, 'limit': INT}, [], lambda a: pianoroll.read(fl, a)),
    ('fl_save', 'Save the project with FL’s own Save command (opens Save As if never saved). new_version saves a numbered copy. Only when the user asks.',
     {'new_version': BOOL}, [], lambda a: fl(SAVE, new_version=bool(a.get('new_version')))),
    ('fl_window', 'Show/focus or hide an FL window: playlist, channel_rack, piano_roll, mixer, browser.',
     {'window': {'type': 'string', 'enum': list(WINDOWS)}, 'show': BOOL}, ['window'],
     lambda a: fl(SHOW_WINDOW, wid=WINDOWS[a['window']], show=a.get('show', True))),
    ('fl_bbcso_list', 'List BBC Symphony Orchestra Discover instruments and their articulations (index = keyswitch MIDI note).',
     {}, [], lambda a: bbcso.catalog()),
    ('fl_bbcso_load', 'Load a BBC Symphony Orchestra instrument (e.g. "Violins 1", "Celli", "Horns", "Harp and Celeste") into a new channel. Articulations are switched with keyswitch notes: MIDI note = articulation index from fl_bbcso_list (strings: 0 Long, 1 Spiccato, 2 Pizzicato, 3 Tremolo), placed just before the passage.',
     {'instrument': STR, 'channel_name': STR}, ['instrument'],
     lambda a: {'channel': bbcso.load(fl, a['instrument'], a.get('channel_name'))}),
    ('fl_drum_kit', "One call: load the user's own drum kit from kit.json (%APPDATA%\\claude-fl-studio-bridge\\kit.json, see kit.example.json): each sample from its FL Browser path into a channel, routed to its mixer insert, named and coloured, with an optional starter step pattern. Re-run safe: keeps channels already named like the kit, writes steps only on empty rows, never renames inserts the user named, never touches mixer effects or EQ. kit_file overrides the kit path.",
     {'kit_file': STR}, [],
     lambda a: kits.setup_kit(fl, a.get('kit_file'))),
    # hands-off window tools: they post messages to FL's own windows only - nothing else on screen is
    # hidden, minimized or clicked, and the real mouse/keyboard are not used
    ('fl_windows', 'List FL\'s open windows: main, dialogs (incl. file dialogs and popup menus) and plugin editors, with hwnd and rect. Use when a tool says FL is blocked by a dialog.',
     {}, [], lambda a: flhands.windows()),
    ('fl_snapshot', 'Picture of one FL window (default main; target = hwnd or title text, e.g. "Recording", "sfizz"), captured off-screen so other apps are not disturbed. crop [x0,y0,x1,y1] and scale (2 = half size) in window pixels. Coordinates from the image (times scale) go to fl_click.',
     {'target': {'type': ['string', 'integer']}, 'crop': {'type': 'array', 'items': INT}, 'scale': INT}, [], snapshot),
    ('fl_click', 'Click inside an FL window at x,y (window pixels, as in fl_snapshot at scale 1) by posting mouse messages to FL only - the real mouse does not move. Works on FL dialogs, buttons and plugin UIs; for FL popup menus use fl_exec ui.down()/ui.right() and fl_key enter on the menu window.',
     {'target': {'type': ['string', 'integer']}, 'x': NUM, 'y': NUM, 'button': {'type': 'string', 'enum': ['left', 'right']}, 'double': BOOL},
     ['x', 'y'], lambda a: flhands.click(target_arg(a), a['x'], a['y'], a.get('button', 'left'), bool(a.get('double')))),
    ('fl_key', 'Send a key (enter, esc, tab, up/down/left/right, a letter...) and/or text to an FL window by posted messages (no real keyboard).',
     {'target': {'type': ['string', 'integer']}, 'key': STR, 'text': STR}, [], fl_key),
    ('fl_file_dialog', 'Answer an open Windows Open/Save dialog of FL or a plugin (e.g. sfizz "Load SFZ file") with a full file path, in the background.',
     {'path': STR, 'target': {'type': ['string', 'integer']}}, ['path'],
     lambda a: {'done': flhands.fill_file_dialog(a['path'], a.get('target'))}),
    ('fl_read_window', 'Optional (needs Ollama with a vision model). Ask the local vision model (default qwen3-vl, offline) what an FL window shows: title, message and buttons with click points in window pixels. Default target = top dialog, else main window. ~10-20 s.',
     {'target': {'type': ['string', 'integer']}}, [],
     lambda a: ollama_eyes.read_window(target_arg(a) if a.get('target') is not None else None)),
    ('fl_local_idea', 'Optional local copilot (needs Ollama): the local model (local_ai.idea_model) writes a short piano idea for request (mood, key, tempo, chords, melody); music-theory code then fixes it (in-key triads, smooth voicing, melody on chord tones, no semitone clashes - refuses if any are left) and writes it into a channel\'s piano roll (default the selected channel; mode replace/add), sets the tempo (set_tempo, default true) and optionally plays it. write=false only returns the notes. Works offline. ~20-40 s.',
     {'request': STR, 'channel': INT, 'mode': {'type': 'string', 'enum': ['replace', 'add']}, 'set_tempo': BOOL,
      'play': BOOL, 'write': BOOL, 'seed': INT, 'model': STR}, ['request'], local_idea),
    ('fl_reference', 'Optional Reference Ears (needs its own Python environment, see tools/reference_ears/README.md): listen to a reference song offline - song = audio file path, YouTube link or "Artist - Title" (downloaded with yt-dlp if allowed). Returns the report: tempo (+half/double), key, chords per section, sections with the parts playing, loudness, frequency balance, per-part shares; with start/end (m:ss) and/or focus (guitar, vocals, bass, piano, other, drums) also the lead line as notes + a .mid file (drag into the piano roll) + a plot image, bends, vibrato, scale, phrasing; plus the optional local listening AI (Qwen3-Omni via llama.cpp) and vibe check. HEAVY: the first run on a song splits it (several minutes on CPU) and the listening AI needs a big model in memory - tell the user before running it; no_split / no_ai / no_vibe make it lighter. Measured numbers are reliable; the AI words are a second opinion.',
     {'song': STR, 'start': STR, 'end': STR, 'focus': {'type': 'string', 'enum': ['guitar', 'vocals', 'bass', 'piano', 'other', 'drums', 'mix']},
      'ask': STR, 'no_ai': BOOL, 'no_split': BOOL, 'no_vibe': BOOL, 'vibe_ref': STR}, ['song'], reference),
    ('fl_muse_render','Optional orchestral route (needs MuseScore Studio 4 + Muse Sounds): write parts as MusicXML (slurs for legato, dynamics, tempo map for rubato), render each part with Muse Sounds through MuseScore\'s command line, and either keep one WAV stem per part from bar 1 (mode stems: put them on playlist tracks at bar 1) or cut them into phrase clips plus an SFZ map for sfizz (mode clips: one key per clip). parts: [{instrument (violin, viola, cello, contrabass, flute, oboe, clarinet, bass clarinet, bassoon, soprano sax, alto sax, tenor sax, baritone sax, horn, trumpet, trombone, bass trombone, tuba), name, dynamic (pp..ff), legato (default true), notes [{pitch (concert MIDI), start, length} in beats], phrases [[start_beat, end_beat]] for clips}]. Also name, bpm, time_signature [4,4], tempo_map [[beat, bpm]], key_fifths. Takes ~20-60 s per part. spec_file loads a whole spec from JSON (relative to the bridge folder), e.g. spec_file=examples/muse_chorale.json.',
     {'name': STR, 'bpm': NUM, 'time_signature': {'type': 'array', 'items': INT}, 'tempo_map': {'type': 'array'},
      'key_fifths': INT, 'mode': {'type': 'string', 'enum': ['stems', 'clips']}, 'parts': {'type': 'array'},
      'out_dir': STR, 'spec_file': STR}, [],
     lambda a: __import__('muse').run(json.load(open(a['spec_file'] if os.path.isabs(a['spec_file']) else os.path.join(
         os.path.dirname(os.path.dirname(os.path.abspath(__file__))), a['spec_file']), encoding='utf-8'))
         if a.get('spec_file') else a)),
    ('fl_project', 'Projects without Open/Save dialogs. new: copy an FL template (default "Empty": no stock channels) to <projects_dir>\\<name>\\<name>.flp (projects_dir from config.json; default Documents\\Image-Line\\FL Studio\\Projects; or pass folder) and open it, so fl_save then saves there. open: open a .flp (restarts FL cleanly). close / start FL. templates: list template names. unsaved: refuse (default) / save / discard when FL has unsaved changes.',
     {'action': {'type': 'string', 'enum': ['new', 'open', 'close', 'start', 'templates']}, 'name': STR, 'path': STR,
      'template': STR, 'folder': STR, 'unsaved': {'type': 'string', 'enum': ['refuse', 'save', 'discard']}},
     ['action'], project),
    ('fl_render', 'Render (export) to audio files, hands-off. FAST route (default when FL is open with this project): FL’s own Export window driven by posted clicks - FL stays open, nothing restarts, renders the project AS IT IS NOW (unsaved changes included, no save needed; ~6 s for a short song). split=true: one WAV per mixer insert (<name>_<insert>.wav) + <name>_Master.wav in one pass (FL turns master effects off while splitting - use it to measure balance, not as the final mix). SLOW route (FL closed, another project, or mode=cli): FL’s command-line renderer, which needs FL closed, so an open FL is closed (unsaved: refuse/save/discard) and reopened (reopen=false to skip); renders the SAVED file. Song mode is switched on automatically for in-app renders. project: .flp path (default: the open project). formats: any of wav, mp3, ogg, flac. out_dir / name: where the files go (default: next to the project, named after it). Returns file paths, the route used, and a check of the WAV: length, integrated LUFS, true peak, clipping, silent share.',
     {'project': STR, 'formats': {'type': 'array', 'items': {'type': 'string', 'enum': list(render.FORMATS)}},
      'out_dir': STR, 'name': STR, 'unsaved': {'type': 'string', 'enum': ['refuse', 'save', 'discard']},
      'reopen': BOOL, 'timeout': INT, 'mode': {'type': 'string', 'enum': ['auto', 'app', 'cli']}, 'split': BOOL}, [],
     lambda a: render.render(fl, a.get('project'), a.get('formats') or ['wav'], a.get('out_dir'), a.get('name'),
                             a.get('unsaved', 'refuse'), a.get('reopen', True), a.get('timeout', 1800),
                             a.get('mode', 'auto'), bool(a.get('split')))),
    ('fl_playlist', 'Playlist clips, hands-off, at any zoom/scroll. place: pattern (number or name) on track (1-based) at bar. place_many: copy the pattern to every bar in bars (trap-style layout: short patterns pasted along the playlist; ~2.5 s per copy; the first copy is fixed to the pattern\'s real length because FL paints at the last resized length). trim: make the clip covering bar end at end_bar (drag its right edge; also extends). cut: cut a gap from from_bar to to_bar out of a clip (slice tool + delete, e.g. 808 gaps; trims when to_bar is the clip end). remove: delete the clip starting at bar. ORDER: place all copies first, then cut. Refuses to cover existing clips unless allow_overlap; every action is read back.',
     {'action': {'type': 'string', 'enum': ['place', 'place_many', 'trim', 'cut', 'remove']},
      'pattern': {'type': ['string', 'integer']}, 'track': INT, 'bar': NUM, 'bars': {'type': 'array', 'items': NUM},
      'end_bar': NUM, 'from_bar': NUM, 'to_bar': NUM, 'allow_overlap': BOOL}, ['action', 'track'],
     lambda a: {'place': lambda: playlist_place.place(fl, a['pattern'], a['track'], a['bar'], bool(a.get('allow_overlap'))),
                'place_many': lambda: playlist_place.place_many(fl, a['pattern'], a['track'], a['bars'], bool(a.get('allow_overlap'))),
                'trim': lambda: playlist_place.trim(fl, a['track'], a['bar'], a['end_bar']),
                'cut': lambda: playlist_place.cut(fl, a['track'], a['from_bar'], a['to_bar']),
                'remove': lambda: playlist_place.remove(fl, a['track'], a['bar'])}[a['action']]()),
    ('fl_automation', 'Automation clips, hands-off. create_volume: make an automation clip for a mixer insert\'s volume fader (mixer_track; 0 = master) - FL adds it as a channel and puts it on the first free playlist track at bar 1. create_pan: the same for the insert\'s PAN knob (clip named \"<insert> - Panning\", placed at the song start; points: 0.5 = centre, up = right). sidechain: on the playlist track holding a volume clip, drop to duck_db (default -12) every every_beats beats (default 4) from start_bar for bars bars, recovering to top_db (default 0) over release_beats (default 1.5) with release_curve (Single curve, Smooth, Single curve 2/3, Double curve, Half sine); optional offset_beats (e.g. 1 = hit beat 2), hold_beats (stay down), attack_beats (ramp down instead of a drop), hits (explicit beats, 0 = bar 1). Call again with start_bar/bars for another section. add_point: add a point at beat (0 = bar 1 start) with value db. set_mode: change the curve mode of the point at beat/db (Smooth, Hold, Single curve, Double curve, Half sine, Stairs, Pulse, Wave ...).',
     {'action': {'type': 'string', 'enum': ['create_volume', 'create_pan', 'sidechain', 'add_point', 'set_mode']},
      'mixer_track': INT, 'track': INT, 'bars': INT, 'start_bar': INT, 'duck_db': NUM, 'every_beats': NUM,
      'release_beats': NUM, 'beat': NUM, 'db': NUM, 'mode': STR, 'offset_beats': NUM, 'hold_beats': NUM,
      'attack_beats': NUM, 'top_db': NUM, 'release_curve': STR, 'hits': {'type': 'array', 'items': NUM}},
     ['action'], lambda a: automation(a)),
    ('fl_knob', 'Set a mixer effect knob to the value FL displays, instantly (lookup tables for Fruity Compressor, Parametric EQ 2, Reeverb 2; other plugins fall back to a search). param = index or name ("Threshold", "Band 3 freq", "Decay time"); value = number in the display unit (dB, Hz, ms, sec, %, ratio) or text for choices ("Soft", "High pass").',
     {'mixer_track': INT, 'slot': INT, 'param': {'type': ['string', 'integer']}, 'value': {'type': ['string', 'number']}},
     ['mixer_track', 'slot', 'param', 'value'],
     lambda a: {'display': knobtable.set_display(a['mixer_track'], a['slot'], a['param'], a['value'])}),
    ('fl_api', 'List the function names in one of FL\'s scripting modules (transport, mixer, channels, patterns, playlist, ui, general, plugins, arrangement, device, midi).',
     {'module': STR}, ['module'], lambda a: fl(API_LIST, module=a['module'])),
    ('fl_exec', 'Run Python inside FL Studio with its scripting modules (transport, mixer, channels, patterns, playlist, ui, general, plugins, midi) already imported. A single expression returns its value; for statements, assign the answer to `result`. print() output is returned too.',
     {'code': STR}, ['code'], lambda a: fl(a['code'])),
]

TOOL_MAP = {t[0]: t for t in TOOLS}


def tool_list():
    out = []
    for name, desc, props, req, _ in TOOLS:
        out.append({'name': name, 'description': desc,
                    'inputSchema': {'type': 'object', 'properties': props, 'required': req}})
    return out


# ---------------------------------------------------------------- MCP plumbing

def send(obj):
    sys.stdout.buffer.write(json.dumps(obj).encode('utf-8') + b'\n')
    sys.stdout.buffer.flush()


def handle(msg):
    method, mid = msg.get('method'), msg.get('id')
    if mid is None:
        return  # notification
    if method == 'initialize':
        client_ver = (msg.get('params') or {}).get('protocolVersion') or PROTOCOL_VERSION
        send({'jsonrpc': '2.0', 'id': mid, 'result': {
            'protocolVersion': client_ver,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'fl-studio', 'version': VERSION},
            'instructions': 'Controls a running FL Studio through the Claude Bridge MIDI script. '
                            'Call fl_status first. Channels are 0-based, patterns 1-based, mixer track 0 is Master.'}})
    elif method == 'tools/list':
        send({'jsonrpc': '2.0', 'id': mid, 'result': {'tools': tool_list()}})
    elif method == 'tools/call':
        params = msg.get('params') or {}
        tool = TOOL_MAP.get(params.get('name'))
        if not tool:
            send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': 'Unknown tool'}})
            return
        try:
            banner = overlay.busy() if params.get('name') not in QUIET else contextlib.nullcontext()
            with banner:
                value = tool[4](params.get('arguments') or {})
            image = value.pop('_image', None) if isinstance(value, dict) else None
            text, is_error = json.dumps(value, indent=1, ensure_ascii=False), False
        except BridgeError as e:
            text, is_error, image = str(e), True, None
        except Exception as e:
            text, is_error, image = '%s: %s' % (type(e).__name__, e), True, None
        content = [{'type': 'text', 'text': text}]
        if image:
            content.append({'type': 'image', 'data': image, 'mimeType': 'image/png'})
        send({'jsonrpc': '2.0', 'id': mid, 'result': {'content': content, 'isError': is_error}})
    elif method == 'ping':
        send({'jsonrpc': '2.0', 'id': mid, 'result': {}})
    else:
        send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'Method not found: %s' % method}})


def main():
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        handle(msg)


if __name__ == '__main__':
    main()
