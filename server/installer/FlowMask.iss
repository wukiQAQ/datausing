; FlowMask 安装包脚本（Inno Setup 6）
;
; 由 scripts/build_installer.ps1 调用：
;   ISCC.exe installer\FlowMask.iss
; 产物：dist_install\FlowMask-Setup-<version>.exe（当前用户安装，无需管理员权限）
;
; 安装位置：%LOCALAPPDATA%\Programs\FlowMask，桌面/开始菜单各一个快捷方式；
; 数据在 %LOCALAPPDATA%\FlowMask\data（首次启动自动创建），卸载时保留数据目录。

#define MyAppName "FlowMask"
#define MyAppVersion "0.27.2"
#define MyAppPublisher "FlowMask"
#define MyAppExeName "FlowMask.exe"

[Setup]
; AppId 固定不变：升级安装会覆盖旧版，而不是并存
AppId={{6C2F0A54-9D3B-4C3E-8C0B-6B0B7F7A9B41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\FlowMask
DefaultGroupName=FlowMask
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist_install
OutputBaseFilename=FlowMask-Setup-{#MyAppVersion}
SetupIconFile=..\assets\flowmask.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 覆盖安装时若 FlowMask 正在运行，提示关闭（避免 exe 被占用）
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist_exe\FlowMask.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\netmon.example.toml"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\FlowMask"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\FlowMask"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch FlowMask"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; onefile 运行时临时目录（runtime_tmpdir=".flowmask-tmp"），卸载时顺手清掉
Type: filesandordirs; Name: "{app}\.flowmask-tmp"
