#!/bin/bash
# SPDX-License-Identifier: MIT
# restart-bridge.sh — daily 04:00 restart for the Codex Telegram bridge
# (mirrors the servers' 04:00 systemd timer). kickstart -k kills the
# running bridge and starts it fresh.
launchctl kickstart -k "gui/$(id -u)/com.codex.telegram-bridge"
