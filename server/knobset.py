"""Set a plugin knob to a target value as FL displays it (e.g. -14 dB, 2.5 : 1, 35 Hz, 2.8 sec).

FL knobs are 0..1 internally with non-linear curves, so this searches for the value whose display text
matches the target number (assumes the display rises or falls steadily with the knob).
"""
import re

import server


def _num(text):
    t = text.replace(',', '.').strip()
    m = re.search(r'-?\d+(\.\d+)?', t)
    if not m:
        return None
    v = float(m.group(0))
    if re.search(r'\dk', t.lower()) or 'khz' in t.lower():
        v *= 1000
    return v


def display(track, slot, param):
    r = server.plugin_params({'mixer_track': track, 'slot': slot, 'limit': 200})
    return next(p for p in r['params'] if p['param'] == param)


def set_value(track, slot, param, value):
    server.plugin_set({'mixer_track': track, 'slot': slot, 'param': param, 'value': value})
    return display(track, slot, param)


def set_display(track, slot, param, target, iters=16):
    lo, hi = 0.0, 1.0
    d_lo = _num(set_value(track, slot, param, lo)['display'])
    d_hi = _num(set_value(track, slot, param, hi)['display'])
    if d_hi is None:  # e.g. a filter that reads "Off" at the end of its range
        hi = 0.98
        d_hi = _num(set_value(track, slot, param, hi)['display'])
    rising = (d_hi or 0) >= (d_lo or 0)
    best = None
    for _ in range(iters):
        mid = (lo + hi) / 2
        p = set_value(track, slot, param, mid)
        v = _num(p['display'])
        if v is None:
            break
        if best is None or abs(v - target) < abs(best[1] - target):
            best = (mid, v, p['display'])
        if (v < target) == rising:
            lo = mid
        else:
            hi = mid
    set_value(track, slot, param, best[0])
    return best[2]
