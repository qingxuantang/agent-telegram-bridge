# SPDX-License-Identifier: MIT
# create-codex-tasks.ps1 — create the Codex Telegram bridge Scheduled Tasks.
# Replace <user> with your Windows username and <computer-name> with your PC name.
# Mirrors the Claude daemon (ClaudeCodeTelegramDaemon) pattern.
# Run once, as <user>, in an elevated PowerShell if possible (registration
# for the current user works without elevation; -RunLevel Highest is not set
# so the bridge runs in the user's session like the Claude daemon).

$ErrorActionPreference = "Stop"

# --- Bridge task: run at logon, hidden, restart on failure ---
$bridgeAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\<user>\codex-tg\scripts\run-bridge.ps1"' `
    -WorkingDirectory "C:\Users\<user>"
$bridgeTrigger = New-ScheduledTaskTrigger -AtLogOn -User "<computer-name>\<user>"
$bridgeSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "CodexTelegramBridge" `
    -Action $bridgeAction -Trigger $bridgeTrigger -Settings $bridgeSettings `
    -User "<computer-name>\<user>" -Force | Out-Null
Write-Output "CodexTelegramBridge registered."

# --- Daily restart task: 04:00, restart the bridge ---
$restartAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\<user>\codex-tg\scripts\restart-bridge.ps1"' `
    -WorkingDirectory "C:\Users\<user>"
$restartTrigger = New-ScheduledTaskTrigger -Daily -At 04:00
$restartSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "CodexTelegramBridgeRestart" `
    -Action $restartAction -Trigger $restartTrigger -Settings $restartSettings `
    -User "<computer-name>\<user>" -Force | Out-Null
Write-Output "CodexTelegramBridgeRestart registered."
