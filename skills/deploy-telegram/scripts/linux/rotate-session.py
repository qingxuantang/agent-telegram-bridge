#!/usr/bin/env python3
"""Claude Code Telegram daemon -- in-process session rotation monitor.

Why this exists
---------------
The daemon runs one long-lived interactive `claude --channels` process in
tmux. A *process* restart forces a fresh fetch of the remote managed
settings / organization policy, and on subscription-authenticated
deployments that fetch can be refused (403, `oauth_org_not_allowed`:
"Your organization has disabled Claude subscription access for Claude
Code"), which silences the Telegram bot until a human re-logs in.
Re-login only fixes the process that performed it; the next restart
breaks it again.

Verified on Claude Code 2.1.329: `/clear` inside a running process does
NOT re-fetch the policy (no 403 banner, channels stay up, test message
answered). So instead of restarting the process to bound long-session
memory growth / attention drift, this monitor rotates the *session*
inside the live process: archive a summary into the deployment's
memory files, then `/clear`.

What it does (every run, cheap)
--------------------------------
1. Find the live session transcript (newest .jsonl in the session dir).
2. Measure file size and cumulative tokens (deduped usage snapshots:
   input_tokens + cache_read_input_tokens + cache_creation_input_tokens).
3. If under threshold: log the numbers and exit 0.
4. If over threshold: check the tmux pane is idle and healthy (no 403
   banner), ask Claude to write a session summary, verify the file
   exists, send `/clear`, verify with a test message, and log the result.

Configuration (environment variables)
-------------------------------------
  CLAUDE_SESSION_DIR   transcript directory; default
                       ~/.claude/projects/<cwd-slug> where <cwd-slug> is the
                       daemon's working directory with "/" replaced by "-"
                       (the daemon's start script does `cd ~`, so this is
                       usually ~/.claude/projects/-home-<user>). If the
                       computed directory does not exist, the monitor falls
                       back to the newest .jsonl under ~/.claude/projects/.
  SUMMARY_DIR          where session summaries go; default is
                       <session-dir>/memory/wings/self/drawers when that
                       directory exists (memory-palace deployments), else
                       ~/.claude/session-summaries/ (created automatically).
  TMUX_SESSION         tmux session name of the daemon; default "claude".
  TMUX_BIN             tmux binary; default is found on PATH, else
                       /usr/bin/tmux.

Trigger defaults (tune from real data -- busy-day transcripts run
20-50 MB; do NOT copy the Codex bridge's 1 MB / 5M-token numbers, they
are for a different transcript format):
  SIZE_LIMIT_MB   = 50
  TOKEN_LIMIT_M   = 150  (deduped millions)

Usage
-----
  rotate-session.py [--dry-run] [--force] [--check-only] [--log-file PATH]
  --dry-run    measure + log, never rotate
  --force      rotate regardless of threshold (test the full sequence)
  --check-only measure + log, exit 0 (same as --dry-run)
  --log-file   also append log lines to PATH (default: stdout only)

Cron (run every 15 minutes; replace <repo> with the checkout path):
  */15 * * * * flock -n /tmp/claude-rotate.lock /usr/bin/python3 <repo>/skills/deploy-telegram/scripts/linux/rotate-session.py >> $HOME/claude-session-rotation.log 2>&1

Safety
------
- Never rotates a broken daemon: if the pane shows the 403 / policy
  banner, log an alert and exit without touching the session.
- Never clears without an archive: if the summary file is not found
  after the wait, abort before `/clear`.
- Never runs twice at once: use flock (see cron line).
- Never restarts the process: this script only types into tmux.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

TMUX = os.environ.get("TMUX_BIN") or shutil.which("tmux") or "/usr/bin/tmux"
TMUX_SESSION = os.environ.get("TMUX_SESSION", "claude")

SIZE_LIMIT_MB = 50
TOKEN_LIMIT_M = 150  # deduped millions

SUMMARY_WAIT_S = 600   # up to 10 min for the model to write the summary
SUMMARY_POLL_S = 15
HELLO_WAIT_S = 60

BROKEN_MARKERS = (
    "Remote managed settings failed to load",
    "waiting for your organization's policy",
    "has disabled Claude subscription access",
)

# Instruction typed into the daemon's pane to produce the archive summary.
# The {date} and {time} placeholders are filled in at runtime; {path} is
# the summary file the model must write. Translate this into the daemon's
# working language if it is not English, but keep the "reply with just
# DONE" terminator -- the monitor waits for that exact word.
SUMMARY_PROMPT = (
    "Write a compact summary of this session and save it to {path} "
    "(append a new section headed with the time {time} if the file "
    "already exists): open tasks, decisions made, unresolved problems, "
    "and any context the next session needs to pick up. "
    "When done, reply with just the single word DONE and nothing else."
)

# Replies the monitor accepts as a healthy answer to its post-rotation
# "hello" probe. Extend if the daemon answers in another language.
HELLO_OK_MARKERS = ("What's up", "Hello", "Hey", "Hi there")

LOG_FILE = None


def log(msg):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def tmux(*args, input_text=None):
    r = subprocess.run(
        [TMUX, *args], input=input_text, capture_output=True,
        text=True, timeout=30,
    )
    return r.stdout


def send_keys(text, enter=True):
    # Type the text and Enter as SEPARATE tmux calls with a pause:
    # a long literal string plus Enter in one call can lose the Enter
    # (observed: a 200-char instruction sat unsubmitted in the input
    # buffer until a separate Enter was sent).
    subprocess.run(
        [TMUX, "send-keys", "-t", TMUX_SESSION, "--", text],
        timeout=30, check=False,
    )
    if enter:
        time.sleep(1)
        subprocess.run(
            [TMUX, "send-keys", "-t", TMUX_SESSION, "Enter"],
            timeout=30, check=False,
        )


def pane_text():
    return tmux("capture-pane", "-t", TMUX_SESSION, "-p")


def resolve_session_dir():
    """Transcript directory: env override, else ~/.claude/projects/<slug>."""
    override = os.environ.get("CLAUDE_SESSION_DIR")
    if override:
        return os.path.expanduser(override)
    home = os.path.expanduser("~")
    slug = home.replace("/", "-")
    return os.path.join(home, ".claude", "projects", slug)


def find_live_session():
    session_dir = resolve_session_dir()
    files = glob.glob(os.path.join(session_dir, "*.jsonl"))
    if not files:
        # Fall back to the newest transcript anywhere under
        # ~/.claude/projects/ (covers daemons whose cwd is not $HOME).
        base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
        files = glob.glob(os.path.join(base, "*", "*.jsonl"))
    if not files:
        return None, session_dir
    return max(files, key=os.path.getmtime), session_dir


def resolve_summary_dir(session_dir):
    """Where summaries go: env override, else palace drawers if present,
    else ~/.claude/session-summaries/ (created automatically)."""
    override = os.environ.get("SUMMARY_DIR")
    if override:
        d = os.path.expanduser(override)
    else:
        palace = os.path.join(session_dir, "memory", "wings", "self", "drawers")
        if os.path.isdir(palace):
            d = palace
        else:
            d = os.path.join(os.path.expanduser("~"), ".claude",
                             "session-summaries")
    os.makedirs(d, exist_ok=True)
    return d


def measure(path):
    """Return (size_bytes, deduped_token_total)."""
    size = os.path.getsize(path)
    total = 0
    prev = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            usage = None
            if ev.get("type") == "assistant":
                usage = (ev.get("message") or {}).get("usage")
            elif ev.get("type") == "result":
                usage = ev.get("usage")
            if not usage:
                continue
            key = (
                usage.get("input_tokens"),
                usage.get("cache_read_input_tokens"),
                usage.get("cache_creation_input_tokens"),
                usage.get("output_tokens"),
            )
            if key != prev:
                total += sum(x or 0 for x in key[:3])
            prev = key
    return size, total


def pane_is_broken(text):
    return any(m in text for m in BROKEN_MARKERS)


def pane_is_idle(text):
    """Idle = at an empty prompt, no reply in flight, no 403 banner."""
    if pane_is_broken(text):
        return False
    if "esc to interrupt" in text:
        return False
    lines = [ln for ln in text.splitlines()]
    # The prompt line is the last line starting with the prompt glyph.
    prompt_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("❯"):
            prompt_idx = i
            break
    if prompt_idx is None:
        return False
    # Nothing typed after the prompt, and the bypass-permissions footer
    # (shown only at a resting prompt) is present.
    after = "".join(lines[prompt_idx + 1:]).strip()
    if after and "bypass permissions" not in after:
        return False
    return "bypass permissions" in text


def wait_for(cond, timeout, poll=15, desc=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = pane_text()
        if cond(text):
            return text
        time.sleep(poll)
    return None


def main():
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--log-file", default=None)
    args = ap.parse_args()
    dry = args.dry_run or args.check_only
    LOG_FILE = args.log_file

    session, session_dir = find_live_session()
    if not session:
        log("ERROR no session transcript found under " + session_dir)
        return 1
    size, tokens = measure(session)
    size_mb = size / 1048576
    tokens_m = tokens / 1000000
    log(
        f"session {os.path.basename(session)[:8]} size={size_mb:.1f}MB "
        f"tokens={tokens_m:.1f}M (limits {SIZE_LIMIT_MB}MB / {TOKEN_LIMIT_M}M)"
    )

    over = size_mb >= SIZE_LIMIT_MB or tokens_m >= TOKEN_LIMIT_M
    if not over and not args.force:
        return 0
    if dry:
        log("DRY-RUN over threshold, would rotate -- stopping here")
        return 0

    log("ROTATE triggered (force=%s)" % args.force)
    pane = pane_text()
    if pane_is_broken(pane):
        log("ALERT daemon shows org-policy/403 banner -- NOT rotating, needs human re-login")
        return 2
    if not pane_is_idle(pane):
        log("daemon busy, skipping this cycle")
        return 0

    summary_dir = resolve_summary_dir(session_dir)
    date = datetime.now().strftime("%Y-%m-%d")
    summary_file = os.path.join(
        summary_dir, f"{date}-telegram-daemon-session.md"
    )
    stamp = datetime.now().strftime("%H:%M")

    instruction = SUMMARY_PROMPT.format(
        path=summary_file, date=date, time=stamp)
    log(f"asking for summary -> {summary_file}")
    send_keys(instruction)

    def summary_done(text):
        if pane_is_broken(text):
            return False
        if "DONE" in text and pane_is_idle(text):
            return os.path.exists(summary_file) and os.path.getsize(summary_file) > 0
        return False

    if wait_for(summary_done, SUMMARY_WAIT_S, SUMMARY_POLL_S) is None:
        log("ERROR summary not confirmed within timeout -- NOT clearing")
        return 3
    log("summary archived, sending /clear")
    send_keys("/clear")
    time.sleep(10)
    pane = pane_text()
    if pane_is_broken(pane):
        log("ALERT 403/policy banner appeared after /clear -- daemon needs human re-login")
        return 4
    if not pane_is_idle(pane):
        time.sleep(10)
        pane = pane_text()
    log("sending hello verification")
    send_keys("hello")

    def hello_ok(text):
        if pane_is_broken(text):
            return False
        return pane_is_idle(text) and any(m in text for m in HELLO_OK_MARKERS)

    if wait_for(hello_ok, HELLO_WAIT_S, 10) is None:
        log("WARNING hello reply not seen after rotation -- check pane manually")
        return 5
    log("ROTATE done: summary archived, session cleared, hello answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
