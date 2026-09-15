# App Upload Auto — Windows 常驻盯盘（方式 B：serve + 计划任务）
# 只读轮询商店状态，不写 Google Play / App Store。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    # scripts/windows -> repo root is two levels up; fallback one level
    $Root = Split-Path -Parent $PSScriptRoot
}
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "找不到 venv Python: $Python"
}

# 避免与占用 8088 的旧进程冲突；本机回环，不对外
$env:HOST = "127.0.0.1"
if (-not $env:PORT) { $env:PORT = "18088" }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $Root

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "serve-watch.out.log"
$ErrLog = Join-Path $LogDir "serve-watch.err.log"

# 停进程时会禁用巡检；这里拉起 serve 后重新启用（任务存在才生效）
Enable-ScheduledTask -TaskName "AppUploadAuto-ServeHealth" -ErrorAction SilentlyContinue | Out-Null

# 已在跑则不重复拉起
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'cli\.py serve') }
if ($existing) {
    Write-Host "serve 已在运行 PID=$($existing.ProcessId -join ',')"
    exit 0
}

Write-Host "启动 serve（SCHEDULE 轮询）ROOT=$Root PORT=$env:PORT"
$p = Start-Process -FilePath $Python `
    -ArgumentList @("-u", "cli.py", "serve") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru
Write-Host "started PID=$($p.Id)  out=$OutLog  err=$ErrLog"
