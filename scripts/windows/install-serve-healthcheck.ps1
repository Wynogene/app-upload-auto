# Install periodic serve healthcheck with pythonw (no console flash).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $Root "cli.py"))) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$Pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Checker = Join-Path $Root "scripts\windows\check_serve_watch.py"
if (-not (Test-Path $Pyw)) {
    throw "Missing $Pyw"
}
if (-not (Test-Path $Checker)) {
    throw "Missing $Checker"
}

$TaskName = "AppUploadAuto-ServeHealth"
# pythonw = no console window (avoids PowerShell flash)
$Arg = "`"$Checker`" --notify-if-down"
$Action = New-ScheduledTaskAction -Execute $Pyw -Argument $Arg -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "Installed scheduled task: $TaskName (every 15 min, pythonw, no console)"
Write-Host "Uninstall: powershell -File $Root\scripts\windows\uninstall-serve-healthcheck.ps1"
