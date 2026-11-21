#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Merge Claude identity/rules into a Codex AGENTS.md.

Usage (on the target server, as the agent user):
    python3 merge-agents.py [--apply] [--home PATH] [--global PATH] [--out PATH]

Default (dry-run): prints what would be written and exits without changing
anything. Pass --apply to write.

Sources:
  --home   ~/CLAUDE.md            (live identity: persona, who is served,
                                   do/don't, plus channel-routing and
                                   memory-palace blocks)
  --global ~/.claude/CLAUDE.md   (global rules: same two blocks, shorter)

Merge rules:
  * Base is the home file, in full, in order.
  * The Claude-only channel-routing block (between
    <!-- BEGIN: channel-routing-rule --> and
    <!-- END: channel-routing-rule -->) is replaced by a short Codex note:
    under Codex there is no Telegram reply tool -- the bridge daemon owns
    delivery and the model just produces the reply text.
  * The global file's blocks duplicate the home file's fuller versions, so
    they are skipped; any non-block remainder in the global file is appended
    under its own heading.
  * The memory-palace block is kept verbatim (flagged in the report: its
    paths may still point at Claude locations and may need a Codex pass).
  * Output must stay under Codex's 32 KiB AGENTS.md cap (32768 bytes).
  * Backs up the existing output file before overwriting.
  * Never touches either source file or anything under ~/.claude/.
"""
import argparse
import hashlib
import os
import sys

CAP = 32768
ROUTING_BEGIN = "<!-- BEGIN: channel-routing-rule -->"
ROUTING_END = "<!-- END: channel-routing-rule -->"

CODEX_ROUTING_NOTE = """<!-- BEGIN: channel-routing-rule (Codex adaptation) -->
## Channel Routing 规则（Codex 版）
- 本 Codex agent 由 Telegram 桥接 daemon 驱动：Telegram 来的消息由桥接喂进来，
  你的最终回复文本由桥接原样发回 Telegram。
- Codex 里没有 Telegram 回复工具，不要尝试调用任何 telegram reply 工具；
  只需输出回复正文，投递由桥接负责。
- Telegram 发来的斜杠命令只是普通文本，按文本意思处理，不要当作 CLI 命令。
<!-- END: channel-routing-rule (Codex adaptation) -->"""


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def split_blocks(text):
    """Split text into (pre, routing_block_or_None, post) by routing markers."""
    begin = text.find(ROUTING_BEGIN)
    end = text.find(ROUTING_END)
    if begin == -1 or end == -1 or end < begin:
        return text, None
    end = end + len(ROUTING_END)
    return text[:begin], text[begin:end], text[end:]


def non_block_remainder(text):
    """Text outside the routing block and the memory-palace block."""
    # Remove routing block.
    pre, _, post = split_blocks(text)
    rest = pre + post
    # Remove memory-palace block.
    begin = rest.find("<!-- BEGIN: memory-palace-override -->")
    end = rest.find("<!-- END: memory-palace-override -->")
    if begin != -1 and end != -1 and end > begin:
        end = end + len("<!-- END: memory-palace-override -->")
        rest = rest[:begin] + rest[end:]
    return rest.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Write the merged file (default is dry-run).")
    ap.add_argument("--home", default=os.path.expanduser("~/CLAUDE.md"))
    ap.add_argument("--global", dest="glob",
                    default=os.path.expanduser("~/.claude/CLAUDE.md"))
    ap.add_argument("--out", default=os.path.expanduser("~/.codex/AGENTS.md"))
    args = ap.parse_args()

    for p in (args.home, args.glob):
        if not os.path.exists(p):
            print(f"missing source: {p}", file=sys.stderr)
            return 1

    home = open(args.home, encoding="utf-8").read()
    glob = open(args.glob, encoding="utf-8").read()
    home_md5, glob_md5 = md5(args.home), md5(args.glob)

    pre, routing, post = split_blocks(home)
    if routing is None:
        print("WARN: no routing block found in home file; "
              "appending Codex note at end instead.", file=sys.stderr)
        body = home.rstrip() + "\n\n" + CODEX_ROUTING_NOTE + "\n"
    else:
        body = pre + CODEX_ROUTING_NOTE + post

    remainder = non_block_remainder(glob)
    extra = ""
    if remainder:
        extra = ("\n\n<!-- BEGIN: from global ~/.claude/CLAUDE.md "
                 "(non-block remainder) -->\n" + remainder +
                 "\n<!-- END: from global ~/.claude/CLAUDE.md -->\n")

    header = ("<!-- AGENTS.md — merged for Codex from ~/CLAUDE.md and "
              "~/.claude/CLAUDE.md.\n"
              "     Claude-only channel-routing block replaced by the Codex "
              "adaptation below.\n"
              "     Memory-palace block kept verbatim; its paths may still "
              "reference Claude\n"
              "     locations and may need a Codex pass. Sources untouched. -->\n\n")
    merged = header + body.rstrip() + "\n" + extra
    size = len(merged.encode("utf-8"))

    print(f"home:   {args.home} ({len(home.encode())} bytes, md5 {home_md5})")
    print(f"global: {args.glob} ({len(glob.encode())} bytes, md5 {glob_md5})")
    print(f"merged: {size} bytes (cap {CAP}) -> "
          f"{'UNDER CAP' if size <= CAP else 'OVER CAP - ABORT'}")
    print(f"routing block replaced: {routing is not None}")
    print(f"global non-block remainder appended: {bool(remainder)}")
    if size > CAP:
        return 1

    if not args.apply:
        print("DRY-RUN: nothing written. Pass --apply to write.")
        return 0

    if os.path.exists(args.out):
        bak = args.out + ".bak-20261115"
        with open(args.out, "rb") as f, open(bak, "wb") as g:
            g.write(f.read())
        print(f"backup: existing {args.out} -> {bak}")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(merged)
    print(f"wrote: {args.out} ({size} bytes, md5 {md5(args.out)})")
    print(f"sources unchanged: home md5 {md5(args.home)} "
          f"(was {home_md5}), global md5 {md5(args.glob)} (was {glob_md5})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
