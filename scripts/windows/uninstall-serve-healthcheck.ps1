# Uninstall serve healthcheck scheduled task
$ErrorActionPreference = "Continue"
$TaskName = "AppUploadAuto-ServeHealth"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Uninstalled scheduled task: $TaskName"
