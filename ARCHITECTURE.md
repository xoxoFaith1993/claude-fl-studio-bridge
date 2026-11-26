# Architecture

```
┌──────────────┐  MCP over stdio   ┌────────────────────┐  SysEx on "Claude to FL"  ┌──────────────────────┐
│ Claude Code  │ ────────────────> │ server/server.py   │ ────────────────────────> │ FL Studio            │
│ (MCP client) │ <──────────────── │  + helper modules  │ <──────────────────────── │  MIDI script         │
└──────────────┘   tool results    └────────────────────┘  SysEx on "FL to Claude"  │  "Claude Bridge"     │
                                      │  posted clicks / keys, PrintWindow captures  │  (device_ClaudeBridge)│
                                      └────────────────────────────────────────────> │  + piano roll script │
                                                                                     └──────────────────────┘
```

## The pieces

| File | Job |
|---|---|
| `server/server.py` | MCP server (JSON-RPC 2.0, one JSON object per line on stdin/stdout). Holds the tool table `TOOLS` and the FL-side code snippets. No third-party packages. |
| `server/bridge.py` | Sends a Python snippet to FL as SysEx frames and waits for the reply. |
| `server/winmidi.py` | Minimal WinMM MIDI in/out through `ctypes`. |
| `fl_script/device_ClaudeBridge.py` | Runs **inside FL** as a MIDI controller script: joins SysEx chunks, `eval`/`exec`s the code with FL's modules (`mixer`, `channels`, `transport`, ...), sends back `{ok, value, output}`. |
| `fl_script/Claude Notes.pyscript` | FL piano roll script: applies queued note requests from a JSON file and writes the roll's notes back. |
| `server/flhands.py` | "Hands-off" window layer: finds FL's windows, captures them off-screen (`PrintWindow`), posts mouse and key messages to FL only, answers file dialogs, starts and closes FL. |
| `server/overlay.py` | The click-through "hands off" banner (its own process; heartbeat file plus release hooks). |
| `server/config.py` | All settings: defaults, auto-detection, `config.json`, env vars, calibration. |
| `server/browser.py`, `pianoroll.py`, `playlist_place.py`, `automation_clip.py`, `export.py`, `render.py`, `knobtable.py`/`knobset.py`, `kits.py`, `bbcso.py`, `ollama_eyes.py` | One feature each (see [docs/TOOLS.md](docs/TOOLS.md)). |

## The wire format

Each frame (all bytes after `F0` are 7-bit):

```
F0 7D <kind> <id hi> <id lo> <chunk hi> <chunk lo> <count hi> <count lo> <ascii payload ...> F7
kind 0x01 = command (server -> FL), 0x02 = reply (FL -> server); payload chunks are <= 200 bytes
```

The joined payload is a Python string literal (`ascii()` of the code). The reply is a dict literal parsed with
`ast.literal_eval`. Message ids 1-15000 are used by the server; 15001+ are free for other tools sharing the
ports.

## Why posted clicks?

FL's scripting API has no calls for playlist clips, automation clips or the Export window. Instead of screen
control (which moves the real mouse and hides other apps), the bridge:

1. captures the FL window with `PrintWindow` (works while FL is behind other windows),
2. finds things by reading pixels or, better, FL's own **hint bar** (`ui.getHintMsg()` after a posted
   `WM_MOUSEMOVE` tells what's under a point, e.g. `"5:01:00"` on the playlist grid),
3. posts `WM_LBUTTONDOWN`/`UP` to the deepest child window at that point,
4. **reads the result back** and errors out rather than guessing.

## Adding a tool

1. If FL's API can do it, write an FL-side snippet in `server.py` (a string; `ARGS` holds the tool's
   arguments, assign the answer to `result`):

   ```python
   METRONOME = r'''
   if bool(ui.isMetronomeEnabled()) != bool(ARGS['on']):
       transport.globalTransport(midi.FPT_Metronome, 1)
   result = {'metronome_on': bool(ui.isMetronomeEnabled())}
   '''
   ```
2. Add a row to `TOOLS`: `(name, description, properties, required, handler)`. The description is what Claude
   reads, so say what it does, the units, and any caveat.
   ```python
   ('fl_metronome', 'Turn the metronome on or off.', {'on': BOOL}, ['on'], lambda a: fl(METRONOME, on=a['on'])),
   ```
3. If it only reads, add its name to `QUIET` (no banner).
4. If it needs clicks, put the logic in its own module that uses `flhands`, and put any pixel positions in
   `config.DEFAULTS['calibration']`.
5. Add a check to `tests/test_loopback.py` if it's protocol-level, and document it in `docs/TOOLS.md`.

Quick ad-hoc test without an MCP client:

```powershell
$env:FL_OVERLAY = "0"   # no banner while testing
python -c "import sys; sys.path.insert(0,'server'); import server; print(server.TOOL_MAP['fl_status'][4]({}))"
```

## Tests

`python tests/test_loopback.py` runs the whole protocol against fake FL modules (no FL or MIDI needed).
Anything that touches FL's windows needs a real FL. Please say in your PR what you tested by hand, on which FL
version.
