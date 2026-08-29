#define AppName "키움 실시간 모니터"
#define AppVersion "1.1.19"
#define AppPublisher "크니"
#define AppExeName "KiwoomMonitor.exe"
#ifndef DistDir
  #define DistDir "..\\dist\\KiwoomMonitor"
#endif

[Setup]
AppId={{3B79D124-E394-4F72-AD44-9133D9AEAD3C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/jhimm3
AppSupportURL=https://github.com/jhimm3/kiwoom-realtime-monitor/issues
AppUpdatesURL=https://github.com/jhimm3/kiwoom-realtime-monitor/releases
DefaultDirName={autopf}\KiwoomMonitor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=KiwoomMonitor-Setup-1.1.19
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\resources\app_icon.ico
PrivilegesRequired=admin
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0
VersionInfoCopyright=Copyright 2026 크니. All rights reserved.

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; Flags: unchecked

[Files]
Source: "{#DistDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; IconIndex: 0
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; IconIndex: 0; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} 실행"; Flags: nowait postinstall skipifsilent

[Code]
var
  RemovePersonalData: Boolean;

function InitializeUninstall(): Boolean;
var
  Choice: Integer;
begin
  Choice := MsgBox('프로그램과 개인 데이터까지 모두 삭제할까요?' + #13#10 + #13#10 +
    '예: API 키, 테마 DB, 설정, 로그를 포함해 %LocalAppData%\\KiwoomMonitor 폴더도 제거합니다.' + #13#10 +
    '아니오: 프로그램만 삭제하고 개인 데이터는 보관합니다.' + #13#10 +
    '취소: 프로그램 제거를 취소합니다.',
    mbConfirmation, MB_YESNOCANCEL);
  if Choice = IDCANCEL then
  begin
    Result := False;
    exit;
  end;
  RemovePersonalData := Choice = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and RemovePersonalData then
    DelTree(ExpandConstant('{localappdata}\\KiwoomMonitor'), True, True, True);
end;
