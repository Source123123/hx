# hx — 把 Agent 开发环境打包、版本化、迁移

用 Agent（目前支持 Claude Code 和 OpenAI Codex CLI）做项目时，真正决定产出的除了代码，还有 Agent 的配置和它积累的上下文：个人指令、skills、MCP 服务器、插件、记忆、聊过的内容。这些大多散落在你自己机器的主目录里，换一台机器或者交给同事就没了。

hx 把它们收进项目里的 `.harness/` 目录，用 git 记录每一次变化，并能在另一台机器上还原。

## 支持哪些 Agent

| Agent | 保存 / 载入 | 一键（插件、自动保存） |
|---|---|---|
| Claude Code | 支持 | 支持 |
| OpenAI Codex CLI | 支持（命令行 `hx save` / `hx load`） | 还没有 |
| 在 Claude Code 或 Codex 里换用别家模型（如 DeepSeek） | 支持，和上面一样 | 同上 |

hx 认的是 Agent 程序，不是背后的模型。

**可以混用。** 一个项目可以同时登记多个 Agent：同事 A 用 Claude Code、同事 B 用 Codex，各自 `hx save`，谁的内容都不会被对方覆盖，会话摘要合在同一份交接简报里。如果项目是用另一种 Agent 保存的，`hx load` 会把能通用的部分转过来：

- 技能（两边都是 `SKILL.md` 格式）直接复制。
- MCP 服务器在 `.mcp.json` 和 Codex 的 `config.toml` 之间转换，密钥保持用环境变量传入。
- 指令文件不复制内容，只生成一个指向原文件的入口（`CLAUDE.md` 里引用 `AGENTS.md`，或反过来），避免两份内容不一致。
- 权限、钩子、插件这类各家不通用的设置不转换。

## 支持哪些操作系统

Windows 10、Windows 11、macOS、Linux 都支持，也可以跨系统迁移（比如在 Mac 上保存，在 Windows 上载入）。每次改动都会在这三类系统上自动跑一遍测试。

| 系统 | 需要先装好 |
|---|---|
| Windows 10 / 11 | [Python](https://www.python.org/downloads/)（安装时勾选 "Add python.exe to PATH"）和 [Git for Windows](https://git-scm.com/download/win) |
| macOS | 在终端运行一次 `xcode-select --install`，它会装好 git 和 Python |
| Linux | 用系统的软件包管理器安装 `git` 和 `python3`（多数发行版已自带） |

跨系统时 hx 替你处理的事：

- **换行符**：Windows 和 Mac/Linux 的换行不同，hx 比较内容时会忽略这一点，不会把只有换行不同的文件当成"改过"。
- **路径**：包里只存相对路径；会话摘要里"改了哪些文件"在三类系统上写法一致。
- **中文**：所有文件用 UTF-8 保存，Windows 上输出不会乱码。

需要你自己处理的事：

- **环境变量**在每台机器上各设一次，`hx load` 会按你的系统告诉你具体怎么设。
- MCP 服务器如果配置的是某台机器上的**绝对路径**（比如 `/usr/local/bin/某工具`），换系统后找不到，`hx load` 会指出来，需要在新机器上装好对应工具。

## 最简单的用法：装成 Claude Code 插件

需要电脑上有 Python 3.9 以上和 git。在 Claude Code 里输入一次：

```
/plugin install hx --marketplace Source123123/hx
```

装好之后：

| 你想做的事 | 怎么做 |
|---|---|
| 保存这个项目的 Agent 环境 | 什么都不用做，会话结束时自动保存。想立刻保存就输入 `/hx:save` |
| 在新机器上接手项目 | 把项目 clone 下来，在 Claude Code 里输入 `/hx:load` |
| 关掉自动保存 | 对 Claude 说"关掉 hx 自动保存"，或运行 `hx auto off` |

### 团队一键接入

想让同事连安装命令都不用输，在项目里开启一次（或者第一次 `/hx:save` 时 Claude 会问你）：

```bash
hx team on
```

它会在项目的 `.claude/settings.json` 里声明 hx 插件并提交。你 `git push` 之后，同事 clone 项目、用 Claude Code 打开并信任这个目录时，会被提示安装 hx；装好输入 `/hx:load` 就接上了。前提是同事的 GitHub 账号能访问 hx 所在的仓库。关闭用 `hx team off`。

说明：

- 第一次需要手动 `/hx:save` 一次，之后这个项目才会自动保存。没有保存过的项目，插件不会碰。
- 自动保存只在本地做一次 git 提交，而且只提交 `.harness/` 目录，不会推送，也不会动你的其他文件。
- `/hx:load` 会先列出要放到本机的文件，等你同意再执行。之后 Claude 会告诉你还缺什么（比如某个环境变量），并概括项目进展。
- 打开一个带 `.harness/` 的项目时，Claude 会被提示先读交接简报。

## 不用插件：命令行

```bash
pip install git+https://github.com/Source123123/hx
```

日常只需要两条命令。保存：

```bash
hx save
```

载入（可以直接给 git 地址或 `.hxb` 包文件，会自动下载）：

```bash
hx load https://github.com/你/你的项目
```

不带任何参数运行 `hx`，它会告诉你当前状态和下一步该做什么。

没有共享的 git 仓库时，用 `hx export` 导出一个 `.hxb` 文件发给对方，对方 `hx load 文件.hxb`。

## 全部命令

| 命令 | 作用 |
|---|---|
| `hx save` | **常用。** 一键保存：需要时自动初始化，抓取并提交。`--push` 同时推送 |
| `hx load [来源]` | **常用。** 一键载入：还原配置并检查环境 |
| `hx auto on/off` | 开关自动保存 |
| `hx team on/off` | 开关团队一键接入 |
| `hx log` | 快照历史。`--all` 同时列出带快照标记的代码提交 |
| `hx diff [A] [B]` | 比较两个快照；不带参数时比较最近的快照和当前状态 |
| `hx restore <快照>` | 把 `.harness/` 恢复到历史快照 |
| `hx export` / `hx import` | 导出、导入单个包文件 |
| `hx hook` | 安装 git 钩子，让每个代码提交都带上当时的快照 ID |
| `hx init` / `capture` / `snapshot` / `doctor` / `materialize` | `save` 和 `load` 的分解步骤，需要精细控制时用 |

## 包里有什么

```
.harness/
  harness.json          清单：Agent 类型、需要哪些环境变量
  harness.lock.json     锁文件：每个文件的哈希、Agent 版本、模型、对应的代码提交
  agent/claude-code/    从 ~/.claude 复制来的个人指令、设置、skills、agents、MCP、插件清单
  agent/codex/          从 ~/.codex 复制来的个人指令、skills、提示词、MCP
  context/
    intent.md           项目目标和设计思路 —— 你来写
    tasks.md            进行中和待办 —— 你来写
    decisions/          决策记录 —— 你来写
    memory/             Agent 的记忆
    sessions/           每次会话的摘要：提了什么要求、改了哪些文件、最后的答复（全团队累积，保留最近 30 个）
    HANDOFF.md          自动生成的交接简报
```

项目里已有的文件（`CLAUDE.md`、`.claude/`、`.mcp.json`）不会被复制，只在锁文件里记录哈希。

要让接手的 Agent 自动读到交接简报，在项目的 `CLAUDE.md` 里加一行：

```
@.harness/context/HANDOFF.md
```

## 关于密钥

- 密钥不进包。MCP 配置里的密钥值会换成 `${名字}`，settings 里的密钥条目会被删掉，名字登记在 `harness.json` 里，由目标机器的环境变量提供。
- 所有复制进包的文本都会过一遍脱敏规则（常见的 API key、token、私钥、带密码的 URL）。
- **脱敏是基于规则的，不保证没有遗漏。** 会话摘要里有你对 Agent 说过的话。把仓库或包文件分享给团队之外的人之前，请自己看一遍 `.harness/`。
- 载入会把项目带来的设置、技能放进你的 `~/.claude/`，其中可能有会自动执行命令的内容。**只载入你信任的项目。** 载入前会列出文件并要求确认；本机已有的不同文件不会被覆盖。
- 原始对话记录默认不导出，`hx export --include-transcripts` 才会带上。

## 做不到的事

- 不保证 Agent 在新机器上做出同样的结果。模型有随机性，hx 保证的是配置和上下文一致。
- 不安装系统依赖。请用 Dev Container、Nix 或 Docker，hx 会记录这些文件的哈希。
- 不自动安装项目用到的其他插件，载入时会告诉你缺哪些。
- ChatGPT 网页版或 App 里的对话不在本机，hx 打包不了。
- Codex 的通用设置（`config.toml` 里 MCP 以外的部分）只保存作参考，不会自动还原。
- 接入其他 Agent 见 [docs/EXTENDING.md](docs/EXTENDING.md)。

## 文档

- [docs/DESIGN.md](docs/DESIGN.md)：总体设计
- [docs/EXTENDING.md](docs/EXTENDING.md)：如何扩展
- [PROGRESS.md](PROGRESS.md)：开发进度

## 开发

```bash
python -m unittest discover -s tests
```
