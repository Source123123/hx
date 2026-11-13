"""提炼器：把会话摘要和人工维护的上下文合成一份交接简报 HANDOFF.md。

默认实现不调用任何模型，只做整理。要换成调用 LLM 的实现，
注册一个 `hx.distillers` entry point（见 docs/EXTENDING.md）。
"""
from __future__ import annotations

from pathlib import Path

from .util import read_json


def _first_line(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def builtin_distiller(harness: Path, manifest: dict, lock: dict) -> str:
    ctx = harness / "context"
    out = ["# 交接简报", "",
           "> 本文件由 `hx capture` 自动生成，请勿手改。它是给接手这个项目的人或 Agent 看的。", ""]
    agent = lock.get("agent") or {}
    out += ["## 快照", "",
            f"- 快照 ID：`{lock.get('snapshot')}`",
            f"- Agent：{agent.get('kind')} {agent.get('version') or ''}".rstrip(),
            f"- 模型：{lock.get('model') or '未知'}",
            f"- 对应代码提交：`{lock.get('code_commit') or '无'}`", ""]

    out += ["## 先读这些", "",
            "- [intent.md](intent.md)：项目目标和设计思路",
            "- [tasks.md](tasks.md)：进行中和待办的任务"]
    decisions = sorted(p for p in (ctx / "decisions").glob("*.md") if p.name != "README.md") \
        if (ctx / "decisions").is_dir() else []
    for p in decisions:
        out.append(f"- [decisions/{p.name}](decisions/{p.name})：{_first_line(p)}")
    memory = sorted(p for p in (ctx / "memory").rglob("*") if p.is_file()) if (ctx / "memory").is_dir() else []
    for p in memory:
        rel = p.relative_to(ctx).as_posix()
        out.append(f"- [{rel}]({rel})：Agent 记忆")
    out.append("")

    sessions = []
    if (ctx / "sessions").is_dir():
        sessions = [read_json(p) for p in (ctx / "sessions").glob("*.json")]
        sessions = sorted((s for s in sessions if s), key=lambda s: s.get("ended") or "", reverse=True)
    limit = int((manifest.get("capture") or {}).get("handoff_sessions", 5))
    if sessions:
        out += [f"## 最近的会话（共 {len(sessions)} 个，显示最近 {min(limit, len(sessions))} 个）", ""]
    for s in sessions[:limit]:
        out += [f"### {(s.get('ended') or '')[:16].replace('T', ' ')}　分支 {s.get('git_branch') or '-'}　"
                f"模型 {', '.join(s.get('models') or []) or '-'}", ""]
        prompts = s.get("prompts") or []
        if prompts:
            out.append("用户的要求：")
            out.append("")
            for p in prompts[:8]:
                text = " ".join(p.split())
                out.append(f"- {text[:300]}{'…' if len(text) > 300 else ''}")
            if len(prompts) > 8:
                out.append(f"- （另有 {len(prompts) - 8} 条，见 sessions/{s['id']}.json）")
            out.append("")
        files = s.get("files_changed") or []
        if files:
            shown = "、".join(f"`{f}`" for f in files[:15])
            out += [f"改动的文件：{shown}{' 等' if len(files) > 15 else ''}", ""]
        if s.get("last_reply"):
            out += ["最后的答复：", ""]
            out += [f"> {line}" for line in s["last_reply"].splitlines()]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def get_distiller(name: str):
    if name in (None, "", "builtin"):
        return builtin_distiller
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="hx.distillers") if hasattr(eps, "select") else eps.get("hx.distillers", [])
        for ep in group:
            if ep.name == name:
                return ep.load()
    except Exception:
        pass
    from .util import HxError
    raise HxError(f"找不到提炼器 {name!r}。请检查 harness.json 的 distiller 字段。")
