<#
.SYNOPSIS
    停止由 scripts/start_local.ps1 启动的所有 FlowMask 进程。

.DESCRIPTION
    读取 data/run/*.pid，按 pid 停止进程并删除 pid 文件；进程已退出时只清理文件。

.EXAMPLE
    .\scripts\stop_local.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ServerDir = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $ServerDir 'data\run'

if (-not (Test-Path $RunDir)) {
    Write-Host '[flowmask] 没有运行记录（data/run 不存在），无需停止'
    return
}

$pidFiles = @(Get-ChildItem -Path $RunDir -Filter '*.pid' -ErrorAction SilentlyContinue)
if ($pidFiles.Count -eq 0) {
    Write-Host '[flowmask] 没有运行记录（无 *.pid），无需停止'
    return
}

$stopped = 0
foreach ($pidFile in $pidFiles) {
    $raw = @(Get-Content -Path $pidFile.FullName -ErrorAction SilentlyContinue)
    $processId = 0
    $parsed = $false
    if ($raw.Count -gt 0) {
        $parsed = [int]::TryParse([string]$raw[0], [ref]$processId)
    }
    if (-not $parsed -or $processId -le 0) {
        Write-Warning ("{0} 内容无效，仅删除该文件" -f $pidFile.Name)
        Remove-Item -Path $pidFile.FullName -Force -ErrorAction SilentlyContinue
        continue
    }

    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $processId -Force
        Write-Host ("[flowmask] 已停止 {0,-4} pid={1}" -f $pidFile.BaseName, $processId)
        $stopped++
    } else {
        Write-Host ("[flowmask] {0,-4} pid={1} 已退出，清理记录" -f $pidFile.BaseName, $processId)
    }
    Remove-Item -Path $pidFile.FullName -Force -ErrorAction SilentlyContinue
}

Write-Host ("[flowmask] 完成：本次停止 {0} 个进程" -f $stopped)
