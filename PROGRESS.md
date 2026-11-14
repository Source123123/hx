# 开发进度与验证状态

> 接手开发时先读本文件，再读 [docs/DESIGN.md](docs/DESIGN.md)。每完成一步就更新本文件并提交。
> 本文件如实记录哪些经过真实环境验证、哪些没有。

## 状态
- [x] M0 仓库初始化、设计文档
- [x] M1 核心模块：util / redact / gitutil / adapter 基类
- [x] M2 Claude Code adapter：capture + materialize + doctor
- [x] M3 CLI：init / capture / snapshot / log / diff / doctor / materialize / restore / export / import / hook
- [x] M4 测试（tests/test_hx.py，7 个，全部通过）+ 手工端到端验证（git clone 和 .hxb 两条迁移路径）
- [x] M5 README、EXTENDING

- [x] M6（v0.2）一键 save / load、自动保存钩子、Claude Code 插件打包（10 个测试通过；`claude plugin validate` 通过；用 `claude --plugin-dir` 实际跑通 /hx:save）

- [x] M7 团队一键接入 `hx team on/off`（11 个测试通过）

- [x] M8（v0.3）多 Agent 核心、会话摘要全团队累积、Codex adapter、跨 Agent 交换（17 个测试通过）
- [x] M9 跨操作系统：哈希忽略换行差异、统一 LF/UTF-8、Windows 路径、输出编码、Windows 启动器 bin/hx.cmd、三系统 CI（.github/workflows/test.yml）
- [x] M10（v0.4）兼容性：格式版本检查、git 版本检查、Python 版本检查、可关闭会话摘要；文档体系重写（31 个测试通过）

## 已验证
- Python 3.9 / 3.10 / 3.11 / 3.12 / 3.13 / 3.14：本机用 uv 逐个版本跑过全部测试；GitHub Actions 在 Windows、macOS、Linux 上各跑一遍（Ubuntu 的 3.9 在 22.04 镜像上）。
- 旧格式（hx 0.1 / 0.2 的单 Agent 清单和锁文件）可以载入，再次保存时升级；更新的格式被明确拒绝。
- 三个系统的自动测试（GitHub Actions）全部通过，包括用 bin/hx（Git Bash）和 bin\\hx.cmd（命令提示符）实际执行保存。
- Claude Code：机器 A 抓取 → 机器 B clone 后载入；导出 .hxb（含原始对话）→ 机器 C 导入：通过
- 密钥不进包（settings env、MCP env/headers、文本里的 key、Codex config.toml 里写死的值）：通过
- 从 GitHub 安装 Claude Code 插件（`claude plugin marketplace add` + `claude plugin install hx@hx`）：通过
- 用 `claude --plugin-dir` 实际跑通 /hx:save，SessionStart 提示触发：通过
- 真实的 Claude Code installed_plugins.json / known_marketplaces.json 与解析一致：通过
- Codex（真实的 codex-cli 0.169.0，装在临时目录）：
  - 从真实的 CODEX_HOME 抓取（config.toml 的 MCP、技能、个人 AGENTS.md、一个真实会话文件）：通过
  - hx 写入 config.toml 的 MCP 段落能被 `codex mcp get --json` 正确读出（stdio + env_vars、http + bearer_token_env_var）：通过
- 跨 Agent：Codex 保存的项目在只有 Claude Code 的机器上载入、反过来也一样：通过（Claude 侧用模拟主目录）

## 未验证 / 已知局限
- Codex 会话文件里**助手回复和工具调用**的形状没有对照真实文件（开发机连不上 OpenAI）。见 src/hx/adapters/codex.py 开头的说明。
- Codex 的项目级技能目录、prompts 目录、AGENTS.override.md、项目级 config.toml 的 MCP：按推测写，未核对。
- Codex 没有"一键"体验（插件、钩子、团队接入）；Codex 的通用设置只留作参考、不还原。
- 跨 Agent 转过去的技能和指令入口，没有在真实的 Agent 会话里确认"确实被用上了"。
- 团队一键接入写入的 .claude/settings.json，没有在交互式会话里确认"同事打开项目时弹出安装提示"。
- /hx:load 的多步交互流程没有在真实对话里跑过。
- 还原的原始对话能否被 `claude --resume` 续上没有测（实验性）；Codex 的原始对话只导出、不还原。
- Windows 上 Claude Code 插件的钩子和 /hx:save 是否能正确调用 bin/hx 没有实测（CI 只验证了启动器本身在 Git Bash 和命令提示符下可用）。Windows 10 没有单独测，CI 用的是 Windows Server（与 Windows 11 同代内核）。
- 同一种 Agent 的个人配置是"最后保存的人为准"（见 DESIGN 6.2）。

## 下一步
1. 在装有 Codex 且能登录的机器上：用真实会话核对会话文件格式、核对其余推测、做 Codex 侧的一键体验、真实环境端到端验证。
2. LLM 提炼器：从会话里自动抽取决策和待办，写入 decisions/ 和 tasks.md 的草稿。
3. `hx status`：不改文件，只报告当前状态与上次快照的差异。
4. 插件图标（提交到官方插件目录才需要）。

## 备注
- 远端：https://github.com/Source123123/hx（main 分支）。每完成一步就 commit 并 push。
- 模拟另一台机器：设置环境变量 HX_HOME 指向一个目录，hx 会把它当作主目录（此时忽略 CLAUDE_CONFIG_DIR / CODEX_HOME，也不探测本机的 claude / codex 版本）。
