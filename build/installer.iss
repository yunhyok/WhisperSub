; Inno Setup 6 script — per-user install (no admin), builds dist\WhisperSub-Setup-<version>.exe
; Compile from repo root:  ISCC build\installer.iss
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "WhisperSub"
#define AppPublisher "yunhyok"
#define AppURL "https://github.com/yunhyok/WhisperSub"
#define SrcDir "..\dist\WhisperSub"

[Setup]
AppId={{C5F3CD79-E49D-4017-9111-4ED8BFD10BB9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\WhisperSub.exe
Compression=lzma2/max
SolidCompression=no
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
ShowLanguageDialog=auto
LicenseFile=..\LICENSE

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\WhisperSub.exe"
Name: "{group}\{#AppName} (CLI)"; Filename: "{app}\whispersub-cli.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\WhisperSub.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WhisperSub.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
