# Agent Telegram Bridge

<p align="center">
  <a href="#english">English</a> | <a href="#中文">中文</a>
</p>

---

<a id="english"></a>

## English

Open-source skills for deploying agentic coding CLIs on remote servers with
Telegram integration — and for migrating a full OpenClaw agent over to
Claude Code.

> **Background**: As of April 5, 2026, Anthropic blocked OpenClaw from using
> Claude Pro/Max subscriptions. This repository started as a first-party
> alternative using Claude Code's native Channels feature, and now covers a
> second agent framework — OpenAI Codex CLI — under the same Telegram-bridge
> idea. The repo is now **Agent Telegram Bridge**: one home for
> Telegram deployment skills across agent frameworks, with more frameworks
> addable in the same shape later.

### Skills

#### 🚀 [deploy-telegram](skills/deploy-telegram/SKILL.md) — Claude Code — **multi-platform: Linux + macOS + Windows**

Deploy a Claude Code daemon that listens for messages over Telegram and
replies through the same channel. The daemon runs as a separate process from
any local interactive Claude Code (e.g. your Desktop app), sharing
`~/.claude/skills/` and `~/CLAUDE.md` so personality is consistent.

Structured as a **thin core + platform overlays + shared references**:

```
skills/deploy-telegram/
├── SKILL.md                              # Core: contract, architecture, do-not, platform router
├── platforms/
│   ├── linux.md                          # apt + systemd + tmux + GNU sed; remote SSH + local self-execution
│   ├── macos.md                          # brew + launchd (wrapper pattern) + tmux + BSD sed; Desktop App scope isolation
│   └── windows.md                        # winget + Task Scheduler + FileSystemWatcher + PowerShell hooks
└── references/
    ├── architecture-and-design.md        # bun-as-child-of-claude, server.ts patch, hooks, inbox mover
    ├── claude-md-rules.md                # channel-routing-rule + no-interactive-select-rule (both universal)
    ├── pairing-and-access.md             # /telegram:access pair + policy allowlist + access.json
    ├── process-supervisors.md            # systemd vs launchd vs Task Scheduler comparison
    ├── post-deploy-hardening.md          # 9 production incidents + their permanent fixes
    └── troubleshooting.md                # cross-platform decision tree
```

**Validation status**:
| Platform | Tested on | Status |
|---|---|---|
| Linux | Ubuntu 22.04 / 24.04 across 4 production servers (HK / SG / JP) | Stable since 2026-04-07 |
| macOS | Mac mini (Darwin 24.6.0 arm64), macOS 15 (Sequoia) | Stable since 2026-05-14 with 4 hardening fixes |
| Windows | Windows 11 Pro 26100 + Desktop app 2.1.142 + Bun 1.3.14 | End-to-end verified 2026-05-21 |

**Common requirements** (universal across platforms):
- Telegram bot token from `@BotFather /newbot` — **must be a fresh bot**
- Network access to `api.telegram.org`, `github.com`, `api.anthropic.com` (mainland-China networks blocked)
- Existing Claude Code authentication (Desktop app login on Windows, or `claude setup-token` token on Linux/macOS)

**Platform-specific notes**:
- **Linux**: supports both remote-SSH mode (controller agent → server) and local self-execution mode (e.g. OpenClaw migrating to CC on the same host)
- **macOS**: requires `--scope local` plugin install to avoid Desktop App contending for the bot token (verified incident)
- **Windows**: reuses the bundled `claude.exe` from `%APPDATA%\Claude\claude-code\<version>\` — no separate npm install needed; PowerShell hooks (no Python dependency); no tmux automation (operator dismisses first-launch dialogs at the keyboard)

For the full Claude story — key learnings, the Telegram Channel reply-tool
rule, and the architecture diagram — see
[skills/deploy-telegram/SKILL.md](skills/deploy-telegram/SKILL.md) and its
`references/` (in particular `architecture-and-design.md`,
`post-deploy-hardening.md`, and `troubleshooting.md`).

#### 🤖 [deploy-codex-telegram](skills/deploy-codex-telegram/SKILL.md) — Codex CLI — **multi-platform: Linux + macOS + Windows + Docker**

Deploy a Codex CLI agent that listens for messages over Telegram and replies
through the same channel.

**Architecture in one sentence:** Codex has no `--channels` flag and no
official Telegram plugin, so a small Python bridge daemon (stdlib only) owns
the Telegram side and drives Codex from the outside — the exact inverse of
the Claude architecture, where the CLI owns Telegram. The bridge long-polls
`getUpdates`, enforces a one-user allowlist, maps each chat to a Codex
thread, runs `codex exec [--json]` / `codex exec resume`, and delivers the
reply itself — which also removes, by construction, the Claude design's
worst failure (replies generated but never delivered because the model
forgot to call the reply tool). Full detail:
[skills/deploy-codex-telegram/references/architecture-and-design.md](skills/deploy-codex-telegram/references/architecture-and-design.md).

```
skills/deploy-codex-telegram/
├── SKILL.md                              # Core: contract, architecture, do-not, platform router
├── platforms/
│   ├── linux.md                          # systemd --user + daily 04:00 restart timer
│   ├── macos.md                          # launchd + wrapper scripts
│   ├── windows.md                        # Task Scheduler + Windows bridge fork
│   └── docker.md                         # codex-tg container; read-only trading mounts
├── references/
│   ├── architecture-and-design.md        # bridge-outside-codex; why no 1:1 replacement; app-server outlook
│   ├── bridge-config.md                  # config.json fields; bridge.env / codex.env (0600); Windows diffs
│   ├── agents-md-merge.md                # CLAUDE.md → AGENTS.md merge rules; 32 KiB cap
│   ├── sandbox-and-approval.md           # danger-full-access decision; --approve-for-me conflicts
│   ├── thread-rotation.md                # 1 MB / 5M-token rotation; palace archive flow
│   ├── process-supervisors.md            # systemd vs launchd vs Task Scheduler vs docker restart
│   ├── post-deploy-hardening.md          # 8 production incidents + their permanent fixes
│   └── troubleshooting.md                # cross-platform decision tree
└── scripts/
    ├── systemd/                          # tg-codex-bridge.py, config example, systemd units, migrate script
    ├── launchd/                          # macOS plists + wrapper scripts
    ├── windows/                          # Windows bridge fork, task-creation + restart scripts
    └── docker/                           # codex-tg container build/run scripts
```

**Validation status**: 9 machines deployed and verified end-to-end —
Linux servers (systemd, including root-run hosts), Docker containers on
trading hosts (read-only data mounts, trading processes untouched), macOS
(launchd), and Windows 11 (Task Scheduler). Codex 0.169.0 on the Linux/macOS
hosts, 0.170.0 on Windows and in the containers. Bridge version
`v0.2.0-rotation` (Python stdlib only); per-turn watchdog 1800 s; rotation
at 1 MB session file / 5M cumulative input tokens; default model
`gpt-6.1-sol`.

**Common requirements** (universal across platforms):
- Telegram bot token from `@BotFather /newbot` — **must be a fresh bot per
  machine** (never reuse the Claude bot's token — two pollers cause 409
  Conflict)
- Network access to `api.telegram.org`, `github.com`,
  `api.openai.com` / `chatgpt.com`, `registry.npmjs.org` (mainland-China
  networks blocked)
- Codex authentication per machine: service-account API key (recommended
  for unattended servers — one key per machine, with a spend cap and
  alerts) or `codex login --device-auth`
- Existing Claude Code identity on the machine (for the `CLAUDE.md` →
  `AGENTS.md` merge; see
  [skills/deploy-codex-telegram/references/agents-md-merge.md](skills/deploy-codex-telegram/references/agents-md-merge.md))

**Quick start — 5 steps** (full procedure in
[skills/deploy-codex-telegram/SKILL.md](skills/deploy-codex-telegram/SKILL.md)):

1. Install Codex on the target machine and authenticate it (API key or
   device login — done by the operator, never by a script).
2. Verify by hand: `codex exec --json` one turn, `codex exec resume` the
   thread back, `codex app-server --help` (existence check only).
3. Migrate the identity: run the migrate script dry-run first, read the
   report, then `--apply` (copies skills and merges `CLAUDE.md` →
   `~/.codex/AGENTS.md`; Claude is untouched and keeps running).
4. Configure the bridge: fresh BotFather bot token into
   `~/.codex/tg/bridge.env` (0600), copy the config example to
   `~/.codex/tg/config.json`, set the allowlist to exactly one user id,
   then `python3 tg-codex-bridge.py --dry-run --config
   ~/.codex/tg/config.json`.
5. Install the platform supervisor unit (systemd / launchd / Task Scheduler
   / Docker) and message the bot — the bridge answers from the new thread.

**Why there is no Codex `deploy.sh`:** the Codex deployment has too many
steps and too much platform divergence (flag sets differ by Codex version,
supervisors differ per OS, sandbox behaviour differs between bare metal and
containers) for a single script to be honest. The skill is the source of
truth; the per-platform guides and scripts under
`skills/deploy-codex-telegram/` are the executable form.

#### 🔄 [migrate-openclaw](skills/migrate-openclaw/SKILL.md)

Migrate a complete OpenClaw agent to Claude Code — not just files, but the
full identity, memory, and deployment. See
[skills/migrate-openclaw/SKILL.md](skills/migrate-openclaw/SKILL.md) for the
full description (inventory, `CLAUDE.md` merge, typed memory parsing, skill
copy, per-plugin guidance, dual-track deployment, automation-policy tags
🤖 AUTO / 🟡 AUTO-FIRST / 👤 MANUAL / 🚫 DO NOT).

### Design: Agent-First

These skills are designed to be **executed by AI agents**, not read by
humans. Any agent (Claude Code, OpenClaw, Codex) can:

1. Read the SKILL.md file
2. Collect the required inputs from the user
3. Execute each step sequentially
4. Handle errors using the troubleshooting decision tree

The skills use deterministic shell commands — no judgment calls required.

### Quick Start

#### For Claude Code users

```
# Option 1: Reference directly
"Use the skill at /path/to/skills/deploy-telegram/SKILL.md to deploy Claude Code on my server"

# Option 2: Copy to project
cp -r skills/deploy-telegram .claude/skills/
```

#### For Codex users

```
# Option 1: Reference directly
"Use the skill at /path/to/skills/deploy-codex-telegram/SKILL.md to deploy Codex on my server"

# Option 2: Copy to project
cp -r skills/deploy-codex-telegram .codex/skills/
```

#### For humans (manual deployment)

<details>
<summary>Click to expand manual steps (Claude)</summary>

1. Create a Telegram bot via @BotFather → get bot token
2. Run `claude setup-token` on a machine with a browser → get OAuth token
3. SSH into your server
4. Follow Steps 1–9 in [deploy-telegram/SKILL.md](skills/deploy-telegram/SKILL.md)
5. Message your bot on Telegram → get pairing code
6. Run the pairing command from the skill

</details>

<details>
<summary>Click to expand manual steps (Codex)</summary>

1. Create a Telegram bot via @BotFather → get a fresh bot token (one per machine)
2. Install Codex on the target machine; authenticate with an API key or `codex login --device-auth`
3. Run the migrate script dry-run, then `--apply`
4. Copy the bridge config example to `~/.codex/tg/config.json`, put the token in `~/.codex/tg/bridge.env` (0600), set the allowlist to your own user id
5. Dry-run the bridge, then install the supervisor unit for your platform
6. Message your bot on Telegram

</details>

### Safety and redaction

- **This repository contains no real credentials.** Every token, key,
  password, user id, IP address, hostname, bot name, personal name, email,
  and domain in this repo is a placeholder (e.g. `PASTE_TOKEN_HERE`,
  `YOUR_TELEGRAM_USER_ID`, `1234567890` as an example id). Never commit a
  real secret; the packaging checklist is in [PACKAGING-NOTES.md](PACKAGING-NOTES.md).
- **Dual-track by default.** Deploying Codex never stops, deletes, or
  reconfigures Claude Code on the same machine. Rollback is stopping the
  Codex bridge and going back to the Claude group.
- **Never touch** running trading processes, production websites, or
  protected data mounts. Live trading data is mounted read-only wherever a
  container is involved.
- The bridge is a remote shell: the allowlist (one operator user id) is the
  only gate. Do not make the bot public; do not add it to unrelated groups.
- Bridge logs contain message text and stay on the machine.

### Proposals

Design proposals awaiting an owner decision live under
[`docs/proposals/`](docs/proposals/):

- [Telegram file upload (outbound attachments)](docs/proposals/PROPOSAL_telegram-file-upload-2026-11-24.md) — let the Codex bridge send files the server produced back to the operator in Telegram (`[SEND_FILE: <path>]` marker + `/send <path>` command), with path confinement and a credential-file denylist.

---

<a id="中文"></a>

## 中文

开源技能集：在远程服务器上部署 agentic 编程 CLI 并通过 Telegram 远程访问，
以及把一整个 OpenClaw agent（Soul、Memory、Skills、workspace 身份）完整迁移
到 Claude Code。

> **背景**：2026 年 4 月 5 日起，Anthropic 封禁了 OpenClaw 使用 Claude Pro/Max
> 订阅。本仓库最初是基于 Claude Code 原生 Channels 功能的第一方替代方案，
> 现在覆盖了第二个 agent 框架——OpenAI Codex CLI，沿用同一套 Telegram 桥接思路。
> 本仓库现为 **Agent Telegram Bridge**：多个 agent 框架的 Telegram 部署技能的
> 统一存放处，未来可按同样结构加入更多框架。

### 技能列表

#### 🚀 [deploy-telegram](skills/deploy-telegram/SKILL.md) — Claude Code — **多平台：Linux + macOS + Windows**

部署一个 Claude Code daemon 进程，监听 Telegram 入站消息并通过同样的 channel
回复。Daemon 作为**独立进程**跟你本地交互用的 Claude Code（比如桌面版）
**并行**运行，共享 `~/.claude/skills/` 和 `~/CLAUDE.md`，所以人格 / 技能一致，
但 context 隔离、互不打扰。

结构是**薄核心 + 平台 overlay + 共享 references**：

```
skills/deploy-telegram/
├── SKILL.md                              # 核心：契约、架构、do-not、平台路由
├── platforms/
│   ├── linux.md                          # apt + systemd + tmux + GNU sed；支持 remote SSH + local self-execution
│   ├── macos.md                          # brew + launchd（wrapper 模式）+ tmux + BSD sed；Desktop App 隔离
│   └── windows.md                        # winget + Task Scheduler + FileSystemWatcher + PowerShell hooks
└── references/
    ├── architecture-and-design.md        # bun 作为 claude 子进程、server.ts 补丁、hooks、inbox 搬运
    ├── claude-md-rules.md                # channel-routing-rule + no-interactive-select-rule（两条都跨平台必装）
    ├── pairing-and-access.md             # /telegram:access pair + policy allowlist + access.json
    ├── process-supervisors.md            # systemd vs launchd vs Task Scheduler 对比
    ├── post-deploy-hardening.md          # 9 个生产事故 + 永久修复
    └── troubleshooting.md                # 跨平台诊断决策树
```

**验证状态**：
| 平台 | 验证环境 | 状态 |
|---|---|---|
| Linux | Ubuntu 22.04 / 24.04，4 台生产服务器（HK / SG / JP） | 2026-04-07 以来稳定运行 |
| macOS | Mac mini（Darwin 24.6.0 arm64）、macOS 15 Sequoia | 2026-05-14 以来稳定运行，含 4 个生产 hardening 修复 |
| Windows | Windows 11 Pro 26100 + Desktop app 2.1.142 + Bun 1.3.14 | 2026-05-21 端到端实测通过 |

**通用前置条件**（三平台共用）：
- `@BotFather /newbot` 创建的**全新** Telegram bot token
- 能访问 `api.telegram.org`、`github.com`、`api.anthropic.com`（大陆网络过不了）
- 已有 Claude Code 认证（Windows：Desktop app 登录态；Linux/macOS：`claude setup-token` 获取 token）

**平台特有要点**：
- **Linux**：支持远端 SSH 模式（控制端 agent → 服务器）和本地 self-execution 模式（例如 OpenClaw 在原机迁移到 CC）
- **macOS**：必须用 `--scope local` 装插件以避免 Desktop App 抢 bot token（生产事故已验证）
- **Windows**：复用 Desktop app 自带的 `claude.exe`（路径 `%APPDATA%\Claude\claude-code\<version>\`），不需要单独装 CLI；PowerShell hooks（不依赖 Python）；不做 tmux 仿真——首次对话框由 operator 在键盘前手动按掉

Claude 部分的完整内容——踩坑经验、Telegram Channel 回复工具规则、架构图——见
[skills/deploy-telegram/SKILL.md](skills/deploy-telegram/SKILL.md) 及其
`references/`（重点是 `architecture-and-design.md`、`post-deploy-hardening.md`、
`troubleshooting.md`）。

#### 🤖 [deploy-codex-telegram](skills/deploy-codex-telegram/SKILL.md) — Codex CLI — **多平台：Linux + macOS + Windows + Docker**

部署一个 Codex CLI agent，监听 Telegram 入站消息并通过同样的 channel 回复。

**架构一句话：** Codex 没有 `--channels` 参数、没有官方 Telegram 插件，所以由一个
Python 小桥接 daemon（只用标准库）拥有 Telegram 侧、在外面驱动 Codex——与 Claude
架构正好相反（Claude 是 CLI 拥有 Telegram）。桥接长轮询 `getUpdates`、执行单用户
allowlist、把每个 chat 映射到一个 Codex thread、跑 `codex exec [--json]` /
`codex exec resume`，并自己确定性地投递回复——这也按构造消除了 Claude 设计最严重
的故障（模型生成了回复却忘了调 reply 工具，用户收不到）。完整说明见
[skills/deploy-codex-telegram/references/architecture-and-design.md](skills/deploy-codex-telegram/references/architecture-and-design.md)。

```
skills/deploy-codex-telegram/
├── SKILL.md                              # 核心：契约、架构、do-not、平台路由
├── platforms/
│   ├── linux.md                          # systemd --user + 每日 04:00 重启 timer
│   ├── macos.md                          # launchd + wrapper 脚本
│   ├── windows.md                        # Task Scheduler + Windows 版桥接
│   └── docker.md                         # codex-tg 容器；实盘数据只读挂载
├── references/
│   ├── architecture-and-design.md        # 桥在外驱动 Codex；为何不能 1:1 平替；app-server 展望
│   ├── bridge-config.md                  # config.json 字段；bridge.env / codex.env（0600）；Windows 差异
│   ├── agents-md-merge.md                # CLAUDE.md → AGENTS.md 合并规则；32 KiB 上限
│   ├── sandbox-and-approval.md           # danger-full-access 决策；--approve-for-me 冲突
│   ├── thread-rotation.md                # 1 MB / 5M token 轮换；归档进宫殿流程
│   ├── process-supervisors.md            # systemd vs launchd vs Task Scheduler vs docker restart
│   ├── post-deploy-hardening.md          # 8 个生产事故 + 永久修复
│   └── troubleshooting.md                # 跨平台诊断决策树
└── scripts/
    ├── systemd/                          # tg-codex-bridge.py、配置模板、systemd 单元、迁移脚本
    ├── launchd/                          # macOS plist + wrapper 脚本
    ├── windows/                          # Windows 版桥接、任务创建与重启脚本
    └── docker/                           # codex-tg 容器构建/运行脚本
```

**验证状态**：9 台机器部署完成并经实测——Linux 服务器（systemd，含 root 运行主机）、
实盘主机上的 Docker 容器（数据只读挂载、实盘进程未动）、macOS（launchd）、Windows 11
（Task Scheduler）。Codex 0.169.0（Linux/macOS 主机）、0.170.0（Windows 与容器）。
桥版本 `v0.2.0-rotation`（Python 只用标准库）；单轮 watchdog 1800 秒；1 MB 会话文件 /
5M 累计输入 token 轮换；默认模型 `gpt-6.1-sol`。

**通用前置条件**（三平台共用）：
- `@BotFather /newbot` 创建的**全新** Telegram bot token——**每台机器一个**（绝不复用
  Claude 的 bot token，否则两个 poller 抢 `getUpdates` 会出 409 冲突）
- 能访问 `api.telegram.org`、`github.com`、`api.openai.com` / `chatgpt.com`、
  `registry.npmjs.org`（大陆网络过不了）
- 每台机器单独的 Codex 认证：service-account API key（无人值守服务器推荐——每台一个
  key、设消费上限 + 告警）或 `codex login --device-auth`
- 目标机器上已有 Claude Code 身份（供 `CLAUDE.md` → `AGENTS.md` 合并；见
  [skills/deploy-codex-telegram/references/agents-md-merge.md](skills/deploy-codex-telegram/references/agents-md-merge.md)）

**快速上手 5 步**（完整流程见
[skills/deploy-codex-telegram/SKILL.md](skills/deploy-codex-telegram/SKILL.md)）：

1. 在目标机器上安装 Codex 并认证（API key 或 device 登录——由操作人做，脚本不做）。
2. 手工验证三步：`codex exec --json` 跑一轮、`codex exec resume` 续回该 thread、
   `codex app-server --help`（只确认命令存在）。
3. 迁移身份：先跑迁移脚本 dry-run 看报告，再 `--apply`（复制 skills、合并
   `CLAUDE.md` → `~/.codex/AGENTS.md`；Claude 原样不动、继续运行）。
4. 配桥接：全新 BotFather bot token 写进 `~/.codex/tg/bridge.env`（0600），配置模板拷成
   `~/.codex/tg/config.json`，allowlist 只放一个用户 id，然后
   `python3 tg-codex-bridge.py --dry-run --config ~/.codex/tg/config.json`。
5. 按平台装 supervisor 单元（systemd / launchd / Task Scheduler / Docker），给 bot
   发消息——桥接从新 thread 回复。

**为什么没有 Codex 版 `deploy.sh`：** Codex 部署步骤多、平台差异大（Codex 版本不同 flag
集不同、各 OS 的 supervisor 不同、裸机与容器的沙箱行为不同），一个脚本装不下还容易
误导。Skill 是唯一事实来源；`skills/deploy-codex-telegram/` 下的分平台指南与脚本是
可执行形态。

#### 🔄 [migrate-openclaw](skills/migrate-openclaw/SKILL.md)

把一整个 OpenClaw agent 完整迁移到 Claude Code —— 不只是文件转换，还包括身份、
记忆、部署全流程。完整说明见
[skills/migrate-openclaw/SKILL.md](skills/migrate-openclaw/SKILL.md)（清点、CLAUDE.md
合并、typed memory 解析、skill 复制、逐插件迁移建议、双轨部署、自动化策略标签
🤖 AUTO / 🟡 AUTO-FIRST / 👤 MANUAL / 🚫 DO NOT）。

### 设计理念：Agent-First（代理优先）

这些技能专为 **AI 代理执行**而设计，而非供人类阅读。任何代理（Claude Code、OpenClaw、Codex）都可以：

1. 读取 SKILL.md 文件
2. 向用户收集所需输入参数
3. 按顺序执行每个步骤
4. 使用故障排除决策树处理错误

所有技能使用确定性的 Shell 命令 —— 不需要任何主观判断。

### 快速开始

#### Claude Code 用户

```
# 方式一：直接引用
"使用 /path/to/skills/deploy-telegram/SKILL.md 这个 skill 在我的服务器上部署 Claude Code"

# 方式二：复制到项目
cp -r skills/deploy-telegram .claude/skills/
```

#### Codex 用户

```
# 方式一：直接引用
"使用 /path/to/skills/deploy-codex-telegram/SKILL.md 这个 skill 在我的服务器上部署 Codex"

# 方式二：复制到项目
cp -r skills/deploy-codex-telegram .codex/skills/
```

#### 手动部署

<details>
<summary>点击展开手动步骤（Claude）</summary>

1. 通过 @BotFather 创建 Telegram Bot → 获取 Bot Token
2. 在有浏览器的机器上运行 `claude setup-token` → 获取 OAuth Token
3. SSH 登录服务器
4. 按照 [deploy-telegram/SKILL.md](skills/deploy-telegram/SKILL.md) 中的步骤 1–9 执行
5. 在 Telegram 上给你的 Bot 发消息 → 获取配对码
6. 执行 Skill 中的配对命令

</details>

<details>
<summary>点击展开手动步骤（Codex）</summary>

1. 通过 @BotFather 创建 Telegram Bot → 获取全新 bot token（每台机器一个）
2. 在目标机器上安装 Codex；用 API key 或 `codex login --device-auth` 认证
3. 先跑迁移脚本 dry-run，再 `--apply`
4. 把桥接配置模板拷成 `~/.codex/tg/config.json`，token 写进 `~/.codex/tg/bridge.env`（0600），allowlist 只放自己的用户 id
5. 先 dry-run 桥接，再按平台装 supervisor 单元
6. 在 Telegram 上给你的 Bot 发消息

</details>

### 安全与脱敏声明

- **本仓库不含任何真实凭证。** 仓库里所有 token、key、密码、用户 id、IP、主机名、bot
  名、人名、邮箱、域名均为占位符（如 `PASTE_TOKEN_HERE`、`YOUR_TELEGRAM_USER_ID`、示例
  id `1234567890`）。绝不要提交真实 secret；打包检查清单见
  [PACKAGING-NOTES.md](PACKAGING-NOTES.md)。
- **默认双轨。** 部署 Codex 不会停掉、删除或改动同一台机器上的 Claude Code。回滚就是
  停掉 Codex 桥接、回 Claude 的群。
- **绝不碰**运行中的交易进程、生产网站、受保护的数据挂载。容器场景下实盘数据一律只读挂载。
- 桥接就是远程 shell：allowlist（一个操作人用户 id）是唯一门。不要把 bot 设公开、不要加进无关群。
- 桥接日志含消息原文，只存本机，不外发。

### 提案

待拍板的设计提案放在 [`docs/proposals/`](docs/proposals/)：

- [Telegram 文件上传（出站附件）](docs/proposals/PROPOSAL_telegram-file-upload-2026-11-24.md)——让 Codex 桥接把服务器上产出的文件直接发回给 Telegram 里的操作人（`[SEND_FILE: <path>]` 标记 + `/send <path>` 命令），带路径 confinement 与凭证文件黑名单。

---

## License

MIT
