#define AppVersion "0.2.0"
#define AppName "WeChatTextExport"

[Setup]
AppId={{5B47D90E-929A-4EAB-81E0-E285AC725599}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\WeChatTextExport\versions\{#AppVersion}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release\v{#AppVersion}
OutputBaseFilename=WeChatTextExport-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
Uninstallable=no
WizardStyle=modern

[Files]
Source: "dist\WeChatTextExport\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "install_task.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  CommandLine: String;
begin
  if CurStep = ssPostInstall then
  begin
    CommandLine := '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\install_task.ps1') + '" -Program "' + ExpandConstant('{app}\WeChatTextExport.exe') + '"';
    if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), CommandLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
      RaiseException('Could not start the background task. See install.log in LocalAppData\WeChatTextExport.');
  end;
end;
