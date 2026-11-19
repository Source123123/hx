# 常见问题与排查

遇到问题时，先在项目目录运行体检，它会列出大多数问题及对应的处理方法：

```bash
hx doctor
```

## 安装与启动

### 提示"hx 需要 Python 3.9 或更高版本"

本机没有可用的 Python，或版本过旧。

- Windows：从 [python.org](https://www.python.org/downloads/) 安装，安装时勾选 "Add python.exe to PATH"，然后重新打开终端。
- macOS：在终端运行 `xcode-select --install`。
- Linux：用软件包管理器安装 `python3`。

### Windows 上运行 `python3` 弹出应用商店

这是 Windows 自带的占位程序，不是真正的 Python。按上一条安装 Python 即可；hx 的启动器会自动跳过这个占位程序。

### 提示"git 版本太旧"

hx 需要 git 2.22 或更新。从 [git-scm.com](https://git-scm.com/downloads) 安装新版。

### Claude Code 提示找不到插件或安装命令无效

较旧的 Claude Code 不支持一步安装。改用两步：

```
/plugin marketplace add Source123123/hx
```

```
/plugin install hx@hx
```

## 保存

### 提示"这个目录不是 git 仓库"或保存后没有历史

hx 的历史依赖 git。在项目目录运行一次：

```bash
git init
```

没有 git 时 `hx save` 仍会写入 `.harness/`，可以用 `hx export` 分发，只是没有历史记录。

### 提示"这些文件还没有 git commit"

快照引用了项目里的文件（如 `CLAUDE.md`），而这些文件有未提交的改动。把它们提交并推送，否则其他机器取得的内容与快照不一致。

### 每次保存都产生新的快照

属于正常现象：每次会话都会产生新的会话摘要，摘要是快照内容的一部分。不希望保存会话摘要时，把 `.harness/harness.json` 中的 `capture.max_sessions` 设为 `0`。

### 自动保存没有发生

依次检查：

1. 是否安装了 Claude Code 插件。
2. 该项目是否已经手动保存过一次。
3. `hx auto` 是否显示为"开"。
4. 仓库是否处于合并或变基过程中，此时只更新文件、不提交。

### 提示"仓库正在合并或变基中"

完成或放弃当前的合并、变基后，再次运行 `hx save`。

### 合并分支时 `.harness/` 里出现冲突

两个分支都保存过 Agent 环境时会出现。直接运行：

```bash
hx save
```

hx 会自动解决其中能机械处理的部分，并列出需要你判断的文件（通常是意图、任务、决策或记忆）。处理完后用 `git add` 标记，再 `git commit` 完成合并。

### `hx audit` 列出了很多内容

它列出的是"需要人看一眼"的内容，不都是问题。逐类判断：

- 名称类（客户名、内部域名）：用 `hx private add 真实的词 代称` 登记，然后重新保存。
- 确实不该出现的内容：修改或删除来源文件（个人技能、记忆等），然后重新保存。
- 无害的内容（公开的邮箱、文档里的示例地址）：可以忽略。

### 向量库或索引被提交进了仓库

用 `hx resource add` 登记它的重建方法，把它的路径加进 `.gitignore`，再用 `git rm -r --cached 路径` 从仓库中移除。

## 载入

### 提示"需要确认"

在非交互环境中运行了 `hx load`。先用 `hx load --dry-run` 查看将要写入的文件，确认无误后运行 `hx load --yes`。

### 提示某文件"本机已有不同的版本，没有改动"

本机已有同名但内容不同的配置，hx 默认不覆盖。

- 保留本机版本：无需操作。
- 改用项目中的版本：运行 `hx load --force`，原文件备份为 `<文件名>.hx-bak`。

### 提示"环境变量 … 未设置"

项目的 Agent 配置需要该变量，而密钥不会随包迁移。按提示在本机设置：

- Windows：在命令提示符运行 `setx 变量名 "值"`。
- macOS：在 `~/.zshrc` 中加入 `export 变量名="值"`。
- Linux：在 `~/.bashrc` 中加入 `export 变量名="值"`。

设置后重新打开终端和 Agent。

### 提示"MCP 服务器 …：找不到命令"

该 MCP 服务器依赖的程序没有安装在本机，或原配置使用了另一台机器上的绝对路径。在本机安装对应的程序；如果路径不同，修改项目的 `.mcp.json` 或 Agent 的配置。

### 提示"这个快照是用更新的 hx 保存的"

保存该项目的人使用了更新的 hx。升级后再试：

```bash
pip install --upgrade git+https://github.com/Source123123/hx
```

使用插件时，在 Claude Code 中更新插件。

### 提示"可重建资源 … 还没有生成"或"比它的源文件旧"

这类数据（向量库、索引）不随项目迁移。按提示在项目目录运行重建命令。hx 不会替你运行。

### 提示"记忆中的说法在本机不成立"或"需要人工核实"

之前的工作依赖了项目之外的某个状态，而这台机器上没有，或者 hx 无法自动判断。补齐它，或者明确告诉 Agent 这一条已经不成立，避免它按旧记忆行事。

### 载入后文件里出现 `[[某个词]]`

这是保存者隐去的私有名称的代称，文字仍然可以正常阅读。需要原词时，向保存者索取对照表并用 `hx private add` 登记，再运行 `hx load --force`。

### 载入后 Agent 没有使用新配置

配置在 Agent 启动时读取。关闭并重新打开 Agent。

### 提示"这台机器上没有找到受支持的 Agent"

本机既没有 Claude Code 也没有 Codex 的配置目录。安装其中之一并至少启动一次。

## 跨操作系统

### 在 Windows 上克隆后，体检提示大量文件"改过"

hx 0.4 起比较内容时忽略换行差异，不应出现该现象。如果仍然出现，请确认各台机器的 hx 均为 0.4 或更新。

### Windows 终端里中文显示为乱码

把终端切换为 UTF-8：在命令提示符运行 `chcp 65001`，或改用 Windows Terminal。hx 写入的文件不受终端编码影响。

## 团队协作

### 同事打开项目时没有被提示安装插件

依次检查：

1. `hx team` 是否显示为"开"，以及 `.claude/settings.json` 的改动是否已经推送。
2. 同事是否在 Claude Code 中信任了该项目目录。
3. 同事的 GitHub 账号能否访问 hx 插件所在的仓库。

### 我保存后，同事的个人技能从快照里消失了

同一种 Agent 的个人配置以最后保存的人为准。请先 `hx load` 取得同事的配置，再开始工作和保存。被替换的内容仍在 git 历史中，可以用 `hx restore <快照>` 找回。

### 同事用的是另一种 Agent

可以混用。各自运行 `hx save` 即可，hx 会分别保存，互不覆盖。载入时，技能和 MCP 服务器会自动转换，指令文件会生成指向原文件的入口。

## 仍未解决

提交 [issue](https://github.com/Source123123/hx/issues) 时请附上：

- `hx --version` 的输出
- 操作系统和 Python 版本
- 所用的 Agent 及其版本
- `hx doctor` 的完整输出（提交前检查其中是否有不便公开的路径或名称）

## Codex 插件与自动保存

- 找不到 `codex plugin`：升级 Codex，或用命令行方式安装 hx。
- 安装后没有技能：重新打开会话，在 `/skills` 中找 hx 的 save/load，入口是 `$hx:save` 和 `$hx:load`。
- 钩子没运行：先在 `/hooks` 审阅并信任；插件脚本更新后可能需要重新审阅。项目配置还要求项目被信任。
- `codex exec` 没有开始提示：本轮版本没有发出 SessionStart；需要时显式运行 hx auto-load。
- 结束后看不到新提交：后台保存稍等几秒，再检查 ~/.hx/logs/auto-save-*.log。项目须先保存过且 hx auto 为开；强制终止不保证触发。
- 项目 MCP 没有出现在 `codex mcp list`：在项目根目录运行，并确认 Codex 已信任项目；用户级和项目级配置分开还原。
- 插件清单缺失：`codex plugin list --json` 必须可用。hx 只记录名字、来源和版本，不自动安装插件，也不复制账号认证。

## DeepSeek Harness

### 提示"DSH 会话是 zstd 压缩的，本机没有可用的解码器"

DSH 默认用 zstd 压缩会话。任选一种：

- 用 Python 3.14 运行 hx；
- 安装 `zstandard`（`pip install zstandard`）；
- 把 `node` 放进 PATH，或设置 `HX_NODE` 指向 node 可执行文件（DSH 自带的 Node 运行时也会被自动发现）。

没有解码器时只跳过会话摘要，配置、技能和 MCP 仍然可以保存和载入。

### 提示 DSH 版本未知

DSH 桌面版的版本在 `app.asar` 的 `dsh/desktop-runtime.json` 中。hx 会从
`DSH_INSTALL_DIR` 解析；如果自定义安装路径或只装了命令行版，可能读不到版本。
这不影响保存和载入，只是体检少一项版本对比。

### `hx load` 提示 profile 的 `cordis.patch.yml` 已有不同内容

本机 profile 已经有一份配置。hx 默认保留本机版本：

- 想保留本机：无需操作；
- 想用快照里的：先备份，再运行 `hx load --force`，原文件会变成 `.hx-bak`；
- 想只合并 MCP：不要 `--force`，hx 仍会把快照里缺少的 MCP 服务器加进本机 profile。

### 提示"凭据 … 未找到"

DSH 的 `${credential:NAME}` 由 DSH 凭据提供者解析，hx 不复制 `.credentials.yaml` 的值。
在 DSH 里登录/配置该凭据，或设置同名环境变量；不要向 hx 索要或粘贴凭据原文。

### DSH 项目没有被自动识别

`hx init` 只有在项目里有 `.dsh/`、`.agents/skills/`，或 `$DSH_HOME/sessions` 下有这个项目的
会话时才自动识别 DSH。也可以在初始化时显式指定：

```bash
hx init --agent deepseek-harness
```

### DSH 没有自动保存 / 团队一键接入

这两项需要 DSH 原生插件，当前版本还没有。请手动运行 `hx save` / `hx load`；
后续计划见 [DSH.md](DSH.md) 和 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)。
