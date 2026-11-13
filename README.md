# hx — 把 Agent 开发环境打包、版本化、迁移

用 Agent（目前支持 Claude Code）做项目时，真正决定产出的除了代码，还有 Agent 的配置和它积累的上下文：个人指令、skills、MCP 服务器、插件、记忆、聊过的内容。这些大多散落在你自己机器的主目录里，换一台机器或者交给同事就没了。

hx 把它们收进项目里的 `.harness/` 目录，用 git 记录每一次变化，并能在另一台机器上还原。

## 安装

需要 Python 3.9 以上和 git，没有其他依赖。

```bash
pip install -e .
```

## 五分钟上手

在机器 A 上，进入你的项目目录：

```bash
hx init
```

```bash
hx snapshot -m "初始快照"
```

```bash
git push
```

在机器 B 上，`git clone` 之后：

```bash
hx doctor
```

```bash
hx materialize
```

`doctor` 告诉你这台机器缺什么（环境变量、MCP 需要的命令、插件）；`materialize` 把配置放回 Agent 能读到的位置。

没有共享的 git 仓库时，用包文件传递：

```bash
hx export -o demo.hxb
```

```bash
hx -C 新项目目录 import demo.hxb
```

## 命令

| 命令 | 作用 |
|---|---|
| `hx init` | 在项目里创建 `.harness/` |
| `hx capture` | 抓取当前的配置和上下文，不提交 |
| `hx snapshot -m "说明"` | 抓取并提交一个快照 |
| `hx log` | 快照历史。`--all` 同时列出带快照标记的代码提交 |
| `hx diff [A] [B]` | 比较两个快照；不带参数时比较最近的快照和当前状态 |
| `hx doctor` | 检查本机能不能跑这个 harness |
| `hx materialize` | 把配置还原到本机。`--dry-run` 预演，`--force` 覆盖（会备份） |
| `hx restore <快照>` | 把 `.harness/` 恢复到历史快照 |
| `hx export` / `hx import` | 导出、导入单个包文件 |
| `hx hook` | 安装 git 钩子，让每个代码提交都带上当时的快照 ID |

## 包里有什么

```
.harness/
  harness.json          清单：Agent 类型、需要哪些环境变量
  harness.lock.json     锁文件：每个文件的哈希、Agent 版本、模型、对应的代码提交
  agent/claude-code/    从 ~/.claude 复制来的个人指令、设置、skills、agents、MCP、插件清单
  context/
    intent.md           项目目标和设计思路 —— 你来写
    tasks.md            进行中和待办 —— 你来写
    decisions/          决策记录 —— 你来写
    memory/             Agent 的记忆
    sessions/           每次会话的摘要：提了什么要求、改了哪些文件、最后的答复
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
- 原始对话记录默认不导出，`hx export --include-transcripts` 才会带上。

## 做不到的事

- 不保证 Agent 在新机器上做出同样的结果。模型有随机性，hx 保证的是配置和上下文一致。
- 不安装系统依赖。请用 Dev Container、Nix 或 Docker，hx 会记录这些文件的哈希。
- 不自动安装插件，`doctor` 会告诉你缺哪些。
- 目前只支持 Claude Code。接入其他 Agent 见 [docs/EXTENDING.md](docs/EXTENDING.md)。

## 文档

- [docs/DESIGN.md](docs/DESIGN.md)：总体设计
- [docs/EXTENDING.md](docs/EXTENDING.md)：如何扩展
- [PROGRESS.md](PROGRESS.md)：开发进度

## 开发

```bash
python -m unittest discover -s tests
```
