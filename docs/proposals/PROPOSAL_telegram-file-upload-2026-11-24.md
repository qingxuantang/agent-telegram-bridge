# Proposal: outbound file upload for the Codex Telegram bridge

**Status:** proposed — awaiting owner decision on Q1–Q4 below
**Date:** 2026-11-24
**Target repo:** `agent-telegram-bridge` (public, branch `master`)
**Scope:** `skills/deploy-codex-telegram/` only (bridge script + `AGENTS.md`
rule + docs). No change to `deploy-telegram` (Claude Code side).
**Author:** Galimuse (on behalf of the repo owner)

This proposal adds Telegram **file upload (outbound attachments)** to the
Codex bridge, so the operator can receive files the server produced —
reports, images, tables — directly in Telegram instead of fetching them
over SSH/scp.

Nothing in this document is deployment-specific: no server addresses,
hostnames, bot names, Telegram user IDs, account emails, organization IDs,
tokens, or credentials of any kind. Paths are generic (`~/`, `<workspace>`,
`~/.codex/tg/`).

---

## 1. Background and current state

The bridge (`skills/deploy-codex-telegram/scripts/tg-codex-bridge.py`,
v0.2.0-rotation, Python stdlib only) owns the Telegram side and drives
Codex from outside: `getUpdates` long-poll → allowlist check → per-chat
queue → `codex exec` / `codex exec resume` → parse JSONL → reply.

**Inbound attachments already work.** The `Telegram` class has JSON-only
`_call` plus `get_file_path` / `download_file`, and
`Bridge._download_attachment` downloads a photo/document the operator sends
into `~/telegram-inbox/<chat_id>/` (config `inbox_dir`) and hands the local
path to Codex as an `[Attachment saved to: <path>]` prefix.

**Outbound is a blank.** The `Telegram` class has no `sendDocument`,
`sendPhoto`, or `sendVideo`; `_worker` only calls
`send_message(chat_id, reply)` once per turn. A Codex turn can write a
file to the workdir, but the operator has no way to receive it through
Telegram — the only path today is a separate SSH/scp fetch.

Commands today (`_handle_command`, never sent to the model): `/start`,
`/help`, `/new`, `/status`, `/cancel`. An unknown `/command` is passed to
Codex as text. The allowlist is a double gate: `allowed_user_ids` (and, if
configured, `allowed_chat_ids`).

## 2. The problem

The bridge is a remote shell, but it is text-only in one direction. Every
task whose product *is* a file — a generated report, a rendered chart, a
spreadsheet, a photo the model produced or processed — ends with the
operator going to another channel to collect it. That breaks the
"Telegram is the whole interface" premise of the deployment, and it is the
single most-requested gap after the bridge went live on all nine hosts.

## 3. Proposed scheme

Two trigger paths. Both are needed: A is the main path, B is the escape
hatch when the model cannot or should not be in the loop.

### 3.1 Path A — marker in the model's reply (main path)

After a turn finishes, the bridge scans the reply text for markers of the
form:

```text
[SEND_FILE: <path>]
```

For each marker, in order:

1. Validate the path (see §4).
2. Upload the file with `send_document` (see §3.3).
3. Remove the marker line from the reply text.
4. Send the remaining text as usual (chunked at 4096 characters, as today).

If a marker fails validation or the upload fails, the marker is replaced
in the text by a one-line failure note (e.g. `⚠️ Could not send <name>:
<reason>`), and the rest of the reply is delivered normally. A failed
upload never kills the turn.

Multiple markers in one reply are uploaded in order; each gets its own
`sendDocument` call.

### 3.2 Path B — `/send <path>` command (escape hatch)

A new bridge command, handled in `_handle_command` like `/new` and
`/status` and never sent to the model:

```text
/send <path>
```

The bridge validates the path exactly as in §4 and uploads the file
directly, without involving Codex. Use it when the operator knows the
path already, or when the model is mid-turn / unavailable. `/send` with no
argument, or with a path that fails validation, replies with usage text
and the reason — no upload attempted.

The `/help` text gains one line: `/send <path> — send a file from the
server to this chat`.

### 3.3 Upload: `send_document` on the `Telegram` class

```python
def send_document(self, chat_id: int, path: Path,
                  caption: str | None = None) -> None
```

- **Transport:** `multipart/form-data` POST to `sendDocument`, built by
  hand. The bridge is stdlib-only (no `requests`): assemble the body with
  a generated boundary (or `email.mime`), and **stream the file in
  chunks** — never read a whole file into memory. Timeout 120 s to start
  (tunable via config, same shape as the existing `download_file`
  timeout).
- **Default to `sendDocument` for everything.** It preserves the original
  filename and accepts any file type. `sendPhoto` (inline image preview)
  is a Phase 2 option, decided by Q2.
- **Caption:** the reply text (or a caller-supplied caption) is attached
  as the document caption when present. Telegram caps captions at 1024
  characters: truncate and append `… (truncated)` (see §3.4).
- **Response handling:** reuse the existing `_call` error shape
  (`Telegram <method> HTTP <code>: <body>`); a non-`ok` result raises, and
  the caller turns it into the one-line failure note from §3.1.

### 3.4 Limits (enforced before any upload)

| Limit | Value | On violation |
|---|---|---|
| File size | 50 MB (Bot API hard limit for local-file upload) | Refuse; reply with a text explanation. No partial upload. |
| Caption | 1024 characters | Truncate, append `… (truncated)`. |
| Path | Must exist, be a regular file, and pass §4 | Refuse with a one-line reason. |

50 MB is the Bot API's hard limit for uploading a local file. Larger
files cannot go through this path at all (see §7).

### 3.5 Codex side: one AGENTS.md rule

Append a short rule to the deployment's `AGENTS.md` (the same file that
already carries the bridge's Codex note):

```markdown
## Sending files to the operator

When the operator asks for a file, or the task's product is itself a
file, put a marker on its own line in your reply:

[SEND_FILE: <absolute path under the workdir>]

The bridge uploads the file and removes the marker before delivering
your reply. Do not send intermediate artifacts — only files the
operator asked for or the final product of the task.
```

The marker format is deliberately boring: one line, absolute path, no
quoting rules. The bridge only honors a marker when the path passes §4
and the file really exists — a hallucinated marker is a no-op plus a
failure note, never an upload of something else.

### 3.6 What changes in the deployment

| Before | After |
|---|---|
| `Telegram` has `send_message` / `get_file_path` / `download_file` only | Adds `send_document` (multipart, streaming, 120 s timeout) |
| `_worker` sends one text reply per turn | Parses `[SEND_FILE: ...]` markers, uploads, then sends the remaining text |
| `_handle_command` knows `/start /help /new /status /cancel` | Adds `/send <path>`; `/help` gains one line |
| `AGENTS.md` has no file-sending rule | One short rule appended (see §3.5) |
| No outbound file path exists | Outbound files flow through the §4 validator + §4.4 logging |

No change to polling, the allowlist, rotation, the Codex invocation, or
the supervisor units. The Windows fork
(`tg-codex-bridge-winpc.py`) gets the same change, applied in the same
commit (see §7).

## 4. Safety

The bridge is a remote shell; file upload adds an exfiltration surface.
Every item below is a hard requirement, not guidance.

### 4.1 Allowlist — unchanged

The existing double gate (`allowed_user_ids`, and `allowed_chat_ids` when
configured) already covers `/send` and marker uploads, because both run
inside `handle_update` after the gate. No new gate is introduced and none
is removed. The bot stays private; one bot per server.

### 4.2 Path confinement

Every path — from a marker or from `/send` — must resolve inside the
configured workdir root (`workdir` in `config.json`):

1. Resolve with `realpath` (follows symlinks).
2. Reject `..` traversal and any symlink that escapes the root.
3. Reject paths that are not regular files (directories, devices,
   sockets, FIFOs).

A rejected path is never opened. The operator gets a one-line reason.

### 4.3 Credential files are never sendable

Even inside the workdir, the following are refused outright, by name:

- `bridge.env`, `codex.env`, `secrets.env`, `.env` (any directory)
- `*.key`, `*.pem`, `id_rsa*`, `*.p12`, `*.pfx`
- Any file under a directory named `.ssh`, `.aws`, `.gnupg`

The list lives in one constant at the top of the bridge script so it can
be extended without touching the upload path. A refused credential file
is logged as a refusal (see §4.4) and answered with a generic "file not
sendable" note — the reply does not name the denylist rule.

### 4.4 Failure never kills the turn

An upload failure (validation, network, Telegram error) is contained:

- The text reply is still delivered, with a one-line failure note in
  place of the marker.
- The file stays on disk, untouched.
- The bridge process is never affected; the per-chat worker continues to
  the next queued message.

### 4.5 Logging

Every upload attempt is logged, one line each (same logger as the rest of
the bridge, local file only):

```text
chat=<id> upload file=<name> size=<bytes> result=<ok|refused|failed> reason=<...>
```

Logs stay on the machine, like all other bridge logs. No file contents
are logged. Whether to keep a separate queryable send-record is Q4.

## 5. Verification plan

Run on a Linux deployment first (reference implementation), then repeat
the smoke set on the Windows fork.

| # | Case | Expected |
|---|---|---|
| 1 | Small text file via marker | File arrives, original name intact, marker gone from the reply text |
| 2 | Image via marker | Arrives as a document with the original filename (Phase 1: no inline preview) |
| 3 | File just under 50 MB | Uploads successfully |
| 4 | File just over 50 MB | Refused with a text explanation; no upload attempted |
| 5 | Marker for a nonexistent file | One-line failure note; rest of the reply delivered |
| 6 | `/send` with a path outside the workdir | Refused; generic reason |
| 7 | `/send` with `..` traversal and with a symlink escaping the root | Both refused |
| 8 | `/send bridge.env` / `codex.env` / `*.key` (even inside the workdir) | Refused; logged as a refusal |
| 9 | Upload fails mid-transfer (kill network / bad token in a dry-run) | Text reply still arrives with a failure note; bridge keeps polling |
| 10 | Two markers in one reply | Both files arrive, in order |
| 11 | `/send` with no argument | Usage text, no crash |
| 12 | Windows fork with the same change | Cases 1, 5, 8 pass on the fork |

## 6. Decisions for the owner (Q1–Q4)

Each item is a checkbox the owner can answer directly. A recommendation
is given per item; anything not decided here blocks landing.

- [ ] **Q1 — May the model send attachments unprompted?**
  **Recommendation:** only when the operator has asked for a file, or
  the task's product is itself a file. Otherwise the model asks first
  ("Shall I send you the report?"). Rationale: the bridge is a remote
  shell with an exfiltration surface; an unprompted send is a
  data-movement decision the operator should control. The AGENTS.md rule
  in §3.5 encodes whichever answer is chosen.

- [ ] **Q2 — Images: `sendPhoto` inline preview, or `sendDocument` for everything?**
  **Recommendation:** `sendDocument` for everything in Phase 1;
  `sendPhoto` as a Phase 2 option. Rationale: one code path, original
  filenames preserved, no format surprises. Inline preview is a
  nicety, not a requirement, and it adds a second upload path to audit.

- [ ] **Q3 — Operational size cap: the 50 MB API limit, or a lower 20 MB?**
  **Recommendation:** 50 MB, i.e. the API limit. Rationale: the cap is
  enforced by Telegram anyway; a lower self-imposed cap only helps if
  the operator wants a tripwire against accidentally sending large
  files. Revisit if a large accidental send ever happens.

- [ ] **Q4 — Keep a queryable send-record, or is the log enough?**
  **Recommendation:** the log (§4.5) is enough; do not build a separate
  record file. Rationale: the log already carries time, chat, filename,
  size and result, and it stays on the machine. A separate record adds
  a second store to maintain and to protect.

## 7. Caveats and open questions

- **50 MB is a Bot API hard limit** for local-file upload. Files above
  it cannot go through this path at all. The escape hatch is a self-hosted
  Local Bot API Server (up to 2000 MB), which is out of scope for this
  proposal — it is a separate deployment decision with its own hosting
  and update burden.
- **Caption cap is 1024 characters.** Long replies are not captions:
  the full text is still sent as a separate message; only a short
  caption (or none) rides with the document.
- **Telegram compresses preview thumbnails but the file itself is
  intact.** What arrives via `sendDocument` is byte-identical to the
  server file; only the in-client preview is a thumbnail.
- **A hallucinated marker is harmless.** The bridge only uploads a path
  that passes §4 and exists on disk. A model-invented path yields a
  one-line failure note, never an upload.
- **The Windows fork drifts.** `tg-codex-bridge-winpc.py` is a fork of
  the main bridge with three known differences (guarded `fcntl` import,
  `shell=True`, `stdin=DEVNULL`). This change must be applied to both
  files in the same commit, and the fork should be re-diffed against
  the main bridge at that point — the long-term fix is extracting a
  shared core, which is out of scope here.
- **No inbound change.** Download (`getFile`, 20 MB limit) is untouched.
  Note the asymmetry: the bridge can receive up to 20 MB via `getFile`
  but send up to 50 MB.
- **Uploads are synchronous in the worker.** A 50 MB upload at a slow
  uplink blocks that chat's worker until it finishes (other chats are
  unaffected — one worker per chat). The 120 s timeout bounds the stall;
  a future improvement is a per-chat upload queue, not in this proposal.

---

## Desensitization checklist (before any push)

- [x] No server IPs, hostnames, or SSH details.
- [x] No bot names, Telegram user IDs, or group references.
- [x] No account emails, organization IDs, or subscription details.
- [x] No tokens, keys, passwords, or credential file contents.
      Credential filenames appear only as a denylist (see §4.3).
- [x] No personal domains or machine-specific paths beyond the generic
      `~/`, `<workspace>`, `~/.codex/tg/` layout.
- [x] Error strings quoted are generic Telegram/Bot API messages.
- [x] `gitleaks` + grep scan of every new/changed file before push
      (see `CHANGES.md` in the staging directory).
