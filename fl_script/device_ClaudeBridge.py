# name=Claude Bridge
# url=
#
# Runs inside FL Studio as a MIDI controller script.
# Claude sends Python code over a virtual MIDI port (as SysEx), this script
# runs it with FL's scripting modules and sends the result back as SysEx.
#
# Message frame (all bytes after F0 are 7-bit):
#   F0 7D <kind> <id hi> <id lo> <chunk hi> <chunk lo> <count hi> <count lo> <ascii payload...> F7
#   kind 0x01 = command (Claude -> FL), 0x02 = reply (FL -> Claude)
# The payload of all chunks joined is a Python string literal (ascii-escaped).

import device
import transport
import mixer
import channels
import patterns
import playlist
import ui
import general
import midi

try:
    import plugins
except ImportError:
    plugins = None

try:
    import arrangement
except ImportError:
    arrangement = None

MAGIC = 0x7D
KIND_CMD = 0x01
KIND_REPLY = 0x02
CHUNK = 200

_pending = {}  # msg id -> {chunk index: bytes}
_ns = {}


def _make_ns():
    ns = {
        'transport': transport, 'mixer': mixer, 'channels': channels,
        'patterns': patterns, 'playlist': playlist, 'ui': ui,
        'general': general, 'midi': midi, 'device': device,
        'plugins': plugins, 'arrangement': arrangement,
        'result': None,
    }
    return ns


def _clean(v, depth=0):
    # Turn results into plain Python literals that the other side can parse.
    if depth > 6:
        return str(v)
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (bytes, bytearray)):
        return list(v)
    if isinstance(v, (list, tuple, set)):
        return [_clean(x, depth + 1) for x in v]
    if isinstance(v, dict):
        out = {}
        for k in v:
            out[str(k)] = _clean(v[k], depth + 1)
        return out
    return str(v)


def _run(code):
    global _ns
    if not _ns:
        _ns = _make_ns()
    out = []

    def _print(*args, **kwargs):
        out.append(' '.join(str(a) for a in args))

    _ns['print'] = _print
    _ns['result'] = None
    try:
        try:
            compiled = compile(code, '<claude>', 'eval')
            is_expr = True
        except SyntaxError:
            compiled = compile(code, '<claude>', 'exec')
            is_expr = False
        if is_expr:
            value = eval(compiled, _ns)
        else:
            exec(compiled, _ns)
            value = _ns.get('result')
        return {'ok': True, 'value': _clean(value), 'output': '\n'.join(out)}
    except Exception as e:
        return {'ok': False, 'error': type(e).__name__ + ': ' + str(e), 'output': '\n'.join(out)}


def _send(kind, msg_id, text):
    data = text.encode('ascii')
    parts = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b'']
    count = len(parts)
    for idx in range(count):
        frame = bytes([0xF0, MAGIC, kind,
                       (msg_id >> 7) & 0x7F, msg_id & 0x7F,
                       (idx >> 7) & 0x7F, idx & 0x7F,
                       (count >> 7) & 0x7F, count & 0x7F]) + parts[idx] + bytes([0xF7])
        device.midiOutSysex(frame)


def _handle_sysex(data):
    data = bytes(data)
    if len(data) < 10 or data[0] != 0xF0 or data[1] != MAGIC:
        return False
    if data[2] != KIND_CMD:
        return True  # our own reply echoed back; ignore but swallow it
    msg_id = (data[3] << 7) | data[4]
    idx = (data[5] << 7) | data[6]
    count = (data[7] << 7) | data[8]
    end = len(data) - 1 if data[-1] == 0xF7 else len(data)
    chunks = _pending.setdefault(msg_id, {})
    chunks[idx] = data[9:end]
    if len(chunks) < count:
        return True
    del _pending[msg_id]
    text = b''.join(chunks[i] for i in range(count)).decode('ascii')
    try:
        code = eval(text, {'__builtins__': {}})
        reply = _run(code)
    except Exception as e:
        reply = {'ok': False, 'error': 'Bad command frame: ' + str(e), 'output': ''}
    _send(KIND_REPLY, msg_id, ascii(reply))
    return True


def OnInit():
    try:
        port = str(device.getPortNumber())
    except Exception:
        port = '?'
    print('Claude Bridge ready (port ' + port + ')')


def OnDeInit():
    _pending.clear()


def OnSysEx(event):
    if _handle_sysex(event.sysex):
        event.handled = True


def OnMidiMsg(event):
    # Some FL builds route SysEx here instead of OnSysEx.
    sysex = getattr(event, 'sysex', None)
    if sysex and event.status == 0xF0:
        if _handle_sysex(sysex):
            event.handled = True
