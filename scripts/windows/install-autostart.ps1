# 安装「登录后自动启动 serve 盯盘」计划任务（只读，不写商店）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$StartScript = Join-Path $Root "scripts\windows\start-serve-watch.ps1"
if (-not (Test-Path $StartScript)) {
    throw "找不到 $StartScript"
}

$TaskName = "AppUploadAuto-ServeWatch"
$Arg = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arg -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "已安装计划任务: $TaskName"
Write-Host "触发: 当前用户登录后自动执行 $StartScript"
Write-Host ""
Write-Host "人工中断:"
Write-Host "  临时停止进程:  powershell -File $Root\scripts\windows\stop-serve-watch.ps1"
Write-Host "  禁用开机自启:  Disable-ScheduledTask -TaskName $TaskName"
Write-Host "  卸载任务:      powershell -File $Root\scripts\windows\uninstall-autostart.ps1"
