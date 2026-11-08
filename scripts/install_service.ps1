# 메일 비서를 "항상 켜져 있는 서비스"로 설치합니다.
#  - Windows에 로그인하면 자동으로 백그라운드에서 시작 (창 없음)
#  - 서버가 죽으면 1분 뒤 자동 재시작
#  - 바탕화면에 '메일 비서' 바로가기 생성
#
# 사용:   powershell -ExecutionPolicy Bypass -File scripts\install_service.ps1
# 해제:   powershell -ExecutionPolicy Bypass -File scripts\uninstall_service.ps1
param(
    [int]$Port = 3000,
    [string]$TaskName = "KMU Mail Agent Service",
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $PythonW)) {
    throw "가상환경이 없습니다: $PythonW`n먼저 'python -m venv .venv' 와 '.venv\Scripts\pip install -r requirements.txt' 를 실행하세요."
}
if (-not (Test-Path (Join-Path $ProjectDir ".env"))) {
    throw ".env 파일이 없습니다. .env.example을 복사해 ROOT_API_KEY 등을 채우세요."
}

$Action = New-ScheduledTaskAction -Execute $PythonW `
    -Argument "-m mail_agent serve --http --port $Port" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Principal $Principal -Description "Outlook 메일을 확인해 정리 제안과 캘린더 할 일 알림을 만드는 메일 비서 서버" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

# 바탕화면 바로가기 (.url)
$Url = "http://localhost:$Port/"
$Desktop = [Environment]::GetFolderPath("Desktop")
Set-Content -Path (Join-Path $Desktop "메일 비서.url") -Encoding ASCII -Value "[InternetShortcut]`r`nURL=$Url`r`n"

# 서버가 뜰 때까지 잠깐 확인
$ok = $false
foreach ($i in 1..45) {
    Start-Sleep -Seconds 2
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $Port); $c.Close(); $ok = $true; break
    } catch { }
}
if ($ok) {
    Write-Host "설치 완료: 로그인할 때마다 자동으로 켜집니다. 바탕화면의 '메일 비서'를 열면 $Url 로 이동합니다."
    if (-not $NoBrowser) { Start-Process $Url }
} else {
    Write-Host "작업은 등록했지만 서버 응답을 아직 확인하지 못했습니다. 로그: $ProjectDir\data\logs\server.log"
}
