"""Settings for the FL Studio bridge.

Everything machine-specific lives here, so nothing is hard-coded to one PC.

Where settings come from (later wins):
  1. DEFAULTS below
  2. auto-detection (FL Studio's own registry entries, the running FL64.exe)
  3. config.json in %APPDATA%\\claude-fl-studio-bridge\\ (or the file named by FL_BRIDGE_CONFIG)
  4. a few environment variables (FL_BRIDGE_FL_EXE, FL_BRIDGE_PROJECTS, FL_OVERLAY=0, ...)

Run `python config.py` to print the settings the bridge will use and where each one came from.
Copy config.example.json to the config folder and edit it to override anything.
"""
import glob
import json
import os
import winreg

APP = 'claude-fl-studio-bridge'
CONFIG_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP)
CONFIG_FILE = os.environ.get('FL_BRIDGE_CONFIG') or os.path.join(CONFIG_DIR, 'config.json')
DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), APP)
FL_USER_DATA = os.path.join(os.path.expanduser('~'), 'Documents', 'Image-Line', 'FL Studio')

DEFAULTS = {
    # FL Studio program (FL64.exe). null = auto-detect.
    'fl_exe': None,
    # FL's template folder (<FL folder>\Data\Templates). null = next to fl_exe.
    'templates_dir': None,
    # where fl_project "new" puts new projects (<projects_dir>\<name>\<name>.flp)
    'projects_dir': os.path.join(FL_USER_DATA, 'Projects'),
    # FL's user data folder (Documents\Image-Line\FL Studio unless you moved it in FL's settings)
    'fl_user_data': FL_USER_DATA,
    # virtual MIDI port names (loopMIDI or Windows MIDI Services loopback)
    'port_to_fl': 'Claude to FL',
    'port_from_fl': 'FL to Claude',
    # folders the bridge writes to
    'midi_dir': os.path.join(DATA_DIR, 'midi'),
    'snap_dir': os.path.join(DATA_DIR, 'snaps'),
    # the "hands off" banner shown while Claude clicks inside FL
    'banner': {
        'enabled': True,
        'text': 'Chill bro, let me solo her...',
        'subtext': 'Claude is working in FL Studio - hands off the mouse and keys',
        'hold_seconds': 90,
        'color': '#C8283C',
    },
    # colour given to channels / inserts the bridge creates
    'new_item_color': '#C8283C',
    # optional local vision model for fl_read_window (Ollama)
    'ollama_host': 'http://localhost:11434',
    'vision_model': 'qwen3-vl:8b-instruct',
    # pixel positions inside FL windows, measured on FL Studio 2026 (26.1) at 100% scaling.
    # If a hands-off tool misses its target on your PC, re-measure with fl_snapshot and change it here.
    # See docs/CALIBRATION.md.
    'calibration': {
        'piano_roll_tools_icon': [37, 14],      # wrench icon in the piano roll's top-left toolbar
        'file_menu_label': [18, 20],            # FILE in FL's main menu bar
        'export_row_y': 234,                    # centre y of File > Export in the opened File menu
        'browser_first_row': [60, 10],          # first row of the Browser tree
        'mixer_pan_knob_y': 192,                # y of the big PAN knob under each mixer insert's name (500 px mixer)
        'record_dialog_rows': {'edison': 243, 'audio_clip': 280, 'notes_automation': 317, 'everything': 354},
        'record_dialog_height': 421,
        'render_window': {
            'led': {'wav': [16, 133], 'mp3': [72, 133], 'ogg': [126, 133], 'flac': [180, 133],
                    'split': [196, 422], 'show_files': [16, 584]},
            'label': {'wav': [36, 133], 'mp3': [90, 133], 'ogg': [144, 133], 'flac': [198, 133],
                      'split': [245, 422], 'show_files': [90, 585]},
            'start': [391, 586],
        },
    },
}

_SOURCES = {}


def _merge(base, extra, path=''):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v, path + k + '.')
        else:
            if isinstance(v, str) and (k.endswith('_dir') or k.endswith('_exe') or k == 'fl_user_data'):
                v = os.path.expandvars(v)          # "%USERPROFILE%\\Music" etc.
            base[k] = v
            _SOURCES[path + k] = 'config.json'


def _reg(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def detect_fl_exe():
    """FL64.exe from FL's own registry entry, then the usual install folders."""
    p = _reg(r'Software\Image-Line\Shared\Paths', 'FL Studio')
    if p and os.path.isfile(p):
        return p
    for pattern in (r'%ProgramFiles%\Image-Line\FL Studio*\FL64.exe',
                    r'%ProgramFiles(x86)%\Image-Line\FL Studio*\FL64.exe'):
        hits = sorted(glob.glob(os.path.expandvars(pattern)))
        if hits:
            return hits[-1]
    return None


def detect_user_data():
    p = _reg(r'Software\Image-Line\Shared\Paths', 'Shared data')
    if p:
        cand = os.path.join(p, 'FL Studio')
        if os.path.isdir(cand):
            return cand
    return FL_USER_DATA


def mru_key():
    """Registry key of FL's recent-files list for the newest FL version installed (e.g. 'FL Studio 26')."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Image-Line') as k:
            names, i = [], 0
            while True:
                try:
                    names.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        return None
    vers = []
    for n in names:
        if n.startswith('FL Studio '):
            try:
                vers.append((int(n.split()[-1]), n))
            except ValueError:
                pass
    return r'Software\Image-Line\%s\MRU' % max(vers)[1] if vers else None


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    for k in cfg:
        _SOURCES[k] = 'default'
    exe = detect_fl_exe()
    if exe:
        cfg['fl_exe'], _SOURCES['fl_exe'] = exe, 'auto-detected'
    ud = detect_user_data()
    if ud != FL_USER_DATA:
        cfg['fl_user_data'], _SOURCES['fl_user_data'] = ud, 'auto-detected'
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8') as f:
            _merge(cfg, json.load(f))
    env = {'FL_BRIDGE_FL_EXE': 'fl_exe', 'FL_BRIDGE_PROJECTS': 'projects_dir', 'FL_BRIDGE_TO_FL': 'port_to_fl',
           'FL_BRIDGE_FROM_FL': 'port_from_fl', 'FL_BRIDGE_MIDI_DIR': 'midi_dir', 'OLLAMA_HOST': 'ollama_host'}
    for var, key in env.items():
        if os.environ.get(var):
            cfg[key], _SOURCES[key] = os.environ[var], 'env ' + var
    if os.environ.get('FL_OVERLAY') == '0':
        cfg['banner']['enabled'] = False
    if not cfg['templates_dir'] and cfg['fl_exe']:
        cfg['templates_dir'] = os.path.join(os.path.dirname(cfg['fl_exe']), 'Data', 'Templates')
        _SOURCES['templates_dir'] = 'next to fl_exe'
    return cfg


CFG = load()


def get(key, default=None):
    return CFG.get(key, default)


def cal(*path):
    """A calibration value, e.g. cal('render_window', 'start')."""
    v = CFG['calibration']
    for p in path:
        v = v[p]
    return tuple(v) if isinstance(v, list) else v


def fl_exe():
    if not CFG['fl_exe'] or not os.path.isfile(CFG['fl_exe']):
        raise RuntimeError('FL Studio (FL64.exe) not found. Set "fl_exe" in %s' % CONFIG_FILE)
    return CFG['fl_exe']


def hex_color(key='new_item_color'):
    return int(str(CFG[key]).lstrip('#'), 16)


if __name__ == '__main__':
    print('config file: %s (%s)' % (CONFIG_FILE, 'found' if os.path.isfile(CONFIG_FILE) else 'not found, using defaults'))
    for k, v in CFG.items():
        if k != 'calibration':
            print('%-15s %-14s %s' % (k, '[' + _SOURCES.get(k, 'derived') + ']', v))
    print('calibration    (see docs/CALIBRATION.md)')
