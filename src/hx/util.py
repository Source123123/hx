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


class HxError(Exception):
    """给用户看的错误，CLI 会直接打印信息而不是堆栈。"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HxError(f"无法解析 JSON 文件 {path}: {e}")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__", "node_modules"))
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
