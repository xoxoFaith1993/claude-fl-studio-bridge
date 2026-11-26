"""BBC Symphony Orchestra Discover helpers: load instruments by name, articulation keyswitches.

Instruments are FL presets saved once from the plugin (one per instrument) in
  Plugin presets > Generators > Fruity Wrapper - BBC Symphony Orchestra
Each preset's XML lists its articulations; articulation i is selected by MIDI note i (keyswitch),
e.g. strings: 0 Long, 1 Spiccato, 2 Pizzicato, 3 Tremolo. Put the keyswitch note just before the passage.
"""
import os
import re
import time

import browser
import config

PRESET_DIR = os.path.join(config.get('fl_user_data'), 'Presets', 'Plugin presets', 'Generators',
                          'Fruity Wrapper - BBC Symphony Orchestra')
BROWSER_PATH = ['Plugin presets', 'Generators', 'Fruity Wrapper - BBC Symphony Orchestra']


def catalog():
    """{instrument: {'name': plugin name, 'family': ..., 'articulations': [names in keyswitch order]}}"""
    out = {}
    if not os.path.isdir(PRESET_DIR):
        raise FileNotFoundError('No BBC Symphony Orchestra presets in %s. This tool is optional: install the free '
                                'BBC SO Discover (Spitfire Audio), load each instrument once in FL and save it as an '
                                'FL preset there (one preset per instrument).' % PRESET_DIR)
    for f in sorted(os.listdir(PRESET_DIR)):
        if not f.lower().endswith('.fst'):
            continue
        d = open(os.path.join(PRESET_DIR, f), 'rb').read()
        meta = re.search(rb'<META family="([^"]*)" name="([^"]*)"', d)
        arts = []
        for m in re.finditer(rb'<ARTIC>(.*?)</ARTIC>', d, re.S):
            body = m.group(1)
            name = re.search(rb'id="a_name" value="([^"]*)"', body)
            ks = re.search(rb'id="t_keyswitch" value="([^"]*)"', body)
            parts = name.group(1).decode().split(' - ')       # e.g. "f - Violas - Long", "c - Harp - Short ..."
            arts.append((int(ks.group(1)) if ks else len(arts), parts))
        arts.sort()
        labels = [p[-1] for _, p in arts]
        mixed = any(len(p) >= 3 and meta and p[1].lower() not in meta.group(2).decode().lower() for _, p in arts)
        if len(set(labels)) < len(labels) or mixed:
            labels = [p[1] if len(p) >= 3 else p[-1] for _, p in arts]   # multi-instrument: Harp / Celeste / Timpani
        arts = list(enumerate(labels))
        out[f[:-4]] = {'name': meta.group(2).decode() if meta else f[:-4],
                       'family': meta.group(1).decode() if meta else '',
                       'articulations': [a for _, a in arts]}
    return out


def keyswitch(instrument, articulation):
    """MIDI note that selects an articulation (matches by substring, e.g. 'pizz', 'long', 'short')."""
    arts = catalog()[instrument]['articulations']
    for i, a in enumerate(arts):
        if articulation.lower() in a.lower():
            return i
    raise ValueError('%s has no articulation like %r (has %s)' % (instrument, articulation, arts))


def load(fl, instrument, channel_name=None):
    """Load an instrument preset into a new channel; returns the channel index."""
    cat = catalog()
    if instrument not in cat:
        raise ValueError('Unknown BBCSO instrument %r. Available: %s' % (instrument, ', '.join(cat)))
    browser.load(fl, {'path': BROWSER_PATH + [instrument + '.fst'], 'where': 'new_channel'})
    time.sleep(2.5)
    idx = fl('result = channels.channelCount() - 1')
    fl('channels.setChannelName(ARGS["c"], ARGS["n"])\nchannels.showEditor(ARGS["c"], 0)\nresult = 1',
       c=idx, n=channel_name or instrument)
    return idx
