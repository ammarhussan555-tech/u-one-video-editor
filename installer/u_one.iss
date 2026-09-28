; =====================================================================
; U One - AI Automatic Video Editor - Inno Setup installer script
;
; Build on Windows with Inno Setup 6:
;     iscc installer\u_one.iss
;
; Produces:  Output\U_One_Setup.exe
;
; The installer:
;   - installs U One for the current user (no admin rights required)
;   - creates a Desktop shortcut and a Start Menu shortcut
;   - registers an Uninstaller (Add/Remove Programs)
;   - bundles FFmpeg/FFprobe inside the app folder (no separate install)
; =====================================================================

#define MyAppName "U One"
#define MyAppDisplayName "U One - AI Automatic Video Editor"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "U One"
#define MyAppExeName "U One.exe"

[Setup]
AppId={{8F3B2A41-7C6E-4D9B-9E2F-1A5C8D3B7E01}
AppName={#MyAppName}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=U_One_Setup
SetupIconFile=..\assets\u_one.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDisplayName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller one-folder build output
Source: "..\dist\U One\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; leave user data (projects, cache, logs, keys) in place on uninstall
