<#
.SYNOPSIS
    FlowMask 一键演示：跑完「检测 → 任务 → 流量 → 批量 → 动态脱敏 → 隐私导出 → 审计」。

.DESCRIPTION
    假设服务已经跑起来（scripts/start_local.ps1 或 FlowMask.exe）。
    脚本只调用 HTTP 接口，不依赖 Python；跑完打印一份可对照 PPT 的清单。

.PARAMETER BaseUrl
    服务地址，默认 http://127.0.0.1:8090。

.PARAMETER NoBrowser
    跑完不自动打开浏览器。

.PARAMETER SkipTraffic
    跳过 pcap 导入（例如没有 samples/ 目录时）。

.EXAMPLE
    .\scripts\demo.ps1
    .\scripts\demo.ps1 -BaseUrl http://127.0.0.1:18099 -NoBrowser
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8090",
    [switch]$NoBrowser,
    [switch]$SkipTraffic
)

$ErrorActionPreference = "Stop"
$script:Failures = 0
$Api = "$($BaseUrl.TrimEnd('/'))/api/v1"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

function Write-Step([string]$Text) { Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK]   $Text" -ForegroundColor Green }
function Write-Bad([string]$Text) {
    $script:Failures++
    Write-Host "  [FAIL] $Text" -ForegroundColor Red
}

function Invoke-Api {
    param([string]$Method = "GET", [string]$Path, [object]$Body, [int]$TimeoutSec = 30)
    $uri = "$Api$Path"
    try {
        if ($null -eq $Body) {
            return Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec $TimeoutSec
        }
        $json = $Body | ConvertTo-Json -Depth 8 -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        return Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec $TimeoutSec `
            -ContentType "application/json; charset=utf-8" -Body $bytes
    } catch {
        Write-Bad "$Method $Path → $($_.Exception.Message)"
        return $null
    }
}

function Wait-Task([string]$TaskId, [double]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $task = Invoke-Api -Path "/tasks/$TaskId"
        if ($null -eq $task) { return $null }
        if ($task.status -eq "done" -or $task.status -eq "failed") { return $task }
        Start-Sleep -Milliseconds 250
    }
    Write-Bad "任务 $TaskId 超时"
    return $null
}

function New-DemoFile([string]$Name, [string]$Content) {
    $dir = Join-Path $env:TEMP ("flowmask-demo-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $path = Join-Path $dir $Name
    [System.IO.File]::WriteAllText($path, $Content, [System.Text.UTF8Encoding]::new($false))
    return $path
}

function Send-Multipart([string]$Path, [string]$FileField, [string[]]$Files, [string]$ExtraQuery = "") {
    $curlArgs = @("-s", "-S", "$Api$Path$ExtraQuery")
    foreach ($file in $Files) { $curlArgs += @("-F", "$FileField=@$file") }
    $raw = & curl.exe @curlArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "curl $Path 失败（exit $LASTEXITCODE）"
        return $null
    }
    return $raw | ConvertFrom-Json
}

# ---------------------------------------------------------------- 0. 健康检查
Write-Step "0/8 健康检查"
$health = Invoke-Api -Path "/health"
if ($null -eq $health) {
    Write-Host "服务没起来：先跑 scripts\start_local.cmd（或双击 FlowMask.exe）再重试。" -ForegroundColor Yellow
    exit 2
}
Write-Ok "服务在 $BaseUrl（版本 $($health.version)，运行 $($health.uptime_s)s；L2=$($health.enable_l2) / L3=$($health.enable_l3) / NER=$($health.ner) / 敏感词 $($health.keywords) 条）"

# ---------------------------------------------------------------- 1. 即时检测
Write-Step "1/8 即时检测（文本里带身份证 / 手机号 / 地址 / 密钥）"
$sampleText = "客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号；接口凭据 api_key=sk-demo-1234567890abcdef。"

$detect = Invoke-Api -Method POST -Path "/detect" -Body @{ text = $sampleText }
if ($detect -and $detect.findings.Count -gt 0) {
    $finding = $detect.findings[0]
    $types = ($finding.types -join ",")
    Write-Ok "分级 $($finding.level)，命中 $($finding.hits) 处，类别 $types，耗时 $($detect.duration_ms) ms"
    if ($finding.masked -match "11010519491231002X|13800138000") {
        Write-Bad "脱敏副本里仍出现原始敏感值！"
    } else {
        Write-Ok "脱敏副本里没有原始身份证/手机号"
    }
    $maskedLen = if ($finding.masked) { $finding.masked.Length } else { 0 }
    Write-Ok "脱敏副本长度 $maskedLen（原文 $($sampleText.Length) 字符）"
}

# ---------------------------------------------------------------- 2. 任务 + 审计
Write-Step "2/8 建任务（落库 + 审计链）"
$created = Invoke-Api -Method POST -Path "/tasks/text" -Body @{ text = $sampleText }
if ($created) {
    $task = Wait-Task $created.task_id
    if ($task) {
        Write-Ok "任务 $($task.id.Substring(0,10))… 状态 $($task.status)，命中 $($task.total_findings) 条"
        $findings = Invoke-Api -Path "/tasks/$($task.id)/findings"
        if ($findings -and $findings.total -gt 0) {
            $f = $findings.items[0]
            Write-Ok ("首条结果：$($f.path) / $($f.level) / 命中 $($f.hits) 处 / 类别 " + ($f.types -join ","))
            Write-Ok ("脱敏副本长度 " + $f.masked.Length + "（原文 " + $sampleText.Length + " 字符），原文不入库")
        }
    }
}

# ---------------------------------------------------------------- 3. pcap 流量
Write-Step "3/8 pcap 流量导入（同一套识别/分级/脱敏/审计）"
if ($SkipTraffic) {
    Write-Host "  已按 -SkipTraffic 跳过" -ForegroundColor DarkGray
} else {
    $pcap = Join-Path (Join-Path $RepoRoot "samples") "demo-traffic.pcap"
    if (-not (Test-Path $pcap)) {
        Write-Host "  找不到 $pcap，跳过（可用 -SkipTraffic 显式跳过）" -ForegroundColor Yellow
    } else {
        $traffic = Send-Multipart -Path "/traffic/import" -FileField "pcap" -Files @($pcap)
        if ($traffic) {
            $trafficTask = Wait-Task $traffic.task_id
            if ($trafficTask) {
                Write-Ok "pcap → $($traffic.sessions) 个会话 / $($traffic.segments) 个待检片段；任务命中 $($trafficTask.total_findings) 条"
            }
        }
    }
}

# ---------------------------------------------------------------- 4. 批量检测
Write-Step "4/8 批量检测（多文件一次提交）"
$files = @(
    (New-DemoFile "note.txt" "客户李四，手机 13900139000，收货地址：上海市浦东新区世纪大道 100 号。"),
    (New-DemoFile "order.json" '{"name":"王五","id_card":"11010519491231002X","amount":88}'),
    (New-DemoFile "users.csv" "email,phone`nzhaoliu@example.com,13700137000")
)
$batch = Send-Multipart -Path "/tasks/batch" -FileField "files" -Files $files
if ($batch) {
    Write-Ok "批量提交 $($batch.total) 个文件，受理 $($batch.accepted) 个（坏文件只在结果里标错）"
    foreach ($item in $batch.items) {
        if ($item.error) {
            Write-Bad "$($item.filename)：$($item.error)"
            continue
        }
        $detail = Wait-Task $item.task_id 15
        $hits = if ($detail) { $detail.total_findings } else { "?" }
        $note = if ($detail) { $detail.status } else { "unknown" }
        Write-Host ("  - {0} → {1}（命中 {2}）" -f $item.filename, $note, $hits)
    }
}

# ---------------------------------------------------------------- 5. 动态脱敏
Write-Step "5/8 动态脱敏（未授权=脱敏副本；授权=原文并留审计）"
$maskedView = Invoke-Api -Method POST -Path "/dynamic/render" -Body @{ text = $sampleText; authorized = $false }
$rawView = Invoke-Api -Method POST -Path "/dynamic/render" -Body @{ text = $sampleText; authorized = $true; mask_level = "L3" }
if ($maskedView) { Write-Ok "未授权视图：policy=$($maskedView.policy)，分级 $($maskedView.level)，脱敏副本长度 $($maskedView.text.Length)" }
if ($rawView) { Write-Ok "授权视图：policy=$($rawView.policy)，操作人 $($rawView.operator_id)，返回原文长度 $($rawView.text.Length)（已写审计链）" }

# ---------------------------------------------------------------- 6. 隐私导出
Write-Step "6/8 隐私增强导出（Laplace + l-多样性）"
if ($task) {
    $privacy = Invoke-Api -Path "/tasks/$($task.id)/export/privacy?format=json&epsilon=1.0&l=3"
    if ($privacy) {
        $p = $privacy.meta.privacy
        Write-Ok ("total=$($p.total)；by_level=" + ($p.by_level | ConvertTo-Json -Compress) + "；dp_by_level=" + ($p.dp_by_level | ConvertTo-Json -Compress))
        Write-Ok "l-多样性：ok=$($p.l_diversity.ok)，分组 $($p.l_diversity.groups)" 
    }
}

# ---------------------------------------------------------------- 7. 审计链
Write-Step "7/8 审计链校验"
$verify = Invoke-Api -Path "/audit/verify"
if ($verify) {
    if ($verify.ok) { Write-Ok "审计链完整：$($verify.total) 条，ok=$($verify.ok)" }
    else { Write-Bad "审计链断在 #$($verify.broken_at)" }
}

# ---------------------------------------------------------------- 8. 统计看板
Write-Step "8/8 统计看板数据"
$stats = Invoke-Api -Path "/stats"
if ($stats) {
    Write-Ok ("任务 $($stats.tasks) 个 / 命中 $($stats.total_findings) 条 / 分级 " + ($stats.by_level | ConvertTo-Json -Compress))
}

# ---------------------------------------------------------------- 结果
Write-Host ""
if ($script:Failures -eq 0) {
    Write-Host "演示全部通过。打开 $BaseUrl/app/ 看正式前端，或 /ui/ 看零依赖单页。" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process "$BaseUrl/app/" | Out-Null }
    exit 0
}
Write-Host "演示有 $($script:Failures) 处失败，按上面的 [FAIL] 排查。" -ForegroundColor Red
exit 1
