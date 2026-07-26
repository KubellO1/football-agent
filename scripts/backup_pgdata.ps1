<#
.SYNOPSIS
    Enhanced PostgreSQL backup for football-agent database with monthly restore test.

.DESCRIPTION
    Daily pg_dump → 14-day retention. Monthly restore test (first run each month)
    restores latest backup to a temp database and compares row counts across all
    core tables, outputting a validation report.

.PARAMETER BackupDir
    Directory to store backup files. Default: project's backups\ folder.

.PARAMETER RetentionDays
    Number of days to retain backups. Default: 14.

.PARAMETER ContainerName
    Name of the PostgreSQL Docker container. Default: football-postgres.

.PARAMETER DatabaseName
    Name of the database to back up. Default: football.

.PARAMETER Username
    PostgreSQL user. Default: football.

.PARAMETER SkipRestoreTest
    Skip the monthly restore test even if it's the first run of the month.

.EXAMPLE
    .\scripts\backup_pgdata.ps1
    .\scripts\backup_pgdata.ps1 -RetentionDays 30
    .\scripts\backup_pgdata.ps1 -SkipRestoreTest
#>

param(
    [string]$BackupDir = (Join-Path $PSScriptRoot "..\backups"),
    [int]$RetentionDays = 14,
    [string]$ContainerName = "football-postgres",
    [string]$DatabaseName = "football",
    [string]$Username = "football",
    [switch]$SkipRestoreTest
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$BackupDir = [System.IO.Path]::GetFullPath($BackupDir)
$ProjectRoot = (Get-Item (Join-Path $PSScriptRoot "..")).FullName
$OutputDir = Join-Path $ProjectRoot "output"
$RestoreDbName = "football_restore_test"

$CoreTables = @(
    "competitions", "seasons", "teams", "fixtures",
    "predictions", "value_bets", "odds_snapshots",
    "decision_logs", "settlements", "bookmakers",
    "bankroll_entries", "performance_snapshots"
)

foreach ($dir in @($BackupDir, $OutputDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

function Test-DockerReady {
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker is not running or not accessible. Aborting."
        exit 1
    }
    $containerStatus = docker inspect -f '{{.State.Running}}' $ContainerName 2>&1
    if ($LASTEXITCODE -ne 0 -or $containerStatus -ne "true") {
        Write-Error "Container '$ContainerName' is not running or does not exist. Aborting."
        exit 1
    }
}

function Invoke-DailyBackup {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupFile = "football_agent_backup_${timestamp}.sql"
    $backupPath = Join-Path $BackupDir $backupFile
    $tempPath = Join-Path $BackupDir "${backupFile}.tmp"

    Write-Host "============================================"
    Write-Host " PostgreSQL Backup - football"
    Write-Host "============================================"
    Write-Host "Date       : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "Container  : $ContainerName"
    Write-Host "Database   : $DatabaseName"
    Write-Host "Output     : $backupPath"
    Write-Host "Retention  : $RetentionDays days"
    Write-Host "============================================"

    Write-Host "`n[1/3] Running pg_dump..."
    $startTime = Get-Date

    $dumpResult = docker exec $ContainerName pg_dump -U $Username $DatabaseName 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Error "pg_dump failed: $dumpResult"
        exit 1
    }

    $dumpResult | Out-File -FilePath $tempPath -Encoding UTF8

    Write-Host "[2/3] Verifying backup integrity..."
    $fileSize = (Get-Item $tempPath).Length
    if ($fileSize -lt 1024) {
        Remove-Item $tempPath -Force -ErrorAction SilentlyContinue
        Write-Error "Backup file is too small ($fileSize bytes). Possible empty dump. Aborting."
        exit 1
    }

    $firstLine = Get-Content $tempPath -TotalCount 1
    if ($firstLine -notmatch "^--|^CREATE|^SET|^SELECT") {
        Remove-Item $tempPath -Force -ErrorAction SilentlyContinue
        Write-Error "Backup file does not look like a valid PostgreSQL dump. First line: '$firstLine'. Aborting."
        exit 1
    }

    Move-Item -Force $tempPath $backupPath
    $elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
    Write-Host "  OK: $backupFile ($([math]::Round($fileSize/1MB, 2)) MB) completed in ${elapsed}s"

    Write-Host "[3/3] Pruning backups older than $RetentionDays days..."
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $oldBackups = Get-ChildItem -Path $BackupDir -Filter "football_agent_backup_*.sql" |
        Where-Object { $_.LastWriteTime -lt $cutoff }

    if ($oldBackups.Count -gt 0) {
        foreach ($f in $oldBackups) {
            Remove-Item $f.FullName -Force
            Write-Host "  Removed: $($f.Name)"
        }
        Write-Host "  Pruned $($oldBackups.Count) old backup(s)."
    } else {
        Write-Host "  No old backups to prune."
    }

    $remaining = (Get-ChildItem -Path $BackupDir -Filter "football_agent_backup_*.sql").Count
    $totalSize = [math]::Round(
        (Get-ChildItem -Path $BackupDir -Filter "football_agent_backup_*.sql" |
            Measure-Object -Property Length -Sum).Sum / 1MB, 2
    )

    Write-Host "`n============================================"
    Write-Host " Backup Complete"
    Write-Host "============================================"
    Write-Host "File       : $backupFile"
    Write-Host "Size       : $([math]::Round($fileSize/1MB, 2)) MB"
    Write-Host "Retained   : $remaining backups ($totalSize MB total)"
    Write-Host "============================================"

    return @{
        BackupFile = $backupPath
        FileSize   = $fileSize
        Timestamp  = $timestamp
    }
}

function Should-RunRestoreTest {
    if ($SkipRestoreTest) { return $false }
    $monthKey = Get-Date -Format "yyyy-MM"
    $markerFile = Join-Path $BackupDir ".restore_test_${monthKey}"
    if (Test-Path $markerFile) { return $false }
    "" | Out-File -FilePath $markerFile -Encoding UTF8
    return $true
}

function Get-SourceRowCounts {
    $counts = @{}
    foreach ($tbl in $CoreTables) {
        $sql = "SELECT COUNT(*) FROM $tbl;"
        $result = docker exec $ContainerName psql -U $Username -d $DatabaseName -t -c $sql 2>&1
        $clean = ($result -replace '\s+', '' -replace '\n', '' -replace '\r', '').Trim()
        $counts[$tbl] = if ([int]::TryParse($clean, [ref]$null)) { [int]$clean } else { -1 }
    }
    return $counts
}

function Invoke-RestoreTest {
    param([string]$BackupFilePath, [long]$BackupFileSize)

    Write-Host "`n============================================"
    Write-Host " Monthly Restore Test"
    Write-Host "============================================"

    $restoreTestStartTime = Get-Date
    $monthKey = Get-Date -Format "yyyy-MM"
    $reportPath = Join-Path $OutputDir "backup_restore_test_${monthKey}.md"
    $backupSizeMB = [math]::Round($BackupFileSize / 1MB, 2)

    Write-Host "[1/5] Counting rows in source database ($DatabaseName)..."
    $tableCountStartTime = Get-Date
    $sourceCounts = Get-SourceRowCounts
    $sourceTableCountElapsed = [math]::Round(((Get-Date) - $tableCountStartTime).TotalSeconds, 1)
    $sourceSummary = ($sourceCounts.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', '
    Write-Host "  Source counts: $sourceSummary (${sourceTableCountElapsed}s)"

    Write-Host "[2/5] Preparing restore database..."
    docker exec $ContainerName psql -U $Username -c "DROP DATABASE IF EXISTS $RestoreDbName;" 2>&1 | Out-Null
    docker exec $ContainerName psql -U $Username -c "CREATE DATABASE $RestoreDbName;" 2>&1 | Out-Null

    Write-Host "[3/5] Restoring backup to $RestoreDbName..."
    $pgRestoreStartTime = Get-Date
    $backupFileName = Split-Path $BackupFilePath -Leaf
    docker cp $BackupFilePath "${ContainerName}:/tmp/${backupFileName}" 2>&1 | Out-Null
    $restoreResult = docker exec $ContainerName psql -U $Username -d $RestoreDbName -f "/tmp/${backupFileName}" 2>&1
    $restoreExitCode = $LASTEXITCODE
    docker exec $ContainerName rm -f "/tmp/${backupFileName}" 2>&1 | Out-Null
    $pgRestoreElapsed = [math]::Round(((Get-Date) - $pgRestoreStartTime).TotalSeconds, 1)

    if ($restoreExitCode -ne 0) {
        Write-Host "  RESTORE FAILED (${pgRestoreElapsed}s)"
        $totalElapsed = [math]::Round(((Get-Date) - $restoreTestStartTime).TotalSeconds, 1)
        $reportLines = @(
            "# Backup Restore Test — $monthKey", "",
            "**Result**: FAILED", "",
            "Restore to temp database `$RestoreDbName` failed.", "",
            "| Metric | Value |",
            "|--------|-------|",
            "| Backup file | $(Split-Path $BackupFilePath -Leaf) |",
            "| Backup size (MB) | $backupSizeMB |",
            "| pg_restore elapsed (s) | $pgRestoreElapsed |",
            "| Total restore test elapsed (s) | $totalElapsed |",
            "", "```", ($restoreResult -join "`n"), "```", "",
            "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        )
        $reportLines -join "`n" | Out-File -FilePath $reportPath -Encoding UTF8
        Write-Error "Restore test FAILED. See report: $reportPath"
        exit 1
    }

    Write-Host "  Restore completed in ${pgRestoreElapsed}s"

    Write-Host "[4/5] Comparing row counts..."
    $verifyStartTime = Get-Date
    $restoreCounts = @{}
    $mismatches = @()
    $perTableVerificationTimes = @{}

    foreach ($tbl in $CoreTables) {
        $singleTableStart = Get-Date
        $sql = "SELECT COUNT(*) FROM $tbl;"
        $result = docker exec $ContainerName psql -U $Username -d $RestoreDbName -t -c $sql 2>&1
        $clean = ($result -replace '\s+', '' -replace '\n', '' -replace '\r', '').Trim()
        $singleTableElapsed = [math]::Round(((Get-Date) - $singleTableStart).TotalSeconds, 2)
        $perTableVerificationTimes[$tbl] = $singleTableElapsed

        $restoreCounts[$tbl] = if ([int]::TryParse($clean, [ref]$null)) { [int]$clean } else { -1 }

        $sourceVal = $sourceCounts[$tbl]
        $restoreVal = $restoreCounts[$tbl]

        if ($sourceVal -ne $restoreVal) {
            $mismatches += @{ Table = $tbl; Source = $sourceVal; Restore = $restoreVal }
            Write-Host "  MISMATCH: $tbl (source=$sourceVal, restore=$restoreVal) — ${singleTableElapsed}s"
        } else {
            Write-Host "  OK: $tbl ($sourceVal rows) — ${singleTableElapsed}s"
        }
    }

    $verifyElapsed = [math]::Round(((Get-Date) - $verifyStartTime).TotalSeconds, 1)

    Write-Host "[5/5] Cleaning up temp database..."
    docker exec $ContainerName psql -U $Username -c "DROP DATABASE IF EXISTS $RestoreDbName;" 2>&1 | Out-Null

    $totalElapsed = [math]::Round(((Get-Date) - $restoreTestStartTime).TotalSeconds, 1)

    $overallStatus = if ($mismatches.Count -eq 0) { "PASSED" } else { "FAILED — $($mismatches.Count) table(s) with count mismatch" }

    $reportLines = @(
        "# Backup Restore Test — $monthKey", "",
        "**Result**: $overallStatus", "",
        "## Timing Summary", "",
        "| Metric | Value |",
        "|--------|-------|",
        "| Backup file | $(Split-Path $BackupFilePath -Leaf) |",
        "| Backup size (MB) | $backupSizeMB |",
        "| Source DB table counts | ${sourceTableCountElapsed}s |",
        "| pg_restore elapsed | ${pgRestoreElapsed}s |",
        "| Restore DB table verification | ${verifyElapsed}s |",
        "| Total restore test elapsed | ${totalElapsed}s |",
        "",
        "## Table Row Count Comparison", "",
        "| Table | Source ($DatabaseName) | Restore ($RestoreDbName) | Match | Verification (s) |",
        "|-------|---------------|----------|-------|------------------|"
    )

    foreach ($tbl in $CoreTables) {
        $match = if ($sourceCounts[$tbl] -eq $restoreCounts[$tbl]) { "OK" } else { "MISMATCH" }
        $verifyTime = $perTableVerificationTimes[$tbl]
        $reportLines += "| $tbl | $($sourceCounts[$tbl]) | $($restoreCounts[$tbl]) | $match | ${verifyTime} |"
    }

    $reportLines += ""
    $reportLines += "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

    $reportLines -join "`n" | Out-File -FilePath $reportPath -Encoding UTF8

    if ($mismatches.Count -gt 0) {
        Write-Host "`n============================================"
        Write-Host " RESTORE TEST FAILED — $($mismatches.Count) mismatch(es)"
        Write-Host " Report: $reportPath"
        Write-Host "============================================"
        Write-Error "Restore test: $($mismatches.Count) table mismatches detected"
        exit 1
    }

    Write-Host "`n============================================"
    Write-Host " Restore Test PASSED — all $($CoreTables.Count) tables match"
    Write-Host " Report: $reportPath"
    Write-Host "============================================"
}

# ── Main ──
Test-DockerReady
$backupInfo = Invoke-DailyBackup

if (Should-RunRestoreTest) {
    Invoke-RestoreTest -BackupFilePath $backupInfo.BackupFile -BackupFileSize $backupInfo.FileSize
} else {
    Write-Host "`n[Restore Test] Skipped — already completed this month or --SkipRestoreTest specified."
}
