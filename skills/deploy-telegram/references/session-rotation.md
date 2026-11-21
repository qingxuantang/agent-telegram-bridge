# Session rotation: rotate the conversation, not the process

On subscription-authenticated deployments, the nightly **process restart**
that this repo used to recommend as the long-session drift mitigation is
itself a daily outage trigger. This document describes the replacement:
keep one daemon process alive indefinitely and rotate the **session**
inside it once it grows past a threshold.

The Linux path is the reference implementation
([`../platforms/linux.md`](../platforms/linux.md) Step 12,
[`../scripts/linux/rotate-session.py`](../scripts/linux/rotate-session.py)).
macOS and Windows overlays need the same treatment in a follow-up.

---

## 1. The problem: the restart re-triggers an organization-policy failure

### 1.1 What happens

On a Claude Code daemon authenticated with a **Pro subscription (OAuth)**,
a fresh process start can fail with:

```text
Your organization has disabled Claude subscription access for Claude Code
· Use an Anthropic API key instead, or ask your admin to enable access
```

At startup the pane also shows:

```text
⚠ Remote managed settings failed to load (access denied (403)) · no remote
  policy applied · /status for details
▎ --channels waiting for your organization's policy
▎ (plugin:telegram@claude-plugins-official)
▎ Messages are dropped until it loads
```

`/status` confirms: `Managed settings (remote): fetch failed — no policy
applied (access denied (403))` and `Organization policy: Couldn't load`.
The Telegram bot goes dark — messages are dropped, not queued.

### 1.2 Observed pattern

The error first appeared on a daemon that had been running normally.
`/logout` + browser-OAuth re-login with the same account fixed it:
`Logged in as <account>`, test message answered, Telegram bot working.
The fix lasted roughly half a day. The next scheduled 04:00 process
restart brought the identical error back, and a second re-login fixed it
again the same way.

Observed on Claude Code 2.1.327 → 2.1.328 → 2.1.329. The version is
incidental: the error occurred on 2.1.327 before any update, and again on
2.1.328 after a restart.

### 1.3 Root cause (as far as it can be established from the outside)

The organization-policy fetch is a **process-startup event**. A running
process that authenticated successfully keeps working; a new process
starting from stored OAuth credentials re-fetches the policy and can be
refused with `oauth_org_not_allowed`. Re-login is not a durable fix
because it only helps the process that performed the login — the next
restart starts a new process and hits the same wall.

There is no toggle for this on the account side: personal Pro settings
have no Organization/Admin section, and the Anthropic Console organization
(individual or Team) has no subscription-access switch for Claude Code —
its Claude Code settings expose only Fast mode and Dynamic workflows.
The block must be lifted server-side (Anthropic Support) or avoided by
authenticating with an API key instead.

### 1.4 Why this matters for this repo

The repo's previous guidance (nightly daemon restart to mitigate
long-session attention drift, see
[`post-deploy-hardening.md`](./post-deploy-hardening.md) §7) turns
every restart into a fresh policy fetch on subscription-authenticated
deployments. A refused fetch silences the bot until a human re-logs in —
so the restart became a daily outage trigger instead of a mitigation.

---

## 2. Verified behavior: `/clear` does not re-fetch the policy

Tested on Claude Code 2.1.329, in a process that had just been
re-logged-in via browser OAuth:

1. Sent `/clear` into the daemon's tmux pane.
2. No 403 banner reappeared; the channels banner stayed up
   (`Channels (experimental) messages from plugin:telegram@... inject
   directly in this session`).
3. A `hello` test message afterwards was answered normally.

Conclusion: **starting a new conversation inside the same process does
not trigger a policy fetch.** The policy fetch is tied to process startup
(and login), not to session rotation. This is the fact the whole scheme
rests on.

---

## 3. The scheme: rotate the session, not the process

### 3.1 Principle

Keep one `claude --channels` process alive indefinitely. When the
conversation grows past a threshold, archive a summary into the
deployment's memory files and start a fresh conversation **inside the
same process** with `/clear`. The nightly restart timer is retired; the
watchdog (restart if the process is gone) stays as a safety net.

This preserves the benefit the restart was for — bounding memory growth
and attention drift — without paying the restart's price (a fresh policy
fetch).

### 3.2 Trigger: transcript size / cumulative input tokens

The monitor watches the current session transcript:

```text
~/.claude/projects/<cwd-slug>/<session-id>.jsonl
```

Two signals, either one trips rotation:

- **Transcript file size** past a configured limit.
- **Cumulative input tokens** past a configured limit, summed from the
  `token_usage_record` events in the transcript.

Thresholds must be set at **Claude scale**. Observed daily transcripts
run **20–50 MB** on a busy daemon, so the numbers used by the Codex
bridge rotation (1 MB file size / 5M cumulative input tokens) do **not**
transfer — they would rotate every few minutes. The monitor script ships
with starting defaults of **50 MB / 150M tokens** (measured on a real
busy deployment), but these are starting points only: **measure a few
days of transcript growth on the target deployment first, then set the
thresholds from real data**. The monitor logs the current size/token
count on each check so the operator can tune from measurements instead
of guessing.

### 3.3 Rotation sequence

Performed by a local monitor (cron job or small loop), **not** by
Telegram:

1. **Check idle.** Read the tmux pane; proceed only when the daemon is
   sitting at an empty prompt (no reply in flight). If busy, skip this
   cycle and retry later.
2. **Summarize.** Send a message into the pane instructing Claude to
   write a compact session summary into the deployment's memory files:
   open tasks, decisions, unresolved items, and anything the next
   session needs to pick up.
3. **Wait for completion.** Poll the pane until the summary write is
   done and the prompt is idle again.
4. **Rotate.** Send `/clear` into the pane. The next Telegram message
   starts a fresh conversation in the same process.
5. **Verify.** Send a short test message and confirm a normal reply
   and no 403 banner. Log the rotation (time, pre-rotation size/token
   count, summary file path).

### 3.4 Why the trigger cannot come from Telegram

Already documented in this repo
([`post-deploy-hardening.md`](./post-deploy-hardening.md) §8):
slash commands sent over Telegram are **not** recognized — channel
messages are injected into the prompt stream and bypass the CLI parser
entirely, so `/clear` (like `/model`, `/compact`, `/cost`) cannot be
triggered in-band from Telegram. The rotation command must be typed into
the terminal, which is why the monitor drives it through `tmux
send-keys`, the same mechanism an operator uses over SSH.

### 3.5 What changes in the deployment

| Before | After |
|---|---|
| Daily restart timer (e.g. 04:00) restarts the service | Timer disabled/removed; rotation monitor installed instead |
| `start-claude.sh` loop respawns `claude` on exit | Unchanged — still the respawn loop |
| Watchdog restarts the service if the process is gone | Unchanged — safety net only |
| Session context wiped daily by the restart, with no summary | Context wiped by rotation **after** a summary is archived |

---

## 4. Caveats and open questions

- **Mitigation, not a cure.** Any process death — OOM kill, crash,
  host reboot, watchdog restart — starts a new process and re-triggers
  the policy fetch. Rotation turns "breaks every night" into "breaks
  only when the process dies", which is strictly better but not a fix.
  The durable fixes remain: Anthropic Support re-enabling subscription
  access server-side, or switching the daemon to API-key auth.
- **OAuth token refresh.** It is unknown whether a periodic token
  refresh inside a running process re-fetches the policy. If a
  long-lived process ever goes dark without a restart, this is the
  first thing to check.
- **Auto-update lags.** Claude Code's built-in updater downloads in the
  background and applies on process restart. Without restarts the
  daemon stays on its current version indefinitely (the pane shows
  "Update installed · restart to apply"). Accept the lag, or schedule
  an occasional deliberate restart with a human on standby to re-login
  if the policy fetch fails.
- **Summary quality is the continuity.** `/clear` wipes context; what
  the next session knows is exactly what the summary captured. Keep the
  summary instruction tight (open tasks, decisions, unresolved items)
  and verify the summary file exists before clearing.
- **Thresholds need real data.** The numbers in §3.2 are starting
  points. Measure a few days of transcript growth on the target
  deployment before fixing them.
- **`/clear` verification is single-point.** It was verified once, on
  one version (2.1.329), on one deployment. If a future Claude Code
  version re-fetches policy on session rotation, the scheme fails
  loudly (403 banner returns) — the monitor's post-rotation verify
  step exists to catch exactly that.
