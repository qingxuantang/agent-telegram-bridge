# Thread Rotation — deploy-codex-telegram

> Part of the `deploy-codex-telegram` skill. Start at
> [../SKILL.md](../SKILL.md). Config keys live in
> [bridge-config.md](bridge-config.md).

## Why rotation exists

Every turn on the same thread replays the thread's history as input. A long
thread therefore gets slower and more expensive on every message, and
eventually the replay alone dominates the cost. Measured in practice: a
session file of ~1 MB replays as roughly 9M input tokens. Rotation caps
that growth by starting a fresh thread — after first saving what matters
into the memory palace.

## Thresholds

Both are config keys (`config.json`, see [bridge-config.md](bridge-config.md)):

| Key | Default | Meaning |
|---|---|---|
| `rotation_session_max_bytes` | `1000000` (1 MB) | Rotate when the thread's session file exceeds this size |
| `rotation_cumulative_input_max` | `5000000` (5M tokens) | Rotate when cumulative input tokens for the thread exceed this |

Either threshold tripping is enough. The check runs before each turn.

## Counting tokens correctly

**Only `token_usage_record` events count.** The bridge sums input tokens
from JSONL events whose `type` is exactly `token_usage_record`, reading the
`token_usage` (or `usage`) payload.

Do **not** count by grepping the raw JSONL for `input_tokens`: the string
also appears in other event types (per-message usage snapshots, cached-token
breakdowns, nested objects), and a naive grep over-counts severely — in one
measured case it inflated the total enough to trigger rotation far too
early. If you change the counting code, verify it against a known session
file by hand before deploying.

## Rotation flow

1. **Check.** Before a turn, the bridge reads the session file size and the
   cumulative input total for the current thread.
2. **Archive turn (old thread).** If a threshold is hit, the bridge runs one
   final turn on the **old** thread via `resume`, with an archive prompt
   instructing the model to:
   - write durable facts, decisions, and todos into the corresponding
     palace files (facts / events / decisions, per the memory-palace skill's
     layout);
   - create a dated drawer for in-progress work under the most relevant
     room — `wings/<wing>/<room>/drawers/<date>-<topic>-thread.md`
     (short kebab-case topic, `source: codex-session` in the frontmatter),
     and link it from that room's hall file;
   - add one line at the top of `timeline.md` pointing at the drawer
     (date + one sentence + full drawer path), so the new thread can find it.
   - The archive prompt also tells the model to first read the palace L1
     files (`MEMORY.md`, user / project / reference / feedback / timeline)
     so the archive lands in the right places.
3. **Clear the mapping.** Only after the archive turn completes with
   return code 0 does the bridge clear the chat → thread mapping in
   `state.json`.
4. **Fresh thread + notice.** The user's message then runs as the first turn
   of a new thread, and the bridge tells the user in Telegram:
   `🔄 上个会话已过长（<size> / <tokens>），已归档进记忆宫殿，开启新会话。`
5. **Old thread files are never deleted.** The rollout in
   `~/.codex/sessions/` and the session file stay on disk forever. Rotation
   only drops the *mapping*; the history is always recoverable by thread id.

## Failure rules

- **Archive fails → do not rotate.** If the archive turn times out or
  returns non-zero, rotation is skipped for that turn and retried on the
  next message. The old thread keeps serving. Losing context silently is
  worse than running one more turn on a long thread — and since old thread
  files are never deleted, a failed archive loses nothing; the next attempt
  can still read the same thread.
- **`/new` discards without archiving.** `/new` is an explicit user request
  to drop the thread; it clears the mapping immediately with no archive
  turn. (The thread file itself still remains on disk.)
- **`/status`** reports the current thread id, session size, cumulative
  input tokens, and whether rotation is pending — the operator's way to see
  rotation coming before it happens.

## Cost context (why rotation, not more caching)

Measured on a heavy day: ~$5.84 for 31.1M input tokens (98% cached) and 99k
output tokens. The dominant cost lever is **shrinking the replayed context
/ starting a new thread** — not adding more cache: extra caching would have
saved only ~$0.81 on that day, while the new-thread counterfactual was
~$2.21. Rotation is the mechanism that realises that saving. (ChatGPT Plus
at $20/month was evaluated as an alternative and rejected by the operator:
the 5-hour and weekly limits are too tight for always-on multi-machine
deployment, so service-account API keys remain the funding route, with
per-server keys, spend caps, and alerts.)
