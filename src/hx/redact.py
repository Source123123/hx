"""脱敏：把文本里的密钥换成占位符。

规则是可扩展的：往 RULES 里追加 (名字, 正则) 即可，
也可以通过 `hx.redactors` entry point 注册（见 docs/EXTENDING.md）。
脱敏基于规则，不能保证没有遗漏。
"""
from __future__ import annotations

import re
from collections import Counter

# (规则名, 正则)。整个匹配会被替换成 [REDACTED:规则名]。
RULES: list[tuple[str, re.Pattern]] = [
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("google-key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9_\-\.=]{20,}")),
    ("url-credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)")),
]

# 名字像密钥的赋值：password = "xxx"、"api_key": "xxx"、TOKEN=xxx
_ASSIGN = re.compile(
    r"""(?ix)
    (?P<key>["']?[A-Za-z0-9_\-]*(?:secret|token|password|passwd|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|credential)[A-Za-z0-9_\-]*["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<q>["']?)
    (?P<val>[^\s"',;}]{8,})
    (?P=q)
    """
)


def _load_plugins() -> None:
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="hx.redactors") if hasattr(eps, "select") else eps.get("hx.redactors", [])
        for ep in group:
            for rule in ep.load()():
                RULES.append(rule)
    except Exception:
        pass


_load_plugins()


class Redactor:
    """对文本做脱敏，并统计每条规则命中了多少次。"""

    def __init__(self, rebaser=None):
        self.hits: Counter = Counter()
        self.rebaser = rebaser      # hx.portable.Rebaser：把本机路径和私有词换成占位符

    def text(self, s: str) -> str:
        for name, pat in RULES:
            s, n = pat.subn(f"[REDACTED:{name}]", s)
            if n:
                self.hits[name] += n

        def assign(m: re.Match) -> str:
            val = m.group("val")
            # 已经是占位符或环境变量引用的不算
            if val.startswith(("[REDACTED", "${", "$")) or val.lower() in ("true", "false", "null", "none"):
                return m.group(0)
            self.hits["named-secret"] += 1
            q = m.group("q")
            return f"{m.group('key')}{m.group('sep')}{q}[REDACTED:named-secret]{q}"

        s = _ASSIGN.sub(assign, s)
        return self.rebaser.out(s) if self.rebaser else s

    def summary(self) -> str:
        hits = Counter(self.hits)
        if self.rebaser:
            hits.update(self.rebaser.hits)
        if not hits:
            return "未发现需要处理的内容"
        return "，".join(f"{k}×{v}" for k, v in sorted(hits.items()))
