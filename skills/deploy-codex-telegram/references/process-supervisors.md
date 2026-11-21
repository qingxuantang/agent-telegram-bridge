# Process Supervisors — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). Per-platform setup lives in
> `../platforms/` (Linux/systemd, macOS/launchd, Windows/Task Scheduler,
> Docker). The bridge itself needs no tmux or PTY — it is a plain
> long-running process, `Type=simple` wherever the platform has the concept.

## Comparison

| | systemd | launchd | Task Scheduler | Docker restart |
|---|---|---|---|---|
| **Platform** | Linux (incl. Ubuntu 22.04/24.04 servers) | macOS | Windows 11 | Any host running the `codex-tg` container |
| **Unit files** | `codex-tg-bridge.service` (+ `codex-tg-daily-restart.service` / `.timer`) in `~/.config/systemd/user/` | `com.codex.telegram-bridge.plist` (+ restart plist) in `~/Library/LaunchAgents/` | Scheduled tasks created by `scripts/windows/create-codex-tasks.ps1` | `run.sh` with `--restart unless-stopped` |
| **Scope** | `--user` units for normal users; **system** units for root-run machines | User agents (`LaunchAgents`) | User logon tasks | Container |
| **Start** | `systemctl --user enable --now codex-tg-bridge.service` (after `loginctl enable-linger "$USER"`, otherwise user services are killed on logout) | `launchctl load` the plist; a wrapper `.sh` sets up env and starts the bridge | `AtLogOn` trigger, hidden wrapper script (`winpc-run-bridge.ps1`) | `docker start` / container start via `run.sh` |
| **Restart on crash** | `Restart=always` in the unit | `KeepAlive` in the plist | `RestartCount 999` on the task | `--restart unless-stopped` (Docker restarts the container, not the process inside) |
| **Daily restart** | `.timer` firing **04:00 daily** (mirrors the Claude palace pattern), oneshot service runs `systemctl --user restart codex-tg-bridge.service` | Separate restart plist firing 04:00 daily | Separate daily 04:00 restart task | **None — there is no daily scheduled restart inside a container.** This is a known limitation; see below |
| **Logs** | `journalctl --user -u codex-tg-bridge.service -f` (+ `~/.codex/tg/bridge.log`) | `Console.app` / `log show`, plus the bridge's own log file | Task Scheduler history + `winpc-restart-bridge.ps1` output + bridge log | `docker logs codex-tg` (+ bridge log inside the container) |
| **Known pitfalls** | systemd runs with a **minimal PATH** — `node`/`codex` may not resolve; set `Environment=PATH=…` explicitly in the unit. Forgetting `enable-linger` kills the bridge on logout. | A missing `~/.codex/config.toml` silently leaves Codex read-only (see [sandbox-and-approval.md](sandbox-and-approval.md)). Wrapper script must be executable and use absolute paths. | Without `stdin=DEVNULL` the Codex child hangs (see [post-deploy-hardening.md](post-deploy-hardening.md)). NordVPN / other VPNs can block the Tailscale path used to reach the machine — not a bridge fault, but it looks like one. | No daily restart means thread state can grow unbounded until someone restarts the container; rotation (see [thread-rotation.md](thread-rotation.md)) is the only bound. Rebuilding the image is the only way to change the bridge code. |

## Notes per supervisor

### systemd (`--user`)

- `loginctl enable-linger "$USER"` is a prerequisite, not an optimisation:
  without it, `--user` services are stopped when the user logs out, and the
  bridge silently dies after the deploy SSH session closes.
- Root-run machines use **system** units instead (`/etc/systemd/system/`),
  with the same timer. Do not mix scopes on one machine.
- The daily 04:00 timer is the palace pattern carried over from the Claude
  deployment: a fresh bridge process (and therefore a fresh long-poll) every
  day bounds the damage from any slow state leak.

### launchd

- The plist launches a wrapper shell script, which sets `PATH` and any proxy
  variables, then execs the bridge. Keep the wrapper minimal and absolute —
  launchd's environment is nearly empty.
- `KeepAlive` restarts the bridge on crash; the separate restart plist
  handles the daily 04:00 bounce.

### Task Scheduler

- Two tasks: the bridge itself (`AtLogOn`, hidden, `RestartCount 999`) and a
  daily 04:00 restart task.
- The wrapper (`winpc-run-bridge.ps1`) must run hidden and must not open a
  visible console, or the operator gets a flashing window on every logon.
- The Windows bridge is a separate file (`tg-codex-bridge-winpc.py`) — see
  [bridge-config.md](bridge-config.md) for the three differences.

### Docker

- The container is built from the `codex-tg` image and started by `run.sh`
  with `--restart unless-stopped`. There is **no in-container cron and no
  daily restart timer** — the container restarts only on crash or host
  reboot.
- Practical consequences: (1) long threads are bounded only by rotation;
  (2) bridge code changes require an image rebuild + container recreate, not
  a file edit; (3) the host's `run.sh` / `secrets.env` pattern is reused for
  secrets, and live trading data is mounted **read-only** — never mount a
  trading process or writable trading directory into the Codex container.
- TODO (known limitation): add a host-side daily restart (a host cron that
  runs `docker restart codex-tg` at 04:00) or document explicitly that the
  container intentionally has no daily bounce. Do not add a cron inside the
  image without a design note — it changes the image's failure modes.
