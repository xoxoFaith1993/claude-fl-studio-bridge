"""Write notes straight into FL's piano roll (FL 2024+) through the 'Claude Notes' piano roll script."""

import json
import os
import time
import uuid

import flhands
import winfocus

SCRIPTS_DIR = flhands.USER_PR_SCRIPTS
REQUEST = os.path.join(SCRIPTS_DIR, 'claude_notes_request.json')
STATE = os.path.join(SCRIPTS_DIR, 'claude_notes_state.json')

FIRST_RUN_HELP = ('FL did not run the Claude Notes script. Ctrl+Alt+Y only re-runs the LAST piano roll script, '
                  'so it must be run once by hand: in the piano roll, open the Scripts menu (or Tools > Scripting) '
                  'and click "Claude Notes". After that this works automatically.')


def run(fl, requests, channel=None, timeout=6.0):
    rid = uuid.uuid4().hex[:8]
    if requests:
        requests[0]['id'] = rid
    else:
        requests = [{'action': 'none', 'id': rid}]
    if channel is not None:
        # the piano roll keeps showing its old channel until it is closed and reopened
        fl('ui.hideWindow(midi.widPianoRoll)\nchannels.selectOneChannel(ARGS["c"])\nresult = 1', c=channel)
        time.sleep(0.2)
    fl('ui.showWindow(midi.widPianoRoll)\nui.setFocused(midi.widPianoRoll)\nresult = 1')
    time.sleep(0.2)
    with open(REQUEST, 'w') as f:
        json.dump(requests, f)
    if os.path.exists(STATE):
        os.remove(STATE)
    menu_error = None
    try:
        # hands-off route: Tools menu via a posted click + FL's own menu keys; FL stays in the background
        flhands.run_piano_roll_script(fl, 'Claude Notes')
    except Exception as e:
        menu_error = e
        # old route: bring FL to the front and press Ctrl+Alt+Y ("run last script again")
        if not winfocus.focus_fl():
            raise RuntimeError('Could not bring FL Studio to the front.')
        time.sleep(0.3)
        fl('ui.setFocused(midi.widPianoRoll)\nresult = 1')
        winfocus.send_hotkey('ctrl', 'alt', 'Y')
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(STATE) as f:
                state = json.load(f)
            if state.get('request_id') == rid or not state.get('ok', True):
                if not state.get('ok', True):
                    raise RuntimeError('Piano roll script error: %s' % state.get('error'))
                return state
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError(FIRST_RUN_HELP + (' Hands-off menu route failed: %s' % menu_error if menu_error else ''))


def write(fl, a):
    notes = a['notes']
    reqs = []
    if a.get('mode', 'replace') == 'replace':
        reqs.append({'action': 'clear'})
    reqs.append({'action': 'add', 'notes': notes})
    state = run(fl, reqs, a.get('channel'))
    return {'added': state['added'], 'cleared': state['cleared'], 'note_count': state['note_count']}


def read(fl, a):
    state = run(fl, [], a.get('channel'))
    return {'note_count': state['note_count'], 'notes': state['notes'][:a.get('limit', 500)]}
