"""对 git 命令行的薄封装。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import HxError


def git(project: Path, *args: str, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise HxError("没有找到 git，请先安装 git。")
    if check and r.returncode != 0:
        raise HxError(f"git {' '.join(args)} 失败：{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def is_repo(project: Path) -> bool:
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=project, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def head(project: Path) -> str | None:
    out = git(project, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    return out or None


def show_file(project: Path, rev: str, relpath: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{relpath}"], cwd=project, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def code_head(project: Path, exclude: str) -> str | None:
    """最近一个改动了 exclude 目录之外内容的提交，也就是"代码"的当前版本。"""
    if not head(project):
        return None
    out = git(project, "log", "-1", "--format=%H", "--", ".", f":(exclude){exclude}", check=False).strip()
    return out or None
