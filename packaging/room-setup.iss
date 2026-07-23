; Room Suite - one-click Windows installer (Inno Setup)
;
; Product goal: double-click Setup.exe -> engine + skill + PATH + env ready.
; Single install engine: install.ps1 (same as zip suite). Inno only stages
; the full suite under {app} then runs that script; non-zero exit = install fails.
;
; Build: scripts\build-windows-suite.ps1 (stages dist\installer-payload first)
; Manual: ISCC packaging\room-setup.iss

#define AppName "Room"
#define AppVersion "0.1.5"
#define AppPublisher "zonebond"
#define AppURL "https://gitea.orb.local/zonebond/room-tui"
#define AppExeName "room.exe"
#define EngineExeName "paper-derived.exe"
#define OobExeName "oob-divzero.exe"
#define PayloadDir "..\dist\installer-payload"

[Setup]
AppId={{B8F4A3D2-7E6C-4A1B-9D5F-2C8E0A6B4F91}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\Programs\Room
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Room-Setup-{#AppVersion}-windows-x86_64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName} Suite {#AppVersion}
UninstallDisplayIcon={app}\bin\{#AppExeName}
; Compile fails if required sources are missing (no skip on engine/skill/install.ps1)
LicenseFile=..\README.md

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
english.BeveledLabel=Room Suite
english.FinishedLabel=Setup finished.%n%nOpen a NEW terminal and run:%n  room doctor%n%nExpect: engine OK, skills OK (paper-derived + oob-divzero).%nIf suite includes tools\c-toolchain, doctor shows asan toolchain bundled.%nIf suite includes tools\libreoffice, doctor shows doc converter OK.

[Files]
; Full suite layout under {app} so install.ps1 and uninstall share one tree
; REQUIRED - compile fails if absent
Source: "{#PayloadDir}\bin\room.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#PayloadDir}\bin\paper-derived.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
; Capability #2 (required for full product; skip if transitional payload omitted)
Source: "{#PayloadDir}\bin\oob-divzero.exe"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PayloadDir}\skills\*"; DestDir: "{app}\skills"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\install.bat"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Optional agent + sidecars
Source: "{#PayloadDir}\bin\pi.exe"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PayloadDir}\bin\theme\*"; DestDir: "{app}\bin\theme"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#PayloadDir}\bin\assets\*"; DestDir: "{app}\bin\assets"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#PayloadDir}\bin\room.BUILD.txt"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PayloadDir}\VERSION"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PayloadDir}\required-skills.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PayloadDir}\config.example.toml"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#PayloadDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Optional: LibreOffice (.doc) + C toolchain (oob ASan)
Source: "{#PayloadDir}\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
Name: "{app}\bin"
Name: "{app}\skills"
Name: "{app}\tools"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\bin\{#AppExeName}"; Comment: "Room TUI - AI document workspace"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\bin\{#AppExeName}"; Comment: "Room TUI"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
; PATH is always applied in [Code] (one-click product requirement)

[Code]
function GetUserProfile: string;
begin
  Result := GetEnv('USERPROFILE');
  if Result = '' then
    Result := ExpandConstant('{userdocs}\..');
end;

function RoomPiAgentRoot: string;
begin
  { Room-branded pi default: ~/.config/room-tui/agent }
  Result := GetUserProfile + '\.config\room-tui\agent';
end;

procedure SetUserEnv(const Name, Value: string);
begin
  RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', Name, Value);
end;

procedure EnsureUserPath(const BinDir: string);
var
  Path, NewPath: string;
begin
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
  begin
    if Pos(Uppercase(BinDir), Uppercase(Path)) = 0 then
    begin
      NewPath := Path;
      if NewPath <> '' then
        NewPath := NewPath + ';';
      NewPath := NewPath + BinDir;
      RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', NewPath);
    end;
  end
  else
    RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', BinDir);
end;

function RunInstallPs1: Boolean;
var
  ResultCode: Integer;
  AppDir, Ps1, Bat, Params, LogHint: string;
begin
  Result := False;
  AppDir := ExpandConstant('{app}');
  Ps1 := AppDir + '\install.ps1';
  Bat := AppDir + '\run-install.cmd';
  LogHint := ExpandConstant('{%TEMP}\room-install.log');
  if not FileExists(Ps1) then
  begin
    MsgBox('install.ps1 missing under:' + #13#10 + Ps1 + #13#10 +
      'Installer package is incomplete. Re-run build-windows-suite.ps1.', mbError, MB_OK);
    Exit;
  end;
  { Write a tiny cmd wrapper so ROOM_HOME + -File are reliable (no quoting hell). }
  SaveStringToFile(Bat,
    '@echo off' + #13#10 +
    'set "ROOM_HOME=' + AppDir + '"' + #13#10 +
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + Ps1 + '"' + #13#10 +
    'exit /b %ERRORLEVEL%' + #13#10,
    False);
  Log('Running one-click install via ' + Bat + ' ROOM_HOME=' + AppDir);
  if not Exec(ExpandConstant('{cmd}'), '/c "' + Bat + '"', AppDir, SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Could not start install.ps1 via cmd.', mbError, MB_OK);
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox('Room install.ps1 failed (exit ' + IntToStr(ResultCode) + ').' + #13#10 +
      'See log:' + #13#10 + LogHint + #13#10 + #13#10 +
      'Required: paper-derived + oob-divzero skills (and CLIs when packaged).' + #13#10 +
      'Re-build Setup after git pull, then re-run.', mbError, MB_OK);
    Exit;
  end;
  Result := True;
end;

function VerifyProductReady: Boolean;
var
  BinDir, PdExe, OobExe, SkillProd, SkillPi, OobSkillProd, OobSkillPi: string;
begin
  Result := False;
  BinDir := ExpandConstant('{app}\bin');
  PdExe := BinDir + '\{#EngineExeName}';
  OobExe := BinDir + '\{#OobExeName}';
  SkillProd := ExpandConstant('{app}\skills\paper-derived\SKILL.md');
  SkillPi := RoomPiAgentRoot + '\skills\paper-derived\SKILL.md';
  OobSkillProd := ExpandConstant('{app}\skills\oob-divzero\SKILL.md');
  OobSkillPi := RoomPiAgentRoot + '\skills\oob-divzero\SKILL.md';

  if not FileExists(ExpandConstant('{app}\bin\{#AppExeName}')) then
  begin
    MsgBox('room.exe missing after install.', mbError, MB_OK);
    Exit;
  end;
  if not FileExists(PdExe) then
  begin
    MsgBox('paper-derived.exe missing after install:' + #13#10 + PdExe, mbError, MB_OK);
    Exit;
  end;
  if not FileExists(SkillProd) then
  begin
    MsgBox('paper-derived skill missing under product skills:' + #13#10 + SkillProd, mbError, MB_OK);
    Exit;
  end;
  if not FileExists(SkillPi) then
  begin
    MsgBox('paper-derived skill missing under Room pi-agent:' + #13#10 + SkillPi + #13#10 +
      'install.ps1 did not seed required skills.', mbError, MB_OK);
    Exit;
  end;
  if not FileExists(OobSkillProd) then
  begin
    MsgBox('oob-divzero skill missing under product skills:' + #13#10 + OobSkillProd, mbError, MB_OK);
    Exit;
  end;
  if not FileExists(OobSkillPi) then
  begin
    MsgBox('oob-divzero skill missing under Room pi-agent:' + #13#10 + OobSkillPi + #13#10 +
      'install.ps1 did not seed required skills.', mbError, MB_OK);
    Exit;
  end;
  if not FileExists(OobExe) then
    Log('WARN: oob-divzero.exe missing (suite may have used -AllowNoOob)');
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  BinDir, PiExe: string;
begin
  if CurStep = ssPostInstall then
  begin
    BinDir := ExpandConstant('{app}\bin');

    { 1) One-click install engine (same as zip) }
    if not RunInstallPs1 then
      RaiseException('Room one-click install failed (install.ps1).');

    { 2) Hard verify: engines + required skills in product + pi-agent }
    if not VerifyProductReady then
      RaiseException('Room install verification failed (engine or required skills).');

    { 3) Always PATH + env (one-click).
      Room-branded pi: ROOM_CODING_AGENT_DIR (not system ~/.pi) }
    EnsureUserPath(BinDir);
    SetUserEnv('ROOM_INSTALL_BIN', BinDir);
    SetUserEnv('ROOM_HOME', ExpandConstant('{app}'));
    SetUserEnv('ROOM_CODING_AGENT_DIR', RoomPiAgentRoot);
    SetUserEnv('ROOM_PI_AGENT_DIR', RoomPiAgentRoot);
    SetUserEnv('PI_CODING_AGENT_DIR', RoomPiAgentRoot);
    PiExe := BinDir + '\pi.exe';
    if FileExists(PiExe) then
      SetUserEnv('PI_BIN', PiExe);
    if FileExists(BinDir + '\{#OobExeName}') then
      SetUserEnv('OOB_DIVZERO_BIN', BinDir + '\{#OobExeName}');
    { Bundled LibreOffice for .doc (optional payload) }
    if FileExists(ExpandConstant('{app}\tools\libreoffice\program\soffice.exe')) then
    begin
      SetUserEnv('ROOM_LIBREOFFICE',
        ExpandConstant('{app}\tools\libreoffice\program\soffice.exe'));
      SetUserEnv('PAPER_DERIVED_LIBREOFFICE',
        ExpandConstant('{app}\tools\libreoffice\program\soffice.exe'));
      Log('OK bundled LibreOffice soffice registered');
    end;
    { Bundled C toolchain for oob ASan }
    if FileExists(ExpandConstant('{app}\tools\c-toolchain\bin\clang.exe')) then
    begin
      SetUserEnv('OOB_CC',
        ExpandConstant('{app}\tools\c-toolchain\bin\clang.exe'));
      SetUserEnv('ROOM_CC',
        ExpandConstant('{app}\tools\c-toolchain\bin\clang.exe'));
      SetUserEnv('ROOM_C_TOOLCHAIN',
        ExpandConstant('{app}\tools\c-toolchain'));
      EnsureUserPath(ExpandConstant('{app}\tools\c-toolchain\bin'));
      Log('OK bundled c-toolchain clang registered');
    end;

    Log('OK one-click install verified: room + paper-derived + oob-divzero skills');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Path, BinDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    BinDir := ExpandConstant('{app}\bin');
    if RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
    begin
      StringChangeEx(Path, BinDir + ';', '', True);
      StringChangeEx(Path, ';' + BinDir, '', True);
      StringChangeEx(Path, BinDir, '', True);
      StringChangeEx(Path, ';;', ';', True);
      if (Length(Path) > 0) and (Path[Length(Path)] = ';') then
        Delete(Path, Length(Path), 1);
      RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path);
    end;
  end;
end;
