# unregister_scheduled_tasks.ps1
# Removes all FootballAgent scheduled tasks.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\unregister_scheduled_tasks.ps1

$ErrorActionPreference = "Continue"

$TaskPath = "\FootballAgent"
$TaskNames = @(
    "DailyProductionRun",
    "PreKickoffValidation",
    "SettlementFallback",
    "HealthVerification"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Unregistering Football Agent Scheduled Tasks" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$removed = 0
foreach ($name in $TaskNames) {
    $existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  [REMOVE] $TaskPath\$name" -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $name -Confirm:$false
        $removed++
    } else {
        Write-Host "  [SKIP]   $TaskPath\$name (not found)" -ForegroundColor Gray
    }
}

# Clean up the task folder if empty
$remaining = Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue
if (-not $remaining) {
    Write-Host ""
    Write-Host "  [CLEAN] Task folder $TaskPath is empty" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Done. Removed $removed task(s)." -ForegroundColor Green
