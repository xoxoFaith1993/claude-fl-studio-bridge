"""Local 'eyes' for FL (optional): ask a vision model running in Ollama what an FL window or dialog shows.

Needs Ollama (ollama.com, MIT) running with a vision model pulled, e.g. `ollama pull qwen3-vl:8b-instruct`.
Runs fully offline on your GPU. Claude can also just look at fl_snapshot itself, so this is only a cheap
helper for reading dialogs that pop up while Claude drives FL, so they can be answered with posted
clicks/keys instead of screen control.
Small models read text well but guess pixel positions roughly; the result's click points are
converted back to window pixels and should be treated as a best guess.
"""
import base64
import json
import os
import urllib.request

import config
import flhands

import ollama_ctl
OLLAMA = ollama_ctl.OLLAMA          # normalized ('0.0.0.0:11434' -> 'http://127.0.0.1:11434')
MODEL = os.environ.get('FL_EYES_MODEL') or config.get('vision_model')

PROMPT = ('This is a screenshot of one window from FL Studio (a music program). Reply with JSON only: '
          '{"kind": "dialog" | "menu" | "plugin" | "other", "title": str, "message": str (the main text, '
          'verbatim, short), "buttons": [{"label": str, "x": int, "y": int}]} where x,y is the centre of each '
          'clickable button in 0-1000 coordinates relative to the image width and height. '
          'List every button or option you can click. Do not invent buttons.')


def ask(png_path, prompt=PROMPT, model=MODEL, timeout=120):
    import ollama_ctl
    if not ollama_ctl.ensure_running():
        raise RuntimeError('Ollama is not running at %s and could not be started. The local AI is optional: install '
                           'Ollama (ollama.com), run "ollama pull %s", or use fl_snapshot instead.' % (OLLAMA, model))
    if not ollama_ctl.has_model(model):
        raise RuntimeError('Ollama has no "%s" model yet. Run: ollama pull %s' % (model, model))
    with open(png_path, 'rb') as f:
        img = base64.b64encode(f.read()).decode('ascii')
    body = {'model': model, 'stream': False, 'format': 'json', 'keep_alive': '10m',
            'options': {'temperature': 0},
            'messages': [{'role': 'user', 'content': prompt, 'images': [img]}]}
    req = urllib.request.Request(OLLAMA + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = json.load(r)['message']['content']
    try:
        return json.loads(text)
    except ValueError:
        return {'raw': text}


def read_window(target=None):
    """Describe an FL window (default: the top dialog, else the main window). Button points are in window pixels."""
    if target is None:
        ds = flhands.dialogs()
        target = ds[0] if ds else 'main'
    shot = flhands.snap(target)
    w, h = shot['window_size']
    info = ask(shot['path'])
    for b in info.get('buttons', []) if isinstance(info, dict) else []:
        try:
            b['x'], b['y'] = round(float(b['x']) * w / 1000), round(float(b['y']) * h / 1000)
        except (KeyError, TypeError, ValueError):
            pass
    info['window'] = {'hwnd': flhands._hwnd(target), 'size': [w, h], 'snapshot': shot['path']}
    return info


if __name__ == '__main__':
    import sys
    print(json.dumps(read_window(int(sys.argv[1]) if len(sys.argv) > 1 else None), indent=1))
