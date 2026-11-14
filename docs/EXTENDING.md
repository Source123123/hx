# 扩展 hx

hx 有三个扩展点，都通过 Python 的 entry point 注册，不需要改 hx 本身的代码。

## 1. 接入新的 Agent（Adapter）

继承 `hx.adapters.base.Adapter`，参考 `src/hx/adapters/claude_code.py`。

```python
from pathlib import Path
from hx.adapters.base import Adapter, CaptureContext, Check

class MyAgentAdapter(Adapter):
    name = "my-agent"
    # capture 前会清空重写的目录（相对 .harness/）
    generated_dirs = ("agent/my-agent", "context/memory", "context/sessions")

    def detect(self, project: Path, home: Path) -> bool:
        return (project / "AGENTS.md").exists()

    def user_root(self, home: Path) -> Path:
        return home / ".my-agent"

    def capture(self, ctx: CaptureContext) -> None:
        # 项目内的文件：引用
        ctx.ref("AGENTS.md", "instructions")
        # 项目外的文件：复制进包。target 说明还原时放到哪里
        ctx.put_tree(self.user_root(ctx.home) / "prompts", "agent/my-agent/user/prompts",
                     "commands", "user:prompts")
        # 需要的环境变量
        ctx.secrets.add("MY_AGENT_API_KEY")
        ctx.info["agent_version"] = "1.0.0"
        ctx.info["model"] = "some-model"

    def doctor(self, project, home, manifest, lock) -> list:
        return [Check("ok", "my-agent 可用")]
```

要点：

- `ctx.ref(路径, 类别)`：项目内已有的文件，只记哈希。
- `ctx.put(包内路径, 内容, 类别, target)`：项目外的内容，复制进包并自动脱敏。`target` 为 `user:相对路径` 或 `project:相对路径`；带 `target` 的文件由核心统一还原，你不用写还原代码。
- `ctx.session(摘要)`：登记一个会话摘要。字段与 `claude_code.digest_session` 的返回值一致：`id, started, ended, git_branch, agent_version, models, prompts, files_changed, tool_counts, last_reply`。摘要全团队累积，核心负责去重和只保留最近的。
- `used_here(project, home)`：这台机器上是否真的用这个 Agent 做过这个项目（一般看有没有会话记录）。返回真时 `hx save` 会自动把你的 Agent 加进项目清单。
- `available(home)`：这台机器是否装了这个 Agent，默认看 `user_root` 目录在不在。没装时抓取和还原都跳过。
- 只有需要合并而不是整文件覆盖的内容（比如 MCP 配置）才需要实现 `materialize`。它必须支持 `dry_run`：预演时不能写文件。
- 跨 Agent：实现 `export_neutral(project)` 把包里属于你的技能、MCP、指令文件导出成 `Neutral`；实现 `adopt(project, home, neutral, force, dry_run)` 接收别的 Agent 的内容。MCP 的中立写法就是 `.mcp.json` 的写法，密钥为 `${NAME}`。写文件请用 `hx.adapters.base.place_file`，它统一处理冲突、备份和预演。
- 完整的参考实现：`src/hx/adapters/codex.py`（会话不按项目分目录、配置是 TOML）和 `src/hx/adapters/claude_code.py`。

在你的包的 `pyproject.toml` 里注册：

```toml
[project.entry-points."hx.adapters"]
my-agent = "my_package:MyAgentAdapter"
```

然后 `hx init --agent my-agent`。

## 2. 增加脱敏规则（Redactor）

注册一个返回规则列表的函数，每条规则是 `(名字, 编译好的正则)`，整个匹配会被替换成 `[REDACTED:名字]`。

```python
import re

def rules():
    return [("employee-id", re.compile(r"\bEMP-\d{6}\b"))]
```

```toml
[project.entry-points."hx.redactors"]
my-company = "my_package:rules"
```

## 3. 替换提炼器（Distiller）

提炼器决定 `HANDOFF.md` 的内容。默认实现只做整理，不调用模型。你可以写一个调用 LLM 的版本，从会话摘要里总结出决策和待办。

```python
from pathlib import Path

def distill(harness: Path, manifest: dict, lock: dict) -> str:
    # 读 harness / "context/sessions/*.json"，返回 Markdown 文本
    ...
```

```toml
[project.entry-points."hx.distillers"]
llm = "my_package:distill"
```

在 `.harness/harness.json` 里把 `"distiller"` 改成 `"llm"` 即可启用。

注意：`HANDOFF.md` 不计入快照 ID，所以提炼器的输出不稳定（比如 LLM 每次措辞不同）不会制造多余的快照，但会产生文件改动。

## 4. 还没有做成扩展点的部分

| 部分 | 现状 | 以后的方向 |
|---|---|---|
| 包格式 | tar.gz（`core.export_bundle`） | OCI 制品，便于放进镜像仓库 |
| 存储 | 全部在 git 里 | 大文件（原始对话）放对象存储 |
| 合并 | 直接用 git 的文本合并 | 锁文件和上下文的专用合并驱动 |
| 指令互转 | 无 | `CLAUDE.md` 与 `AGENTS.md` 之间转换 |
