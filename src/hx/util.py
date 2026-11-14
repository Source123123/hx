"""通用小工具：哈希、JSON 读写、路径。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

HARNESS_DIR = ".harness"
MANIFEST = "harness.json"
LOCK = "harness.lock.json"
MAX_FILE_BYTES = 1_000_000  # 超过这个大小的文件不进包
MAX_TREE_FILES = 2000       # 一个目录最多复制这么多文件，防止误把依赖目录、数据目录整个打进包
MAX_TREE_BYTES = 50_000_000
# 遍历目录时一律跳过：版本库、依赖、虚拟环境、缓存
SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
                       ".mypy_cache", ".pytest_cache", ".ruff_cache"})


class HxError(Exception):
    """给用户看的错误，CLI 会直接打印信息而不是堆栈。"""


def normalize_newlines(data: bytes) -> bytes:
    """文本文件统一成 LF 换行。Windows 上的编辑器和 git 常把换行变成 CRLF，
    不统一的话同一份内容在不同系统上哈希不同，会被误判为"改过"。二进制文件原样返回。"""
    if b"\r\n" not in data or b"\x00" in data[:8192]:
        return data
    return data.replace(b"\r\n", b"\n")


def sha256_bytes(data: bytes) -> str:
    """内容哈希。对换行风格不敏感（见 normalize_newlines）。"""
    return hashlib.sha256(normalize_newlines(data)).hexdigest()


def write_text(path: Path, text: str) -> None:
    """用 UTF-8 和 LF 换行写文件，在任何系统上结果都一样。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def path_key(p, flavor=os.path) -> str:
    """用于比较的路径：统一分隔符；在 Windows 上不区分大小写。
    flavor 是路径规则（默认本机的；测试里传 ntpath 来模拟 Windows）。"""
    return flavor.normcase(flavor.normpath(str(p))).replace("\\", "/")


def relative_to_project(file_path: str, project, flavor=os.path) -> str:
    """把会话记录里的绝对路径变成相对项目的路径（用 / 分隔）。不在项目里的原样返回。"""
    root, fp = path_key(project, flavor), path_key(file_path, flavor)
    if fp.startswith(root + "/"):
        return flavor.normpath(file_path).replace("\\", "/")[len(root) + 1:]
    return file_path


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HxError(f"无法解析 JSON 文件 {path}: {e}")


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def home_dir() -> Path:
    """用户主目录。HX_HOME 可以覆盖它，用于测试或模拟另一台机器。"""
    return Path(os.environ.get("HX_HOME") or Path.home())


def iter_files(root: Path):
    """按固定顺序列出 root 下的所有普通文件（不跟随符号链接目录）。"""
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_file():
                yield p


def decode_text(data: bytes):
    """是文本就返回 str，否则返回 None。"""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


def env_refs(text: str) -> list[str]:
    """找出文本里所有 ${NAME} 形式的环境变量引用。"""
    return sorted(set(_ENV_REF.findall(text)))
