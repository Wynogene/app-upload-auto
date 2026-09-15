# Install periodic serve healthcheck (current user). Personal-only notify when used with -NotifyIfDown.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$CheckScript = Join-Path $Root "scripts\windows\check-serve-watch.ps1"
if (-not (Test-Path $CheckScript)) {
    throw "Missing $CheckScript"
}

$TaskName = "AppUploadAuto-ServeHealth"
$Arg = "-NoProfile -ExecutionPolicy Bypass -File `"$CheckScript`" -NotifyIfDown"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arg -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "Installed scheduled task: $TaskName (every 15 min, NotifyIfDown)"
Write-Host "Uninstall: powershell -File $Root\scripts\windows\uninstall-serve-healthcheck.ps1"
