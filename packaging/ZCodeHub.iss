; ZCode Hub —— Inno Setup 安装器脚本
; 不要手工编译：版本号、stage 目录、BOM 处理由 packaging/build.ps1 统一注入。
; 本文件必须存成 UTF-8 with BOM（build.ps1 会代劳），否则中文向导文字会变乱码。

#define MyAppName    "ZCode Hub"
#define MyAppExeName "ZCodeHub.exe"
; 默认入口：无控制台的托盘程序，后台拉起并看护 ZCodeHub.exe serve
#define MyAppTrayExe "ZCodeHubTray.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppVersionNum
  #define MyAppVersionNum "0.0.0.0"
#endif

[Setup]
AppId={{8F1B3E2C-7A4D-4F0B-9C6E-2D5A8B3E1F77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersionNum}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={autopf}\ZCodeHub
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppTrayExe}
OutputDir=output
OutputBaseFilename=ZCodeHub-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 允许 /CURRENTUSER 降到用户目录，方便本机免 UAC 试装
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
; 「开机自动启动」写 HKCU Run 键：单机桌面场景下 UAC 提级后仍是同一个交互用户，
; 符合预期；若用别的管理员账户提级，该项会对不上号，属可接受的边角（任务默认不勾选）。
UsedUserAreasWarning=no
SetupLogging=yes
; 托盘/控制台在跑时会占用 exe 与 _internal：让安装器提示关闭（不自动重启应用）
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："
Name: "autostart"; Description: "登录后自动启动网关（可选）"; GroupDescription: "开机选项：" ; Flags: unchecked

[Files]
; stage 目录已由 build.ps1 组装并本地验证过，安装的就是它
Source: "stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
; 空的 data/ 由 [Files] 带不进来，显式声明；运行期写脏后卸载器不会删。
; Program Files 默认拒绝普通用户写入，而账号库/日志/device_mid 全写在这里，
; 托盘又以 asInvoker 运行（无虚拟化兼容），所以必须授 Users 修改权限。
Name: "{app}\data"; Permissions: users-modify

[Icons]
; 托盘是默认入口：没有黑色窗口，右键菜单里控制服务，误点也不会把服务带走
Name: "{group}\ZCode Hub（托盘）"; Filename: "{app}\{#MyAppTrayExe}"; WorkingDir: "{app}"
Name: "{group}\ZCode Hub 控制台（调试用，关窗即停服）"; Filename: "{app}\{#MyAppExeName}"; Parameters: "serve --open-browser"; WorkingDir: "{app}"
Name: "{group}\ZCode Hub 状态"; Filename: "{app}\{#MyAppExeName}"; Parameters: "status"; WorkingDir: "{app}"
Name: "{group}\ZCode Hub 账号列表"; Filename: "{app}\{#MyAppExeName}"; Parameters: "accounts"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{autodesktop}\ZCode Hub"; Filename: "{app}\{#MyAppTrayExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; 路径全部锚在 exe 同级，Run 键不支持工作目录也不是问题
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ZCodeHub"; ValueData: """{app}\{#MyAppTrayExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppTrayExe}"; WorkingDir: "{app}"; Description: "立即启动 ZCode Hub（托盘常驻，右键菜单控制服务）"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if FileExists(ExpandConstant('{app}\.env')) then
      Log('.env 已存在，保留用户配置')
    else
      Log('警告：.env 缺失，请从 .env.example 复制一份到安装目录');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    Log('data\ 目录（含账号库与凭据）不会被自动删除，需手工清理则删除 {app}\data');
end;
