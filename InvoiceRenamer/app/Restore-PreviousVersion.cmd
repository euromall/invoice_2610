@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "INVOICE_RESTORE_SCRIPT=%~f0"
powershell.exe -NoProfile -Command "$text=[IO.File]::ReadAllText($env:INVOICE_RESTORE_SCRIPT); $body=$text -split '(?m)^# INVOICE_RESTORE_SCRIPT\r?$',2; if($body.Length -ne 2){exit 1}; & ([ScriptBlock]::Create($body[1]))"
if errorlevel 1 echo [복원 오류] 위 메시지를 확인해 주세요.
pause
exit /b
# INVOICE_RESTORE_SCRIPT
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'InvoiceRenamer'
$app = Join-Path $root 'app'
$backups = Join-Path $root 'update-backups'
try {
    if (-not (Test-Path -LiteralPath $backups)) { throw 'No update backup found.' }
    $backup = Get-ChildItem -LiteralPath $backups -Directory |
        Where-Object { $_.Name -notlike 'before-rollback-*' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'backup-ready.txt')) } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $backup) { throw 'No update backup found.' }
    if (Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
            Where-Object { $_.CommandLine -match 'invoice_renamer\.py' }) {
        throw 'Close InvoiceRenamer before restoring.'
    }
    $version = Get-Content -LiteralPath (Join-Path $backup.FullName 'backup-ready.txt') -Raw
    Write-Host "Rollback to $($version.Trim()) from $($backup.FullName)"
    $choice = Read-Host 'Restore [P]rogram only, [B]oth program and saved working records, or press Enter to cancel'
    if ($choice -notin @('P', 'B')) { exit 0 }
    $current = Join-Path $backups ('before-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    $savedApp = Join-Path $current 'app'
    $savedData = Join-Path $current 'records'
    New-Item -ItemType Directory -Path $savedApp, $savedData -Force | Out-Null
    Get-ChildItem -LiteralPath $app -Force |
        Where-Object { $_.Name -notin @('.venv', 'tessdata', '__pycache__') } |
        Copy-Item -Destination $savedApp -Recurse
    foreach ($name in @('monthly-close.json', 'invoice-edits.json')) {
        $record = Join-Path $root $name
        if (Test-Path -LiteralPath $record) { Copy-Item -LiteralPath $record -Destination $savedData }
    }
    Get-ChildItem -LiteralPath (Join-Path $backup.FullName 'app') -Force |
        Copy-Item -Destination $app -Recurse -Force
    if ($choice -eq 'B') {
        foreach ($name in @('monthly-close.json', 'invoice-edits.json')) {
            $record = Join-Path (Join-Path $backup.FullName 'records') $name
            if (Test-Path -LiteralPath $record) { Copy-Item -LiteralPath $record -Destination $root -Force }
        }
    }
    Write-Host "Rollback complete. The replaced files are saved in $current."
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
