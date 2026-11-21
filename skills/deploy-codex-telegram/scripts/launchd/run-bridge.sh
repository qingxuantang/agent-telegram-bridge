#!/bin/bash
# SPDX-License-Identifier: MIT
# run-bridge.sh — wrapper for the Codex Telegram bridge (macOS launchd).
# launchd's default PATH is minimal and lacks node/npm/bun, so set it
# explicitly (lesson: daemon env had no node, codex exec failed).
# CODEX_API_KEY comes from the 0600 codex.env (the bridge reads the bot
# token from bridge.env itself). No secrets in the plist or this script.
export PATH=/usr/local/bin:/opt/homebrew/bin:$HOME/.npm-global/bin:$HOME/.bun/bin:/usr/bin:/bin:/usr/sbin:/sbin
CODEX_API_KEY="$(grep '^CODEX_API_KEY=' "$HOME/.codex/tg/codex.env" 2>/dev/null | cut -d= -f2-)"
if [ -z "$CODEX_API_KEY" ]; then
  echo "run-bridge: CODEX_API_KEY not found in ~/.codex/tg/codex.env" >&2
  exit 1
fi
export CODEX_API_KEY
exec /usr/bin/python3 "$HOME/codex-tg/scripts/tg-codex-bridge.py" --config "$HOME/.codex/tg/config.json"
