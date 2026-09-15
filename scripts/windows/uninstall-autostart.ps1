# 卸载开机自启计划任务（并可选停止当前 serve）
$ErrorActionPreference = "Continue"
$TaskName = "AppUploadAuto-ServeWatch"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    $Root = Split-Path -Parent $PSScriptRoot
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "已卸载计划任务: $TaskName"

& (Join-Path $Root "scripts\windows\stop-serve-watch.ps1")
