#!/bin/bash
# SPDX-License-Identifier: MIT
# codex-tg container bootstrap (runs as 'codex', PID 1). Idempotent: safe to
# re-run on every container (re)start. Writes the Codex env files + config from
# the container env (passed by run.sh -e), then launches the Telegram bridge
# in the foreground and stays alive.
set -uo pipefail
H=/home/codex
export PATH="/usr/local/bin:$PATH"

mkdir -p "$H/.codex/tg"

# --- 1. env files from container env (run.sh passes -e CODEX_API_KEY -e TELEGRAM_BOT_TOKEN) ---
if [ -n "${CODEX_API_KEY:-}" ]; then
  printf 'CODEX_API_KEY=%s\n' "$CODEX_API_KEY" > "$H/.codex/tg/codex.env"
  chmod 600 "$H/.codex/tg/codex.env"
else
  echo "[entrypoint] WARNING: CODEX_API_KEY not set in container env" >&2
fi
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TELEGRAM_BOT_TOKEN" > "$H/.codex/tg/bridge.env"
  chmod 600 "$H/.codex/tg/bridge.env"
else
  echo "[entrypoint] WARNING: TELEGRAM_BOT_TOKEN not set in container env" >&2
fi

# --- 2. config.toml (danger-full-access; REQUIRED for resume turns) ---
# `codex exec resume` takes no --approve-for-me, so without this resume turns
# fall back to read-only (same fix as macOS/Windows).
if [ ! -f "$H/.codex/config.toml" ]; then
  printf 'sandbox_mode = "danger-full-access"\n' > "$H/.codex/config.toml"
  echo "[entrypoint] wrote config.toml (danger-full-access)"
fi

# --- 3. config.json (create if missing; the bridge merges it over its defaults) ---
if [ ! -f "$H/.codex/tg/config.json" ]; then
  # NOTE: 1234567890 below is a placeholder — replace with your own Telegram user id.
  cat > "$H/.codex/tg/config.json" <<'JSON'
{
  "bot_token": "PASTE_TOKEN_HERE",
  "allowed_user_ids": [1234567890],
  "allowed_chat_ids": [],
  "codex_bin": "codex",
  "workdir": "~",
  "inbox_dir": "~/telegram-inbox",
  "state_file": "~/.codex/tg/state.json",
  "log_file": "~/.codex/tg/bridge.log",
  "lock_file": "~/.codex/tg/bridge.lock",
  "codex_timeout_s": 1800,
  "rotation_session_max_bytes": 1000000,
  "rotation_cumulative_input_max": 5000000,
  "sandbox": "workspace-write",
  "approve_for_me": true
}
JSON
  chmod 600 "$H/.codex/tg/config.json"
  echo "[entrypoint] wrote config.json"
fi

# --- 4. launch the bridge in the foreground ---
# stdin is /dev/null: codex reads stdin and blocks forever on a live pipe
# (found on Windows); detached docker gives no stdin anyway, but be explicit.
echo "[entrypoint] starting bridge..."
exec python3 "$H/tg-codex-bridge.py" --config "$H/.codex/tg/config.json" < /dev/null
