# register_scheduled_tasks.ps1
# Creates all 7 Windows Task Scheduler tasks for football-agent production pipeline.
# Uses Europe/Paris timezone, NT AUTHORITY\SYSTEM service account.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_tasks.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot  = "C:\Users\ruowa\Projects\football-agent"
$PythonExe    = "$ProjectRoot\.venv\Scripts\python.exe"
$TaskPath     = "\FootballAgent"
$TaskUser     = "NT AUTHORITY\SYSTEM"

$VerbosePreference = "Continue"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Registering Football Agent Scheduled Tasks" -ForegroundColor Cyan
Write-Host " Timezone  : Europe/Paris (CET/CEST auto)" -ForegroundColor Cyan
Write-Host " Principal : $TaskUser (ServiceAccount, Highest)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Remove existing tasks under \FootballAgent\
# ---------------------------------------------------------------------------
$existing = Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing FootballAgent tasks..." -ForegroundColor Yellow
    foreach ($t in $existing) {
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $TaskPath -Confirm:$false
        Write-Host "  [REMOVED] $TaskPath\$($t.TaskName)" -ForegroundColor DarkYellow
    }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Common task components
# ---------------------------------------------------------------------------

function New-FootballTask {
    param(
        [string]$TaskName,
        [string]$TaskId,
        [string]$Description,
        [string]$TriggerType,       # "Daily", "Repetition", "Weekly"
        [string]$StartTime = "",    # HH:MM for Daily/Weekly
        [int]$IntervalMinutes = 0,  # for Repetition
        [string]$DaysOfWeek = ""    # for Weekly, e.g. "Monday"
    )

    $argument = "-m app.workers.scheduler_runner --command $TaskId --trigger-source scheduler"

    $Action = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument $argument `
        -WorkingDirectory $ProjectRoot

    $Principal = New-ScheduledTaskPrincipal `
        -UserID $TaskUser `
        -LogonType ServiceAccount `
        -RunLevel Highest

    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew

    $trigger = switch ($TriggerType) {
        "Daily" {
            New-ScheduledTaskTrigger -Daily -At "$StartTime" -RandomDelay (New-TimeSpan -Seconds 30)
        }
        "Repetition" {
            New-ScheduledTaskTrigger -Daily -At "00:05" -RandomDelay (New-TimeSpan -Seconds 30) `
                | ForEach-Object {
                    $_.Repetition = (New-ScheduledTaskRepetition -Interval (New-TimeSpan -Minutes $IntervalMinutes) -Duration (New-TimeSpan -Days 1))
                    $_
                  }
        }
        "Weekly" {
            New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DaysOfWeek -At "$StartTime" -RandomDelay (New-TimeSpan -Seconds 30)
        }
    }

    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -TaskPath $TaskPath `
            -Action $Action `
            -Principal $Principal `
            -Settings $Settings `
            -Trigger $trigger `
            -Description $Description `
            -Force | Out-Null

        Write-Host "  [CREATED] $TaskPath\$TaskName" -ForegroundColor Green
        Write-Host "    Schedule  : $TriggerType $StartTime$DaysOfWeek" -ForegroundColor Gray
        Write-Host "    Task ID   : $TaskId" -ForegroundColor Gray
    } catch {
        Write-Host "  [FAILED] $TaskPath\$TaskName : $_" -ForegroundColor Red
        throw
    }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Register all 7 tasks
# ---------------------------------------------------------------------------

Write-Host "[1/9] ProviderHealthCheck — Daily 07:45" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-ProviderHealthCheck" `
    -TaskId "provider_health" `
    -Description "Health check all providers (API-Football, Odds API, WeatherAPI, PostgreSQL, Redis, OpenAI)" `
    -TriggerType "Daily" -StartTime "07:45"

Write-Host "[2/9] DailyProductionRun — Daily 08:00" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-DailyProductionRun" `
    -TaskId "daily_job" `
    -Description "Full production pipeline: fixtures -> odds -> picks -> settlement -> performance" `
    -TriggerType "Daily" -StartTime "08:00"

Write-Host "[3/9] ProductionRecoveryCheck — Daily 10:30" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-ProductionRecoveryCheck" `
    -TaskId "production_recovery" `
    -Description "Verify heartbeat and daily_job status; retry daily_run once if failed" `
    -TriggerType "Daily" -StartTime "10:30"

Write-Host "[4/9] PreKickoffValidation — Every 30 minutes" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-PreKickoffValidation" `
    -TaskId "pre_kickoff" `
    -Description "Refresh odds, lineups, injuries, weather for T-90 and T-30 fixtures" `
    -TriggerType "Repetition" -IntervalMinutes 30

Write-Host "[5/9] SettlementFallback — Daily 23:00" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-SettlementFallback" `
    -TaskId "settlement" `
    -Description "Settle all eligible paper bets (idempotent); update bankroll/ROI/CLV/performance" `
    -TriggerType "Daily" -StartTime "23:00"

Write-Host "[6/9] DailyPerformanceReport — Daily 23:30" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-DailyPerformanceReport" `
    -TaskId "daily_report" `
    -Description "Generate daily performance report (Markdown) with P&L, win rate, ROI" `
    -TriggerType "Daily" -StartTime "23:30"

Write-Host "[7/9] WeeklyPerformanceReport — Monday 08:30" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-WeeklyPerformanceReport" `
    -TaskId "weekly_report" `
    -Description "Generate weekly performance report (Markdown) with cumulative stats" `
    -TriggerType "Weekly" -StartTime "08:30" -DaysOfWeek "Monday"

Write-Host "[8/9] DashboardRefresh-1300 — Daily 13:00" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-DashboardRefresh-1300" `
    -TaskId "dashboard_refresh" `
    -Description "Midday snapshot: regenerate full dashboard HTML (read-only)" `
    -TriggerType "Daily" -StartTime "13:00"

Write-Host "[9/9] DashboardRefresh-1700 — Daily 17:00" -ForegroundColor White
New-FootballTask -TaskName "FootballAgent-DashboardRefresh-1700" `
    -TaskId "dashboard_refresh" `
    -Description "Pre-evening snapshot: regenerate full dashboard HTML (read-only)" `
    -TriggerType "Daily" -StartTime "17:00"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Registration Complete — 9 tasks created" -ForegroundColor Green
Write-Host " All times in Europe/Paris local time (CET/CEST auto)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$tasks = Get-ScheduledTask -TaskPath $TaskPath
foreach ($task in $tasks) {
    $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
    Write-Host "  $($task.TaskName)" -ForegroundColor White
    Write-Host "    State     : $($task.State)" -ForegroundColor Gray
    Write-Host "    Next Run  : $($info.NextRunTime)" -ForegroundColor Gray
    Write-Host ""
}

Write-Host "Unregister: powershell -ExecutionPolicy Bypass -File scripts\unregister_scheduled_tasks.ps1" -ForegroundColor Gray
