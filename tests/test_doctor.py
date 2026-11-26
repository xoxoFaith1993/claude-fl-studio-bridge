"""Offline tests for fl_doctor: fake MIDI ports and FL state, no FL, MIDI or Ollama needed.

  python tests\\test_doctor.py
"""
import os
import sys
import types

os.environ['FL_OVERLAY'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import config  # noqa: E402
import doctor  # noqa: E402
import flhands  # noqa: E402
import winmidi  # noqa: E402

config.CFG['local_ai']['enabled'] = False        # keep the test away from any real Ollama


def run(outs, ins, pids, reply=None, error=None, dialogs=()):
    winmidi.output_names = lambda: outs
    winmidi.input_names = lambda: ins
    flhands.fl_pids = lambda: pids
    flhands.dialogs = lambda: list(dialogs)

    class FakeBridge:
        def run(self, code, timeout=10.0):
            if error:
                raise RuntimeError(error)
            return reply, ''
    return doctor.diagnose(FakeBridge(), read_dialogs=False)


def result(res, name):
    return next(r for r in res['checks'] if r['check'] == name)


# 1. no virtual ports -> stops at MIDI ports with a fix
r = run(['Microsoft GS Wavetable Synth'], [], [])
assert result(r, 'MIDI ports')['result'] == 'fail' and 'fix' in result(r, 'MIDI ports'), r
assert r['checks'][-1]['check'] == 'MIDI ports' and 'note' in r

# 2. ports present, FL closed -> stops at FL running
r = run(['Claude to FL'], ['FL to Claude'], [])
assert result(r, 'MIDI ports')['result'] == 'ok'
assert result(r, 'FL Studio running')['result'] == 'fail'

# 3. FL open but no reply and a popup open -> names the popup
r = run(['Claude to FL'], ['FL to Claude'], [1234], error='No reply from FL Studio within 4s',
        dialogs=[{'title': 'Save changes?', 'class': 'TMsgForm', 'hwnd': 42}])
row = result(r, 'bridge answers')
assert row['result'] == 'fail' and 'Save changes?' in row['detail'], row

# 4. FL open, no reply, no popup -> MIDI settings advice
r = run(['Claude to FL'], ['FL to Claude'], [1234], error='No reply from FL Studio within 4s')
assert 'Controller type' in result(r, 'bridge answers')['fix']

# 5. text report renders
assert 'fix:' in doctor.text_report(r)
print('all doctor tests passed')
