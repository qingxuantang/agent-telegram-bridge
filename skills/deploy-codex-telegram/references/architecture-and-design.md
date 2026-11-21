# Architecture and Design — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). Companion references:
> [bridge-config.md](bridge-config.md),
> [sandbox-and-approval.md](sandbox-and-approval.md),
> [thread-rotation.md](thread-rotation.md).

## The one-sentence version

Codex CLI has no Telegram channel, so a small Python bridge daemon owns the
Telegram side and drives Codex from the outside — the exact inverse of the
Claude Code architecture, where the CLI owns Telegram.

## Why Codex cannot be a 1:1 replacement for Claude Code

| Dimension | Claude Code (deploy-telegram) | Codex CLI (this skill) |
|---|---|---|
| Telegram接入 | Official Telegram plugin; `claude --channels` built in; Bun `server.ts` runs as an MCP child process and long-polls `getUpdates` | **No `--channels`, no official Telegram plugin** |
| MCP role | CLI is both MCP host and channel server | Codex is **MCP client only**; `codex mcp-server` was removed in 0.154.0 (deprecated 2026-08-24) |
| Who owns Telegram | The CLI process | **The bridge daemon**, outside Codex |
| Who sends the reply | The model, by calling a `reply` MCP tool | The bridge, deterministically, after the turn finishes |
| Non-interactive call | `claude -p` / `--channels` daemon | `codex exec --json` (JSONL) / `codex exec resume <thread_id>`; `codex app-server` (JSON-RPC 2.0 over stdio, experimental) |
| Skills / memory | `~/.claude/skills/`, `CLAUDE.md` | Agent Skills standard `.agents/skills/<name>/SKILL.md`, `AGENTS.md` (**32 KiB cap**) |
| Hooks | Claude hooks | Event surface is similar (PreToolUse / PermissionRequest / PostToolUse, …) but the **payload schema differs — every hook must be rewritten and retested** |

The migration is therefore not "swap `claude` for `codex` in one command".
It is an architecture change: Telegram logic moves out of the agent process
into an independent bridge process that invokes Codex.

## What the bridge does

The bridge (`scripts/tg-codex-bridge.py`, Python stdlib only — no pip
dependencies) is one process per machine. Its responsibilities:

1. **Long-poll Telegram** via `getUpdates` (one bot token per machine, a fresh
   BotFather bot — never reuse the Claude bot's token, or the two pollers
   fight and produce 409 Conflict).
2. **Allowlist gate.** Only the configured user id and chat ids are served.
   Everything else is ignored silently — no reply, no execution. The
   allowlist is the only gate; the bridge is effectively a remote shell.
3. **Per-chat thread mapping.** `~/.codex/tg/state.json` maps each chat id to
   its current Codex `thread_id` (taken from the first turn's
   `thread.started` JSONL event; rollouts live in `~/.codex/sessions/`).
   Messages for one chat are serialised — one turn at a time — because
   Codex has no concurrent-resume lock and two runs on the same thread
   corrupt state. Different chats have separate queues and worker threads.
4. **Deterministic commands** handled by the bridge, never sent to the model:
   `/new` (drop the thread mapping, next message starts fresh),
   `/status` (thread id, queue depth, running state, Codex version, bridge
   uptime), `/cancel` (kill the running Codex process for that chat, drop
   queued messages; the thread is kept, and a failed resume auto-retries as
   a fresh thread), `/help` / `/start`.
5. **Attachments.** Photos/documents are downloaded to
   `~/telegram-inbox/<chat>/<date>_<file_id>_<name>` and their path is
   prepended to the prompt.
6. **Watchdog / timeout.** Each `codex exec` run is bounded by
   `codex_timeout_s` (deployed value **1800**). See
   [post-deploy-hardening.md](post-deploy-hardening.md) for why 600 was too
   short. On timeout the process is killed and the user is told what
   happened.
7. **Thread rotation.** When a session file exceeds 1 MB or cumulative input
   exceeds 5M tokens, the bridge runs one archive turn on the old thread,
   then starts a fresh one. See [thread-rotation.md](thread-rotation.md).

## Invocation shape (pilot / current)

```
Telegram → tg-codex-bridge.py → codex exec [--json] → parse JSONL → sendMessage
                                  └─ resume <thread_id> to continue
```

- First turn:
  `codex exec --json --approve-for-me --skip-git-repo-check "<prompt>"`
- Continuation:
  `codex exec resume <thread_id> --json --skip-git-repo-check "<prompt>"`
  — resume accepts no `-s` / `-C` / `--approve-for-me`; the sandbox is fixed
  at thread creation. See [sandbox-and-approval.md](sandbox-and-approval.md).
- Accepted limits of this shape: no true streaming (one long message after
  the whole turn finishes), no mid-turn approval or steering, `/cancel` can
  only kill the running process and clear the queue.

## The app-server direction (future evolution)

The intended upgrade path is to swap the bridge's executor from `codex exec`
to `codex app-server` — JSON-RPC 2.0 over stdio, with
`initialize / thread/start / thread/resume / turn/start / turn/steer`,
streaming `item/agentMessage/delta` events, and approval requests mappable to
Telegram Approve/Deny buttons. `request_user_input` (the AskUserQuestion
equivalent) is still experimental: do not depend on it in the pilot — things
needing a human decision are handled by the bridge as "decline / skip and
explain".

Why it is not the pilot path: as of the writing of this skill, `app-server`
is still marked **experimental**, and its 24/7 stability, memory growth,
quota exhaustion mid-turn, and thread reliability across restarts were
unverified. The Telegram side, allowlist, state-file format, and supervisor
units do not change when the executor is swapped, so the upgrade is
contained to the bridge's execution layer.

## Comparison with deploy-telegram (Claude) architecture

```
Claude Code (deploy-telegram):
  Telegram ──long-poll──▶ Bun server.ts (MCP child of claude)
                              │ stdio (MCP)
                              ▼
                           claude --channels (tmux, systemd)
                              │ model calls `reply` tool to answer

Codex (this skill):
  Telegram ──long-poll──▶ tg-codex-bridge.py (systemd/launchd/Task Scheduler)
                              │ subprocess: codex exec [--json] / resume
                              ▼
                           Codex CLI (one process per turn)
                              │ bridge parses JSONL, sends the reply itself
```

Things the bridge design **gains** over the Claude design:

- **Long-session routing drift disappears by construction.** The Claude
  architecture's worst defect — replies generated in the terminal but never
  delivered because the model forgot to call the reply tool (notably after
  very long sessions) — cannot happen: delivery is the bridge's job, not the
  model's.
- **Slash commands work.** `/new`, `/status`, `/cancel` are implemented
  deterministically by the bridge. On Claude, `/model` sent from Telegram is
  fed to the model as a literal user message and silently does nothing.
- **No tmux / PTY, no trust dialogs, no inbox mover, no server.ts patch.**

Things it **loses / must rebuild**:

- Native channel, model-driven reply tool, long-session compaction semantics,
  AskUserQuestion equivalent.
- Telegram tooling becomes bridge code; chat→thread mapping and allowlist
  are the bridge's job.
- Hooks must be rewritten (payload schema differs); MCP config moves to
  `~/.codex/config.toml`, and Codex is a client only.
- `AGENTS.md` 32 KiB cap: a large `CLAUDE.md` must be split (see
  [agents-md-merge.md](agents-md-merge.md)).

## Dual-track principle

During migration, Claude Code keeps running untouched on every machine.
Codex is an addition, not a replacement. Switching traffic means only
"which group the operator talks in"; rolling back means stopping the Codex
bridge and going back to the Claude group. Nothing on the Claude side is
stopped, deleted, or reconfigured. See the skill's deployment contract in
[../SKILL.md](../SKILL.md) and the rollout order in
[../platforms/](../platforms/) (per-platform guides).
