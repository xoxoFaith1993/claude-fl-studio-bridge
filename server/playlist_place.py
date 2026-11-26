"""Place a pattern clip on FL's playlist at a given track and bar - hands-off, any zoom or scroll.

FL's script API cannot add playlist clips, so this posts a click to the Playlist window. To know where to
click it never guesses pixels: it hovers (posted mouse moves) and reads FL's hint line, which shows
  - the track name over a track header,
  - the song position ("8:02:21" = bar 8, step 2, tick 21) over empty grid,
  - "1:01:00 for 8:00:00" (start for length) over an existing clip.
It scrolls with ui.scrollWindow (bars) and posted mouse-wheel messages (tracks), checks the spot is free,
clicks, then reads the new clip back and undoes the click if it landed in the wrong place.
"""
import re
import time

import flhands

u = flhands.u
TIME = re.compile(r'^(\d+):(\d+):(\d+)$')
CLIP = re.compile(r'^(\d+):(\d+):(\d+) for (\d+):(\d+):(\d+)$')
WM_MOUSEMOVE, WM_MOUSEWHEEL = 0x200, 0x20A


def _beats(bar, step, tick):  # FL hint: 16 steps per bar (4/4), 24 ticks per step
    return (bar - 1) * 4 + (step - 1) / 4 + tick / 96


class Playlist:
    def __init__(self, fl):
        self.fl = fl
        fl('ui.showWindow(midi.widPlaylist)\nresult = 1')
        time.sleep(0.2)
        self.hwnd = flhands._child(flhands.winfocus.find_fl_window(), 'TEventEditForm', 'Playlist')
        if not self.hwnd:
            raise RuntimeError('Playlist window not found')
        l, t, r, b = flhands._rect(self.hwnd)
        self.left, self.top, self.w, self.h = l, t, r - l, b - t
        self.names = fl('ui.setHintMsg("~")\n'
                        'result = [playlist.getTrackName(i) for i in range(1, playlist.trackCount() + 1)]')

    # -- probing
    def hint(self, x, y, wait=0.08):
        # one round trip per probe: read the hint and reset it to "~" so the next probe can tell
        # "no hint here" from a stale one
        u.PostMessageW(self.hwnd, WM_MOUSEMOVE, 0, flhands._lp(int(x), int(y)))
        time.sleep(wait)
        h = self.fl('h = ui.getHintMsg()\nui.setHintMsg("~")\nresult = h')
        return None if h == '~' else h

    def track_at(self, y):
        h = self.hint(self.hx, y)
        return h if h in self.names else None

    def wheel(self, notches):
        c, _, _ = flhands._target_at(self.hwnd, self.gx + 50, self.h // 2)
        u.PostMessageW(c, WM_MOUSEWHEEL, ((-120 * notches) & 0xFFFF) << 16,
                       flhands._lp(self.left + self.gx + 50, self.top + self.h // 2))
        time.sleep(0.3)

    # -- geometry
    def find_columns(self):
        """Header x (track names) and grid start x, scanning along the middle of the window."""
        y = self.h // 2
        self.hx = self.gx = None
        for x in range(40, self.w // 2, 20):
            h = self.hint(x, y)
            if self.hx is None and h in self.names:
                self.hx = x + 6
            elif self.hx is not None and h and (TIME.match(h) or CLIP.match(h)):
                self.gx = x
                break
        if self.hx is None or self.gx is None:
            raise RuntimeError('Could not find the playlist track headers/grid')

    def find_rows(self):
        """Top of the first visible track and the row height (tracks assumed equal height)."""
        def first_change(y0, name0, step):
            y, prev = y0, y0
            while y < self.h - 10:
                y += step
                n = self.track_at(y)
                if n and n != name0:
                    lo, hi = prev, y  # binary search the boundary
                    while hi - lo > 1:
                        m = (lo + hi) // 2
                        if self.track_at(m) == name0:
                            lo = m
                        else:
                            hi = m
                    return hi, n
                if n == name0:
                    prev = y
            return None, None
        y0 = 30
        while y0 < self.h // 2 and not self.track_at(y0):
            y0 += 6
        name0 = self.track_at(y0)
        b1, name1 = first_change(y0, name0, 12)
        b2, _ = first_change(b1 + 2, name1, 12)
        if b1 is None or b2 is None:
            raise RuntimeError('Could not measure playlist rows')
        self.rh = b2 - b1
        idx1 = self._index_of(name1, b1)
        self.row1_top, self.row1_index = b1, idx1   # top pixel of track idx1 (1-based)

    def _index_of(self, name, y_top):
        cands = [i + 1 for i, n in enumerate(self.names) if n == name]
        if len(cands) == 1:
            return cands[0]
        # duplicate names: use the next row's name to pick
        nxt = self.track_at(y_top + self.rh * 1.5) if hasattr(self, 'rh') else None
        for c in cands:
            if c < len(self.names) and self.names[c] == nxt:
                return c
        return cands[0]

    def row_center(self, track):
        return self.row1_top + (track - self.row1_index) * self.rh + self.rh / 2

    def ensure_track_visible(self, track):
        per_notch = None
        for _ in range(12):
            y = self.row_center(track)
            if 40 < y < self.h - self.rh:
                return y
            # aim for the target row to end up about a third of the way down
            rows_off = (y - self.h / 3) / self.rh
            notches = 3 if per_notch is None else max(1, round(abs(rows_off) / per_notch))
            notches = notches if rows_off > 0 else -notches
            first_before = self.row1_index - (self.row1_top - 60) / self.rh
            self.wheel(notches)
            time.sleep(0.35)
            self.find_rows()
            first_after = self.row1_index - (self.row1_top - 60) / self.rh
            moved = abs(first_after - first_before)
            if moved > 0.5:
                per_notch = moved / abs(notches)
        raise RuntimeError('Could not scroll to track %d' % track)

    def time_fit(self, y):
        """Linear map x -> beats from two empty-grid probes. The map is the same on every row, so if row y
        is full of clips, rows below (then above) are tried."""
        rh = getattr(self, 'rh', 48)
        for yy in [y] + [y + k * rh for k in range(1, 8)] + [y - k * rh for k in range(1, 4)]:
            if not (20 < yy < self.h - 10):
                continue
            pts = []
            for frac in (0.1, 0.9, 0.3, 0.7, 0.5, 0.2, 0.8, 0.4, 0.6):
                x = self.gx + 10 + frac * (self.w - self.gx - 60)
                m = TIME.match(self.hint(x, yy) or '')
                if m:
                    pts.append((x, _beats(*map(int, m.groups()))))
                if len(pts) >= 2 and abs(pts[-1][0] - pts[0][0]) > 100:
                    break
            if len(pts) >= 2 and abs(pts[-1][0] - pts[0][0]) > 50:
                break
        if len(pts) < 2:
            raise RuntimeError('Could not read song positions (playlist rows are full of clips)')
        (x1, b1), (x2, b2) = pts[0], pts[-1]
        self.ppb = (x2 - x1) / (b2 - b1)          # pixels per beat
        self.x0 = x1 - b1 * self.ppb              # x of beat 0 (bar 1)

    def stable_fit(self, y, tries=8):
        """FL scrolls smoothly: refit until two fits agree, so we never measure a moving view."""
        prev = None
        for _ in range(tries):
            time.sleep(0.25)
            self.time_fit(y)
            if prev and abs(prev[0] - self.x0) < 1.5 and abs(prev[1] - self.ppb) < 0.05:
                return
            prev = (self.x0, self.ppb)

    def x_of(self, beat):
        return self.x0 + beat * self.ppb


def _locate(fl, track, bar):
    pl = Playlist(fl)
    if not 1 <= track <= len(pl.names):
        raise ValueError('track must be 1..%d' % len(pl.names))
    pl.find_columns()
    pl.find_rows()
    y = pl.ensure_track_visible(track)
    beat = (bar - 1) * 4
    pl.stable_fit(y)
    x = pl.x_of(beat)
    if not (pl.gx - 22 < x < pl.w - 120):
        fl('ui.scrollWindow(midi.widPlaylist, ARGS["b"], 1)\nresult = 1', b=max(1, int(bar) - 1))
        pl.stable_fit(y)
        x = pl.x_of(beat)
        if not (pl.gx - 22 < x < pl.w - 40):
            raise RuntimeError('Could not scroll bar %d into view' % bar)
    return pl, x, y


def place_many(fl, pattern, track, bars, allow_overlap=False):
    """Copy a pattern to many bars on one track (a common trap layout: short patterns pasted along the
    playlist). The playlist is measured once; each copy then costs one click + one hint read."""
    if isinstance(pattern, str):
        found = fl('result = [i for i in range(1, patterns.patternCount() + 1) '
                   'if patterns.getPatternName(i) == ARGS["n"]]', n=pattern)
        if not found:
            raise ValueError('No pattern named %r' % pattern)
        pattern = found[0]
    fl('patterns.jumpToPattern(ARGS["p"])\nresult = 1', p=pattern)
    pat_beats = (fl('result = patterns.getPatternLength(ARGS["p"])', p=pattern) or 16) / 4
    pl, x, y = _locate(fl, track, min(bars))
    done = []
    for bar in sorted(bars):
        beat = (bar - 1) * 4
        x = pl.x_of(beat)
        if not (pl.gx - 22 < x < pl.w - 40):
            fl('ui.scrollWindow(midi.widPlaylist, ARGS["b"], 1)\nresult = 1', b=max(1, int(bar) - 1))
            pl.stable_fit(y)
            x = pl.x_of(beat)
        h = pl.hint(x + 3, y) or ''
        if CLIP.match(h) and not allow_overlap:
            done.append((bar, 'skipped: clip already there (%s)' % h))
            continue
        # not too close to the bar line: the previous clip's right-edge resize handle sits right there
        cx = x + max(5, min(8, pl.ppb * 0.25))
        # a freshly placed clip stays selected, and the next click on empty space would only deselect it
        fl('playlist.deselectAll()\nresult = 1')
        flhands.click(pl.hwnd, cx, y)
        time.sleep(0.25)
        pl.hint(cx + 25, y, wait=0.05)                    # move off, then back: forces a fresh hint
        m = CLIP.match(pl.hint(cx + 6, y, wait=0.15) or '')
        for _ in range(2):                                # a real mouse move over FL overwrites the hint: re-read
            if m:
                break
            time.sleep(0.3)
            pl.hint(cx + 25, y, wait=0.05)
            m = CLIP.match(pl.hint(cx + 6, y, wait=0.25) or '')
        whole = abs(bar - round(bar)) < 1e-6
        ok = bool(m) and (not whole or int(m.group(1)) == int(round(bar)))
        done.append((bar, m.group(0) if m else 'MISSING'))
        if m and len([d for d in done if not str(d[1]).startswith('skipped')]) == 1:
            # FL paints at the length of the last clip that was resized: fix the first copy to the pattern's
            # real length (later copies then follow it)
            want = pat_beats / 4
            have = int(m.group(4)) + int(m.group(5)) / 16 + int(m.group(6)) / 384
            if abs(have - want) > 0.01:
                trim(fl, track, bar, bar + want)
                pl, x, y = _locate(fl, track, bar)
                done[-1] = (bar, '%s -> length fixed to %g bars' % (done[-1][1], want))
        if not ok:
            raise RuntimeError('Copy at bar %s did not land (%s)' % (bar, done[-1][1]))
    return {'pattern': pattern, 'track': track, 'placed': done,
            'song_length_bars': fl('result = transport.getSongLength(midi.SONGLENGTH_BARS)')}


# Playlist toolbar buttons (x, y in the Playlist window, FL 2026 default layout)
TOOL = {'draw': (70, 11), 'paint': (87, 11), 'delete': (108, 11), 'slice': (173, 11)}
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x201, 0x202


def _clip_at(pl, bar, y):
    """(start_bar, length_bars) of the clip covering `bar` on row y, or None."""
    x = pl.x_of((bar - 1) * 4)
    pl.hint(x + 25, y, wait=0.04)                         # move off first: fresh hint
    m = CLIP.match(pl.hint(x, y, wait=0.15) or '')
    if not m:
        return None
    sb, ss, st, lb, ls, lt = map(int, m.groups())
    return (sb + (ss - 1) / 16 + st / 384, lb + ls / 16 + lt / 384)


def _show_bars(fl, pl, y, bars):
    """Scroll horizontally until every bar in `bars` is on screen (drag/slice points must be visible)."""
    def visible():
        return all(pl.gx + 6 < pl.x_of((b - 1) * 4) < pl.w - 40 for b in bars)
    if visible():
        return
    fl('ui.scrollWindow(midi.widPlaylist, ARGS["b"], 1)\nresult = 1', b=max(1, int(min(bars)) - 1))
    pl.stable_fit(y)
    if not visible():
        raise RuntimeError('Bars %s do not fit on screen at this zoom' % (bars,))


def trim(fl, track, bar, new_end_bar):
    """Shorten the clip covering `bar` so it ends at new_end_bar (drag its right edge, like resizing by hand)."""
    pl, x, y = _locate(fl, track, bar)
    clip = _clip_at(pl, bar + 0.05, y)                   # +0.05: never read exactly on a boundary
    if not clip:
        raise RuntimeError('No clip at bar %s on track %d' % (bar, track))
    start, length = clip
    end = start + length
    if not new_end_bar > start or abs(new_end_bar - end) < 1e-6:
        raise ValueError('new end %s must be after the clip start %s and differ from its end %s'
                         % (new_end_bar, start, end))
    _show_bars(fl, pl, y, [new_end_bar, end])
    xe, xt = pl.x_of((end - 1) * 4), pl.x_of((new_end_bar - 1) * 4)
    fl('playlist.deselectAll()\nresult = 1')
    c, cx, cy = flhands._target_at(pl.hwnd, int(xe - 2), int(y))
    lp = lambda xx: flhands._lp(int(round(xx)), int(cy))  # noqa: E731
    flhands.u.PostMessageW(c, 0x200, 0, lp(cx))
    time.sleep(0.05)
    flhands.u.PostMessageW(c, WM_LBUTTONDOWN, 1, lp(cx))
    time.sleep(0.08)
    for i in range(1, 9):
        flhands.u.PostMessageW(c, 0x200, 1, lp(cx + (xt - (xe - 2)) * i / 8))
        time.sleep(0.03)
    flhands.u.PostMessageW(c, WM_LBUTTONUP, 0, lp(cx + (xt - (xe - 2))))
    time.sleep(0.35)
    got = _clip_at(pl, start + 0.1, y)
    if not got or abs(got[0] + got[1] - new_end_bar) > 0.02:
        raise RuntimeError('Trim did not land: clip is now %s' % (got,))
    return {'track': track, 'clip_start_bar': start, 'end_bar': round(got[0] + got[1], 3)}


def cut(fl, track, from_bar, to_bar):
    """Cut a gap out of a clip (e.g. an 808 drop-out): slice at from_bar and to_bar, delete the piece in between.
    If to_bar is the clip's end, the clip is simply trimmed."""
    pl, x, y = _locate(fl, track, from_bar)
    clip = _clip_at(pl, from_bar + 0.01, y)
    if not clip:
        raise RuntimeError('No clip at bar %s on track %d' % (from_bar, track))
    start, length = clip
    end = start + length
    if abs(to_bar - end) < 0.01:
        return trim(fl, track, start + 0.01, from_bar)
    head = abs(from_bar - start) < 0.01                    # removing the start of the clip (e.g. a late kick)
    _show_bars(fl, pl, y, [from_bar + 0.05, to_bar + 0.1])
    fl('playlist.deselectAll()\nresult = 1')
    try:
        flhands.click(pl.hwnd, *TOOL['slice'])
        time.sleep(0.25)
        for b in ((to_bar,) if head else (from_bar, to_bar)):
            flhands.click(pl.hwnd, pl.x_of((b - 1) * 4), y)
            time.sleep(0.25)
        flhands.click(pl.hwnd, *TOOL['delete'])
        time.sleep(0.25)
        flhands.click(pl.hwnd, pl.x_of(((from_bar + to_bar) / 2 - 1) * 4), y)
        time.sleep(0.3)
    finally:
        flhands.click(pl.hwnd, *TOOL['paint'])           # always go back to painting clips
        time.sleep(0.25)
    left = None if head else _clip_at(pl, from_bar - 0.05, y)
    gap = _clip_at(pl, (from_bar + to_bar) / 2, y)
    right = _clip_at(pl, to_bar + 0.05, y)
    if gap or (not left and not head) or not right:
        raise RuntimeError('Cut did not land as expected: left %s, gap %s, right %s' % (left, gap, right))
    return {'track': track, 'left': left, 'gap': [from_bar, to_bar], 'right': right}


def remove(fl, track, bar):
    """Delete the clip that starts at `bar` on `track` (right-click with the draw tool)."""
    pl, x, y = _locate(fl, track, bar)
    cx = x + max(3, min(8, pl.ppb * 0.3))
    h = pl.hint(cx, y) or ''
    m = CLIP.match(h)
    if not m or tuple(map(int, m.groups()[:3])) != (bar, 1, 0):
        raise RuntimeError('No clip starting at bar %d on track %d (hint: %s)' % (bar, track, h))
    flhands.click(pl.hwnd, cx, y, button='right')
    time.sleep(0.5)
    h2 = pl.hint(cx + 5, y, wait=0.2) or ''
    if CLIP.match(h2) and tuple(map(int, CLIP.match(h2).groups()[:3])) == (bar, 1, 0):
        raise RuntimeError('Clip is still there (hint: %s)' % h2)
    return {'removed': h, 'track': track, 'track_name': pl.names[track - 1],
            'song_length_bars': fl('result = transport.getSongLength(midi.SONGLENGTH_BARS)')}


def place(fl, pattern, track, bar, allow_overlap=False):
    """Put `pattern` (1-based number or name) on playlist `track` (1-based) starting at `bar` (1-based)."""
    if isinstance(pattern, str):
        pattern = fl('result = [i for i in range(1, patterns.patternCount() + 1) '
                     'if patterns.getPatternName(i) == ARGS["n"]]', n=pattern)
        if not pattern:
            raise ValueError('No pattern with that name')
        pattern = pattern[0]
    length_steps = fl('result = patterns.getPatternLength(ARGS["p"])', p=pattern) or 16
    length_beats = length_steps / 4
    fl('patterns.jumpToPattern(ARGS["p"])\nresult = 1', p=pattern)
    pl, x, y = _locate(fl, track, bar)
    beat = (bar - 1) * 4
    # the spot (whole clip length, as far as visible) must be free
    if not allow_overlap:
        end_x = min(pl.x_of(beat + length_beats) - 2, pl.w - 30)
        n = max(2, int((end_x - x) / 25))
        for i in range(n + 1):
            px = x + 2 + (end_x - x - 2) * i / n
            h = pl.hint(px, y) or ''
            if CLIP.match(h):
                raise RuntimeError('Track %d already has a clip at %s (bar %d). Use allow_overlap or another '
                                   'track/bar.' % (track, h, bar))
    song_before = fl('result = transport.getSongLength(midi.SONGLENGTH_ABSTICKS)')
    cx = x + max(1.5, min(4, pl.ppb * 0.1))
    fl('playlist.deselectAll()\nresult = 1')   # a selected clip would turn this click into a deselect
    flhands.click(pl.hwnd, cx, y)
    time.sleep(0.5)
    want = (bar, 1, 0)
    m, seen = None, []
    # read the new clip back; the hint only refreshes when the pointer moves, so try a few nearby points
    for dx in (6, 12, pl.ppb * 1.5, pl.ppb * 0.6, 20):
        if cx + dx > pl.w - 30:
            continue
        h = pl.hint(cx + dx, y, wait=0.2) or ''
        seen.append(h)
        m = CLIP.match(h)
        if m:
            break
    if not m or tuple(map(int, m.groups()[:3])) != want:
        changed = fl('result = transport.getSongLength(midi.SONGLENGTH_ABSTICKS)') != song_before
        if m:  # a clip is there but in the wrong place: take our click back
            fl('general.undoUp()\nresult = 1')
        raise RuntimeError('Clip did not land at bar %d (hints read: %s; song length changed: %s). Is the '
                           'playlist draw/paint tool selected?' % (bar, seen, changed))
    return {'pattern': pattern, 'pattern_name': fl('result = patterns.getPatternName(ARGS["p"])', p=pattern),
            'track': track, 'track_name': pl.names[track - 1], 'bar': bar,
            'clip': m.group(0), 'song_length_bars': fl('result = transport.getSongLength(midi.SONGLENGTH_BARS)')}
