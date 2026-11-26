"""Render from the OPEN FL Studio through its own Export window, hands-off (no restart, no focus steal).

  File menu (posted click on the FILE label) -> Export -> Wave file... -> Save As dialog (path) ->
  render window: output formats, Split mixer tracks, "Show files when complete" off -> Start.

Much faster than render.py's command-line route (which must close and relaunch FL): a 27 s song renders in
~6 s and FL stays open. Split mixer tracks writes one WAV per used insert ("<name>_<insert>.wav") plus
"<name>_Master.wav" (FL turns master effects off while splitting, so use a normal render for the final mix).
The render plays the project as it is in FL right now - unsaved changes included.
"""
import os
import time

import config
import flhands

RENDER_CLASS = 'TWAVRenderForm'
# Positions measured on FL 26.1 at 100% scaling; change them in config.json "calibration" (docs/CALIBRATION.md).
# LED centres in the render window (window pixels); posted clicks on the label toggle them
LED = {k: tuple(v) for k, v in config.cal('render_window', 'led').items()}
LABEL = {k: tuple(v) for k, v in config.cal('render_window', 'label').items()}
START = config.cal('render_window', 'start')
FILE_LABEL = config.cal('file_menu_label')       # FILE in FL's main menu bar
EXPORT_ROW_Y = config.cal('export_row_y')        # highlighted-row centre of File > Export in the File menu


def _pixel(px, w, x, y):
    i = (y * w + x) * 4
    b, g, r = px[i], px[i + 1], px[i + 2]
    return r, g, b


def led(win, name):
    """True when the option's LED is lit (orange)."""
    w, h, px = flhands.grab(win)
    x0, y0 = LED[name]
    best = 0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            r, g, b = _pixel(px, w, x0 + dx, y0 + dy)
            best = max(best, r - b)
    return best > 90


def set_led(win, name, on):
    for _ in range(3):
        if led(win, name) == on:
            return True
        flhands.click(win, *LABEL[name])
        time.sleep(0.25)
    return led(win, name) == on


def _render_window():
    for w in flhands.windows(False):
        if w['class'] == RENDER_CLASS:
            return w['hwnd']
    return None


def open_export(fl, path, timeout=10):
    """Open File > Export > Wave file..., answer the Save As dialog with path; return the render window."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fl('ui.closeActivePopupMenu()\nresult = 1')
    main = flhands.winfocus.find_fl_window()
    flhands.click(main, *FILE_LABEL)
    end = time.time() + 3
    menu = None
    while not menu and time.time() < end:
        time.sleep(0.15)
        menu = flhands._menu()
    if not menu:
        raise RuntimeError('File menu did not open')
    for _ in range(20):                                   # step down to "Export" (disabled rows are skipped)
        fl('ui.down()\nresult = 1')
        time.sleep(0.1)
        y, _band = flhands._highlight_y(menu, top_skip=0)
        if y is not None and abs(y - EXPORT_ROW_Y) <= 6:
            break
    else:
        fl('ui.closeActivePopupMenu()\nresult = 1')
        raise RuntimeError('Export row not found in the File menu')
    fl('ui.right()\nresult = 1')                          # submenu opens with "Wave file..." highlighted
    time.sleep(0.3)
    sub = [w['hwnd'] for w in flhands.windows(False) if w['class'] == 'TQuickPopupMenuWindow' and w['hwnd'] != menu]
    if not sub:
        raise RuntimeError('Export submenu did not open')
    flhands.key(sub[0], 'enter')
    flhands.fill_file_dialog(path, timeout=timeout)
    end = time.time() + timeout
    while time.time() < end:
        win = _render_window()
        if win:
            time.sleep(0.4)                               # let it paint before reading LEDs
            return win
        for d in flhands.file_dialogs():                  # "replace existing file?" or a path warning
            raise RuntimeError('Save dialog problem: %s' % flhands._text(d['hwnd']))
        time.sleep(0.2)
    raise RuntimeError('Render window did not open')


def export(fl, path, formats=('wav',), split=False, timeout=900):
    """Render the open project to path (.wav name; other formats get the same base name). Returns the files."""
    formats = [f.lower() for f in formats]
    folder = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    t0 = time.time()
    if fl('result = transport.getLoopMode()') != 1:       # pattern mode would render only the pattern
        fl('transport.setLoopMode()\nresult = transport.getLoopMode()')
    win = open_export(fl, path)
    for f in ('wav', 'mp3', 'ogg', 'flac'):
        if not set_led(win, f, f in formats):
            raise RuntimeError('could not set output format %s' % f)
    if not set_led(win, 'show_files', False):
        raise RuntimeError('could not turn off "Show files when complete"')
    split_ok = set_led(win, 'split', split)
    flhands.click(win, *START)
    end = time.time() + timeout
    while flhands.u.IsWindow(win) and flhands.u.IsWindowVisible(win):
        if time.time() > end:
            raise RuntimeError('render did not finish within %ds' % timeout)
        time.sleep(0.25)
    time.sleep(0.5)
    files = sorted(os.path.join(folder, n) for n in os.listdir(folder)
                   if n.startswith(base) and os.path.getmtime(os.path.join(folder, n)) >= t0 - 1)
    return {'files': files, 'seconds': round(time.time() - t0, 1), 'split': split, 'split_led_ok': split_ok}
