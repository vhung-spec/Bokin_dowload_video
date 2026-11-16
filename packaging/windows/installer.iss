; Bộ cài Bokin Clip cho Windows (Inno Setup 6).
; Đóng gói thư mục build\app do build.ps1 tạo ra; xem .github/workflows/windows.yml.
;   ISCC.exe /DAppVersion=1.0.0 packaging\windows\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Bokin Clip"
#define Root "..\.."
#define Launcher "{app}\python\BokinClip.exe"

[Setup]
AppId={{6F4C2B8E-3D1A-4E7B-9C5F-2A8D7E1B4C93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Bokin
; Cài cho riêng người dùng hiện tại: không cần quyền admin, và app tự cập nhật yt-dlp được.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Bokin Clip
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir={#Root}\dist
OutputBaseFilename=BokinClip-Setup-{#AppVersion}
SetupIconFile={#Root}\assets\bokin.ico
UninstallDisplayIcon={app}\bokin.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
ShowLanguageDialog=no

[Languages]
#if FileExists(AddBackslash(SourcePath) + "Vietnamese.isl")
Name: "vi"; MessagesFile: "Vietnamese.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Cài đè bản cũ: xoá Python và thư viện cũ (kể cả bản yt-dlp app đã tự cập nhật) để không lẫn phiên bản.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\templates"
Type: filesandordirs; Name: "{app}\static"

[Files]
Source: "{#Root}\build\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{#Launcher}"; Parameters: """{app}\desktop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\bokin.ico"; AppUserModelID: "Bokin.Clip"
Name: "{autodesktop}\{#AppName}"; Filename: "{#Launcher}"; Parameters: """{app}\desktop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\bokin.ico"; AppUserModelID: "Bokin.Clip"; Tasks: desktopicon

[Run]
Filename: "{#Launcher}"; Parameters: """{app}\desktop.py"""; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Gồm cả file app tạo ra sau khi cài (thư viện cập nhật, settings.json, log). Video đã tải nằm ở
; thư mục lưu riêng nên không bị xoá.
Type: filesandordirs; Name: "{app}"
