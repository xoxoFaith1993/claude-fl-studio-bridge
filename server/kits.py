"""fl_drum_kit: load YOUR drum kit into the open project in one call.

The kit is a JSON file you write once (copy kit.example.json to %APPDATA%\\claude-fl-studio-bridge\\kit.json):

{
  "color": "#C8283C",
  "zero_db_faders": true,
  "channels": [
    {"label": "Kick",  "browser": ["Packs", "Drums", "Kicks", "Kick 01.wav"], "insert": 4, "steps": "x---------------"},
    {"label": "Snare", "browser": ["Packs", "Drums", "Snares", "Snare 01.wav"], "insert": 3, "steps": "--------x-------"},
    {"label": "Hat",   "browser": ["Packs", "Drums", "Hats", "Hat 01.wav"], "insert": 2, "steps": "x-x-x-x-x-x-x-x-"}
  ],
  "insert_names": {"2": "Hats", "3": "Snare", "4": "Kick"}
}

- "browser" is the path of the sample in FL's Browser, from the top (check it with fl_browser_list).
- "steps" is a starter pattern (x = hit), repeated to fill 64 steps; leave it out for no steps.
- Re-run safe: channels already named like a kit label are kept as they are; stock template channels with
  no notes are reused; steps are only written on EMPTY rows; inserts you already named are left alone;
  mixer effects and EQ are never touched.
"""
import json
import os

import config

KIT_FILE = os.environ.get('FL_BRIDGE_KIT') or os.path.join(config.CONFIG_DIR, 'kit.json')
EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kit.example.json')
ZERO_DB = 0.8   # mixer fader value for 0 dB
# stock channels some FL templates add; reused only if their step rows are empty (checked below)
STOCK = ('808 Kick', '808 Clap', '808 HiHat', '808 Snare', 'Sampler')


def load_kit(path=None):
    path = path or KIT_FILE
    if not os.path.isfile(path):
        raise FileNotFoundError('No drum kit file at %s. Copy %s there and list your own samples '
                                '(paths from fl_browser_list).' % (path, EXAMPLE))
    with open(path, encoding='utf-8') as f:
        kit = json.load(f)
    if not kit.get('channels'):
        raise ValueError('%s has no "channels"' % path)
    return kit


def _steps64(s):
    return (s * (64 // len(s) + 1))[:64]


def _load_into(fl, path, channel=None):
    """Browser-load a sample into a new channel, or replace the sample of an existing `channel`."""
    import browser
    if channel is None:
        r = browser.load(fl, {'path': path, 'where': 'new_channel'})
        return len(r['channels_now']) - 1
    fl('channels.selectOneChannel(ARGS["c"])\nresult = 1', c=channel)
    browser.load(fl, {'path': path, 'where': 'selected_channel'})
    return channel


def setup_kit(fl, kit_path=None, log=lambda m: None):
    kit = load_kit(kit_path)
    col = int(str(kit.get('color') or config.get('new_item_color')).lstrip('#'), 16)
    chans = fl('result = [(i, channels.getChannelName(i), channels.getChannelType(i) == midi.CT_Sampler, '
               'any(channels.getGridBit(i, s) for s in range(64))) for i in range(channels.channelCount())]')
    by_name = {n: i for i, n, _, _ in chans}
    stock = [i for i, n, s, used in chans if n in STOCK and s and not used]
    done = {}
    for c in kit['channels']:
        label = c['label']
        if label in by_name:            # already in the project: keep it as it is
            done[label] = by_name[label]
            continue
        ch = _load_into(fl, c['browser'], stock.pop(0) if stock else None)
        fl('channels.setChannelName(ARGS["c"], ARGS["n"])\nchannels.setChannelColor(ARGS["c"], ARGS["col"])\n'
           'result = 1', c=ch, n=label, col=col)
        done[label] = ch
        log('%-10s -> channel %d' % (label, ch))
    route = {str(done[c['label']]): c['insert'] for c in kit['channels'] if c.get('insert') is not None}
    steps = {str(done[c['label']]): _steps64(c['steps']) for c in kit['channels'] if c.get('steps')}
    out = fl('''
for c, t in ARGS["route"].items():
    channels.setTargetFxTrack(int(c), t)
kept = []
for t, n in ARGS["names"].items():
    cur = mixer.getTrackName(int(t))
    if not cur.startswith("Insert "):
        kept.append((int(t), cur))      # already named: leave name and fader alone
        continue
    mixer.setTrackName(int(t), n)
    mixer.setTrackColor(int(t), ARGS["col"])
    if ARGS["zero"]:
        mixer.setTrackVolume(int(t), ARGS["zero"])
wrote, skipped = [], []
for c, row in ARGS["steps"].items():
    c = int(c)
    if any(channels.getGridBit(c, s) for s in range(256)):
        skipped.append(channels.getChannelName(c))   # the user's own steps: leave them
        continue
    for s, ch in enumerate(row):
        if ch == "x":
            channels.setGridBit(c, s, 1)
    wrote.append(channels.getChannelName(c))
result = {"kept_inserts": kept, "steps_written": wrote, "steps_left": skipped}''',
             route=route, names={str(k): v for k, v in (kit.get('insert_names') or {}).items()}, steps=steps,
             zero=ZERO_DB if kit.get('zero_db_faders', True) else 0, col=col)
    out.update({'channels': done, 'kit_file': kit_path or KIT_FILE})
    return out
