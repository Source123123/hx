# hx 总体设计

hx（harness exchange）把一个项目的 **Agent 开发环境**——Agent 的配置和它积累的上下文——打包成可以迁移、可以版本化的 harness 包。

## 1. 目标与非目标

**目标**

1. 在机器 A 上抓取项目的 Agent 环境，在机器 B 上还原出相近的环境。
2. 对 harness 本身做版本管理：每次配置或上下文变化都可以提交，形成一条可追溯的历史线。
3. 记录"代码提交 ↔ harness 快照"的对应关系。
4. 中间格式与具体 Agent 无关，通过 adapter 接入不同的 Agent。

**非目标**

- 不保证 Agent 行为可重放。模型有随机性，旧模型会下线。
- 不打包密钥。包里只记录需要哪些密钥的名字。
- 不重造环境工具。系统依赖交给 Dev Container / Nix / Docker，hx 只引用它们。
- 不重造 git。harness 的历史就是 git 历史。

## 2. 分层模型

| 层 | 内容 | hx 的处理 |
|---|---|---|
| L0 代码 | 源码 | 交给 git，hx 只记录提交号 |
| L1 运行环境 | devcontainer、Dockerfile、flake.nix、锁文件 | 引用并记录哈希 |
| L2 Agent 配置 | 指令文件、skills、子 agent、命令、hooks、权限、插件、MCP | 仓库内的引用，仓库外的复制进包 |
| L3 上下文 | 记忆、会话摘要、意图、决策、任务 | 提炼后进包；原始对话可选归档 |
| L4 行为 | 重跑结果 | 不处理，只追溯 |

## 3. 关键规则：引用与复制

Agent 的配置分散在两处：

- **项目内**（如 `CLAUDE.md`、`.claude/`、`.mcp.json`）：已经在仓库里，hx **只引用**，在锁文件里记下路径和哈希。
- **项目外**（如 `~/.claude/` 下的个人指令、skills、记忆、会话）：换机器就没了，hx **复制**进 `.harness/`，复制时脱敏。

这样避免仓库里同一份文件存两遍，同时保证搬走的包是完整的。

## 4. 目录结构

```
.harness/
  harness.json            总清单：Agent 类型、模型、需要的密钥、环境文件
  harness.lock.json       锁文件：每个文件的哈希、插件和工具版本、对应的代码提交
  agent/<adapter>/        每个 Agent 一个目录，如 agent/claude-code/、agent/codex/
    user/                 从项目外复制来的配置（指令、skills、agents、commands、settings）
    mcp.json              规范化后的 MCP 服务器声明，密钥写成 ${NAME}
    plugins.json          已安装插件清单（名字、来源、版本）
  context/
    intent.md             项目目标和设计思路（人写或 Agent 写）
    decisions/            决策记录，一条一个文件
    tasks.md              进行中和待办
    memory/               Agent 的长期记忆
    sessions/<id>.json    会话摘要（脱敏）
    HANDOFF.md            自动生成的交接简报，给新机器上的 Agent 读
```

`intent.md`、`decisions/`、`tasks.md` 由人或 Agent 维护，hx 不会覆盖。其余由 `hx capture` 生成。

## 5. 版本模型

- `hx snapshot` = 抓取 + 一次只包含 `.harness/` 的 git 提交。
- 提交信息带 trailer：`Hx-Snapshot`（内容哈希）、`Hx-Agent`、`Hx-Model`、`Hx-Code`（当时的代码提交）。
- `hx log` 读这些 trailer，列出 harness 的历史。分支、合并直接用 git。
- `hx hook install` 安装一个 git 钩子，给之后的每个代码提交加上 `Hx-Snapshot` trailer，于是能从任何一个代码提交查到它是在哪个 harness 下产出的。
- `hx diff A B` 比较两个快照的锁文件，按类别（指令、skills、MCP、插件、记忆、会话、模型）报告增删改。

快照 ID 是锁文件中所有条目哈希的摘要：内容相同则 ID 相同，与机器无关。

## 6. 迁移流程

```
机器 A                                   机器 B
hx init                                  git clone（或 hx import 包文件）
hx snapshot -m "..."                     hx doctor        检查缺什么
git push（或 hx export 成包文件）          hx materialize   还原 Agent 配置
```

`materialize` 的规则：

- 目标位置没有文件：直接写入。
- 目标位置有相同内容：跳过。
- 目标位置有不同内容：默认不动并报告冲突；加 `--force` 才覆盖，覆盖前备份为 `.hx-bak`。
- MCP 服务器合并进项目的 `.mcp.json`，密钥保持 `${NAME}` 形式，由目标机器的环境变量提供。

## 6.1 一键使用

面向不写代码的用户，日常只有两个动作：

- `hx save`：没有初始化就初始化；抓取；是 git 仓库就提交（说明自动生成，如"技能 1 项，会话 2 项"）；不是 git 仓库也照样写入 `.harness/`。仓库正在合并或变基时只更新文件，不提交。
- `hx load [来源]`：来源可以是 git 地址或 `.hxb` 文件；列出将写入本机的文件并要求确认；还原；体检并用一句话说明每个待处理项。

仓库本身同时是一个 Claude Code 插件（`.claude-plugin/`、`commands/`、`hooks/`、`bin/hx`），自带启动器，不需要 pip：

- `/hx:save`、`/hx:load`：由 Agent 代为执行并用自然语言向用户说明结果。
- `SessionEnd` 钩子调用 `hx auto-save`：只对已经保存过、且 `auto_save` 为真的项目生效。
- `SessionStart` 钩子调用 `hx auto-load`：**只报告，不写任何文件**，提示 Agent 阅读交接简报、提示用户有配置待载入。

自动载入故意不做：载入会把项目带来的设置写进用户的主目录，其中可以包含钩子命令。如果打开一个仓库就自动写入，恶意仓库就能借此在用户机器上执行代码。所以载入必须有一次明确的确认。

## 6.2 多个 Agent 与跨 Agent

- `harness.json` 的 `agents` 是一个列表。`hx save` 时，如果发现这台机器上用另一个受支持的 Agent 做过这个项目（有它的会话记录），自动加进列表。
- 抓取时逐个 adapter 处理。**这台机器上没装的 Agent 直接跳过，它在包里的内容和锁文件条目原样沿用。** 所以用 Codex 的同事保存时，不会抹掉用 Claude Code 的同事留下的配置。
- 锁文件的每个条目带 `agent` 字段，记录它属于谁；`agents` 字段记录每个 Agent 的版本和模型。
- 会话摘要全团队累积在 `context/sessions/`，每条带 `agent` 字段，只保留最近 `max_sessions` 个（更早的在 git 历史里）。
- 还原时只处理本机装了的 Agent。本机装了、但包里没有它内容的 Agent，走"接收"流程：
  其他 adapter 用 `export_neutral()` 导出中立内容（技能文件、MCP 规范、指令文件路径），本机 adapter 用 `adopt()` 接收。这样 N 个 Agent 只需要 N 个 adapter，不需要两两写转换。
- MCP 的中立写法就是 `.mcp.json` 的写法，密钥一律是 `${NAME}`。Codex 那边对应 `env_vars`、`bearer_token_env_var`、`env_http_headers`。
- 指令文件不复制内容，只生成入口文件指向原文件。

已知取舍：同一个 Agent 的个人配置（如个人技能）是"最后保存的人为准"。两个人都用 Claude Code 时，后保存的人的个人技能会替换包里的那份；因为他载入时已经拿到了前一个人的技能，通常是并集，但如果他没载入就保存，前一个人的会从当前快照里消失（git 历史里还在）。

## 7. 安全

- 所有复制进包的文本都经过脱敏器：已知格式的密钥（API key、token、私钥块）和名字像密钥的配置值都被替换为占位符。
- MCP 和 settings 里的环境变量值一律换成 `${NAME}`，名字登记到 `harness.json` 的 `secrets`。
- 原始对话记录默认不进包。`hx export --include-transcripts` 才归档，同样先脱敏。
- 脱敏是基于规则的，**不能保证没有漏网**。把包分享给团队外的人之前要人工检查。

## 8. 扩展点

| 扩展点 | 位置 | 说明 |
|---|---|---|
| Adapter | `hx.adapters` entry point | 接入新的 Agent。实现 `detect / capture / materialize / doctor` |
| Redactor | `hx.redact.RULES` / `hx.redactors` entry point | 增加脱敏规则 |
| Distiller | `hx.distillers` entry point | 把会话摘要提炼成交接简报。默认实现不调用模型；可以换成调用 LLM 的实现 |
| 包格式 | `hx.bundle` | 目前是 tar.gz；可以加 OCI |

详见 [EXTENDING.md](EXTENDING.md)。

## 9. 路线图

- **v0.1**：Claude Code adapter；init / capture / snapshot / log / diff / doctor / materialize / export / import / hook。
- **v0.2**：一键 save / load；Claude Code 插件；会话结束自动保存。
- **v0.3（当前）**：Codex adapter；一个项目多个 Agent；跨 Agent 交换技能、MCP、指令入口；会话摘要全团队累积。
- **待办**：Codex 侧的一键（插件、钩子）；在真实 Codex 会话上核对会话文件格式。
- **v0.4**：LLM 提炼器（从会话中自动抽取决策和待办）；上下文的三方合并。
- **v0.5**：OCI 分发；团队服务端（浏览 harness 历史、按 harness 对比产出质量）。
