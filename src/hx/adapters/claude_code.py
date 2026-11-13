"""Claude Code 的 adapter。

抓取范围：
  项目内（引用）：CLAUDE.md、AGENTS.md、.claude/、.mcp.json
  项目外（复制）：~/.claude/ 下的个人指令、设置、skills、agents、commands、rules，
                ~/.claude.json 里的 MCP 服务器，已安装插件清单，
                本项目的记忆和会话记录（提炼成摘要）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from ..util import HARNESS_DIR, env_refs, read_json, write_json
from .base import Action, Adapter, CaptureContext, Check

A = "agent/claude-code"          # 本 adapter 在 .harness/ 下的目录
USER_TREES = {                   # ~/.claude/ 下的目录 -> 类别
    "skills": "skills", "agents": "agents", "commands": "commands",
    "rules": "rules", "output-styles": "other",
}
PROJECT_TREES = {                # .claude/ 下的目录 -> 类别
    "skills": "skills", "agents": "agents", "commands": "commands",
    "rules": "rules", "hooks": "hooks", "output-styles": "other",
}
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def project_slug(project: Path) -> str:
    """Claude Code 存放项目数据的目录名：绝对路径里的非字母数字字符都换成 '-'。"""
    return re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))


class ClaudeCodeAdapter(Adapter):
    name = "claude-code"
    generated_dirs = (A, "context/memory", "context/sessions")

    # ---------- 路径 ----------

    def user_root(self, home: Path) -> Path:
        custom = os.environ.get("CLAUDE_CONFIG_DIR")
        if custom and not os.environ.get("HX_HOME"):
            return Path(custom)
        return home / ".claude"

    def _global_config(self, home: Path) -> Path:
        """~/.claude.json：存放用户级和本地级 MCP 服务器。"""
        inside = self.user_root(home) / ".claude.json"
        return inside if inside.is_file() else home / ".claude.json"

    def _project_data(self, project: Path, home: Path) -> Path:
        return self.user_root(home) / "projects" / project_slug(project)

    def detect(self, project: Path, home: Path) -> bool:
        return ((project / "CLAUDE.md").exists() or (project / ".claude").exists()
                or self._project_data(project, home).exists())

    # ---------- capture ----------

    def capture(self, ctx: CaptureContext) -> None:
        root = self.user_root(ctx.home)
        self._capture_project(ctx)
        self._capture_user(ctx, root)
        self._capture_mcp(ctx)
        self._capture_plugins(ctx, root)
        data = self._project_data(ctx.project, ctx.home)
        ctx.put_tree(data / "memory", "context/memory", "memory")
        latest = self._capture_sessions(ctx, data)
        settings = read_json(root / "settings.json", {}) or {}
        project_settings = read_json(ctx.project / ".claude/settings.json", {}) or {}
        ctx.info["agent_version"] = self._cli_version() or latest.get("agent_version")
        ctx.info["model"] = (project_settings.get("model") or settings.get("model")
                             or (latest.get("models") or [None])[-1])

    def _capture_project(self, ctx: CaptureContext) -> None:
        for f in ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md"):
            ctx.ref(f, "instructions")
        ctx.ref(".claude/settings.json", "settings")
        for d, cat in PROJECT_TREES.items():
            ctx.ref_tree(f".claude/{d}", cat)
        if ctx.ref(".mcp.json", "mcp"):
            ctx.secrets.update(env_refs((ctx.project / ".mcp.json").read_text(encoding="utf-8", errors="replace")))
        # 本地文件通常被 gitignore，换机器会丢，所以复制进包
        local = ctx.project / "CLAUDE.local.md"
        if local.is_file():
            ctx.put(f"{A}/local/CLAUDE.local.md", local.read_bytes(), "instructions",
                    "project:CLAUDE.local.md")
        local_settings = read_json(ctx.project / ".claude/settings.local.json")
        if isinstance(local_settings, dict):
            ctx.put(f"{A}/local/settings.local.json", self._settings_text(ctx, local_settings),
                    "settings", "project:.claude/settings.local.json")

    def _capture_user(self, ctx: CaptureContext, root: Path) -> None:
        f = root / "CLAUDE.md"
        if f.is_file():
            ctx.put(f"{A}/user/CLAUDE.md", f.read_bytes(), "instructions", "user:CLAUDE.md")
        settings = read_json(root / "settings.json")
        if isinstance(settings, dict):
            ctx.put(f"{A}/user/settings.json", self._settings_text(ctx, settings),
                    "settings", "user:settings.json")
        f = root / "keybindings.json"
        if f.is_file():
            ctx.put(f"{A}/user/keybindings.json", f.read_bytes(), "settings", "user:keybindings.json")
        for d, cat in USER_TREES.items():
            # skills/synced 是账号同步下来的内置技能，登录后会自动出现，不用打包
            ctx.put_tree(root / d, f"{A}/user/{d}", cat, f"user:{d}",
                         exclude=("synced",) if d == "skills" else ())

    def _settings_text(self, ctx: CaptureContext, settings: dict) -> str:
        """settings 里 env 的值一律换成 ${NAME}，名字登记为需要的密钥。"""
        settings = json.loads(json.dumps(settings))
        env = settings.get("env")
        if isinstance(env, dict):
            for k in env:
                env[k] = "${%s}" % k
                ctx.secrets.add(k)
        return json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _capture_mcp(self, ctx: CaptureContext) -> None:
        cfg = read_json(self._global_config(ctx.home), {}) or {}
        servers = {}
        for scope, src in (
            ("user", cfg.get("mcpServers")),
            ("local", ((cfg.get("projects") or {}).get(str(ctx.project.resolve())) or {}).get("mcpServers")),
        ):
            for name, spec in (src or {}).items():
                if isinstance(spec, dict):
                    servers[name] = self._normalize_server(ctx, name, spec, scope)
        if servers:
            ctx.put(f"{A}/mcp.json",
                    json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    "mcp")

    def _normalize_server(self, ctx: CaptureContext, name: str, spec: dict, scope: str) -> dict:
        spec = json.loads(json.dumps(spec))
        prefix = re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        if isinstance(spec.get("env"), dict):
            for k in spec["env"]:
                spec["env"][k] = "${%s}" % k
                ctx.secrets.add(k)
        if isinstance(spec.get("headers"), dict):
            for k, v in spec["headers"].items():
                refs = env_refs(str(v))
                if refs:
                    ctx.secrets.update(refs)
                    continue
                var = f"MCP_{prefix}_{re.sub(r'[^A-Za-z0-9]', '_', k).upper()}"
                spec["headers"][k] = "${%s}" % var
                ctx.secrets.add(var)
        if isinstance(spec.get("args"), list):
            spec["args"] = [ctx.redactor.text(a) if isinstance(a, str) else a for a in spec["args"]]
        for key in ("url", "command"):
            if isinstance(spec.get(key), str):
                spec[key] = ctx.redactor.text(spec[key])
        spec["x-hx-scope"] = scope
        return spec

    def _capture_plugins(self, ctx: CaptureContext, root: Path) -> None:
        installed = read_json(root / "plugins/installed_plugins.json", {}) or {}
        markets = read_json(root / "plugins/known_marketplaces.json", {}) or {}
        plugins = []
        raw = installed.get("plugins") if isinstance(installed, dict) else None
        for key, installs in sorted((raw or {}).items()):
            items = installs if isinstance(installs, list) else [installs]
            for it in items:
                if not isinstance(it, dict):
                    continue
                # 项目级安装只记录属于本项目的
                if it.get("projectPath") and Path(it["projectPath"]).resolve() != ctx.project.resolve():
                    continue
                name, _, market = key.partition("@")
                plugins.append({
                    "id": key, "name": name, "marketplace": market,
                    "scope": it.get("scope", "user"), "version": it.get("version"),
                    "commit": it.get("gitCommitSha"),
                    "source": (markets.get(market) or {}).get("source") if isinstance(markets, dict) else None,
                })
        if plugins:
            ctx.put(f"{A}/plugins.json",
                    json.dumps({"plugins": plugins}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    "plugins")

    def _capture_sessions(self, ctx: CaptureContext, data: Path) -> dict:
        """把最近的会话记录提炼成摘要。返回最新一个会话的摘要。"""
        limit = int(ctx.options.get("max_sessions", 20))
        files = sorted(data.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-limit:] if data.is_dir() else []
        latest = {}
        for f in files:
            digest = digest_session(f, ctx.project)
            if not digest["prompts"] and not digest["files_changed"]:
                continue
            ctx.put(f"context/sessions/{digest['id']}.json",
                    json.dumps(digest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "sessions")
            latest = digest
        return latest

    def _cli_version(self) -> str | None:
        if os.environ.get("HX_HOME"):   # 模拟另一台机器时不看本机的 claude
            return None
        exe = shutil.which("claude")
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return None
        m = re.search(r"\d+\.\d+\.\d+", out)
        return m.group(0) if m else None

    # ---------- materialize ----------

    def materialize(self, project: Path, home: Path, lock: dict, force: bool) -> list:
        actions = []
        actions += self._merge_mcp(project)
        # 记忆放回 Claude Code 为"新路径下的这个项目"准备的目录
        src = project / HARNESS_DIR / "context/memory"
        dst = self._project_data(project, home) / "memory"
        if src.is_dir():
            from ..core import place_file
            for p in sorted(x for x in src.rglob("*") if x.is_file()):
                actions.append(place_file(p.read_bytes(), dst / p.relative_to(src), force))
        return actions

    def _merge_mcp(self, project: Path) -> list:
        packed = read_json(project / HARNESS_DIR / A / "mcp.json", {}) or {}
        servers = packed.get("mcpServers") or {}
        if not servers:
            return []
        target = project / ".mcp.json"
        current = read_json(target, {}) or {}
        existing = current.setdefault("mcpServers", {})
        actions, changed = [], False
        for name, spec in sorted(servers.items()):
            spec = {k: v for k, v in spec.items() if not k.startswith("x-hx-")}
            if name not in existing:
                existing[name] = spec
                changed = True
                actions.append(Action("written", f".mcp.json#{name}", "MCP 服务器已加入项目配置"))
            elif existing[name] == spec:
                actions.append(Action("same", f".mcp.json#{name}"))
            else:
                actions.append(Action("conflict", f".mcp.json#{name}", "项目里已有同名但不同的 MCP 服务器，未改动"))
        if changed:
            write_json(target, current)
        return actions

    # ---------- doctor ----------

    def doctor(self, project: Path, home: Path, manifest: dict, lock: dict) -> list:
        checks = []
        want = (lock.get("agent") or {}).get("version")
        have = self._cli_version()
        if not shutil.which("claude"):
            checks.append(Check("fail", "没有找到 claude 命令", "安装 Claude Code：https://code.claude.com/docs"))
        elif want and have and have != want:
            checks.append(Check("warn", f"Claude Code 版本不同：本机 {have}，快照 {want}", "行为可能有差异"))
        else:
            checks.append(Check("ok", f"Claude Code 已安装（{have or '版本未知'}）"))

        # MCP 服务器需要的命令
        servers = {}
        for f in (project / ".mcp.json", project / HARNESS_DIR / A / "mcp.json"):
            servers.update((read_json(f, {}) or {}).get("mcpServers") or {})
        for name, spec in sorted(servers.items()):
            cmd = spec.get("command") if isinstance(spec, dict) else None
            if not cmd:
                continue
            if shutil.which(cmd) or Path(cmd).is_file():
                checks.append(Check("ok", f"MCP 服务器 {name}：命令 {cmd} 可用"))
            else:
                checks.append(Check("fail", f"MCP 服务器 {name}：找不到命令 {cmd}", f"请安装 {cmd}"))

        # 插件
        packed = (read_json(project / HARNESS_DIR / A / "plugins.json", {}) or {}).get("plugins") or []
        installed = (read_json(self.user_root(home) / "plugins/installed_plugins.json", {}) or {}).get("plugins") or {}
        for p in packed:
            if p["id"] in installed:
                checks.append(Check("ok", f"插件 {p['id']} 已安装"))
            else:
                checks.append(Check("warn", f"插件 {p['id']} 未安装",
                                    f"在 Claude Code 里运行 /plugin install {p['id']}"))
        return checks

    # ---------- 原始对话 ----------

    def transcripts(self, project: Path, home: Path) -> list:
        d = self._project_data(project, home)
        return sorted(d.glob("*.jsonl")) if d.is_dir() else []

    def transcript_dir(self, project: Path, home: Path) -> Path:
        return self._project_data(project, home)


# ---------- 会话摘要 ----------

def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + " …(截断)"


def _user_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def digest_session(path: Path, project: Path) -> dict:
    """把一个 .jsonl 会话记录压缩成摘要：用户说了什么、改了哪些文件、用了哪些工具、最后的答复。"""
    prompts, files, tools, models = [], [], Counter(), []
    started = ended = branch = version = None
    last_reply = ""
    root = str(project.resolve())
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict) or d.get("isSidechain"):
                continue
            ts = d.get("timestamp")
            if ts:
                started = started or ts
                ended = ts
            branch = d.get("gitBranch") or branch
            version = d.get("version") or version
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            if d.get("type") == "user" and not d.get("isMeta"):
                text = _user_text(msg).strip()
                # 以 < 开头的是系统注入的内容（命令输出、提醒），不是用户的话
                if text and not text.startswith("<"):
                    prompts.append(_clip(text, 2000))
            elif d.get("type") == "assistant":
                model = msg.get("model")
                if model and not model.startswith("<") and (not models or models[-1] != model):
                    models.append(model)
                for block in msg.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text", "").strip():
                        last_reply = block["text"]
                    elif block.get("type") == "tool_use":
                        tools[block.get("name", "?")] += 1
                        fp = (block.get("input") or {}).get("file_path") or (block.get("input") or {}).get("notebook_path")
                        if block.get("name") in EDIT_TOOLS and isinstance(fp, str):
                            rel = fp[len(root):].lstrip("/") if fp.startswith(root + "/") else fp
                            if rel not in files:
                                files.append(rel)
    return {
        "id": path.stem, "started": started, "ended": ended, "git_branch": branch,
        "agent_version": version, "models": models, "prompts": prompts,
        "files_changed": files, "tool_counts": dict(sorted(tools.items())),
        "last_reply": _clip(last_reply, 1500),
    }
