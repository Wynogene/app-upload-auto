# 停止本机 serve 盯盘进程（不影响商店线上数据）
$ErrorActionPreference = "Continue"
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'cli\.py serve') } |
    ForEach-Object {
        Write-Host "停止 serve PID=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
# uvicorn 子进程偶发残留
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'uvicorn|app\.main:app') -and ($_.CommandLine -match 'app-upload-auto') } |
    ForEach-Object {
        Write-Host "停止相关 PID=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
if ($killed -eq 0) {
    Write-Host "没有发现 serve 进程"
} else {
    Write-Host "已停止 $killed 个进程"
}
