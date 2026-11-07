# Register the two backup scheduled tasks (daily local, weekly remote).
# Run from an elevated-or-normal PowerShell; it only touches the current user.
# Review the paths first: they point at this repository checkout.

param(
    [string]$Vault = "$env:USERPROFILE\.jarvis\vault",
    [string]$Local = "$env:USERPROFILE\.jarvis\backups",
    [string]$Python = "$PSScriptRoot\..\..\.jarvis-python-3.14.10\python.exe",
    [string]$Daily = "02:00",
    [string]$Weekly = "Sunday 03:00"
)

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cli = Join-Path $repo 'backend\experimental\backup_cli.py'

if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
if (-not (Test-Path $cli)) { throw "CLI not found: $cli" }

$arguments = "`"$cli`" run --vault `"$Vault`" --local `"$Local`""
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $repo

$daily = New-ScheduledTaskTrigger -Daily -At $Daily
$dailyTask = New-ScheduledTask -Action $action -Trigger $daily -Description "Jarvis daily local backup (synthetic vault)"

$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At ($Weekly -replace '^Sunday ', '')
$weeklyAction = New-ScheduledTaskAction -Execute $Python -Argument ($arguments + ' --remote') -WorkingDirectory $repo
$weeklyTask = New-ScheduledTask -Action $weeklyAction -Trigger $weeklyTrigger -Description "Jarvis weekly offsite backup (R2)"

Register-ScheduledTask -TaskName 'Jarvis Backup Daily' -InputObject $dailyTask -Force | Out-Null
Register-ScheduledTask -TaskName 'Jarvis Backup Weekly' -InputObject $weeklyTask -Force | Out-Null

Write-Host "Registered 'Jarvis Backup Daily' and 'Jarvis Backup Weekly'."
Write-Host "Credentials: run 'python backend/experimental/backup_cli.py store-passphrase' once."
Write-Host "The tasks read the passphrase from Windows Credential Manager; no secret is stored in the task."
