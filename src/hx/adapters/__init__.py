"""Adapter 注册表。内置 adapter 和通过 entry point 安装的第三方 adapter 都从这里取。"""
from __future__ import annotations

from ..util import HxError
from .base import Adapter


def _builtin() -> dict[str, type]:
    from .claude_code import ClaudeCodeAdapter
    from .codex import CodexAdapter
    from .deepseek_harness import DeepSeekHarnessAdapter
    return {ClaudeCodeAdapter.name: ClaudeCodeAdapter, CodexAdapter.name: CodexAdapter,
            DeepSeekHarnessAdapter.name: DeepSeekHarnessAdapter}


def all_adapters() -> dict[str, type]:
    found = _builtin()
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="hx.adapters") if hasattr(eps, "select") else eps.get("hx.adapters", [])
        for ep in group:
            if ep.name not in found:
                found[ep.name] = ep.load()
    except Exception:
        pass
    return found


def get_adapter(name: str) -> Adapter:
    adapters = all_adapters()
    if name not in adapters:
        raise HxError(f"未知的 Agent 类型 {name!r}。可用：{', '.join(sorted(adapters))}")
    return adapters[name]()
