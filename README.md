# Claude FL Studio Bridge

![status: work in progress](https://img.shields.io/badge/status-work%20in%20progress-orange)
![license: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)
![platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)
![MCP server](https://img.shields.io/badge/MCP-server-8A2BE2)

**Let Claude work inside a running FL Studio while you keep working in it by hand.**

You ask Claude (in [Claude Code](https://claude.com/claude-code) or any app that speaks MCP) for a chord
progression, a drum pattern, a mixer change or a render. Claude does it live in your open FL Studio project, and
you can see and change everything it did. It's a two-way bridge, not a one-shot generator.

> **This is a work in progress, and it's never perfect.** It has grown out of daily use on one PC. Some parts
> click inside FL's windows and were measured on one screen layout, so they may need a small calibration on
> yours ([docs/CALIBRATION.md](docs/CALIBRATION.md)). Bug reports, fixes and new tools are very welcome; see
> [CONTRIBUTING.md](CONTRIBUTING.md).

## What it can do

- **Mixer and channel rack:** read and change volume, pan, mute, solo, names, colours and routing.
- **Notes:** write and read notes straight into the piano roll (FL 2024+), program step-sequencer patterns,
  play notes live, or write a `.mid` file.
- **Sounds:** browse FL's Browser and load plugins, samples and presets; set plugin knobs by the value FL shows
  ("-12 dB", "250 Hz") instead of raw 0-1 numbers.
- **Arrangement:** place, copy, trim and cut pattern clips on the playlist, at any zoom.
- **Automation:** create volume and pan automation clips and draw points, curves and sidechain pumps.
- **Projects:** start a new project from a template, open, save and close projects without Open/Save dialogs,
  and render to WAV/MP3/OGG/FLAC with a loudness and peak check.
- **Your drum kit in one call:** describe your kit once in `kit.json`, then "set up my drum kit" loads it.
- **Hands off, not screen control:** dialogs and plugin windows are read with off-screen captures, and clicks
  and keys are posted to FL's own windows only. Your real mouse doesn't move and other apps aren't hidden.
  While Claude clicks inside FL, a banner reminds you to keep your hands off (default text:
  *"Chill bro, let me solo her..."*; change it or turn it off in `config.json`).
- **A built-in doctor:** `fl_doctor` (or `python tools\doctor.py`) checks every link from Claude to FL: MIDI
  ports, the FL scripts, FL itself, port settings, popups, screen scaling, hooks and the local AI. It says in plain
  words what's wrong and how to fix it, so a problem gets explained instead of the bridge just timing out.
- **Optional local AI (Ollama), fully offline:**
  - `fl_local_idea`: a local model writes a piano idea, and music-theory code fixes it before anything reaches FL
    (in-key chords, smooth voicing, melody on chord tones, no clashing notes).
  - `fl_read_window`: a local vision model reads FL popups.
  - When FL is stuck on a popup, the bridge reads it and tells Claude what it says.
  - Ollama starts only when one of these tools needs it, and stops again after FL closes. It only stops an
    Ollama the bridge started itself.
- **Anything else:** `fl_exec` runs Python inside FL with all of FL's scripting modules.

The full list of 40 tools is in [docs/TOOLS.md](docs/TOOLS.md).

## How it works (short version)

```
Claude ──MCP (stdio)──> server/server.py ──SysEx on "Claude to FL"──> FL MIDI script "Claude Bridge"
       <────────────── results as SysEx on "FL to Claude" <──────────  (runs the code with FL's API)
```

FL Studio's scripting API is reachable only from inside FL, through a MIDI controller script. So the bridge
sends small Python snippets over a virtual MIDI cable, and the script inside FL runs them and sends the
answer back. Things the API can't do (playlist clips, automation clips, the Export dialog) are done by posting
clicks to FL's windows and checking the result. More detail, and how to add a tool:
[ARCHITECTURE.md](ARCHITECTURE.md).

## What you need

| | | |
|---|---|---|
| Windows 10 or 11 | | the bridge uses Windows APIs (WinMM MIDI, window messages) |
| [FL Studio](https://www.image-line.com/) | proprietary, not included | FL 2024 or newer is best (piano-roll scripts, Browser, `plugins` module). FL 20.7+ works with fewer tools. |
| Two virtual MIDI ports | | [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) (freeware for private, non-commercial use; closed source; not bundled), or the open-source **Windows MIDI Services** loopback endpoints on recent Windows 11 (untested so far: [docs/SETUP.md](docs/SETUP.md#option-b-windows-midi-services)) |
| [Claude Code](https://claude.com/claude-code) or another MCP client | | |
| Python 3.10+ | bundled by the installer | the server uses only the standard library |

Optional extras: [Ollama](https://ollama.com) with a model such as `qwen3-vl:8b-instruct` for the local AI tools (`fl_local_idea`, `fl_read_window`, reading stuck popups), `numpy` for the
render loudness check, and Spitfire's free BBC Symphony Orchestra Discover for the `fl_bbcso_*` tools.
Licences and links for each are in [docs/PREREQUISITES.md](docs/PREREQUISITES.md).

## Install

### Option 1: Setup.exe (easiest)

1. Download `Claude-FL-Studio-Bridge-Setup-<version>.exe` from
   [Releases](https://github.com/xoxoFaith1993/claude-fl-studio-bridge/releases) and run it. It installs for
   your user only (no admin), with its own small Python.
2. It can also install the FL-side scripts, register the tools with Claude Code (`claude mcp add`), and add the
   two hooks that hide the banner when Claude's turn ends. Each one is a checkbox.
3. Follow [docs/SETUP.md](docs/SETUP.md): create the two MIDI ports and pick "Claude Bridge" in FL's MIDI
   settings. It takes about 5 minutes, once.

### Option 2: from source

```powershell
git clone https://github.com/xoxoFaith1993/claude-fl-studio-bridge
cd claude-fl-studio-bridge
python tools\install_fl_scripts.py        # copies the FL-side scripts into FL's user data folder
python tools\connect_claude.py            # claude mcp add + banner hooks (--print to just show them)
python server\config.py                   # shows the settings it found (FL path etc.)
```

Then do the MIDI and FL steps in [docs/SETUP.md](docs/SETUP.md). To build the installer yourself, see
[docs/BUILDING.md](docs/BUILDING.md).

### Check it works

Start a new Claude Code session and ask: *"Check the FL Studio connection."* Claude calls `fl_status` and
should report your tempo and channel count. If anything fails, ask *"Run the FL doctor"* (or run
`python tools\doctor.py`): it checks each step and tells you the fix.

## Settings

Everything machine-specific is in one file, `%APPDATA%\claude-fl-studio-bridge\config.json` (start from
[config.example.json](config.example.json)): the FL path (auto-detected), where new projects go, the MIDI port
names, the banner text, the colour for new items, and the calibration values for the click-based tools.
`python server\config.py` prints what's in use and where each value came from.

## Good to know

- **Security:** the bridge runs Python inside FL Studio (that's how it works), and only over local virtual MIDI
  ports. Don't connect those ports to anything you don't trust. See [SECURITY.md](SECURITY.md).
- **Rough edges:** the honest list is [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).
- **Not affiliated** with Image-Line (FL Studio) or Anthropic (Claude). FL Studio is a trademark of Image-Line.

## Sister project

**[Claude Krita Bridge](https://github.com/xoxoFaith1993/claude-krita-bridge)** does the same for
[Krita](https://krita.org): Claude adds and edits layers in your open Krita canvas while you paint, with
optional AI cut-out, object removal and colour matching. Both bridges share one pattern: a small server with no
dependencies, a plugin inside the app, a single `config.json`, and red labels on everything Claude adds.

## Contributing

It's never perfect, and that's the point of sharing it. Good first issues: calibrating on other FL versions and
screen scalings, testing Windows MIDI Services, new tools (see [ARCHITECTURE.md](ARCHITECTURE.md#adding-a-tool)),
and docs. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).

## Thanks

- [FL-Studio-API-Stubs](https://github.com/MaddyGuthridge/FL-Studio-API-Stubs) by MaddyGuthridge: the reference
  for every FL scripting function.
- Other FL Studio MCP projects that shared ideas: karl-andres/fl-studio-mcp, rosasynthesiz/flstudio-mcp,
  dawhubapp/flstudio-mcp. [PyFLP](https://github.com/demberto/PyFLP) for reading `.flp` files.

## Licence

[GPL-3.0](LICENSE). You can use, study, change and share it. If you share a changed version, it has to stay
open source under the GPL too.
