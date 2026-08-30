$ErrorActionPreference = "Stop"
cd $PSScriptRoot
Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m","streamlit","run","app.py","--server.headless","true" `
    -RedirectStandardOutput "st_out.log" -RedirectStandardError "st_err.log" -NoNewWindow
Start-Sleep 8
if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Running: http://localhost:8501" -ForegroundColor Green
} else {
    Write-Host "Failed to start - check st_err.log" -ForegroundColor Red
    Get-Content st_err.log -Tail 20
}
