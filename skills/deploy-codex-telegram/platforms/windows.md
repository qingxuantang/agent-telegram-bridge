# Windows — Task Scheduler deploy

Target: Windows 11 (Codex 0.170.0 validated). The bridge runs under
Task Scheduler: an AtLogOn task with a hidden PowerShell wrapper, plus a
separate daily 04:00 restart task. There is no systemd and no launchd —
do not try to transplant the Linux or macOS overlay. Read the thin core
([`../SKILL.md`](../SKILL.md)) first for prerequisites, bridge facts,
and the universal verification checklist.

## 0. Assumptions

- You are executing **locally on the Windows host** (self-execution) or
  over OpenSSH to it (see Step 5 for the SSH setup). The target paths
  below are Windows paths.
- The host already runs Claude in parallel (dual track). Do not stop,
  replace, or reconfigure it.
- This host **connects directly to Telegram and OpenAI — no proxy is
  needed**. Do not add proxy variables "just in case"; a wrong proxy is
  a failure mode, not a safety net.
- **Model note (open):** on Codex 0.170.0 this host resolved the model
  as `gpt-5.6-luna` / `gpt-6.2-sol`, inconsistent with the servers'
  `gpt-6.1-sol`. Whether to pin `-m` is **TODO / open** — record the
  resolved model in the deploy log, do not assert a pinning policy.

## 1. Install Codex and lay out the deploy directory

Install Codex for the deploy user, then lay out (target-side
`C:\Users\<user>\codex-tg\`):

```
C:\Users\<user>\codex-tg\
├── tg-codex-bridge-winpc.py     # bridge daemon (Windows variant, stdlib only)
├── tg-codex-bridge.config.json  # copy of tg-codex-bridge.config.json.example, filled in
├── bridge.env                   # BOT_TOKEN      — file ACL: only the deploy user + SYSTEM
├── codex.env                    # OPENAI_API_KEY — same ACL
├── config.toml                  # sandbox_mode = "danger-full-access" (Step 2)
└── scripts\
    ├── run-bridge.ps1           # hidden wrapper: launches the bridge
    ├── restart-bridge.ps1       # daily restart: stops + relaunches the bridge
    └── create-codex-tasks.ps1   # creates both scheduled tasks (Step 3)
```

- `tg-codex-bridge-winpc.py`:
  [`scripts/tg-codex-bridge-winpc.py`](../scripts/tg-codex-bridge-winpc.py)
- Config: copy the `.example` file, set `bot_token_file` →
  `bridge.env`, `codex_env_file` → `codex.env`, `telegram_user_id` →
  your id (digits, e.g. `1234567890`), `codex_timeout_s` → `1800`.
  Never put the token or key literally in the JSON.
- Lock the env files down: only the deploy user account and SYSTEM may
  read `bridge.env` / `codex.env` (Windows equivalent of 0600 —
  `icacls` to remove inherited access). Never commit them, never paste
  them into chat.

## 2. `config.toml` — sandbox mode

Codex's config lives at `C:\Users\<user>\.codex\config.toml`:

```toml
sandbox_mode = "danger-full-access"
```

Same option-B decision as the other platforms (see
[`../SKILL.md`](../SKILL.md) §Bridge facts). Deleting the line restores
the sandbox. Without it, resume rounds can silently fall back to
read-only and palace recall fails.

## 3. Task Scheduler — AtLogOn + hidden wrapper + daily 04:00 restart

Create both tasks with
[`scripts/windows/create-codex-tasks.ps1`](../scripts/windows/create-codex-tasks.ps1)
What it creates:

**Bridge task (AtLogOn):**

```powershell
$bridgeAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\<user>\codex-tg\scripts\run-bridge.ps1"'
$bridgeTrigger = New-ScheduledTaskTrigger -AtLogOn -User "<HOST>\<user>"
$bridgeSettings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
```

- `-WindowStyle Hidden`: no console window on the desktop.
- `RestartCount 999` / 1-minute interval: Task Scheduler itself revives
  the bridge after a crash — this is the Windows equivalent of
  systemd `Restart=on-failure` / launchd `KeepAlive`.
- Runs as the deploy user, at logon. Verify in Task Scheduler that the
  task is `Ready` and its last run result is `0x0`.

**Daily restart task (04:00):**

```powershell
$restartAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\<user>\codex-tg\scripts\restart-bridge.ps1"'
$restartTrigger = New-ScheduledTaskTrigger -Daily -At 04:00
```

`restart-bridge.ps1` stops the bridge task and starts it again (a
clean restart, not a kill of the Codex child — the bridge's own
shutdown path applies). Confirm the trigger shows next run 04:00.

Wrapper scripts (also under `scripts/windows/`):

| File | Purpose |
|---|---|
| `run-bridge.ps1` | Launches the bridge: `python.exe <full-path>\tg-codex-bridge-winpc.py --config ...` (see Step 4 for the `python.exe` rule) |
| `restart-bridge.ps1` | Stops + restarts the bridge task for the daily 04:00 restart |
| `create-codex-tasks.ps1` | Creates both tasks above (this step) |
| `winpc-smoke-test.ps1` | Smoke test: turn 1 + resume turn + palace recall (Step 6) |

## 4. Windows-only code traps (in the bridge and the wrappers)

These are already handled in the canonical Windows bridge variant —
know them so you recognise a regression if you edit the bridge:

- **`codex.cmd` stdin hang.** `codex` on Windows is a `.cmd` shim.
  The bridge must start it with `stdin=subprocess.DEVNULL` — a Codex
  child that inherits a readable stdin blocks forever waiting on it.
  If the bridge hangs with no output and no error, check the stdin
  wiring first.
- **`shell=True` for `.cmd` shims.** Subprocess calls that invoke the
  `codex` command must use `shell=True` on Windows, otherwise the
  `.cmd` shim is not executed at all. (On Linux/macOS the same code
  runs with `shell=False`.)
- **`fcntl` guard.** The Linux bridge uses `fcntl.flock` for its
  singleton lock; Windows has no `fcntl`. The Windows variant guards
  the import (`try: import fcntl / except ImportError: fcntl = None`)
  and skips file locking — the singleton is enforced by the Scheduled
  Task instead (one AtLogOn task, one bridge). Do not "fix" the missing
  `fcntl` by installing a shim.
- **PowerShell 5.1 encoding.** When writing `authorized_keys` (Step 5)
  or any file the OpenSSH server will read, write it with
  `-Encoding Ascii` — **not** `utf8NoBOM`. A UTF-8 BOM / non-ASCII
  encoding silently breaks key authentication.
- **`python3` is not recognised.** There is no `python3` on PATH on a
  stock Windows host. Call Python by its **full path to `python.exe`**
  (e.g. `C:\Users\<user>\AppData\Local\Programs\Python\Python3<xx>\python.exe`
  — verify the real path with `where.exe python`). Every wrapper script
  and every manual command must use that full path.

## 5. Remote access — OpenSSH Server (if deploying over SSH)

If the deploy is done remotely rather than at the console:

- Install OpenSSH Server from the **GitHub MSI release** (the
  PowerShell `Add-WindowsCapability` path is not the validated one for
  this deploy). Install, start the `sshd` service, set it to Automatic.
- Authorise the deploy key: append the public key to
  `C:\Users\<user>\.ssh\authorized_keys` with `-Encoding Ascii` (Step 4
  — a BOM breaks auth). Verify `ssh <user>@<host> "echo ok"` before
  starting the deploy.
- SSH to this host needs the explicit identity flag if the default key
  is not picked up (symptom: `Permission denied` despite a correct
  key): `ssh -i $HOME/.ssh/id_ed25519 <user>@<host>`.
- A VPN running on the host can block the remote path (observed: an
  active commercial VPN blocks the Tailscale/SSH route; turning it off
  restores access). If SSH suddenly stops working mid-deploy, check
  whether a VPN was just turned on before debugging keys.

## 6. Verify (Windows)

Run the universal five checks from [`../SKILL.md`](../SKILL.md) §Step 2
(`getMe`, first round trip, resume, palace recall, restart survival).
Windows-specific confirmations:

```powershell
Get-ScheduledTask -TaskName "*codex*" | Select TaskName, State
# Smoke test (turn 1 + resume + palace recall):
#   scripts/windows/winpc-smoke-test.ps1
Get-Content C:\Users\<user>\codex-tg\logs\bridge.log -Tail 50 -Wait
```

Record the resolved model version in the deploy log (Step 0 note).

## Troubleshooting (Windows)

| Symptom | Likely cause → fix |
|---|---|
| Bridge task `Ready` but bridge never starts | `python3` in the wrapper (Step 4) — replace with the full path to `python.exe`. |
| Bridge starts, Codex child hangs with no output | `stdin` not `DEVNULL` (Step 4) — the `.cmd` shim is waiting on stdin. |
| `codex` not executed at all / exit code 9009 | Missing `shell=True` on the subprocess call (Step 4). |
| `ImportError: No module named 'fcntl'` | The Windows variant's `fcntl` guard is missing — use `tg-codex-bridge-winpc.py`, not the Linux bridge file. |
| SSH `Permission denied` after key setup | `authorized_keys` written with a BOM (Step 4/5) — rewrite with `-Encoding Ascii`; or the key needs `-i` (Step 5). |
| SSH suddenly dead mid-deploy | A VPN was turned on and blocks the route (Step 5). Turn it off, retry. |
| Task shows last run result `0x1` | Read the bridge log — usually a bad token/key file path (relative path in the task's working directory). Use absolute paths in `run-bridge.ps1`. |
