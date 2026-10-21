<#
.SYNOPSIS
    构建 FlowMask Windows 单文件 exe：前端构建 → PyInstaller 打包 → 产物自检。

.DESCRIPTION
    产物为 server/dist_exe/FlowMask.exe，双击启动后自动开一个应用窗口（Chrome/Edge app 模式）。
    模型权重不打进 exe：把 l2_models/ 与 models/ 放到 exe 同级目录即可被自动识别。

.PARAMETER SkipFrontend
    跳过 npm 构建（web/dist 已存在且是最新时用）。

.PARAMETER OutDir
    输出目录，默认 dist_exe。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [string]$OutDir = "dist_exe"
)

$ErrorActionPreference = "Stop"
$serverDir = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $serverDir
$webDir = Join-Path $repoRoot "web"

if (-not $SkipFrontend) {
    Write-Host "[FlowMask] 1/3 构建前端（web/）..."
    # esbuild 会删临时目录；把 TEMP 指到仓库内，规避 Windows 上的 EPERM
    $env:TEMP = Join-Path $repoRoot "_build_tmp"
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    Push-Location $webDir
    try {
        npm run build
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "[FlowMask] 1/3 跳过前端构建（-SkipFrontend）"
}

$indexHtml = Join-Path $webDir "dist\index.html"
if (-not (Test-Path $indexHtml)) {
    throw "缺少 $indexHtml，请先在 web/ 下执行 npm install 与 npm run build"
}

Write-Host "[FlowMask] 2/3 PyInstaller 打包..."
# 正在运行的 FlowMask 会锁住 exe，导致 PyInstaller 覆盖失败（但脚本仍报成功）；先停掉再打包
$running = Get-Process -Name FlowMask -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "[FlowMask] 检测到正在运行的 FlowMask（pid: $($running.Id -join ', ')），先停止再打包..."
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
Push-Location $serverDir
try {
    python -m PyInstaller flowmask.spec --noconfirm --distpath $OutDir --workpath build_exe
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败（exit=$LASTEXITCODE），请查看上面的日志（常见原因：exe 正被占用、磁盘空间不足）"
    }
}
finally {
    Pop-Location
}

Write-Host "[FlowMask] 3/3 校验产物..."
$exe = Join-Path $serverDir (Join-Path $OutDir "FlowMask.exe")
if (-not (Test-Path $exe)) {
    throw "打包失败：没有找到 $exe"
}
# 校验产物确实是"这次"打出来的：exe 必须不早于构建脚本
if ((Get-Item $exe).LastWriteTime -lt (Get-Item $PSCommandPath).LastWriteTime.AddMinutes(-1)) {
    throw "打包产物未更新（$exe 的修改时间早于构建脚本），可能有进程占用或打包被跳过"
}
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "[FlowMask] 构建完成：$exe （$sizeMb MB）"
Write-Host "[FlowMask] 双击即可启动；L2/L3/NER 权重放 exe 同级 l2_models/ 与 models/"
Write-Host "[FlowMask] 用户数据：%LOCALAPPDATA%\FlowMask\data    日志：%LOCALAPPDATA%\FlowMask\logs\desktop.log"
