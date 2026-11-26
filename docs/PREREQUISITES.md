# Prerequisites and their licences

This project is GPL-3.0. Nothing below is part of it. Each item is a separate download with its own licence, and
only Python is bundled by the installer. Licences were checked on 2026-11-25; check the linked pages for
current terms.

| Item | Needed? | Licence | Open source | Where |
|---|---|---|---|---|
| FL Studio (Image-Line) | yes | proprietary, paid | no | [image-line.com](https://www.image-line.com/) |
| loopMIDI (Tobias Erichsen) | one of these two | freeware, "free for private, non-commercial use"; no redistribution without the author's consent (so it's never bundled); commercial users are asked to contact the author | no | [tobias-erichsen.de/software/loopmidi.html](https://www.tobias-erichsen.de/software/loopmidi.html) |
| Windows MIDI Services (Microsoft) | one of these two | MIT | **yes** | in-box on recent Windows 11 (24H2 / 25H2, rolled out in phases); tools from [aka.ms/midireleases](https://aka.ms/midireleases), source [github.com/microsoft/MIDI](https://github.com/microsoft/MIDI) |
| Claude Code (Anthropic), or any MCP client | yes | proprietary | no | [claude.com/claude-code](https://claude.com/claude-code) |
| Python (PSF) | bundled by the installer | PSF License v2 (redistribution allowed; the licence file ships inside `python\`) | yes | [python.org](https://www.python.org/downloads/windows/) |
| numpy | optional (render loudness check) | BSD-3-Clause | yes | `pip install numpy` |
| Ollama | optional (`fl_read_window`) | MIT | yes | [ollama.com](https://ollama.com) |
| qwen3-vl (Alibaba Qwen) vision model | optional, used with Ollama | Apache-2.0 (per the Qwen3-VL-8B-Instruct model card) | open weights | `ollama pull qwen3-vl:8b-instruct` |
| BBC Symphony Orchestra Discover (Spitfire Audio) | optional (`fl_bbcso_*`) | proprietary, free of charge (Spitfire account needed) | no | [spitfireaudio.com](https://www.spitfireaudio.com/bbc-symphony-orchestra-discover/) |
| Inno Setup | only to build the installer | Inno Setup License (free for any use; commercial users are asked to buy a licence) | source available | [jrsoftware.org](https://jrsoftware.org/isinfo.php) |

## The fully open option

With **Windows MIDI Services** instead of loopMIDI, every piece the bridge itself needs is open source except
FL Studio and your MCP client. Microsoft says loopback endpoints work for all MIDI 1.0 apps, including the classic
WinMM API that FL Studio and this bridge use. **It hasn't been tested with FL Studio yet**, and a report either way
would really help ([open a setup report](https://github.com/xoxoFaith1993/claude-fl-studio-bridge/issues/new/choose)). Setup steps: [SETUP.md](SETUP.md#option-b-windows-midi-services).

## FL Studio trial

FL's trial version can't reopen saved projects until you buy it. Image-Line doesn't say whether MIDI controller
scripts run in the trial. If you try it, please tell us.
