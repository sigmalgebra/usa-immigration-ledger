# Registers a daily Windows scheduled task that runs fetch-state.ps1 from its own clone. Missed runs (machine off or
# asleep) start as soon as the machine is back. Run once; rerunning replaces the task.
$clone = Join-Path $env:LOCALAPPDATA "usa-immigration-ledger-fetch"
if (-not (Test-Path $clone)) {
    git clone --quiet https://github.com/sigmalgebra/usa-immigration-ledger.git $clone
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
}
$script = Join-Path $clone "scripts\fetch-state.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 10am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "usa-immigration-ledger fetch-state" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Fetch new State Dept files for usa-immigration-ledger" -Force | Out-Null
Write-Output "registered; log at $clone.log"
