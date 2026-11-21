# Docker — containerised deploy (`codex-tg`)

Target: a Linux Docker host where Claude already runs containerised as
`claude-tg` (Codex 0.170.0 validated, Ubuntu 22.04-based image). Codex
runs in its **own** container, `codex-tg`, modelled on `claude-tg` —
never inside the Claude container, and never by sharing Claude's
runtime. Read the thin core ([`../SKILL.md`](../SKILL.md)) first for
prerequisites, bridge facts, and the universal verification checklist.

## 0. Assumptions

- Docker is installed and the operator can run `docker` on the host.
- A `claude-tg` container already exists and is running (dual track:
  Claude keeps running untouched throughout this deploy).
- The memory palace lives in the Claude container's state volume and is
  **shared read-write** with `codex-tg` via a bind-mount (Step 3) — it
  is the single shared writable mount.
- Resource limits and the no-daily-restart limitation (Step 5) are
  accepted by the operator before starting.

## 1. Image — bubblewrap is mandatory

Build the image from the skill's Dockerfile
([`scripts/docker/Dockerfile`](../scripts/docker/Dockerfile)). The image must install **`bubblewrap`** (plus `python3`,
`node`, `git`, `curl`, `ca-certificates`, `tmux`, `procps`, `rsync`,
`inotify-tools` as in the `claude-tg` image).

**Why bubblewrap matters:** without it, the first `codex exec` round
with `--approve-for-me` fails to start its sandbox and therefore cannot
read the memory-palace index — the bridge replies, but palace recall
fails and the failure looks like a memory problem, not a sandbox
problem. This was the first-image failure on the validated deploy: the
fix was rebuilding the image with bubblewrap, not changing the bridge.
Verify after build:

```bash
docker run --rm <image> sh -c 'which bwrap && bwrap --version'
docker run --rm <image> sh -c 'codex --version; node --version; python3 --version'
```

Pre-create the Codex home inside the image and make it owned by the
`codex` user, so the named volume mounted there on first start inherits
codex ownership (an empty root-owned volume would be unwritable).

## 2. Container — resource caps and restart policy

Run script: [`scripts/docker/run.sh`](../scripts/docker/run.sh). The validated `docker run` shape:

```bash
docker run -d --name codex-tg \
  --cpus=1.0 --cpu-shares=512 \
  --memory=2g --memory-swap=2g \
  --restart unless-stopped \
  <image> <entrypoint>
```

- **1 CPU / 2 GB RAM** is the validated cap: enough for the bridge +
  one Codex turn at a time; raising it is allowed but record the new
  cap, do not silently run uncapped next to a live-trading host.
- `--restart unless-stopped` is the only supervisor. There is **no
  daily 04:00 restart inside the container** — see Step 5.

## 3. Mounts — the writability table

| Host source | Container path | Mode | Notes |
|---|---|---|---|
| Named volume `codex_tg_state` | `/home/codex/.codex` | rw | Codex's own state (sessions, threads). Separate from Claude's state volume. |
| Host dir `codex-workspace` | `/workspace` | rw | Codex's working directory (AGENTS.md, skills staging). |
| Skill dir (`$DIR/skills`) | `/home/codex/.codex/skills` | **:ro** | Skills are read-only inside the container; stage them on the host. |
| Entrypoint (`entrypoint-codex.sh`) | `/home/codex/entrypoint-codex.sh` | **:ro** | Entrypoint script, mounted read-only. |
| **Palace bind-mount** (Claude state volume path `.../projects/-workspace/memory`) | `/home/codex/.codex/projects/-workspace/memory` | **rw** | **The single shared writable mount.** Both containers read/write the same palace. Resolve the host path from the `claude-tg` volume (e.g. under the Docker volumes directory for `claude_tg_state`) — do not guess it; `docker inspect claude-tg` shows the real source path. |
| Live logs / strategy / equity dirs | `/logs/...`, `/strategy`, `/equity/...` | **:ro** | Every live-data mount is `:ro`, unconditionally. |

Rules:

- **Never mount live trading data, strategy snapshots, or equity data
  writable.** If a path is not in the table above, mount it `:ro` or
  not at all.
- **Never mount QMT or any trading-execution path.** It is out of scope
  for this container entirely.
- The `codex.env` / `bridge.env` files (0600 on the host) are supplied
  to the container via `--env-file` or a host-side mount — never baked
  into the image, never committed.
- `config.toml` (`sandbox_mode = "danger-full-access"`) goes in the
  Codex home (`/home/codex/.codex/config.toml`, persisted in
  `codex_tg_state`) — same option-B decision as the other platforms
  (see [`../SKILL.md`](../SKILL.md) §Bridge facts).

Entrypoint: [`scripts/docker/entrypoint-codex.sh`](../scripts/docker/entrypoint-codex.sh) — starts the bridge (`tg-codex-bridge.py`)
inside the container with the config from the mounted workspace.

## 4. Palace check — via `docker exec`, never `sudo test -d`

Before starting `codex-tg`, verify the palace is reachable from the
Claude side — **without sudo**:

```bash
docker exec claude-tg test -d /home/claude/.claude/projects/-workspace/memory/wings && echo WINGS_YES || echo WINGS_NO
docker exec claude-tg ls /home/claude/.claude/projects/-workspace/memory/ | grep -E "^(user|timeline|wings)"
docker exec claude-tg test -f /home/claude/.claude/projects/-workspace/memory/.palace-config.yaml && echo CONFIG_YES || echo CONFIG_NO
```

**Do not use `sudo test -d <path>`** for this check: without a TTY,
`sudo` waits on stdin for a password and hangs indefinitely. `docker
exec <claude-container> ...` runs the check with the container's own
identity and is the validated path. (Same rule applies to any host-path
existence check that tempts you to reach for `sudo` — use `docker
exec` or a plain `test -d` as the owning user instead.)

After `codex-tg` is up, repeat the check from inside it:

```bash
docker exec codex-tg ls /home/codex/.codex/projects/-workspace/memory/ | head
docker exec codex-tg test -d /home/codex/.codex/projects/-workspace/memory/wings && echo WINGS_YES || echo WINGS_NO
```

If the second check fails while the first passes, the palace bind-mount
source path is wrong — re-resolve it from `docker inspect claude-tg`.

## 5. Proxy env and the known limitation

**Proxy:** if the Docker host reaches Telegram/OpenAI only via a proxy,
pass it into the container as environment (`HTTP_PROXY` / `HTTPS_PROXY`
/ `NO_PROXY`) in `run.sh` — the bridge inherits the container's
environment, and there is no per-process proxy config to fix later.
Verify from inside the container:

```bash
docker exec codex-tg sh -c 'echo $HTTP_PROXY; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://api.telegram.org'
```

**Known limitation — no daily restart:** a container has no systemd
timer and no launchd job. `--restart unless-stopped` revives the
container after a crash or host reboot, but it does **not** perform the
daily 04:00 bridge restart the other platforms get. Consequences:

- Long-session drift (the same reason the other platforms restart
  daily) is unmitigated inside the container; the bridge's own thread
  rotation (1 MB / 5M tokens) is the only rollover mechanism.
- If a daily restart is later required, add it on the **host** (a host
  cron job running `docker restart codex-tg` at 04:00) — do not add a
  cron daemon inside the image. That host-side addition is a separate,
  operator-approved step; this skill does not install it by default.

Record this limitation in the deploy log so a later reader does not
assume the 04:00 restart exists.

## 6. Verify (Docker)

Run the universal five checks from [`../SKILL.md`](../SKILL.md) §Step 2
(`getMe`, first round trip, resume, palace recall, restart survival).
Docker-specific confirmations:

```bash
docker ps --filter name=codex-tg --format '{{.Names}} {{.Status}}'
docker logs -f codex-tg            # watch a live turn
docker restart codex-tg            # restart survival: bridge must come back, thread must resume
docker exec codex-tg ps aux | grep -E "codex|bridge" | grep -v grep
```

Palace recall deserves extra attention here: it is the check that
catches both the missing-bubblewrap image (Step 1) and a wrong palace
bind-mount (Step 3). Do not declare success until recall passes.

## Troubleshooting (Docker)

| Symptom | Likely cause → fix |
|---|---|
| First turn replies, palace recall fails | Missing bubblewrap in the image (Step 1) — rebuild with bubblewrap; or wrong palace bind-mount source (Step 3) — re-resolve from `docker inspect claude-tg`. |
| `codex-tg` exits immediately | Entrypoint or env-file path wrong; `docker logs codex-tg` shows the Python traceback. Check `--env-file` paths are absolute host paths. |
| Container runs but Telegram unreachable | Proxy env missing inside the container (Step 5). Add `HTTP(S)_PROXY` to `run.sh`, recreate the container. |
| A host check hangs forever with no output | `sudo test -d ...` waiting for a password on non-TTY stdin (Step 4). Kill it, use `docker exec claude-tg test -d ...`. |
| `codex-tg` uses far more than 2 GB | A runaway turn (e.g. a huge session replay). The cap is doing its job — check the bridge log for the stuck thread id and `/new` the thread. Do not just raise the cap. |
| Bridge works but never restarts daily | Expected — known limitation (Step 5). Add a host-side cron `docker restart` only with operator approval. |
