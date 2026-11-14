"""Adapter 基类：接入一个新的 Agent 需要实现的全部接口。

写新 adapter 的步骤见 docs/EXTENDING.md。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..redact import Redactor
from ..util import (HARNESS_DIR, MAX_FILE_BYTES, decode_text, iter_files, normalize_newlines,
                    sha256_bytes, write_text)

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
    info: dict = field(default_factory=dict)      # agent 版本、模型等（每个 adapter 抓取前清空）
    agent: str = ""                               # 正在抓取的 adapter 名，自动写进条目
    sessions: int = 0                             # 本次写入的会话摘要数

    @property
    def harness(self) -> Path:
        return self.project / HARNESS_DIR

    def ref(self, relpath: str, category: str) -> bool:
        """引用项目内已有的文件：只记录哈希，不复制。"""
        p = self.project / relpath
        if not p.is_file():
            return False
        if any(e["path"] == relpath for e in self.entries):
            return True     # 另一个 adapter 已经登记过（比如两边都认的 AGENTS.md）
        entry = {"path": relpath, "category": category, "scope": "project",
                 "sha256": sha256_bytes(p.read_bytes())}
        if self.agent:
            entry["agent"] = self.agent
        self.entries.append(entry)
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
        data = normalize_newlines(data)     # 包里的文本一律 LF，在任何系统上都一样
        out = self.harness / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        entry = {
            "path": f"{HARNESS_DIR}/{dest}", "category": category, "scope": "bundle",
            "sha256": sha256_bytes(data),
        }
        if target:
            entry["target"] = target
        if self.agent:
            entry["agent"] = self.agent
        self.entries.append(entry)
        return True

    def session(self, digest: dict) -> None:
        """写入一个会话摘要。摘要按团队累积：别人机器上的会话不会因为你这次抓取而消失，
        由核心统一登记进锁文件并只保留最近的若干个。"""
        digest = {**digest, "agent": self.agent}
        text = json.dumps(digest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        out = self.harness / "context/sessions" / f"{digest['id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_text(out, self.redactor.text(text))
        self.sessions += 1

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


def place_file(data: bytes, dest: Path, force: bool, dry_run: bool = False) -> Action:
    """把内容写到 dest。已有不同内容时默认不动；force 时先备份再覆盖。
    dry_run 为真时不写文件，只返回将会发生什么。"""
    note = ""
    if dest.is_file():
        if normalize_newlines(dest.read_bytes()) == normalize_newlines(data):
            return Action("same", str(dest))    # 只有换行风格不同也算相同
        if not force:
            return Action("conflict", str(dest), "已有不同内容，未改动（加 --force 覆盖，原文件会备份）")
        note = "覆盖，原文件备份为 .hx-bak"
        if not dry_run:
            shutil.copy2(dest, dest.with_name(dest.name + ".hx-bak"))
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return Action("written", str(dest), note)


@dataclass
class Neutral:
    """与具体 Agent 无关的可交换内容。一个 Agent 导出，另一个 Agent 接收。"""
    skills: dict = field(default_factory=dict)     # "技能名/相对路径" -> bytes
    mcp: dict = field(default_factory=dict)        # 服务器名 -> 规范（.mcp.json 的写法，密钥为 ${NAME}）
    instructions: list = field(default_factory=list)   # 项目内指令文件的相对路径


class Adapter:
    """一个 Agent 的接入实现。子类至少要实现 capture 和 user_root。"""

    name = "base"
    #: 给人看的名字
    title = "Agent"
    #: capture 前会被清空重写的目录（相对 .harness/）。人工维护的文件不要放在这里。
    generated_dirs: tuple = ()

    def detect(self, project: Path, home: Path) -> bool:
        """这个项目看起来是否在用这个 Agent（宽松判断）。hx init 用它来猜默认值。"""
        return False

    def used_here(self, project: Path, home: Path) -> bool:
        """这台机器上是否真的用这个 Agent 做过这个项目（严格判断，通常看有没有会话记录）。
        hx save 用它来自动把新的 Agent 加进清单。"""
        return False

    def available(self, home: Path) -> bool:
        """这台机器上是否装有这个 Agent。没装的话，抓取和还原都会跳过它，
        包里别人留下的这部分内容原样保留。"""
        return self.user_root(home).is_dir()

    def capture(self, ctx: CaptureContext) -> None:
        """抓取配置和上下文，通过 ctx.ref / ctx.put / ctx.session 登记。
        把 agent 版本和模型写进 ctx.info["agent_version"] / ctx.info["model"]。"""
        raise NotImplementedError

    def user_root(self, home: Path) -> Path:
        """target 为 "user:..." 的文件还原到哪个目录下。"""
        raise NotImplementedError

    def materialize(self, project: Path, home: Path, lock: dict, force: bool, dry_run: bool = False) -> list:
        """做 adapter 特有的还原步骤（如合并 MCP 配置、放置记忆）。
        带 target 的普通文件由核心统一还原，这里不用管。返回 Action 列表。
        dry_run 为真时不能写任何文件，只返回将会发生什么。"""
        return []

    def export_neutral(self, project: Path) -> Neutral:
        """把包里属于自己的内容导出成中立格式，供别的 Agent 接收。"""
        return Neutral()

    def adopt(self, project: Path, home: Path, neutral: Neutral, force: bool, dry_run: bool = False) -> list:
        """接收别的 Agent 的内容：项目是用别的 Agent 保存的，而这台机器用的是自己。返回 Action 列表。"""
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
        """import --restore-transcripts 时把对话记录放回哪里。None 表示不支持。"""
        return None
