# 在桌面创建 FlowMask 快捷方式（双击启动 exe 并自动打开界面）
#
# 用法（在任意目录）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -ExePath D:\tools\FlowMask.exe -Name "FlowMask 演示"
#
# 说明：
# - 快捷方式的「起始位置」必须指向 exe 所在目录：exe 是 PyInstaller onefile，
#   解包目录固定在工作目录下的 .flowmask-tmp，指错目录时可能因没有写权限而闪退；
# - 桌面路径用系统「桌面」特殊文件夹解析，兼容桌面被重定向到其它盘的情况。

[CmdletBinding()]
param(
    [string]$ExePath = "",
    [string]$Name = "FlowMask",
    [string]$Description = "FlowMask · 分级脱敏与审计溯源系统（双击启动，自动打开界面）"
)

$ErrorActionPreference = "Stop"

if (-not $ExePath) {
    $ExePath = Join-Path (Split-Path $PSScriptRoot -Parent) "dist_exe\FlowMask.exe"
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
$lnkPath = Join-Path $desktop "$Name.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $exeFull
$shortcut.WorkingDirectory = $exeDir
$shortcut.IconLocation = "$exeFull,0"
$shortcut.Description = $Description
$shortcut.WindowStyle = 1
$shortcut.Save()

Write-Output "已创建快捷方式：$lnkPath"
Write-Output "  目标：$exeFull"
Write-Output "  起始位置：$exeDir"
