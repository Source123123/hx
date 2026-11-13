# 开发进度（断点续做看这里）

> 任何新会话接手时：先读本文件，再读 docs/DESIGN.md。每完成一步就更新本文件并 git commit。

## 状态
- [x] M0 仓库初始化、设计文档
- [x] M1 核心模块：util / redact / gitutil / adapter 基类
- [x] M2 Claude Code adapter：capture + materialize + doctor
- [x] M3 CLI：init / capture / snapshot / log / diff / doctor / materialize / restore / export / import / hook
- [x] M4 测试（tests/test_hx.py，7 个，全部通过）+ 手工端到端验证（git clone 和 .hxb 两条迁移路径）
- [x] M5 README、EXTENDING

- [x] M6（v0.2）一键 save / load、自动保存钩子、Claude Code 插件打包（10 个测试通过；`claude plugin validate` 通过；用 `claude --plugin-dir` 实际跑通 /hx:save）

- [x] M7 团队一键接入 `hx team on/off`（11 个测试通过）

## 已验证
- 从 GitHub 安装插件（`claude plugin marketplace add Source123123/hx` + `claude plugin install hx@hx`）：通过
- 真实的 installed_plugins.json / known_marketplaces.json 格式与插件清单解析一致：通过
- 模拟机器 A 抓取 → 机器 B clone 后 doctor / materialize：通过
- 导出 .hxb（含原始对话）→ 机器 C 导入并还原：通过
- 密钥不进包（settings env、MCP env/headers、文本里的 key）：通过

## 未验证 / 已知局限
- 没有在真实的第二台机器上用真实的 Claude Code 打开还原后的项目验证。
- 团队一键接入写入的 .claude/settings.json 字段（extraKnownMarketplaces / enabledPlugins）没有在交互式会话里确认过"同事打开项目时弹出安装提示"这一步。
- 还原的原始对话能否被 `claude --resume` 正常续上，没有测（标为实验性）。
- Windows 路径没有测。

## 下一步
0. 用户按 README 从 GitHub 安装插件实测（仓库私有时安装需要 GitHub 登录）；/hx:load 的交互流程还没有在真实会话里跑过。
1. 第二个 adapter：Codex CLI（见 docs/DESIGN.md 路线图）。需要在装有 Codex 的机器上核对真实文件格式。
1b. 插件图标（提交到官方目录才需要）。
2. 第二个 adapter（Codex 或 Cursor），验证中立格式。
3. LLM 提炼器：从会话里自动抽取决策和待办，写入 decisions/ 和 tasks.md 的草稿。
4. `hx status`：不改文件，只报告当前状态与上次快照的差异。

## 备注
- 远端：https://github.com/Source123123/hx（main 分支）。每完成一步就 commit 并 push。
