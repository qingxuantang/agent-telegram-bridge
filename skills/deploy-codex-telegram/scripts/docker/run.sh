#!/bin/bash
# SPDX-License-Identifier: MIT
# Template: replace the <...> placeholders with your host's actual directories before use.
# All live-data mounts stay :ro by design; only the bridge workspace/state are writable.
# codex-tg 启动器（照 claude-tg 的样子单独起一个容器）。
#   run.sh               -> 常开窗口(默认):/strategy + /equity 只读常挂
#   run.sh --no-strategy -> 紧急:不挂 /strategy(revoke 用)
# 安全：所有实盘数据/日志挂载一律 :ro；唯一可写的是 codex 自己的
# workspace、codex_tg_state volume，以及与 Claude 共享的宫殿 bind-mount。
# 绝不碰 claude-tg 容器和宿主机 PM2 进程。
set -euo pipefail
DIR=$HOME/codex-telegram
SNAP=$HOME/.claude-strategy-snapshot
PALACE_HOST="/var/lib/docker/volumes/<claude-state-volume>/_data/projects/-workspace/memory"  # TODO: replace <claude-state-volume> with your companion Claude container's state volume name
PALACE_CONT=/home/codex/.codex/projects/-workspace/memory

WITH_STRATEGY=1
case "${1:-}" in
  --no-strategy) WITH_STRATEGY=0 ;;
  --with-strategy|"") WITH_STRATEGY=1 ;;
esac

DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

[ -f "$DIR/secrets.env" ] || { echo "FATAL: $DIR/secrets.env missing"; exit 1; }
set -a; . "$DIR/secrets.env"; set +a
: "${CODEX_API_KEY:?missing in secrets.env}"
: "${TELEGRAM_BOT_TOKEN:?missing in secrets.env}"

sudo test -d "$PALACE_HOST" || { echo "FATAL: palace not found at $PALACE_HOST"; exit 1; }

mkdir -p $HOME/codex-workspace "$DIR/skills"

MOUNTS=(
  -v codex_tg_state:/home/codex/.codex
  -v $HOME/codex-workspace:/workspace:rw
  -v "$DIR/skills":/home/codex/.codex/skills:ro
  -v "$DIR/entrypoint-codex.sh":/home/codex/entrypoint-codex.sh:ro
  -v "$PALACE_HOST":"$PALACE_CONT":rw
  # Optional read-only mounts of live strategy logs (one line per source):
  #   -v "<host-strategy-log-dir-1>":/logs/strategy-1:ro
  #   -v "<host-strategy-log-dir-2>":/logs/strategy-2:ro
)
if [ "$WITH_STRATEGY" = "1" ]; then
  # The snapshot is built/maintained by claude-telegram; codex-tg never rebuilds
  # it (that touches strategy data). Fail clearly if it is somehow missing.
  [ -d "$SNAP" ] || { echo "FATAL: strategy snapshot missing at $SNAP (run claude-telegram's refresh-strategy-snapshot.sh, or use --no-strategy)"; exit 1; }
  MOUNTS+=( -v "$SNAP":/strategy:ro )
  # Optional extra read-only data mounts (one block per source dir):
  #   EXTRA_DATA="<host-data-dir>"  # TODO: set to your host's data dir
  #   [ -d "$EXTRA_DATA" ] && MOUNTS+=( -v "$EXTRA_DATA":/equity/example:ro )
fi

$DOCKER rm -f codex-tg >/dev/null 2>&1 || true
$DOCKER run -d --name codex-tg \
  --restart unless-stopped \
  --cpus=1.0 --cpu-shares=512 \
  --memory=2g --memory-swap=2g \
  -e CODEX_API_KEY -e TELEGRAM_BOT_TOKEN \
  "${MOUNTS[@]}" \
  codex-telegram:latest

echo "OK: codex-tg started (with_strategy=$WITH_STRATEGY)"
