# 在桌面创建 FlowMask 快捷方式（启动 / 停止）
#
# 用法（在任意目录）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -Mode stop
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -ExePath D:\tools\FlowMask.exe -Name "FlowMask 演示"
#
# 说明：
# - 启动快捷方式的「起始位置」必须指向 exe 所在目录：exe 是 PyInstaller onefile，
#   解包目录固定在工作目录下的 .flowmask-tmp，指错目录时可能因没有写权限而闪退；
# - 停止快捷方式调用 scripts\stop_flowmask.cmd（内部是 FlowMask.exe --stop），
#   不依赖模型/服务状态，点了就退出后台实例；
# - 桌面路径用系统「桌面」特殊文件夹解析，兼容桌面被重定向到其它盘的情况。

[CmdletBinding()]
param(
    [ValidateSet("start", "stop")]
    [string]$Mode = "start",
    [string]$ExePath = "",
    [string]$Name = "",
    [string]$Description = ""
)

$ErrorActionPreference = "Stop"

$serverDir = Split-Path $PSScriptRoot -Parent
if (-not $ExePath) {
    $ExePath = Join-Path $serverDir "dist_exe\FlowMask.exe"
}
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "找不到 exe：$ExePath（先运行 scripts\build_exe.ps1 打包）"
}
$exeFull = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path $exeFull -Parent

$desktop = (New-Object -ComObject WScript.Shell).SpecialFolders.Item("Desktop")
if (-not (Test-Path -LiteralPath $desktop)) {
    throw "找不到桌面目录：$desktop"
}

$shell = New-Object -ComObject WScript.Shell

if ($Mode -eq "stop") {
    if (-not $Name) { $Name = "FlowMask 停止" }
    if (-not $Description) { $Description = "停止后台运行的 FlowMask 实例" }
    $stopCmd = Join-Path $serverDir "scripts\stop_flowmask.cmd"
    if (-not (Test-Path -LiteralPath $stopCmd)) {
        throw "找不到停止脚本：$stopCmd"
    }
    $lnkPath = Join-Path $desktop "$Name.lnk"
    $shortcut = $shell.CreateShortcut($lnkPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot "System32\cmd.exe"
    $shortcut.Arguments = "/c `"`"$stopCmd`"`""
    $shortcut.WorkingDirectory = $serverDir
    $shortcut.IconLocation = "$exeFull,0"
    $shortcut.Description = $Description
    # 1 = 正常窗口：点完后能看到一句「已停止」确认（3 秒后自动关）
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Output "已创建停止快捷方式：$lnkPath"
    Write-Output "  命令：$stopCmd"
    exit 0
}

if (-not $Name) { $Name = "FlowMask" }
if (-not $Description) { $Description = "FlowMask · 分级脱敏与审计溯源系统（双击启动，自动打开界面）" }
$lnkPath = Join-Path $desktop "$Name.lnk"
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $exeFull
$shortcut.WorkingDirectory = $exeDir
$shortcut.IconLocation = "$exeFull,0"
$shortcut.Description = $Description
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Output "已创建启动快捷方式：$lnkPath"
Write-Output "  目标：$exeFull"
Write-Output "  起始位置：$exeDir"
