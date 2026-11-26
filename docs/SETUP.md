# Setup (about 5 minutes, once)

The installer, or `tools\install_fl_scripts.py` plus `tools\connect_claude.py`, has already put the files in
place. What's left is connecting FL Studio and the bridge through two virtual MIDI ports.

## 1. Make two virtual MIDI ports

They must be named exactly **`Claude to FL`** and **`FL to Claude`**. Other names work if you also set
`port_to_fl` / `port_from_fl` in `config.json`.

### Option A: loopMIDI (works today)

1. Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) (freeware for private use).
2. Open it, type `Claude to FL` in "New port-name" and press **+**. Do the same for `FL to Claude`.
3. Keep loopMIDI running; it can start with Windows from its tray icon menu.

### Option B: Windows MIDI Services

This option is fully open source (MIT), but untested with FL Studio so far, so please report how it goes.

1. You need Windows 11 with Windows MIDI Services switched on (24H2 or newer, rolled out in phases). Get the
   **MIDI Settings** app from the Tools download at [aka.ms/midireleases](https://aka.ms/midireleases).
2. In MIDI Settings, create **two loopback pairs**, one named `Claude to FL` and one named `FL to Claude`.
   Choose the classic (MIDI 1.0 / WinMM) port names, so older apps see exactly those names. WinMM names are
   cut off at 31 characters.
3. On each loopback, set the feedback protection to **"Do nothing"**. FL listens and sends on these ports, so
   the default protection could mute them.
4. Carry on with step 2 below. If FL or the bridge can't see the ports, run
   `python -c "import sys; sys.path.insert(0,'server'); import winmidi; print(winmidi.input_names(), winmidi.output_names())"`
   from the install folder and post the output in an issue.

## 2. Tell FL Studio about the ports

FL Studio → **Options → MIDI settings** (F10):

- **Input** list: select **Claude to FL**, tick **Enable**, set **Controller type** to **Claude Bridge**,
  and give it a **Port** number (any free one, e.g. 8).
- **Output** list: select **FL to Claude** and set the **same port number**.
- Make sure **Claude to FL** is *not* also given that port in the Output list. If both ports share a number
  there, FL sends its replies to the wrong port.

Then **View → Script output**: it should say `Claude Bridge ready (port 8)`.

If "Claude Bridge" isn't in the controller type list, the script isn't installed. Check that
`Documents\Image-Line\FL Studio\Settings\Hardware\Claude Bridge\device_ClaudeBridge.py` exists, then click
"Update MIDI scripts" (or restart FL).

## 3. Piano roll script (FL 2024 and newer)

`Claude Notes.pyscript` goes in `Documents\Image-Line\FL Studio\Settings\Piano roll scripts\` (the installer
puts it there). The bridge runs it through the piano roll's **Tools → Scripts** menu by itself. If writing
notes fails once after an FL restart, run it by hand once from that menu.

## 4. Connect Claude Code

If you didn't tick the installer's Claude Code boxes, run:

```powershell
python tools\connect_claude.py            # or --print to only show the commands
```

It runs `claude mcp add --scope user fl-studio -- <python> <server.py>` and adds two hooks
(`Stop` and `UserPromptSubmit`) to `~/.claude/settings.json` that run `overlay.py release`. These make the
"hands off" banner fade as soon as Claude's turn ends. Without them it fades by itself after 90 s.
**Start a new Claude Code session** so the tools load.

Other MCP clients: point them at `<python> server/server.py` (stdio transport).

## 5. Try it

Ask Claude: *"Check the FL Studio connection"*, then *"Set the tempo to 140 and make a 4-bar hi-hat pattern on
channel 1"*.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No reply from FL Studio within 10s" | FL is closed, or the controller type isn't "Claude Bridge", or input and output port numbers differ. |
| Works once, then silent after an FL restart | Both ports landed on the same Output port number: give **Claude to FL** a different (or no) output port. |
| "FL is blocked by a dialog" | A popup is open in FL. Claude can read and answer it (`fl_windows`, `fl_snapshot`, `fl_click`), or close it yourself. |
| A click-based tool misses (playlist, automation, export) | Different FL version or screen scaling: see [CALIBRATION.md](CALIBRATION.md). |
| Banner never goes away | Hooks not installed: run `python tools\connect_claude.py --no-mcp`, or set `"banner": {"enabled": false}`. |
| `fl_project` can't find templates | Set `"fl_exe"` (or `"templates_dir"`) in `config.json`; `python server\config.py` shows what was found. |

Still stuck? [Open an issue](https://github.com/xoxoFaith1993/claude-fl-studio-bridge/issues/new/choose) and include
the output of `python server\config.py` and your FL version.
