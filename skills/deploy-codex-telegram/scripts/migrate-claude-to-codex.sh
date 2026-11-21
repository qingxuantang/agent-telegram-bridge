# MIT License — see skills/deploy-codex-telegram/../../LICENSE.
# TODO (unverified): this script copies skills to ~/.agents/skills/, but the
# deployed Codex skills in this project live under ~/.codex/skills/. Check
# your Codex version's skill path before running with --apply.
#!/usr/bin/env bash
# migrate-claude-to-codex.sh — Move Claude Code identity (skills, CLAUDE.md,
# hooks inventory) to Codex locations. RUN ON THE SERVER.
#
# Usage:
#   bash migrate-claude-to-codex.sh [--apply] [--workspace DIR] [--help]
#
#   Default is DRY-RUN: prints exactly what would be copied / flagged,
#   changes nothing. Pass --apply to actually copy.
#   --workspace DIR  also copy skills into DIR/.agents/skills (per-workspace
#                      skills, in addition to the global ~/.agents/skills).
#
# What it does:
#   1. ~/.claude/skills/*  ->  ~/.agents/skills/*   (copy, never move)
#   2. CLAUDE.md (first found of ~/.claude/CLAUDE.md, ~/CLAUDE.md,
#      ./.claude/CLAUDE.md) -> ~/.codex/AGENTS.md
#      with a 32 KiB size check — Codex caps AGENTS.md at 32 KiB. If over,
#      it warns and suggests splitting (core file + per-topic skills).
#   3. Hooks: LISTS ~/.claude/settings.json hooks and ~/.claude/hooks/*
#      and reports them as NEED-REWRITE. It does NOT convert hook payloads:
#      Codex hook events look similar but the payload schema differs.
#   4. MCP: lists MCP servers in ~/.claude/settings.json / .mcp.json and
#      notes Codex keeps MCP config in ~/.codex/config.toml (client only).
#
# What it NEVER does:
#   - Never deletes or moves anything under ~/.claude/ (copy only).
#   - Never copies credentials: ~/.claude/.credentials.json, auth tokens,
#     channels/telegram/.env, secrets.env are explicitly excluded and listed
#     as NOT migrated (Codex has its own auth; see PLAN.md §8.1).
#   - Never writes a token or secret into any file.
#
# Exit code: 0 = report complete (dry-run or apply); 1 = usage error.
set -euo pipefail

PROG="$(basename "$0")"
APPLY=0
WORKSPACE=""

usage() {
  cat <<EOF
Usage: $PROG [--apply] [--workspace DIR] [--help]

  (no flags)     Dry-run: report what would move, change nothing.
  --apply        Actually copy skills + CLAUDE.md.
  --workspace DIR  Also copy skills into DIR/.agents/skills.
  --help         Show this help.
EOF
}

log()  { printf '[migrate] %s\n' "$*"; }
warn() { printf '[migrate] WARN: %s\n' "$*" >&2; }
ok()   { printf '[migrate] OK: %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --workspace)
      if [ $# -lt 2 ]; then echo "ERROR: --workspace needs a directory argument" >&2; usage; exit 1; fi
      WORKSPACE="$2"; shift 2 ;;
    --workspace=*) WORKSPACE="${1#--workspace=}"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) warn "unknown argument: $1"; usage; exit 2 ;;
  esac
done
if [ -n "$WORKSPACE" ] && [ -e "$WORKSPACE" ] && [ ! -d "$WORKSPACE" ]; then
  echo "ERROR: --workspace must be a directory: $WORKSPACE" >&2; exit 1
fi

if [ "$APPLY" -eq 0 ]; then
  log "DRY-RUN mode — nothing will be copied. Pass --apply to apply."
else
  log "APPLY mode — copying (never moving, never deleting ~/.claude/*)."
fi

CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
AGENTS_HOME="${AGENTS_HOME:-$HOME/.agents}"
AGENTS_LIMIT_BYTES=32768   # Codex AGENTS.md cap: 32 KiB

moved=0; skipped=0; manual=0

# ---------------------------------------------------------------- skills
log "--- Skills: $CLAUDE_HOME/skills -> $AGENTS_HOME/skills"
if [ -d "$CLAUDE_HOME/skills" ]; then
  count="$(find "$CLAUDE_HOME/skills" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
  log "found $count skill(s) under $CLAUDE_HOME/skills"
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$AGENTS_HOME/skills"
    cp -r "$CLAUDE_HOME/skills/." "$AGENTS_HOME/skills/"
    ok "copied $count skill(s) to $AGENTS_HOME/skills"
  else
    log "would copy: $CLAUDE_HOME/skills/* -> $AGENTS_HOME/skills/"
    find "$CLAUDE_HOME/skills" -mindepth 1 -maxdepth 1 | sort | while read -r s; do
      log "  would copy: $(basename "$s")"
    done
  fi
  moved=$((moved + count))
  if [ -n "$WORKSPACE" ]; then
    if [ "$APPLY" -eq 1 ]; then
      mkdir -p "$WORKSPACE/.agents/skills"
      cp -r "$CLAUDE_HOME/skills/." "$WORKSPACE/.agents/skills/"
      ok "also copied to $WORKSPACE/.agents/skills"
    else
      log "would also copy to: $WORKSPACE/.agents/skills/"
    fi
  fi
else
  warn "no $CLAUDE_HOME/skills directory — nothing to migrate"
  skipped=$((skipped + 1))
fi

# ---------------------------------------------------------------- CLAUDE.md
log "--- CLAUDE.md -> $CODEX_HOME/AGENTS.md (32 KiB cap: $AGENTS_LIMIT_BYTES bytes)"
CLAUDE_MD=""
for cand in "$CLAUDE_HOME/CLAUDE.md" "$HOME/CLAUDE.md" "$PWD/.claude/CLAUDE.md" "$PWD/CLAUDE.md"; do
  if [ -f "$cand" ]; then CLAUDE_MD="$cand"; break; fi
done
if [ -z "$CLAUDE_MD" ]; then
  warn "no CLAUDE.md found (checked $CLAUDE_HOME/CLAUDE.md, ~/CLAUDE.md, ./.claude/CLAUDE.md, ./CLAUDE.md)"
  skipped=$((skipped + 1))
else
  size="$(wc -c < "$CLAUDE_MD" | tr -d ' ')"
  log "source: $CLAUDE_MD ($size bytes)"
  if [ "$size" -gt "$AGENTS_LIMIT_BYTES" ]; then
    warn "OVER the 32 KiB AGENTS.md cap by $((size - AGENTS_LIMIT_BYTES)) bytes."
    warn "Codex will truncate/ignore the excess. Split it instead:"
    warn "  - Keep a short core in ~/.codex/AGENTS.md (identity, hard rules, how to work)"
    warn "  - Move per-topic detail into ~/.agents/skills/<topic>/SKILL.md files"
    warn "  - Keep the full palace text in the repo/memory, referenced by path"
    manual=$((manual + 1))
    if [ "$APPLY" -eq 1 ]; then
      warn "APPLY: copying anyway (truncated upstream) — review and split afterwards."
      mkdir -p "$CODEX_HOME"
      cp "$CLAUDE_MD" "$CODEX_HOME/AGENTS.md"
      ok "copied (oversize) to $CODEX_HOME/AGENTS.md — SPLIT IT before relying on it"
    else
      log "dry-run: would copy oversize file to $CODEX_HOME/AGENTS.md (not recommended as-is)"
    fi
  else
    if [ "$APPLY" -eq 1 ]; then
      mkdir -p "$CODEX_HOME"
      cp "$CLAUDE_MD" "$CODEX_HOME/AGENTS.md"
      ok "copied to $CODEX_HOME/AGENTS.md ($size bytes, under cap)"
    else
      log "would copy to: $CODEX_HOME/AGENTS.md ($size bytes, under cap)"
    fi
    moved=$((moved + 1))
  fi
fi

# ---------------------------------------------------------------- hooks
log "--- Hooks: inventory only — payloads need manual rewrite for Codex"
hook_found=0
if [ -f "$CLAUDE_HOME/settings.json" ]; then
  if grep -q -i '"hooks"' "$CLAUDE_HOME/settings.json" 2>/dev/null; then
    hook_found=1
    log "hooks declared in $CLAUDE_HOME/settings.json:"
    grep -o '"[A-Za-z]*ToolUse"\|"PermissionRequest"\|"SessionStart"\|"SessionEnd"\|"PreCompact"\|"Stop"\|"SubagentStop"' \
      "$CLAUDE_HOME/settings.json" 2>/dev/null | sort -u | while read -r h; do log "  hook event: $h"; done
  fi
fi
if [ -d "$CLAUDE_HOME/hooks" ]; then
  hook_found=1
  log "hook scripts in $CLAUDE_HOME/hooks:"
  find "$CLAUDE_HOME/hooks" -type f | sort | while read -r h; do log "  $h"; done
fi
# Palace-known per-machine hook scripts (may live outside ~/.claude/hooks).
for h in "$HOME/bypass-claude-folder.sh" "$HOME/ops/claude-tg-watchdog.sh"; do
  if [ -f "$h" ]; then hook_found=1; log "  known hook/helper script: $h"; fi
done
if [ "$hook_found" -eq 1 ]; then
  warn "Hooks are NOT auto-converted. Codex hook events look similar"
  warn "(PreToolUse / PermissionRequest / PostToolUse ...) but the JSON"
  warn "payload schema differs — rewrite each hook and test it under Codex."
  manual=$((manual + 1))
else
  ok "no hooks found — nothing to rewrite"
fi

# ---------------------------------------------------------------- MCP
log "--- MCP servers: inventory only"
mcp_found=0
for f in "$CLAUDE_HOME/settings.json" "$HOME/.mcp.json" "$PWD/.mcp.json"; do
  if [ -f "$f" ] && grep -q -i 'mcpServers' "$f" 2>/dev/null; then
    mcp_found=1
    log "MCP servers declared in $f — re-declare in ~/.codex/config.toml"
    log "  (Codex is an MCP *client* only; there is no mcp-server mode.)"
  fi
done
[ "$mcp_found" -eq 0 ] && ok "no MCP server declarations found"
[ "$mcp_found" -eq 1 ] && manual=$((manual + 1))

# ---------------------------------------------------------------- never copy
log "--- Explicitly NOT migrated (by design):"
for f in "$CLAUDE_HOME/.credentials.json" "$CLAUDE_HOME/channels/telegram/.env" \
         "$HOME/.codex/auth.json" "$HOME/claude-telegram/secrets.env"; do
  if [ -e "$f" ]; then log "  NOT copied (credentials/token): $f"; fi
done
log "  (Codex auth is separate: codex login --device-auth / --with-api-key.)"

# ---------------------------------------------------------------- report
echo
log "================ REPORT ================"
log "skills/items counted: $moved"
log "skipped (not found): $skipped"
log "needs manual work: $manual item(s) (hooks/MCP/oversize AGENTS.md)"
if [ "$APPLY" -eq 0 ]; then
  log "DRY-RUN complete — nothing changed. Review, then re-run with --apply."
else
  log "APPLY complete. Verify:"
  log "  ls $AGENTS_HOME/skills/"
  log "  wc -c $CODEX_HOME/AGENTS.md   (must be <= $AGENTS_LIMIT_BYTES)"
  log "  codex exec --json \"...\"      (smoke test)"
fi
log "Claude side untouched: ~/.claude/ was only read, never modified."
