<#
Registers (or removes) the Windows Task Scheduler entry that runs the engine every 30 minutes (D-008).

    powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1            # install, or replace
    powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1 -Remove    # remove

The task runs `.venv\Scripts\pythonw.exe -m riffi_ingest fetch --due` from the project folder with no window,
only while this user is logged on (no password is stored), also on battery. After a missed start (laptop off
or asleep) it runs as soon as it can. Never two at once; Windows stops a run after 1 hour.
Check on it with: .venv\Scripts\python.exe -m riffi_ingest status
#>
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$TaskName = 'Riffi ingestion engine - fetch'
$Project = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed '$TaskName' (if it was there)."
    return
}

$Pythonw = Join-Path $Project '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pythonw)) { throw "Cannot find $Pythonw - set up the virtual environment first (README)." }

$now = Get-Date
$start = $now.Date.AddHours($now.Hour).AddMinutes(30 * ([math]::Floor($now.Minute / 30) + 1))  # next :00 or :30
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m riffi_ingest fetch --due' -WorkingDirectory $Project
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'Riffi ingestion engine: fetch the sources that are due (config/schedule.yaml). See README.' | Out-Null
Write-Output "Installed '$TaskName': every 30 minutes from $($start.ToString('yyyy-MM-dd HH:mm'))."
