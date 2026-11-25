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
    under its own heading -- unless it is already present in the merged
    body (e.g. when the two sources are byte-identical), in which case
    appending it would duplicate the rules verbatim.
  * The leading comment header of an existing output file is preserved
    verbatim when present, so a hand-edited header survives rebuilds;
    a fresh output gets the built-in header below.
  * The memory-palace block is kept verbatim (flagged in the report: its
    paths may still point at Claude locations and may need a Codex pass).
  * Output must stay under Codex's 32 KiB AGENTS.md cap (32768 bytes).
  * Backs up the existing output file before overwriting.
  * Preserves any <!-- BEGIN: ... --> ... <!-- END: ... --> block found in
    the existing output file that the rebuild does not regenerate (e.g. a
    supplement block appended to the output by hand during palace wiring):
    it is re-inserted after the same anchor line it followed before, or
    appended at the end with a note if that anchor is gone. Exception: a
    stale 'from global' remainder block is tool-generated, so when the
    rebuild no longer appends it (dedup) the old copy is dropped, not
    preserved -- its content is already in the body.
  * Never touches either source file or anything under ~/.claude/.
"""
import argparse
import hashlib
import os
import re
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
- 需要发文件时：只有当用户在本轮对话里明确要过文件时，才在回复里写
  `[SEND_FILE: <path>]` 标记（一次最多 3 个文件，只发任务产物，不要发
  中间产物）；否则先用文字问用户要不要发，不要直接发。标记由桥接解析
  后去掉并上传文件，剩余文字照常发回。路径必须在工作区内，凭证文件
  永远发不出去。
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


BLOCK_RE = re.compile(r"<!-- BEGIN:.*?-->(?:.*?)<!-- END:.*?-->",
                      re.DOTALL)


def remainder_already_present(body, remainder):
    """True when the global remainder adds nothing new.

    Every non-empty line of the remainder already occurs in the merged
    body -- e.g. when the home and global sources are byte-identical,
    the remainder is just the home file minus its blocks, which is
    already in the body. (A whole-text substring check does not work
    here: the remainder glues the pre- and post-routing spans together,
    while the body has the Codex routing note between them.)
    """
    lines = [ln for ln in remainder.split("\n") if ln.strip()]
    if not lines:
        return True
    body_lines = set(body.split("\n"))
    return all(ln in body_lines for ln in lines)


HEADER_RE = re.compile(r"<!--.*?-->\s*", re.DOTALL)


def existing_header(out_path):
    """Leading comment block of an existing output file, if any.

    Returns the first <!-- ... --> comment (plus following whitespace)
    verbatim, or None when the file does not exist or does not start
    with a comment. Lets a hand-edited header survive rebuilds; a
    future change to the built-in header text does not propagate to
    existing outputs (accepted: the header is informational).
    """
    if not out_path or not os.path.exists(out_path):
        return None
    old = open(out_path, encoding="utf-8").read()
    if not old.startswith("<!--"):
        return None
    m = HEADER_RE.match(old)
    return m.group(0) if m else None


def should_preserve_block(begin, merged):
    """Whether an old-output block should be carried over.

    Blocks the rebuild regenerates (opening marker present in the
    rebuilt text) are skipped -- and so is a stale 'from global'
    remainder block: the tool itself generated it, and when the rebuild
    no longer appends it (dedup: its content is already in the body),
    the old copy must go instead of being preserved forever. Hand-
    appended blocks are unaffected.
    """
    if begin in merged:
        return False
    return not begin.startswith("<!-- BEGIN: from global")


def find_marked_blocks(text):
    """Find all <!-- BEGIN: ... --> ... <!-- END: ... --> blocks.

    Returns a list of dicts: begin (the opening marker line), block (the
    full matched text), start/end (char offsets), anchor (the last
    non-empty line before the block, or None if the block is first).
    """
    blocks = []
    for m in BLOCK_RE.finditer(text):
        begin = re.match(r"<!-- BEGIN:.*?-->", m.group(0)).group(0)
        before = text[:m.start()]
        lines = [ln for ln in before.split("\n") if ln.strip()]
        blocks.append({
            "begin": begin,
            "block": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "anchor": lines[-1] if lines else None,
        })
    return blocks


def reinsert_preserved_blocks(merged, preserved):
    """Re-insert blocks the rebuild did not regenerate.

    Each block goes back after the same anchor line it followed in the
    old output; if the anchor is gone, the block is appended at the end.
    Blocks are processed in reverse old-file order so earlier insertions
    cannot shift a later anchor. Returns (merged, notes).
    """
    notes = []
    for b in sorted(preserved, key=lambda x: x["start"], reverse=True):
        block = b["block"].strip("\n")
        anchor = b["anchor"]
        if anchor and anchor in merged:
            idx = merged.rfind(anchor) + len(anchor)
            merged = merged[:idx] + "\n\n" + block + merged[idx:]
            notes.append(f"preserved block re-inserted after anchor: "
                         f"{b['begin']}")
        else:
            merged = merged.rstrip() + "\n\n" + block + "\n"
            notes.append(f"NOTE: anchor not found for preserved block "
                         f"{b['begin']}; appended at end.")
    return merged, notes


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
    if remainder and not remainder_already_present(body, remainder):
        extra = ("\n\n<!-- BEGIN: from global ~/.claude/CLAUDE.md "
                 "(non-block remainder) -->\n" + remainder +
                 "\n<!-- END: from global ~/.claude/CLAUDE.md -->\n")
    elif remainder:
        print("global non-block remainder already present in body; "
              "skipped (dedup)")

    fixed_header = ("<!-- AGENTS.md — merged for Codex from ~/CLAUDE.md and "
                    "~/.claude/CLAUDE.md.\n"
                    "     Claude-only channel-routing block replaced by the Codex "
                    "adaptation below.\n"
                    "     Memory-palace block kept verbatim; its paths may still "
                    "reference Claude\n"
                    "     locations and may need a Codex pass. Sources untouched. -->\n\n")
    header = existing_header(args.out)
    if header is None:
        header = fixed_header
    else:
        print("header preserved from existing output")
    merged = header + body.rstrip() + "\n" + extra

    # Preserve blocks the rebuild does not regenerate (e.g. a supplement
    # appended to the output by hand): any BEGIN/END block in the existing
    # output whose opening marker is absent from the rebuilt text.
    preserved = []
    if os.path.exists(args.out):
        old = open(args.out, encoding="utf-8").read()
        for b in find_marked_blocks(old):
            if should_preserve_block(b["begin"], merged):
                preserved.append(b)
    if preserved:
        merged, notes = reinsert_preserved_blocks(merged, preserved)
        for n in notes:
            print(n)

    size = len(merged.encode("utf-8"))

    print(f"home:   {args.home} ({len(home.encode())} bytes, md5 {home_md5})")
    print(f"global: {args.glob} ({len(glob.encode())} bytes, md5 {glob_md5})")
    print(f"merged: {size} bytes (cap {CAP}) -> "
          f"{'UNDER CAP' if size <= CAP else 'OVER CAP - ABORT'}")
    print(f"routing block replaced: {routing is not None}")
    print(f"global non-block remainder appended: {bool(extra)}")
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
