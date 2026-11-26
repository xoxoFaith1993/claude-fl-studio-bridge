"""Browser tools: find items in FL's Browser by path and load them (plugins, samples, presets).

FL's script API can only step the Browser up/down, toggle folders and open an item's popup menu,
so everything here is built from those moves. Needs FL Studio 2024+ (API with navigateBrowser).
"""

import time

import flhands
import winfocus

BROWSER_LIB = r'''
def _cap():
    return ui.getFocusedNodeCaption()
def _down():
    return ui.navigateBrowser(midi.FPT_Down, False)
def _up():
    return ui.navigateBrowser(midi.FPT_Up, False)
def _top():
    # go to the first Browser item and close every top-level folder so searches are unambiguous
    ui.showWindow(midi.widBrowser)
    ui.setFocused(midi.widBrowser)
    prev = None
    for _ in range(20000):
        c = _up()
        if c == prev:
            break
        prev = c
    steps = 0
    prev = None
    for _ in range(500):
        if ui.getFocusedNodeFileType() <= -100:
            ui.toggleBrowserNode(0)
        c = _down()
        steps += 1
        if c == prev:
            break
        prev = c
    for _ in range(steps):
        _up()
def _next_after():
    c = _down()
    _up()
    return c
def _is_folder():
    return ui.getFocusedNodeFileType() <= -100
def _walk_find(name, marker, limit=20000):
    # step down looking for name; stop at marker (end of this folder) or end of list.
    # returns number of steps taken if found, else go back and return -1
    steps = 0
    prev = _cap()
    for _ in range(limit):
        c = _down()
        steps += 1
        if c == name:
            return steps
        if (marker is not None and c == marker) or c == prev:
            break
        prev = c
    for _ in range(steps):
        _up()
    return -1
def _expand():
    # open the focused folder; returns the caption that follows its contents
    ui.toggleBrowserNode(0)
    marker = _next_after()
    ui.toggleBrowserNode(1)
    return marker
def _goto(path):
    _top()
    marker = None
    for i, name in enumerate(path):
        if not (i == 0 and _cap() == name):
            if _walk_find(name, marker) < 0:
                raise RuntimeError('Not found in Browser: ' + ' > '.join(path[:i + 1]))
        if i < len(path) - 1:
            if not _is_folder():
                raise RuntimeError(name + ' is not a folder')
            marker = _expand()
    return _cap()
def _children(limit):
    # list what is inside the focused folder (open sub-folders are listed flattened)
    marker = _expand()
    items = []
    steps = 0
    prev = _cap()
    for _ in range(limit):
        c = _down()
        steps += 1
        if c == marker or c == prev:
            break
        items.append([c, ui.getFocusedNodeFileType() <= -100])
        prev = c
    for _ in range(steps):
        _up()
    return items
'''

GOTO = BROWSER_LIB + r'''
_goto(ARGS['path'])
result = {'focused': _cap(), 'file_type': ui.getFocusedNodeFileType()}
'''

LIST = BROWSER_LIB + r'''
if ARGS['path']:
    _goto(ARGS['path'])
    items = _children(ARGS['limit'])
else:
    _top()
    items = [[_cap(), _is_folder()]]
    for _ in range(ARGS['limit']):
        c = _down()
        if c == items[-1][0]:
            break
        items.append([c, _is_folder()])
result = {'path': ARGS['path'], 'items': [{'name': n, 'folder': f} for n, f in items]}
'''

AUDIO_TYPES = {7, 13, 14, 15, 21, 24, 35}  # wav, mp3, ogg, flac, aiff, wavpack, m4a

# 1-based positions in the popup menu FL shows for each kind of Browser item
MENU_ITEMS = {
    'plugin': {'selected_channel': 1, 'new_channel': 2, 'playlist_track': 3},
    'audio': {'selected_channel': 1, 'new_channel': 2, 'playlist_audio_clip': 3, 'playlist_audio_track': 4,
              'playlist_track': 5},
    'other': {'selected_channel': 1, 'new_channel': 2},
}
WHERE = sorted({k for m in MENU_ITEMS.values() for k in m})


def load(fl, a):
    """Find a Browser item by path and pick an entry in its popup menu."""
    path = a['path']
    where = a.get('where', 'new_channel')
    found = fl(GOTO, path=path)
    ftype = found['file_type']
    if ftype <= -100:
        raise ValueError('%s is a folder, not something to load' % ' > '.join(path))
    kind = 'plugin' if ftype == 4 else 'audio' if ftype in AUDIO_TYPES else 'other'
    item = a.get('menu_item') or MENU_ITEMS[kind].get(where)
    if not item:
        raise ValueError('For a %s item, where must be one of %s (or give menu_item)' % (kind, list(MENU_ITEMS[kind])))
    before = fl('result = channels.channelCount()')
    if where == 'new_channel' and not a.get('menu_item') and fl(EMPTY_SAMPLER):
        # a project from the Empty template has one blank "Sampler" channel (the API can't delete it):
        # use it for the first sound instead of leaving an unused channel behind
        fl('channels.selectOneChannel(0)\nresult = 1')
        item = MENU_ITEMS[kind]['selected_channel']
    if not _load_background(fl, item):
        _load_foreground(fl, item, found)
    after = fl('result = [(i, channels.getChannelName(i)) for i in range(channels.channelCount())]')
    return {'loaded': found['focused'], 'kind': kind, 'where': where, 'channels_before': before,
            'channels_now': [{'channel': i, 'name': n} for i, n in after]}


CLOSE_MENU = 'ui.closeActivePopupMenu()\nresult = 1'
# True when the project only has the Empty template's blank Sampler channel
EMPTY_SAMPLER = ('result = channels.channelCount() == 1 and channels.getChannelName(0) == "Sampler" '
                 'and channels.getChannelType(0) == midi.CT_Sampler')
MOVE = 'for _ in range(ARGS["n"]):\n    ui.navigateBrowserMenu(1, False)\nresult = 1'


def _wait_menu(fl, secs=4.0):
    end = time.time() + secs
    while time.time() < end:
        if fl('ui.isInPopupMenu()'):
            return True
        time.sleep(0.1)
    return False


def _load_background(fl, item):
    """Open the item's menu, move with FL's menu keys and confirm with an Enter posted to the menu window.
    FL ignores ui.selectBrowserMenuItem() picks in the background, but not a posted Enter."""
    try:
        if fl('ui.isInPopupMenu()'):
            fl(CLOSE_MENU)
        fl('ui.selectBrowserMenuItem()\nresult = 1')
        if not _wait_menu(fl):
            return False
        time.sleep(0.15)
        menu = flhands._menu()
        if not menu:
            fl(CLOSE_MENU)
            return False
        fl(MOVE, n=item)
        time.sleep(0.1)
        flhands.key(menu, 'enter')
        for _ in range(20):
            time.sleep(0.1)
            if not fl('ui.isInPopupMenu()'):
                time.sleep(0.8)
                return True
        fl(CLOSE_MENU)
        return False
    except Exception:
        return False


def _load_foreground(fl, item, found):
    if not winfocus.focus_fl():
        raise RuntimeError('Could not bring FL Studio to the front; FL ignores Browser menu picks in the background.')
    time.sleep(0.2)
    fl('ui.selectBrowserMenuItem()\nresult = 1')
    if not _wait_menu(fl):
        raise RuntimeError('FL did not open the Browser menu for ' + found['focused'])
    try:
        fl(MOVE.replace('result = 1', 'ui.selectBrowserMenuItem()\nresult = 1'), n=item)
    finally:
        time.sleep(1.0)
        if fl('ui.isInPopupMenu()'):
            fl(CLOSE_MENU)

