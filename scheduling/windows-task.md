# Daily run on Windows (Task Scheduler)

Adjust the two paths, then run this once in an **elevated PowerShell**:

```powershell
$proj = "C:\tender-radar"
$py   = "$proj\.venv\Scripts\python.exe"

$action  = New-ScheduledTaskAction -Execute $py -Argument "run.py fetch --mark-seen" -WorkingDirectory $proj
$trigger = New-ScheduledTaskTrigger -Daily -At 7:15am
$set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
             -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName "TenderRadar" -Action $action -Trigger $trigger `
  -Settings $set -Description "Daily public tender fetch and scoring"
```

Check it: `Get-ScheduledTaskInfo TenderRadar`
Run it now: `Start-ScheduledTask TenderRadar`
Remove it: `Unregister-ScheduledTask TenderRadar -Confirm:$false`

`-StartWhenAvailable` means a missed run (laptop asleep at 07:15) fires as soon
as the machine wakes. With `lookback_days: 3` in config.yaml nothing is lost.

Logs: add `>> "$proj\data\run.log" 2>&1` by wrapping the call in a small
`run-daily.cmd`, since Task Scheduler cannot redirect output on its own:

```cmd
@echo off
cd /d C:\tender-radar
.venv\Scripts\python.exe run.py fetch --mark-seen >> data\run.log 2>&1
```
