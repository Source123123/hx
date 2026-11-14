# 变更记录

本项目的所有重要变更记录于此。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.0] - 2026-11-14

### 新增

- 支持 Windows 10 / 11、macOS、Linux，并支持跨操作系统迁移。
- Windows 命令提示符使用的启动器 `bin/hx.cmd`。
- 体检按操作系统给出设置环境变量的具体方法。
- 体检检查 git 版本；git 低于 2.22 时保存操作给出明确提示。
- 清单和锁文件的格式版本检查：旧版 hx 遇到更新的格式时拒绝读取并提示升级。
- 清单的 `capture.max_sessions` 可设为 `0`，表示不保存会话摘要。
- `hx load` 接受本地仓库目录作为来源。
- 自动测试覆盖三类操作系统上的 Python 3.9 至 3.14。
- 文档：命令参考、格式规范、常见问题、安全说明、贡献指南。

### 变更

- 内容哈希对换行风格不敏感；包内文本统一为 UTF-8 编码、LF 换行。
- 还原时，仅换行风格不同的文件视为一致。
- 输出被其他程序接收时统一为 UTF-8。
- 启动器会确认找到的 Python 真实可用且版本满足要求。

### 修复

- Windows 上，会话摘要无法识别项目内被改动的文件。
- Windows 路径中的 `\n` 被误当作换行。
- 同一快照在 Windows 上被判定为所有文件均已修改。

## [0.3.0] - 2026-11-14

### 新增

- OpenAI Codex CLI 的 adapter：抓取个人指令、技能、提示词、MCP 服务器和会话摘要。
- 一个项目可以登记多个 Agent；保存时自动登记本机新使用的 Agent。
- 跨 Agent 载入：技能、MCP 服务器和指令入口在 Agent 之间转换。
- 锁文件格式升级为版本 2，条目标明所属的 Agent。

### 变更

- 会话摘要改为全团队累积，保留最近 30 个。
- 本机未安装的 Agent，其在包内的内容在保存时原样保留。

## [0.2.0] - 2026-11-13

### 新增

- `hx save` 和 `hx load`：一步完成保存与载入；`hx load` 接受 git 地址和包文件。
- Claude Code 插件：`/hx:save`、`/hx:load`，会话结束时自动保存。
- `hx team`：团队一键接入。
- `hx auto`：开关自动保存。
- 不带参数运行 `hx` 时显示状态和下一步建议。

## [0.1.0] - 2026-11-13

### 新增

- 首个版本。Claude Code 的 adapter。
- 命令：`init`、`capture`、`snapshot`、`log`、`diff`、`doctor`、`materialize`、`restore`、`export`、`import`、`hook`。
- 密钥替换、文本脱敏、交接简报。

[0.4.0]: https://github.com/Source123123/hx/compare/a948da9...main
[0.3.0]: https://github.com/Source123123/hx/compare/9453027...a948da9
[0.2.0]: https://github.com/Source123123/hx/compare/0ce6517...9453027
[0.1.0]: https://github.com/Source123123/hx/commit/0ce6517
