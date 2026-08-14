#define MyAppName "AKAISDS"
#define MyAppPublisher "Martin Curkovic"
#define MyAppExeName "main.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputBaseFilename=AKAISDS-Setup
OutputDir=..\..
Compression=lzma
SolidCompression=yes
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes


[Files]
Source: "..\..\src\AKAISDS.dist\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AKAISDS"; Flags: nowait postinstall skipifsilent
