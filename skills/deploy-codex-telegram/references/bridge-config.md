# Bridge Config — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). See also
> [architecture-and-design.md](architecture-and-design.md) and
> [thread-rotation.md](thread-rotation.md) (rotation thresholds live here
> too, as config keys).

## Files and their homes

| File | Location | Permissions | Contents |
|---|---|---|---|
| `config.json` | `~/.codex/tg/config.json` | readable by the bridge user only (0600 recommended) | All bridge settings, including the bot token field |
| `bridge.env` | `~/.codex/tg/bridge.env` | **0600** | `TELEGRAM_BOT_TOKEN=<token>` (fallback token source) |
| `codex.env` | `~/.codex/tg/codex.env` | **0600** | Codex-side credentials, e.g. `CODEX_API_KEY=<key>` — kept separate from the Telegram token so the two secrets have different blast radii and can be rotated independently |
| `state.json` | `~/.codex/tg/state.json` | bridge user only | Per-chat thread mapping (written by the bridge) |
| `bridge.lock` | `~/.codex/tg/bridge.lock` | — | Single-instance lock (see below) |
| `bridge.log` | `~/.codex/tg/bridge.log` | bridge user only | Bridge log; **contains message text — stays on the machine, never forwarded** |

Token lookup order in the bridge: `config.json` → `bot_token` →
`TELEGRAM_BOT_TOKEN` environment variable → `~/.codex/tg/bridge.env`.
The placeholder value `PASTE_TOKEN_HERE` counts as missing. The bridge
never writes a credential anywhere else, and no credential is ever committed
to git, pasted into chat, or baked into a script.

In Docker deployments the same two env files live in the container's secret
mount (see `scripts/docker/`); the paths inside the container are the same
`~/.codex/tg/` paths.

## `config.json` fields

Template: `scripts/config.json.example` (copy to
`~/.codex/tg/config.json` and edit). Field reference:

| Field | Type | Deployed value | Notes |
|---|---|---|---|
| `bot_token` | string | `PASTE_TOKEN_HERE` in the template — **replace with a fresh BotFather token, one per machine** | Never reuse the Claude bot's token (409 Conflict). May be left as the placeholder if the token is supplied via env / `bridge.env` instead. |
| `allowed_user_ids` | array of ints | **exactly one id — the operator's** | Example placeholder: `[1234567890]`. Replace with the operator's real Telegram user id. The allowlist is the only gate: anyone not listed is ignored silently, no reply, no execution. |
| `allowed_chat_ids` | array of ints | `[]` (empty = any chat the allowed user talks in) | Restrict further when the bot is in a group: list only the intended group chat id(s). |
| `codex_bin` | string | `"codex"` | Full path if `codex` is not on the supervisor's PATH (systemd runs with a minimal PATH — see [post-deploy-hardening.md](post-deploy-hardening.md)). |
| `workdir` | string | `"~"` | Codex working directory; the sandbox is workspace-write relative to it. Keep it away from `~/.ssh`, credential files, and other secret locations. |
| `inbox_dir` | string | `"~/telegram-inbox"` | Where Telegram attachments are downloaded before their path is handed to Codex. |
| `state_file` | string | `"~/.codex/tg/state.json"` | Chat → thread mapping. |
| `log_file` | string | `"~/.codex/tg/bridge.log"` | |
| `lock_file` | string | `"~/.codex/tg/bridge.lock"` | `fcntl.flock` single-instance lock (Unix). On Windows there is no `fcntl`; the singleton is enforced by the Scheduled Task instead — see "Windows differences" below. |
| `codex_timeout_s` | int | **1800** | Watchdog for one `codex exec` run. The shipped template still shows `600` — raise it to `1800` on deploy. 600 killed already-finished long turns (see [post-deploy-hardening.md](post-deploy-hardening.md)). |
| `sandbox` | string | `"workspace-write"` | Passed on the first turn only. Mutually exclusive with `approve_for_me` on Codex 0.169.0/0.170.0 — the bridge sends exactly one. See [sandbox-and-approval.md](sandbox-and-approval.md). |
| `approve_for_me` | bool | `true` | Unattended approval mode; implies the workspace-write sandbox. |
| `poll_timeout_s` | int | `30` | `getUpdates` long-poll timeout. |
| `rotation_session_max_bytes` | int | `1000000` (1 MB) | Rotate when the session file exceeds this. See [thread-rotation.md](thread-rotation.md). |
| `rotation_cumulative_input_max` | int | `5000000` (5M tokens) | Rotate when cumulative input tokens exceed this. Counted only from `token_usage_record` events — see [thread-rotation.md](thread-rotation.md). |

## Allowlist rules

- **One user id only.** The deployed allowlist contains a single operator id.
  Do not add other people: the bridge is a remote shell, and Telegram
  access equals server execution rights.
- Do not make the bot public; do not add it to unrelated groups.
- Group topology (one group per machine: operator + bridge bot, plus the
  operator's own assistant account if one exists) is enforced by
  `allowed_chat_ids`, not by hoping nobody else finds the bot.
- If the bot token ever leaks: revoke it at `@BotFather` (`/revoke`), issue a
  new one, update `bridge.env` / `config.json`, restart the bridge. The
  Codex API key is a separate secret and does not need rotation for a
  Telegram-token leak (and vice versa).

## Windows differences (3 places)

The Windows bridge (`scripts/tg-codex-bridge-winpc.py`) is a fork of
the main bridge with exactly three behavioural differences; everything else
— config keys, commands, rotation, state format — is identical:

1. **`fcntl` guard.** `import fcntl` is wrapped in `try/except ImportError`
   and falls back to `fcntl = None`. The `flock` single-instance lock is
   skipped on Windows; the Scheduled Task (single instance, "do not start a
   new instance if already running") is the singleton instead.
2. **`shell=True` on subprocess calls.** `codex` is resolved through the
   Windows shell (`shell=ON_WINDOWS`); on Unix the bridge spawns it directly.
3. **`stdin=subprocess.DEVNULL` on the Codex subprocess.** Without this, the
   child inherits the bridge's stdin and hangs waiting for input on
   Windows — the bridge appears alive but never answers. See
   [post-deploy-hardening.md](post-deploy-hardening.md).

When fixing a bug, fix it in both files or note explicitly which one the fix
applies to. TODO: the two files have already drifted once (the Windows file
was produced by patching the main file); a future cleanup should extract the
shared core so the fork is mechanical.

## Pre-flight checklist (before first real run)

```bash
python3 tg-codex-bridge.py --dry-run --config ~/.codex/tg/config.json
```

The dry run validates the config, the token source, and the paths without
polling Telegram or spawning Codex. If it fails, fix the config — do not
start the supervisor unit and debug from the journal.
