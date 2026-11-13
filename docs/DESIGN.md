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
  agent/<adapter>/
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

- **v0.1（当前）**：Claude Code adapter；init / capture / snapshot / log / diff / doctor / materialize / export / import / hook。
- **v0.2**：第二个 adapter（Codex 或 Cursor），验证中立格式；指令文件在 `CLAUDE.md` 和 `AGENTS.md` 之间转换。
- **v0.3**：LLM 提炼器（从会话中自动抽取决策和待办）；上下文的三方合并。
- **v0.4**：OCI 分发；团队服务端（浏览 harness 历史、按 harness 对比产出质量）。
