# Post-Deploy Hardening — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). Each item is a production incident in the
> same format: **symptom → root cause → fix → how to verify**. The fixes
> below are already in the shipped scripts — this file is the record
> of *why*, so nobody "simplifies" them away.

## 1. Bubblewrap missing → sandbox cannot start

- **Symptom.** Codex turns fail before the model runs; the bridge log shows
  a sandbox/bwrap startup error. The bot may still answer Telegram
  (the bridge is alive), but no work ever happens.
- **Root cause.** The container image was built without `bubblewrap`
  installed, and the sandbox path needs `bwrap`. On bare Ubuntu 24.04 the
  sibling cause is `apparmor_restrict_unprivileged_userns=1` blocking the
  unprivileged user namespace `bwrap` needs.
- **Fix.** Rebuild the image with bubblewrap installed (verified version
  0.8.0 in the image that fixed it). On bare metal, the operator's decision
  was instead `sandbox_mode = "danger-full-access"` in `config.toml` — see
  [sandbox-and-approval.md](sandbox-and-approval.md).
- **Verify.** Run one turn that writes inside the workdir (must succeed) and
  one that writes outside it (must be blocked *and* logged, if the sandbox
  is on; must succeed only if `danger-full-access` is the intended mode).

## 2. Telegram 409 Conflict — stale bridge process still polling

- **Symptom.** `409 Conflict: terminated by other getUpdates request` in the
  bridge log; the bot answers intermittently or not at all.
- **Root cause.** An old bridge process was still alive and polling with the
  same bot token — either a previous deploy was never stopped, or the bot
  token was reused from the Claude deployment so two pollers (Claude's Bun
  server and the Codex bridge) fought over `getUpdates`.
- **Fix.** One bot token per machine, never reused; before starting the new
  bridge, kill every previous bridge/bot process (`pkill -f
  tg-codex-bridge` on Unix; stop the old Scheduled Task on Windows) and
  confirm no process holds the token. The single-instance lock
  (`bridge.lock`, `fcntl.flock`) prevents a second bridge on the *same*
  machine, but it cannot stop a poller on a *different* machine — only token
  hygiene does that.
- **Verify.** `getUpdates` returns cleanly for several minutes with no 409;
  exactly one bridge process is running.

## 3. WinPC stdin hang

- **Symptom.** The Windows bridge starts, logs look normal, but the bot
  never answers; no Codex process ever appears to finish.
- **Root cause.** On Windows the `codex` child inherited the bridge's stdin
  and blocked waiting for input.
- **Fix.** The Windows bridge passes `stdin=subprocess.DEVNULL` on the
  Codex subprocess (and `shell=True` — see
  [bridge-config.md](bridge-config.md)). This is one of the three
  Windows-only differences; do not "unify" it away.
- **Verify.** Send a message; a `codex` child spawns and exits, and the
  reply arrives. If the bridge is alive but no child ever exits, check
  stdin handling first.

## 4. `run.sh` palace check hangs without passwordless sudo

- **Symptom.** A container (re)start hangs indefinitely at the palace-check
  step of `run.sh`; no bridge, no error, no timeout.
- **Root cause.** The check invoked a command requiring `sudo` and the
  machine had no passwordless sudo configured, so `sudo` waited forever for
  a password on a non-interactive stdin.
- **Fix.** Make the check non-blocking: either grant the specific command
  passwordless sudo, or (preferred) rewrite the check to not require sudo at
  all, with a hard timeout so a stuck check fails loudly instead of hanging
  the deploy.
- **Verify.** Run `run.sh` from a non-interactive shell with stdin closed;
  it must complete or fail within seconds, never hang.

## 5. Flat palace vs wing-structure supplement mismatch

- **Symptom.** The Codex bot answers, but it cannot find the memory index —
  it behaves as if it has no memory, and no error is raised.
- **Root cause.** The machine's palace was still flat (`MEMORY.md` index +
  standalone notes, never `/mp init`'d), but the merged `AGENTS.md` had a
  Memory supplement written for the wing structure (wings/rooms/drawers).
  The supplement pointed at paths that did not exist.
- **Fix.** Migrate the palace first (`/mp init` + `/mp migrate-from`),
  *then* write the supplement against the real layout. Verified twice: 55
  units into 4 wings / 8 rooms on one machine, 50 units into 2 wings / 5
  rooms on another. Keep the flat source files forever as the migration
  source — never delete them. See
  [agents-md-merge.md](agents-md-merge.md).
- **Verify.** A fresh thread asked about a known durable fact must answer
  from the palace (and cite the palace file). If it cannot, the supplement
  is wrong — do not blame the model.

## 6. `token_usage_record` miscount

- **Symptom.** Threads rotated far too early; `/status` showed implausibly
  large cumulative input totals.
- **Root cause.** Token counting grepped the raw JSONL for `input_tokens`,
  which also appears in other event types (per-message snapshots, cached
  breakdowns, nested objects) and was counted multiple times.
- **Fix.** Sum input tokens **only** from events whose `type` is exactly
  `token_usage_record`. See [thread-rotation.md](thread-rotation.md).
- **Verify.** Hand-check the count against a known session file before
  deploying any change to the counting code.

## 7. 600 s timeout killed already-finished turns

- **Symptom.** Long turns that had actually completed (the model had
  finished and the output was ready) were reported as timed out; the user
  got a timeout message instead of the answer.
- **Root cause.** The bridge's own watchdog (`codex_timeout_s = 600`) fired
  while the bridge was still draining/parsing the child's output — the turn
  was done, the watchdog killed the delivery anyway.
- **Fix.** Raise the watchdog to **1800 s**. The shipped config template
  still shows 600 — raise it on deploy (see
  [bridge-config.md](bridge-config.md)). Do not "fix" this by shortening
  turns; the timeout must exceed the longest legitimate turn plus drain time.
- **Verify.** Run a deliberately slow turn (large read / long synthesis)
  and confirm the reply arrives instead of a timeout notice.

## 8. `PATH` missing node under systemd

- **Symptom.** The bridge starts under systemd but every Codex spawn fails
  with `codex: command not found` (or `node: command not found`), while the
  same command works in an interactive SSH shell.
- **Root cause.** systemd units run with a minimal `PATH` that does not
  include the operator's Node/npm install location (e.g. an nvm or
  user-local bin directory).
- **Fix.** Set `Environment=PATH=…` explicitly in the systemd unit, with the
  full path that contains `node` and `codex`. Alternatively use an absolute
  `codex_bin` in `config.json`.
- **Verify.** `systemctl --user show-environment` / the unit's
  `Environment=` line lists the directory; restart the unit and confirm a
  turn runs. Never "verify" this from an interactive shell — the bug only
  exists in the supervisor's environment.
