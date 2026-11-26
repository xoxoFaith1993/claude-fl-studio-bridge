"""Render FL Studio projects to audio files hands-off (no screen control, no clicks).

How: FL's own command-line renderer, `FL64.exe /R /Ewav,mp3 project.flp`, which opens the project
windowless, writes <project>.<ext> next to it and exits. Verified on FL 26.1.

Catch: while FL is running, that command is handed to the open FL, which ignores it. So if FL is open,
this saves (only when allowed), closes FL cleanly, renders, then reopens the project that was open.
The render uses the project as saved on disk, so unsaved changes must be saved first to be heard.

  python render.py project.flp [wav,mp3] [out_dir]
"""
import json
import os
import shutil
import subprocess
import sys
import time
import winreg

import config
import flhands
import winfocus

FORMATS = ('wav', 'mp3', 'ogg', 'flac')
MRU_KEY = config.mru_key()       # e.g. Software\Image-Line\FL Studio 26\MRU (newest FL version installed)


def current_project():
    """Path of the saved project open in FL, or None (FL closed, or an unsaved new project)."""
    main = winfocus.find_fl_window()
    if not main or not MRU_KEY:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MRU_KEY) as k:
            latest = winreg.QueryValueEx(k, '0')[0]
    except OSError:
        return None
    name = os.path.splitext(os.path.basename(latest))[0]
    return latest if name.lower() in flhands._text(main).lower() and os.path.isfile(latest) else None


def _cli_render(project, formats, timeout):
    """Run FL's command-line render. Returns {ext: path of the new file next to the project}."""
    folder, base = os.path.dirname(project), os.path.splitext(os.path.basename(project))[0]
    targets = {ext: os.path.join(folder, base + '.' + ext) for ext in formats}
    backups = {}
    for p in targets.values():                      # never lose an older render with the same name
        if os.path.exists(p):
            backups[p] = p + '.prerender.bak'
            os.replace(p, backups[p])
    t0 = time.time()
    try:
        exe = config.fl_exe()
        subprocess.run([exe, '/R', '/E' + ','.join(formats), project], cwd=os.path.dirname(exe), timeout=timeout)
    except subprocess.TimeoutExpired:
        for pid in flhands.fl_pids():               # a stuck render: stop it, keep going to clean up
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
        for p, b in backups.items():
            os.replace(b, p)
        raise RuntimeError('FL did not finish rendering within %ds' % timeout)
    made = {ext: p for ext, p in targets.items() if os.path.exists(p) and os.path.getmtime(p) >= t0 - 2}
    return made, backups


def check(path):
    """Quick sanity report on a rendered WAV: length, loudness, peaks, and how much of it is silent.
    Needs numpy (optional); without it the render still works and this check is skipped."""
    try:
        import numpy as np
    except ImportError:
        return {'skipped': 'install numpy for the loudness/peak check (pip install numpy)'}
    import audio_analyze as A
    x, sr = A.load(path)
    y = A.k_filter(x, sr)
    blk = int(sr * 0.5)
    rms = [A.db(np.sqrt((x[i:i + blk] ** 2).mean())) for i in range(0, max(1, len(x) - blk), blk)]
    return {'seconds': round(len(x) / sr, 1), 'sample_rate': sr,
            'integrated_lufs': round(A.integrated(A.lufs_blocks(y, sr)), 1),
            'sample_peak_dbfs': round(A.db(np.abs(x).max()), 2),
            'true_peak_dbtp': round(A.true_peak(x, sr), 2),
            'silent_share': round(sum(r < -60 for r in rms) / max(1, len(rms)), 2),
            'clipping': bool(np.abs(x).max() >= 0.999)}


def open_project_name():
    """Base name of the project open in FL, from its window title ('X.flp - FL Studio 2026'), or None."""
    main = winfocus.find_fl_window()
    if not main:
        return None
    title = flhands._text(main)
    return title.split('.flp')[0].strip() if '.flp' in title else None


def _render_in_app(fl, project, formats, out_dir, name, split, timeout):
    """Export from the open FL (export.py): no restart, renders the project as it is now (unsaved too)."""
    import export
    out_dir = os.path.abspath(out_dir or (os.path.dirname(project) if project else os.getcwd()))
    stem = name or open_project_name() or 'render'
    os.makedirs(out_dir, exist_ok=True)
    old = [os.path.join(out_dir, n) for n in os.listdir(out_dir)
           if os.path.splitext(n)[0] == stem or (split and n.startswith(stem + '_'))]
    backups = {}
    for p in old:                                    # FL would ask "replace?"; keep the old file until success
        backups[p] = p + '.prerender.bak'
        os.replace(p, backups[p])
    try:
        r = export.export(fl, os.path.join(out_dir, stem + '.wav'), formats, split, timeout)
    except Exception:
        for p, b in backups.items():
            if not os.path.exists(p):
                os.replace(b, p)
        raise
    for p, b in backups.items():
        if os.path.exists(b):
            os.remove(b)
    files = {}
    for p in r['files']:
        base, ext = os.path.splitext(os.path.basename(p))
        key = ext[1:] if base == stem else base[len(stem) + 1:] + ext   # 'wav' / 'Kick.wav' for split stems
        files[key] = p
    res = {'mode': 'in-app export (FL stayed open)', 'files': files, 'render_seconds': r['seconds'],
           'split': split}
    main_wav = files.get('wav') or files.get('Master.wav')
    if main_wav:
        res['check'] = check(main_wav)
    return res


def render(fl, project=None, formats=('wav',), out_dir=None, name=None, unsaved='refuse', reopen=True,
           timeout=1800, mode='auto', split=False):
    """Render a project to audio files and return where they went plus a check of the WAV.

    project  .flp path; default: the saved project open in FL.
    formats  any of wav, mp3, ogg, flac.
    out_dir  folder for the files (default: the project's folder); name: file name without extension.
    mode     'auto' (default): if FL is open with this project, export inside FL (fast, no restart, includes
             unsaved changes); otherwise the command-line render. 'app' / 'cli' force one route.
    split    in-app only: one WAV per mixer insert ("<name>_<insert>.wav") + "<name>_Master.wav" (FL turns
             master effects off while splitting - use it to measure balance, not for the final mix).
    unsaved  CLI route, if FL is open with unsaved changes: 'refuse' (error, the default), 'save' or 'discard'.
    reopen   CLI route: reopen what was open in FL afterwards.
    """
    formats = [f.lower().lstrip('.') for f in ([formats] if isinstance(formats, str) else formats)]
    bad = [f for f in formats if f not in FORMATS]
    if bad or not formats:
        raise ValueError('formats must be some of %s' % ', '.join(FORMATS))
    running = bool(winfocus.find_fl_window())
    open_name = open_project_name() if running else None
    same = running and open_name and (not project or
                                      os.path.splitext(os.path.basename(project))[0].lower() == open_name.lower())
    if mode == 'app' or (mode == 'auto' and same) or (split and running):
        if not running:
            raise RuntimeError('FL is not open: the in-app export needs FL running (use mode="cli")')
        if project and not same:
            raise RuntimeError('FL has "%s" open, not %s: open it first or use mode="cli"' % (open_name, project))
        return _render_in_app(fl, project, formats, out_dir, name, split, timeout)
    if split:
        raise RuntimeError('split mixer tracks needs the in-app export (FL open with the project)')
    was_open = current_project() if running else None
    project = os.path.abspath(project or was_open or '')
    if not os.path.isfile(project):
        raise FileNotFoundError('No saved project to render: pass project=<.flp>, or save the open one first')
    if running:                                     # the command-line render only works with FL closed
        flhands.close_fl(fl, unsaved)
        time.sleep(1)
    t0 = time.time()
    try:
        made, backups = _cli_render(project, formats, timeout)
    finally:
        reopened = None
        if running and reopen:
            flhands.launch(was_open)                # back to what the user had open
            reopened = {'project': was_open, 'bridge': flhands.wait_bridge(fl)}
    out_dir = os.path.abspath(out_dir or os.path.dirname(project))
    os.makedirs(out_dir, exist_ok=True)
    stem = name or os.path.splitext(os.path.basename(project))[0]
    files = {}
    for ext, p in made.items():
        dest = os.path.join(out_dir, stem + '.' + ext)
        if os.path.abspath(dest) != os.path.abspath(p):
            shutil.move(p, dest)                    # works across drives too
        files[ext] = dest
    for p, b in backups.items():                    # put older renders back unless replaced on purpose
        if os.path.exists(p):
            os.remove(b)                            # same name and folder requested: the new file wins
        else:
            os.replace(b, p)
    missing = [f for f in formats if f not in files]
    res = {'mode': 'command line (FL closed and reopened)' if running else 'command line',
           'project': project, 'files': files, 'missing': missing, 'render_seconds': round(time.time() - t0),
           'fl_was_open': running, 'reopened': reopened}
    if 'wav' in files:
        res['check'] = check(files['wav'])
    return res


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bridge import Bridge
    br = Bridge()

    def fl(body, **args):
        code = 'ARGS = %s\n%s' % (ascii(args), body) if args else body
        return br.run(code, timeout=30)[0]
    out = render(fl, sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else 'wav').split(','),
                 sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps(out, indent=1))
