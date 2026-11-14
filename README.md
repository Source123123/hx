# hx

[![tests](https://github.com/Source123123/hx/actions/workflows/test.yml/badge.svg)](https://github.com/Source123123/hx/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.14-blue)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![license](https://img.shields.io/badge/license-MIT-green)

**hx 把一个项目的 Agent 开发环境保存进项目本身，用 git 记录它的每一次变化，并能在另一台机器、另一种 Agent 上还原。**

用编程 Agent 做项目时，决定产出质量的除了代码，还有 Agent 的配置和它积累的上下文：个人指令、技能、MCP 服务器、插件、记忆、聊过的内容。这些大多散落在个人电脑的主目录里，换一台机器或交接给同事时就丢了。hx 把它们收进项目的 `.harness/` 目录，随代码一起版本化。

## 目录

- [功能](#功能)
- [兼容性](#兼容性)
- [安装](#安装)
- [快速开始](#快速开始)
- [工作原理](#工作原理)
- [安全](#安全)
- [已知限制](#已知限制)
- [文档](#文档)
- [版本策略](#版本策略)
- [参与开发](#参与开发)
- [许可证](#许可证)

## 功能

| 功能 | 说明 |
|---|---|
| 保存 | 一条命令抓取 Agent 的配置和上下文，提交为一个快照 |
| 载入 | 在新机器上还原配置，并检查缺少的环境变量、工具和插件 |
| 历史 | 快照就是 git 提交，可以查看、比较、回退；代码提交可以标记它产生时所用的快照 |
| 多 Agent | 同一个项目可以同时登记 Claude Code 和 Codex，各自保存互不覆盖 |
| 跨 Agent | 用一种 Agent 保存的项目，可以在只装了另一种 Agent 的机器上载入 |
| 团队交接 | 会话摘要全团队累积，自动生成交接简报 |
| 密钥保护 | 密钥不进包，只登记环境变量名；文本经过脱敏规则 |
| 路径可搬运 | 本机的项目目录和主目录保存为占位符，载入时换成新机器的路径；用户名不外泄 |
| 私有名称代称 | 你登记的客户名、内部域名等保存为代称，对照表只留在本机 |
| 分享前检查 | 列出规则无法判断、需要人看一眼的内容（邮箱、内网地址、疑似密钥等） |
| 派生数据 | 向量库、索引不打包，只记录重建方法；载入时提示重建 |
| 外部状态核对 | 把 Agent 记忆所依赖的外部状态写成声明，载入时在新机器上重新核对 |
| 合并 | 两个分支各自保存后合并，`.harness/` 的冲突自动解决 |
| 离线分发 | 没有共享仓库时，导出为单个包文件传递 |
| 可扩展 | 新的 Agent、脱敏规则、提炼器通过插件接口接入 |

## 兼容性

以下组合在每次代码变更时由自动测试验证。

| 维度 | 支持范围 |
|---|---|
| 操作系统 | Windows 10 / 11、macOS、Linux |
| Python | 3.9、3.10、3.11、3.12、3.13、3.14 |
| git | 2.22 及以上 |
| 运行依赖 | 无（仅使用 Python 标准库） |

| Agent | 保存与载入 | 一键体验（插件、自动保存、团队接入） |
|---|---|---|
| Claude Code | 支持 | 支持 |
| OpenAI Codex CLI | 支持 | 尚未提供，使用命令行 |

hx 识别的是 Agent 程序，而不是它背后的模型。在 Claude Code 或 Codex 中改用其他厂商的模型（例如 DeepSeek），hx 的行为不变。

各项的验证程度和尚未验证的部分见 [PROGRESS.md](PROGRESS.md)。

## 安装

**前置条件**

| 系统 | 需要先安装 |
|---|---|
| Windows 10 / 11 | [Python](https://www.python.org/downloads/)（安装时勾选 "Add python.exe to PATH"）和 [Git for Windows](https://git-scm.com/download/win) |
| macOS | 在终端运行一次 `xcode-select --install`，它会装好 git 和 Python |
| Linux | 用系统的软件包管理器安装 `git` 和 `python3` |

**方式一：Claude Code 插件（推荐）**

在 Claude Code 中输入：

```
/plugin install hx --marketplace Source123123/hx
```

如果你的 Claude Code 版本较旧、不认识上面的写法，改用两步：

```
/plugin marketplace add Source123123/hx
```

```
/plugin install hx@hx
```

插件自带启动器，不需要再用 pip 安装。

**方式二：命令行**

```bash
pip install git+https://github.com/Source123123/hx
```

## 快速开始

### 使用 Claude Code 插件

| 目的 | 操作 |
|---|---|
| 首次保存 | 输入 `/hx:save` |
| 之后的保存 | 无需操作，会话结束时自动保存 |
| 在新机器上接手 | clone 项目后输入 `/hx:load` |
| 让同事免安装接入 | 运行 `hx team on` 后推送；同事打开项目时会被提示安装插件 |

`/hx:load` 会先列出将要放到本机的文件，经你同意后执行，然后说明还缺什么，并概括项目进展。

### 使用命令行

保存当前项目的 Agent 环境：

```bash
hx save
```

在另一台机器上载入（来源可以是 git 地址、本地目录或包文件）：

```bash
hx load https://github.com/你的账号/你的项目
```

不带参数运行 `hx`，会显示当前状态和下一步建议。

没有共享仓库时，导出为单个文件：

```bash
hx export -o 项目.hxb
```

对方执行：

```bash
hx load 项目.hxb
```

完整的命令和选项见 [docs/COMMANDS.md](docs/COMMANDS.md)。

## 工作原理

```
机器 A                                            机器 B
┌──────────────────────────┐                     ┌──────────────────────────┐
│ ~/.claude  ~/.codex      │                     │ ~/.claude  ~/.codex      │
│ 个人指令、技能、MCP、记忆 │                     │                          │
│ 会话记录                 │                     │                          │
└────────────┬─────────────┘                     └────────────▲─────────────┘
             │ hx save                                        │ hx load
             │ 复制、脱敏、提炼                                 │ 确认、还原、体检
┌────────────▼─────────────┐   git push / .hxb   ┌────────────┴─────────────┐
│ 项目/.harness/           │ ──────────────────▶ │ 项目/.harness/           │
│ 配置 + 上下文 + 锁文件    │                     │                          │
└──────────────────────────┘                     └──────────────────────────┘
```

- **项目内已有的文件**（`CLAUDE.md`、`AGENTS.md`、`.claude/`、`.mcp.json`）只在锁文件中记录哈希，不复制。
- **项目外的文件**（主目录下的个人配置、记忆、会话）复制进 `.harness/`，复制时脱敏。
- **会话记录**不整份保存，而是提炼为摘要：用户提出的要求、改动的文件、最后的答复。
- **快照**是一次只包含 `.harness/` 的 git 提交，快照 ID 由内容算出，与机器和操作系统无关。

`.harness/` 的目录结构和文件格式见 [docs/FORMAT.md](docs/FORMAT.md)，设计取舍见 [docs/DESIGN.md](docs/DESIGN.md)。

## 安全

hx 的默认行为按"包可能被团队以外的人看到"来设计。

- **密钥不进包。** 配置中的密钥值被替换为环境变量引用，名字登记在清单里，由目标机器提供。
- **文本脱敏。** 所有复制进包的文本经过规则匹配，覆盖常见的 API 密钥、令牌、私钥和带密码的地址。
- **载入需要确认。** 载入会把项目带来的设置写入你的主目录，其中可能包含会自动执行的命令。hx 不会在打开项目时自动载入，而是列出文件并等待确认。
- **不覆盖已有文件。** 本机已有的不同文件默认保留，强制覆盖时先备份。
- **不自动推送。** 自动保存只做本地提交，且只提交 `.harness/`。
- **原始对话默认不导出。**
- **本机路径和用户名不进包。** 保存为占位符，载入时换成新机器的路径。
- **私有名称可以换成代称。** 用 `hx private add` 登记，对照表只存在本机。
- **向量库和索引不打包。** 向量可以被反推出原文的大意，所以 hx 只记录重建方法。

分享前运行一次检查，它会列出规则没有把握的内容：

```bash
hx audit
```

脱敏基于规则，**不能保证没有遗漏**。会话摘要包含你对 Agent 说过的原话。把仓库或包文件分享到团队之外前，请人工检查 `.harness/`。完整的安全模型和漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 包带不走的东西

Agent 的环境里有四类内容，hx 对每一类用不同的方式处理：

| 内容 | 例子 | 处理方式 |
|---|---|---|
| 可以原样带走的 | 指令、技能、决策记录 | 复制进包，脱敏 |
| 只在这台机器上成立的 | 绝对路径、用户名、私有名称 | 换成占位符，载入时换回本机的值 |
| 能重新生成的 | 向量库、检索索引、缓存 | 不带数据，只带重建方法 |
| 根本带不走的 | 数据库里的数据、远端服务的状态、本机装的工具 | 写成声明，载入时重新核对 |

最后一类是 Agent 换机器后"记错事"的主要来源：它的记忆里写着"测试库里已经有某条数据"，而新机器连的库里没有。hx 不能把外部世界打包，但可以把这些依赖明确记下来：

```bash
hx claim add "测试库里已经导入了订单样例"
```

```bash
hx claim add "模型权重已下载" --path models/weights.bin
```

载入时，能自动核对的（文件是否存在、环境变量是否设置、命令是否安装）会自动核对，其余的会提醒接手的人和 Agent 先核实再使用。交接简报开头也会注明：其中的会话摘要和记忆是历史记录，不是当前事实。

向量库这类派生数据用同样的思路登记：

```bash
hx resource add docs-index --path .rag/index --sources "docs/**/*.md" --build "python tools/build_index.py"
```

## 已知限制

- hx 保证配置和上下文一致，不保证 Agent 在新机器上做出相同的结果：模型输出有随机性，厂商也可能更新模型。hx 记录保存时的模型和 Agent 版本，供事后追查。
- 外部状态的声明要靠人或 Agent 写下来，hx 不能自动发现 Agent 依赖了哪些外部状态。
- hx 不会执行包里带来的任何命令，包括资源的重建命令和声明的核对；重建由你确认后自己运行。
- hx 不安装系统依赖。请使用 Dev Container、Nix 或 Docker；hx 会记录这些文件的哈希。
- hx 不自动安装项目用到的其他插件，载入时会列出缺少的项。
- 权限、钩子、插件等各 Agent 不通用的设置不做跨 Agent 转换。
- Codex 的通用设置（`config.toml` 中 MCP 以外的部分）仅保存作参考，不自动还原。
- ChatGPT 网页版或 App 中的对话不在本机，无法打包。
- 同一种 Agent 的个人配置以最后保存的人为准。建议先载入再工作。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/COMMANDS.md](docs/COMMANDS.md) | 命令参考：全部命令、选项、退出码、环境变量 |
| [docs/FORMAT.md](docs/FORMAT.md) | 格式规范：目录结构、清单、锁文件、提交标记、包文件 |
| [docs/DESIGN.md](docs/DESIGN.md) | 设计说明：分层模型、多 Agent、跨系统、取舍 |
| [docs/EXTENDING.md](docs/EXTENDING.md) | 扩展指南：接入新的 Agent、脱敏规则、提炼器 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 常见问题与排查 |
| [SECURITY.md](SECURITY.md) | 安全模型与漏洞报告 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、测试、提交规范 |
| [PROGRESS.md](PROGRESS.md) | 开发进度、验证状态、路线图 |

## 版本策略

- 版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。1.0 之前，次版本号的变化可能包含不兼容改动，并会在变更记录中标明。
- **向后兼容**：新版 hx 可以读取旧版保存的包，再次保存时自动升级为新格式。
- **向前保护**：旧版 hx 遇到新格式的包会拒绝读取并提示升级，不会按错误的理解去还原。
- Python 版本的支持范围跟随上表。已停止维护的 Python 版本会保留到主流操作系统不再预装它为止。

## 参与开发

```bash
git clone https://github.com/Source123123/hx
```

```bash
pip install -e ./hx
```

```bash
python -m unittest discover -s hx/tests
```

开发约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)
