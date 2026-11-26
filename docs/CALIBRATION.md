# Calibration: when a click-based tool misses

Most tools use FL's scripting API and work the same on every PC. A few things have no API, so the bridge posts
mouse clicks to FL's own windows at known spots. Those spots were measured on **FL Studio 2026 (26.1) at 100%
Windows display scaling** with the default FL theme. On another version, scaling or layout a click can miss,
and you can fix it in `config.json` without touching code.

| Setting (`calibration.*`) | Used by | What it points at |
|---|---|---|
| `piano_roll_tools_icon` `[x, y]` | `fl_piano_roll_write/read` | the wrench (Tools) icon at the top-left of the piano roll window |
| `file_menu_label` `[x, y]` | `fl_render` (in-app route) | the word FILE in FL's main menu bar |
| `export_row_y` | `fl_render` | the centre height of the highlighted "Export" row in the open File menu |
| `browser_first_row` `[x, y]` | `fl_project` open/new | the first row of the Browser tree (focuses the Browser after a project opens) |
| `record_dialog_rows` / `record_dialog_height` | `fl_transport record` | the buttons in "What would you like to record?" |
| `render_window.led` / `.label` / `.start` | `fl_render` | the format LEDs, their labels and the Start button in the Export window |

Playlist and automation tools (`fl_playlist`, `fl_automation`) don't use fixed spots: they measure the playlist
through FL's hint bar each time, so they usually adapt by themselves.

## How to measure a spot

All coordinates are **window pixels**: the top-left of that FL window is 0,0, at full size.

1. With FL open, ask Claude: *"Take an fl_snapshot of the piano roll window"* (or `"main"`, or a dialog title).
   Claude gets the picture and can read the position of the icon or button you mean.
2. Or run `python server\flhands.py snap "Piano roll"` yourself. It saves a half-size PNG in
   `%LOCALAPPDATA%\claude-fl-studio-bridge\snaps`. Open it in any image viewer, read x,y, and **double them**
   (half size).
3. Put the numbers in `%APPDATA%\claude-fl-studio-bridge\config.json`, for example:

```json
{ "calibration": { "piano_roll_tools_icon": [44, 17] } }
```

4. Restart the MCP server (start a new Claude Code session).

If you calibrate for another FL version or scaling, please share the values in an issue or PR, so we can make
the defaults detect it automatically.
