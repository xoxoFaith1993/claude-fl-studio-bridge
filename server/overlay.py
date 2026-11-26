"""'Hands off' banner shown while Claude drives FL hands-off.

The user sees it and keeps their hands off the mouse/keys in FL until it fades. Change the default text
("Chill bro, let me solo her...") or turn the banner off in config.json:
    "banner": {"enabled": true, "text": "...", "subtext": "...", "hold_seconds": 90, "color": "#C8283C"}

How it works:
  - touch() / busy(): the bridge marks itself busy by touching a heartbeat file and starts the
    overlay process if it is not running (one per user session, guarded by a named mutex).
  - The overlay process (this file run with pythonw) shows a big banner in the middle of FL's monitor
    from the first beat until release() - run by Claude Code's Stop and UserPromptSubmit hooks, so
    it stays up for Claude's whole turn, also while Claude thinks between FL actions. Safety net:
    it fades by itself HOLD seconds after the last beat if no release ever comes.
  - The banner is click-through, never takes focus and is not in the taskbar, so nothing else
    (a video on another screen, say) moves or minimizes. It quits by itself after IDLE_EXIT of no use.
  - Turn it off with "banner": {"enabled": false} in config.json or the environment variable FL_OVERLAY=0.
"""
import contextlib
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

_B = config.get('banner')
ENABLED = bool(_B.get('enabled', True))
TEXT = _B.get('text', 'Chill bro, let me solo her...')
SUB = _B.get('subtext', 'Claude is working in FL Studio - hands off the mouse and keys')
COLOR = int(str(_B.get('color', '#C8283C')).lstrip('#'), 16)
BEAT = os.path.join(tempfile.gettempdir(), 'fl_claude_overlay.beat')
RELEASE = os.path.join(tempfile.gettempdir(), 'fl_claude_overlay.release')
MUTEX = 'Local\\FLClaudeOverlay'
HOLD = int(_B.get('hold_seconds', 90))   # banner stays at most this long after the last beat when no release comes
IDLE_EXIT = 15 * 60     # overlay process quits after this long unused

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
k32.OpenMutexW.restype = wt.HANDLE
k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
k32.CreateMutexW.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
SYNCHRONIZE = 0x00100000


# ------------------------------------------------------------------ client side (bridge)

_spawned = 0.0


def _running():
    h = k32.OpenMutexW(SYNCHRONIZE, False, MUTEX)
    if h:
        k32.CloseHandle(h)
        return True
    return False


def _spawn():
    global _spawned
    if time.time() - _spawned < 5 or _running():
        return
    _spawned = time.time()
    exe = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.exists(exe):
        exe = sys.executable
    args = [exe, os.path.abspath(__file__), 'run']
    base = 0x00000008 | 0x00000200 | 0x08000000      # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    for flags in (base | 0x01000000, base):          # try to break away from the server's job first
        try:
            subprocess.Popen(args, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


def touch():
    """Mark Claude as busy in FL right now (banner shows until release())."""
    if not ENABLED:
        return
    try:
        with open(BEAT, 'a'):
            pass
        os.utime(BEAT, None)
        _spawn()
    except Exception:
        pass


def release():
    """Claude's turn is over: fade the banner out (the next touch() brings it back)."""
    try:
        with open(RELEASE, 'a'):
            pass
        os.utime(RELEASE, None)
    except Exception:
        pass


@contextlib.contextmanager
def busy():
    """Keep the banner up for the whole with-block (beats every 0.5 s from a helper thread)."""
    stop = threading.Event()

    def beat():
        while not stop.is_set():
            touch()
            stop.wait(0.5)
    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        touch()


# ------------------------------------------------------------------ overlay process

def run():
    u = ctypes.WinDLL('user32', use_last_error=True)
    g = ctypes.WinDLL('gdi32', use_last_error=True)
    try:
        u.SetProcessDPIAware()
    except Exception:
        pass
    mutex = k32.CreateMutexW(None, False, MUTEX)
    if ctypes.get_last_error() == 183:                # ERROR_ALREADY_EXISTS: one banner is enough
        return
    _ = mutex

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [('cbSize', wt.UINT), ('style', wt.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                    ('cbWndExtra', ctypes.c_int), ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON),
                    ('hCursor', wt.HANDLE), ('hbrBackground', wt.HBRUSH), ('lpszMenuName', wt.LPCWSTR),
                    ('lpszClassName', wt.LPCWSTR), ('hIconSm', wt.HICON)]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [('hdc', wt.HDC), ('fErase', wt.BOOL), ('rcPaint', wt.RECT), ('fRestore', wt.BOOL),
                    ('fIncUpdate', wt.BOOL), ('rgbReserved', ctypes.c_byte * 32)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]

    u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    u.DefWindowProcW.restype = LRESULT
    u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
    u.CreateWindowExW.restype = wt.HWND
    u.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, wt.BYTE, wt.DWORD]
    u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    u.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
    u.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u.BeginPaint.restype = wt.HDC
    u.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
    u.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT]
    u.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
    u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    u.MonitorFromWindow.restype = wt.HMONITOR
    u.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
    u.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
    u.DestroyWindow.argtypes = [wt.HWND]
    u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    g.CreateSolidBrush.argtypes = [wt.COLORREF]
    g.CreateSolidBrush.restype = wt.HBRUSH
    g.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR]
    g.CreateFontW.restype = wt.HFONT
    g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    g.SelectObject.restype = wt.HGDIOBJ
    g.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
    g.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
    g.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    g.CreateRoundRectRgn.restype = wt.HRGN
    g.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
    u.GetDC.restype = wt.HDC
    u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

    def rgb(r, gg, b):
        return r | (gg << 8) | (b << 16)

    dc0 = u.GetDC(None)
    scale = g.GetDeviceCaps(dc0, 90) / 96.0       # LOGPIXELSY
    u.ReleaseDC(None, dc0)
    W, H, R, BORDER = int(1120 * scale), int(112 * scale), int(30 * scale), max(3, int(3 * scale))

    red_brush = g.CreateSolidBrush(rgb((COLOR >> 16) & 0xFF, (COLOR >> 8) & 0xFF, COLOR & 0xFF))
    bg_brush = g.CreateSolidBrush(rgb(0x16, 0x12, 0x14))
    big = g.CreateFontW(-int(52 * scale), 0, 0, 0, 800, 0, 0, 0, 0, 0, 0, 5, 0, 'Segoe UI')
    small = g.CreateFontW(-int(20 * scale), 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 5, 0, 'Segoe UI')
    DT = 0x1 | 0x4 | 0x20                          # DT_CENTER | DT_VCENTER | DT_SINGLELINE

    state = {'alpha': 0, 'shown': False, 'last_use': time.time()}

    def fl_monitor():
        try:
            import winfocus
            h = winfocus.find_fl_window()
        except Exception:
            h = None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        u.GetMonitorInfoW(u.MonitorFromWindow(h, 1), ctypes.byref(mi))   # 1 = default to primary
        return mi.rcMonitor

    def place(hwnd):
        rc = fl_monitor()
        x = rc.left + (rc.right - rc.left - W) // 2
        y = rc.top + (rc.bottom - rc.top - H) // 2    # dead centre of FL's monitor
        u.SetWindowPos(hwnd, wt.HWND(-1), x, y, W, H, 0x0010 | 0x0040)   # TOPMOST, NOACTIVATE | SHOWWINDOW

    def wanted():
        try:
            beat = os.path.getmtime(BEAT)
        except OSError:
            return False
        try:
            rel = os.path.getmtime(RELEASE)
        except OSError:
            rel = 0.0
        return beat > rel and time.time() - beat < HOLD

    def paint(hwnd):
        ps = PAINTSTRUCT()
        dc = u.BeginPaint(hwnd, ctypes.byref(ps))
        rc = wt.RECT(0, 0, W, H)
        u.FillRect(dc, ctypes.byref(rc), red_brush)
        inner = wt.RECT(BORDER, BORDER, W - BORDER, H - BORDER)
        u.FillRect(dc, ctypes.byref(inner), bg_brush)
        g.SetBkMode(dc, 1)                         # TRANSPARENT
        g.SelectObject(dc, big)
        g.SetTextColor(dc, rgb(0xF2, 0x4A, 0x5E))
        top = wt.RECT(0, int(6 * scale), W, int(76 * scale))
        u.DrawTextW(dc, TEXT, -1, ctypes.byref(top), DT)
        g.SelectObject(dc, small)
        g.SetTextColor(dc, rgb(0xB8, 0xB0, 0xB4))
        bottom = wt.RECT(0, int(72 * scale), W, int(104 * scale))
        u.DrawTextW(dc, SUB, -1, ctypes.byref(bottom), DT)
        u.EndPaint(hwnd, ctypes.byref(ps))

    def tick(hwnd):
        want = wanted()
        if want:
            state['last_use'] = time.time()
            if not state['shown']:
                place(hwnd)
                state['shown'] = True
            state['alpha'] = min(235, state['alpha'] + 47)
        elif state['shown']:
            state['alpha'] = max(0, state['alpha'] - 30)
            if state['alpha'] == 0:
                u.ShowWindow(hwnd, 0)              # SW_HIDE
                state['shown'] = False
        if state['shown']:
            u.SetLayeredWindowAttributes(hwnd, 0, state['alpha'], 2)   # LWA_ALPHA
        if not state['shown'] and time.time() - state['last_use'] > IDLE_EXIT:
            u.DestroyWindow(hwnd)

    def proc(hwnd, msg, wp, lp):
        if msg == 0x000F:                          # WM_PAINT
            paint(hwnd)
            return 0
        if msg == 0x0113:                          # WM_TIMER
            tick(hwnd)
            return 0
        if msg == 0x0084:                          # WM_NCHITTEST: let clicks fall through
            return -1                              # HTTRANSPARENT
        if msg == 0x0021:                          # WM_MOUSEACTIVATE
            return 3                               # MA_NOACTIVATE
        if msg == 0x0002:                          # WM_DESTROY
            u.PostQuitMessage(0)
            return 0
        return u.DefWindowProcW(hwnd, msg, wp, lp)

    wndproc = WNDPROC(proc)
    hinst = k32.GetModuleHandleW(None)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.lpszClassName = 'FLClaudeOverlay'
    u.RegisterClassExW(ctypes.byref(wc))
    # LAYERED | TRANSPARENT (click-through) | TOPMOST | TOOLWINDOW (no taskbar) | NOACTIVATE
    ex = 0x00080000 | 0x00000020 | 0x00000008 | 0x00000080 | 0x08000000
    hwnd = u.CreateWindowExW(ex, 'FLClaudeOverlay', 'Claude working', 0x80000000, 0, 0, W, H,
                             None, None, hinst, None)                  # WS_POPUP, created hidden
    u.SetWindowRgn(hwnd, g.CreateRoundRectRgn(0, 0, W + 1, H + 1, R, R), True)
    u.SetLayeredWindowAttributes(hwnd, 0, 0, 2)
    u.SetTimer(hwnd, 1, 40, None)
    msg = wt.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


if __name__ == '__main__':
    if sys.argv[1:] == ['run']:
        run()
    elif sys.argv[1:] == ['release']:
        release()
    elif sys.argv[1:2] == ['demo']:
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5
        with busy():
            time.sleep(secs)
        release()
        print('banner shown for %.0f s' % secs)
    else:
        print(__doc__)
