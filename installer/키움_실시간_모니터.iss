#define AppName "키움 실시간 모니터"
#define AppVersion "2.0.0"
#define AppPublisher "개인용"
#define AppExeName "키움_실시간_모니터.exe"

[Setup]
AppId={{A5C1AE31-8168-4DA5-81D2-3C531D8A3ED1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName=C:\kiwoom-monitor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\dist\설치파일
OutputBaseFilename=키움_실시간_모니터_설치
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면 바로가기 만들기"; GroupDescription: "바로가기:"; Flags: unchecked

[Files]
Source: "..\build\installer_payload\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "삭제 방법.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"
Name: "{app}\data\logs"
Name: "{app}\data\near_high_sounds"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\{#AppExeName}"
Type: files; Name: "{app}\삭제 방법.txt"
