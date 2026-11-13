"""对 git 命令行的薄封装。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import HxError


def git(project: Path, *args: str, check: bool = True) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True)
    except FileNotFoundError:
        raise HxError("没有找到 git，请先安装 git。")
    if check and r.returncode != 0:
        raise HxError(f"git {' '.join(args)} 失败：{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def is_repo(project: Path) -> bool:
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=project, capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def head(project: Path) -> str | None:
    out = git(project, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    return out or None


def show_file(project: Path, rev: str, relpath: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{relpath}"], cwd=project, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None
