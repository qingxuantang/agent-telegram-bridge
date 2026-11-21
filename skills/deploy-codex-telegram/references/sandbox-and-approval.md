# Sandbox and Approval — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). See also
> [bridge-config.md](bridge-config.md) (the `sandbox` / `approve_for_me`
> config keys) and
> [post-deploy-hardening.md](post-deploy-hardening.md) (the bwrap incident).

## The decision: `danger-full-access`

Codex's sandbox is controlled by `sandbox_mode` in `~/.codex/config.toml`.
The deployed value on all machines is:

```toml
sandbox_mode = "danger-full-access"
```

This is the deliberate, documented choice — option B in the decision the
operator made — and it is the direct equivalent of Claude Code's
`--dangerously-skip-permissions`. It means: no approval prompts, no sandbox
confinement; Codex runs with the bridge user's full filesystem rights.

### Why it was chosen — the cost of the alternative

On Ubuntu 24.04 the kernel sets
`apparmor_restrict_unprivileged_userns=1`, which blocks the unprivileged user
namespace creation that `bubblewrap` (`bwrap`) needs for its loopback/sandbox
mode. The observable failure: Codex cannot start its sandbox at all, and the
bridge's turns fail before the model ever runs (see
[post-deploy-hardening.md](post-deploy-hardening.md), "bwrap missing").

The alternatives were:

- **A. Fix the sandbox:** rebuild the container image with bubblewrap
  installed, or relax the host's AppArmor setting. The image fix works for
  containers; the host setting is a host-wide security change the operator
  did not want to make on trading machines.
- **B. `danger-full-access`:** accept that the sandbox is off and rely on
  the remaining controls — allowlist (only the operator can talk to the
  bot), workspace-write working-directory discipline, and the fact that the
  bridge runs as an unprivileged user with read-only mounts for protected
  data.

The operator chose **B**. The trade-off is explicit: the sandbox no longer
protects against the model writing outside the workdir. What still protects
the machine is (a) the allowlist — only one Telegram user id can reach the
bridge; (b) the bridge's `workdir` discipline; (c) read-only mounts for live
trading data and protected directories; and (d) the deployment rule that the
bridge never runs as root on trading machines and never touches a running
trading process.

### Reversibility

The choice is one line in one file. To re-enable the sandbox, delete the
`sandbox_mode = "danger-full-access"` line from `~/.codex/config.toml`
(and, on containers, rebuild the image with bubblewrap installed first —
otherwise the sandbox fails to start instead of engaging). No other file
needs to change. Re-verify with a turn that writes inside the workdir
(should succeed) and one that writes outside it (should be blocked and
logged).

### What NOT to use

`--dangerously-bypass-approvals-and-sandbox` (`--yolo`) is **forbidden on
bare metal**. It is acceptable only inside an isolated container, and even
there `danger-full-access` in `config.toml` is the preferred form because it
is visible in a file rather than hidden in a launch flag.

## Flag conflicts: `--approve-for-me` vs `--sandbox`

On Codex 0.169.0 and 0.170.0, `--approve-for-me` and `--sandbox` are
**mutually exclusive** — passing both makes `codex exec` fail immediately
with a clap argument error. `--approve-for-me` already implies the
workspace-write sandbox, so the bridge passes **exactly one** of them:

- First turn: `codex exec --json --approve-for-me --skip-git-repo-check
  "<prompt>"`
- The `sandbox: "workspace-write"` config value is therefore documentation of
  the effective sandbox, not a second flag — the bridge must not translate it
  into a `--sandbox` argument alongside `--approve-for-me`.

Older research notes mention `--ask-for-approval never`; that flag does not
exist in 0.164.0-alpha.9 or later. Do not use it. **Always re-check flags
against the target machine's own `codex exec --help`** — flag sets have
changed between minor versions, and the bridge's flag set is only as correct
as the version it was verified against.

## Resume turns do not take `--approve-for-me`

`codex exec resume <thread_id>` accepts `--json` and
`--skip-git-repo-check` but **not** `-s` / `-C` / `--approve-for-me`. The
sandbox (and approval mode) is fixed at thread creation and cannot be
changed on resume. Consequences:

- A resumed thread runs under whatever sandbox the first turn established.
  If the first turn was created without approval, resume cannot add it.
- The bridge therefore relies on `config.toml`'s `sandbox_mode` as the
  backstop: whatever the flags say on a given turn, the config file's mode
  is what the process actually enforces.
- If a resume fails (bad thread id, corrupt rollout), the bridge opens a
  fresh thread automatically — see [thread-rotation.md](thread-rotation.md).

## The macOS sibling failure: missing `config.toml` means read-only

The same mechanism produced a different-looking failure on macOS: the
machine had no `~/.codex/config.toml` at all, so Codex fell back to its
default (read-only sandbox) and every write the model attempted was denied.
Symptom: the bot answers, but nothing it tries to do on disk works. Fix:
create `~/.codex/config.toml` with the intended `sandbox_mode` before the
first real turn, and verify with a trivial write. This is the same class of
fault as the bwrap failure — "the sandbox is not what you think it is" —
and the same verification (one in-workdir write, one out-of-workdir write)
catches both.
