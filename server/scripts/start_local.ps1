<#
.SYNOPSIS
    FlowMask 本机一键启动：主服务 + 可选 L2/L3/NER 模型服务。

.DESCRIPTION
    按依赖顺序拉起所需进程，写入 data/run/<name>.pid，最后等待 /api/v1/health 就绪并打印入口。
    默认只起主服务（L1 规则链路，不加载任何模型）；需要真模型时加开关。

.EXAMPLE
    .\scripts\start_local.ps1                     # 只起主服务，http://127.0.0.1:8090/app/
    .\scripts\start_local.ps1 -WithL2 -EnableNer  # 叠加 L2 语义模型与自由文本 NER
    .\scripts\start_local.ps1 -WithL2 -WithL3 -L3Device cuda
    .\scripts\start_local.ps1 -NoBrowser -Port 8095

.NOTES
    停止：.\scripts\stop_local.ps1
    日志：server/data/run/*.log、*.err.log
#>
[CmdletBinding()]
param(
    [int]$Port = 8090,
    [string]$BindHost = '127.0.0.1',
    [switch]$WithL2,
    [switch]$WithL3,
    [switch]$EnableNer,
    [string]$L3Device = 'cpu',
    [switch]$SkipFrontendCheck,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$ServerDir = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $ServerDir 'data\run'
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

# 优先用 FLOWMASK_PYTHON 指定的解释器（例如虚拟环境里的 python）
if ($env:FLOWMASK_PYTHON) {
    $Python = $env:FLOWMASK_PYTHON
} else {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { throw '找不到 python：请安装 Python 3.12+，或设置 FLOWMASK_PYTHON 指向解释器' }
    $Python = $found.Source
}
Write-Host ("[flowmask] python = {0}" -f $Python)
Write-Host ("[flowmask] 工作目录 = {0}" -f $ServerDir)

# 加载 .env（如果存在）：只接受 KEY=VALUE 形式，忽略注释与空行
$envFile = Join-Path $ServerDir '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -le 0) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim()
        if ($key) { [Environment]::SetEnvironmentVariable($key, $value, 'Process') }
    }
    Write-Host '[flowmask] 已加载 .env'
}

function Start-FlowMaskService {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [hashtable]$Environment = @{}
    )

    $outLog = Join-Path $RunDir "$Name.log"
    $errLog = Join-Path $RunDir "$Name.err.log"

    # Start-Process 继承当前进程环境：临时设置，启动后立刻还原，避免互相污染
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], 'Process')
    }
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList $Arguments `
            -WorkingDirectory $ServerDir -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
    } finally {
        foreach ($key in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process')
        }
    }

    Set-Content -Path (Join-Path $RunDir "$Name.pid") -Value $proc.Id -Encoding ASCII
    Write-Host ("[flowmask] 启动 {0,-4} pid={1,-6} 日志={2}" -f $Name, $proc.Id, $outLog)
}

function Wait-FlowMaskHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 180
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-RestMethod -Uri $Url -TimeoutSec 3
            return $true
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    return $false
}

# ---------- L2 语义模型服务 ----------
if ($WithL2) {
    $modelDir = Join-Path $ServerDir 'l2_models\bert-sens-onnx'
    if (-not (Test-Path $modelDir)) {
        Write-Warning "未找到 L2 模型目录 $modelDir，跳过 L2（可用 l2_training + l2_service.export_onnx 生成）"
    } else {
        Start-FlowMaskService -Name 'l2' -Arguments @('-m', 'l2_service') -Environment @{
            FLOWMASK_L2_MODE      = 'onnx'
            FLOWMASK_L2_MODEL_DIR = $modelDir
            FLOWMASK_L2_PORT      = '8091'
        }
    }
}

# ---------- L3 大模型复核服务 ----------
if ($WithL3) {
    $modelDir = Join-Path $ServerDir 'models\qwen2.5-1.5b-instruct'
    if (-not (Test-Path $modelDir)) {
        Write-Warning "未找到 L3 模型目录 $modelDir，跳过 L3（用 modelscope download 下载 Qwen/Qwen2.5-1.5B-Instruct）"
    } else {
        Start-FlowMaskService -Name 'l3' -Arguments @('-m', 'l3_service') -Environment @{
            FLOWMASK_L3_MODE      = 'instruct'
            FLOWMASK_L3_MODEL_DIR = $modelDir
            FLOWMASK_L3_DEVICE    = $L3Device
            FLOWMASK_L3_PORT      = '8092'
        }
    }
}

# ---------- 主服务 ----------
$mainEnv = @{
    FLOWMASK_HOST     = $BindHost
    FLOWMASK_PORT     = "$Port"
    FLOWMASK_DATA_DIR = (Join-Path $ServerDir 'data')
}
if ($WithL2) { $mainEnv['FLOWMASK_ENABLE_L2'] = '1' }
if ($WithL3) { $mainEnv['FLOWMASK_ENABLE_L3'] = '1' }
if ($EnableNer) {
    $nerDir = Join-Path $ServerDir 'models\bert4ner'
    if (-not (Test-Path $nerDir)) {
        Write-Warning "未找到 NER 模型目录 $nerDir，NER 将保持关闭"
        $EnableNer = $false
    } else {
        $mainEnv['FLOWMASK_ENABLE_NER'] = '1'
        $mainEnv['FLOWMASK_NER_BACKEND'] = 'token'
        $mainEnv['FLOWMASK_NER_MODEL_DIR'] = $nerDir
    }
}
Start-FlowMaskService -Name 'main' -Arguments @('-m', 'uvicorn', 'app.main:app', '--host', $BindHost, '--port', "$Port") -Environment $mainEnv

# ---------- 等待就绪 ----------
$base = "http://{0}:{1}" -f $BindHost, $Port
Write-Host ''
Write-Host '[flowmask] 等待主服务就绪……'
if (-not (Wait-FlowMaskHealth -Url "$base/api/v1/health" -TimeoutSeconds 180)) {
    Write-Warning '主服务未在 180s 内就绪，请查看 data/run/main.err.log'
    exit 1
}

Write-Host ''
Write-Host '[flowmask] 就绪：'
Write-Host ("  正式前端（Vue3 + Element Plus）: {0}/app/" -f $base)
Write-Host ("  内置单页（零依赖）            : {0}/ui/" -f $base)
Write-Host ("  OpenAPI 文档                  : {0}/docs" -f $base)
Write-Host ("  健康检查                      : {0}/api/v1/health" -f $base)
Write-Host ''
Write-Host '[flowmask] 停止：.\scripts\stop_local.ps1'

if (-not $NoBrowser) {
    try { Start-Process "$base/app/" | Out-Null } catch { Write-Warning '自动打开浏览器失败，请手动访问上面的地址' }
}
