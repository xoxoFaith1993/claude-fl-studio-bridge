# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org/); before 1.0 anything may change.

## [0.1.0] - 2026-11-25
First public release (work in progress).

- MCP server with 40 tools: transport, tempo, mixer, channel rack, step sequencer, patterns, plugin parameters
  and presets, Browser loading, piano roll read/write, playlist clips, automation clips (volume, sidechain
  pumps), display-value knobs, projects without dialogs, rendering with a loudness check, window tools
  (list, snapshot, click, key, file dialog), optional local vision reader, BBC SO Discover helpers, `fl_exec`.
- `fl_doctor` / `tools/doctor.py`: checks the whole chain and explains each problem with a fix; failing tools point to it.
- Optional local AI (Ollama): `fl_local_idea` (local model + music-theory fixer, no clashing notes), `fl_read_window`, popups read automatically when FL is stuck; Ollama starts on demand and stops after FL closes (only if the bridge started it).
- `fl_automation create_pan`: pan automation clips.
- `fl_drum_kit` loads your own kit from `kit.json`.
- Configurable "hands off" banner (default "Chill bro, let me solo her...") with Claude Code hooks.
- One `config.json` for paths, ports, colours and calibration; FL Studio auto-detected.
- Setup.exe (Inno Setup) with bundled embeddable Python; `tools/connect_claude.py` for `claude mcp add` and
  hooks.
