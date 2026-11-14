"""OpenAI Codex CLI 的 adapter。

抓取范围：
  项目内（引用）：AGENTS.md、AGENTS.override.md、.codex/config.toml、.agents/skills（兼容 .codex/skills）
  项目外（复制）：~/.codex/ 下的个人指令 AGENTS.md、技能、自定义提示词、config.toml（去掉 MCP 和本机路径后留作参考），
                ~/.agents/skills、用户级及项目级 MCP、插件清单、本项目会话摘要。
  核对：.codex/prompts 仍可用但已弃用；AGENTS.override.md 优先；项目配置仅在受信任时生效。
  记忆：官方的 memories/、memories_v2/ 是用户级生成状态，无受支持的项目级还原接口，因此不打包。

格式核对情况（2026-11-14，对照本机 codex-cli 0.167.0-alpha.4.1 完整会话）：
  已核对：CODEX_HOME、config.toml 的 [mcp_servers.*] 写法（command/args/env/env_vars/url/
          bearer_token_env_var/env_http_headers/http_headers）、skills/.system 是内置技能、
          会话文件 sessions/年/月/日/rollout-*.jsonl 的 session_meta、用户消息、turn_context。
  已核对：助手 output_text、phase=final_answer；custom_tool_call/exec 包含转义的
          tools.apply_patch 补丁；用户消息的来源标记。裁短的真实记录见 tests/fixtures/。
  兼容但未在本机产生：旧版直接调用 function_call、custom_tool_call/apply_patch、local_shell_call。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from ..util import HARNESS_DIR, HxError, env_refs, path_key, read_json, relative_to_project, write_text
from ..portable import localize, read_packed_json
from .base import Action, Adapter, CaptureContext, Check, Neutral, place_file

A = "agent/codex"
SCAN_LIMIT = 500     # 最多检查最近多少个会话文件
ENV_ONLY = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
BEARER_ENV = re.compile(r"^Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class CodexAdapter(Adapter):
    name = "codex"
    title = "Codex CLI"
    generated_dirs = (A,)

    # ---------- 路径 ----------

    def user_root(self, home: Path) -> Path:
        custom = os.environ.get("CODEX_HOME")
        if custom and not os.environ.get("HX_HOME"):
            return Path(custom)
        return home / ".codex"

    def detect(self, project: Path, home: Path) -> bool:
        return any((project / p).exists() for p in ("AGENTS.md", "AGENTS.override.md", ".codex", ".agents/skills")) or self.used_here(project, home)

    def used_here(self, project: Path, home: Path) -> bool:
        return bool(self._session_files(project, home, first_only=True))

    def _session_files(self, project: Path, home: Path, first_only: bool = False) -> list:
        """属于这个项目的会话文件（按时间从旧到新）。Codex 的会话不按项目分目录，要看每个文件开头记录的工作目录。"""
        root = self.user_root(home) / "sessions"
        if not root.is_dir():
            return []
        files = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:SCAN_LIMIT]
        want = path_key(project.resolve())
        found = []
        for f in files:
            cwd = _rollout_cwd(f)
            if cwd and path_key(cwd) == want:
                found.append(f)
                if first_only:
                    break
        return found[::-1]

    # ---------- capture ----------

    def capture(self, ctx: CaptureContext) -> None:
        root = self.user_root(ctx.home)
        for f in ("AGENTS.md", "AGENTS.override.md"):
            ctx.ref(f, "instructions")
        ctx.ref(".codex/config.toml", "settings")
        for d in (".codex/skills", ".agents/skills"):
            ctx.ref_tree(d, "skills")

        for name in ("AGENTS.md", "AGENTS.override.md"):
            f = root / name
            if f.is_file():
                ctx.put(f"{A}/user/{name}", f.read_bytes(), "instructions", f"user:{name}")
        # skills/.system 是 Codex 自带的技能，装好就有，不用打包
        ctx.put_tree(root / "skills", f"{A}/user/skills", "skills", "user:skills", exclude=(".system",))
        # 当前官方用户级技能位置；与旧版 CODEX_HOME/skills 分开保存，不能使用越界的 user:../ 目标。
        ctx.put_tree(ctx.home / ".agents/skills", f"{A}/shared-skills", "skills")
        ctx.put_tree(root / "prompts", f"{A}/user/prompts", "commands", "user:prompts")

        config_text = _read(root / "config.toml")
        config = parse_toml(config_text)
        if config_text.strip():
            # 通用设置只留作参考，不自动还原：里面多是个人偏好和本机路径
            kept = strip_sections(config_text, ("mcp_servers", "projects", "plugins", "marketplaces"))
            if kept.strip():
                ctx.put(f"{A}/user/config.reference.toml", kept, "settings")
        servers = {}
        project_config = parse_toml(_read(ctx.project / ".codex/config.toml"))
        effective = effective_mcp(config, project_config)
        for name, spec in effective.items():
            if isinstance(spec, dict):
                scope = "project" if name in (project_config.get("mcp_servers") or {}) else "user"
                servers[name] = codex_to_neutral(spec, ctx, name, scope)
        if servers:
            ctx.put(f"{A}/mcp.json",
                    json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "mcp")

        plugins = self._plugin_inventory(ctx.home, ctx.project)
        if plugins is not None:
            safe = [{k: p[k] for k in ("pluginId", "name", "marketplaceName", "version", "enabled", "source", "marketplaceSource")
                     if k in p} for p in plugins if isinstance(p, dict) and p.get("installed", True)]
            if safe:
                ctx.put(f"{A}/plugins.json", json.dumps({"plugins": safe}, ensure_ascii=False, indent=2) + "\n", "plugins")
        elif not os.environ.get("HX_HOME"):
            ctx.skipped.append(("Codex 插件清单", "本机 codex plugin list --json 不可用，未记录插件"))

        latest = {}
        limit = int(ctx.options.get("max_sessions", 30))   # 0 表示不保存会话摘要
        for f in self._session_files(ctx.project, ctx.home)[-max(limit, 1):]:
            digest = digest_rollout(f, ctx.project)
            if digest["prompts"] or digest["files_changed"]:
                if limit > 0:
                    ctx.session(digest)
                latest = digest
        ctx.info["agent_version"] = self._cli_version() or latest.get("agent_version")
        model = project_config.get("model") or config.get("model")
        ctx.info["model"] = model if isinstance(model, str) else (latest.get("models") or [None])[-1]

    def _cli_version(self) -> str | None:
        if os.environ.get("HX_HOME"):   # 模拟另一台机器时不看本机的 codex
            return None
        exe = shutil.which("codex")
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10,
                                 encoding="utf-8", errors="replace").stdout
        except Exception:
            return None
        m = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", out)
        return m.group(0) if m else None

    # ---------- materialize / 跨 Agent ----------

    def materialize(self, project: Path, home: Path, lock: dict, force: bool, dry_run: bool = False) -> list:
        packed = read_packed_json(project / HARNESS_DIR / A / "mcp.json", project, home, {}) or {}
        servers = packed.get("mcpServers") or {}
        actions = merge_mcp_toml(self.user_root(home) / "config.toml",
                                 {n: s for n, s in servers.items() if s.get("x-hx-scope") != "project"}, dry_run)
        actions += merge_mcp_toml(project / ".codex/config.toml",
                                  {n: s for n, s in servers.items() if s.get("x-hx-scope") == "project"}, dry_run)
        base = project / HARNESS_DIR / A / "shared-skills"
        if base.is_dir():
            for p in sorted(x for x in base.rglob("*") if x.is_file()):
                actions.append(place_file(localize(p.read_bytes(), project, home),
                                          home / ".agents/skills" / p.relative_to(base), force, dry_run))
        return actions

    def export_neutral(self, project: Path) -> Neutral:
        n = Neutral()
        h = project / HARNESS_DIR / A
        for base in (project / ".codex/skills", project / ".agents/skills", h / "user/skills", h / "shared-skills"):
            if base.is_dir():
                for p in sorted(x for x in base.rglob("*") if x.is_file()):
                    n.skills[p.relative_to(base).as_posix()] = p.read_bytes()
        for name, spec in ((read_json(h / "mcp.json", {}) or {}).get("mcpServers") or {}).items():
            if isinstance(spec, dict) and spec.get("x-hx-enabled") is not False:
                n.mcp[name] = {k: v for k, v in spec.items() if not k.startswith("x-hx-")}
        n.instructions = [f for f in ("AGENTS.override.md", "AGENTS.md") if _read(project / f).strip()][:1]
        return n

    def adopt(self, project: Path, home: Path, neutral: Neutral, force: bool, dry_run: bool = False) -> list:
        actions = []
        if neutral.instructions and not (project / "AGENTS.md").is_file():
            # Codex 不支持引入别的文件，所以写一段指路的话，而不是复制内容（复制会两头不一致）
            lines = ["# 项目指令", "",
                     "本项目的指令写在下面这些文件里。开始工作前请完整阅读并遵守："]
            lines += [f"- `{f}`" for f in neutral.instructions]
            lines += ["", "项目当前的进展和待办见交接简报：`.harness/context/HANDOFF.md`。", ""]
            actions.append(place_file("\n".join(lines).encode(), project / "AGENTS.md", force, dry_run))
        root = project / ".agents/skills"
        for rel, data in sorted(neutral.skills.items()):
            actions.append(place_file(data, root / rel, force, dry_run))
        actions += merge_mcp_toml(self.user_root(home) / "config.toml", neutral.mcp, dry_run)
        return actions

    # ---------- doctor ----------

    def doctor(self, project: Path, home: Path, manifest: dict, lock: dict) -> list:
        checks = []
        want = ((lock.get("agents") or {}).get(self.name) or {}).get("version")
        have = self._cli_version()
        if not shutil.which("codex"):
            checks.append(Check("fail", "没有找到 codex 命令", "安装 Codex CLI：npm install -g @openai/codex"))
        elif want and have and have != want:
            checks.append(Check("warn", f"Codex 版本不同：本机 {have}，快照 {want}", "行为可能有差异"))
        else:
            checks.append(Check("ok", f"Codex CLI 已安装（{have or '版本未知'}）"))
        config = parse_toml(_read(self.user_root(home) / "config.toml"))
        project_config = parse_toml(_read(project / ".codex/config.toml"))
        for name, spec in sorted(effective_mcp(config, project_config).items()):
            if isinstance(spec, dict) and spec.get("enabled") is False:
                continue
            cmd = spec.get("command") if isinstance(spec, dict) else None
            if not cmd:
                continue
            if shutil.which(cmd) or Path(cmd).is_file():
                checks.append(Check("ok", f"MCP 服务器 {name}：命令 {cmd} 可用"))
            else:
                checks.append(Check("fail", f"MCP 服务器 {name}：找不到命令 {cmd}", f"请安装 {cmd}"))
        wanted = (read_packed_json(project / HARNESS_DIR / A / "plugins.json", project, home, {}) or {}).get("plugins") or []
        if wanted:
            installed = self._plugin_inventory(home, project)
            if installed is None:
                checks.append(Check("warn", "无法读取本机 Codex 插件清单", "运行 codex plugin list --json 检查，必要时升级 Codex。"))
            else:
                have_plugins = {p.get("pluginId"): p for p in installed if isinstance(p, dict) and p.get("installed", True)}
                for p in wanted:
                    key = p.get("pluginId")
                    have_plugin = have_plugins.get(key)
                    if not have_plugin:
                        checks.append(Check("warn", f"缺少 Codex 插件：{key}", f"先添加可信插件市场，再运行 codex plugin add {key}；来源见 .harness/agent/codex/plugins.json。"))
                    elif p.get("enabled", True) and not have_plugin.get("enabled", True):
                        checks.append(Check("warn", f"Codex 插件未启用：{key}", "在 Codex 插件设置中启用。"))
                    elif p.get("version") and have_plugin.get("version") != p["version"]:
                        checks.append(Check("warn", f"Codex 插件版本不同：{key}", f"本机 {have_plugin.get('version')}，快照 {p['version']}。"))
        return checks

    def _plugin_inventory(self, home: Path, project: Path) -> list | None:
        """使用 CLI 的公开 JSON，避免猜测私有缓存布局；模拟主目录时不接触真实安装。"""
        if os.environ.get("HX_HOME"):
            return []
        exe = shutil.which("codex")
        if not exe:
            return None
        try:
            result = subprocess.run([exe, "plugin", "list", "--json"], cwd=project,
                                    env=dict(os.environ, CODEX_HOME=str(self.user_root(home))),
                                    capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
            data = json.loads(result.stdout)
            return data["installed"] if result.returncode == 0 and isinstance(data, dict) and isinstance(data.get("installed"), list) else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def transcripts(self, project: Path, home: Path) -> list:
        return self._session_files(project, home)

    def team_enabled(self, project: Path) -> bool:
        config = parse_toml(_read(project / ".codex/config.toml"))
        plugin = (config.get("plugins") or {}).get("hx@hx") or {}
        market = (config.get("marketplaces") or {}).get("hx") or {}
        return plugin.get("enabled") is True and market.get("source") == _hx_market_source()

    def team_setup(self, project: Path, enable: bool) -> list:
        """项目声明可信的 Git 市场；信任、插件安装和钩子审阅仍由 Codex 负责。"""
        path = project / ".codex/config.toml"
        text = _read(path)
        config = parse_toml(text)
        market = (config.get("marketplaces") or {}).get("hx") or {}
        if market and market.get("source") != _hx_market_source():
            raise HxError("项目已声明另一个名为 hx 的 Codex 插件市场，未覆盖；请先检查 .codex/config.toml。")
        if not enable and not market and "hx@hx" not in (config.get("plugins") or {}):
            return []
        updated = text
        if enable:
            updated = set_toml_fields(updated, ("marketplaces", "hx"),
                                      {"source_type": "git", "source": _hx_market_source()})
        updated = set_toml_fields(updated, ("plugins", "hx@hx"), {"enabled": enable})
        if updated == text:
            return []
        write_text(path, updated)
        return [".codex/config.toml"]


# ---------- MCP：Codex 写法 <-> 中立写法 ----------

def codex_to_neutral(spec: dict, ctx: CaptureContext, name: str = "server", scope: str = "user") -> dict:
    """Codex 的 MCP 服务器配置 -> 中立写法（与 .mcp.json 相同，密钥一律写成 ${NAME}）。"""
    out: dict = {}
    if isinstance(spec.get("url"), str):
        out["type"] = "http"
        out["url"] = ctx.redactor.text(spec["url"])
        headers = {}
        for h, v in (spec.get("http_headers") or {}).items():
            var = "MCP_" + re.sub(r"[^A-Za-z0-9]", "_", name + "_" + h).upper()
            headers[h] = "${%s}" % var
            ctx.secrets.add(var)
        for h, var in (spec.get("env_http_headers") or {}).items():
            headers[h] = "${%s}" % var
            ctx.secrets.add(var)
        if isinstance(spec.get("bearer_token_env_var"), str):
            headers["Authorization"] = "Bearer ${%s}" % spec["bearer_token_env_var"]
            ctx.secrets.add(spec["bearer_token_env_var"])
        if headers:
            out["headers"] = headers
    else:
        out["command"] = ctx.redactor.text(str(spec.get("command", "")))
        if isinstance(spec.get("args"), list):
            out["args"] = [ctx.redactor.text(a) if isinstance(a, str) else a for a in spec["args"]]
        env = {}
        for k in (spec.get("env") or {}):        # 写死在配置里的值一律当作密钥
            env[k] = "${%s}" % k
            ctx.secrets.add(k)
        for k in (spec.get("env_vars") or []):   # 从环境变量透传
            if isinstance(k, str):
                env[k] = "${%s}" % k
                ctx.secrets.add(k)
        if env:
            out["env"] = env
    if spec.get("enabled") is False:
        out["x-hx-enabled"] = False
    out["x-hx-scope"] = scope
    return out


def effective_mcp(user: dict, project: dict) -> dict:
    """Codex 配置按字段分层合并；项目同名服务器仅覆盖它声明的字段。"""
    def merge(a, b):
        out = dict(a)
        for key, value in b.items():
            out[key] = merge(out[key], value) if isinstance(out.get(key), dict) and isinstance(value, dict) else value
        return out
    return merge(user.get("mcp_servers") or {}, project.get("mcp_servers") or {})


def _hx_market_source() -> str:
    repo = os.environ.get("HX_PLUGIN_REPO") or "Source123123/hx"
    return repo if "://" in repo or repo.startswith("git@") else f"https://github.com/{repo}.git"


def set_toml_fields(text: str, section: tuple, values: dict) -> str:
    """仅修改指定表里的几个标量，保留其他设置和注释（不重写整份用户 TOML）。"""
    lines = text.splitlines()
    start = end = None
    for index, line in enumerate(lines):
        header = _HEADER.match(line)
        if header:
            if start is not None:
                end = index
                break
            if tuple(_split_key(header.group(1))) == section:
                start = index + 1
    if start is None:
        return text.rstrip() + ("\n\n" if text.strip() else "") + \
            "[" + ".".join(_toml_key(k) for k in section) + "]\n" + \
            "".join(f"{_toml_key(k)} = {_toml_value(v)}\n" for k, v in values.items())
    end = len(lines) if end is None else end
    pending = dict(values)
    for index in range(start, end):
        key, sep, _ = lines[index].partition("=")
        key = key.strip().strip("\"'")
        if sep and key in pending:
            if _simple_value(lines[index].partition("=")[2]) != pending[key]:
                lines[index] = f"{_toml_key(key)} = {_toml_value(pending[key])}"
            pending.pop(key)
    lines[end:end] = [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in pending.items()]
    return "\n".join(lines) + "\n"


def neutral_to_codex(spec: dict) -> tuple:
    """中立写法 -> Codex 的配置表。返回 (表, 无法表达而丢掉的项)。"""
    out, dropped = {}, []
    if spec.get("x-hx-enabled") is False:
        out["enabled"] = False
    if isinstance(spec.get("url"), str):
        out["url"] = spec["url"]
        literal, from_env = {}, {}
        for h, v in (spec.get("headers") or {}).items():
            v = str(v)
            m = BEARER_ENV.match(v)
            if h.lower() == "authorization" and m:
                out["bearer_token_env_var"] = m.group(1)
            elif ENV_ONLY.match(v):
                from_env[h] = ENV_ONLY.match(v).group(1)
            elif env_refs(v):
                dropped.append(f"请求头 {h}")
            else:
                literal[h] = v
        if literal:
            out["http_headers"] = literal
        if from_env:
            out["env_http_headers"] = from_env
    else:
        out["command"] = spec.get("command", "")
        if spec.get("args"):
            out["args"] = list(spec["args"])
        literal, passthrough = {}, []
        for k, v in (spec.get("env") or {}).items():
            v = str(v)
            m = ENV_ONLY.match(v)
            if m and m.group(1) == k:
                passthrough.append(k)
            elif env_refs(v):
                dropped.append(f"环境变量 {k}")   # Codex 只能按原名透传，表达不了 A=${B}
            else:
                literal[k] = v
        if literal:
            out["env"] = literal
        if passthrough:
            out["env_vars"] = sorted(passthrough)
    return out, dropped


def _toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def server_toml(name: str, table: dict) -> str:
    head = f"mcp_servers.{_toml_key(name)}"
    lines = [f"[{head}]"]
    subs = []
    for k, v in table.items():
        if isinstance(v, dict):
            subs.append((k, v))
        else:
            lines.append(f"{_toml_key(k)} = {_toml_value(v)}")
    for k, v in subs:
        lines += ["", f"[{head}.{_toml_key(k)}]"]
        lines += [f"{_toml_key(kk)} = {_toml_value(vv)}" for kk, vv in v.items()]
    return "\n".join(lines) + "\n"


def merge_mcp_toml(config: Path, servers: dict, dry_run: bool = False) -> list:
    """把 MCP 服务器追加到 Codex 的 config.toml：只添加没有的，同名但不同的不动。"""
    if not servers:
        return []
    text = _read(config)
    existing = (parse_toml(text).get("mcp_servers") or {})
    actions, blocks = [], []
    for name, spec in sorted(servers.items()):
        table, dropped = neutral_to_codex(spec)
        label = f"{config}#mcp_servers.{name}"
        if name in existing:
            same = {k: v for k, v in existing[name].items() if v not in (None, [], {})} == table
            actions.append(Action("same", label) if same else
                           Action("conflict", label, "Codex 配置里已有同名但不同的 MCP 服务器，未改动"))
            continue
        note = "MCP 服务器加入 Codex 配置"
        if dropped:
            note += f"（{'、'.join(dropped)} 无法转换，需要手动补上）"
        blocks.append(server_toml(name, table))
        actions.append(Action("written", label, note))
    if blocks and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
        write_text(config, text.replace("\r\n", "\n") + sep + "\n".join(blocks))
    return actions


# ---------- TOML 读取 ----------

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def strip_sections(text: str, prefixes: tuple) -> str:
    """去掉名字以 prefixes 之一开头的 TOML 段落，其余原样保留。"""
    out, skipping = [], False
    for line in text.splitlines():
        m = _HEADER.match(line)
        if m:
            top = m.group(1).split(".", 1)[0].strip().strip("\"'")
            skipping = top in prefixes
        if not skipping:
            out.append(line)
    return "\n".join(out).strip() + "\n"


def parse_toml(text: str) -> dict:
    """解析 TOML。Python 3.11+ 用标准库；更老的版本用下面的简化解析（够读 Codex 的配置）。"""
    if not text.strip():
        return {}
    try:
        import tomllib
    except ImportError:
        return parse_toml_simple(text)
    try:
        return tomllib.loads(text)
    except Exception:
        return parse_toml_simple(text)


def _split_key(key: str) -> list:
    return [p.strip().strip("\"'") for p in re.findall(r'"[^"]*"|\'[^\']*\'|[^.]+', key)]


def _simple_value(raw: str):
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return {k.strip().strip("\"'"): _simple_value(v)
                for k, v in re.findall(r'([A-Za-z0-9_\-"\']+)\s*=\s*("(?:[^"\\]|\\.)*"|[^,}]+)', raw[1:-1])}
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1]
    try:
        return json.loads(raw)
    except ValueError:
        return raw.split("#", 1)[0].strip()


def parse_toml_simple(text: str) -> dict:
    """简化的 TOML 解析：支持段落、单行的字符串/数字/布尔/数组/内联表。不支持多行值。"""
    root: dict = {}
    table = root
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _HEADER.match(line)
        if m and "=" not in stripped.split("]")[0]:
            table = root
            for part in _split_key(m.group(1)):
                table = table.setdefault(part, {})
            continue
        if "=" in stripped:
            key, _, raw = stripped.partition("=")
            parts = _split_key(key.strip())
            target = table
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = _simple_value(raw)
    return root


# ---------- 会话摘要 ----------

_PATCH_FILE = re.compile(r"^\*\*\* (?:(?:Update|Add|Delete) File|Move to): (.+)$", re.M)
_JS_PATCH = re.compile(r'\btools\.apply_patch\s*\(\s*("(?:[^"\\]|\\.)*")')


def _patch_texts(payload: dict) -> list:
    """只读补丁，不执行会话中的代码。code mode 把补丁包在 JSON 兼容的 JS 字符串里。"""
    texts = _strings(payload.get("input")) + _strings(_maybe_json(payload.get("arguments")))
    if payload.get("name", "").rsplit(".", 1)[-1] == "apply_patch":
        return texts
    if payload.get("name", "").rsplit(".", 1)[-1] == "exec":
        return [value for text in texts for literal in _JS_PATCH.findall(text)
                for value in _strings(_maybe_json(literal))]
    return []


def _user_texts(payload: dict, texts: list) -> list:
    meta = payload.get("internal_chat_message_metadata_passthrough") or {}
    provenance = meta.get("content_item_metadata") if isinstance(meta, dict) else None
    if isinstance(provenance, list) and len(provenance) == len(texts):
        return [text for text, item in zip(texts, provenance)
                if isinstance(item, dict) and (item.get("provenance") or {}).get("type") == "user"]
    # 旧版没有来源标记：只排除已知的注入块，保留用户自己输入的 XML、HTML 等。
    injected = ("# AGENTS.md instructions", "<environment_context>", "<permissions instructions>",
                "<turn_aborted>", "<user_instructions>", "<skill>")
    return [text for text in texts if text.strip() and not text.lstrip().startswith(injected)]


def _first_record(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            d = json.loads(fh.readline())
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _rollout_cwd(path: Path) -> str | None:
    d = _first_record(path)
    payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
    return payload.get("cwd") if d.get("type") == "session_meta" else None


def _maybe_json(value):
    """工具调用的参数常常是一段 JSON 文本，解开它才能拿到里面真正的字符串。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _strings(value) -> list:
    """把任意嵌套的值里的字符串都取出来。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + " …(截断)"


def digest_rollout(path: Path, project: Path) -> dict:
    """把 Codex 的一个会话文件压缩成摘要，字段与 Claude Code 的摘要一致。

    每行是 {"timestamp", "type", "payload"}。
    已对照真实文件：session_meta、turn_context、用户来源标记、助手 output_text、
    code mode 的 exec/tools.apply_patch。工具次数按会话中记录的调用名统计（exec 不猜拆为子调用）。
    文件列表来自编辑补丁；无法从任意 shell 命令可靠推断改过哪些文件。
    """
    prompts, files, tools, models = [], [], Counter(), []
    started = ended = branch = version = None
    session_id = path.stem
    last_reply = ""
    root = str(project.resolve())
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            ts = d.get("timestamp")
            if ts:
                started = started or ts
                ended = ts
            p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            kind = d.get("type")
            if kind == "session_meta":
                session_id = p.get("id") or session_id
                version = p.get("cli_version") or version
                git = p.get("git") if isinstance(p.get("git"), dict) else {}
                branch = git.get("branch") or branch
            elif kind == "turn_context":
                model = p.get("model")
                if isinstance(model, str) and (not models or models[-1] != model):
                    models.append(model)
            elif kind == "response_item":
                ptype = p.get("type")
                if ptype == "message":
                    texts = [c.get("text", "") for c in (p.get("content") or [])
                             if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text")]
                    if p.get("role") == "user":
                        mine = [t.strip() for t in _user_texts(p, texts) if t.strip()]
                        if mine:
                            prompts.append(_clip("\n".join(mine), 2000))
                    elif p.get("role") == "assistant" and p.get("phase") != "analysis":
                        text = "\n".join(t for t in texts if t.strip())
                        if text.strip():
                            last_reply = text
                elif ptype in ("function_call", "custom_tool_call", "local_shell_call"):
                    tools[p.get("name") or ptype] += 1
                    blob = "\n".join(_patch_texts(p))
                    for fp in _PATCH_FILE.findall(blob):
                        fp = fp.strip().strip("\"'")
                        rel = relative_to_project(fp, root)
                        if rel not in files:
                            files.append(rel)
    return {
        "id": session_id, "started": started, "ended": ended, "git_branch": branch,
        "agent_version": version, "models": models, "prompts": prompts,
        "files_changed": files, "tool_counts": dict(sorted(tools.items())),
        "last_reply": _clip(last_reply, 1500),
    }
