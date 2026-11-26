"""Instant 'set a knob to the value FL displays' for FL's stock effects.

build(): sweep every parameter of a loaded plugin inside FL (one bridge call per parameter, values
restored afterwards) and save value -> display text to knob_tables.json.
set_display(track, slot, param, target): look the target up in the table (no searching in FL), set it,
and return what FL shows. Falls back to knobset's binary search for plugins without a table.
Targets are numbers in the display's unit (dB, Hz, ms, sec, %, ratio) or text for choice knobs ('Soft',
'High pass').
"""
import json
import os
import re

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = os.path.join(HERE, 'knob_tables.json')
STEPS = 400


class _Server:
    """server.py imports this module, so fetch it lazily (works whether it runs as __main__ or 'server')."""
    def __getattr__(self, name):
        mod = sys.modules.get('server') or sys.modules.get('__main__')
        if not hasattr(mod, 'plugin_params'):
            import importlib
            mod = importlib.import_module('server')
        return getattr(mod, name)


server = _Server()

SWEEP_BODY = r'''
idx, slot, p, n = ARGS['index'], ARGS['slot'], ARGS['param'], ARGS['n']
old = plugins.getParamValue(p, idx, slot)
rows = []
for k in range(n + 1):
    v = k / n
    plugins.setParamValue(v, p, idx, slot)
    rows.append(plugins.getParamValueString(p, idx, slot))
plugins.setParamValue(old, p, idx, slot)
result = {'name': (plugins.getParamName(p, idx, slot) or '').strip(), 'rows': rows}
'''


def _num(text):
    t = (text or '').replace(',', '.').strip().lower()
    m = re.search(r'-?\d+(\.\d+)?', t)
    if not m:
        return None
    v = float(m.group(0))
    if re.search(r'\d\s*k', t):
        v *= 1000
    return v


def load():
    if os.path.exists(TABLES):
        with open(TABLES) as f:
            return json.load(f)
    return {}


def build(track, slot):
    info = server.plugin_params({'mixer_track': track, 'slot': slot, 'limit': 500})
    plug = info['plugin']
    tables = load()
    tables[plug] = {}
    for p in info['params']:
        r = server.fl(server.PLUGINS_MOD + SWEEP_BODY, index=track, slot=slot, param=p['param'], n=STEPS)
        tables[plug][str(p['param'])] = {'name': r['name'], 'rows': r['rows']}
    # some plugins (e.g. Fruity Delay 3) update their display text late, so a sweep inside one FL call
    # records stale text. Check a few points with separate calls; keep the table only if they match.
    bad = 0
    for p in info['params'][:12]:
        old = p['value']
        for k in (STEPS // 4, STEPS // 2, 3 * STEPS // 4):
            server.plugin_set({'mixer_track': track, 'slot': slot, 'param': p['param'], 'value': k / STEPS})
            shown = next(x['display'] for x in server.plugin_params({'mixer_track': track, 'slot': slot, 'limit': 500})
                         ['params'] if x['param'] == p['param'])
            if shown != tables[plug][str(p['param'])]['rows'][k]:
                bad += 1
        server.plugin_set({'mixer_track': track, 'slot': slot, 'param': p['param'], 'value': old})
    if bad:
        del tables[plug]
        plug += ' (display lags: no table, use knobset search)'
    with open(TABLES, 'w') as f:
        json.dump(tables, f)
    return plug, len(info['params'])


def find_param(plug, name):
    t = load()[plug]
    for k, v in t.items():
        if v['name'].lower() == name.lower():
            return int(k)
    raise KeyError('%s has no parameter %r' % (plug, name))


def value_for(plug, param, target):
    rows = load()[plug][str(param)]['rows']
    if isinstance(target, str) and _num(target) is None:
        for k, r in enumerate(rows):
            if r.strip().lower() == target.strip().lower():
                return k / STEPS
        raise ValueError('%r is not one of %s' % (target, sorted(set(rows))))
    tgt = _num(target) if isinstance(target, str) else float(target)
    best = min((k for k in range(len(rows)) if _num(rows[k]) is not None),
               key=lambda k: abs(_num(rows[k]) - tgt))
    return best / STEPS


def set_display(track, slot, param, target):
    """param: index or parameter name. Returns the display FL shows afterwards."""
    plug = server.plugin_params({'mixer_track': track, 'slot': slot, 'limit': 1})['plugin']
    if plug not in load():
        import knobset
        p = param
        if not isinstance(p, int):
            names = server.plugin_params({'mixer_track': track, 'slot': slot, 'limit': 500})['params']
            p = next((x['param'] for x in names if x['name'].lower() == str(param).lower()), None)
            if p is None:
                raise KeyError('%s has no parameter %r' % (plug, param))
        return knobset.set_display(track, slot, p, _num(target) if isinstance(target, str) else float(target))
    p = param if isinstance(param, int) else find_param(plug, param)
    server.plugin_set({'mixer_track': track, 'slot': slot, 'param': p, 'value': value_for(plug, p, target)})
    r = server.plugin_params({'mixer_track': track, 'slot': slot, 'limit': 500})
    return next(x['display'] for x in r['params'] if x['param'] == p)
