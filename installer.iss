[Setup]
AppName=Qasimodo Agent
AppVersion=1.0.0
AppPublisher=Qasimodo AI
DefaultDirName={autopf}\qasimodo-agent
DefaultGroupName=Qasimodo Agent
OutputDir=dist
OutputBaseFilename=qasimodo-agent-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\qasimodo-agent.exe

[Files]
Source: "dist\qasimodo-agent\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Qasimodo Agent"; Filename: "{app}\qasimodo-agent.exe"
Name: "{autodesktop}\Qasimodo Agent"; Filename: "{app}\qasimodo-agent.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\qasimodo-agent.exe"; Description: "Launch Qasimodo Agent"; Flags: postinstall nowait skipifsilent
