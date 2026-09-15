# Android 发版包装：upload-submit + 盯盘提示（默认可 WhatIf，不写商店）
# 飞书：依赖 SAFETY_PERSONAL_ONLY=true 时仅私聊本人。

param(
    [Parameter(Mandatory = $true)][string]$AppId,
    [Parameter(Mandatory = $true)][string]$Artifact,
    [ValidateSet("internal", "alpha", "beta", "production")]
    [string]$Track = "internal",
    [switch]$AllowProduction,
    [string]$Rollout = "",
    [switch]$Notify,
    [switch]$WhatIf,
    [switch]$NoActivateHint
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    $Root = Split-Path -Parent $PSScriptRoot
}
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing venv python: $Python"
}
if (-not (Test-Path $Artifact)) {
    if ($WhatIf) {
        Write-Host "WARN: artifact not found (WhatIf ok): $Artifact"
    } else {
        throw "Artifact not found: $Artifact"
    }
}

if ($Track -eq "production" -and -not $AllowProduction) {
    throw "production requires -AllowProduction (explicit). Or use -Track internal first."
}

$argsList = @(
    "-u", "cli.py", "upload-submit",
    "--app-id", $AppId,
    "--platform", "android",
    "--artifact", $Artifact,
    "--track", $Track
)
if ($AllowProduction) { $argsList += "--allow-production" }
if ($Rollout -ne "") {
    $argsList += @("--rollout", $Rollout)
}
if ($Notify) { $argsList += "--notify" } else { $argsList += "--no-notify" }

Write-Host "ROOT=$Root"
Write-Host "CMD= $Python $($argsList -join ' ')"

if ($WhatIf) {
    Write-Host ""
    Write-Host "WhatIf only — no store write. Remove -WhatIf to run."
    Write-Host "After success: production submit auto-registers watch_targets; ensure serve is running."
    Write-Host "  powershell -File .\scripts\windows\start-serve-watch.ps1"
    Write-Host "  python cli.py watch --app-id $AppId --platform android --version-code <NEW> --once --heartbeat-hours 0 --no-notify"
    exit 0
}

$env:PYTHONPATH = $Root
$env:PYTHONIOENCODING = "utf-8"
& $Python @argsList
$code = $LASTEXITCODE
Write-Host ""
Write-Host "ExitCode=$code"
if ($code -eq 0) {
    Write-Host "If production submit succeeded, watch target was auto-registered."
    Write-Host "Keep serve up (read-only poll). Health check:"
    Write-Host "  powershell -File .\scripts\windows\check-serve-watch.ps1 -NotifyIfDown"
}
exit $code
