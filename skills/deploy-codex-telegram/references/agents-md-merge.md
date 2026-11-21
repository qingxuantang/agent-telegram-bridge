# AGENTS.md Merge — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). Companion reference:
> [architecture-and-design.md](architecture-and-design.md) (the 32 KiB cap
> and the skills layout).

## Goal

Carry a machine's Claude Code identity (its `CLAUDE.md` plus its skills) into
Codex, as `~/.codex/AGENTS.md`, **without touching the Claude side at all**.
Claude keeps running in parallel; the merge is copy-and-transform, never
move-and-delete.

## Source lookup

The migration script (`scripts/migrate-claude-to-codex.sh`, dry-run
by default) takes the first `CLAUDE.md` it finds, in this order:

1. `~/.claude/CLAUDE.md`
2. `~/CLAUDE.md`
3. `./.claude/CLAUDE.md`
4. `./CLAUDE.md`

Skills are copied `~/.claude/skills/*` → `~/.agents/skills/*` (and
optionally `<workspace>/.agents/skills/`). Copy only — `~/.claude/` is never
moved, deleted, or edited.

**Never migrated:** credentials of any kind — `.credentials.json`,
`channels/telegram/.env`, `secrets.env`, `auth.json`. The migration script
lists these as explicitly excluded. Hooks are inventoried only, never
auto-converted (Codex hook payload schemas differ — rewrite and retest each
one). MCP servers are inventoried only; Codex config lives in
`~/.codex/config.toml` and Codex is an MCP client only.

## Merge rules

Apply in order; every rule below is a transformation of the *copy*:

1. **Replace the channel-routing section.** Claude's Telegram channel-routing
   rule (reply via the `plugin:telegram:telegram - reply` MCP tool) is
   meaningless under Codex — the bridge delivers replies deterministically.
   Replace it with a short note that Telegram delivery is the bridge's job
   and the model must not attempt any reply-tool call.
2. **Keep `no-interactive-select` verbatim.** The rule forbidding interactive
   selection prompts (the model must not present menus that need a
   keypress) is carried over word-for-word — it matters more, not less, when
   the model is driven non-interactively by a bridge.
3. **Keep `quant-sandbox` verbatim, if present.** On machines that run live
   trading, the quant-sandbox rules (read-only data mounts, never touch a
   running trading process) are copied unchanged. If the source has no such
   section, none is invented.
4. **Insert a Codex note right after the YAML frontmatter.** A short block
   stating: this file is the Codex-side identity merged from `CLAUDE.md`;
   the agent runs under Codex CLI driven by the Telegram bridge; sandbox is
   workspace-write; the model must not assume a terminal user is watching.
5. **Append a Memory supplement at the end.** A section describing the
   memory palace (wing/room/drawer structure, where durable facts live, how
   a new thread finds prior context via the palace index and timeline). The
   supplement's exact shape depends on the palace layout on that machine:
   a flat palace (`MEMORY.md` index + standalone notes, no `/mp init`) gets
   a different supplement from a wing-structured palace — a mismatch means
   the new thread cannot find the memory index at all. See
   [post-deploy-hardening.md](post-deploy-hardening.md) ("flat palace vs
   wing-structure supplement mismatch").
6. **Respect the 32 KiB cap.** `AGENTS.md` has a 32 KiB limit in Codex. If
   the merged file exceeds it, the script warns and the operator must split
   core rules from per-topic skills instead of relying on a truncated file.
   Never silently truncate.

## Verification — before and after

- **md5 the originals before merging, and again after.** The merge must
  leave every source file byte-identical: `md5sum` the source `CLAUDE.md`
  and the skills tree before the run, and re-check after. Any change means
  the script wrote to the wrong place — stop and investigate.
- **Originals stay put.** `~/.claude/` is untouched; `crontab`, `launchd`
  plists, and Windows Scheduled Tasks belonging to Claude are untouched.
- **Dry-run first.** Run the migration script with no flags, read the
  report, then run `--apply`. The report lists what would be copied, what
  would be skipped, and any cap warnings.

## Platform variants (paths differ, rules do not)

| Platform | Claude source | Codex target | Notes |
|---|---|---|---|
| Linux (incl. Docker containers) | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | Root-run machines: home is the root user's home; in containers the paths are inside the container, not the host |
| macOS | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | Same layout as Linux; the bridge runs under launchd (see [process-supervisors.md](process-supervisors.md)) |
| Windows | `%USERPROFILE%\.claude\CLAUDE.md` | `%USERPROFILE%\.codex\AGENTS.md` | Merge scripts are the platform-specific variants under `scripts/windows/`; line endings stay LF in the merged file |
| Docker | host paths do not apply | paths inside the `codex-tg` container | The merge runs inside the container (or on files copied in); the host's `~/.claude` is never mounted into the Codex container |

## What Codex does NOT inherit

The memory palace is **not** automatically inherited. Codex receives only
two things: the merged `AGENTS.md` (plus the appended Memory supplement) and
the copied skills. It does **not** receive Claude's conversation history,
its session rollouts, or its in-memory context. A machine whose palace is
flat (`MEMORY.md` index + standalone notes, never `/mp init`'d) must first be
migrated to the wing structure (`/mp init` + `/mp migrate-from`) before the
supplement can point at it — verified in practice on two machines: 55 memory
units into 4 wings / 8 rooms on one, 50 units into 2 wings / 5 rooms on the
other. The flat source files are kept forever as the migration source and are
never deleted. A new Codex thread finds prior context only through the palace
files the supplement points at; if the supplement is wrong, the thread starts
with no memory and no error.
