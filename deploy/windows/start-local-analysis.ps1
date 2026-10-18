<#
.SYNOPSIS
  在 Windows 上单机启动 netmon 引擎：分析本地 pcap 文件，或直接实时抓本机网卡，并打开浏览器仪表盘。
.DESCRIPTION
  对应《双模式设计文档》的 M3（离线 pcap 分析）与 M2（本机 Windows 实时抓包）。两种模式互斥：

  - **离线 pcap（-Pcap，默认用法）**：零权限——不需要管理员、不需要装驱动，只读指定文件；
    采集端 0.23.4 起查询窗口跟随 pcap 报文时间（不是墙钟），所以 TOP 榜 / 历史曲线 / 会话明细的数字彼此一致。
  - **实时抓包（-Live）**：原始套接字 + SIO_RCVALL 抓本机网卡，**需要管理员权限**（脚本会自动请求提权）；
    留空 -Iface 时自动选默认路由出口网卡。
    注意：只看得见本机网卡上的 IP 流量——抓不到 127.0.0.1 回环（走本地代理的流量看不见）、抓不到 ARP。

  两种模式都使用独立数据目录（默认 %LOCALAPPDATA%\MeTD\local-data），与远程 Linux 采集端的数据、
  与其它本机会话互不混存；启动参数与客户端 sidecar 完全一致（见 client/src/localsource.js）。
.EXAMPLE
  .\start-local-analysis.ps1 -Pcap D:\samples\demo.pcap
  .\start-local-analysis.ps1 -Live
  .\start-local-analysis.ps1 -Live -Iface 10.24.68.159 -Port 18090 -NoBrowser
  .\start-local-analysis.ps1 -Stop
#>
param(
  [string]$Pcap = "",
  [switch]$Live,
  [string]$Iface = "",
  [int]$Port = 18080,
  [string]$DataDir = "",
  [string]$Netmon = "",
  [int]$TimeoutSec = 20,
  [switch]$NoBrowser,
  [switch]$Stop
)

$ErrorActionPreference = "Stop"
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$scriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }

function Test-IsAdmin {
  return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 以管理员身份重启本脚本（弹一次 UAC），由新实例带着同样的参数继续执行。
# 当前实例只负责请求提权，避免"先启动、再失败"这种假成功。
function Restart-Elevated {
  param([string[]]$ExtraArgs = @())
  $hostExe = (Get-Process -Id $PID).Path
  if ([string]::IsNullOrWhiteSpace($hostExe)) {
    $hostExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
  }
  $elevArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $scriptPath, '-Port', $Port, '-TimeoutSec', $TimeoutSec)
  if ($ExtraArgs.Count -gt 0) { $elevArgs += $ExtraArgs }
  if (-not [string]::IsNullOrWhiteSpace($Iface)) { $elevArgs += @('-Iface', $Iface) }
  if (-not [string]::IsNullOrWhiteSpace($DataDir)) { $elevArgs += @('-DataDir', $DataDir) }
  if (-not [string]::IsNullOrWhiteSpace($Netmon)) { $elevArgs += @('-Netmon', $Netmon) }
  if ($NoBrowser) { $elevArgs += '-NoBrowser' }
  try {
    Start-Process -FilePath $hostExe -ArgumentList $elevArgs -Verb RunAs -ErrorAction Stop
  } catch {
    throw ("提权被取消或失败：$($_.Exception.Message)`n" +
      "请改用「以管理员身份运行」的 PowerShell 再执行本脚本。")
  }
}

function Get-DefaultDataDir {
  if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    return (Join-Path $env:LOCALAPPDATA "MeTD\local-data")
  }
  if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    return (Join-Path $env:USERPROFILE "AppData\Local\MeTD\local-data")
  }
  throw "无法确定本机数据目录：环境变量 LOCALAPPDATA / USERPROFILE 都为空，请显式指定 -DataDir。"
}

if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = Get-DefaultDataDir }
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
$pidFile = Join-Path $DataDir "local-engine.pid"
$outLog = Join-Path $DataDir "local-engine.log"
$errLog = Join-Path $DataDir "local-engine.err.log"
$healthUrl = "http://127.0.0.1:$Port/api/v1/health"
$baseUrl = "http://127.0.0.1:$Port"

# -Stop：停掉上一次用本脚本启动的引擎（按 pid 文件，不动别的 netmon 进程）
if ($Stop) {
  if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "没有找到运行记录（$pidFile），本机引擎可能未启动。" -ForegroundColor Yellow
    exit 0
  }
  $oldPid = 0
  [void][int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$oldPid)
  if ($oldPid -gt 0 -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
    try {
      Stop-Process -Id $oldPid -Force -ErrorAction Stop
      Write-Host "已停止本机引擎（PID $oldPid）。" -ForegroundColor Green
    } catch {
      # 实时抓包的引擎是提权启动的，普通权限停不掉：自动提权重试一次（会再弹一次 UAC）
      Write-Host "停止失败（该引擎是管理员身份启动的），正在请求提权重试……" -ForegroundColor Yellow
      Restart-Elevated -ExtraArgs @('-Stop')
      exit 0
    }
  } else {
    Write-Host "PID $oldPid 已不在运行。" -ForegroundColor Yellow
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
  exit 0
}

# 数据来源二选一：两种模式互斥，且必须显式选一个（避免"以为在抓包、其实什么都没抓"）
if ($Live -and -not [string]::IsNullOrWhiteSpace($Pcap)) {
  throw "-Pcap 与 -Live 互斥，请二选一：离线分析用 -Pcap，实时抓包用 -Live。"
}
if (-not $Live -and [string]::IsNullOrWhiteSpace($Pcap)) {
  throw ("必须指定一种数据来源：`n" +
    "  离线 pcap 分析：.\start-local-analysis.ps1 -Pcap D:\samples\demo.pcap`n" +
    "  本机实时抓包：.\start-local-analysis.ps1 -Live    （需要管理员权限，脚本会自动请求提权）")
}

# -Iface 必须是本机 IPv4：Windows 按 IP 绑定，写上网卡名会被引擎当成"自动选择"，
# 那属于静默换网卡、最难自查的一类问题，所以在脚本这一层就拦住。
if ($Live -and -not [string]::IsNullOrWhiteSpace($Iface)) {
  $parsedIp = $null
  $isV4 = [System.Net.IPAddress]::TryParse($Iface, [ref]$parsedIp) -and
    $parsedIp.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
  if (-not $isV4) {
    throw ("-Iface 需要是本机 IPv4 地址（例如 10.24.68.159）：Windows 上按 IP 绑定、不接受网卡名；" +
      "留空则自动选默认路由出口。当前值：$Iface")
  }
}

$pcapFull = ""
if (-not $Live) {
  if (-not (Test-Path -LiteralPath $Pcap)) { throw "找不到 pcap 文件：$Pcap" }
  $pcapFull = (Resolve-Path -LiteralPath $Pcap).Path
}

# 实时抓包必须提权：未提权时先自动以管理员身份重启本脚本，由新实例继续跑完整流程。
if ($Live -and -not (Test-IsAdmin)) {
  Write-Host "本机实时抓包需要管理员权限，正在请求提权……请在 UAC 对话框点「是」，随后在新窗口中查看结果。" -ForegroundColor Yellow
  Restart-Elevated -ExtraArgs @('-Live')
  exit 0
}

function Resolve-NetmonExe {
  param([string]$Explicit)
  $candidates = @()
  if (-not [string]::IsNullOrWhiteSpace($Explicit)) { $candidates += $Explicit }
  $candidates += @(
    (Join-Path $scriptDir "netmon.exe"),
    (Join-Path $repoRoot "bin\netmon.exe"),
    (Join-Path $repoRoot "netmon.exe")
  )
  foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { return (Resolve-Path -LiteralPath $c).Path }
  }
  throw ("找不到 netmon.exe。请先构建 Windows 采集端，例如：`n" +
    "  cd `"$repoRoot`"`n" +
    "  `$env:GOOS=`"windows`"; `$env:GOARCH=`"amd64`"; go build -o `"$scriptDir\netmon.exe`" ./cmd/netmon`n" +
    "或用 -Netmon 指定已有可执行文件的路径。")
}

$exe = Resolve-NetmonExe -Explicit $Netmon

# 端口占用检查：先在环回上试绑一次，避免"启动了却连不上"
$listener = $null
try {
  $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
  $listener.Start()
} catch {
  throw "端口 $Port 已被占用（或被拒绝绑定），请用 -Port 换一个端口。原始错误：$($_.Exception.Message)"
} finally {
  if ($listener) { $listener.Stop() }
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
if (Test-Path -LiteralPath $pidFile) {
  $oldPid = 0
  [void][int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$oldPid)
  if ($oldPid -gt 0 -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
    throw "本机引擎已在运行（PID $oldPid）。请先执行：.\start-local-analysis.ps1 -Stop"
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

# 参数契约与客户端 sidecar 完全一致（见 client/src/localsource.js 与《双模式设计文档》第 7 节）：
#   离线：-replay-file <pcap> -offline -data-dir <目录> -listen 127.0.0.1:<端口>
#   实时：-source live [-iface <本机 IPv4>] -data-dir <目录> -listen 127.0.0.1:<端口>
if ($Live) {
  $ifaceArg = ""
  if (-not [string]::IsNullOrWhiteSpace($Iface)) { $ifaceArg = " -iface `"$Iface`"" }
  $argLine = "-source live$ifaceArg -data-dir `"$DataDir`" -listen 127.0.0.1:$Port"
} else {
  $argLine = "-replay-file `"$pcapFull`" -offline -data-dir `"$DataDir`" -listen 127.0.0.1:$Port"
}

Write-Host "本机引擎：$exe" -ForegroundColor Cyan
if ($Live) {
  $ifaceDesc = if ([string]::IsNullOrWhiteSpace($Iface)) { "自动选择默认路由出口网卡" } else { "指定网卡 $Iface" }
  Write-Host "数据来源：本机实时抓包（$ifaceDesc）"
} else {
  Write-Host "分析文件：$pcapFull"
}
Write-Host "数据目录：$DataDir"
Write-Host "监听地址：$baseUrl"

$proc = Start-Process -FilePath $exe -ArgumentList $argLine -PassThru -WindowStyle Hidden `
  -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding Ascii

# 轮询健康检查，等引擎起来（离线回放可能要先读完 pcap 头部）
$deadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSec))
$ready = $false
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 400
  if ($proc.HasExited) {
    Write-Host "引擎启动失败（退出码 $($proc.ExitCode)）。日志尾部：" -ForegroundColor Red
    if (Test-Path -LiteralPath $errLog) { Get-Content -LiteralPath $errLog -Tail 20 }
    if (Test-Path -LiteralPath $outLog) { Get-Content -LiteralPath $outLog -Tail 20 }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    exit 1
  }
  try {
    $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
    if ($resp.StatusCode -eq 200) { $ready = $true; break }
  } catch { }
}
if (-not $ready) {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
  throw "等待引擎就绪超时（$TimeoutSec 秒）：$healthUrl 无响应。引擎已停止，请查看 $errLog"
}

# 打印一段概览，便于命令行直接确认"确实抓/分析到了东西"
try {
  $now = Invoke-WebRequest -Uri "$baseUrl/api/v1/traffic/now" -UseBasicParsing -TimeoutSec 5 | Select-Object -ExpandProperty Content
  $nowObj = $now | ConvertFrom-Json
  Write-Host ("引擎已就绪：采集端 {0}，数据源 {1}，平台 {2}，数据时间 {3}" -f $nowObj.version, $nowObj.source, $nowObj.os, $nowObj.ts) -ForegroundColor Green
  if ($Live) {
    if ($nowObj.features -notcontains "source.local") {
      Write-Host "警告：引擎未声明 source.local —— 说明它没拿到管理员权限，实时抓包很可能收不到数据。请先 -Stop 再用管理员身份重跑。" -ForegroundColor Red
    }
    Write-Host "提示：本机实时抓包只看得见本机网卡上的 IP 流量——抓不到 127.0.0.1 回环（走本地代理的流量看不见）、抓不到 ARP。" -ForegroundColor DarkYellow
  } elseif ($nowObj.features -notcontains "capture.offline") {
    Write-Host "提示：该引擎未声明 capture.offline 能力，离线分析可能不完整（建议使用采集端 0.23.4 及以上的 netmon.exe）。" -ForegroundColor Yellow
  }
  $topn = Invoke-WebRequest -Uri "$baseUrl/api/v1/topn?since=3600&n=3" -UseBasicParsing -TimeoutSec 10 | Select-Object -ExpandProperty Content
  $topnObj = $topn | ConvertFrom-Json
  Write-Host ("近一小时：会话 {0} 条，字节 {1}" -f $topnObj.total_flows, $topnObj.total_bytes)
  if ($topnObj.by_ip) {
    foreach ($e in ($topnObj.by_ip | Select-Object -First 3)) {
      Write-Host ("  TOP IP {0}：{1} 条会话 / {2} 字节" -f $e.key, $e.flows, $e.bytes)
    }
  }
} catch {
  Write-Host "（概览查询失败，不影响使用：$($_.Exception.Message)）" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "浏览器打开：$baseUrl" -ForegroundColor Green
Write-Host "停止本机引擎：.\start-local-analysis.ps1 -Stop   （PID $($proc.Id)）" -ForegroundColor DarkGray
if ($Live) { Write-Host "（实时抓包的引擎以管理员身份运行，-Stop 会自动再请求一次提权）" -ForegroundColor DarkGray }
if (-not $NoBrowser) { Start-Process $baseUrl }