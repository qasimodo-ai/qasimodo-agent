[Setup]
AppName=PyInstaller Test
AppVersion=1.0.0
AppPublisher=Qasimodo AI
DefaultDirName={autopf}\pyinstaller-test
DefaultGroupName=PyInstaller Test
OutputDir=dist
OutputBaseFilename=pyinstaller-test-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\pyinstaller-test.exe

[Files]
Source: "dist\pyinstaller-test\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PyInstaller Test"; Filename: "{app}\pyinstaller-test.exe"
Name: "{autodesktop}\PyInstaller Test"; Filename: "{app}\pyinstaller-test.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\pyinstaller-test.exe"; Description: "Launch PyInstaller Test"; Flags: postinstall nowait skipifsilent
