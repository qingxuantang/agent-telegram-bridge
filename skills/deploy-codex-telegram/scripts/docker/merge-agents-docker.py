#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Merge a container's CLAUDE.md into a Codex AGENTS.md.

Template — adapt the paths below to your host before use.

Base: the container's /home/claude/.claude/CLAUDE.md inside the companion
Claude container (verify size/md5 on your host). It contains three blocks:
  channel-routing-rule (Telegram reply tool) -> REPLACED by the Codex note
  no-interactive-select-rule                -> kept verbatim
  quant-sandbox-rule (live-trading host)    -> kept verbatim
No memory-palace block exists in the source, so a concise one is ADDED
(PALACE_ROOT = the palace bind-mount inside codex-tg).
Then the Memory supplement (host-adapted) is appended.

Output must stay under 32768 bytes and contain ZERO
`plugin:telegram:telegram` references. Never touches the source.
"""
import hashlib
import os
import re
import sys

CAP = 32768
# TODO: set these paths to your host's actual locations before running.
SRC = "CLAUDE.md"  # path to the container's CLAUDE.md (a copy on this host)
OUT_MERGED = "AGENTS-merged.md"
OUT_FINAL = "AGENTS-final.md"
SUPP_SRC = "agents-memory-supplement.md"  # TODO: path to your memory supplement, if any

EXPECTED_SRC_MD5 = ""  # TODO: set to the md5 of your CLAUDE.md to pin the source; empty skips the check.

PALACE_ROOT = "/home/codex/.codex/projects/-workspace/memory"

CODEX_ROUTING_NOTE = """<!-- BEGIN: channel-routing-rule (Codex adaptation) -->
## Channel Routing 规则（Codex 版）
- 本 Codex agent 由 Telegram 桥接 daemon 驱动：Telegram 来的消息由桥接喂进来，
  你的最终回复文本由桥接原样发回 Telegram。
- Codex 里没有 Telegram 回复工具，不要尝试调用任何 telegram reply 工具；
  只需输出回复正文，投递由桥接负责。
- Telegram 发来的斜杠命令只是普通文本，按文本意思处理，不要当作 CLI 命令。
<!-- END: channel-routing-rule (Codex adaptation) -->"""

# Concise memory-palace block, written for this host (own wording).
MEMORY_PALACE_BLOCK = """<!-- BEGIN: memory-palace-override -->
## Memory System Rules (Memory Palace as sole memory backend)

This environment has Memory Palace installed as the only long-term memory system.

- All memory writes go through the memory-palace skill (`/memory-palace`).
  Do NOT create or modify `MEMORY.md`; do NOT create flat `.md` files in
  the `memory/` root directory.
- All memory retrieval reads from the Palace first. The L1 files
  (`MEMORY.md` is the index, plus `timeline.md` and the wing halls) are the
  fast index; `wings/` holds detail organized by topic.
- Memory Palace root: `/home/codex/.codex/projects/-workspace/memory/`
  (shared with this host's Claude; never move, copy, or import another
  machine's palace).
<!-- END: memory-palace-override -->"""


HEADER_RE = re.compile(r"<!--.*?-->\s*", re.DOTALL)


def existing_header(out_path):
    """Leading comment block of an existing output file, if any.

    Returns the first <!-- ... --> comment (plus following whitespace)
    verbatim, or None when the file does not exist or does not start
    with a comment. Lets a hand-edited header survive rebuilds; a
    future change to the built-in header text does not propagate to
    existing outputs (accepted: the header is informational).
    Same rule as merge-agents.py:existing_header.
    """
    if not out_path or not os.path.exists(out_path):
        return None
    old = open(out_path, encoding="utf-8").read()
    if not old.startswith("<!--"):
        return None
    m = HEADER_RE.match(old)
    return m.group(0) if m else None


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    src = open(SRC, encoding="utf-8").read()
    src_md5 = md5(SRC)
    if EXPECTED_SRC_MD5:
        assert src_md5 == EXPECTED_SRC_MD5, f"source md5 changed: {src_md5}"

    # Locate the channel-routing span (BEGIN -> END markers, inclusive).
    b = src.find("<!-- BEGIN: channel-routing-rule -->")
    e = src.find("<!-- END: channel-routing-rule -->")
    assert b != -1 and e != -1 and e > b, "channel-routing span not found"
    e = e + len("<!-- END: channel-routing-rule -->")
    replaced = src[b:e]
    body = src[:b] + CODEX_ROUTING_NOTE + src[e:]

    # Append the memory-palace block (source has none).
    assert "memory-palace-override" not in src, "source already has a palace block"
    body = body.rstrip() + "\n\n" + MEMORY_PALACE_BLOCK + "\n"

    fixed_header = ("<!-- AGENTS.md — merged for Codex from the "
                    "container's /home/claude/.claude/CLAUDE.md.\n"
                    "     Channel Routing section replaced by the Codex adaptation. "
                    "no-interactive-select and quant-sandbox rules kept verbatim.\n"
                    "     Memory-palace block added (source had none); PALACE_ROOT "
                    "is the shared palace bind-mount. Source untouched. -->\n\n")
    header = existing_header(OUT_MERGED)
    if header is None:
        header = fixed_header
    else:
        print("header preserved from existing output")
    merged = header + body.rstrip() + "\n"
    msize = len(merged.encode("utf-8"))
    assert msize <= CAP, f"merged over cap: {msize}"
    assert "plugin:telegram:telegram" not in merged, "plugin ref remains in merged"

    with open(OUT_MERGED, "w", encoding="utf-8") as f:
        f.write(merged)
    print(f"merged: {msize} bytes, md5 {md5(OUT_MERGED)}")
    print(f"  routing span replaced: {len(replaced.encode())} bytes -> "
          f"Codex note ({len(CODEX_ROUTING_NOTE.encode())} bytes)")
    print(f"  source md5 still {md5(SRC)}")

    # Memory supplement, host-adapted.
    # TODO: adapt the two replacements below to your own supplement file;
    # the search strings are examples from the original host.
    supp = open(SUPP_SRC, encoding="utf-8").read()
    supp = supp.replace(
        "Palace 根目录固定为 `~/.claude/projects/-<host-home>/memory/`，与本机 Claude 共用同一份，**不要**移动、复制或另建 palace；其他机器的 palace 是另一个 agent 的记忆，**绝不能**导进来。",
        "Palace 根目录固定为 `/home/codex/.codex/projects/-workspace/memory/`，与本机 Claude 共用同一份，**不要**移动、复制或另建 palace；其他机器的 palace 是另一个 agent 的记忆，**绝不能**导进来。")
    supp = supp.replace("`~/.codex/skills/memory-palace/`",
                        "`/home/codex/.codex/skills/memory-palace/`")
    assert "/home/codex/.codex/projects/-workspace/memory/" in supp, "supp PALACE_ROOT not replaced"
    assert "~/.claude/projects/-<host-home>/memory/" not in supp, "supp still has old path"
    # TODO: add an assert here if your supplement must not name other hosts.

    final = merged.rstrip() + "\n\n" + supp.rstrip() + "\n"
    fsize = len(final.encode("utf-8"))
    assert fsize <= CAP, f"final over cap: {fsize}"
    assert "plugin:telegram:telegram" not in final, "plugin ref remains in final"
    with open(OUT_FINAL, "w", encoding="utf-8") as f:
        f.write(final)
    print(f"final (merged+supplement): {fsize} bytes, md5 {md5(OUT_FINAL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
