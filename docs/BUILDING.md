# Building the installer yourself

The Setup.exe on the Releases page is built from this repo by one script, so you can build exactly the same
thing.

## You need
- Windows 10/11 and PowerShell 5.1+
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (free). The script looks in the usual install folders;
  otherwise pass `-Iscc <path to ISCC.exe>`.
- Internet once: the script downloads the official **embeddable Python** from python.org and checks its
  SHA-256 against the value pinned in `installer/build.ps1`.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

Output: `dist\Claude-FL-Studio-Bridge-Setup-<version>.exe` plus a `.sha256` file. The version comes from
`VERSION` in `server/server.py`.

What it does:
1. downloads `python-<ver>-embed-amd64.zip` into `installer\build\` (cached) and verifies it,
2. copies an **allow-list** of folders and files (`server`, `fl_script`, `tools`, `docs`, `tests`, README, LICENSE,
   CHANGELOG, the example configs) into `installer\build\stage-fl`, so nothing else in your working copy
   can end up in the installer,
3. unpacks Python into `stage-fl\python`,
4. compiles `installer\claude-fl-studio-bridge.iss` with ISCC.

To use a newer Python, pass `-PythonVersion 3.14.x -PythonSha256 <hash>`. The hash is in the `.spdx.json` file
next to the download on python.org.

## What the installer does

- installs per user to `%LOCALAPPDATA%\Programs\Claude FL Studio Bridge` (no admin),
- checkbox: copies `device_ClaudeBridge.py` and `Claude Notes.pyscript` into FL's user data folder (read from
  FL's own registry entry, default `Documents\Image-Line\FL Studio`),
- checkbox: `connect_claude.py --no-hooks` (registers the MCP server with Claude Code, if `claude` is on PATH),
- checkbox: `connect_claude.py --no-mcp` (adds the banner hooks to `~/.claude/settings.json`, after a backup),
- uninstall removes the files, the MCP registration and its own hooks (other hooks are left alone), and keeps
  your `%APPDATA%\claude-fl-studio-bridge` settings.

## Test an installer without touching your setup

```powershell
.\dist\Claude-FL-Studio-Bridge-Setup-0.1.0.exe /VERYSILENT /CURRENTUSER /DIR="$env:TEMP\flb-test" /TASKS=""
& "$env:TEMP\flb-test\python\python.exe" "$env:TEMP\flb-test\tests\test_loopback.py"
& "$env:TEMP\flb-test\python\python.exe" "$env:TEMP\flb-test\tests\test_doctor.py"
& "$env:TEMP\flb-test\unins000.exe" /VERYSILENT
```

`/TASKS=""` skips all three checkboxes, so FL's script folders and your Claude settings aren't touched.

## Releasing (maintainers)
1. Bump `VERSION` in `server/server.py` and add a `CHANGELOG.md` entry.
2. `python tools\gen_tools_md.py --check` (the tool list must be current).
3. Run the privacy scan: `powershell -File tools\privacy_scan.ps1`.
4. Build, test-install as above, then
   `gh release create vX.Y.Z dist\Claude-FL-Studio-Bridge-Setup-X.Y.Z.exe dist\*.sha256`.
