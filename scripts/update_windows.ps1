# 메일 비서 업데이트 (git 없이): GitHub에서 최신 ZIP을 받아 프로그램 파일만 바꿉니다.
# .env(키), data\(메일 기록·로그인·설정), outputs\, .venv\ 는 건드리지 않습니다.
param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [string]$ZipUrl = "https://github.com/tabjun/kmu-mail-agent/archive/refs/heads/main.zip",
    [string]$TaskName = "KMU Mail Agent Service",
    [switch]$NoRestart
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Ok($m) { Write-Host "  ✅ $m" -ForegroundColor Green }

Write-Host "[1/4] 최신 버전 내려받기" -ForegroundColor Cyan
$tmp = Join-Path $env:TEMP ("kmu-update-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp "main.zip"
Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$src = Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "mail_agent") } | Select-Object -First 1
if (-not $src) { throw "내려받은 파일에서 프로그램을 찾지 못했습니다." }
Ok "내려받았습니다"

Write-Host "[2/4] 실행 중인 메일 비서 잠시 멈추기" -ForegroundColor Cyan
if (-not $NoRestart) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    Get-Process pythonw, python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path -like "$ProjectDir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
}
Ok "멈췄습니다"

Write-Host "[3/4] 프로그램 파일 바꾸기 (.env · data · .venv 는 그대로)" -ForegroundColor Cyan
robocopy $src.FullName $ProjectDir /E /XD data outputs .venv .claude .git /XF .env .env.txt /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "파일을 복사하지 못했습니다 (robocopy $LASTEXITCODE)" }
$VenvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    & $VenvPy -m pip install -r (Join-Path $ProjectDir "requirements.txt") --quiet --disable-pip-version-check
    Ok "필요한 패키지를 맞췄습니다"
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Ok "프로그램을 최신으로 바꿨습니다"

Write-Host "[4/4] 메일 비서 다시 켜기" -ForegroundColor Cyan
if (-not $NoRestart) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
    } else {
        & (Join-Path $ProjectDir "scripts\start_background.ps1")
    }
    Ok "다시 켰습니다. 잠시 뒤 http://localhost:3000 에서 확인하세요."
}
