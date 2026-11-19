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
- [x] M10（v0.4）兼容性：格式版本检查、git 版本检查、Python 版本检查、可关闭会话摘要；文档体系重写（27 个测试通过）
- [x] M11（v0.5）动态内容：路径占位符、私有词代称、hx audit、可重建资源、外部状态声明、来源标记、合并冲突自动解决、目录大小保护（35 个测试通过）

- [x] M12（v0.6）Codex 真实格式核对、项目 MCP、覆盖指令、技能、插件体检与一键入口
- [x] M13（v0.6.1）真实 Codex 迁移和 GitHub 插件安装验证、新机器团队目录修复
- [x] M14（v0.7.0）DeepSeek Harness adapter：指令、技能、profile patch、MCP、插件清单、凭据名字、zstd 会话摘要、跨 Agent

## 已验证
- 本轮接手完成真实 Codex 格式核对、当前配置核对、插件入口与钩子、真实迁移验证，详细证据见下方第 1–4 节。主机为 Windows 10 专业版 19045、Python 3.12、codex-cli 0.167.0-alpha.4.1。
- 实际 Windows 目录下的路径、盘符、JSON 双反斜杠完成保存 → clone → 载入，按 path_key 核对指向新位置。跨系统真实模型会话未测，三系统自动测试已覆盖。
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
- hx audit 的"像密钥的随机字符串"靠信息熵判断，会漏掉低熵的密钥，也会误报随机 ID。它是提示，不是保证。
- 外部状态声明要靠人或 Agent 主动记录；Agent 是否会按 /hx:save 的提示去记，没有在真实会话里观察过。
- 可重建资源的新旧判断用文件修改时间（和 make 一样），git 检出会刷新修改时间，可能把已是最新的产物报成"过期"。
- hx doctor 仍只做命令存在性检查，不启动任意 MCP；本轮受控 MCP 已通过真实 Codex 握手和工具调用。
- Codex 的通用设置只留作参考、不还原；用户级生成记忆不按项目搬运。
- Claude 格式 → Codex 的技能、指令、MCP 已在真实 Codex 会话中使用；反向的真实 Claude 会话未测，本机没有 Claude Code。
- 团队一键接入写入的 .claude/settings.json，没有在交互式会话里确认"同事打开项目时弹出安装提示"。
- Claude 的 /hx:load 多步交互未测；Codex 的 $hx:load 已在授权的真实会话中实际预览并载入。
- 还原的原始对话能否被 `claude --resume` 续上没有测（实验性）；Codex 的原始对话只导出、不还原。
- Windows 上 Claude Code 插件的钩子和 /hx:save 是否能正确调用 bin/hx 没有实测（CI 只验证了启动器本身在 Git Bash 和命令提示符下可用）。本轮真实主机是 Windows 10，Codex 钩子和入口已测；Claude 钩子仍未测。
- 同一种 Agent 的个人配置是"最后保存的人为准"（见 DESIGN 6.2）。

## 下一步
1. 在装有 Claude Code 的真实环境补做反向迁移和 Claude 插件的 Windows 交互验证。
2. LLM 提炼器：从会话里自动抽取决策、待办和外部状态声明的草稿。
2b. 可选的深度体检：真实启动 MCP 服务器并完成握手（需要用户明确同意，因为这等于执行项目带来的命令）。
3. `hx status`：不改文件，只报告当前状态与上次快照的差异。
4. 插件图标（提交到官方插件目录才需要）。

## 备注
- 远端：https://github.com/Source123123/hx（main 分支）。每完成一步就 commit 并 push。
- 模拟另一台机器：设置环境变量 HX_HOME 指向一个目录，hx 会把它当作主目录（此时忽略 CLAUDE_CONFIG_DIR / CODEX_HOME，也不探测本机的 claude / codex 版本）。

## 本轮接手（2026-11-19）

### 1. DSH 接入
- 新增 `src/hx/adapters/deepseek_harness.py`、注册表和 entry point；支持项目/用户/共享
  指令与技能、profile 与主目录 patch、`mcp-manager` 的 MCP、插件清单、凭据名字、`.env`
  参考副本和会话摘要。
- DSH 的会话是 `session.v4.jsonl.zstd`（多个 zstd frame 串联）。按 Python 3.14
  `compression.zstd` → `zstandard` → `node:zlib` 的顺序解压；都没有时跳过摘要并记录。
- DSH 的 `${credential:NAME}` 不能当普通赋值脱敏，已修 `redact.py`；MCP 载入时转回
  `${credential:NAME}`，只添加缺失服务器。
- profile patch 的 YAML 采用窄化读写：只认 `mcp-manager`/`mcp` 的 servers 和
  `agent-default-model` 的 model/provider，写回只插入局部行，保留注释。

### 2. 验证
- 本机真实 `~/.dsh`（Windows 10、DSH 0.2.2-rc.3、Python 3.12、Node 24.21）：
  `hx init --agent deepseek-harness`、`hx capture`、`hx doctor`、`hx save` 通过。
  真实 profile patch 被保存并按 `{{hx:home}}` 脱敏；真实会话摘要提取出用户消息、
  改动文件和工具次数；模型为 `deepseek-flash`。
- 假 `HX_HOME` 的 `materialize` 与 `.hxb` `load` 通过：profile patch、用户/共享技能、
  用户/共享 AGENTS 写到了预期位置；缺少凭据 `DEEPSEEK_API_KEY` 被报告。
- 新增 8 个 DSH 测试全过（有 node 时包括 zstd 往返）；`tests/test_dsh.py` 覆盖抓取、
  脱敏、摘要、还原、MCP 合并/冲突、中立格式、zstd。

### 3. 未验证 / 下一步
- 没有 DSH 原生插件：`$hx:save` / `$hx:load`、会话结束自动保存、会话开始提示、
  `hx team on` 都未实现；用户目前手动运行 `hx save` / `hx load`。
- 没有启动真实 DSH 读取还原后的 profile；没有真实双机迁移。
- 本机 DSH sandbox 使现有 46 个测试因 `tempfile`/`git clone` 权限报错，未能跑全量；
  DSH 专项 8 个测试通过。需要在普通终端或 CI 上重跑全量。
- `--include-transcripts` 不导出 DSH 的 zstd 原始会话；YAML 窄化解析不支持锚点/别名/
  多行折叠；凭据名与环境变量的语义仍可能产生体检提醒。
- 详细交接见 [docs/DSH.md](docs/DSH.md) 和 [docs/CODEX_HANDOFF.md](docs/CODEX_HANDOFF.md)。

## 本轮接手（2026-11-14）

### 1. 真实会话格式核对
- 在 Windows、已登录的 codex-cli 0.167.0-alpha.4.1 上实际创建并修改 greeting.txt 和 notes.md，读回正确后正常结束。
- 六项均已对照：用户要求、文件、工具次数、模型、最后答复、git 分支。助手 output_text 的推测正确；工具实际是 custom_tool_call/exec，里面的 tools.apply_patch 字符串需要先解开转义。已修正误把整段补丁当文件名的问题。
- 用户来源标记优先，保留用户输入的 HTML/XML，排除自动注入指令；工具次数按真实外层调用名记录，因此本次为 exec: 3。
- tests/fixtures/codex-real-rollout.jsonl 是裁短脱敏的真实文件；tests/test_hx.py 的构造同步更新。
- 初始安装和全部 35 项测试通过；新增 2 项真实记录回归测试通过，原有 Codex 相关测试通过。
- 限制：任意 shell 命令是否修改文件无法可靠推断；旧版直接工具形状保留构造测试，未在本机另装旧版生成。

### 2. 其余格式核对
- 官方当前约定和本机真实会话均确认 `.agents/skills` 是项目级技能位置。本机版本也发现 `.codex/skills` 和 CODEX_HOME/skills 中的兼容技能，保留旧位置；新增抓取 ~/.agents/skills，跨 Agent 新技能写入项目 `.agents/skills`。
- 实际会话执行了 probe-current 和 probe-user-legacy 技能，输出 CURRENT_SKILL_OK、LEGACY_USER_OK；全局和项目 AGENTS.override.md 都优先于同层 AGENTS.md，输出 GLOBAL_OVERRIDE_OK、PROJECT_OVERRIDE_OK。
- 官方确认 ~/.codex/prompts 仍是旧自定义提示词目录，但已弃用；继续保存旧内容，新入口改用技能。未在交互式提示菜单另测旧提示词。
- 项目 `.codex/config.toml` 的 MCP 纳入按字段合并、保存、跨 Agent 导出及体检；项目来源仍还原到项目，不提升为用户全局配置。项目必须由用户信任后才会加载。
- 用户级覆盖指令原先漏存，已补齐；MCP HTTP 请求头原先依赖通用脱敏，短密钥可能漏掉，现全部改成环境变量名。
- 插件使用本机 `codex plugin list --json` 的公开接口，记录名字、版本和来源，体检报告缺失、禁用和版本差异；不复制插件凭证、不自动安装插件。不支持此 CLI 的版本会明确提示。
- Codex 有用户级生成记忆（memories / memories_v2），官方没有安全的按项目抓取和还原约定；不把其他项目的全局记忆混入包。本机 memories 功能默认关闭，未开启后台生成。
- 全部 41 项测试通过（含 Claude Code 和旧格式兼容）；新增项目 MCP、短密钥、技能、覆盖指令、插件和后备 TOML 解析测试。
- 官方依据（2026-11-14）：https://learn.chatgpt.com/docs/build-skills 、https://learn.chatgpt.com/docs/custom-prompts 、https://learn.chatgpt.com/docs/agent-configuration/agents-md 、https://learn.chatgpt.com/docs/config-file/config-advanced 、https://learn.chatgpt.com/docs/customization/memories 。

### 3. Codex 一键入口（0.6.0）
- 本机实际 `codex plugin --help` 有 add/list/marketplace/remove；`features list` 显示 plugins、hooks 均为 stable/true（plugin_hooks 已 removed）。保留版本输出和真实行为，不使用已移除开关。
- 新增 `.codex-plugin/plugin.json`、`.agents/plugins/marketplace.json`；隔离 CODEX_HOME 中实际 marketplace add → plugin add hx@hx → plugin list 成功。
- Codex 技能使用独立的 codex-skills/，避免与 Claude 的 commands/ 重名。入口 `$hx:save` / `$hx:load`，通过 bin/hx 或 bin/hx.cmd 调用，无需 pip。技能校验通过。
- `hooks/codex.json` 单独供 Codex 使用，未改变 Claude 的 hooks/hooks.json。Windows 实测钩子 shell 是 PowerShell，修正了 cmd 风格变量和调用语法。
- 真实 Codex app-server 的原生 startup/SessionStart 和归档/SessionEnd 事件都已完成，开始事件将 HANDOFF 提示注入上下文，结束事件在约 2.1 秒返回，后台提交完成。测试只对隔离的自有插件临时跳过信任，不修改用户信任设置。
- 本机 `codex exec` 不发 SessionStart；原生交互式客户端传入 startup 事件时会触发。强制杀进程不保证会话结束钩子执行。
- 结束钩子最长 3 秒，使用 `auto-save --background`；后台结果保存在本机 ~/.hx/logs/auto-save-*.log。开始只报告，不还原，不写用户目录。
- 项目目录优先取钩子标准输入 JSON 的 cwd，兼容 CLAUDE_PROJECT_DIR，并支持从项目子目录启动。
- `team_setup` / `team_enabled` 最终使用项目 `.agents/plugins/marketplace.json` 的远程插件来源，配合 `.codex/config.toml` 的插件开关，保留已有市场名和其他条目。不承诺与 Claude 相同的安装弹窗，钩子仍须同事审阅。
- 新机器实测修正：早期仅写 Git 市场配置会因缺少已下载快照而失败；现原生 `plugin/list(cwds=[项目])` 能发现 hx，状态为 AVAILABLE、enabled=true，且无加载错误。CLI 的 plugin list 不传 cwds，因此不显示项目目录；CLI 用户首次接入仍须添加市场并安装，不能假装已自动完成。
- 插件抓取和体检查询也临时纳入项目目录，不写用户配置。实际记录了从 GitHub 装入的 hx@hx 0.6.0 的名称、Git 来源和版本。
- README、COMMANDS、DESIGN、CHANGELOG 已更新。新增跨系统声明钩子测试（含带空格的插件目录）：开始时主目录完全不变；结束只提交 .harness/，保留已暂存的代码和未安装 Agent 的内容。9 项 Codex 专项测试通过；全部 44 项测试通过（Python 3.12、Windows 10 专业版 19045）。
- 官方依据：https://developers.openai.com/plugins/build/plugins 、https://learn.chatgpt.com/docs/hooks 、https://learn.chatgpt.com/docs/config-file/config-reference 。

### 4. 真实环境端到端验证
- 同类迁移：Windows 上实际 hx save → git clone 到另一目录 → 空的 HX_HOME（只创建 .codex 模拟已装 Codex）→ hx load --dry-run（确认未写入）→ hx load --yes。codex mcp list --json 读出 hx_user 和 hx_project。
- 启动真实 Codex，实际调用 hx_project.hx_ping 返回 HX_MCP_OK，且最终输出 HX_PROJECT_RULE_OK、HX_USER_RULE_OK、HX_USER_SKILL_OK、HX_PROJECT_SKILL_OK，证明项目/个人指令及两类技能实际生效。
- 跨 Agent：受控 Claude 格式样例经过真实 hx save → clone → 只有 Codex 的 HX_HOME 中 hx load，生成 AGENTS.md 指路入口、项目技能、MCP。真实 Codex 阅读 CLAUDE.md、执行 e2e-claude 技能、调用 hx_claude.hx_ping，输出 HX_CLAUDE_RULE_OK、HX_CLAUDE_SKILL_OK、HX_MCP_OK。
- 从 GitHub 实际安装 hx@hx 0.6.0 后，在真实 Codex 会话中执行 $hx:save → $hx:load 的预览 → 已授权载入 → 阅读 HANDOFF，三条启动器命令均成功，无需 pip；未推送、未强制覆盖。
- 本机没有 claude 命令，因此没有反向真实 Claude 会话验证；Claude 来源是受控样例，不能算运行过 Claude 本身。反向转换及 Claude 原有流程仍由自动测试覆盖。
- 新增 tests/manual_codex_e2e.py 可重复上述迁移检查。它会使用真实模型额度，只写临时项目和隔离配置；认证不进项目，结束后删除临时复制的 auth.json。
- Windows 真实路径（含 JSON 里的双反斜杠）保存后换根正确，按项目统一的 path_key 比较；保留本机不同内容的行为与备份也经过既有测试覆盖。
- 0.6.0 提交 46b1a06 的 GitHub Actions 18/18 组合已通过：https://github.com/Source123123/hx/actions/runs/43563678746 。本轮最后修订的自动测试结果见后续记录。

- 0.6.1 最终本机检查：46/46 测试通过（含后备 TOML、旧格式、Claude Code、跨系统钩子）；测试用认证副本已清理。

### 最终自动测试
- 0.6.1 代码提交 382b68b 已推送 main，GitHub Actions 的 Windows / macOS / Linux × Python 3.9–3.14 共 18/18 组全部通过（每组 46 个测试及启动器检查）。
- 结果：https://github.com/Source123123/hx/actions/runs/43564908868 。
- 本机安装已更新为 hx 0.6.1，包元数据确认运行依赖为空。工作目录位于本机 hx/repo。
