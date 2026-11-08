# 메일 비서 서버를 지금 바로 백그라운드(창 없음)로 실행합니다. (nohup과 같은 역할)
# 재부팅 후에도 자동으로 켜지게 하려면 install_service.ps1을 쓰세요.
# 사용: powershell -ExecutionPolicy Bypass -File scripts\start_background.ps1
param([int]$Port = 3000)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $PythonW)) { throw "가상환경이 없습니다: $PythonW" }

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) { Write-Host "이미 포트 $Port 에서 실행 중입니다. http://localhost:$Port/"; exit 0 }

Start-Process -FilePath $PythonW -ArgumentList "-m", "mail_agent", "serve", "--port", "$Port" `
    -WorkingDirectory $ProjectDir -WindowStyle Hidden
Start-Sleep -Seconds 4
Write-Host "백그라운드로 시작했습니다: http://localhost:$Port/   (로그: data\logs\server.log)"
Write-Host "멈추려면: Get-Process pythonw | Where-Object { `$_.Path -like '$ProjectDir*' } | Stop-Process"
