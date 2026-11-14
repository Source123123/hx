# 给 Codex 的任务说明

> 用法：在装有 Codex CLI 的机器上 clone 本仓库，进入仓库目录启动 Codex，把下面"任务"一节整段发给它。

---

## 任务

你在接手一个叫 hx 的开源工具。它把项目的 Agent 开发环境（配置、技能、MCP、记忆、会话摘要）保存进项目里的 `.harness/` 目录，用 git 记录历史，并能在另一台机器、另一种 Agent 上载入。

之前的开发是在一台**没有 OpenAI 账号、连不上 OpenAI 接口**的机器上完成的。Codex 这一侧有几处只能按推测写，需要你在真实环境里核对并补完。项目主人不写代码，所以请你自己把事情做完、测完，最后用大白话汇报。

### 先读这些

1. `PROGRESS.md`：进度、已验证和未验证的清单
2. `docs/DESIGN.md`：总体设计，重点看 6.1、6.2 和第 7 节
3. `docs/EXTENDING.md`：adapter 接口
4. `src/hx/adapters/codex.py`：你要核对的主要文件，文件开头列了哪些格式核对过、哪些没有
5. `src/hx/adapters/claude_code.py`、`commands/`、`hooks/`、`bin/hx`、`.claude-plugin/`：Claude Code 一侧已经做好的"一键"体验，是你要对标的样子

然后装上并跑通测试：

```bash
pip install -e .
python -m unittest discover -s tests
```

### 第一件事：核对 Codex 会话文件的格式

`digest_rollout()` 把 `~/.codex/sessions/年/月/日/rollout-*.jsonl` 压缩成摘要。下面两种记录的形状是按推测写的，没有对照过真实文件：

- 助手回复：假定为 `type=response_item`，`payload.type=message`，`payload.role=assistant`，文本在 `content[].text`（`type=output_text`）。
- 工具调用：假定 `payload.type` 为 `function_call`、`custom_tool_call` 或 `local_shell_call`；改文件走 `apply_patch`，从补丁文本里的 `*** Update File: 路径` 提取被改的文件。

请这样核对：

1. 在一个临时项目里用 Codex 真实地做一个小任务（让它新建并修改一两个文件），产生一个完整的会话文件。
2. 对照那个文件检查 `digest_rollout()` 的输出：`prompts`（只应有用户真正说的话，不应有 Codex 自动注入的内容）、`files_changed`、`tool_counts`、`models`、`last_reply`、`git_branch` 是否都对。
3. 不对就改代码。把那个真实会话文件**删掉敏感内容并裁短**后放进 `tests/fixtures/`，写一个用它的测试；同时更新 `tests/test_hx.py` 里的 `codex_rollout()` 构造函数，让它和真实形状一致。
4. 更新 `codex.py` 文件开头的"已核对 / 未核对"说明。

### 第二件事：核对其余几处推测

逐条确认，错了就改，并补测试：

- 项目级技能目录到底是哪个。现在同时引用了 `.codex/skills` 和 `.agents/skills`。
- `~/.codex/prompts/` 是否还存在、是否还是自定义提示词的位置。
- `AGENTS.override.md` 是否是真实存在的约定。
- 项目级的 `.codex/config.toml` 是否生效；如果它里面也能配置 MCP 服务器，抓取和跨 Agent 导出要把它算进去（现在只读了用户级的）。
- Codex 是否有按项目保存的"记忆"。如果有，像 Claude Code 的记忆那样抓取和还原（见 `claude_code.py` 里对 `context/memory` 的处理）。
- Codex 的插件清单：如果用户装了 Codex 插件，像 `claude_code.py` 的 `_capture_plugins` 那样记录名字、来源、版本，并在 `doctor` 里提示缺哪些。

### 第三件事：给 Codex 做"一键"体验

目标：Codex 用户和 Claude Code 用户一样，装一次，之后几乎不用敲命令。请对标 Claude Code 侧已有的东西：

| Claude Code 侧（已完成） | 你要在 Codex 侧做的 |
|---|---|
| 仓库本身是一个插件，`/plugin install hx --marketplace Source123123/hx` 一步安装 | 让仓库同时是一个 Codex 插件（或 Codex 支持的等价形式），给出一步安装的方法 |
| `/hx:save`、`/hx:load` 两个命令（`commands/*.md`） | Codex 里的等价入口（技能、自定义提示词或插件命令，按 Codex 实际支持的来） |
| `SessionEnd` 钩子调用 `hx auto-save` | Codex 的会话结束钩子，同样调用 `hx auto-save` |
| `SessionStart` 钩子调用 `hx auto-load`（只报告，不写文件） | Codex 的会话开始钩子，同样调用 `hx auto-load` |
| `hx team on` 在项目里声明插件，同事打开项目时被提示安装 | 给 `CodexAdapter` 实现 `team_setup` / `team_enabled`（如果 Codex 有等价机制） |

注意：

- `bin/hx` 是不依赖 pip 的启动器，钩子和命令都应该通过它调用。
- `hx auto-save` / `hx auto-load` 目前靠环境变量 `CLAUDE_PROJECT_DIR` 找项目目录（见 `cli.py` 的 `_hook_project`）。请查清 Codex 的钩子怎么传项目目录，并让这两个命令同时兼容两边。
- 先查 Codex 当前版本的官方文档和 `codex plugin --help`、`codex features list` 的实际输出，以真实行为为准，不要凭记忆写。某项 Codex 确实不支持，就如实写进 `PROGRESS.md`，不要做假的。
- 做完后更新 `README.md` 的"支持哪些 Agent"表格和安装说明。

### 第四件事：真实环境的端到端验证

在真实的 Codex 上各跑一遍，并把结果写进 `PROGRESS.md` 的"已验证"：

1. Codex 项目：`hx save` → clone 到另一个目录（用 `HX_HOME` 指向一个空目录来模拟另一台机器）→ `hx load` → 用 `codex mcp list` 确认 MCP 服务器被正确读出 → 启动 Codex 确认技能和指令生效。
2. 用 Claude Code 保存的项目，在只有 Codex 的环境里 `hx load`，确认生成的 `AGENTS.md`、技能、MCP 都能被 Codex 用上。
3. 如果你的机器上也有 Claude Code，反过来再验一次；没有就在汇报里说明没验。

### 不能破坏的规则

- **零依赖**：只用 Python 标准库，兼容 Python 3.9（所以 TOML 读取有一个简化的后备解析器，不要删）。
- **密钥不进包**：任何密钥值都不能出现在 `.harness/` 里，只登记环境变量名。改动抓取逻辑后要有测试证明这一点。
- **载入必须经用户确认**：会话开始时不能自动往用户主目录写任何东西。理由见 `docs/DESIGN.md` 6.1。
- **不自动推送**：自动保存只做本地提交，且只提交 `.harness/`。
- **不覆盖别人的内容**：本机没装的 Agent，它在包里的内容必须原样保留；本机已有的不同文件默认不覆盖。
- **不要破坏 Claude Code 一侧**：现有测试必须全部通过；改了公共代码（`core.py`、`adapters/base.py`、`cli.py`）要跑全部测试。
- **三个操作系统都要能用**：Windows 10/11、macOS、Linux。写文件用 `hx.util.write_text`（UTF-8、LF），比较内容用 `sha256_bytes` / `normalize_newlines`，比较路径用 `path_key` / `relative_to_project`，不要自己拼 `/` 或假设换行。你新加的钩子和命令入口在 Windows 上也要能调用（`bin/hx.cmd` 是给命令提示符用的启动器）。推送后看 GitHub Actions 的三系统测试是否全绿，见 `docs/DESIGN.md` 6.3。
- 给用户看的文字用中文，说人话，不用内部术语。
- 每完成一件事就更新 `PROGRESS.md` 并 `git commit`；全部完成后 `git push`。

### 最后的汇报

用不写代码的人能看懂的话写，包括：

1. 四件事各自的结果：做完了什么，改了什么。
2. 哪些推测被证实，哪些是错的、你怎么改的。
3. Codex 用户现在从安装到保存、载入，各需要几步。
4. 仍然没有验证或做不到的事，以及原因。
