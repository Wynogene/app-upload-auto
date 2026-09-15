# Stop local serve watch process (read-only; does not touch store data).
# Also disable ServeHealth so the 15-min patrol does not start serve again.
$ErrorActionPreference = "Continue"
Disable-ScheduledTask -TaskName "AppUploadAuto-ServeHealth" -ErrorAction SilentlyContinue | Out-Null
Write-Host "Disabled scheduled task AppUploadAuto-ServeHealth (if present)"
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'cli\.py serve') } |
    ForEach-Object {
        Write-Host "Stopping serve PID=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'uvicorn|app\.main:app') -and ($_.CommandLine -match 'app-upload-auto') } |
    ForEach-Object {
        Write-Host "Stopping related PID=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
if ($killed -eq 0) {
    Write-Host "No serve process found"
} else {
    Write-Host "Stopped $killed process(es)"
}
