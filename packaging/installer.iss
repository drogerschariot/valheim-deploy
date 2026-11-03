; The Windows installer, built with Inno Setup 6:
;   iscc /DAppVersion=0.5.0 packaging\installer.iss
; Installs for the current user only, so it never asks for admin rights.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{E40CDC6D-9CB0-41E1-B2AA-BD54656158D5}
AppName=Valheim Deploy
AppVersion={#AppVersion}
AppPublisher=Valheim Deploy
AppPublisherURL=https://drogerschariot.github.io/valheim-deploy/
DefaultDirName={localappdata}\Programs\Valheim Deploy
DefaultGroupName=Valheim Deploy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ValheimDeploy-Setup
SetupIconFile=..\build\ValheimDeploy.ico
UninstallDisplayIcon={app}\ValheimDeploy.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; An upgrade must not swap files under a running app.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ValheimDeploy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Valheim Deploy"; Filename: "{app}\ValheimDeploy.exe"
Name: "{userdesktop}\Valheim Deploy"; Filename: "{app}\ValheimDeploy.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ValheimDeploy.exe"; Description: "Open Valheim Deploy now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop the app first, which stops every server and saves its world.
Filename: "{app}\ValheimDeploy.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitApp"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { Upgrading: quit the running app the safe way, so every world is saved. }
  if FileExists(ExpandConstant('{app}\ValheimDeploy.exe')) then
    Exec(ExpandConstant('{app}\ValheimDeploy.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { No is the default, and what a silent uninstall picks: worlds are kept. }
    if MsgBox('Also delete your servers, worlds and backups?' + #13#10#13#10 +
              'Choose No to keep them, in ' + ExpandConstant('{localappdata}\ValheimDeploy') +
              ', for when you install again.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{localappdata}\ValheimDeploy'), True, True, True);
  end;
end;
