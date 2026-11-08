# 메일 비서 설치 (Windows) — "설치하기.bat"을 더블클릭하면 이 스크립트가 실행됩니다.
# 하는 일: 파이썬 확인(없으면 설치 안내) → 필요한 패키지 설치 → API 키 입력(.env) → 연결 확인
#          → 항상 켜지는 서비스 등록 → 브라우저에서 설정 화면 열기
# 다시 실행해도 안전합니다(이미 된 단계는 건너뜀).
param(
    [switch]$NoService,     # 서비스 등록 없이 지금만 실행 (테스트용)
    [switch]$NoBrowser,     # 브라우저를 열지 않음
    [switch]$NoPrompt,      # 키 입력을 묻지 않음 (.env를 직접 편집할 때)
    [int]$Port = 3000
)
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  ✓ $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  ! $text" -ForegroundColor Yellow }

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  메일 비서 설치를 시작합니다 ($ProjectDir)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. Python
Step 1 "파이썬 확인"
function Find-Python {
    foreach ($cand in @(
        @("py", "-3"),
        @("python"),
        @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"),
        @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"),
        @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"),
        @("$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"))) {
        try {
            $exe = $cand[0]; $args0 = @($cand | Select-Object -Skip 1)
            $out = & $exe @args0 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -match '^3\.(1[0-9])$') { return ,$cand }
        } catch { }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Warn "파이썬 3.10 이상이 없습니다."
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        $ans = Read-Host "  지금 파이썬 3.12를 설치할까요? (Y/N)"
        if ($ans -match '^[Yy]') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $py = Find-Python
        }
    }
    if (-not $py) {
        Write-Host "  https://www.python.org/downloads/ 에서 파이썬을 설치한 뒤(설치 화면의 'Add python.exe to PATH' 체크)" -ForegroundColor Yellow
        Write-Host "  '설치하기.bat'을 다시 실행해 주세요." -ForegroundColor Yellow
        exit 1
    }
}
Ok ("파이썬 " + (& $py[0] @($py | Select-Object -Skip 1) --version))

# ---------------------------------------------------------------- 2. venv + packages
Step 2 "필요한 프로그램 설치 (처음 한 번, 2~5분)"
$VenvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    & $py[0] @($py | Select-Object -Skip 1) -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "가상환경을 만들지 못했습니다." }
}
& $VenvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $VenvPy -m pip install -r requirements.txt --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요." }
Ok "설치 완료"

# ---------------------------------------------------------------- 3. .env (API key)
Step 3 "Claude API 키 설정 (.env 파일)"
$EnvPath = Join-Path $ProjectDir ".env"
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $ProjectDir ".env.example") $EnvPath
    Ok ".env 파일을 만들었습니다: $EnvPath"
}
function Get-EnvValue($name) {
    $line = Get-Content $EnvPath -Encoding UTF8 | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
    if ($line) { return (($line -split "=", 2)[1] -replace '#.*$', '').Trim() } else { return "" }
}
function Set-EnvValue($name, $value) {
    $lines = @(Get-Content $EnvPath -Encoding UTF8)
    $found = $false
    $lines = $lines | ForEach-Object { if ($_ -match "^\s*$name\s*=") { $found = $true; "$name=$value" } else { $_ } }
    if (-not $found) { $lines += "$name=$value" }
    [System.IO.File]::WriteAllLines($EnvPath, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
}
if (-not (Get-EnvValue "ROOT_API_KEY")) {
    if ($NoPrompt) {
        Warn ".env의 ROOT_API_KEY가 비어 있습니다. 메모장으로 .env를 열어 'ROOT_API_KEY=' 뒤에 키를 붙여 넣으세요."
    } else {
        Write-Host "  연구실에서 받은 Claude API 키를 붙여 넣고 Enter를 누르세요 (화면에 표시되지 않습니다)."
        $sec = Read-Host "  ROOT_API_KEY" -AsSecureString
        $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        if ($key) { Set-EnvValue "ROOT_API_KEY" $key.Trim(); Ok "키를 .env에 저장했습니다." }
    }
}
if (-not (Get-EnvValue "ANTHROPIC_WORKSPACE_ID") -and -not $NoPrompt) {
    $ws = Read-Host "  워크스페이스 ID (wrkspc_로 시작, 없으면 Enter)"
    if ($ws) { Set-EnvValue "ANTHROPIC_WORKSPACE_ID" $ws.Trim(); Ok "워크스페이스 ID를 저장했습니다." }
}

# ---------------------------------------------------------------- 4. check key
Step 4 "Claude 연결 확인"
$env:PYTHONIOENCODING = "utf-8"
& $VenvPy -m mail_agent check-key
if ($LASTEXITCODE -ne 0) {
    Warn "Claude 연결을 확인하지 못했습니다. .env의 ROOT_API_KEY / ANTHROPIC_WORKSPACE_ID를 확인한 뒤 다시 실행하세요."
    Warn "(키 없이도 설치는 계속합니다. 웹 화면의 '연결 상태'에서 다시 확인할 수 있습니다.)"
}

# ---------------------------------------------------------------- 5. service
Step 5 "항상 켜지는 서비스로 등록"
$Url = "http://localhost:$Port/"
if ($NoService) {
    & (Join-Path $PSScriptRoot "start_background.ps1") -Port $Port
} else {
    & (Join-Path $PSScriptRoot "install_service.ps1") -Port $Port -NoBrowser
}

# ---------------------------------------------------------------- 6. open
Step 6 "설정 화면 열기"
Write-Host "  서버가 켜지기를 기다리는 중… (처음 실행은 1분 정도 걸릴 수 있습니다)"
$ready = $false
foreach ($i in 1..90) {
    try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $Port); $c.Close(); $ready = $true; break } catch { Start-Sleep -Seconds 1 }
}
if ($ready) {
    Ok "메일 비서가 실행 중입니다: $Url"
    if (-not $NoBrowser) { Start-Process "${Url}setup" }
    Write-Host ""
    Write-Host "설치가 끝났습니다. 브라우저의 '처음 설정' 3단계를 진행하세요." -ForegroundColor Green
    Write-Host "다음부터는 바탕화면의 '메일 비서' 아이콘이나 '메일비서 열기.bat'으로 열 수 있습니다." -ForegroundColor Green
} else {
    Warn "서버 응답을 확인하지 못했습니다. 로그: $ProjectDir\data\logs\server.log"
    exit 1
}
