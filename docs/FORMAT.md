# 格式规范

本文档定义 `.harness/` 目录的结构和其中各文件的格式。面向需要读取、生成或校验 hx 包的工具开发者。

- 清单格式版本：`hx/v1`
- 锁文件格式版本：`3`

## 1. 目录结构

```
.harness/
├── harness.json                清单（见第 2 节）
├── harness.lock.json           锁文件（见第 3 节）
├── .gitattributes              内容为 "* -text"，禁止 git 转换换行
├── agent/
│   ├── claude-code/
│   │   ├── user/               来自 ~/.claude：CLAUDE.md、settings.json、skills/、agents/、commands/、rules/
│   │   ├── local/              项目内未纳入版本管理的本地文件：CLAUDE.local.md、settings.local.json
│   │   ├── mcp.json            MCP 服务器（中立写法，见第 5 节）
│   │   └── plugins.json        已安装插件清单
│   ├── codex/
│   │   ├── user/               来自 ~/.codex：AGENTS.md、AGENTS.override.md、skills/、prompts/、config.reference.toml
│   │   ├── shared-skills/      来自 ~/.agents/skills，载入时放回该目录
│   │   ├── plugins.json        codex plugin list --json 的已安装清单，仅用于体检
│   │   └── mcp.json            用户级和项目级 MCP 服务器（中立写法）
│   └── deepseek-harness/
│       ├── user/               来自 $DSH_HOME：AGENTS.md、skills/、env.reference
│       ├── profile/<名称>/     profile 的 cordis.patch.yml
│       ├── shared/             来自 ~/.agents：AGENTS.md
│       ├── shared-skills/      来自 ~/.agents/skills，载入时放回该目录
│       ├── credentials.json    凭据名字（不含值），仅用于体检
│       ├── plugins.json        profile 的 bundles、npm 依赖和已安装版本
│       └── mcp.json            MCP 服务器（中立写法，见第 5 节）
└── context/
    ├── intent.md               项目目标和设计思路。人工维护
    ├── tasks.md                进行中和待办。人工维护
    ├── decisions/              决策记录，一条一个文件。人工维护
    ├── claims.json             外部状态声明（见第 9 节）
    ├── memory/                 Agent 的长期记忆
    ├── sessions/<id>.json      会话摘要（见第 4 节）
    └── HANDOFF.md              交接简报。自动生成，不计入快照 ID
```

约定：

- 所有文本文件为 UTF-8 编码、LF 换行。
- 所有路径为相对路径，使用 `/` 分隔。
- 文本内容中的本机路径和私有名称以占位符表示（见第 10 节）。
- `intent.md`、`tasks.md`、`decisions/` 由人或 Agent 维护，hx 不会覆盖。其余由 hx 生成。
- 每个 Agent 只重写自己的 `agent/<名称>/` 目录。本机未安装的 Agent，其目录保持不变。

## 2. 清单 harness.json

```json
{
  "apiVersion": "hx/v1",
  "name": "demo",
  "agents": ["claude-code", "codex"],
  "capture": { "max_sessions": 30, "handoff_sessions": 5 },
  "auto_save": true,
  "distiller": "builtin",
  "secrets": ["DB_PASSWORD", "GITHUB_TOKEN"],
  "extra_secrets": [],
  "resources": [
    { "name": "docs-index", "path": ".rag/index", "sources": ["docs/**/*.md"],
      "build": "python tools/build_index.py", "note": "向量模型 bge-small" }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `apiVersion` | 字符串 | 清单格式版本。当前为 `hx/v1`；不一致时 hx 拒绝读取 |
| `name` | 字符串 | 项目名，默认取目录名 |
| `agents` | 字符串数组 | 项目登记的 Agent。第一个为主 Agent |
| `capture.max_sessions` | 整数 | 保留的会话摘要数量，默认 30。设为 0 表示不保存会话摘要 |
| `capture.handoff_sessions` | 整数 | 交接简报中展示的会话数量，默认 5 |
| `auto_save` | 布尔 | 是否允许会话结束钩子自动保存 |
| `distiller` | 字符串 | 生成交接简报的提炼器，默认 `builtin` |
| `secrets` | 字符串数组 | 运行所需的环境变量名。由 hx 在抓取时写入 |
| `extra_secrets` | 字符串数组 | 人工追加的环境变量名，会并入 `secrets` |
| `resources` | 对象数组 | 可重建资源。`name`、`path`（产物的相对路径）、`build`（重建命令）必填；`sources`（源文件通配符）、`note` 可选 |

旧版清单使用 `"agent": {"kind": "claude-code"}` 表示单个 Agent，仍可读取。

## 3. 锁文件 harness.lock.json

```json
{
  "lockVersion": 3,
  "hx": "0.5.0",
  "snapshot": "23be233bba52",
  "agents": {
    "claude-code": { "version": "2.1.323", "model": "claude-opus-5-5" },
    "codex": { "version": "0.169.0", "model": "gpt-6.1-sol" }
  },
  "agent": { "kind": "claude-code", "version": "2.1.323" },
  "model": "claude-opus-5-5",
  "code_commit": "027930293cae36f81101046df63e0a0b217177e2",
  "origin": { "platform": "macOS", "machine": "3f9a1c0e" },
  "resources": { "docs-index": { "sources": 42, "sources_sha": "9b1d…" } },
  "entries": [
    { "path": "CLAUDE.md", "category": "instructions", "scope": "project",
      "agent": "claude-code", "sha256": "…" },
    { "path": ".harness/agent/claude-code/user/skills/deploy/SKILL.md", "category": "skills",
      "scope": "bundle", "agent": "claude-code", "target": "user:skills/deploy/SKILL.md", "sha256": "…" }
  ]
}
```

### 3.1 顶层字段

| 字段 | 说明 |
|---|---|
| `lockVersion` | 锁文件格式版本。`1`：单 Agent；`2`：多 Agent；`3`：包内文本使用占位符。高于当前支持的版本时 hx 拒绝读取 |
| `hx` | 写入该锁文件的 hx 版本 |
| `snapshot` | 快照 ID（见 3.3） |
| `agents` | 每个 Agent 的版本和模型 |
| `agent`、`model` | 最近一次实际执行抓取的 Agent 及其模型，用于提交标记和展示 |
| `code_commit` | 抓取时最近一个改动了 `.harness/` 之外内容的提交 |
| `origin` | 最近一次改变了内容的保存发生在哪：`platform` 为操作系统，`machine` 为机器与用户的匿名标识（不可反推主机名）。内容未变时沿用旧值 |
| `resources` | 每个可重建资源在保存时的源文件数量；源文件不超过 2000 个时附带内容指纹 |
| `entries` | 条目列表，按 `path` 排序 |

### 3.2 条目字段

| 字段 | 说明 |
|---|---|
| `path` | 相对项目根目录的路径 |
| `category` | 类别：`env`、`instructions`、`settings`、`skills`、`agents`、`commands`、`rules`、`hooks`、`mcp`、`plugins`、`memory`、`sessions`、`context`、`other` |
| `scope` | `project`：项目内已有的文件，仅引用；`bundle`：位于 `.harness/` 内的文件 |
| `sha256` | 内容哈希。文本文件先将 CRLF 统一为 LF 再计算，二进制文件按原样计算 |
| `agent` | 可选。该条目所属的 Agent |
| `target` | 可选。还原位置，形如 `user:<相对 Agent 配置目录的路径>` 或 `project:<相对项目的路径>`。没有该字段的文件只保留在包内 |

`target` 解析后必须位于对应的基准目录之内，否则 hx 拒绝还原。

### 3.3 快照 ID

快照 ID 是下列内容的 SHA-256 的前 12 位十六进制字符：`agents` 字段，以及所有条目的 `(path, sha256)` 按路径排序后的列表。

由此可得：

- 内容相同则 ID 相同，与机器、操作系统、保存时间无关。
- `code_commit`、`origin`、`resources` 和 `HANDOFF.md` 不参与计算。

## 4. 会话摘要

每个会话一个文件，文件名为会话 ID。

```json
{
  "id": "4273e7da-8f98-53e9-9f1c-cc8ef79f3582",
  "agent": "claude-code",
  "started": "2026-11-13T16:08:33.450Z",
  "ended": "2026-11-13T16:19:02.118Z",
  "git_branch": "main",
  "agent_version": "2.1.323",
  "models": ["claude-opus-5-5"],
  "prompts": ["add a login page"],
  "files_changed": ["src/login.py"],
  "tool_counts": { "Bash": 12, "Edit": 3 },
  "last_reply": "Done."
}
```

| 字段 | 说明 |
|---|---|
| `prompts` | 用户输入的原文，经过脱敏，单条超过 2000 字符时截断。不含 Agent 自动注入的内容 |
| `files_changed` | 该会话中被编辑工具改动过的文件，相对项目根目录 |
| `tool_counts` | 各工具的调用次数 |
| `last_reply` | Agent 的最后一段文字答复，超过 1500 字符时截断 |

## 5. MCP 服务器的中立写法

`agent/<名称>/mcp.json` 采用与 Claude Code 的 `.mcp.json` 相同的结构，所有密钥以 `${变量名}` 表示。

```json
{
  "mcpServers": {
    "db":  { "command": "dbmcp", "env": { "DB_PASSWORD": "${DB_PASSWORD}" } },
    "api": { "type": "http", "url": "https://example.com/mcp",
             "headers": { "Authorization": "Bearer ${API_TOKEN}" } }
  }
}
```

与 Codex `config.toml` 的对应关系：

| 中立写法 | Codex |
|---|---|
| `env` 中 `"K": "${K}"` | `env_vars = ["K"]` |
| `env` 中的字面值 | `[mcp_servers.<名称>.env]` |
| `headers` 中 `"Authorization": "Bearer ${V}"` | `bearer_token_env_var = "V"` |
| `headers` 中 `"H": "${V}"` | `env_http_headers = { H = "V" }` |
| `headers` 中的字面值 | `http_headers = { H = "…" }` |

以 `x-hx-` 开头的键为 hx 内部使用，还原时移除。

Codex 的 `x-hx-scope` 为 `user` 或 `project`，决定还原至用户级或项目级
`.codex/config.toml`；缺省按旧格式当作用户级。配置按字段合并后保存项目的有效 MCP。
`x-hx-enabled: false` 保留禁用状态，跨 Agent 导出时跳过这些服务器。
HTTP 字面请求头一律改为 `${MCP_<服务器>_<请求头>}`（非字母数字改成下划线并大写），
只登记变量名。项目里的原始配置仍是引用，不会被 hx 自动替换。

`agent/codex/plugins.json` 包含 `plugins` 数组，每项保留公开 CLI 输出中的
`pluginId`、`name`、`marketplaceName`、`version`、`enabled`、`source`、`marketplaceSource`。
来源会经过脱敏和路径占位符处理；清单没有还原目标，不会自动安装或启用插件。
这些均为可选内容，锁文件格式仍为 3，旧清单和旧锁文件仍可读取。

### 5.1 DeepSeek Harness 的 MCP 写法

DSH 把 MCP 服务器放在 `mcp-manager` 行的 `config.servers` 中，密钥写成
`${credential:NAME}`。hx 保存时转成第 5 节的中立写法：`transport: stdio`
对应 `command`/`args`/`env`，`transport: streamable-http` 对应 `type: http`/`url`/`headers`；
`${credential:NAME}` 和 `Bearer ${credential:NAME}` 分别变成 `${NAME}` 和
`Bearer ${NAME}`，名字登记进清单。载入时再变回 `${credential:NAME}`，只添加
DSH 配置里没有的同名服务器，同名但不同的服务器不覆盖。

DSH 的会话默认是 `$DSH_HOME/sessions/<项目目录>/<会话>/session.v4.jsonl.zstd`。
adapter 用 Python 3.14 的 `compression.zstd`、`zstandard` 模块或 `node:zlib`
解压后提炼摘要；没有可用的解码器时只在锁文件的跳过信息里说明，不影响配置。

## 6. 提交标记

快照提交的说明末尾包含以下标记（git trailer）：

| 标记 | 含义 |
|---|---|
| `Hx-Snapshot` | 快照 ID |
| `Hx-Agent` | Agent 及版本，例如 `claude-code@2.1.323` |
| `Hx-Model` | 模型 |
| `Hx-Code` | 对应的代码提交 |

安装 `hx hook` 后，普通代码提交也会带上 `Hx-Snapshot`。

## 7. 包文件 .hxb

`.hxb` 是 gzip 压缩的 tar 归档：

```
hx-bundle.json                  元数据：format、name、snapshot、agents、transcripts
project/<相对路径>              .harness/ 的全部文件，以及锁文件引用的项目文件
transcripts/<agent>/<文件名>    可选。脱敏后的原始对话记录
```

导入时只接受普通文件，拒绝包含 `..` 或绝对路径的成员。

## 8. 兼容性规则

| 情形 | 行为 |
|---|---|
| 新版 hx 读取旧格式（`lockVersion` 1 或 2、清单使用 `agent.kind`） | 正常读取；再次保存时升级为当前格式 |
| 旧版 hx 读取更新的 `lockVersion` | 拒绝并提示升级 |
| `apiVersion` 不一致 | 拒绝并提示升级 |
| 未知的 `category` | 原样保留并在比较结果中显示 |
| 条目缺少 `agent` | 带 `target` 的条目归属主 Agent |

## 9. 外部状态声明 claims.json

```json
{
  "claims": [
    { "id": "c9824e5", "text": "测试库里有 ID 102 的订单" },
    { "id": "c32000c", "text": "模型权重已下载", "check": { "type": "path", "value": "models/weights.bin" } }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `id` | 由 `text` 的哈希得到，相同的说法只保留一条 |
| `text` | 说法本身 |
| `check` | 可选。`type` 为 `path`、`env` 或 `command`，`value` 为核对的对象 |

核对只做存在性检查。实现不得因声明而执行任何命令。

## 10. 占位符

包内的文本使用以下占位符。写入包时替换，内容离开包时换回本机的值。

| 占位符 | 含义 | 载入时 |
|---|---|---|
| `{{hx:project}}` | 项目根目录 | 换成本机的项目路径 |
| `{{hx:home}}` | 用户主目录 | 换成本机的主目录 |
| `[[代称]]` | 用户登记的私有词 | 本机对照表中有该代称则换回原词，否则保持不变 |

规则：

- 路径只在其后紧跟分隔符、引号、空白、标点或结尾时替换，前缀相同的其他路径不受影响。
- 换回时一律使用 `/` 作为分隔符，在 JSON 和 TOML 中无需转义，在 Windows 上同样有效。
- 条目的 `sha256` 按包内的占位符形式计算，因此在不同机器上往返不改变哈希。
- 私有词对照表位于本机的 `~/.hx/terms.json`，不属于包的一部分。
