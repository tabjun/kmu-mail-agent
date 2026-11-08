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
