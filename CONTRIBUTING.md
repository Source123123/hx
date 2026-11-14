# 贡献指南

感谢你为 hx 做贡献。本文档说明开发环境、测试方法和必须遵守的约定。

## 开发环境

需要 Python 3.9 或更新版本，以及 git 2.22 或更新版本。hx 没有运行依赖，也没有开发依赖。

```bash
git clone https://github.com/Source123123/hx
```

```bash
pip install -e ./hx
```

## 测试

运行全部测试：

```bash
python -m unittest discover -s tests
```

测试在临时目录中模拟多台机器，不会读写你真实的 Agent 配置。需要手工模拟另一台机器时，把环境变量 `HX_HOME` 指向一个空目录。

在多个 Python 版本上运行（需要 [uv](https://docs.astral.sh/uv/)）：

```bash
for v in 3.9 3.10 3.11 3.12 3.13 3.14; do PYTHONPATH=src uv run -q --no-project --python $v python -m unittest discover -s tests; done
```

每次推送和每个拉取请求都会在 Windows、macOS、Linux 上的 Python 3.9 至 3.14 运行全部测试。合并前必须全部通过。

## 必须遵守的约定

以下约定保证 hx 的安全性和可移植性。违反其中任何一条的改动不会被接受。

### 安全

- **密钥的值不得进入 `.harness/`。** 改动抓取逻辑时，必须有测试证明这一点。
- **载入必须经过用户确认。** 不得在会话开始等自动时机向用户主目录写入内容。
- **不自动推送。** 自动保存只做本地提交，且只提交 `.harness/`。
- **不覆盖已有内容。** 本机已有的不同文件默认保留；本机未安装的 Agent，其在包内的内容必须原样保留。
- **不执行包中的内容。** 包里带来的任何文本都不得被当作命令运行，包括资源的重建命令和声明的核对。
- **进包的文本必须经过 `ctx.put` 或 `ctx.session`。** 它们负责脱敏、路径占位符和私有词代称。包内内容离开 `.harness/` 时必须经过 `hx.portable.localize`。

### 可移植性

- **只使用 Python 标准库**，并兼容 Python 3.9。
- **写文件**使用 `hx.util.write_text`（UTF-8、LF），不要直接调用 `Path.write_text`。
- **比较内容**使用 `hx.util.sha256_bytes` 或 `normalize_newlines`，不要直接比较字节。
- **比较路径**使用 `hx.util.path_key` 或 `relative_to_project`，不要手工拼接分隔符。
- **调用外部程序**时指定 `encoding="utf-8", errors="replace"`。

### 格式兼容

- 改动清单或锁文件的结构时，同步更新 [docs/FORMAT.md](docs/FORMAT.md)。
- 新版必须能读取旧格式。不兼容的改动必须提高格式版本号，并补充"旧格式仍可读取"的测试。

### 面向用户的文字

- 使用中文，表述直接，不使用内部术语。
- 错误信息要说明发生了什么，以及用户下一步该做什么。

## 代码结构

| 位置 | 职责 |
|---|---|
| `src/hx/cli.py` | 命令行、输出、交互确认 |
| `src/hx/core.py` | 流程编排，与具体 Agent 无关 |
| `src/hx/adapters/` | 各 Agent 的文件位置与格式 |
| `src/hx/redact.py` | 脱敏规则 |
| `src/hx/portable.py` | 路径占位符、私有词代称、分享前检查 |
| `src/hx/extras.py` | 可重建资源、外部状态声明 |
| `src/hx/handoff.py` | 交接简报 |
| `src/hx/gitutil.py` | git 封装 |
| `commands/`、`hooks/hooks.json`、`bin/`、`.claude-plugin/` | Claude Code 插件 |
| `codex-skills/`、`hooks/codex.json`、`.codex-plugin/`、`.agents/plugins/` | Codex 插件 |

接入新的 Agent 见 [docs/EXTENDING.md](docs/EXTENDING.md)。

## 提交改动

1. 为改动补充测试。修复缺陷时，先写出能复现该缺陷的测试。
2. 行为有变化时更新相应文档，并在 [CHANGELOG.md](CHANGELOG.md) 的未发布部分记录。
3. 提交说明用一句话写明做了什么。
4. 如实说明验证情况：哪些在真实环境中验证过，哪些只用构造的数据测过。未经真实环境验证的部分，在 [PROGRESS.md](PROGRESS.md) 的"未验证"中登记。

## 报告问题

- 缺陷和功能建议：提交 [issue](https://github.com/Source123123/hx/issues)。
- 安全问题：按 [SECURITY.md](SECURITY.md) 私下报告。
