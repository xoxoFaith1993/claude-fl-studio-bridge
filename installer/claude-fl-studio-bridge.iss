; Claude FL Studio Bridge installer (Inno Setup 6). Built by installer\build.ps1 - see docs/BUILDING.md.
; Installs per user (no admin): the bridge + its own small Python, the FL-side scripts, and optionally
; connects it to Claude Code (MCP server + the banner hooks).
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef StageDir
  #define StageDir "build\stage-fl"
#endif
#define MyAppName "Claude FL Studio Bridge"
#define RepoUrl "https://github.com/xoxoFaith1993/claude-fl-studio-bridge"

[Setup]
AppId={{9B3F2C61-4E7A-4D2B-8C55-1A6E0F7D3B92}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=claude-fl-studio-bridge contributors
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Claude-FL-Studio-Bridge-Setup-{#MyAppVersion}
LicenseFile={#StageDir}\LICENSE
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "flscripts"; Description: "Install the FL Studio scripts (MIDI controller script + Claude Notes piano roll script)"; GroupDescription: "FL Studio:"
Name: "mcp"; Description: "Register the fl-studio tools with Claude Code (claude mcp add)"; GroupDescription: "Claude Code:"
Name: "hooks"; Description: "Add the hooks that hide the 'hands off' banner when Claude's turn ends"; GroupDescription: "Claude Code:"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\fl_script\device_ClaudeBridge.py"; DestDir: "{code:FLUserData}\Settings\Hardware\Claude Bridge"; Flags: ignoreversion; Tasks: flscripts
Source: "{#StageDir}\fl_script\Claude Notes.pyscript"; DestDir: "{code:FLUserData}\Settings\Piano roll scripts"; Flags: ignoreversion; Tasks: flscripts

[Icons]
Name: "{userprograms}\{#MyAppName}\Setup guide"; Filename: "{#RepoUrl}/blob/main/docs/SETUP.md"
Name: "{userprograms}\{#MyAppName}\Settings folder"; Filename: "{userappdata}\claude-fl-studio-bridge"
Name: "{userprograms}\{#MyAppName}\Uninstall"; Filename: "{uninstallexe}"

[Dirs]
Name: "{userappdata}\claude-fl-studio-bridge"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\connect_claude.py"" --no-hooks"; StatusMsg: "Connecting to Claude Code..."; Flags: runhidden; Tasks: mcp
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\connect_claude.py"" --no-mcp"; StatusMsg: "Adding the banner hooks..."; Flags: runhidden; Tasks: hooks
Filename: "{app}\CONNECT_CLAUDE.txt"; Description: "Show how to connect Claude Code (the claude command was not found)"; Flags: postinstall shellexec skipifsilent; Check: NeedsConnectNote
Filename: "{#RepoUrl}/blob/main/docs/SETUP.md"; Description: "Open the setup guide (MIDI ports + FL settings)"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
; only undo what this install set up (markers written after install); other installs and your own hooks stay
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\connect_claude.py"" --remove --no-hooks"; Flags: runhidden; RunOnceId: "DisconnectMcp"; Check: TaskWasDone('mcp')
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\connect_claude.py"" --remove --no-mcp"; Flags: runhidden; RunOnceId: "RemoveHooks"; Check: TaskWasDone('hooks')

[UninstallDelete]
Type: filesandordirs; Name: "{app}\server\__pycache__"
Type: filesandordirs; Name: "{app}\tools\__pycache__"
Type: filesandordirs; Name: "{app}\tests\__pycache__"
Type: files; Name: "{app}\CONNECT_CLAUDE.txt"
Type: files; Name: "{app}\task-*.done"

[Code]
{ FL's user data folder: FL writes it to HKCU\Software\Image-Line\Shared\Paths "Shared data" (+ "FL Studio"). }
function FLUserData(Param: String): String;
var
  Shared: String;
begin
  Result := ExpandConstant('{userdocs}\Image-Line\FL Studio');
  if RegQueryStringValue(HKCU, 'Software\Image-Line\Shared\Paths', 'Shared data', Shared) then
    if DirExists(AddBackslash(Shared) + 'FL Studio') then
      Result := AddBackslash(Shared) + 'FL Studio';
end;

{ remember which optional steps ran, so the uninstaller undoes only those }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('mcp') then SaveStringToFile(ExpandConstant('{app}\task-mcp.done'), 'yes', False);
    if WizardIsTaskSelected('hooks') then SaveStringToFile(ExpandConstant('{app}\task-hooks.done'), 'yes', False);
  end;
end;

function TaskWasDone(Name: String): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\task-' + Name + '.done'));
end;

function NeedsConnectNote: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\CONNECT_CLAUDE.txt'));
end;
