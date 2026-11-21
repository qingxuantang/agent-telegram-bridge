# SPDX-License-Identifier: MIT
# restart-bridge.ps1 — daily 04:00 restart of the Codex Telegram bridge task.
# Mirrors the servers' 04:00 timer and the Mac's restart plist.
Stop-ScheduledTask -TaskName CodexTelegramBridge -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName CodexTelegramBridge
