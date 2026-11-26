"""Hands-off FL Studio control without the computer-use screen tool.

Everything here talks to FL's own windows only:
  - windows():  list FL's main window, dialogs and plugin windows
  - snap():     capture one FL window to PNG with PrintWindow (works even if FL is behind other windows)
  - click()/key()/type_text(): post mouse/keyboard messages straight to an FL window
                 (the real mouse does not move and no other app is hidden or minimized)
  - launch()/new_project(): start FL on a project file, no Open/Save As dialogs

Coordinates for click() are pixels in the window's own snapshot (top-left = 0,0, full size,
not the 2x-downscaled PNG unless scale is passed).
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import struct
import subprocess
import time
import zlib

import functools

import config
import overlay
import winfocus

u = ctypes.WinDLL('user32', use_last_error=True)
g = ctypes.WinDLL('gdi32', use_last_error=True)
try:
    u.SetProcessDPIAware()
except Exception:
    pass

FL_EXE = config.get('fl_exe')            # None if FL was not found; config.fl_exe() raises a helpful error
TEMPLATES = config.get('templates_dir') or ''
MUSIC = config.get('projects_dir')
SNAP_DIR = config.get('snap_dir')

CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u.EnumWindows.argtypes = [CB, wt.LPARAM]
u.EnumChildWindows.argtypes = [wt.HWND, CB, wt.LPARAM]
u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u.SendMessageW.restype = ctypes.c_ssize_t
u.GetWindowDC.restype = wt.HDC
u.GetWindowDC.argtypes = [wt.HWND]
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
u.GetWindow.restype = wt.HWND
u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
u.GetAncestor.restype = wt.HWND
u.ChildWindowFromPointEx.argtypes = [wt.HWND, wt.POINT, wt.UINT]
u.ChildWindowFromPointEx.restype = wt.HWND
u.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
u.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
u.VkKeyScanW.argtypes = [wt.WCHAR]
u.VkKeyScanW.restype = ctypes.c_short
g.CreateCompatibleDC.argtypes = [wt.HDC]
g.CreateCompatibleDC.restype = wt.HDC
g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g.CreateCompatibleBitmap.restype = wt.HBITMAP
g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_char_p, wt.UINT]
g.DeleteObject.argtypes = [wt.HGDIOBJ]
g.DeleteDC.argtypes = [wt.HDC]

WM_CLOSE, WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x10, 0x100, 0x101, 0x102
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x200, 0x201, 0x202, 0x203
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x204, 0x205
MK_LBUTTON, MK_RBUTTON = 1, 2
GW_OWNER = 4
GA_ROOT = 2


def _busy(f):
    """Show the 'hands off' banner (overlay.py) while f posts input to FL."""
    @functools.wraps(f)
    def wrap(*a, **k):
        with overlay.busy():
            return f(*a, **k)
    return wrap


# ---------------------------------------------------------------- finding windows
def _text(h):
    n = u.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, b, n + 1)
    return b.value


def _cls(h):
    b = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(h, b, 128)
    return b.value


def _rect(h):
    r = wt.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _pid(h):
    p = wt.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def fl_pids():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq FL64.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True).stdout
    return [int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"FL64.exe"')]


def windows(include_plugins=True):
    """FL's visible windows: main, dialogs (top-level, owned by FL) and plugin editors (children)."""
    pids = set(fl_pids())
    main = winfocus.find_fl_window()
    rows = []

    def top(h, _):
        if u.IsWindowVisible(h) and _pid(h) in pids:
            l, t, r, b = _rect(h)
            if r - l > 40 and b - t > 30 and l > -30000:
                kind = 'main' if h == main else 'dialog'
                rows.append({'hwnd': h, 'kind': kind, 'title': _text(h), 'class': _cls(h),
                             'rect': [l, t, r, b]})
        return True

    u.EnumWindows(CB(top), 0)
    if include_plugins and main:
        def child(h, _):
            if _cls(h) == 'TPluginForm' and u.IsWindowVisible(h):
                rows.append({'hwnd': h, 'kind': 'plugin', 'title': _text(h), 'class': 'TPluginForm',
                             'rect': list(_rect(h))})
            return True
        u.EnumChildWindows(main, CB(child), 0)
    rows.sort(key=lambda r: {'dialog': 0, 'plugin': 1, 'main': 2}[r['kind']])
    return rows


def dialogs():
    return [w for w in windows(False) if w['kind'] == 'dialog']


def find(title_part=None, kind=None):
    for w in windows():
        if (kind is None or w['kind'] == kind) and (title_part is None or title_part.lower() in w['title'].lower()):
            return w
    return None


def _hwnd(target):
    """Accept an hwnd, a window dict, or a title fragment ('main' = FL's main window)."""
    if isinstance(target, dict):
        return target['hwnd']
    if isinstance(target, int):
        return target
    if target in (None, 'main'):
        return winfocus.find_fl_window()
    w = find(target)
    if not w:
        raise RuntimeError('No FL window matching %r' % target)
    return w['hwnd']


# ---------------------------------------------------------------- capture
def _png(path, w, h, bgra):
    rgb = bytearray(w * h * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    stride = w * 3
    raw = b''.join(b'\x00' + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(data)


def grab(target='main'):
    """Return (width, height, BGRA bytes) of an FL window, rendered off-screen by PrintWindow."""
    h = _hwnd(target)
    l, t, r, b = _rect(h)
    w, hh = r - l, b - t
    hdc = u.GetWindowDC(h)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, hh)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(h, mdc, 2)  # PW_RENDERFULLCONTENT
    bmi = struct.pack('<IiiHHIIiiII', 40, w, -hh, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * hh * 4)
    g.GetDIBits(mdc, bmp, 0, hh, buf, bmi, 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(h, hdc)
    return w, hh, buf.raw


def snap(target='main', path=None, crop=None, scale=1):
    """Save an FL window (optionally cropped: x0,y0,x1,y1 in window pixels) to PNG. scale 2 = half size."""
    w, h, px = grab(target)
    x0, y0, x1, y1 = crop or (0, 0, w, h)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    rows = [px[(y * w + x0) * 4:(y * w + x1) * 4] for y in range(y0, y1, scale)]
    cw = x1 - x0
    if scale > 1:
        rows = [bytes(b''.join(r[i * 4:i * 4 + 4] for i in range(0, cw, scale))) for r in rows]
        cw = (cw + scale - 1) // scale
    if path is None:
        os.makedirs(SNAP_DIR, exist_ok=True)
        path = os.path.abspath(os.path.join(SNAP_DIR, 'fl_%d.png' % int(time.time() * 1000)))
    _png(path, cw, len(rows), b''.join(rows))
    return {'path': path, 'window_size': [w, h], 'crop': [x0, y0, x1, y1], 'scale': scale}


# ---------------------------------------------------------------- input (posted to FL only)
def _target_at(h, x, y):
    """Deepest child of window h under window-relative point (x, y); returns (hwnd, client x, client y)."""
    l, t, _, _ = _rect(h)
    sx, sy = l + x, t + y
    cur = h
    while True:
        p = wt.POINT(sx, sy)
        u.ScreenToClient(cur, ctypes.byref(p))
        nxt = u.ChildWindowFromPointEx(cur, p, 1 | 4)  # skip invisible + transparent
        if not nxt or nxt == cur:
            break
        cur = nxt
    p = wt.POINT(sx, sy)
    u.ScreenToClient(cur, ctypes.byref(p))
    return cur, p.x, p.y


def _lp(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


@_busy
def click(target, x, y, button='left', double=False, scale=1):
    """Click at (x, y) in an FL window's snapshot coordinates, by posting mouse messages to FL."""
    h = _hwnd(target)
    x, y = int(x * scale), int(y * scale)
    c, cx, cy = _target_at(h, x, y)
    lp = _lp(cx, cy)
    down, up, mk = ((WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON) if button == 'left'
                    else (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON))
    u.PostMessageW(c, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.03)
    u.PostMessageW(c, down, mk, lp)
    time.sleep(0.05)
    u.PostMessageW(c, up, 0, lp)
    if double:
        time.sleep(0.05)
        u.PostMessageW(c, WM_LBUTTONDBLCLK, mk, lp)
        time.sleep(0.03)
        u.PostMessageW(c, up, 0, lp)
    return {'hwnd': c, 'class': _cls(c), 'client_xy': [cx, cy]}


VK = {'enter': 0x0D, 'return': 0x0D, 'esc': 0x1B, 'escape': 0x1B, 'tab': 0x09, 'space': 0x20,
      'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'delete': 0x2E, 'backspace': 0x08,
      'home': 0x24, 'end': 0x23, 'f10': 0x79}


@_busy
def key(target, name):
    """Post one key press (e.g. 'enter', 'esc', 'down', 'y') to an FL window."""
    h = _hwnd(target)
    vk = VK.get(name.lower()) or (u.VkKeyScanW(name) & 0xFF)
    sc = u.MapVirtualKeyW(vk, 0)
    u.PostMessageW(h, WM_KEYDOWN, vk, 1 | (sc << 16))
    time.sleep(0.03)
    u.PostMessageW(h, WM_KEYUP, vk, 1 | (sc << 16) | (3 << 30))


@_busy
def type_text(target, text):
    h = _hwnd(target)
    for ch in text:
        u.PostMessageW(h, WM_CHAR, ord(ch), 1)
        time.sleep(0.01)


@_busy
def close(target):
    u.PostMessageW(_hwnd(target), WM_CLOSE, 0, 0)


# ---------------------------------------------------------------- standard Windows file dialogs
WM_SETTEXT, WM_COMMAND, BM_CLICK = 0x0C, 0x111, 0xF5
u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_void_p]
u.GetDlgCtrlID.argtypes = [wt.HWND]
u.GetParent.argtypes = [wt.HWND]
u.GetParent.restype = wt.HWND


def _children(h):
    out = []

    def cb(c, _):
        out.append(c)
        return True
    u.EnumChildWindows(h, CB(cb), 0)
    return out


def file_dialogs():
    return [w for w in dialogs() if w['class'] == '#32770']


@_busy
def fill_file_dialog(path, dialog=None, timeout=8.0):
    """Type a full path into an open Windows Open/Save dialog of FL (or a plugin) and confirm it.

    Works with the dialog in the background: the file name box is set with WM_SETTEXT and OK is
    pressed with WM_COMMAND, so nothing is typed on the real keyboard.
    """
    end = time.time() + timeout
    while dialog is None:
        ds = file_dialogs()
        if ds:
            dialog = ds[0]['hwnd']
        elif time.time() > end:
            raise RuntimeError('No file dialog open')
        else:
            time.sleep(0.2)
    dialog = _hwnd(dialog)
    edits = [c for c in _children(dialog) if _cls(c) == 'Edit']
    # the file name box sits in the ComboBoxEx32 with control id 1148 (0x47C); fall back to the first Edit
    name_box = next((e for e in edits if u.GetDlgCtrlID(u.GetParent(u.GetParent(e))) == 1148), None) or \
        next((e for e in edits if u.GetDlgCtrlID(u.GetParent(e)) == 1148), None) or (edits[0] if edits else None)
    if not name_box:
        raise RuntimeError('File name box not found in the dialog')
    u.SendMessageW(name_box, WM_SETTEXT, 0, ctypes.c_wchar_p(path))
    time.sleep(0.1)
    u.PostMessageW(dialog, WM_COMMAND, 1, 0)  # IDOK
    end = time.time() + timeout
    while time.time() < end:
        if not u.IsWindow(dialog) or not u.IsWindowVisible(dialog):
            return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------- piano roll scripts (no hotkey, no focus)
USER_PR_SCRIPTS = os.path.join(config.get('fl_user_data'), 'Settings', 'Piano roll scripts')
FL_PR_SCRIPTS = os.path.join(os.path.dirname(FL_EXE), 'System', 'Config', 'Piano roll scripts') if FL_EXE else ''


def _child(parent, cls, title_start):
    hits = []

    def cb(h, _):
        if _cls(h) == cls and _text(h).startswith(title_start) and u.IsWindowVisible(h):
            hits.append(h)
        return True
    u.EnumChildWindows(parent, CB(cb), 0)
    return hits[0] if hits else None


def _menu():
    for w in windows(False):
        if w['class'] == 'TQuickPopupMenuWindow':
            return w['hwnd']
    return None


def _highlight_y(menu, top_skip=22):
    """Centre y and height of the highlighted row of an FL popup menu (right-hand column for 2-column menus).
    Light menus highlight with a dark band; dark-themed menus (e.g. the channel rack filter) with a lighter one."""
    w, h, px = grab(menu)
    x = w - 12
    lum = [sum(px[(y * w + x) * 4:(y * w + x) * 4 + 3]) for y in range(h)]
    bg = sorted(lum)[len(lum) // 2]
    if bg >= 3 * 120:
        dark = [y for y in range(top_skip, h - 6) if lum[y] < 3 * 120]
    else:
        dark = [y for y in range(top_skip, h - 4) if lum[y] > bg + 45]
    # group into runs; the highlight is the first run taller than a few pixels (header/borders are skipped)
    runs, cur = [], []
    for y in dark:
        if cur and y != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(y)
    if cur:
        runs.append(cur)
    runs = [r for r in runs if len(r) >= 8]
    if not runs:
        return None, 19
    band = runs[0]
    return (band[0] + band[-1]) / 2, len(band)


def script_index(name):
    names = set()
    for d in (USER_PR_SCRIPTS, FL_PR_SCRIPTS):
        if os.path.isdir(d):
            names |= {f[:-9] for f in os.listdir(d) if f.lower().endswith('.pyscript')}
    order = sorted(names, key=str.lower)
    return order.index(name)


@_busy
def run_piano_roll_script(fl, name='Claude Notes'):
    """Run a piano roll script through the piano roll's Tools menu. FL can stay in the background.

    fl(code) runs code inside FL via the bridge. The piano roll must be open.
    """
    main = winfocus.find_fl_window()
    pr = _child(main, 'TEventEditForm', 'Piano roll')
    if not pr:
        raise RuntimeError('Piano roll window not found (open it first)')
    if fl('ui.isInPopupMenu()'):
        fl('ui.closeActivePopupMenu()\nresult = 1')
        time.sleep(0.2)
    click(pr, *config.cal('piano_roll_tools_icon'))  # wrench icon = Tools menu
    end = time.time() + 3
    while not fl('ui.isInPopupMenu()'):
        if time.time() > end:
            raise RuntimeError('Piano roll Tools menu did not open')
        time.sleep(0.1)
    time.sleep(0.15)
    menu = _menu()
    # the menu opens with nothing highlighted: down selects the first built-in tool, right jumps to Scripts
    fl('ui.down()\nui.right()\nresult = 1')
    time.sleep(0.15)
    y0, _ = _highlight_y(menu)
    if y0 is not None and y0 > 120:
        # FL sometimes lands on a script row (e.g. the last one) instead of the top of the Scripts column.
        # Walk up until the highlight jumps over the separator: that row is "Open script folder".
        prev, band0 = y0, 19
        for _ in range(40):
            fl('ui.up()\nresult = 1')
            time.sleep(0.12)
            y, band0 = _highlight_y(menu)
            if y is None or y > prev:            # wrapped round or lost: give up below
                prev = None
                break
            if prev - y > 1.2 * band0:            # crossed the separator -> on "Open script folder"
                prev = y
                break
            prev = y
        if prev is None or prev > 120:
            fl('ui.closeActivePopupMenu()\nresult = 1')
            raise RuntimeError('Could not read the Tools menu highlight')
        downs = script_index(name) + 1
        fl('for _ in range(%d):\n    ui.down()\nresult = 1' % downs)
        time.sleep(0.1)
        key(menu, 'enter')
        time.sleep(0.3)
        if fl('ui.isInPopupMenu()'):
            fl('ui.enter()\nresult = 1')
        return True
    fl('ui.down()\nresult = 1')
    time.sleep(0.15)
    y1, band = _highlight_y(menu)
    if y0 is None or y1 is None or y0 > 120:
        fl('ui.closeActivePopupMenu()\nresult = 1')
        raise RuntimeError('Could not read the Tools menu highlight')
    # rows are ~1 highlight-height apart; the separator after "Open script folder" adds ~half a row.
    # A bigger jump means y0 was "Open script folder" and we now sit on the first script;
    # otherwise y0 was "Run last script again" (enabled after the first run) and we are on "Open script folder".
    if y1 - y0 < 1.2 * band:
        fl('ui.down()\nresult = 1')
    downs = script_index(name)
    if downs:
        fl('for _ in range(%d):\n    ui.down()\nresult = 1' % downs)
    time.sleep(0.1)
    # ui.enter() is ignored while FL is in the background; an Enter posted to the menu window is not
    key(menu, 'enter')
    time.sleep(0.3)
    if fl('ui.isInPopupMenu()'):
        fl('ui.enter()\nresult = 1')
    return True


# ---------------------------------------------------------------- projects
def template_path(name='Empty'):
    if not os.path.isdir(TEMPLATES):
        raise FileNotFoundError('FL template folder not found (%r). Set "templates_dir" or "fl_exe" in %s'
                                % (TEMPLATES, config.CONFIG_FILE))
    for root, _, files in os.walk(TEMPLATES):
        for f in files:
            if f.lower() == name.lower() + '.flp':
                return os.path.join(root, f)
    raise FileNotFoundError('No FL template named %r' % name)


@_busy
def launch(project=None, wait=60):
    """Start FL (optionally on a .flp). If FL is already running, the file is handed to that instance."""
    # Start it through Explorer (a throwaway .lnk to FL64.exe + the project), never as Claude's child process:
    # apps started from Claude's shell die when Claude closes. .flp may have no file association, so the
    # shortcut carries the project path as FL's argument.
    exe = config.fl_exe()
    lnk = os.path.join(config.DATA_DIR, 'open_fl.lnk')
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    target_args = ('"%s"' % os.path.abspath(project)) if project else ''
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:L); $s.TargetPath=$env:T; "
          "$s.Arguments=$env:A; $s.WorkingDirectory=$env:W; $s.Save()")
    env = dict(os.environ, L=lnk, T=exe, A=target_args, W=os.path.dirname(exe))
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], env=env, check=True,
                   creationflags=0x08000000)  # CREATE_NO_WINDOW
    subprocess.Popen(['explorer.exe', lnk])
    end = time.time() + wait
    while time.time() < end:
        if winfocus.find_fl_window():
            return True
        time.sleep(1)
    return False


def wait_bridge(fl, timeout=90):
    """Wait until the Claude Bridge script answers again (after FL starts)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fl('result = 1') == 1:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


@_busy
def close_fl(fl, unsaved='refuse', timeout=60):
    """Close FL cleanly. unsaved: 'refuse' (error if there are unsaved changes), 'save' or 'discard'."""
    main = winfocus.find_fl_window()
    if not main:
        return True
    changed = 0
    try:
        changed = fl('result = general.getChangedFlag()')
    except Exception:
        pass
    if changed and unsaved == 'refuse':
        raise RuntimeError('FL has unsaved changes in "%s". Pass unsaved="save" or "discard".' % _text(main))
    if changed and unsaved == 'save':
        fl('transport.globalTransport(midi.FPT_Save, 1)\nresult = 1')
        time.sleep(2)
        if file_dialogs():
            raise RuntimeError('FL asked where to save (project never saved). Save it first or use unsaved="discard".')
    pids = fl_pids()
    close(main)
    end = time.time() + timeout
    while time.time() < end:
        alive = [p for p in fl_pids() if p in pids]
        if not alive:
            return True
        for d in dialogs():
            # "Save changes to ...?" - answer it the way we were told; FL's message box takes Y/N keys
            if d['class'] == 'TMsgForm':
                key(d['hwnd'], 'n' if unsaved == 'discard' or not changed else 'y')
        time.sleep(0.5)
    raise RuntimeError('FL did not close within %ds' % timeout)


@_busy
def open_project(fl, path, unsaved='refuse'):
    """Open a .flp with no Open dialog: close FL cleanly if it is running, then start it on the file."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    close_fl(fl, unsaved)
    time.sleep(1)
    launch(path)
    name = os.path.splitext(os.path.basename(path))[0]
    end = time.time() + 90
    while time.time() < end:
        m = winfocus.find_fl_window()
        if m and name.lower() in _text(m).lower():
            break
        time.sleep(1)
    ok = wait_bridge(fl)
    if ok:
        try:
            focus_browser(fl)
        except Exception:
            pass
    return {'opened': path, 'title': _text(winfocus.find_fl_window() or 0), 'bridge': ok,
            'dialogs': [d['title'] for d in dialogs()]}


def focus_browser(fl):
    """After a project opens, FL's Browser has no focused node and every Browser call fails ('Not found in
    Browser'). One posted click on its first row fixes that."""
    if fl('result = ui.getFocusedNodeCaption()'):
        return True
    main = winfocus.find_fl_window()
    tree = _child(main, 'TDataBrowserTree', '')
    if not tree:
        return False
    click(tree, *config.cal('browser_first_row'))
    time.sleep(0.3)
    return bool(fl('result = ui.getFocusedNodeCaption()'))


def song_mode(fl):
    """Switch the transport to song mode (renders in pattern mode skip every playlist audio clip)."""
    return fl('if transport.getLoopMode() == 0:\n    transport.setLoopMode()\nresult = transport.getLoopMode()')


def new_project(name, template='Empty', folder=None):
    """Copy an FL template to <music>/<name>/<name>.flp. Opening it then lets plain Save work (no dialog)."""
    folder = folder or os.path.join(MUSIC, name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + '.flp')
    if os.path.exists(path):
        raise FileExistsError(path)
    shutil.copyfile(template_path(template), path)
    return path


if __name__ == '__main__':
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'snap':
        print(json.dumps(snap(sys.argv[2] if len(sys.argv) > 2 else 'main', scale=2)))
    else:
        for w in windows():
            print(json.dumps(w))
