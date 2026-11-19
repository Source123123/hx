# DeepSeek Harness 适配说明

本文记录 hx 对 DeepSeek Harness（DSH）的接入方式、验证范围和已知限制。实现见
[`src/hx/adapters/deepseek_harness.py`](../src/hx/adapters/deepseek_harness.py)。

## 1. DSH 的文件在哪里

以下路径以 DSH `0.2.2-rc.3` 的默认组合为准；实际值可能被 profile patch 覆盖。

| 内容 | 默认位置 |
|---|---|
| 用户主目录 | `$DSH_HOME`，默认 `~/.dsh` |
| 共享 Agent 目录 | `$DSH_AGENTS_HOME`，默认 `~/.agents` |
| profile | `$DSH_HOME/profiles/<名称>/`，用户设置在同目录的 `cordis.patch.yml` |
| 用户全局指令 | `$DSH_HOME/AGENTS.md`、`~/.agents/AGENTS.md` |
| 项目指令 | 从项目根到工作目录的 `AGENTS.md`、`CLAUDE.md`、`AGENTS.local.md`、`CLAUDE.local.md` |
| 技能 | `<项目>/.dsh/skills`、`<项目>/.agents/skills`、`$DSH_HOME/skills`、`~/.agents/skills` |
| MCP | profile patch 中 `mcp-manager` 行的 `config.servers` |
| 凭据 | `$DSH_HOME/.credentials.yaml`（值不打包） |
| 会话 | `$DSH_HOME/sessions/<项目目录>/<会话>/session.v4.jsonl[.zstd]` |

项目根按最近的 `.git` 祖先确定；没有 `.git` 时用当前工作目录。

## 2. 保存（capture）什么

- **项目内文件只引用、不复制**：`AGENTS.md`、`CLAUDE.md`、`AGENTS.local.md`、
  `CLAUDE.local.md`、`.dsh/skills/**`、`.agents/skills/**`。
- **项目外文件复制进包**：`$DSH_HOME/AGENTS.md`、`$DSH_HOME/skills/**`（跳过 `.system`）、
  `$DSH_HOME/cordis.patch.yml`、profile 的 `cordis.patch.yml`、`~/.agents/AGENTS.md`、
  `~/.agents/skills/**`。
- **只登记、不还原**：
  - `profiles/<名称>/package.json` 的 `dsh.profile.bundles` 和 npm 依赖；
  - `.credentials.yaml` 的 `refs` 名字；
  - `.env` 的变量名和非密钥值（写入 `env.reference`，不覆盖本机 `.env`）。
- **会话摘要**：从会话 JSONL 中取用户原话、改动的文件、工具次数、模型和最后一段答复，
  写入 `.harness/context/sessions/<id>.json`。注入的 runtime/skill/time 消息会被排除。

## 3. 载入（materialize）什么

- 带 `target` 的条目由核心统一放置：
  - `user:AGENTS.md` → `$DSH_HOME/AGENTS.md`
  - `user:skills/...` → `$DSH_HOME/skills/...`
  - `user:profiles/<名称>/cordis.patch.yml` → 对应 profile
  - `user:cordis.patch.yml` → 主目录级 patch
- adapter 自己处理：
  - `shared/AGENTS.md` → `~/.agents/AGENTS.md`
  - `shared-skills/**` → `~/.agents/skills/**`
  - `mcp.json` → 合并进当前 profile 的 `cordis.patch.yml`。

已有不同内容的文件默认保留，`--force` 覆盖并备份为 `.hx-bak`。MCP 只添加没有的同名服务器，
同名但不同的服务器报告冲突、不覆盖。

## 4. 会话解压

DSH 默认写 zstd 压缩的 JSONL（多个独立 frame 串联）。Python 3.9–3.13 没有标准库 zstd，
所以按以下顺序尝试：

1. Python 3.14 的 `compression.zstd`；
2. 已安装的 `zstandard` 模块；
3. `node` 的 `node:zlib`（PATH 里的 node、`HX_NODE`，或
   `$DSH_HOME/dsh-runtimes/*/dependencies/node/bin/node`）。

都没有时跳过会话摘要，并把原因写入 `ctx.skipped`，配置和技能仍正常保存。

压缩文件的原始对话不通过 `hx export --include-transcripts` 导出，因为核心按 UTF-8 文本读取；
需要保留原始对话时，请先确认 DSH 会话的备份方式。

## 5. 已知限制

- DSH 没有像 Claude Code/Codex 那样的命令行插件入口；`hx save` / `hx load` 需要手动运行。
- 没有实现 DSH 会话结束自动保存、会话开始提示和 `hx team on` 的项目技能声明。
- profile patch 整份还原，不按字段合并；不同 DSH 版本或不同 profile 名称可能产生冲突。
- hx 不创建 DSH 的 profile `package.json` 和 bundle 安装；目标机器应先有同名 profile
  （至少启动过一次 DSH），否则只放一个 patch 可能不会被 DSH 当成完整 profile。
- `.credentials.yaml` 只记录名字；`${credential:NAME}` 在目标机器上要由 DSH 凭据提供者解析，
  或设置同名环境变量。
- `mcp-manager` 行的 YAML 读取是窄化实现：只支持块式 YAML、常见的标量和内联 `{}`/`[]`；
  复杂 YAML（锚点、别名、多行折叠）可能读不到，但不会改动原文件。
- DSH 的 shell 命令改动了哪些文件无法从会话里可靠推断；只记录 `write`、`edit`、
  `apply_patch`、`str_replace_editor` 等编辑工具。
- 子 Agent 的会话是独立的 session 目录，adapter 只抓当前项目目录下的会话；没有递归合并父子会话。

## 6. 本地验证

```bash
python -m unittest tests.test_dsh -v
```

覆盖：配置/技能/MCP/凭据/`.env` 抓取与脱敏、会话摘要、跨机器还原、MCP 合并与冲突、
zstd 解码（有 node 时）、中立格式导出与接收。

手工检查真实 DSH 环境：

```bash
hx -C <项目> init --agent deepseek-harness
hx -C <项目> capture
hx -C <项目> doctor
```

模拟另一台机器：

```bash
HX_HOME=/tmp/home-b hx -C <clone> materialize --dry-run
```

## 7. 下一步

优先做原生 DSH 插件，而不是继续扩大 adapter：

1. 一个 DSH profile 插件包，提供 `$hx:save` / `$hx:load` 技能和会话结束自动保存；
2. 读取 `session/end` 或 `turn/end` 事件，后台运行 `hx auto-save --background`；
3. 在会话开始时提示阅读 `.harness/context/HANDOFF.md`（只提示，不自动写入）；
4. 用 DSH 插件自带的技能目录分发启动器，避免用户手动安装 hx；
5. 增加真实的两台机器迁移测试：真 DSH 会话 → clone → 另一台 DSH 打开 → 技能/指令/MCP 生效。

更完整的交接说明见 [CODEX_HANDOFF.md](CODEX_HANDOFF.md)。
