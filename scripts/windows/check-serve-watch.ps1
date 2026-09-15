# Check serve watch health; optionally restart and notify owner only.
# Does not write Google Play / App Store. Feishu stays personal-only via SAFETY_PERSONAL_ONLY.

param(
    [string]$HealthUrl = "http://127.0.0.1:18088/health",
    [switch]$NotifyIfDown,
    [switch]$NoRestart
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    $Root = Split-Path -Parent $PSScriptRoot
}
Set-Location $Root

function Test-ServeHealth {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        return ($resp.StatusCode -eq 200 -and $resp.Content -match '"ok"\s*:\s*true')
    } catch {
        return $false
    }
}

$ok = Test-ServeHealth
if ($ok) {
    Write-Host "serve OK  $HealthUrl"
    exit 0
}

Write-Host "serve DOWN  $HealthUrl"

if (-not $NoRestart) {
    $start = Join-Path $Root "scripts\windows\start-serve-watch.ps1"
    Write-Host "Restarting via $start"
    powershell -NoProfile -ExecutionPolicy Bypass -File $start
    Start-Sleep -Seconds 6
    $ok = Test-ServeHealth
    if ($ok) {
        Write-Host "serve recovered"
    } else {
        Write-Host "serve still down after restart"
    }
}

if ($NotifyIfDown) {
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    $env:PYTHONPATH = $Root
    $env:PYTHONIOENCODING = "utf-8"
    $status = if ($ok) { "recovered after restart" } else { "STILL DOWN" }
    & $Python -c @"
from app.notify.feishu_notify import Notifier
Notifier().notify_owner(
    title='serve 盯盘健康检查',
    markdown=f'本机 serve 曾不可用（$HealthUrl）。当前状态：**$status**。\n仅私聊你；未写商店。',
)
print('notified owner')
"@
}

if (-not $ok) { exit 1 }
exit 0
