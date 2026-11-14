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
