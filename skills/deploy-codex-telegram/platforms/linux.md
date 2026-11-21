# Linux — systemd `--user` deploy

Target: Ubuntu 22.04 / 24.04 (Codex 0.169.0 validated). The bridge runs as
a systemd **user** service with a daily 04:00 restart timer. Read the
thin core ([`../SKILL.md`](../SKILL.md)) first for prerequisites, bridge
facts, and the universal verification checklist — this overlay only adds
the Linux-specific steps.

## 0. Decide: local vs remote, root vs non-root

| Mode | When | How the overlay changes |
|---|---|---|
| **Local** | You are already on the target shell | Run the commands below directly. |
| **Remote SSH** | Target is a server you reach by SSH | Prefix every command with an `SSH_CMD` (e.g. `SSH_CMD="ssh <user>@<host>"` and run `$SSH_CMD "<command>"`). Quote carefully: the remote shell sees the command after one round of local expansion, so use single quotes around remote paths containing `$HOME`. Verify `ssh <user>@<host> 'echo ok'` works before starting. |
| **Non-root user (recommended)** | Normal deploy | systemd `--user` units + `loginctl enable-linger <user>` (below). |
| **Root** | Deploying as root | Use **system** units instead (`/etc/systemd/system/`, `systemctl`
without `--user`), and set an explicit `PATH` in the unit that includes
the node install path (see Step 3). Root user units are not used. |

Do not mix modes mid-deploy: pick one row from each column and stick to
it for every step.

## 1. Install Codex

Run the installer on the target (target-side path
`$HOME/codex-telegram/install-codex.sh`):

- Script: [`scripts/install-codex.sh`](../scripts/install-codex.sh)
- It installs the `codex` CLI for the deploy user. Verify afterwards:

```bash
codex --version   # expect 0.169.0 or newer; record the exact version
which codex      # note the full path — the unit file may need it (Step 3)
node --version   # Codex needs node on PATH; note the install location
```

If `codex` was installed via nvm, its real path is under
`$HOME/.nvm/versions/node/<version>/bin/codex` — that path, not the shim,
is what a systemd unit needs.

## 2. Lay out the deploy directory

```
$HOME/codex-telegram/
├── tg-codex-bridge.py          # bridge daemon (Python stdlib only)
├── tg-codex-bridge.config.json # bridge config (see below)
├── bridge.env                  # BOT_TOKEN            — 0600
├── codex.env                   # OPENAI_API_KEY       — 0600
├── install-codex.sh            # installer (Step 1)
└── logs/                       # bridge stdout/stderr (unit redirects here)
```

- `tg-codex-bridge.py`: [`scripts/tg-codex-bridge.py`](../scripts/tg-codex-bridge.py)
- `tg-codex-bridge.config.json`: copy
  [`scripts/config.json.example`](../scripts/config.json.example)
  to `tg-codex-bridge.config.json` and fill in:
  `bot_token_file` → `bridge.env`, `codex_env_file` → `codex.env`,
  `telegram_user_id` → your id (digits, e.g. `1234567890`),
  `codex_timeout_s` → `1800`, rotation thresholds (session file 1 MB,
  cumulative input 5M tokens — see [`../SKILL.md`](../SKILL.md) §Bridge
  facts). Never put the token or key literally in the JSON — always via
  the env files.
- Permissions (non-negotiable):

```bash
chmod 600 $HOME/codex-telegram/bridge.env $HOME/codex-telegram/codex.env
chmod 700 $HOME/codex-telegram
```

## 3. `~/.codex/config.toml` — sandbox mode

```bash
mkdir -p $HOME/.codex
cat > $HOME/.codex/config.toml <<'EOF'
sandbox_mode = "danger-full-access"
EOF
chmod 600 $HOME/.codex/config.toml
```

Why: on Ubuntu 24.04 `apparmor_restrict_unprivileged_userns=1` makes
bubblewrap + loopback fail, so the deploy deliberately runs Codex
unsandboxed (equivalent to Claude's `--dangerously-skip-permissions`).
Deleting this line restores the sandbox. Without it, resume rounds can
silently fall back to read-only and palace recall fails — the failure
mode is silent, so set this before the first test, not after a failure.

## 4. systemd units (non-root: `--user`; root: system units)

**Non-root** — place the two units in `$HOME/.config/systemd/user/`:

- `codex-tg-bridge.service`:
  [`scripts/systemd/codex-tg-bridge.service`](../scripts/systemd/codex-tg-bridge.service)
- `codex-tg-daily-restart.service` +
  `codex-tg-daily-restart.timer`:
  [`scripts/systemd/codex-tg-daily-restart.service`](../scripts/systemd/codex-tg-daily-restart.service),
  [`scripts/systemd/codex-tg-daily-restart.timer`](../scripts/systemd/codex-tg-daily-restart.timer)

The service runs the bridge with an explicit environment, e.g.:

```ini
[Unit]
Description=Codex Telegram bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/codex-telegram
# Explicit PATH: systemd user units do NOT inherit your shell profile.
# Include the node install path (nvm path if applicable) before /usr/bin.
Environment=PATH=/home/<user>/.nvm/versions/node/<version>/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 %h/codex-telegram/tg-codex-bridge.py --config %h/codex-telegram/tg-codex-bridge.config.json
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

The daily restart pair: the timer fires `OnCalendar=*-*-* 04:00:00`
(`Persistent=true`, so a missed 04:00 run fires on next boot) and the
restart service restarts `codex-tg-bridge.service`. Enable linger so the
user units survive logout:

```bash
loginctl enable-linger <user>
systemctl --user daemon-reload
systemctl --user enable --now codex-tg-bridge.service
systemctl --user enable --now codex-tg-daily-restart.timer
systemctl --user list-timers --all | grep codex   # confirm 04:00 is armed
```

**Root** — same unit contents, but installed as **system** units:

```bash
# Place files in /etc/systemd/system/ (not ~/.config/systemd/user/)
systemctl daemon-reload
systemctl enable --now codex-tg-bridge.service
systemctl enable --now codex-tg-daily-restart.timer
```

Root differences to respect: no `--user` flag anywhere, no linger
needed, and the `PATH` line is mandatory — root's systemd environment
is minimal and will not find an nvm node otherwise. The deploy
directory for root is `/root/codex-telegram/`; the 0600 rule for
`bridge.env` / `codex.env` still applies.

## 5. Verify (Linux)

Run the universal five checks from [`../SKILL.md`](../SKILL.md) §Step 2
(`getMe`, first round trip, resume, palace recall, restart survival).
Linux-specific confirmations:

```bash
systemctl --user status codex-tg-bridge.service   # or systemctl status (root)
journalctl --user -u codex-tg-bridge.service -f   # watch a live turn
systemctl --user list-timers --all | grep codex-tg-daily-restart
```

## Troubleshooting (Linux)

| Symptom | Likely cause → fix |
|---|---|
| `codex: command not found` in the unit, works in shell | systemd PATH lacks the node/nvm path. Add the full nvm bin dir to `Environment=PATH=` in the unit (Step 4), `daemon-reload`, restart. |
| Bridge starts, first turn never replies | Check `journalctl`: usually a bad token (401 — regenerate via `@BotFather`) or a 409 (another poller holds the token — fresh bot). |
| Resume behaves like a fresh thread | Thread id not passed to `codex exec resume` — bridge/config issue, not a Codex issue. Verify the bridge passes an explicit thread id (see [`../SKILL.md`](../SKILL.md) §Bridge facts). |
| Palace recall fails, turns work | Missing or wrong `~/.codex/config.toml` (Step 3) — resume rounds fell back to read-only. Create the file, restart the bridge. |
| Timer never fires after reboot | Linger not enabled (non-root): `loginctl enable-linger <user>`, then re-enable the timer. |
| Bridge dies on logout (non-root) | Same as above — linger. `--user` services without linger are stopped at logout. |
