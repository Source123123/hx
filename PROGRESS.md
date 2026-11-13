# 开发进度（断点续做看这里）

> 任何新会话接手时：先读本文件，再读 docs/DESIGN.md。每完成一步就更新本文件并 git commit。

## 状态
- [x] M0 仓库初始化、设计文档
- [x] M1 核心模块：util / redact / gitutil / adapter 基类
- [x] M2 Claude Code adapter：capture + materialize + doctor
- [x] M3 CLI：init / capture / snapshot / log / diff / doctor / materialize / restore / export / import / hook
- [x] M4 测试（tests/test_hx.py，7 个，全部通过）+ 手工端到端验证（git clone 和 .hxb 两条迁移路径）
- [x] M5 README、EXTENDING

## 已验证
- 模拟机器 A 抓取 → 机器 B clone 后 doctor / materialize：通过
- 导出 .hxb（含原始对话）→ 机器 C 导入并还原：通过
- 密钥不进包（settings env、MCP env/headers、文本里的 key）：通过

## 未验证 / 已知局限
- 没有在真实的第二台机器上用真实的 Claude Code 打开还原后的项目验证。
- 插件清单的解析是按 ~/.claude/plugins/installed_plugins.json 的格式写的，只用构造的数据测过。
- 还原的原始对话能否被 `claude --resume` 正常续上，没有测（标为实验性）。
- Windows 路径没有测。

## 下一步（v0.2）
1. 在真实环境里试用，修问题。
2. 第二个 adapter（Codex 或 Cursor），验证中立格式。
3. LLM 提炼器：从会话里自动抽取决策和待办，写入 decisions/ 和 tasks.md 的草稿。
4. `hx status`：不改文件，只报告当前状态与上次快照的差异。

## 备注
- 本仓库目前只在本地容器里，没有远端。容器回收后会丢失，需要用户提供 GitHub 仓库后 push。
