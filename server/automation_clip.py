"""Draw automation clip points on the Playlist, hands-off.

FL's script API cannot create or edit automation clips, so this works in the Playlist window with posted
messages (FL can stay in the background):
  - right-click on empty clip space adds a point there
  - right-click on a point opens its menu (Delete, curve modes, Type in value...)
  - hovering a point makes FL's hint show its value ("-12.7dB")
Time comes from the Playlist hover mapping in playlist_place; the value is the height inside the clip
body, calibrated for FL 2026's default track height (body = row top+16 .. row top+46).
Typed values (point menu > Type in value) are avoided: FL opens that box at the real mouse pointer,
which can be on another monitor.
"""
import bisect
import math
import time

import config
import flhands
import playlist_place as pp

# FL mixer fader: value -> dB (measured on FL 2026 with mixer.getTrackVolume(t, 1))
FADER = [(0.1, -29.13), (0.2, -21.71), (0.25, -19.05), (0.3, -16.73), (0.35, -14.63), (0.4, -12.7),
         (0.45, -10.89), (0.5, -9.18), (0.55, -7.54), (0.6, -5.95), (0.7, -2.91), (0.8, 0.0), (0.9, 2.83),
         (1.0, 5.59)]


def db_to_value(db):
    if db is None or db <= -60:          # "zero": fader all the way down (silence)
        return 0.0
    dbs = [d for _, d in FADER]
    if db <= dbs[0]:
        return FADER[0][0]
    if db >= dbs[-1]:
        return 1.0
    i = bisect.bisect_left(dbs, db)
    (v0, d0), (v1, d1) = FADER[i - 1], FADER[i]
    return v0 + (v1 - v0) * (db - d0) / (d1 - d0)


def _db(hint):
    """Value from an automation point hint ('-11.5dB', '-INF dB', '-oo dB'); None if not a point."""
    h = (hint or '').strip()
    if not h.lower().endswith('db'):
        return None
    try:
        return float(h[:-2])
    except ValueError:
        return float('-inf')


class AutoClip:
    BODY_TOP, BODY_BOTTOM = 16, 46   # pixels below the track's top edge: value 1.0 .. 0.0

    def __init__(self, fl, track):
        self.fl = fl
        self.track = track
        self.pl = pp.Playlist(fl)
        self.pl.find_columns()
        self.pl.find_rows()
        self.yc = self.pl.ensure_track_visible(track)
        self.pl.stable_fit(self.yc)
        top = self.yc - self.pl.rh / 2
        self.top, self.bottom = top + self.BODY_TOP, top + self.BODY_BOTTOM
        length = self.clip_beats()
        self.length = length
        self.set_zoom(40)  # points closer than ~8 px collide: a beat should be at least ~40 px wide
        # where the clip's points are (beats). A fresh clip has a start and an end point; points added
        # through add() are recorded, so we always know where FL's tension handles (segment midpoints) are.
        y0 = self.y(0.8)                      # a new volume clip starts and ends at the fader's 0 dB
        self.points = [(0.0, y0), (length if length else 1e9, y0)]   # (beat, y pixel), sorted by beat

    ZOOM_STEP = 1.169
    HANDLE_PX = 12         # a right-click this close (px) to a tension handle hits the handle
    POINT_PX = 8           # ... or this close to an existing point opens that point's menu

    def _around(self, beat, pts):
        import bisect
        i = bisect.bisect_right([b for b, _ in pts], beat)
        return pts[max(0, i - 1)], pts[min(len(pts) - 1, i)]

    def blocked(self, beat, y, points=None):
        """True if a right-click at (beat, y) would hit an existing point or the tension handle FL draws
        halfway along the curve between the two points around it."""
        pts = points if points is not None else self.points
        (pb, py), (qb, qy) = self._around(beat, pts)
        ppb = self.pl.ppb
        for b, yy in ((pb, py), (qb, qy)):
            if math.hypot((beat - b) * ppb, y - yy) < self.POINT_PX:
                return True
        return math.hypot(((pb + qb) / 2 - beat) * ppb, (py + qy) / 2 - y) < self.HANDLE_PX

    def helper_for(self, beat, y):
        """A helper point (beat, y) in the same segment that is clickable itself and moves the handle away
        from the target. Helpers go at the bottom or top of the clip, away from the target's height."""
        (pb, _), (qb, _) = self._around(beat, self.points)
        step = 4 / self.pl.ppb
        hy = self.bottom if y < (self.top + self.bottom) / 2 else self.top
        for k in range(1, 400):
            for h in (beat + k * step, beat - k * step):
                if not (pb < h < qb):
                    continue
                pts = sorted(self.points + [(h, hy)])
                if not self.blocked(h, hy) and not self.blocked(beat, y, pts):
                    return h, hy
        return None

    def note_point(self, beat, y):
        if not any(abs(beat - b) < 1e-6 for b, _ in self.points):
            self.points = sorted(self.points + [(beat, y)])

    def forget_point(self, beat):
        self.points = [(b, yy) for b, yy in self.points if abs(b - beat) > 1e-6]

    def zoom_to(self, min_ppb):
        """Zoom in with ONE horZoom call (steps computed from the measured zoom factor), then one refit."""
        if self.pl.ppb >= min_ppb:
            return
        n = math.ceil(math.log(min_ppb / self.pl.ppb) / math.log(self.ZOOM_STEP))
        self.fl('ui.horZoom(ARGS["n"])\nresult = 1', n=n)
        self.pl.stable_fit(self.yc)

    def set_zoom(self, min_ppb):
        """Zoom in OR out (one call) so a beat is at least min_ppb wide but, if possible, the whole clip
        fits on screen; then scroll to bar 1."""
        room = self.pl.w - self.pl.gx - 60
        fit = room / self.length if self.length else min_ppb
        target = max(min_ppb, min(fit, 3 * min_ppb))
        n = math.floor(math.log(target / self.pl.ppb) / math.log(self.ZOOM_STEP))
        if self.pl.ppb * self.ZOOM_STEP ** n < min_ppb:
            n += 1
        if n:
            self.fl('ui.horZoom(ARGS["n"])\nresult = 1', n=n)
        self.fl('ui.scrollWindow(midi.widPlaylist, 1, 1)\nresult = 1')
        self.pl.stable_fit(self.yc)

    def clip_beats(self):
        """Length in beats of the clip on this track starting at bar 1 (from the clip header hint)."""
        h = self.pl.hint(self.x(0.3), self.yc - self.pl.rh / 2 + 7) or ''
        m = pp.CLIP.match(h)
        if not m:
            return None
        bars, steps, ticks = map(int, m.groups()[3:])     # "for 8:00:00" = 8 bars
        return bars * 4 + steps / 4 + ticks / 96

    def x(self, beat):
        x = self.pl.x_of(beat)
        if not (self.pl.gx - 22 < x < self.pl.w - 40):   # scroll that bar into view
            self.fl('ui.scrollWindow(midi.widPlaylist, ARGS["b"], 1)\nresult = 1', b=max(1, int(beat // 4)))
            self.pl.stable_fit(self.yc)
            x = self.pl.x_of(beat)
        return x

    def y(self, value):
        return round(self.bottom - value * (self.bottom - self.top))

    def close_menu(self):
        if self.fl('result = ui.isInPopupMenu()'):
            self.fl('ui.closeActivePopupMenu()\nresult = 1')
            time.sleep(0.15)

    def value_at(self, beat, y):
        """dB shown when hovering (beat, y), or None if no point there."""
        h = self.pl.hint(self.x(beat), y, wait=0.12) or ''
        return _db(h)

    def add(self, beat, db):
        """Add a point; returns the dB FL reports for it."""
        x, y = self.x(beat), self.y(db_to_value(db))
        if not (self.pl.gx - 22 < x < self.pl.w - 30):
            raise RuntimeError('beat %.2f could not be scrolled into view' % beat)
        helper = None
        for _ in range(2):
            # handles have a fixed size in pixels: zooming in frees a spot no helper can free
            if not self.blocked(beat, y) or self.helper_for(beat, y) is not None:
                break
            self.zoom_to(self.pl.ppb * 2)
            x = self.x(beat)
        pre = self.pl.hint(x, y) or ''
        if 'tension' in pre or self.blocked(beat, y):
            # a tension handle (or point) is in the way: add a helper point that moves the handle away
            # (chosen so the helper itself is clickable), and delete it afterwards
            helper = self.helper_for(beat, y)
            if helper is not None:
                flhands.click(self.pl.hwnd, self.x(helper[0]), helper[1], button='right')
                time.sleep(0.25)
                self.close_menu()
                self.note_point(*helper)
        self.fl('ui.setHintMsg("~")\nresult = 1')
        flhands.click(self.pl.hwnd, x, y, button='right')
        time.sleep(0.1)
        # usually FL shows the new point's value at once: one round trip instead of several
        h, in_menu = self.fl('result = (ui.getHintMsg(), ui.isInPopupMenu())')
        if in_menu:                       # we hit an existing point
            self.close_menu()
        if helper is not None:
            self.delete(helper[0], helper[1])
            self.forget_point(helper[0])
        if _db(h) is not None and not in_menu and helper is None:
            self.note_point(beat, y)
            return _db(h)
        for dy in (0, -1, 1, -2, 2):
            got = self.value_at(beat, y + dy)
            if got is not None:
                self.note_point(beat, y)
                return got
        if getattr(self, 'trace', None) is not None:
            self.trace.append({'beat': beat, 'x': round(x, 1), 'y': y, 'before': pre, 'after_click': h,
                               'menu': in_menu, 'helper': helper})
        return None

    def delete(self, beat, y):
        """Delete the point at (beat, y) through its menu (first item)."""
        flhands.click(self.pl.hwnd, self.x(beat), y, button='right')
        time.sleep(0.3)
        if not self.fl('result = ui.isInPopupMenu()'):
            return False
        menu = flhands._menu()
        self.fl('ui.down()\nresult = 1')
        time.sleep(0.1)
        flhands.key(menu, 'enter')
        time.sleep(0.3)
        return True


def _mixer():
    return flhands._child(flhands.winfocus.find_fl_window(), 'TFXForm', 'Mixer')


def _strips(fl, mx, track):
    """x ranges of mixer strips that change when the selection moves from `track` to another one."""
    other = 0 if track != 0 else 1
    fl('mixer.setTrackNumber(ARGS["t"])\nresult = 1', t=track)
    time.sleep(0.3)
    w, h, a = flhands.grab(mx)
    fl('mixer.setTrackNumber(ARGS["t"])\nresult = 1', t=other)
    time.sleep(0.3)
    _, _, b = flhands.grab(mx)
    fl('mixer.setTrackNumber(ARGS["t"])\nresult = 1', t=track)
    cols = [x for x in range(w) if sum(1 for y in range(0, h, 2)
                                       if a[(y * w + x) * 4:(y * w + x) * 4 + 3] != b[(y * w + x) * 4:(y * w + x) * 4 + 3]) > 20]
    runs = []
    for x in cols:
        if runs and x == runs[-1][1] + 1:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    strips = [r for r in runs if 25 <= r[1] - r[0] <= 80]
    return w, h, a, strips


def _grey_cap(w, h, a, x0, x1):
    """Rows/cols of a light-grey fader cap inside strip x0..x1 (unselected strips)."""
    pts = []
    for y in range(60, h - 30):
        for x in range(x0, x1):
            i = (y * w + x) * 4
            bl, g, r = a[i], a[i + 1], a[i + 2]
            if r > 150 and g > 150 and bl > 150 and abs(r - g) < 20:
                pts.append((x, y))
    rows = {}
    for x, y in pts:
        rows.setdefault(y, []).append(x)
    good = {y: xs for y, xs in rows.items() if len(xs) >= 10}
    if not good:
        return None
    ys = sorted(good)
    xs = [x for y in ys for x in good[y]]
    return (min(xs) + max(xs)) / 2 - x0, (ys[0] + ys[-1]) / 2


def create_volume_clip(fl, mixer_track, dry_run=False):
    """Create an automation clip for a mixer insert's volume fader (fader menu > Create automation clip).
    FL adds it as a channel and puts the clip on the first free playlist track. Restores the channel
    rack filter to "All" afterwards (FL switches it to "Automation")."""
    fl('ui.showWindow(midi.widMixer)\nresult = 1')
    time.sleep(0.3)
    mx = _mixer()
    w, h, a, strips = _strips(fl, mx, mixer_track)
    if not strips:
        raise RuntimeError('Mixer insert %d is not visible in the mixer window' % mixer_track)
    strips.sort()
    s = strips[0] if mixer_track == 0 else strips[-1]
    width = s[1] - s[0]
    cap = None
    # an unselected strip right next to a selected insert shows a plain grey fader cap: use its offset
    # from the strip's left edge and its height (same for every strip, master included)
    ref = s if mixer_track != 0 else None
    if ref is None:
        _, _, _, rs = _strips(fl, mx, 1)
        ref = sorted(rs)[-1]
        fl('mixer.setTrackNumber(0)\nresult = 1')
        time.sleep(0.3)
        w, h, a = flhands.grab(mx)
    rw = ref[1] - ref[0]
    for nx in (ref[1] + 1, ref[0] - rw - 1):
        if 0 <= nx and nx + rw < w:
            cap = _grey_cap(w, h, a, nx, nx + rw)
            if cap:
                break
    if not cap:
        raise RuntimeError('Could not find the fader position')
    fx, fy = s[0] + cap[0], cap[1]
    before = fl('result = channels.channelCount()')
    flhands.click(mx, fx, fy, button='right')
    time.sleep(0.4)
    if not fl('result = ui.isInPopupMenu()'):
        raise RuntimeError('Fader menu did not open')
    menu = flhands._menu()
    mw, mh = flhands._rect(menu)[2] - flhands._rect(menu)[0], flhands._rect(menu)[3] - flhands._rect(menu)[1]
    # step down until the highlight reaches the "Create automation clip" row (148 of 325 px in FL 2026);
    # how many presses that takes depends on which items are enabled
    target = 148 / 325 * mh
    hy = None
    for _ in range(12):
        fl('ui.down()\nresult = 1')
        time.sleep(0.12)
        hy, band = _highlight_y(menu)
        if hy is not None and abs(hy - target) < band * 0.5:
            break
        if hy is not None and hy > target + band:
            hy = None
            break
    if hy is None or abs(hy - target) >= 12:
        fl('ui.closeActivePopupMenu()\nresult = 1')
        raise RuntimeError('Could not reach "Create automation clip" in the fader menu (%d x %d)' % (mw, mh))
    if dry_run:
        fl('ui.closeActivePopupMenu()\nresult = 1')
        return {'fader_at': [fx, fy], 'menu_ok': True}
    flhands.key(menu, 'enter')
    time.sleep(1.0)
    restore_rack_filter(fl)
    chans = fl('result = [(i, channels.getChannelName(i)) for i in range(channels.channelCount()) '
               'if channels.getChannelType(i) == midi.CT_AutoClip]')
    return {'automation_channels': chans, 'channels_before': before,
            'channels_now': fl('result = channels.channelCount()')}


def _highlight_y(menu):
    return flhands._highlight_y(menu)


def restore_rack_filter(fl):
    """Set the channel rack's filter back to 'All' (top item of its filter menu)."""
    fl('ui.showWindow(midi.widChannelRack)\nresult = 1')
    time.sleep(0.3)
    main = flhands.winfocus.find_fl_window()
    rack = flhands._child(main, 'TStepSeqForm', 'Channel rack')
    if not rack:
        return False
    flhands.click(rack, 145, 16)   # filter selector next to the rack title
    time.sleep(0.4)
    if not fl('result = ui.isInPopupMenu()'):
        return False
    menu = flhands._menu()
    for _ in range(6):                      # step up until the top row ('All') is highlighted
        hy, band = flhands._highlight_y(menu, top_skip=0)
        if hy is not None and hy < band * 1.5:
            break
        fl('ui.up()\nresult = 1')
        time.sleep(0.12)
    else:
        fl('ui.closeActivePopupMenu()\nresult = 1')
        return False
    flhands.key(menu, 'enter')
    time.sleep(0.3)
    fl('ui.hideWindow(midi.widChannelRack)\nresult = 1')
    return True


MODES = ['Smooth', 'Hold', 'Single curve', 'Single curve 2', 'Single curve 3', 'Double curve', 'Double curve 2',
         'Double curve 3', 'Half sine', 'Stairs', 'Smooth stairs', 'Pulse', 'Wave']


def set_mode(clip, beat, y, mode):
    """Set a point's curve mode (shape of the segment leading into it) through its menu."""
    for attempt in range(4):
        # right after a point is created FL sometimes ignores the next right-click: wait and retry
        flhands.click(clip.pl.hwnd, clip.x(beat), y, button='right')
        time.sleep(0.3)
        if clip.fl('result = ui.isInPopupMenu()'):
            break
        time.sleep(0.4)
    else:
        raise RuntimeError('No point at beat %.2f' % beat)
    menu = flhands._menu()
    # menu: Delete, [Mode], Smooth, Hold, Single curve ...  -> first down = Delete
    clip.fl('for _ in range(ARGS["n"]):\n    ui.down()\nresult = 1', n=2 + MODES.index(mode))
    time.sleep(0.15)
    flhands.key(menu, 'enter')
    time.sleep(0.3)


def pump(fl, track, bars=8, duck_db=-12.0, every_beats=4, release_beats=1.5, start_bar=1, offset_beats=0.0,
         hold_beats=0.0, attack_beats=0.0, top_db=0.0, release_curve='Single curve', hits=None, clip=None):
    """Sidechain-style volume pump on an automation clip, with few menu trips (new points inherit the last
    chosen curve mode, so points are added in groups that share a mode).

    Every `every_beats` beats from bar `start_bar` (+ offset_beats), for `bars` bars - or at the explicit
    beats in `hits` (0 = bar 1 start) - the level falls from top_db to duck_db (instantly, or over
    attack_beats), stays there hold_beats, then returns to top_db over release_beats with release_curve
    (Smooth, Single curve, Single curve 2/3, Double curve, Half sine ...). Call again with another
    start_bar/bars for a section with different settings.
    """
    first = (start_bar - 1) * 4
    end = first + bars * 4
    if 0 < attack_beats < 0.25:
        attack_beats = 0.25        # shorter ramps put points too close together to place reliably
    if 0 < hold_beats < 0.25:
        hold_beats = 0.25
    if hits is None:
        hits, b = [], first + offset_beats
        while b < end - 1e-6:
            hits.append(b)
            b += every_beats
    spacing = min([release_beats, every_beats - release_beats - hold_beats] +
                  [x for x in (hold_beats, attack_beats) if x > 0] or [1])
    if clip is None:
        clip = AutoClip(fl, track)
    length = clip.clip_beats()
    if length is not None and end > length + 0.01:
        raise RuntimeError('The automation clip is only %g bars long; the pump needs %g. Put the music on the '
                           'playlist before creating the clip (FL sizes it to the song).' % (length / 4, end / 4))
    if spacing > 0:
        # points (and the curve-tension handle FL draws between every two points) need ~14 px of room
        clip.zoom_to(min(14 / spacing, 160))
    yd, yt = clip.y(db_to_value(duck_db)), clip.y(db_to_value(top_db))
    log = []

    def schedule(beats, y):
        """Order beats so that, as far as possible, none is added on a tension handle or next to a point."""
        left, order, pts = list(beats), [], list(clip.points)
        while left:
            pick = next((b for b in left if not clip.blocked(b, y, pts)), left[0])
            left.remove(pick)
            order.append(pick)
            pts = sorted(pts + [(pick, y)])
        return order

    def group(kind, beats, db, y, mode):
        missed = []
        for i, at in enumerate(schedule(beats, y)):
            got = clip.add(at, db)
            log.append((kind, round(at, 3), got))
            if got is None:
                missed.append((len(log) - 1, at))
            if i == 0 and mode:
                set_mode(clip, at, y, mode)
        # retry misses right away (same group, so the inherited curve type is still right), then set the
        # curve explicitly in case a retry happened after a mode change
        for n, at in missed:
            got = clip.value_at(at, y)
            if got is None:
                got = clip.add(at, db)
            if got is not None and mode:
                set_mode(clip, at, y, mode)
            log[n] = (kind, round(at, 3), got)

    hit_at = [h + (0.03 if abs(h - first) < 1e-6 and attack_beats == 0 else 0.0) for h in hits]
    # order matters: FL draws a curve-tension handle halfway between every two points, so each new point
    # must not land exactly between two existing ones (recover, pre, duck, hold avoids that)
    group('recover', [min(h + hold_beats + release_beats, end - 0.2) for h in hits], top_db, yt, release_curve)
    if attack_beats > 0:
        group('pre', [h - attack_beats for h in hits if h - attack_beats > first + 0.05], top_db, yt, 'Hold')
        group('duck', hit_at, duck_db, yd, 'Single curve')                        # ramp down
    else:
        group('duck', hit_at, duck_db, yd, 'Hold')                                # straight drop
    if hold_beats > 0:
        group('hold', [h + hold_beats for h in hits], duck_db, yd, None)          # flat at the bottom
    return log


# ---------------------------------------------------------------- pan automation
# FL 2026 mixer (500 px tall window): the big knob under each insert's name/LED is PAN (calibration value)
PAN_KNOB_Y = config.cal('mixer_pan_knob_y')


def create_pan_clip(fl, mixer_track):
    """Automation clip for a mixer insert's PAN knob (knob menu > Create automation clip; same menu as the
    fader's). The knob sits at the strip's centre x, PAN_KNOB_Y. FL names the new clip after the control
    ('<insert> - Panning'), which is how the caller can verify it hit the right knob.
    FL drops the new clip AT THE PLAYHEAD (not bar 1), so the playhead is parked at the song start first."""
    fl('transport.stop()\ntransport.setSongPos(0)\nresult = 1')
    fl('ui.showWindow(midi.widMixer)\nresult = 1')
    time.sleep(0.3)
    mx = _mixer()
    w, h, a, strips = _strips(fl, mx, mixer_track)
    if not strips:
        raise RuntimeError('Mixer insert %d is not visible in the mixer window' % mixer_track)
    strips.sort()
    s = strips[0] if mixer_track == 0 else strips[-1]
    kx, ky = (s[0] + s[1]) / 2, PAN_KNOB_Y
    flhands.click(mx, kx, ky, button='right')
    time.sleep(0.4)
    if not fl('result = ui.isInPopupMenu()'):
        raise RuntimeError('Pan knob menu did not open')
    menu = flhands._menu()
    mh = flhands._rect(menu)[3] - flhands._rect(menu)[1]
    target = 148 / 325 * mh
    hy = None
    for _ in range(12):
        fl('ui.down()\nresult = 1')
        time.sleep(0.12)
        hy, band = _highlight_y(menu)
        if hy is not None and abs(hy - target) < band * 0.5:
            break
        if hy is not None and hy > target + band:
            hy = None
            break
    if hy is None or abs(hy - target) >= 12:
        fl('ui.closeActivePopupMenu()\nresult = 1')
        raise RuntimeError('Could not reach "Create automation clip" in the pan knob menu')
    flhands.key(menu, 'enter')
    time.sleep(1.0)
    restore_rack_filter(fl)
    return {'knob_at': [kx, ky], 'automation_channels': fl(
        'result = [(i, channels.getChannelName(i)) for i in range(channels.channelCount()) '
        'if channels.getChannelType(i) == midi.CT_AutoClip]')}
