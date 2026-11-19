# 给 Codex 的交接提示词

下面整段可以直接交给 Codex（或另一个编码 Agent）。仓库、分支和提交请在推送后补上真实值。

---

## 任务

你接手的是 `Source123123/hx` 仓库的 DeepSeek Harness（DSH）适配分支。hx 把 Agent 的
配置、技能、MCP、插件清单和会话摘要收进项目的 `.harness/`，随 git 提交形成快照链，
让另一台机器或另一个 Agent 接手。Claude Code 和 Codex 已经接入；本次新增了 DSH adapter。

目标：在**不破坏 Claude Code / Codex 现有行为**的前提下，把 DSH 的保存、载入、体检和
跨 Agent 交接跑通，并补上 DSH 原生插件（会话结束自动保存、`$hx:save` / `$hx:load` 技能、
会话开始提示）。

分支：`feat/deepseek-harness-adapter`
基线：`main@e54e93f`
DSH 适配提交：`68f5710`（本分支第一个提交）

## 已经完成的内容

### 1. 新 adapter

文件：`src/hx/adapters/deepseek_harness.py`

- 名称 `deepseek-harness`，标题 `DeepSeek Harness`；已注册进
  `src/hx/adapters/__init__.py` 和 `pyproject.toml` 的 `hx.adapters` entry point。
- 项目内引用：`AGENTS.md`、`CLAUDE.md`、`AGENTS.local.md`、`CLAUDE.local.md`、
  `.dsh/skills/**`、`.agents/skills/**`。
- 项目外复制：
  - `$DSH_HOME/AGENTS.md` → `user:AGENTS.md`
  - `$DSH_HOME/skills/**`（跳过 `.system`）→ `user:skills/**`
  - `$DSH_HOME/cordis.patch.yml` → `user:cordis.patch.yml`
  - `$DSH_HOME/profiles/<profile>/cordis.patch.yml` → `user:profiles/<profile>/cordis.patch.yml`
  - `$DSH_HOME/sessions/...` 的会话摘要
- 只登记不还原：
  - `profiles/<profile>/package.json` 的 bundles、npm 依赖、已安装版本
  - `.credentials.yaml` 的 `refs` 名字
  - `.env` 的变量名和非密钥值（`env.reference`，不覆盖本机 `.env`）
- 共享目录：`~/.agents/AGENTS.md`、`~/.agents/skills/**` 由 adapter 自己还原，
  因为核心的 `user:` target 只能指向一个 `user_root`。
- MCP：读取 profile patch 和主目录 patch 的 `mcp-manager`/`mcp` 行的
  `config.servers`，转成中立 `.mcp.json` 写法；保存时把 `${credential:NAME}` 变成
  `${NAME}` 并登记名字；载入时只添加缺失的同名服务器，同名不同内容报冲突、不覆盖。
- 会话摘要：解析 DSH v4 JSONL，取用户消息（排除 runtime-context、skill-catalog、
  time-context 等注入消息）、`write`/`edit`/`apply_patch` 的文件、工具次数、模型和最后答复。
- zstd 解码顺序：Python 3.14 `compression.zstd` → `zstandard` 模块 → `node:zlib`
  （PATH、`HX_NODE`、`$DSH_HOME/dsh-runtimes/*/dependencies/node/bin/node`）。
  都没有时跳过会话摘要并记录原因，配置仍可保存。
- DSH 版本：解析 `DSH_INSTALL_DIR` 指向的 `app.asar` 中的
  `dsh/desktop-runtime.json` / `dsh/package.json`；模拟主目录（`HX_HOME`）时不读本机版本。
- 体检：DSH 目录、版本差异、MCP 命令、凭据名字、profile 依赖、zstd 解码器。
- `hx team on` 对 DSH 返回“不支持”，没有写半成品。

### 2. 核心小修

- `src/hx/redact.py`：跳过 `${credential:NAME}` 中的 `credential:NAME`，
  避免把 DSH 的凭据引用名误判为“名字像密钥的赋值”。这是 `test_dsh.py` 暴露出来的。

### 3. 测试

新增 `tests/test_dsh.py`，8 个测试：

- 配置、项目/用户/共享技能、MCP、凭据、`.env` 的抓取与脱敏
- 会话摘要（排除注入消息、提取 `write` 和 `apply_patch` 的文件）
- 跨机器 `materialize`（用户 AGENTS、用户技能、共享技能、共享 AGENTS、profile patch、MCP）
- MCP 合并的幂等、冲突、自动创建 `mcp-manager` 行
- 中立格式导出与 `adopt`
- DSH 项目目录名 `project_key` 与真实目录一致
- zstd 会话解码（有 node 时；没有时 skip）

本机结果：

```text
python -m unittest tests.test_dsh -v
Ran 8 tests ... OK (skipped=1，没有 node 时)
```

手工端到端（本机真实 `~/.dsh`，临时项目）：

```text
hx -C <临时项目> init --agent deepseek-harness
hx -C <临时项目> capture      # 真实 profile patch、真实会话摘要、模型 deepseek-flash
hx -C <临时项目> doctor       # 0 个问题，1 个提醒
hx -C <临时项目> save
hx -C <临时项目> export -o proj.hxb
HX_HOME=<假主目录> hx -C <新目录> load proj.hxb -y
```

载入后在假主目录生成了
`~/.dsh/profiles/desktop/cordis.patch.yml`，并正确报告缺少 `DEEPSEEK_API_KEY` 凭据。
真实会话摘要验证过：当前工作区的一个真实会话提取出 1 条用户消息、5 个改动文件、
工具 `apply_patch/grep/load_workspace_dependencies/pwsh/read` 和模型 `deepseek-flash`。

## 没有做到 / 已知缺口

按优先级从高到低：

1. **没有 DSH 原生插件。** 没有 `$hx:save` / `$hx:load` 技能，没有会话结束自动保存，
   没有会话开始提示，没有 `hx team on` 的项目声明。DSH 用户目前只能手动运行
   `hx save` / `hx load`。
2. **没有在真实 DSH 进程里验证还原后的 profile。** `materialize` 只验证了文件写到
   假主目录；没有启动真实 DSH 读取这个 profile，确认模型、MCP、技能、指令真的生效。
3. **没有跑完整旧测试套件。** 本机 DSH sandbox 对 `tempfile.TemporaryDirectory`
   （0o700 子目录）和 `git clone` 有访问限制；加临时目录垫片后 54 个测试中 44 个通过，
   10 个都卡在 `git clone`（不是代码断言）。DSH 专项 8 个测试通过。需要在普通终端/CI
   上跑完整套件。
4. **没有真实的两台机器迁移。** 同一台机器上的假 `HX_HOME` 只验证了文件放置，
   没有验证“另一台装 DSH 的机器 clone 后载入并继续会话”。
5. **`hx export --include-transcripts` 不导出 DSH 的 zstd 原始会话。**
   核心按 UTF-8 文本读 transcript，adapter 只返回未压缩的 `.jsonl`。
6. **YAML 是窄化解析。** 只支持块式 YAML、常见标量、内联 `{}`/`[]`；锚点、别名、
   多行折叠等复杂 YAML 可能读不到。写回只做局部插入，不会重写整份文件。
7. **凭据名字和环境变量语义不完全一致。** `.credentials.yaml` 的 refs 由 DSH 凭据提供者
   解析，核心 `doctor` 仍把 `manifest.secrets` 当环境变量检查；目前 adapter 额外做凭据
   检查，但可能出现“环境变量未设置”的提醒。
8. **没有“每一步自动上链”。** hx 的快照是 `save`/`snapshot` 时的 git 提交；
   没有 per-turn 自动快照，也没有远端链或锚定。`--push` 才推送。
9. **没有处理子 Agent 的父子会话关系。** 只抓当前项目目录下的 session；子 Agent 的
   session 目录不会被合并进父会话摘要。
10. **profile patch 整份还原。** 不按字段合并；不同 DSH 版本/不同 profile 名可能冲突。

## 建议你接下来做的事（按顺序）

### 第 1 步：做 DSH 原生插件（最高价值）

在仓库新增 `dsh-plugin/`（或 `dsh-hx/`）目录，作为一个可安装的 DSH profile 插件包：

- `package.json`：`name`、`version`、`dsh.bundle.patch`、`peerDependencies`；
- `cordis.patch.yml`：插入一行插件，例如
  ```yaml
  - id: dsh-hx
    name: '@your-scope/dsh-hx'
    config:
      hxCommand: hx
      autoSave: true
  ```
- `lib/index.js`：导出 `apply(ctx)`，用 `ctx.on('session/event', (session, event) => ...)`：
  - `event.type === 'turn/end'` 时防抖，后台运行 `hx auto-save --background`
    （或 `hx save`），只对已管理且开启自动保存的项目生效；
  - `event.type === 'turn/start'` 或首个 `agent/pre-step` 时，只提示阅读
    `.harness/context/HANDOFF.md`，不写文件；
  - 命令执行要处理 Windows/macOS/Linux，超时和失败只写本机日志，不阻塞会话。
- 技能：把 `$hx:save` / `$hx:load` 做成插件自带的技能（DSH 技能目录或
  `dsh-skill-filesystem` 的 `customSkillDirs`），技能里通过插件自带启动器调用 hx，
  不要求用户 pip 安装。
- 安装/验证：
  ```bash
  dsh plugin --profile <profile> add <插件包路径>
  dsh --profile <profile> "运行 hx save"
  ```
  验收：会话结束后 `.harness/` 有本地提交；没有 `.harness/` 的项目不产生提交；
  会话开始只提示不写入；强制杀进程不保证保存（与 Claude/Codex 的语义一致）。

### 第 2 步：真实 DSH 迁移测试

新增 `tests/manual_dsh_e2e.py`（可重复、使用临时项目和隔离的 `DSH_HOME`）：

1. 在临时项目里创建真实 DSH 会话（或复用脱敏的 fixture）；
2. `hx save` → 复制/clone 项目 → 另一个假 `HX_HOME`；
3. `hx load` 后启动真实 DSH，验证：
   - 读取 `AGENTS.md` / `CLAUDE.md`；
   - 发现 `.dsh/skills` 和 `~/.agents/skills`；
   - MCP 服务器出现在 DSH 的工具列表；
   - 会话摘要和 HANDOFF 可见。
4. 测试失败时不要修改真实用户配置；使用 `HX_HOME` 或隔离 profile。

### 第 3 步：加固 YAML/MCP

- 增加 fixtures：注释、`!!js`、内联 map、块标量、同名服务器、`enabled: false`；
- 载入时保持注释和其他行不变；
- 同名不同内容不要静默覆盖；
- 多个 `mcp-manager`/`mcp` 行、主目录 patch 覆盖 profile 的顺序要明确；
- 如果 DSH 提供 `--dump-config` 或配置 JSON Schema，优先用它校验，而不是自己猜 YAML。

### 第 4 步：凭据语义

- 明确 `${credential:NAME}` 与 `manifest.secrets` 的对应；
- `doctor` 检查顺序：DSH 凭据库 → 环境变量 → 明确提示；
- 避免核心 `doctor` 把凭据名误报成环境变量缺失；
- 不要读取或复制 `.credentials.yaml` 的值。

### 第 5 步：原始会话导出

- 方案 A：adapter 增加 `transcript_bytes` / `transcript_reader` 接口，核心导出时按 adapter
  提供的文本/字节写出；
- 方案 B：DSH 支持 `compression: none` 时只导出未压缩 `.jsonl`；
- 方案 C：导出时解压成 `.jsonl`，并在包元数据里记录来源版本；
- 无论哪种，都要在 `import --restore-transcripts` 时放回 DSH 能识别的格式。

### 第 6 步：跨 Agent 与链

- 补 DSH → Claude Code、DSH → Codex、反向三种真实/受控测试；
- 指令入口：DSH 读 `AGENTS.md`/`CLAUDE.md`，不要重复写两份不一致的内容；
- 如果用户要“每一步上链”，实现 per-turn 快照（插件里 `turn/end` 调
  `hx snapshot --no-capture` 或 `hx save`），并明确：
  - 只本地提交，还是 `--push`；
  - 是否需要一个远端锚定/签名；
  - 如何避免每轮产生大量无变化提交（hx 已经会跳过无变化提交）。

## 约束

- 核心 Python 包仍然只用标准库；zstd 的新增依赖只能是可选的（`compression.zstd`、
  `zstandard`、node），不能变成硬依赖。
- 密钥不进包：`.credentials.yaml`、`.env` 的值、MCP 的 token 都不复制。
- 写入必须支持 `dry_run`；已有不同文件默认不覆盖；`--force` 才备份覆盖。
- 所有文本 UTF-8 + LF；路径比较与操作系统无关。
- 不自动安装插件、不自动执行包里的命令、不自动信任钩子。
- 不破坏 Claude Code / Codex 的现有测试和行为。
- 每完成一步更新 `PROGRESS.md`、`CHANGELOG.md` 和相关文档，并提交。

## 完成标准

1. `python -m unittest discover -s tests` 在 Windows/macOS/Linux × Python 3.9–3.14 通过；
2. 真实 DSH 会话可以 `save → 换机器 → load → 继续工作`，技能/指令/MCP 至少各验证一项；
3. DSH 插件能自动保存，且没有 `.harness/` 的项目不被触碰；
4. 文档写清楚哪些验证过、哪些没有；
5. 不泄露任何凭据值。

## 常用命令

```bash
# DSH 专项
python -m unittest tests.test_dsh -v

# 全量
python -m unittest discover -s tests

# 本机真实 DSH 抓取
hx -C <项目> init --agent deepseek-harness
hx -C <项目> capture
hx -C <项目> doctor
hx -C <项目> save

# 模拟另一台机器
HX_HOME=/tmp/home-b hx -C <clone> materialize --dry-run
HX_HOME=/tmp/home-b hx -C <clone> materialize

# 真实 DSH 插件（示例，路径按实际包名）
dsh plugin --profile desktop add ./dsh-plugin
```

如果只能做一件事：先做第 1 步的 DSH 插件自动保存。它把 hx 从“手动工具”变成
DSH 的“会话结束即快照”，也最接近用户说的“每一步都上链”。
