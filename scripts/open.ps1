# 메일 비서 열기: 꺼져 있으면 백그라운드로 켜고, 브라우저로 대시보드를 엽니다.
param([int]$Port = 3000)
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    & (Join-Path $PSScriptRoot "start_background.ps1") -Port $Port
}
Start-Process "http://localhost:$Port/"
