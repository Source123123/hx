"""让包里的内容"可搬运"：把只在这台机器、这个人身上成立的东西换成占位符，载入时再换回来。

三件事：
  1. 路径改写：本机的项目目录和主目录 <-> {{hx:project}} / {{hx:home}}。
     既不泄露用户名，也让记忆里的路径在新机器上仍然指向对的地方。
  2. 私有词代称：用户自己登记的敏感词（客户名、内部域名）<-> [[代称]]。
     对照表只存在本机（~/.hx/terms.json），不进包。没有对照表的人看到的是代称，文字依然通顺。
  3. 审查：分享前扫描包里可能敏感、而规则没把握处理的内容，交给人判断。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import socket
import sys
from collections import Counter
from pathlib import Path

from .util import HARNESS_DIR, LOCK, HxError, decode_text, iter_files, read_json, write_json

PROJECT_TOKEN = "{{hx:project}}"
HOME_TOKEN = "{{hx:home}}"
_AFTER_PATH = r"(?=[/\\\"'\s:,;)\]>}]|$)"     # 路径后面必须是分隔符或结束，避免 /root 误伤 /rootfs
_ALIAS = re.compile(r"\[\[([^\[\]\n]{1,60})\]\]")


# ---------- 私有词对照表（只在本机） ----------

def terms_file(home: Path) -> Path:
    return home / ".hx" / "terms.json"


def load_terms(home: Path) -> dict:
    data = read_json(terms_file(home), {}) or {}
    terms = data.get("terms") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in (terms or {}).items() if k and v}


def save_terms(home: Path, terms: dict) -> None:
    write_json(terms_file(home), {"terms": dict(sorted(terms.items()))})


def add_term(home: Path, real: str, alias: str) -> None:
    real, alias = real.strip(), alias.strip()
    if len(real) < 2:
        raise HxError("要隐藏的词至少两个字符，太短会误伤正常文字。")
    if not alias or any(c in alias for c in "[]\n") or len(alias) > 60:
        raise HxError("代称不能为空、不能含方括号，且不超过 60 个字符。")
    terms = load_terms(home)
    if alias in terms.values() and terms.get(real) != alias:
        raise HxError(f"代称 {alias!r} 已经用在别的词上了，换一个。")
    terms[real] = alias
    save_terms(home, terms)


# ---------- 路径改写与代称替换 ----------

def _variants(path: Path) -> list:
    p = str(path)
    found = {p, p.replace("\\", "/"), p.replace("\\", "\\\\")}
    return sorted((v for v in found if len(v) >= 4), key=len, reverse=True)


class Rebaser:
    """本机写法 <-> 可搬运写法。out() 在保存时用，back() 在载入时用。"""

    def __init__(self, project: Path, home: Path, terms: dict | None = None):
        self.project, self.home = project.resolve(), home.resolve()
        self.terms = terms if terms is not None else load_terms(home)
        self.hits: Counter = Counter()
        flags = re.IGNORECASE if os.name == "nt" else 0
        self._paths = []
        # 先换项目目录（它通常在主目录里面），再换主目录
        for token, base in ((PROJECT_TOKEN, self.project), (HOME_TOKEN, self.home)):
            for v in _variants(base):
                self._paths.append((re.compile(re.escape(v) + _AFTER_PATH, flags), token))
        self._terms = sorted(self.terms.items(), key=lambda kv: len(kv[0]), reverse=True)
        self._aliases = {alias: real for real, alias in self.terms.items()}

    def out(self, text: str) -> str:
        for pattern, token in self._paths:
            text, n = pattern.subn(lambda m: token, text)
            if n:
                self.hits["本机路径"] += n
        for real, alias in self._terms:
            n = text.count(real)
            if n:
                text = text.replace(real, f"[[{alias}]]")
                self.hits["私有词"] += n
        return text

    def back(self, text: str) -> str:
        if PROJECT_TOKEN in text or HOME_TOKEN in text:
            # 一律用 / ：在 Windows 上也有效，而且放进 JSON、TOML 里不需要转义
            text = text.replace(PROJECT_TOKEN, self.project.as_posix()).replace(HOME_TOKEN, self.home.as_posix())
        if self._aliases and "[[" in text:
            text = _ALIAS.sub(lambda m: self._aliases.get(m.group(1), m.group(0)), text)
        return text


def localize(data: bytes, project: Path, home: Path) -> bytes:
    """把包里的内容换成本机写法。二进制原样返回。包里的内容离开 .harness/ 之前都要过这一步。"""
    if b"{{hx:" not in data and b"[[" not in data:
        return data
    text = decode_text(data)
    if text is None:
        return data
    return Rebaser(project, home).back(text).encode("utf-8")


def read_packed_json(path: Path, project: Path, home: Path, default=None):
    """读包里的 JSON 并换成本机写法。"""
    import json
    try:
        return json.loads(localize(path.read_bytes(), project, home).decode("utf-8"))
    except (OSError, ValueError):
        return default


# ---------- 来源标记 ----------

PLATFORM_NAMES = {"win32": "Windows", "cygwin": "Windows", "darwin": "macOS", "linux": "Linux"}


def platform_name(platform: str | None = None) -> str:
    return PLATFORM_NAMES.get(platform or sys.platform, platform or sys.platform)


def machine_id(home: Path) -> str:
    """这台机器（加这个用户）的匿名标识。只用来判断"是不是同一台机器"，反推不出主机名。"""
    return hashlib.sha256(f"{socket.gethostname()}|{home.resolve()}".encode()).hexdigest()[:8]


# ---------- 审查 ----------

_UUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
AUDIT_RULES = [
    ("邮箱", re.compile(r"(?<![\w.+-])(?!git@|noreply@)[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("IP 地址", re.compile(r"(?<![\d.])(?!127\.0\.0\.1|0\.0\.0\.0)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    ("内网域名", re.compile(r"\b[A-Za-z0-9][A-Za-z0-9.-]*\.(?:internal|corp|intranet|lan|local)\b")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("其他人的主目录", re.compile(r"(?:/Users/|/home/)[A-Za-z0-9._-]+|[A-Za-z]:\\{1,2}Users\\{1,2}[^\\\s\"']+")),
]
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{24,}")


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _looks_random(token: str) -> bool:
    if _UUID.match(token) or _HEX.match(token):
        return False     # 会话 ID、哈希、提交号
    if not (re.search(r"[A-Za-z]", token) and re.search(r"\d", token)):
        return False
    return _entropy(token) >= 4.0


def audit(project: Path) -> list:
    """扫描 .harness/，列出可能敏感、需要人看一眼的内容。返回 (文件, 行号, 类别, 片段) 列表。"""
    findings = []
    root = project / HARNESS_DIR
    for p in iter_files(root):
        if p.name == LOCK:
            continue     # 里面全是哈希
        text = decode_text(p.read_bytes())
        if text is None:
            continue
        rel = p.relative_to(project).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            for kind, pattern in AUDIT_RULES:
                for m in pattern.finditer(line):
                    findings.append((rel, lineno, kind, m.group(0)[:80]))
            for m in _TOKEN.finditer(line):
                token = m.group(0)
                if _looks_random(token):
                    findings.append((rel, lineno, "像密钥的随机字符串", token[:6] + "…" + token[-3:]))
    return findings
