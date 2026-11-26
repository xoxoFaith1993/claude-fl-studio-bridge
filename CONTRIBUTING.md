# Contributing

Thanks for helping. This bridge is a work in progress and it's never perfect. That's why it's shared.
Musicians, testers and coders are all welcome, and you don't need to be an expert.

## Ways to help (no code needed)
- **Try it on your setup** and report what happened: FL version, Windows version, display scaling, and MIDI
  ports (loopMIDI or Windows MIDI Services). Successes are useful too.
- **Calibration values** for other FL versions or scalings ([docs/CALIBRATION.md](docs/CALIBRATION.md)).
- **Docs:** anything that confused you is a docs bug.

## Reporting a bug
Use the bug report form under **Issues → New issue**. Please include:
- what you asked Claude, and which tool failed (the error text),
- `python server\config.py` output. **Check it for anything private before posting**: it shows your folders.
- FL Studio version and Windows display scaling.

Please don't post `.flp` projects or audio you don't want public.

## Code
- **Python standard library only** in `server/` (the installer ships a bare Python). Optional extras like
  numpy must stay optional, with a clear message when missing.
- **Windows-only APIs through `ctypes`** (`winmidi.py`, `flhands.py`).
- **No screen control.** Use posted messages to FL's own windows, and never move the real mouse or focus other
  apps.
- **Verify, don't guess:** click-based tools must read the result back, and raise a clear error when it isn't
  what they expected.
- **Never overwrite the user's work silently:** no saving unless asked, no overwriting named inserts or EQ, refuse
  when a project has unsaved changes.
- **Nothing machine-specific in code:** paths, port names and pixel positions go in `server/config.py`
  (`DEFAULTS`, with a comment).
- New or changed tool: run `python tools\gen_tools_md.py` so `docs/TOOLS.md` stays current.
- Run `python tests\test_loopback.py`, `tests\test_doctor.py`, `tests\test_musicgen.py` and `tests\test_muse.py` before a PR. If you add a way things can break, add a check to `server/doctor.py` with a plain fix line. Say in the PR what you tested by hand in FL, and on which
  version.
- Style: keep functions small and comments plain. Match the code around you.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit and how to add a tool.

## Pull requests
1. Fork, make a branch, keep the PR to one topic.
2. Describe what changed, why, and how you tested it.
3. By contributing, you agree your contribution is licensed under the project's [GPL-3.0](LICENSE).

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
