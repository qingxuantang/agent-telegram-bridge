# Troubleshooting — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). Find the symptom, follow the checks in
> order, and only then apply the fix the checks point at. Items marked
> **(on target machine)** need the operator (or an SSH session) on the
> machine that runs the bridge — they cannot be diagnosed from the
> controller side.

## Bot does not respond at all

1. Is the bridge process running? **(on target machine)**
   `systemctl --user status codex-tg-bridge` (Linux),
   `launchctl list | grep codex` (macOS),
   Task Scheduler → check the bridge task state (Windows),
   `docker ps` / `docker logs codex-tg` (container).
2. Is there a 409 in the bridge log? → Two pollers share one token. See
   [post-deploy-hardening.md](post-deploy-hardening.md) §2: kill stale
   bridge processes, use a fresh per-machine token.
3. Is the token valid? Send a test `getMe` with the token from
   `bridge.env`. A revoked/typo'd token fails here, not in the bridge.
4. Is the allowlist right? The sender's user id must be in
   `allowed_user_ids` and the chat in `allowed_chat_ids` (if restricted).
   The bridge ignores non-listed senders *silently* — no error anywhere.
5. Network: can the machine reach `api.telegram.org`? **(on target
   machine)** `curl -s https://api.telegram.org`. Mainland-China networks
   are blocked; a proxy/VPN may be required (and on Windows, a VPN can
   block the Tailscale path used to reach the machine — check that first).

## Bot answers but cannot do anything on disk (read-only)

1. Does `~/.codex/config.toml` exist? **(on target machine)** If it is
   missing, Codex falls back to a read-only sandbox. Create it with the
   intended `sandbox_mode`. See
   [sandbox-and-approval.md](sandbox-and-approval.md).
2. If the sandbox is intentionally on, is the failure a *legitimate* block
   (write outside the workdir)? Check the bridge/Codex log for the denied
   path, then either move the work inside `workdir` or revisit the
   `danger-full-access` decision — do not silently widen the workdir.

## Memory index not found / bot has no memory

1. Is the palace actually wing-structured, or still flat? **(on target
   machine)** Check for `wings/` under the palace root.
2. Does the Memory supplement in `~/.codex/AGENTS.md` match the real
   layout? A wing-structure supplement on a flat palace points at
   nonexistent paths. See [post-deploy-hardening.md](post-deploy-hardening.md)
   §5 and [agents-md-merge.md](agents-md-merge.md).
3. Was the palace migrated (`/mp init` + `/mp migrate-from`)? If not,
   migrate it, re-run the AGENTS.md merge, and restart the bridge.
   A fresh thread asked about a known durable fact is the verification.

## Resume opens a new thread instead of continuing

1. Did the previous thread actually exist? Check `state.json` for the chat's
   thread id and `~/.codex/sessions/` for its rollout. A missing rollout
   means the thread is gone — a fresh thread is the correct fallback.
2. Was `/new` sent? `/new` deliberately drops the mapping without archiving.
3. Did rotation just happen? A fresh thread after rotation is expected —
   check the `🔄` notice and the palace drawer (see
   [thread-rotation.md](thread-rotation.md)).
4. **Never** pass `--approve-for-me` / `-s` / `-C` on a resume command —
   resume rejects them and the turn fails. The bridge already handles this;
   if you invoked `codex exec resume` by hand, drop those flags.

## Sandbox errors / Codex will not start

1. Container: is `bubblewrap` installed in the image? Rebuild with it
   (see [post-deploy-hardening.md](post-deploy-hardening.md) §1).
2. Bare Ubuntu 24.04: `apparmor_restrict_unprivileged_userns=1` blocks
   `bwrap`. Either fix the host setting or use the documented
   `danger-full-access` decision — see
   [sandbox-and-approval.md](sandbox-and-approval.md).
3. `--approve-for-me` and `--sandbox` passed together → clap error.
   Pass exactly one. Re-check flags against the target machine's own
   `codex exec --help`.

## 409 Conflict

See "Bot does not respond" step 2 and
[post-deploy-hardening.md](post-deploy-hardening.md) §2. The fix is always
token/process hygiene, never a bridge code change.

## Timeout on long turns

1. Is `codex_timeout_s` 1800? The shipped template still shows 600 — raise
   it (see [bridge-config.md](bridge-config.md) and
   [post-deploy-hardening.md](post-deploy-hardening.md) §7).
2. Is the turn genuinely still running, or did the watchdog kill a finished
   turn during output drain? Check whether the Codex child had already
   exited when the timeout fired.

## Proxy / egress blocked

1. **(on target machine)** `curl -s https://api.openai.com`,
   `https://chatgpt.com`, `https://api.telegram.org`,
   `https://github.com`, `https://registry.npmjs.org` — each must answer.
2. If the machine needs a proxy (e.g. a local clash-style proxy on
   `:7890`), confirm the bridge's environment carries it: systemd units
   need it in `Environment=`; launchd needs it in the wrapper script;
   Docker needs it in the container env. The bridge itself does no proxy
   autodetection.
3. If the machine is reached over Tailscale, confirm Tailscale is up — a
   VPN in TUN mode on the same machine can take the route down, which looks
   exactly like "the bridge is dead" from the outside.

## Checks that need the operator on the target machine

Anything that reads a local file, runs a local command, or restarts a local
service: bridge process state, journal/logs, `config.toml` presence,
palace layout, `curl` egress probes, `codex exec --help` flag checks, and
all supervisor restarts. If you are the controller agent without SSH to the
target, report the exact check (copy the command from above) and wait for
the result — do not guess the machine's state from the Telegram side alone.
