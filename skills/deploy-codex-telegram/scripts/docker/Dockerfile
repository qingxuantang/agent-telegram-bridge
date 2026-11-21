# SPDX-License-Identifier: MIT
FROM node:22-bookworm-slim

# Match container user to the host user (uid=1000, gid=1001) for clean bind-mount ownership
ARG UID=1000
ARG GID=1001

RUN apt-get update && apt-get install -y --no-install-recommends \
      tmux git curl ca-certificates python3 procps unzip rsync inotify-tools bubblewrap \
    && rm -rf /var/lib/apt/lists/*

# Codex CLI (global, installed as root). No claude-code, no bun (the bridge is
# Python stdlib only and Codex needs neither).
RUN npm install -g @openai/codex

# Runtime user. node base image ships a 'node' user at uid 1000 — remove it, then
# create 'codex' at the host user's uid/gid so bind mounts line up (same
# numeric uid as the companion Claude container's user, so the shared palace
# is read/writable by both).
RUN userdel -r node 2>/dev/null || true; \
    groupadd -g ${GID} codex; \
    useradd -m -u ${UID} -g ${GID} -s /bin/bash codex

USER codex
WORKDIR /home/codex

# Pre-create ~/.codex owned by codex so the named volume mounted here inherits
# codex ownership on first init (an empty root-owned volume would be unwritable).
# The .keep file guarantees the dir is non-empty so Docker copies it (with owner).
RUN mkdir -p /home/codex/.codex/tg /home/codex/.codex/skills \
    && touch /home/codex/.codex/.keep

COPY --chown=codex:codex tg-codex-bridge.py entrypoint-codex.sh /home/codex/
COPY --chown=codex:codex AGENTS.md /home/codex/.codex/AGENTS.md
RUN chmod +x /home/codex/entrypoint-codex.sh

WORKDIR /workspace
ENTRYPOINT ["/home/codex/entrypoint-codex.sh"]
