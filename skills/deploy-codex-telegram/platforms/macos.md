# macOS — launchd wrapper deploy

Target: macOS 15 (Sequoia), Apple Silicon (Codex 0.169.0 validated).
The bridge runs under launchd via a **wrapper pattern**: two plists +
two shell scripts. There is no systemd and no Task Scheduler — do not
try to transplant the Linux or Windows overlay. Read the thin core
([`../SKILL.md`](../SKILL.md)) first for prerequisites, bridge facts,
and the universal verification checklist.

## 0. Assumptions

- You are executing **locally on the Mac** (self-execution). Remote
  deployment to a Mac is via SSH from a Linux-style controller using the
  Linux overlay's `SSH_CMD` mode — the target paths below are still the
  Mac paths.
- The Mac already runs the Claude Telegram daemon in parallel (dual
  track). Do not stop, replace, or reconfigure it.
- `codex` is installed (via the installer in Step 1) and the Mac can
  reach `api.telegram.org` and `api.openai.com` — see Step 4, which is
  the step most likely to fail on this platform.

## 1. Install Codex and lay out the deploy directory

```bash
# Installer (target-side $HOME/codex-telegram/install-codex.sh):
# scripts/install-codex.sh — bundled in this skill.
codex --version   # expect 0.169.0 or newer; record the exact version
```

Directory layout (same as Linux):

```
$HOME/codex-telegram/
├── tg-codex-bridge.py           # bridge daemon (Python stdlib only)
├── tg-codex-bridge.config.json  # copy of tg-codex-bridge.config.json.example, filled in
├── bridge.env                   # BOT_TOKEN   — 0600
├── codex.env                    # OPENAI_API_KEY — 0600
└── logs/
```

- `tg-codex-bridge.py`:
  [`scripts/tg-codex-bridge.py`](../scripts/tg-codex-bridge.py)
- Config: copy the `.example` file, set `bot_token_file` →
  `bridge.env`, `codex_env_file` → `codex.env`, `telegram_user_id` →
  your id (digits, e.g. `1234567890`), `codex_timeout_s` → `1800`.
  Never put the token or key literally in the JSON.

```bash
chmod 600 $HOME/codex-telegram/bridge.env $HOME/codex-telegram/codex.env
chmod 700 $HOME/codex-telegram
```

## 2. launchd wrapper — two plists + two shell scripts

launchd runs the bridge directly, but the bridge needs a fully-built
environment (PATH, proxy) that launchd does not provide — hence a
wrapper shell script per job. Four files, all in the skill as
`scripts/launchd/` citations:

| File | Installs to | Purpose |
|---|---|---|
| `com.codex.telegram-bridge.plist` | `$HOME/Library/LaunchAgents/` | Main job: `RunAtLoad` + `KeepAlive`, runs the run script, logs to `StandardOutPath` / `StandardErrorPath` under `$HOME/codex-telegram/logs/` |
| `mac-run-bridge.sh` | `$HOME/codex-telegram/` | Wrapper: sets PATH + proxy env (Step 3–4), then `exec python3 tg-codex-bridge.py --config ...` |
| `com.codex.telegram-bridge-restart.plist` | `$HOME/Library/LaunchAgents/` | Daily 04:00 job: `StartCalendarInterval` hour 4, minute 0 |
| `mac-restart-bridge.sh` | `$HOME/codex-telegram/` | Restart script: `launchctl stop` / start cycle for the main job (or `kill` + launchd `KeepAlive` revival) |

```bash
chmod +x $HOME/codex-telegram/mac-run-bridge.sh $HOME/codex-telegram/mac-restart-bridge.sh
launchctl load $HOME/Library/LaunchAgents/com.codex.telegram-bridge.plist
launchctl load $HOME/Library/LaunchAgents/com.codex.telegram-bridge-restart.plist
launchctl list | grep codex   # both jobs present, exit code 0
```

Do not inline the bridge command in the plist's `ProgramArguments` —
the wrapper script is where environment is fixed, and it is the only
place you should have to edit when PATH or proxy changes.

## 3. The PATH trap (SSH non-interactive shells)

This is the most common macOS failure. An SSH non-interactive shell
does **not** load your profile, so its PATH lacks the directories where
the tools actually live:

| Tool | Location on this platform |
|---|---|
| node | `/usr/local/bin` |
| brew | `/opt/homebrew/bin` |
| claude | `$HOME/.npm-global/bin` |
| python3 | **system Python 3.9.6** (`/usr/bin/python3`) — old; works for the stdlib-only bridge, but do not assume a newer python3 exists |
| codex | wherever the installer put it — verify with `which codex` in an interactive shell |

The wrapper script must set PATH explicitly, e.g.:

```bash
export PATH=/usr/local/bin:/opt/homebrew/bin:$HOME/.npm-global/bin:$HOME/.bun/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

Verify the trap is closed before loading the plist: run the wrapper
script from a **bare SSH non-interactive shell**
(`ssh <user>@<host> '$HOME/codex-telegram/mac-run-bridge.sh'`) and
confirm `which codex`, `which node`, `which python3` all resolve inside
that invocation. If it works interactively but not from launchd/SSH,
it is always PATH.

## 4. Proxy / VPN and connectivity

- `api.telegram.org` and `api.openai.com` **do not connect directly**
  from this host's default network. The bridge needs a working
  proxy/VPN in its environment (export the proxy variables in the
  wrapper script, Step 2 — e.g. `HTTP_PROXY` / `HTTPS_PROXY` /
  `NO_PROXY` — or run under the VPN that is known to work on this Mac).
  Verify from the wrapper's environment, not from your interactive
  shell:

```bash
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://api.telegram.org
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://api.openai.com
```

  Both must return a 2xx/4xx HTTP status (not a timeout / connection
  failure). A 404 from `api.openai.com` still proves connectivity.

- **npmrc trap:** `~/.npmrc` may point at a dead local proxy
  (`http://127.0.0.1:7890`). If `npm`/`npx`/installer steps fail with a
  proxy connection error, do **not** debug the proxy — use a temporary
  clean npmrc for that command (`npm --userconfig /tmp/clean-npmrc ...`
  or `NPM_CONFIG_REGISTRY=https://registry.npmjs.org`), and leave the
  user's `~/.npmrc` alone.
- **TAG / Tailscale interaction:** turning the corporate VPN (TAG) on
  **kills Tailscale SSH** to the Mac. Plan deploys and remote checks for
  TAG-off windows; when TAG is on, the Mac is reachable only via its
  own console / local access. Do not "fix" this by reconfiguring TAG or
  Tailscale — it is a known environmental fact.
- The three production tasks already on this Mac (Xiaohongshu pipeline,
  living-card host, IBKR monitoring) are **never to be touched** during
  this deploy.

## 5. Read-only fix — `~/.codex/config.toml`

If `~/.codex/config.toml` does not exist, **resume rounds silently fall
back to read-only** (the bridge's later turns cannot write, palace
recall fails, and nothing errors loudly enough to notice). Create it
before the first test:

```bash
mkdir -p $HOME/.codex
cat > $HOME/.codex/config.toml <<'EOF'
sandbox_mode = "danger-full-access"
EOF
chmod 600 $HOME/.codex/config.toml
```

This is the same option-B sandbox decision as Linux (see
[`../SKILL.md`](../SKILL.md) §Bridge facts): Codex shell runs as the
user with no sandbox isolation, equivalent to Claude's
`--dangerously-skip-permissions`. Deleting the line restores the
sandbox. If a deploy shows working first turns but broken resume/palace
recall, check this file first.

## 6. Verify (macOS)

Run the universal five checks from [`../SKILL.md`](../SKILL.md) §Step 2
(`getMe`, first round trip, resume, palace recall, restart survival).
macOS-specific confirmations:

```bash
launchctl list | grep codex
tail -f $HOME/codex-telegram/logs/*.log   # watch a live turn
# Daily restart armed: the restart plist's StartCalendarInterval is 04:00
# Restart survival: launchctl stop com.codex.telegram-bridge && launchctl start com.codex.telegram-bridge
```

## Troubleshooting (macOS)

| Symptom | Likely cause → fix |
|---|---|
| `codex: command not found` under launchd, works in Terminal | PATH trap (Step 3). Fix the wrapper's `export PATH`, unload + reload the plist. |
| Bridge starts, Telegram unreachable | Proxy/VPN missing in the wrapper environment (Step 4). Test `curl` from the wrapper, not from Terminal. |
| npm install steps fail with proxy errors | Dead npmrc proxy `127.0.0.1:7890` (Step 4). Use a temporary clean npmrc for that command. |
| First turn works, resume/palace recall fails | Missing `~/.codex/config.toml` (Step 5). Create it, restart the bridge. |
| SSH to the Mac suddenly dead mid-deploy | TAG was turned on and killed Tailscale SSH (Step 4). Wait for a TAG-off window; do not reconfigure VPNs. |
| Bridge not restarted after reboot | Plist not loaded, or `RunAtLoad` missing. `launchctl list \| grep codex`, reload the plist. |
