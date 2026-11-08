# 매일 아침 메일 브리핑을 Windows 작업 스케줄러에 등록합니다.
# 사용: PowerShell에서  .\scripts\register_daily_task.ps1 -Time "07:30"
# 해제: Unregister-ScheduledTask -TaskName "KMU Mail Agent" -Confirm:$false
param(
    [string]$Time = "07:30",
    [string]$TaskName = "KMU Mail Agent"
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "가상환경이 없습니다: $Python  (python -m venv .venv 후 pip install -r requirements.txt)" }

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m mail_agent run" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
# 노트북이 잠자기여도 깨워서 실행, 놓친 실행은 켜지면 바로 실행
$Settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Outlook 메일을 Claude로 분류해 아침 브리핑과 캘린더 일정을 만듭니다." -Force | Out-Null
Write-Host "등록 완료: 매일 $Time 에 '$TaskName' 실행 (로그: data\logs\mail_agent.log)"
