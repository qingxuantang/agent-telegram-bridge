#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tg-codex-bridge.py — Pilot Telegram <-> Codex CLI bridge (pilot: codex exec).

Architecture (see ../PLAN.md §2.2):
  Telegram Bot API long-poll (getUpdates)
    -> allowlist check (your user id + configured chat ids only)
    -> per-chat queue, one codex turn at a time per chat
    -> `codex exec --json ...` (first turn) / `codex exec resume <thread> --json`
    -> parse JSONL, take the final agent message, sendMessage (chunked >4096)

Stdlib only: urllib, json, subprocess, threading, queue, logging, fcntl.
No pip dependencies. No token is ever written to a log file or to state.json.

Commands implemented by the bridge (never sent to the model):
  /new     forget this chat's thread; next message starts a fresh thread
  /status  thread id, queue depth, running state, codex version, uptime
  /cancel  kill the running codex turn for this chat + drop queued messages
  /send <path>  send a file from the server as a Telegram document (no Codex turn)
  /help    / /start  usage

Rotation (automatic, no command needed): when the current thread's
session file exceeds rotation_session_max_bytes (default 1MB) or its
cumulative input tokens exceed rotation_cumulative_input_max (default
5M), the bridge first runs one archive turn on the old thread — the
model writes durable facts/decisions/todos into the memory palace
(wings + a dated drawer + a timeline pointer) — then clears the thread
and starts fresh. The old thread file is never deleted, so a failed
archive never loses context: rotation is skipped that turn and retried
on the next message. /new still discards without archiving.

Attachments: photos/documents are downloaded via getFile to
~/telegram-inbox/<chat_id>/<date>_<file_id>_<name> and their path is
prepended to the prompt, so Codex can read the file. Outbound: a Codex
reply may carry `[SEND_FILE: <path>]` markers (only when the operator
asked for a file — see the AGENTS.md rule); the bridge uploads each
marked file as a Telegram document (sendDocument, 50 MB cap) and sends
the remaining text. `/send <path>` uploads directly without a Codex
turn. Only files under the workdir or a configured send root
(`send_roots` in config.json) may be sent; credential/key files
are always refused.

State: ~/.codex/tg/state.json  {"offset": int,
  "threads": {"<chat_id>": "<thread_id>"}}  (atomic write via tmp+rename).

Auth: token from --config file ("bot_token") or TELEGRAM_BOT_TOKEN env
or ~/.codex/tg/bridge.env (KEY=VALUE). The placeholder value
"PASTE_TOKEN_HERE" is treated as missing.

Safety notes (read PLAN.md §8.3):
  - This bridge is a remote shell. Allowlist is the only gate; keep the
    bot private, one bot per server, never reuse Claude's bot token.
  - Default sandbox is workspace-write via --approve-for-me, which is
    the unattended recommendation. --approve-for-me already implies the
    workspace-write sandbox: on codex-cli 0.169.0 passing --sandbox AND
    --approve-for-me together is a hard error (clap conflict), so this
    bridge passes exactly one of them.
    FLAG NOTE (verified 2026-11-15, codex-cli 0.164.0-alpha.9 locally and
    0.169.0 on a Linux server): `codex exec` in these versions has NO
    --ask-for-approval flag — the unattended-approval flag is
    --approve-for-me ("route approval requests through automatic review
    using the workspace-write sandbox"). Older write-ups citing
    `--ask-for-approval never` are stale for these versions; always
    re-check `codex exec --help` on the target host before trusting any
    flag list, including this file's.
  - Resume does NOT accept -s/--sandbox or -C/--cd: the sandbox is fixed
    when the thread is created, so resume invocations intentionally omit
    them. Resume DOES accept --json and --skip-git-repo-check in 0.164.0.

Run:
  python3 tg-codex-bridge.py --config ~/.codex/tg/config.json
  python3 tg-codex-bridge.py --dry-run --config ~/.codex/tg/config.json
  python3 tg-codex-bridge.py --write-config-template [path]
  python3 tg-codex-bridge.py --help
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.3.0"
DEFAULT_USER_ID = 1234567890  # 示例占位符，请替换为你自己的 Telegram user id
PLACEHOLDER_TOKEN = "PASTE_TOKEN_HERE"
MAX_TG_MESSAGE = 4096
POLL_TIMEOUT_S = 30
CODEX_TIMEOUT_S = 1800

# Outbound attachments (sendDocument): the Telegram Bot API caps a
# bot-sent document at 50 MB. That cap is on the bot side only — it has
# nothing to do with the receiving account (a free account can receive
# far larger files). 20 MB is the getFile *download* limit (inbound),
# not the send limit. Captions are capped at 1024 characters by the API.
MAX_SEND_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_TG_CAPTION = 1024
MAX_SEND_FILES_PER_REPLY = 3

# Files the bridge will never send, even inside the workdir: credential
# and key material. Matched by exact filename or by suffix (case-insensitive).
SENDABLE_DENY_NAMES = {"bridge.env", "codex.env", "secrets.env", ".env"}
SENDABLE_DENY_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}

# Rotation: when a thread's session file (or its cumulative input tokens)
# passes these limits, the bridge archives the thread into the memory
# palace and starts a fresh thread. Session size is the cheap proxy for
# cost: ~1MB of session file replays as roughly 9M input tokens
# (2026-11-16, another host: 3.5MB file <-> 31M cumulative input tokens).
ROTATION_SESSION_MAX_BYTES = 1_000_000
ROTATION_CUMULATIVE_INPUT_MAX = 5_000_000

# ---------------------------------------------------------------- config

CONFIG_TEMPLATE = {
    "bot_token": PLACEHOLDER_TOKEN,
    "allowed_user_ids": [DEFAULT_USER_ID],
    "allowed_chat_ids": [],
    "codex_bin": "codex",
    "workdir": "~",
    "send_roots": [],
    "inbox_dir": "~/telegram-inbox",
    "state_file": "~/.codex/tg/state.json",
    "log_file": "~/.codex/tg/bridge.log",
    "lock_file": "~/.codex/tg/bridge.lock",
    "codex_timeout_s": CODEX_TIMEOUT_S,
    "rotation_session_max_bytes": ROTATION_SESSION_MAX_BYTES,
    "rotation_cumulative_input_max": ROTATION_CUMULATIVE_INPUT_MAX,
    "sandbox": "workspace-write",
    "approve_for_me": True,
    "poll_timeout_s": POLL_TIMEOUT_S,
}


def expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


def write_config_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(CONFIG_TEMPLATE, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote config template to {path}")
    print("Edit it: set bot_token, allowed_chat_ids, workdir. "
          "Keep the file 0600 if it holds a token: chmod 600 " + str(path))


def load_dotenv(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_config(path: Path | None) -> dict:
    cfg = dict(CONFIG_TEMPLATE)
    if path is not None:
        if not path.exists():
            raise SystemExit(f"config file not found: {path}\n"
                             f"Create one with: {sys.argv[0]} --write-config-template {path}")
        with open(path, encoding="utf-8") as f:
            user_cfg = json.load(f)
        if not isinstance(user_cfg, dict):
            raise SystemExit(f"config file must be a JSON object: {path}")
        cfg.update(user_cfg)
    # send_roots: optional extra sendable directories. Must be a list of
    # absolute paths; anything else is a startup error, not a silent skip.
    roots = cfg.get("send_roots") or []
    if not isinstance(roots, list) or any(not isinstance(p, str) for p in roots):
        raise SystemExit("config error: send_roots must be a list of absolute paths")
    for p in roots:
        if not os.path.isabs(expand(p)):
            raise SystemExit(f"config error: send_roots entry is not absolute: {p}")
    # Env / bridge.env overrides for the token only (never for allowlists).
    env = dict(os.environ)
    env.update(load_dotenv(expand("~/.codex/tg/bridge.env")))
    if env.get("TELEGRAM_BOT_TOKEN"):
        cfg["bot_token"] = env["TELEGRAM_BOT_TOKEN"]
    return cfg


def token_is_real(token: str | None) -> bool:
    return bool(token) and token != PLACEHOLDER_TOKEN and len(token) > 20


# ---------------------------------------------------------------- state

class State:
    """Thread mapping + update offset, persisted atomically."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"offset": 0, "threads": {}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        self.data.setdefault("offset", 0)
        self.data.setdefault("threads", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def thread_for(self, chat_id: int) -> str | None:
        return self.data["threads"].get(str(chat_id))

    def set_thread(self, chat_id: int, thread_id: str | None) -> None:
        if thread_id is None:
            self.data["threads"].pop(str(chat_id), None)
        else:
            self.data["threads"][str(chat_id)] = thread_id
        self.save()


# ---------------------------------------------------------------- telegram

class Telegram:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, payload: dict | None = None,
              timeout: int = 60) -> dict:
        url = f"{self.base}/{method}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Telegram {method} HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Telegram {method} network error: {e}") from e

    def get_updates(self, offset: int, timeout_s: int) -> list:
        resp = self._call("getUpdates", {"offset": offset, "timeout": timeout_s},
                          timeout=timeout_s + 15)
        if not resp.get("ok"):
            raise RuntimeError(f"getUpdates not ok: {resp}")
        return resp.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in chunk_text(text):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def send_document(self, chat_id: int, path: Path, caption: str = "") -> None:
        """Upload a local file as a Telegram document (multipart/form-data).

        The body streams from disk in chunks (see _MultipartBody), so a
        50 MB file is never held whole in memory. Raises ValueError when
        the file is missing or over the Bot API 50 MB send limit;
        RuntimeError on a Telegram/network error.
        """
        if not path.is_file():
            raise ValueError(f"file not found: {path}")
        size = path.stat().st_size
        if size > MAX_SEND_DOCUMENT_BYTES:
            raise ValueError(
                f"file too large for Telegram sendDocument: {size:,} bytes "
                f"(limit {MAX_SEND_DOCUMENT_BYTES:,} bytes)")
        if len(caption) > MAX_TG_CAPTION:
            caption = caption[:MAX_TG_CAPTION - 13] + " …(truncated)"
        boundary = f"----tg-bridge-{os.urandom(16).hex()}"
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        body = _MultipartBody(boundary, fields, "document", path)
        url = f"{self.base}/sendDocument"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(len(body))})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Telegram sendDocument HTTP {e.code}: {err}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Telegram sendDocument network error: {e}") from e
        finally:
            body.close()
        if not resp.get("ok"):
            raise RuntimeError(f"sendDocument not ok: {resp}")

    def get_file_path(self, file_id: str) -> str:
        resp = self._call("getFile", {"file_id": file_id})
        if not resp.get("ok"):
            raise RuntimeError(f"getFile not ok: {resp}")
        return resp["result"]["file_path"]

    def download_file(self, file_path: str, dest: Path) -> Path:
        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            f.write(r.read())
        return dest


def chunk_text(text: str, limit: int = MAX_TG_MESSAGE) -> list[str]:
    """Split on line boundaries where possible; never exceed the limit."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit:
            if cur:
                chunks.append(cur)
            # A single over-long line: hard-split it.
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            cur = line
        else:
            cur += line
    if cur:
        chunks.append(cur)
    return chunks or ["(empty reply)"]


# ---------------------------------------------------------------- outbound attachments

SEND_FILE_RE = re.compile(r"\[SEND_FILE:\s*(?P<path>\"[^\"]+\"|'[^']+'|[^\]\n]+?)\s*\]")


def extract_send_files(reply: str) -> tuple[str, list[str]]:
    """Split `[SEND_FILE: <path>]` markers out of a Codex reply.

    Returns (clean_text, [paths]) — paths in marker order, quotes
    stripped. Markers may appear several times, on one line or many.
    clean_text has the markers removed, runs of blank lines collapsed,
    ends stripped.
    """
    paths: list[str] = []

    def _take(m: re.Match) -> str:
        raw = m.group("path").strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        paths.append(raw)
        return ""

    clean = SEND_FILE_RE.sub(_take, reply)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    # A removed marker can leave a trailing space on its line.
    clean = "\n".join(line.rstrip() for line in clean.split("\n")).strip()
    return clean, paths


def resolve_sendable_path(raw: str, roots: list[Path]) -> Path:
    """Resolve a user/model-supplied path for outbound sending.

    Returns the real absolute path. Raises ValueError when the path
    does not exist, escapes every root (``..`` or a symlink pointing
    outside all of them), or is credential/key material (never sent,
    even inside a root). ``roots[0]`` is the bridge's workdir; the rest
    are the configured ``send_roots``. Relative paths are resolved
    against the workdir only; absolute paths must land inside any root.
    """
    roots_real = [r.resolve() for r in roots]
    workdir_real = roots_real[0]
    candidate = (Path(raw).resolve() if os.path.isabs(raw)
                 else (workdir_real / raw).resolve())
    if (candidate.name in SENDABLE_DENY_NAMES
            or candidate.suffix.lower() in SENDABLE_DENY_SUFFIXES):
        raise ValueError(f"refusing to send credential/key file: {candidate.name}")
    if not candidate.is_file():
        raise ValueError(f"file not found: {raw}")
    if not any(candidate == r or r in candidate.parents for r in roots_real):
        raise ValueError(f"path is outside the sendable roots: {raw}")
    return candidate


class _MultipartBody:
    """File-like multipart/form-data body that streams the file from disk.

    urllib hands the object to http.client, which reads it in bounded
    chunks, so a 50 MB file is never held whole in memory. __len__ gives
    the total size so the caller can set Content-Length up front.
    """

    def __init__(self, boundary: str, fields: dict[str, str],
                 file_field: str, path: Path):
        self._file = open(path, "rb")
        self._size = path.stat().st_size
        head = b"".join(
            b"--" + boundary.encode("ascii") + b"\r\n"
            + (f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
               ).encode("utf-8")
            + v.encode("utf-8") + b"\r\n"
            for k, v in fields.items())
        safe_name = (path.name.replace('"', "_").replace("\r", "_")
                     .replace("\n", "_"))
        head += (b"--" + boundary.encode("ascii") + b"\r\n"
                 + (f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{safe_name}"\r\n').encode("utf-8")
                 + b"Content-Type: application/octet-stream\r\n\r\n")
        self._tail = b"\r\n--" + boundary.encode("ascii") + b"--\r\n"
        self._pieces = self._iter_pieces(head)
        self._pending = b""
        self._len = len(head) + self._size + len(self._tail)
        self._closed = False

    def _iter_pieces(self, head: bytes):
        yield head
        try:
            while True:
                chunk = self._file.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            self._file.close()
        yield self._tail

    def __len__(self) -> int:
        return self._len

    def read(self, n: int = -1) -> bytes:
        if self._closed:
            return b""
        want = n if n and n > 0 else 65536
        out = bytearray()
        while len(out) < want:
            if self._pending:
                piece, self._pending = self._pending, b""
            else:
                try:
                    piece = next(self._pieces)
                except StopIteration:
                    break
            if len(out) + len(piece) > want:
                cut = want - len(out)
                out += piece[:cut]
                self._pending = piece[cut:]
                break
            out += piece
        return bytes(out)

    def close(self) -> None:
        self._closed = True
        try:
            self._file.close()
        except Exception:
            pass


# ---------------------------------------------------------------- codex

def build_codex_cmd(cfg: dict, prompt: str, thread_id: str | None) -> list[str]:
    """First turn carries sandbox/approval flags; resume must not take -s/-C.

    Verified against codex-cli 0.164.0-alpha.9 and 0.169.0
    (`codex exec --help`, `codex exec resume --help`): exec has
    --sandbox/--approve-for-me/--skip-git-repo-check/--json; resume has
    --json/--skip-git-repo-check but no --sandbox and no --approve-for-me.
    On 0.169.0 --sandbox CONFLICTS with --approve-for-me (clap error) --
    --approve-for-me already implies the workspace-write sandbox -- so
    pass exactly one of them: --approve-for-me when enabled, else
    --sandbox <mode>.
    """
    codex = cfg["codex_bin"]
    if thread_id:
        return [codex, "exec", "resume", thread_id, "--json",
                "--skip-git-repo-check", prompt]
    cmd = [codex, "exec", "--json"]
    if cfg.get("approve_for_me", True):
        cmd.append("--approve-for-me")
    else:
        cmd += ["--sandbox", cfg["sandbox"]]
    cmd += ["--skip-git-repo-check", prompt]
    return cmd


def parse_codex_jsonl(raw: str) -> tuple[str | None, str]:
    """Return (thread_id, final_agent_text) from `codex exec --json` output.

    Event shapes vary slightly by version; this parser is deliberately
    permissive: it collects thread.started ids and any agent-message text
    it can find, and falls back to the last non-empty raw lines.
    """
    thread_id = None
    agent_parts: list[str] = []
    last_lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        last_lines.append(line[-500:])
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type") or ev.get("event") or ""
        payload = ev.get("payload") or ev
        # thread id
        if etype in ("thread.started", "thread_started"):
            thread_id = (payload.get("thread_id") or payload.get("threadId")
                         or payload.get("id") or thread_id)
        # agent message text — several shapes seen across versions
        text = None
        item = payload.get("item") if isinstance(payload, dict) else None
        if isinstance(item, dict):
            if item.get("type") in ("agent_message", "agentMessage"):
                text = item.get("text") or item.get("content")
            elif isinstance(item.get("payload"), dict):
                text = item["payload"].get("text")
        if text is None and etype in ("agent_message", "agentMessage",
                                       "item.agentMessage/delta"):
            text = payload.get("text") or payload.get("delta")
        if text is None and etype in ("item.completed", "item_completed"):
            if isinstance(item, dict) and item.get("type") in ("agent_message", "agentMessage"):
                text = item.get("text")
        if isinstance(text, str) and text.strip():
            if etype.endswith("/delta") or etype.endswith("delta"):
                agent_parts.append(text)  # streaming deltas concatenate
            elif text not in agent_parts:
                agent_parts.append(text)
    final = "".join(agent_parts).strip()
    if not final:
        # Fallback: last few raw lines, so the user sees *something*.
        final = "(Codex produced no agent message; last output lines:)\n" + \
            "\n".join(last_lines[-5:])
    return thread_id, final


# ---------------------------------------------------------------- rotation

# Prompt for the archive turn. It runs on the OLD thread (resume), its
# output is logged only — never sent to the user as a reply. {size} /
# {tokens} are pre-formatted strings, {date} is YYYY-MM-DD.
ARCHIVE_PROMPT_TEMPLATE = """[系统轮换归档 — 这不是用户消息，不要回复用户，直接执行归档。]

这个 Codex 会话已过长（会话文件 {size}，累计输入约 {tokens} token），即将关闭并开启新 thread。你的任务是把本会话中值得长期记住的内容归档进 memory palace（用 memory-palace skill 的记法），让新 thread 能从宫殿里接续工作。

只写值得长期留的，不要全文抄录对话：
- 已完成的事（带结果和关键文件路径 / 命令 / ID）
- 已做的关键决定及其理由
- 待办 / 下一步（具体到可执行的动作）
- 重要的事实（账号、路径、版本号、外部 ID）
- 绝不要写密钥、token、密码或任何凭证

写入位置（PALACE_ROOT 已在 skill 顶部的 Codex note 里写死，直接用 skill）：
- 事实 / 决定 / 待办：按 skill 结构写入对应的 wings/<wing>/<room>/（facts / events / decisions 等）文件
- 进行中的任务状态：按 skill 的 drawer 规范（§6 / references/drawer-template.md），在最相关的 room 下建 wings/<wing>/<room>/drawers/{date}-<主题>-thread.md（主题从本会话内容提炼，简短英文 kebab-case；frontmatter 按模板写，source: codex-session），并在该 room 的 hall 文件里按 skill 的格式链接这个 drawer
- 在 timeline.md 顶部加一行，指向这个 drawer（日期 + 一句话 + drawer 完整路径），否则新 thread 找不到它

完成后只回一句话确认：归档了哪些文件（列出路径）。不要输出归档内容的全文。"""

# Prepended to the user's first message on a fresh thread after rotation.
FRESH_THREAD_NOTICE = (
    "【系统提示：这是新 thread。上一个会话已归档进 memory palace。"
    "请先读 palace L1 文件（MEMORY.md 与 user / project / reference / feedback / timeline）；"
    "涉及之前的工作时，先查宫殿再回答。】\n"
)


def find_session_file(thread_id: str) -> Path | None:
    """Locate the codex session JSONL for a thread id.

    Files live under ~/.codex/sessions/<yyyy>/<mm>/<dd>/ as
    rollout-<timestamp>-<thread_id>.jsonl. Returns None when the
    thread has no session file yet (e.g. brand-new thread).
    """
    base = expand("~/.codex/sessions")
    if not base.exists():
        return None
    for p in base.rglob(f"*-{thread_id}.jsonl"):
        if p.is_file():
            return p
    return None


def inspect_thread(thread_id: str) -> tuple[int, int]:
    """Return (session_file_bytes, cumulative_input_tokens) for a thread.

    Cumulative input tokens are summed from token_usage_record events
    in the session file. Missing file or unreadable lines count as 0 —
    inspection must never break a turn.
    """
    path = find_session_file(thread_id)
    if path is None:
        return 0, 0
    try:
        size = path.stat().st_size
    except OSError:
        return 0, 0
    total_in = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") != "token_usage_record":
                    continue
                payload = ev.get("payload") or {}
                usage = payload.get("token_usage") or payload.get("usage") or {}
                try:
                    total_in += int(usage.get("input_tokens") or 0)
                except (TypeError, ValueError):
                    continue
    except OSError:
        pass
    return size, total_in


# ---------------------------------------------------------------- bridge

class Bridge:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tg = Telegram(cfg["bot_token"])
        self.state = State(expand(cfg["state_file"]))
        self.log = logging.getLogger("tg-codex-bridge")
        self.started_at = time.time()
        self.workdir = expand(cfg["workdir"])
        self.send_roots = [expand(p) for p in (cfg.get("send_roots") or [])]
        self.sendable_roots = [self.workdir, *self.send_roots]
        self.inbox = expand(cfg["inbox_dir"])
        self.queues: dict[int, queue.Queue] = {}
        self.workers: dict[int, threading.Thread] = {}
        self.current_proc: dict[int, subprocess.Popen] = {}
        self.cancel_events: dict[int, threading.Event] = {}
        self.running: dict[int, bool] = {}
        self.stop = threading.Event()
        self.codex_version: str | None = None

    # -- helpers ------------------------------------------------------
    def _queue_for(self, chat_id: int) -> queue.Queue:
        if chat_id not in self.queues:
            self.queues[chat_id] = queue.Queue()
            self.cancel_events[chat_id] = threading.Event()
            self.running[chat_id] = False
            t = threading.Thread(target=self._worker, args=(chat_id,),
                                 name=f"chat-{chat_id}", daemon=True)
            t.start()
            self.workers[chat_id] = t
        return self.queues[chat_id]

    def _codex_version(self) -> str:
        if self.codex_version is None:
            try:
                r = subprocess.run([self.cfg["codex_bin"], "--version"],
                                   capture_output=True, text=True, timeout=30)
                self.codex_version = (r.stdout or r.stderr).strip().splitlines()[0] \
                    if (r.stdout or r.stderr).strip() else "unknown"
            except Exception as e:  # noqa: BLE001 — version is best-effort
                self.codex_version = f"unavailable ({e})"
        return self.codex_version

    def _uptime(self) -> str:
        s = int(time.time() - self.started_at)
        return f"{s // 3600}h {(s % 3600) // 60}m {s % 60}s"

    # -- rotation -----------------------------------------------------
    def _rotation_limits(self) -> tuple[int, int]:
        return (int(self.cfg.get("rotation_session_max_bytes",
                                 ROTATION_SESSION_MAX_BYTES)),
                int(self.cfg.get("rotation_cumulative_input_max",
                                 ROTATION_CUMULATIVE_INPUT_MAX)))

    def _should_rotate(self, thread_id: str) -> tuple[bool, int, int]:
        """(rotate?, session_bytes, cumulative_input_tokens) for a thread."""
        size, total_in = inspect_thread(thread_id)
        max_bytes, max_input = self._rotation_limits()
        return (size > max_bytes or total_in > max_input), size, total_in

    def _run_codex_once(self, chat_id: int, cmd: list[str]
                        ) -> tuple[int, str, str, bool]:
        """Run one codex command. Returns (rc, stdout, stderr, timed_out)."""
        self.cancel_events[chat_id].clear()
        proc = subprocess.Popen(
            cmd, cwd=str(self.workdir), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.current_proc[chat_id] = proc
        self.running[chat_id] = True
        try:
            out, err = proc.communicate(timeout=int(self.cfg["codex_timeout_s"]))
            return proc.returncode, out, err, False
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return proc.returncode, out, err, True
        finally:
            self.running[chat_id] = False
            self.current_proc.pop(chat_id, None)

    def _archive_thread(self, chat_id: int, thread_id: str,
                        size: int, total_in: int) -> bool:
        """One archive turn on the old thread; True iff it completed rc=0.

        The turn's output is logged only, never sent to the user. The old
        thread file is never deleted, so a failed archive loses nothing —
        the caller keeps the thread and retries next turn.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = ARCHIVE_PROMPT_TEMPLATE.format(
            size=f"{size:,} bytes", tokens=f"{total_in:,}", date=today)
        cmd = build_codex_cmd(self.cfg, prompt, thread_id)
        self.log.info("chat=%s archive turn thread=%s size=%s input_tokens=%s",
                      chat_id, thread_id, size, total_in)
        rc, out, err, timed_out = self._run_codex_once(chat_id, cmd)
        if timed_out:
            self.log.warning("chat=%s archive turn timed out", chat_id)
            return False
        if rc != 0:
            self.log.warning("chat=%s archive turn failed rc=%s: %s",
                             chat_id, rc, (err or out or "").strip()[-500:])
            return False
        _, final = parse_codex_jsonl(out)
        self.log.info("chat=%s archive done: %s", chat_id, final[:500])
        return True

    # -- codex turn ---------------------------------------------------
    def _run_turn(self, chat_id: int, prompt: str) -> str:
        thread_id = self.state.thread_for(chat_id)
        rotation_note = ""
        if thread_id:
            rotate, size, total_in = self._should_rotate(thread_id)
            if rotate:
                self.log.info("chat=%s rotating thread=%s size=%s input_tokens=%s",
                              chat_id, thread_id, size, total_in)
                if self._archive_thread(chat_id, thread_id, size, total_in):
                    self.state.set_thread(chat_id, None)
                    thread_id = None
                    prompt = FRESH_THREAD_NOTICE + prompt
                    rotation_note = (
                        f"🔄 上个会话已过长（{size / 1_000_000:.1f}MB / "
                        f"{total_in:,} input tokens），已归档进记忆宫殿，"
                        "新 thread 从 L1 开始。")
                else:
                    rotation_note = (
                        "⚠️ 会话已过长但归档失败，本轮仍用旧 thread。"
                        "旧 thread 已保留，下一条消息会重试归档。")
        cmd = build_codex_cmd(self.cfg, prompt, thread_id)
        self.log.info("chat=%s thread=%s cmd=%s", chat_id, thread_id,
                      " ".join(cmd[:-1]) + " <prompt>")
        rc, out, err, timed_out = self._run_codex_once(chat_id, cmd)
        if timed_out:
            return ("⏱ Codex turn timed out after "
                    f"{self.cfg['codex_timeout_s']}s and was killed. "
                    "Try a smaller task, or /new to start a fresh thread.")
        if self.cancel_events[chat_id].is_set():
            return "🛑 Turn cancelled."
        if rc != 0:
            detail = (err or out or "").strip()[-1000:]
            # Resume can fail if the thread is gone (e.g. after a crash or a
            # Codex upgrade); retry once as a fresh thread.
            # Edge: if rotation just failed above, this retry drops the
            # rotation note — the retry itself is a fresh start, so the
            # note ("staying on old thread") would be wrong anyway.
            if thread_id and rc != 0:
                self.log.warning("chat=%s resume failed (rc=%s); retrying fresh",
                                 chat_id, rc)
                self.state.set_thread(chat_id, None)
                return self._run_turn(chat_id, prompt)
            return f"❌ Codex failed (exit {rc}).\n{detail}"
        new_thread, final = parse_codex_jsonl(out)
        if new_thread:
            self.state.set_thread(chat_id, new_thread)
        if rotation_note:
            final = rotation_note + "\n\n" + final
        return final

    def _deliver_reply(self, chat_id: int, reply: str) -> None:
        """Send a turn's reply: upload any [SEND_FILE:] attachments first,
        then the remaining text. A failed file never drops the text reply
        — the failure is appended to the text as a warning line. At most
        MAX_SEND_FILES_PER_REPLY files per reply; extras are reported
        as skipped.
        """
        clean, paths = extract_send_files(reply)
        notes: list[str] = []
        for raw in paths[:MAX_SEND_FILES_PER_REPLY]:
            try:
                path = resolve_sendable_path(raw, self.sendable_roots)
                self.tg.send_document(chat_id, path, caption=path.name)
                self.log.info("chat=%s sent file %s (%s bytes)",
                              chat_id, path.name, path.stat().st_size)
            except (ValueError, RuntimeError, OSError) as e:
                self.log.warning("chat=%s send file failed %r: %s",
                                 chat_id, raw, e)
                notes.append(f"⚠️ Could not send {Path(raw).name or raw}: {e}")
        if len(paths) > MAX_SEND_FILES_PER_REPLY:
            notes.append(
                f"⚠️ Only the first {MAX_SEND_FILES_PER_REPLY} files were sent; "
                f"{len(paths) - MAX_SEND_FILES_PER_REPLY} more were skipped.")
        text = clean
        if notes:
            text = (text + "\n\n" + "\n".join(notes)).strip() if text \
                else "\n".join(notes)
        if text:
            self.tg.send_message(chat_id, text)

    def _worker(self, chat_id: int) -> None:
        q = self.queues[chat_id]
        while not self.stop.is_set():
            try:
                prompt = q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                reply = self._run_turn(chat_id, prompt)
                self._deliver_reply(chat_id, reply)
            except Exception as e:  # noqa: BLE001 — a turn must never kill the bridge
                self.log.exception("chat=%s turn error", chat_id)
                try:
                    self.tg.send_message(chat_id, f"❌ Bridge error: {e}")
                except Exception:  # noqa: BLE001, S110 — Telegram itself is down
                    pass
            finally:
                q.task_done()

    # -- inbound ------------------------------------------------------
    def _download_attachment(self, chat_id: int, msg: dict) -> str | None:
        """Download a photo/document; return the local path or None."""
        file_id, name = None, None
        if msg.get("photo"):
            biggest = msg["photo"][-1]
            file_id, name = biggest["file_id"], f"{biggest['file_unique_id']}.jpg"
        elif msg.get("document"):
            doc = msg["document"]
            file_id, name = doc["file_id"], doc.get("file_name") or f"{doc['file_unique_id']}"
        if not file_id:
            return None
        try:
            remote = self.tg.get_file_path(file_id)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            dest = self.inbox / str(chat_id) / f"{today}_{file_id[:12]}_{name}"
            self.tg.download_file(remote, dest)
            self.log.info("chat=%s downloaded %s -> %s", chat_id, name, dest)
            return str(dest)
        except Exception:  # noqa: BLE001 — attachment failure must not drop the message
            self.log.exception("chat=%s attachment download failed", chat_id)
            return None

    def _handle_command(self, chat_id: int, user_id: int, text: str) -> bool:
        cmd = text.strip().split()[0].lower().lstrip("/")
        if cmd in ("start", "help"):
            self.tg.send_message(
                chat_id,
                "Codex bridge (pilot, codex exec).\n"
                "Send any message to run it through Codex on this server.\n\n"
                "Commands:\n/new — start a fresh thread\n"
                "/status — thread, queue, codex version\n"
                "/cancel — kill the running turn + drop queued messages\n"
                "/send <path> — send a file from the server as a document "
                "(inside the workdir or a configured send root, max 50 MB)\n"
                "/help — this text")
            return True
        if cmd == "new":
            self.state.set_thread(chat_id, None)
            self.tg.send_message(chat_id, "🆕 Fresh thread. Next message starts a new Codex session.")
            return True
        if cmd == "status":
            thread = self.state.thread_for(chat_id) or "(none)"
            q = self.queues.get(chat_id)
            depth = q.qsize() if q else 0
            extra = ""
            if thread != "(none)":
                size, total_in = inspect_thread(thread)
                max_bytes, max_input = self._rotation_limits()
                extra = (f"\nthread size: {size / 1_000_000:.2f}MB "
                         f"({size:,} bytes)"
                         f"\nthread input tokens: {total_in:,}"
                         f"\nrotation at: {max_bytes / 1_000_000:.0f}MB or "
                         f"{max_input:,} input tokens")
            self.tg.send_message(
                chat_id,
                "📊 Codex bridge status\n"
                f"uptime: {self._uptime()}\n"
                f"codex: {self._codex_version()}\n"
                f"thread: {thread}\n"
                f"running: {self.running.get(chat_id, False)}\n"
                f"queued: {depth}{extra}")
            return True
        if cmd == "cancel":
            self.cancel_events.setdefault(chat_id, threading.Event()).set()
            proc = self.current_proc.get(chat_id)
            if proc is not None and proc.poll() is None:
                proc.kill()
            q = self.queues.get(chat_id)
            if q is not None:
                while not q.empty():
                    try:
                        q.get_nowait()
                        q.task_done()
                    except queue.Empty:
                        break
            self.tg.send_message(chat_id, "🛑 Cancelled the running turn and cleared the queue.")
            return True
        if cmd == "send":
            rest = text[len("/send"):].strip()
            if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
                rest = rest[1:-1]
            if not rest:
                self.tg.send_message(
                    chat_id,
                    "Usage: /send <path> — send a file from the server as a "
                    "Telegram document (inside the workdir or a configured send root, max 50 MB).")
                return True
            try:
                path = resolve_sendable_path(rest, self.sendable_roots)
                self.tg.send_document(chat_id, path, caption=path.name)
                size = path.stat().st_size
                self.log.info("chat=%s sent file %s (%s bytes) via /send",
                              chat_id, path.name, size)
                self.tg.send_message(chat_id, f"📎 Sent {path.name} ({size:,} bytes).")
            except (ValueError, RuntimeError, OSError) as e:
                self.log.warning("chat=%s /send failed %r: %s", chat_id, rest, e)
                self.tg.send_message(chat_id, f"⚠️ Could not send {rest}: {e}")
            return True
        return False  # unknown /command: fall through to Codex as text

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        user = msg.get("from") or {}
        user_id = user.get("id")
        if chat_id is None:
            return
        # --- allowlist: user id AND (if configured) chat id must match.
        allowed_users = set(self.cfg.get("allowed_user_ids") or [])
        allowed_chats = set(self.cfg.get("allowed_chat_ids") or [])
        if allowed_users and user_id not in allowed_users:
            self.log.warning("ignored message from non-allowlisted user=%s chat=%s",
                             user_id, chat_id)
            return
        if allowed_chats and chat_id not in allowed_chats:
            self.log.warning("ignored message in non-allowlisted chat=%s user=%s",
                             chat_id, user_id)
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        attachment = self._download_attachment(chat_id, msg)
        if attachment:
            prefix = f"[Attachment saved to: {attachment}]\n"
            text = prefix + text if text else prefix + "(see attached file)"
        if not text:
            return  # sticker / voice / etc. with no text: ignore silently
        if text.startswith("/"):
            try:
                if self._handle_command(chat_id, user_id, text):
                    return
            except Exception:  # noqa: BLE001 — command failure must not kill polling
                self.log.exception("chat=%s command error", chat_id)
                return
        self._queue_for(chat_id).put(text)

    # -- main loop ------------------------------------------------------
    def run(self) -> None:
        self.log.info("bridge %s starting; codex=%s workdir=%s send_roots=%s",
                      VERSION, self._codex_version(), self.workdir,
                      ", ".join(str(p) for p in self.send_roots) or "(none)")
        offset = int(self.state.data.get("offset") or 0)
        while not self.stop.is_set():
            try:
                updates = self.tg.get_updates(offset, int(self.cfg["poll_timeout_s"]))
            except Exception as e:  # noqa: BLE001 — network blips must not kill the bridge
                self.log.warning("poll error: %s; retrying in 5s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                self.state.data["offset"] = offset
                try:
                    self.handle_update(update)
                except Exception:  # noqa: BLE001 — one bad update must not kill the bridge
                    self.log.exception("update handling error: %s", update)
            self.state.save()
        self.log.info("bridge stopping")


# ---------------------------------------------------------------- CLI

def single_instance_lock(path: Path):
    """Return an open file holding an exclusive flock, or None if held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.write(f"pid={os.getpid()} started={datetime.now(timezone.utc).isoformat()}\n")
    f.flush()
    return f


def setup_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Telegram <-> Codex CLI pilot bridge (codex exec, stdlib only).")
    ap.add_argument("--config", help="JSON config file (see --write-config-template)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate config, print the codex command that would run, exit without polling Telegram.")
    ap.add_argument("--write-config-template", nargs="?", const="",
                    metavar="PATH", help="Write a config template and exit. "
                    "Default path: ~/.codex/tg/config.json")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = ap.parse_args(argv)

    if args.write_config_template is not None:
        dest = expand(args.write_config_template) if args.write_config_template \
            else expand("~/.codex/tg/config.json")
        write_config_template(dest)
        return 0

    cfg_path = expand(args.config) if args.config else None
    cfg = load_config(cfg_path)

    if args.dry_run:
        print("Config OK (dry-run, no Telegram polling, no codex run).")
        print(f"  codex_bin: {cfg['codex_bin']}")
        print(f"  workdir:   {expand(cfg['workdir'])}")
        print(f"  send_roots: {cfg.get('send_roots') or '(none)'}")
        print(f"  inbox:     {expand(cfg['inbox_dir'])}")
        print(f"  state:     {expand(cfg['state_file'])}")
        print(f"  allowed_user_ids: {cfg['allowed_user_ids']}")
        print(f"  allowed_chat_ids: {cfg['allowed_chat_ids'] or '(any chat from an allowed user)'}")
        print(f"  token present: {token_is_real(cfg.get('bot_token'))}")
        print("  example first-turn command:")
        print("   " + " ".join(build_codex_cmd(cfg, "<prompt>", None)[:-1]) + " <prompt>")
        print("  example resume command:")
        print("   " + " ".join(build_codex_cmd(cfg, "<prompt>", "<thread_id>")[:-1]) + " <prompt>")
        print(f"  rotation: session > "
              f"{int(cfg.get('rotation_session_max_bytes', ROTATION_SESSION_MAX_BYTES)) / 1_000_000:.0f}MB "
              f"or cumulative input > "
              f"{int(cfg.get('rotation_cumulative_input_max', ROTATION_CUMULATIVE_INPUT_MAX)):,} tokens "
              f"-> archive into memory palace, then fresh thread")
        # Best-effort local check of the codex binary (no network, no auth).
        try:
            r = subprocess.run([cfg["codex_bin"], "--version"],
                               capture_output=True, text=True, timeout=15)
            print(f"  codex --version: {(r.stdout or r.stderr).strip() or '(no output)'}")
        except Exception as e:  # noqa: BLE001 — dry-run must still exit 0 with a warning
            print(f"  codex --version: unavailable ({e}) — install first: bash install-codex.sh")
        if not token_is_real(cfg.get("bot_token")):
            print("  NOTE: no real bot token configured yet — set bot_token in config, "
                  "TELEGRAM_BOT_TOKEN env, or ~/.codex/tg/bridge.env before running for real.")
        return 0

    if not token_is_real(cfg.get("bot_token")):
        print("ERROR: no Telegram bot token. Set bot_token in the config file, "
              "TELEGRAM_BOT_TOKEN env, or ~/.codex/tg/bridge.env (0600).",
              file=sys.stderr)
        print("Get a NEW token per server from @BotFather; do not reuse Claude's token.",
              file=sys.stderr)
        return 2

    setup_logging(expand(cfg["log_file"]))
    lock = single_instance_lock(expand(cfg["lock_file"]))
    if lock is None:
        print(f"ERROR: another bridge instance is already running "
              f"(lock held: {cfg['lock_file']})", file=sys.stderr)
        return 3

    bridge = Bridge(cfg)

    def _shutdown(signum, _frame):
        logging.getLogger("tg-codex-bridge").info(
            "received signal %s, shutting down", signum)
        bridge.stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        bridge.run()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
