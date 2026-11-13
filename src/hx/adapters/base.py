"""Adapter 基类：接入一个新的 Agent 需要实现的全部接口。

写新 adapter 的步骤见 docs/EXTENDING.md。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..redact import Redactor
from ..util import HARNESS_DIR, MAX_FILE_BYTES, decode_text, iter_files, sha256_bytes

# 锁文件条目的类别。adapter 可以用新的类别名，diff 会原样显示。
CATEGORIES = ("env", "instructions", "settings", "skills", "agents", "commands",
              "rules", "hooks", "mcp", "plugins", "memory", "sessions", "other")


@dataclass
class Check:
    """doctor 的一项检查结果。level: ok / warn / fail。"""
    level: str
    title: str
    hint: str = ""


@dataclass
class Action:
    """materialize 的一步操作结果。status: written / same / conflict / skipped。"""
    status: str
    target: str
    note: str = ""


@dataclass
class CaptureContext:
    """capture 时交给 adapter 的工具箱。

    adapter 只需要调用 ref() 和 put()，锁文件由它们自动登记。
    """
    project: Path                      # 项目根目录
    home: Path                         # 用户主目录
    redactor: Redactor
    options: dict = field(default_factory=dict)   # harness.json 里的 capture 配置
    entries: list = field(default_factory=list)   # 锁文件条目
    secrets: set = field(default_factory=set)     # 需要的密钥名
    skipped: list = field(default_factory=list)   # 被跳过的文件及原因
    info: dict = field(default_factory=dict)      # agent 版本、模型等

    @property
    def harness(self) -> Path:
        return self.project / HARNESS_DIR

    def ref(self, relpath: str, category: str) -> bool:
        """引用项目内已有的文件：只记录哈希，不复制。"""
        p = self.project / relpath
        if not p.is_file():
            return False
        self.entries.append({
            "path": relpath, "category": category, "scope": "project",
            "sha256": sha256_bytes(p.read_bytes()),
        })
        return True

    def ref_tree(self, reldir: str, category: str) -> int:
        n = 0
        for p in iter_files(self.project / reldir):
            n += self.ref(p.relative_to(self.project).as_posix(), category)
        return n

    def put(self, dest: str, data, category: str, target: str | None = None) -> bool:
        """把项目外的内容复制进 .harness/。

        dest:   相对 .harness/ 的路径
        data:   str 或 bytes。文本会先脱敏。
        target: 还原时写到哪里，形如 "user:skills/x/SKILL.md" 或 "project:CLAUDE.local.md"。
                为 None 表示这个文件只留在包里，不还原。
        """
        if isinstance(data, bytes):
            if len(data) > MAX_FILE_BYTES:
                self.skipped.append((dest, "文件过大"))
                return False
            text = decode_text(data)
            data = self.redactor.text(text).encode("utf-8") if text is not None else data
        else:
            data = self.redactor.text(data).encode("utf-8")
        out = self.harness / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        entry = {
            "path": f"{HARNESS_DIR}/{dest}", "category": category, "scope": "bundle",
            "sha256": sha256_bytes(data),
        }
        if target:
            entry["target"] = target
        self.entries.append(entry)
        return True

    def put_tree(self, src: Path, dest: str, category: str, target_prefix: str | None = None,
                 exclude: tuple = ()) -> int:
        n = 0
        for p in iter_files(src):
            rel = p.relative_to(src).as_posix() if src.is_dir() else p.name
            if any(rel == e or rel.startswith(e + "/") for e in exclude):
                continue
            target = f"{target_prefix}/{rel}" if target_prefix else None
            n += self.put(f"{dest}/{rel}", p.read_bytes(), category, target)
        return n


class Adapter:
    """一个 Agent 的接入实现。子类至少要实现 capture 和 materialize。"""

    name = "base"
    #: capture 前会被清空重写的目录（相对 .harness/）。人工维护的文件不要放在这里。
    generated_dirs: tuple = ()

    def detect(self, project: Path, home: Path) -> bool:
        """这台机器、这个项目是否在用这个 Agent。hx init 用它来猜默认值。"""
        return False

    def capture(self, ctx: CaptureContext) -> None:
        """抓取配置和上下文，通过 ctx.ref / ctx.put 登记。
        把 agent 版本和模型写进 ctx.info["agent_version"] / ctx.info["model"]。"""
        raise NotImplementedError

    def user_root(self, home: Path) -> Path:
        """target 为 "user:..." 的文件还原到哪个目录下。"""
        raise NotImplementedError

    def materialize(self, project: Path, home: Path, lock: dict, force: bool) -> list:
        """做 adapter 特有的还原步骤（如合并 MCP 配置、放置记忆）。
        带 target 的普通文件由核心统一还原，这里不用管。返回 Action 列表。"""
        return []

    def doctor(self, project: Path, home: Path, manifest: dict, lock: dict) -> list:
        """检查这台机器能不能跑这个 harness。返回 Check 列表。"""
        return []

    def team_setup(self, project: Path, enable: bool) -> list:
        """团队一键接入：在项目里声明 hx 插件，同事打开项目时 Agent 会提示安装。
        返回被修改的项目文件（相对路径）。不支持的 Agent 返回 None。"""
        return None

    def team_enabled(self, project: Path) -> bool:
        return False

    def transcripts(self, project: Path, home: Path) -> list:
        """原始对话记录文件的路径列表，供 export --include-transcripts 使用。"""
        return []

    def transcript_dir(self, project: Path, home: Path) -> Path | None:
        """import --restore-transcripts 时把对话记录放回哪里。"""
        return None
