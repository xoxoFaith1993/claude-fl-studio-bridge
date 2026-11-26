"""fl_doctor: check every link from Claude to FL Studio and say, in plain words, what's wrong and how to fix it.

Order of checks (each one only makes sense if the ones before pass):
  settings -> FL Studio program -> virtual MIDI ports -> FL scripts installed -> FL running (one copy) ->
  bridge answers -> FL blocked by a popup? (read with the local vision model if available) -> FL features ->
  screen scaling (for click-based tools) -> banner hooks -> optional local AI (Ollama + models)

Run it from Claude (tool fl_doctor) or by hand:  python tools\\doctor.py
It starts nothing and changes none of your settings, scripts or projects (when FL is stuck on a popup and Ollama
is already running, it saves one snapshot of that popup to read it).
"""
import ctypes
import filecmp
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

OK, WARN, FAIL, SKIP = 'ok', 'warn', 'fail', 'skip'
REPO = os.path.dirname(HERE)
_BUNDLED = os.path.join(REPO, 'python', 'python.exe')
PY = _BUNDLED if os.path.isfile(_BUNDLED) else sys.executable


def cmd(script):
    """A command line that works from anywhere: this install's Python + the full script path."""
    path, _, rest = script.partition(' ')
    return '"%s" "%s"%s' % (PY, os.path.join(REPO, path), (' ' + rest) if rest else '')



def packaged_python():
    """True when this Python is a Microsoft Store / Python Install Manager (packaged) build. Those see a private copy
    of %APPDATA%, so files they write there (settings, logs) are invisible to other programs."""
    try:
        import ctypes
        n = ctypes.c_uint32(0)
        return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(n), None) != 15700   # 15700 = no package
    except Exception:
        return False


def _row(rows, name, state, detail, fix=None):
    rows.append({'check': name, 'result': state, 'detail': detail, **({'fix': fix} if fix else {})})
    return state


def diagnose(bridge=None, read_dialogs=True):
    rows = []

    # 1. settings
    src = 'found' if os.path.isfile(config.CONFIG_FILE) else 'not found (defaults in use, that is fine)'
    _row(rows, 'settings', OK, 'config.json %s: %s' % (src, config.CONFIG_FILE))

    if packaged_python():
        _row(rows, 'Python', WARN, 'This is a Microsoft Store / Python Install Manager Python (%s). It sees a private '
             'copy of AppData, so some checks below can be wrong and files it writes there are invisible to the app.'
             % sys.executable, 'Use the Python the installer bundles (python\\python.exe in the install folder) or '
             'Python from python.org.')
    # 2. FL Studio program
    exe = config.get('fl_exe')
    if exe and os.path.isfile(exe):
        _row(rows, 'FL Studio program', OK, exe)
    else:
        _row(rows, 'FL Studio program', WARN, 'FL64.exe not found. Most tools still work while FL is open; '
             'fl_project and the command-line render need it.', 'Set "fl_exe" in %s' % config.CONFIG_FILE)

    # 3. virtual MIDI ports
    import winmidi
    to_fl, from_fl = config.get('port_to_fl'), config.get('port_from_fl')
    try:
        outs, ins = winmidi.output_names(), winmidi.input_names()
    except Exception as e:
        outs, ins = [], []
        _row(rows, 'MIDI ports', FAIL, 'Windows MIDI could not be listed: %s' % e)
    missing = [n for n, lst in ((to_fl, outs), (from_fl, ins)) if not any(n == x or x.startswith(n) for x in lst)]
    if missing:
        fix = ('Create two ports named exactly "%s" and "%s" in loopMIDI (keep it running) or as Windows MIDI Services '
               'loopbacks (docs/SETUP.md step 1).' % (to_fl, from_fl))
        lm = loopmidi_state()
        if lm == 'installed, not running':
            fix = 'loopMIDI is installed but not running: start it (Start menu > loopMIDI). Its ports only exist while ' \
                  'it runs; it can start with Windows from its tray icon menu.'
        return _finish(rows, _row(rows, 'MIDI ports', FAIL, 'Missing virtual port(s): %s. Found outputs %s, inputs %s.'
                                  ' loopMIDI: %s.' % (', '.join(missing), outs, ins, lm), fix))
    dup = [n for n in (to_fl, from_fl) if sum(1 for x in outs + ins if x == n) > 2]
    _row(rows, 'MIDI ports', WARN if dup else OK, 'both ports present' + (' (duplicate names: %s)' % dup if dup else ''),
         'Remove the duplicate ports so each name exists once.' if dup else None)

    # 4. FL-side scripts installed
    ud = config.get('fl_user_data')
    dev = os.path.join(ud, 'Settings', 'Hardware', 'Claude Bridge', 'device_ClaudeBridge.py')
    notes = os.path.join(ud, 'Settings', 'Piano roll scripts', 'Claude Notes.pyscript')
    ship_dev = os.path.join(REPO, 'fl_script', 'device_ClaudeBridge.py')
    if not os.path.isfile(dev):
        _row(rows, 'FL MIDI script', FAIL, 'Not installed at %s' % dev,
             'Run: ' + cmd('tools\\install_fl_scripts.py') + ' (or re-run the installer with the FL scripts box ticked), then '
             'restart FL.')
    elif os.path.isfile(ship_dev) and not filecmp.cmp(dev, ship_dev, shallow=False):
        _row(rows, 'FL MIDI script', WARN, 'Installed, but different from this version of the bridge.',
             'Run: ' + cmd('tools\\install_fl_scripts.py') + ', then in FL: Options > MIDI settings > Update MIDI scripts.')
    else:
        _row(rows, 'FL MIDI script', OK, dev)
    _row(rows, 'piano roll script', OK if os.path.isfile(notes) else WARN,
         notes if os.path.isfile(notes) else 'Claude Notes.pyscript not installed (piano roll tools will fail).',
         None if os.path.isfile(notes) else 'Run: ' + cmd('tools\\install_fl_scripts.py'))

    # 5. FL running, one copy
    import flhands
    import winfocus
    pids = flhands.fl_pids()
    if not pids:
        return _finish(rows, _row(rows, 'FL Studio running', FAIL, 'FL Studio is not open.',
                                  'Open FL Studio (or ask Claude: fl_project start).'))
    if len(pids) > 1:
        _row(rows, 'FL Studio running', WARN, '%d copies of FL64.exe are running; the MIDI ports belong to only one.'
             % len(pids), 'Close the extra FL Studio windows.')
    else:
        _row(rows, 'FL Studio running', OK, flhands._text(winfocus.find_fl_window() or 0) or 'FL64.exe')

    # 6. does the bridge answer?
    if bridge is None:
        from bridge import Bridge
        bridge = Bridge()
    try:
        ver, _ = bridge.run('general.getVersion()', timeout=4.0)
        _row(rows, 'bridge answers', OK, 'FL scripting API version %s' % ver)
    except Exception as e:
        ds = []
        try:
            ds = flhands.dialogs()
        except Exception:
            pass
        if ds:
            d = ds[0]
            detail = 'FL is blocked by a popup: "%s" (%s, hwnd %s).' % (d['title'], d['class'], d['hwnd'])
            seen = read_dialog(d, allow_start=False) if read_dialogs else None
            if seen:
                detail += ' It says: %s' % seen
            return _finish(rows, _row(rows, 'bridge answers', FAIL, detail,
                                      'Answer or close that popup in FL (or let Claude do it: fl_snapshot, then '
                                      'fl_click / fl_key). FL runs no scripts while a popup is open.'))
        return _finish(rows, _row(rows, 'bridge answers', FAIL, 'No reply from the FL script: %s' % str(e).split('\n')[0],
                                  'In FL: Options > MIDI settings. Input "%s": Enable on, Controller type "Claude Bridge", '
                                  'a port number. Output "%s": the SAME port number. "%s" must not have that number in the '
                                  'Output list. Check View > Script output for "Claude Bridge ready".'
                                  % (to_fl, from_fl, to_fl)))

    # 7. FL features
    try:
        has = bridge.run('result = {"plugins": plugins is not None, "arrangement": arrangement is not None}',
                         timeout=4.0)[0]
        miss = [k for k, v in has.items() if not v]
        _row(rows, 'FL features', WARN if miss else OK,
             'missing modules: %s (older FL; some tools are limited)' % miss if miss else 'plugins + arrangement modules present',
             'FL 2024 or newer gives every tool.' if miss else None)
    except Exception:
        pass

    # 8. screen scaling (click-based tools were measured at 100%)
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(winfocus.find_fl_window())
        pct = round(dpi / 96 * 100) if dpi else None
        _row(rows, 'screen scaling', OK if pct in (None, 100) else WARN,
             'FL runs at %s%% scaling' % pct if pct else 'unknown',
             None if pct in (None, 100) else 'Playlist, piano roll menu and Export clicks were measured at 100%; if one '
             'misses, see docs/CALIBRATION.md.')
    except Exception:
        pass

    # 9. banner hooks
    banner = config.get('banner')
    if banner.get('enabled', True):
        settings = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
        overlay = os.path.join(HERE, 'overlay.py').replace('\\', '/').lower()
        try:
            text = open(settings, encoding='utf-8').read().replace('\\\\', '/').replace('\\', '/').lower()
        except OSError:
            text = ''
        hooked = overlay in text
        _row(rows, 'banner hooks', OK if hooked else WARN,
             'Stop/UserPromptSubmit hooks found' if hooked else 'no hooks for this copy: the banner fades only after '
             '%ss' % banner.get('hold_seconds', 90), None if hooked else 'Run: ' + cmd('tools\\connect_claude.py --no-mcp'))

    # 10. optional local AI
    _local_ai(rows)
    return _finish(rows, None)


def loopmidi_state():
    import subprocess
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq loopMIDI.exe', '/NH'], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    if 'loopMIDI.exe' in out:
        return 'running'
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
        if base and os.path.isfile(os.path.join(base, 'Tobias Erichsen', 'loopMIDI', 'loopMIDI.exe')):
            return 'installed, not running'
    return 'not found'


def _local_ai(rows):
    la = config.get('local_ai')
    if not la.get('enabled', True):
        return _row(rows, 'local AI (optional)', SKIP, 'turned off in config.json (local_ai.enabled)')
    import ollama_ctl
    st = ollama_ctl.status()
    if not st['installed'] and not st['running']:
        return _row(rows, 'local AI (optional)', SKIP, 'Ollama is not installed. fl_read_window and fl_local_idea need '
                    'it; everything else works without it.', 'Optional: install Ollama from ollama.com')
    if not st['running']:
        return _row(rows, 'local AI (optional)', OK, 'Ollama installed, not running (it starts by itself when a tool '
                    'needs it)' if la.get('auto_start', True) else 'Ollama installed, not running (auto_start is off)')
    need = {config.get('vision_model'), la.get('idea_model')}
    miss = [m for m in need if m and not ollama_ctl.has_model(m)]
    _row(rows, 'local AI (optional)', WARN if miss else OK,
         'Ollama running at %s; models: %s' % (st['host'], ', '.join(st['models']) or 'none'),
         ('Pull the missing model(s): ' + '; '.join('ollama pull ' + m for m in miss)) if miss else None)


def read_dialog(d, allow_start=True):
    """Short description of a blocking FL popup by the local vision model, or None if not available.
    allow_start=False (fl_doctor, which must change nothing) only reads it if Ollama is already running."""
    if not config.get('local_ai').get('read_stuck_dialogs', True):
        return None
    try:
        import ollama_ctl
        if not ollama_ctl.is_running() and (not allow_start or not ollama_ctl.ensure_running()):
            return None
        import ollama_eyes
        info = ollama_eyes.read_window(d)
        text = (info.get('message') or info.get('title') or '').strip()
        buttons = ', '.join('"%s" at (%s, %s)' % (b.get('label'), b.get('x'), b.get('y'))
                            for b in info.get('buttons', []) if isinstance(b, dict))
        return (text + ('. Buttons: ' + buttons if buttons else '')) or None
    except Exception:
        return None


def _finish(rows, stopped_at):
    worst = FAIL if any(r['result'] == FAIL for r in rows) else WARN if any(r['result'] == WARN for r in rows) else OK
    summary = {OK: 'Everything checked is fine.', WARN: 'Working, with warnings (see fix lines).',
               FAIL: 'Not working - the first failing check below is the one to fix.'}[worst]
    out = {'summary': summary, 'checks': rows}
    if stopped_at == FAIL:
        out['note'] = 'Later checks were skipped because they depend on this one.'
    return out


def text_report(res):
    sign = {OK: '[ ok ]', WARN: '[warn]', FAIL: '[FAIL]', SKIP: '[skip]'}
    lines = [res['summary'], '']
    for r in res['checks']:
        lines.append('%s %-20s %s' % (sign[r['result']], r['check'], r['detail']))
        if r.get('fix'):
            lines.append('       %-20s fix: %s' % ('', r['fix']))
    if res.get('note'):
        lines += ['', res['note']]
    return '\n'.join(lines)


if __name__ == '__main__':
    res = diagnose()
    print(json.dumps(res, indent=1) if '--json' in sys.argv else text_report(res))
