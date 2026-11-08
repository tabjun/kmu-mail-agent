# 메일 비서 자동 시작을 해제하고 실행 중인 서버를 멈춥니다. (설정·데이터는 지우지 않습니다)
param([string]$TaskName = "KMU Mail Agent Service")

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "자동 시작 작업을 해제했습니다."
} else {
    Write-Host "등록된 작업이 없습니다."
}
$Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "메일 비서.url"
if (Test-Path $Shortcut) { Remove-Item $Shortcut -Confirm:$false }
# 백그라운드로 직접 켠 서버(start_background.ps1)도 멈춤
$ProjectDir = Split-Path -Parent $PSScriptRoot
Get-Process pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path -like "$ProjectDir*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "메일 비서를 멈췄습니다. (설정과 데이터는 data\ 폴더에 그대로 있습니다)"
