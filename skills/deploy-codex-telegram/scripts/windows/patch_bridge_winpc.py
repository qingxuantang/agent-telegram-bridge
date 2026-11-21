#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply the minimal Windows patch to tg-codex-bridge.py.

Reads the local template (NEVER modifies it) and writes a Windows-patched
copy for a Windows host. Patches (all minimal, Windows-only):
  1. Guard `import fcntl` (Unix-only) -> fcntl = None on Windows.
  2. single_instance_lock: on Windows (fcntl None) skip flock; the
     Scheduled Task enforces singleton ("Do not start a new instance").
  3. subprocess calls that run codex: pass shell=True on Windows so the
     .cmd shim (codex.cmd) executes via cmd.exe. (On Windows,
     CreateProcess cannot run a .cmd directly.)
"""
import pathlib
import sys

# Template paths: the bridge template lives next to this script; adjust if
# you keep it elsewhere.
SRC = pathlib.Path(__file__).resolve().parent / "tg-codex-bridge.py"
DST = pathlib.Path(__file__).resolve().parent / "tg-codex-bridge-winpc.py"

text = SRC.read_text(encoding="utf-8")
orig = text

# 1. Guard fcntl import.
old_import = "import argparse\nimport fcntl\nimport json"
new_import = (
    "import argparse\n"
    "try:\n"
    "    import fcntl\n"
    "except ImportError:  # Windows has no fcntl\n"
    "    fcntl = None  # type: ignore[assignment]\n"
    "import json"
)
assert old_import in text, "fcntl import block not found"
text = text.replace(old_import, new_import, 1)

# 1b. ON_WINDOWS flag right after the imports (after `import time`).
old_time = "import threading\nimport time\nimport urllib.error"
new_time = (
    "import threading\nimport time\n"
    "ON_WINDOWS = sys.platform == \"win32\"\n"
    "import urllib.error"
)
assert old_time in text, "import time block not found"
text = text.replace(old_time, new_time, 1)

# 2. single_instance_lock Windows fallback.
old_lock = (
    "    f = open(path, \"a+\")\n"
    "    try:\n"
    "        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "    except OSError:\n"
    "        f.close()\n"
    "        return None\n"
)
new_lock = (
    "    f = open(path, \"a+\")\n"
    "    if fcntl is not None:\n"
    "        try:\n"
    "            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "        except OSError:\n"
    "            f.close()\n"
    "            return None\n"
    "    # Windows: no fcntl; singleton is enforced by the Scheduled Task\n"
    "    # (\"Do not start a new instance\"), so no file lock is needed.\n"
)
assert old_lock in text, "single_instance_lock block not found"
text = text.replace(old_lock, new_lock, 1)

# 3a. _codex_version subprocess.run (line ~476).
old_v = (
    "                r = subprocess.run([self.cfg[\"codex_bin\"], \"--version\"],\n"
    "                                   capture_output=True, text=True, timeout=30)"
)
new_v = (
    "                r = subprocess.run([self.cfg[\"codex_bin\"], \"--version\"],\n"
    "                                   capture_output=True, text=True, timeout=30,\n"
    "                                   shell=ON_WINDOWS)"
)
assert old_v in text, "_codex_version run not found"
text = text.replace(old_v, new_v, 1)

# 3b. _run_codex_once subprocess.Popen (line ~505).
# shell=ON_WINDOWS so the .cmd shim executes; stdin=DEVNULL because codex
# reads additional input from stdin and blocks forever on a live pipe
# (proven 2026-11-18: smoke test hung 9+ min on a live stdin; with stdin
# EOF the same turn finished in ~17s).
old_p = (
    "        proc = subprocess.Popen(\n"
    "            cmd, cwd=str(self.workdir), stdout=subprocess.PIPE,\n"
    "            stderr=subprocess.PIPE, text=True)"
)
new_p = (
    "        proc = subprocess.Popen(\n"
    "            cmd, cwd=str(self.workdir), stdout=subprocess.PIPE,\n"
    "            stderr=subprocess.PIPE, text=True, shell=ON_WINDOWS,\n"
    "            stdin=subprocess.DEVNULL)"
)
assert old_p in text, "Popen call not found"
text = text.replace(old_p, new_p, 1)

# 3c. dry-run / main subprocess.run (line ~820).
old_d = "            r = subprocess.run([cfg[\"codex_bin\"], \"--version\"],"
assert old_d in text, "dry-run run not found"
# Find the full dry-run run call to add shell=ON_WINDOWS. It spans lines;
# locate it and append shell param to its closing paren. Use the unique
# surrounding context: it is followed by capture_output line.
old_d_full = (
    "            r = subprocess.run([cfg[\"codex_bin\"], \"--version\"],\n"
    "                               capture_output=True, text=True, timeout=15)"
)
if old_d_full in text:
    new_d_full = (
        "            r = subprocess.run([cfg[\"codex_bin\"], \"--version\"],\n"
        "                               capture_output=True, text=True, timeout=15,\n"
        "                               shell=ON_WINDOWS)"
    )
    text = text.replace(old_d_full, new_d_full, 1)
else:
    # Fallback: show the actual dry-run block for manual inspection.
    print("WARNING: dry-run run call has unexpected shape; inspect manually.")
    i = text.find(old_d)
    print(text[i:i + 400])
    sys.exit(2)

assert text != orig, "no changes applied"
DST.write_text(text, encoding="utf-8")
print(f"patched copy written: {DST}")
print(f"bytes: {len(text.encode())}")
