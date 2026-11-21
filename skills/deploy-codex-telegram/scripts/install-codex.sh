#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# install-codex.sh — Install Codex CLI on an Ubuntu/Debian server (pilot host).
#
# Usage:
#   bash install-codex.sh [--check-only] [--dry-run] [--force] [--help]
#
#   --check-only   Only probe the host (OS, node/npm, egress, sandbox tools,
#                  existing codex) and print a report. Installs NOTHING,
#                  touches NO network endpoint for install purposes
#                  (egress probes are short HEAD requests; use --no-egress
#                  to skip even those).
#   --dry-run      Print every step that WOULD run, without running it.
#   --force        Reinstall even if `codex` is already on PATH.
#   --no-egress    Skip the egress probes (offline / proxied hosts).
#
# Install method (as of 2026-11-15, verify with `npm view @openai/codex`):
#   npm install -g @openai/codex
# No version is pinned here on purpose: pinning a version that later goes
# stale is worse than installing latest and printing `codex --version`.
# If you later want a pinned version, add e.g.
#   npm install -g @openai/codex@<version>
# and record the version in the pilot log.
#
# What this script NEVER does:
#   - No auth / login (the operator runs `codex login` directly; see PLAN.md §8.1).
#   - No token, key, or credential is written anywhere.
#   - No change to any Claude Code install, service, or token.
#
# Idempotent: safe to re-run; skips install when `codex` already exists
# (unless --force). Creates ~/.codex, ~/.codex/tg, ~/telegram-inbox.
set -euo pipefail

PROG="$(basename "$0")"
CHECK_ONLY=0
DRY_RUN=0
FORCE=0
NO_EGRESS=0

usage() {
  cat <<EOF
Usage: $PROG [--check-only] [--dry-run] [--force] [--no-egress] [--help]

  --check-only   Probe only; install nothing.
  --dry-run      Print steps without running them.
  --force        Reinstall even if codex is already installed.
  --no-egress    Skip egress probes to api.openai.com / chatgpt.com /
                 api.telegram.org / github.com.
  --help         Show this help.
EOF
}

log()  { printf '[install-codex] %s\n' "$*"; }
warn() { printf '[install-codex] WARN: %s\n' "$*" >&2; }
ok()   { printf '[install-codex] OK: %s\n' "$*"; }

run() {
  # run <cmd...>: honour --dry-run by printing instead of executing.
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[install-codex] DRY-RUN: %s\n' "$*"
  else
    "$@"
  fi
}

for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    --force)      FORCE=1 ;;
    --no-egress)  NO_EGRESS=1 ;;
    --help|-h)    usage; exit 0 ;;
    *) warn "unknown argument: $arg"; usage; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- checks
log "Host: $(uname -a)"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  log "OS: ${PRETTY_NAME:-$ID $VERSION_ID} (ID=$ID)"
  case "$ID" in
    ubuntu|debian) ok "supported OS family" ;;
    *) warn "OS '$ID' is not Ubuntu/Debian; npm install may still work, but this script is only tested on Ubuntu/Debian. Continuing." ;;
  esac
else
  warn "/etc/os-release not readable; cannot confirm OS."
fi

log "User: $(whoami)  HOME=$HOME"
read -r mem_total mem_avail _ <<< "$(free -m | awk '/^Mem:/ {print $2, $7}')"
log "Memory: ${mem_total:-?} MB total, ${mem_avail:-?} MB available"
log "Disk /home: $(df -h "$HOME" | awk 'NR==2 {print $4" available"}')"

have() { command -v "$1" >/dev/null 2>&1; }

for tool in curl node npm python3 git; do
  if have "$tool"; then ok "$tool: $($tool --version 2>/dev/null | head -1 || echo present)"; else warn "$tool: MISSING"; fi
done

# Sandbox tooling (Codex workspace-write sandbox uses Landlock / bubblewrap
# where available; absence is not fatal — Codex falls back — but record it).
if have bwrap; then ok "bubblewrap (bwrap): present"; else warn "bubblewrap (bwrap): missing — install with: sudo apt-get install -y bubblewrap"; fi
if [ -e /sys/kernel/security/landlock ]; then ok "Landlock: present"; else warn "Landlock: not detected at /sys/kernel/security/landlock (sandbox may fall back)"; fi

# Existing codex?
if have codex; then
  log "codex already installed: $(codex --version 2>&1 | head -1)"
  if [ "$FORCE" -eq 0 ] && [ "$CHECK_ONLY" -eq 0 ]; then
    log "Skipping install (use --force to reinstall)."
  fi
else
  warn "codex: not on PATH"
fi

# Egress probes — short HEAD requests, report only, never fatal.
if [ "$NO_EGRESS" -eq 0 ]; then
  log "Egress probes (10s timeout each, HEAD only):"
  for url in https://api.openai.com https://chatgpt.com https://api.telegram.org https://github.com https://registry.npmjs.org; do
    if have curl; then
      if curl -sI --max-time 10 -o /dev/null "$url" 2>/dev/null; then
        ok "$url reachable"
      else
        warn "$url UNREACHABLE (check proxy / firewall settings on the host)"
      fi
    else
      warn "curl missing; cannot probe $url"
    fi
  done
else
  log "Egress probes skipped (--no-egress)."
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  log "--check-only: report done, nothing installed."
  exit 0
fi

# ---------------------------------------------------------------- install
if have codex && [ "$FORCE" -eq 0 ]; then
  log "codex already present; skipping npm install."
else
  # Node/npm are required for the npm install route.
  if ! have node || ! have npm; then
    log "Node.js/npm missing; installing via NodeSource LTS (Ubuntu/Debian)."
    log "This needs sudo and network. If it fails, install Node 20+ another way, then re-run this script."
    run sudo apt-get update -y
    run sudo apt-get install -y ca-certificates curl gnupg
    # NodeSource setup script — fetched over HTTPS, standard upstream route.
    if [ "$DRY_RUN" -eq 0 ]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    else
      printf '[install-codex] DRY-RUN: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -\n'
    fi
    run sudo apt-get install -y nodejs
  fi
  log "Installing Codex CLI: npm install -g @openai/codex"
  run npm install -g @openai/codex
fi

# ------------------------------------------------------- directories
log "Creating ~/.codex, ~/.codex/tg, ~/telegram-inbox"
run mkdir -p "$HOME/.codex/tg" "$HOME/telegram-inbox"
run chmod 700 "$HOME/.codex" || true
# Do NOT chmod the whole ~/.codex/tg tree blindly on re-runs; only ensure the
# dirs exist. Token files are created 0600 by the bridge / operator.

# ------------------------------------------------------- verify
if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY-RUN: skipping verification steps."
  log "Next steps (see PLAN.md §10): codex login, then the 3-step manual check."
  exit 0
fi

if ! have codex; then
  warn "codex still not on PATH after install. Check npm global bin dir:"
  warn "  npm config get prefix  — and add <prefix>/bin to PATH, then re-run."
  exit 1
fi
ok "codex --version: $(codex --version 2>&1 | head -1)"
log "codex exec --help (first 30 lines):"
codex exec --help 2>&1 | head -30 || warn "codex exec --help failed — check the install"
log "codex app-server availability:"
if codex app-server --help >/dev/null 2>&1; then
  ok "codex app-server --help works (experimental; see PLAN.md §2.1)"
else
  warn "codex app-server --help did not succeed — exec-pilot path still works; verify flags on this host with: codex app-server --help"
fi
_schema_tmp="$(mktemp -d)"
if codex app-server generate-json-schema --out "$_schema_tmp" >/dev/null 2>&1; then
  ok "codex app-server generate-json-schema works"
else
  log "note: 'codex app-server generate-json-schema --out DIR' did not succeed on this build (not fatal; app-server itself is what matters)"
fi
rm -rf "$_schema_tmp"

cat <<EOF

[install-codex] Done.
Next steps (do NOT skip):
  1. Auth (operator only):  codex login --device-auth
     — or API key:      codex login --with-api-key
     Never paste a token into chat or into any script.
  2. Manual 3-step check (PLAN.md §10 step 5):
       codex exec --json --approve-for-me \\
         --skip-git-repo-check "用一句话回复：codex pilot ok"
       codex exec resume <thread_id> --json "上一句你说了什么？"
       codex app-server --help
  3. Migrate identity: bash scripts/migrate-claude-to-codex.sh   (dry-run first)
  4. Bridge: copy scripts/tg-codex-bridge.config.json.example to ~/.codex/tg/config.json,
     put the NEW BotFather token in ~/.codex/tg/bridge.env (0600),
     then: python3 scripts/tg-codex-bridge.py --dry-run --config ~/.codex/tg/config.json
EOF
