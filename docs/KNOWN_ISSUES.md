# Known rough edges

The honest list. It's a work in progress, and fixes are welcome.

## Setup and environment
- **Windows only.** It uses WinMM MIDI and Win32 window messages. A macOS port would need a different MIDI
  layer and window automation (CoreMIDI and Accessibility).
- **Tested on one setup:** FL Studio 2026 (26.1), Windows 11, 100% scaling, default theme. FL 2024/2025 should
  work, and FL 20.7 works with fewer tools (no `plugins` module, piano-roll scripts or Browser loading).
- **Port number clash:** if "Claude to FL" and "FL to Claude" both get the same *output* port in FL's MIDI
  settings, replies go to the wrong port and every call times out (see [SETUP.md](SETUP.md#troubleshooting)).
- **Windows MIDI Services** loopback ports are untested with FL Studio.
- The piano roll script reads and writes its request files in `Documents\Image-Line\FL Studio\...`. If you
  moved FL's user data folder, notes may not arrive: copy the script there by hand and open an issue.

## Click-based tools (no API in FL for these)
- **Fixed positions** in the Export window, File menu and piano roll toolbar were measured at 100% scaling
  ([CALIBRATION.md](CALIBRATION.md)).
- **Popup menus ignore posted clicks.** The bridge steers them with FL's own `ui.down()` / `ui.right()` and
  posts Enter to the menu. If FL changes its menu order, a tool can land on the wrong item, and it stops with
  an error rather than guess.
- **A plugin window that has focus can steal keys**, so close plugin editors before piano roll reads and writes.
- **Clicks that miss a closed menu land on the playlist** and can paint a clip. Undo with Ctrl+Z (or
  `fl_exec general.undoUp()`).
- **Automation clips:** FL draws a tension handle between points, so points must be roughly 14 px apart at the
  current zoom (the tool zooms in when needed). The "Type in value" box opens at the real mouse pointer, so
  the bridge never uses it.
- **Creating an automation clip** switches the channel rack filter to "Automation"; the tool switches it back.
- Loading audio "as a playlist clip" drops it at the left edge of the playlist view.

## FL behaviour to know
- **Renders use the saved transport mode:** the "Empty" template opens in pattern mode, which drops playlist
  audio. `fl_project new` and `fl_render` switch to song mode for you.
- FL's command-line render needs FL closed, so `fl_render mode=cli` closes and reopens it. It refuses when
  there are unsaved changes unless you say save or discard.
- `transport.record()` opens a modal "What would you like to record?" box. `fl_transport record` answers it.
- After a project opens, FL's Browser has no focused row and Browser calls fail until one is clicked. The
  bridge clicks it for you.
- Starting FL from Claude's shell would tie FL's life to Claude's, so the bridge starts FL through Explorer
  with a temporary shortcut.

## Safety
- `fl_exec` runs any Python inside FL. That's by design: it's local only, through your own virtual ports.
- The bridge never saves unless asked, and `fl_drum_kit` never overwrites inserts, steps or EQ you set.
  Still, keep backups of projects you care about.
