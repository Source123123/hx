"""包不下、也不该包的两类内容，换一种形式带走。

  派生数据（向量库、索引、缓存）：体积大、是二进制、能从源文件重新生成，而且并不"脱敏"
      —— 向量可以被反推出原文的大意。所以不带数据本身，只带"怎么重建"。
  外部状态（数据库里有什么、远端服务怎么配的、本机装了什么）：带不走，
      而 Agent 的记忆里却记着它。所以把这些记忆写成明确的"声明"，载入时重新核对。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from . import gitutil
from .portable import localize
from .util import HARNESS_DIR, HxError, read_json, sha256_bytes, write_json

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", HARNESS_DIR}
# 常见本地向量库 / 索引的目录名和文件后缀
VECTOR_DIRS = {"chroma", ".chroma", "chroma_db", "chromadb", "lancedb", ".lancedb", "qdrant_storage",
               "faiss_index", "vectorstore", "vector_store", ".vectorstore", "milvus_data", "weaviate_data"}
VECTOR_SUFFIXES = (".faiss", ".lance", ".hnsw", ".annoy")
HASH_LIMIT = 2000      # 源文件超过这个数就不逐个算哈希了
WALK_LIMIT = 20000     # 看产物新旧时最多检查多少个文件


# ---------- 派生数据：可重建资源 ----------

def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/")


def add_resource(manifest: dict, name: str, path: str, sources: list, build: str, note: str = "") -> dict:
    if not name or not path or not build:
        raise HxError("需要名字、产物路径（--path）和重建命令（--build）。")
    p = _norm(path)
    if p.startswith("..") or os.path.isabs(path) or p == HARNESS_DIR or p.startswith(HARNESS_DIR + "/"):
        raise HxError("产物路径必须是项目里的相对路径，且不能在 .harness/ 里。")
    res = {"name": name, "path": p, "sources": [_norm(s) for s in sources if s.strip()], "build": build}
    if note:
        res["note"] = note
    items = [r for r in manifest.get("resources") or [] if r.get("name") != name]
    manifest["resources"] = sorted(items + [res], key=lambda r: r["name"])
    return res


def remove_resource(manifest: dict, name: str) -> bool:
    items = manifest.get("resources") or []
    kept = [r for r in items if r.get("name") != name]
    manifest["resources"] = kept
    return len(kept) != len(items)


def _source_files(project: Path, res: dict) -> list:
    found = set()
    for pattern in res.get("sources") or []:
        try:
            for p in project.glob(pattern):
                if p.is_file() and not (SKIP_DIRS & set(p.relative_to(project).parts)):
                    found.add(p)
        except (ValueError, NotImplementedError, OSError):
            continue
    return sorted(found)


def _newest(path: Path) -> float:
    if path.is_file():
        return path.stat().st_mtime
    newest, seen = 0.0, 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                pass
            seen += 1
            if seen >= WALK_LIMIT:
                return newest
    return newest


def resource_fingerprint(project: Path, res: dict) -> dict:
    """保存时记录：这份资源是基于多少个源文件、什么内容建出来的。"""
    files = _source_files(project, res)
    info = {"sources": len(files)}
    if 0 < len(files) <= HASH_LIMIT:
        h = hashlib.sha256()
        for p in files:
            h.update(f"{p.relative_to(project).as_posix()}\0{sha256_bytes(p.read_bytes())}\n".encode())
        info["sources_sha"] = h.hexdigest()[:16]
    return info


def resource_status(project: Path, res: dict) -> str:
    """missing：产物不存在；stale：源文件比产物新；ok。判断方式和 make 一样，看修改时间。"""
    out = project / res["path"]
    if not out.exists():
        return "missing"
    built = _newest(out)
    sources = _source_files(project, res)
    if sources and max(p.stat().st_mtime for p in sources) > built:
        return "stale"
    return "ok"


def is_ignored(project: Path, relpath: str) -> bool | None:
    """这个路径是否被 git 忽略。不是 git 仓库时返回 None。"""
    if not gitutil.is_repo(project):
        return None
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-q", relpath], cwd=project, capture_output=True)
    return r.returncode == 0


def detect_vector_stores(project: Path, declared: list, depth: int = 3) -> list:
    """找出项目里疑似向量库 / 索引、但还没有声明为可重建资源的路径。"""
    known = [_norm(r.get("path", "")) for r in declared]
    found = []
    root = str(project)
    for dirpath, dirs, files in os.walk(project):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        level = 0 if rel_dir == "." else rel_dir.count("/") + 1
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        hits = [d for d in dirs if d.lower() in VECTOR_DIRS]
        hits += [f for f in files if f.lower().endswith(VECTOR_SUFFIXES)]
        for name in hits:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not any(rel == k or rel.startswith(k + "/") or k.startswith(rel + "/") for k in known if k):
                found.append(rel)
        dirs[:] = [d for d in dirs if d.lower() not in VECTOR_DIRS]
        if level >= depth:
            dirs[:] = []
    return sorted(found)


# ---------- 外部状态：声明 ----------

CHECK_TYPES = ("path", "env", "command")


def claims_file(project: Path) -> Path:
    return project / HARNESS_DIR / "context" / "claims.json"


def load_claims(project: Path) -> list:
    data = read_json(claims_file(project), {}) or {}
    return [c for c in (data.get("claims") or []) if isinstance(c, dict) and c.get("text")]


def save_claims(project: Path, claims: list) -> None:
    write_json(claims_file(project), {"claims": sorted(claims, key=lambda c: c["id"])})


def add_claim(project: Path, text: str, check_type: str | None = None, value: str | None = None) -> dict:
    text = " ".join(text.split())
    if not text:
        raise HxError("声明不能为空。")
    claim = {"id": "c" + sha256_bytes(text.encode("utf-8"))[:6], "text": text}
    if check_type:
        if check_type not in CHECK_TYPES or not value:
            raise HxError(f"核对方式只能是 {' / '.join(CHECK_TYPES)}，并且要给出核对的对象。")
        claim["check"] = {"type": check_type, "value": value}
    claims = [c for c in load_claims(project) if c["id"] != claim["id"]]
    save_claims(project, claims + [claim])
    return claim


def remove_claim(project: Path, claim_id: str) -> bool:
    claims = load_claims(project)
    kept = [c for c in claims if c["id"] != claim_id]
    if len(kept) != len(claims):
        save_claims(project, kept)
    return len(kept) != len(claims)


def check_claim(project: Path, home: Path, claim: dict) -> bool | None:
    """在本机核对一条声明。成立返回 True，不成立返回 False，没有可自动核对的方式返回 None。
    核对只做"看一眼"：文件在不在、环境变量设没设、命令装没装。绝不执行包里带来的任何命令。"""
    check = claim.get("check")
    if not isinstance(check, dict):
        return None
    kind, value = check.get("type"), str(check.get("value") or "")
    if kind == "env":
        return bool(os.environ.get(value))
    if kind == "command":
        return shutil.which(value) is not None
    if kind == "path":
        value = localize(value.encode("utf-8"), project, home).decode("utf-8")
        p = Path(value[2:]) if value.startswith(("~/", "~\\")) else Path(value)
        if value.startswith("~"):
            p = home / p
        elif not p.is_absolute():
            p = project / p
        return p.exists()
    return None
