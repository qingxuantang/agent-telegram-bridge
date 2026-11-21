---
name: deploy-codex-telegram
description: Deploy Codex on a Linux / macOS / Windows / Docker host and connect it to Telegram via the tg-codex-bridge daemon. Thin core: detects the target platform and routes to the matching overlay under platforms/. Codex has no --channels and no official Telegram plugin, so the bridge daemon owns Telegram and drives `codex exec` per message.
disable-model-invocation: false
---

# Agent Runbook: Deploy Codex + Telegram Bridge

This skill deploys a long-running Codex instance ("the bridge") that listens
for Telegram messages and replies through the same channel. Unlike the
Claude deployment (`../deploy-telegram/`), **Codex cannot be a Telegram
channel itself**: there is no `--channels` flag and no official Telegram
plugin (`codex mcp-server` was removed in 0.154.0). The bridge daemon owns
Telegram and drives `codex exec` once per incoming message.

## Architecture (universal)

```
┌────────────────────┐
│ Telegram (Phone)   │
└─────────┬──────────┘
          │ Bot API long-poll, getUpdates (HTTPS)
          v
┌────────────────────┐
│ tg-codex-bridge.py │  Python stdlib only; one allowlisted Telegram user id
│  bridge v0.2.0-    │  polls getUpdates, spawns `codex exec` per message,
│  rotation          │  returns the reply to the same chat
└─────────┬──────────┘
          │ `codex exec [--json] --skip-git-repo-check [--approve-for-me]`
          │ resume: `codex exec resume <thread-id> --json ...` (prompt via stdin)
          v
┌────────────────────┐
│ codex exec         │  One process per turn; thread persists via thread id.
│  (per-turn)        │  Memory palace (AGENTS.md + skills + wings/rooms)
└─────────┬──────────┘  is read from the shared workspace on every turn.
          │ filesystem, shell, skills
          v
┌────────────────────┐
│ Host OS            │  Platform supervisor keeps the bridge alive across
│  + supervisor      │  reboots and crashes; daily 04:00 restart (except
└────────────────────┘  Docker — see platforms/docker.md)
```

## Required input (universal — collect before starting)

| Parameter | Format | How to obtain |
|---|---|---|
| `BOT_TOKEN` | `<digits>:<hash>` (e.g. `123456789:AAH...`) | Create via Telegram `@BotFather /newbot`. **Must be a fresh bot**, never one already in use by another long-poller. One bot, one bridge, one machine — two `getUpdates` consumers on the same token → HTTP 409 Conflict. |
| `OPENAI_API_KEY` | Service-account API key (per OpenAI official docs) | Created in the OpenAI dashboard as a **service account** key. Goes in `codex.env` (0600). **Do not use `codex login`** (interactive OAuth) for a daemon. |
| `TELEGRAM_USER_ID` | digits (e.g. `1234567890`) | The operator's Telegram user id (via `@userinfobot`). The bridge allowlist holds **exactly one** id; everyone else is ignored. |
| Target-machine access | SSH / local shell / Docker host | How the deployer reaches the target: local terminal, SSH (key-based), or a Docker host shell. The platform overlay must match the **target**, not the controller machine. |

Also confirm before starting: the target already runs (or will run) Claude
alongside Codex — see "Deployment safety" below. The bridge never replaces
Claude; it is a second, parallel track.

## Bridge facts the overlays rely on (do not re-derive per platform)

- **Bridge version:** v0.2.0-rotation, Python stdlib only
  (`urllib`, `json`, `subprocess`, `threading`, `queue`, `logging`,
  `fcntl` where available). No third-party packages to install.
- **Polling:** Telegram `getUpdates` long-poll. `codex_timeout_s: 1800`
  (30 minutes) per turn; a turn that exceeds it is killed and reported.
- **Rotation (thread rollover):** a thread rotates when its session file
  exceeds **1 MB** or cumulative input tokens exceed **5M**. Cumulative
  input is summed **only from `token_usage_record` events** — grepping
  `input_tokens` directly over the session file over-counts badly.
  On rotation the old thread runs one archive turn (the model writes
  durable facts into the memory palace `wings/`/`rooms/` drawers plus the
  timeline pointer), then a new thread starts and the reply is prefixed
  with 🔄. If archiving fails, **do not rotate** — retry next round.
  `/new` discards the thread **without** archiving. `/status` shows thread
  size, token counts, and thresholds.
- **Flag facts (hard-won, do not guess):**
  - There is **no `--ask-for-approval`** flag. Unattended runs use
    **`--approve-for-me`**.
  - `--sandbox` **conflicts** with `--approve-for-me` (clap error; the
    latter already implies the `workspace-write` sandbox). Pass exactly
    one of them.
  - `codex exec resume` accepts `--json`, `--skip-git-repo-check`, `-c`,
    `-i`, `-m`, `-o`. It does **not** accept `-s`, `-C`, or
    `--approve-for-me`.
  - `codex exec resume` **without an explicit thread id opens a new
    thread** — it does not continue the previous one. Always pass the id.
  - `resume` reads its prompt **from stdin** — pipe the prompt in.
- **Model:** servers and macOS default to `gpt-6.1-sol`; the bridge does
  **not** pass `-m` by default. On one Windows host (Codex 0.170.0) the
  resolved model was observed as `gpt-5.6-luna` / `gpt-6.2-sol`,
  inconsistent with the servers — whether to pin `-m` is **open (TODO)**,
  do not assert a pinning policy.
- **Auth files (both 0600, never committed, never pasted into chat):**
  `bridge.env` holds the bot token; `codex.env` holds the OpenAI
  service-account key.
- **Sandbox:** `~/.codex/config.toml` must contain
  `sandbox_mode = "danger-full-access"`. Background: on Ubuntu 24.04,
  `apparmor_restrict_unprivileged_userns=1` makes bubblewrap + loopback
  fail, so the deployment deliberately chooses option B (no sandbox
  isolation; Codex shell runs as the user, equivalent to Claude's
  `--dangerously-skip-permissions`). Deleting that line restores the
  sandbox. On macOS the same line also fixes a resume-round read-only
  regression (see `platforms/macos.md`).
- **Future direction (not this deploy):** `codex app-server` (JSON-RPC,
  experimental) may one day replace one-process-per-turn `codex exec`.
  Do not build on it yet.

## Deployment safety (universal — read before any overlay)

1. **Additive only, Claude runs in parallel (dual track).** Never stop,
   replace, or reconfigure a running Claude daemon to make room for
   Codex. Both read and write the same business data and the same memory
   palace; only the scheduled-task ownership stays separate (cron runs
   the scripts themselves, through neither agent).
2. **Pilot on a non-critical machine first.** Do not debut on a host
   running live trading, a production website, or protected data.
3. **Never touch a running trading process, production site, or
   protected-data mount.** In Docker, live data mounts are `:ro` only;
   the palace bind-mount is the single shared writable mount.
4. **No secrets in git, chat, or files outside the 0600 env files.**
   Tokens/keys live only in `bridge.env` / `codex.env` on the target
   machine. Never `git push` a deploy directory containing them.
5. **Do not deploy from a network where Telegram / OpenAI / GitHub are
   blocked** unless the platform overlay documents a working proxy/VPN
   path for that host (macOS and Docker do; Windows direct-connect hosts
   need none).

## Execution

### Step 0 — Detect the target platform and route

```bash
case "$(uname -s)" in
    Linux*)    PLATFORM=linux   ;;
    Darwin*)   PLATFORM=macos   ;;
    CYGWIN*|MINGW*|MSYS*) PLATFORM=windows ;;
    *)         PLATFORM=unknown ;;
esac
# Containerised Linux host (Codex runs in Docker, Claude already does):
# PLATFORM=docker — read platforms/docker.md instead of platforms/linux.md.
```

If the controller agent is on a different machine than the target,
`PLATFORM` must reflect the **target**, not the controller. The Linux
overlay documents remote-SSH vs local modes explicitly.

### Step 1 — Read the platform overlay and execute its steps

| If `PLATFORM=` | Read and execute |
|---|---|
| `linux` | [`platforms/linux.md`](./platforms/linux.md) — systemd `--user` units + daily 04:00 restart; remote SSH and local modes; root vs non-root |
| `macos` | [`platforms/macos.md`](./platforms/macos.md) — launchd wrapper (two plists + two shell scripts); PATH, proxy/VPN, and read-only traps |
| `windows` | [`platforms/windows.md`](./platforms/windows.md) — Task Scheduler (AtLogOn + hidden PowerShell wrapper, RestartCount 999; daily 04:00 restart) |
| `docker` | [`platforms/docker.md`](./platforms/docker.md) — standalone `codex-tg` container modelled on `claude-tg`; resource caps, mounts, bubblewrap, proxy env |

Each overlay is **self-sufficient for execution** — once you have picked
your platform, you do not need to bounce back to this SKILL.md for command
details. Cross-references inside an overlay point to the other overlays or
to `references/` for the *why* behind a decision.

### Step 2 — Universal post-deploy verification

Regardless of platform, the deploy is successful only when **all five**
are true:

1. **`getMe` returns `"ok":true`.**
   `curl "https://api.telegram.org/bot<TOKEN>/getMe"` — proves the token
   is valid and no other poller holds it (a 409 here means a second
   consumer; create a fresh bot, do not fight over the token).
2. **First round trip.** From the allowlisted Telegram account, send a
   short message (e.g. `ping`); the bridge log shows the turn starting,
   `codex exec` runs, and the phone receives a reply. Reply latency of
   tens of seconds on the first turn is normal (cold Codex start).
3. **Resume works.** Send a second message that refers to the first
   (`刚才我说的是什么？` / `what did I just say?`). The reply must show
   the same thread continued — i.e. `codex exec resume <thread-id>`
   actually resumed. A reply that behaves like a fresh conversation
   means the thread id was not passed (see flag facts above).
4. **Palace recall works.** Ask something only the memory palace knows
   (a fact previously written to `MEMORY.md` / a wing drawer, e.g. a
   test word planted in the palace before deploy). The reply must
   reproduce it. If recall fails while turns 1–3 pass, the usual cause
   is the sandbox/read-only regression (missing `config.toml`) or, on
   Docker, a missing bubblewrap in the image — see the platform overlay.
5. **Survives a restart.** Restart the bridge (or reboot the host where
   safe) and repeat check 2. The supervisor must bring the bridge back
   without manual intervention, and the thread must resume (not restart
   from scratch). Also confirm the daily 04:00 restart is armed
   (`systemctl --user list-timers`, `launchctl list`, Task Scheduler,
   or `--restart unless-stopped` on Docker — Docker has no daily timer,
   which is a known limitation, see `platforms/docker.md`).

If any check fails, fix it on the platform overlay's troubleshooting
section before declaring the deploy done. Do not declare success on
checks 1–2 alone.

## Compatibility

| | Linux | macOS | Windows | Docker |
|---|---|---|---|---|
| Tested versions | Ubuntu 22.04 / 24.04 | macOS 15 (Sequoia), Apple Silicon | Windows 11 | Ubuntu 22.04-based image |
| Codex tested | 0.169.0 | 0.169.0 | 0.170.0 | 0.170.0 |
| Process supervisor | systemd `--user` (+ timer) | launchd (2 plists + 2 sh) | Task Scheduler | Docker `--restart unless-stopped` |
| Daily 04:00 restart | yes (timer) | yes (restart plist) | yes (scheduled task) | **no — known limitation** |
| Shell | bash | bash / zsh | PowerShell 5.1 | bash (in container) |

Validated end to end on 9 machines: 4 Linux servers, 1 Mac, 1 Windows
host, and 3 containerised hosts (2 Linux Docker hosts + 1 Windows
WSL2 + Docker host). Machine names, bot usernames, and host details are
deliberately not recorded in this skill — see the desensitisation note
in `references/` if one exists, otherwise treat every identifier as
per-deploy input, not skill content.

## Scripts in this skill

Platform overlays cite bundled files by skill-relative path
(`scripts/<path>`, from the skill root). Shared scripts (the bridge,
the installer, the merge script, the config example) live at the
`scripts/` top level; platform-specific files live under
`scripts/systemd/`, `scripts/launchd/`, `scripts/windows/`, and
`scripts/docker/`. All cited files are present in this skill. Do not
invent a script's contents from a citation alone, and do not claim a
`scripts/...` file exists until you have listed it.
