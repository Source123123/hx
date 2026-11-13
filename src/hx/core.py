"""编排层：init / capture / snapshot / log / diff / doctor / materialize / 打包。

与具体 Agent 有关的事情都交给 adapter，这里只处理通用流程。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
from pathlib import Path

from . import __version__, gitutil
from .adapters import all_adapters, get_adapter
from .adapters.base import Action, CaptureContext, Check
from .handoff import get_distiller
from .redact import Redactor
from .util import (HARNESS_DIR, LOCK, MANIFEST, HxError, home_dir, iter_files, read_json,
                   sha256_bytes, write_json)

# L1 运行环境文件：只引用，不解释
ENV_FILES = (".devcontainer/devcontainer.json", ".devcontainer.json", "Dockerfile",
             "docker-compose.yml", "compose.yaml", "flake.nix", "flake.lock",
             ".tool-versions", "mise.toml", ".mise.toml", ".nvmrc", ".python-version",
             ".node-version", "rust-toolchain.toml")

INTENT_TEMPLATE = """# 项目意图

> 这个文件由人或 Agent 维护，hx 不会覆盖它。写清楚下面几件事，接手的人就不用从对话记录里猜。

## 目标

（这个项目要解决什么问题？做成什么样算完成？）

## 设计思路

（为什么选现在这个方案？放弃了哪些方案？）

## 约束

（不能改的东西、必须兼容的东西、性能或安全要求）
"""

TASKS_TEMPLATE = """# 任务

> 这个文件由人或 Agent 维护，hx 不会覆盖它。

## 进行中

## 待办

## 已知问题
"""

DECISIONS_README = """# 决策记录

每个重要决策写一个文件，文件名形如 `0001-用-git-存储快照.md`，内容包括：背景、决定、理由、后果。
第一行标题会出现在交接简报里。
"""


# ---------- 清单与锁文件 ----------

def harness_dir(project: Path) -> Path:
    return project / HARNESS_DIR


def load_manifest(project: Path) -> dict:
    m = read_json(harness_dir(project) / MANIFEST)
    if not isinstance(m, dict):
        raise HxError(f"{project} 里还没有 harness。先运行 hx init。")
    return m


def load_lock(project: Path) -> dict:
    lock = read_json(harness_dir(project) / LOCK)
    if not isinstance(lock, dict):
        raise HxError("还没有锁文件。先运行 hx capture 或 hx snapshot。")
    return lock


def snapshot_id(lock: dict) -> str:
    """快照 ID：由 Agent、模型和所有条目的哈希算出。内容相同则 ID 相同。"""
    basis = {
        "agent": lock.get("agent"), "model": lock.get("model"),
        "entries": sorted((e["path"], e["sha256"]) for e in lock.get("entries", [])),
    }
    return sha256_bytes(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode())[:12]


# ---------- init ----------

def init(project: Path, agent: str | None = None) -> dict:
    h = harness_dir(project)
    if (h / MANIFEST).exists():
        raise HxError("这个项目已经初始化过了。")
    home = home_dir()
    if not agent:
        detected = [n for n, cls in sorted(all_adapters().items()) if cls().detect(project, home)]
        agent = detected[0] if detected else "claude-code"
    get_adapter(agent)  # 名字不对会在这里报错
    manifest = {
        "apiVersion": "hx/v1",
        "name": project.resolve().name,
        "agent": {"kind": agent},
        "capture": {"max_sessions": 20, "handoff_sessions": 5},
        "auto_save": True,
        "distiller": "builtin",
        "secrets": [],
    }
    write_json(h / MANIFEST, manifest)
    for rel, text in (("context/intent.md", INTENT_TEMPLATE), ("context/tasks.md", TASKS_TEMPLATE),
                      ("context/decisions/README.md", DECISIONS_README)):
        p = h / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    return manifest


# ---------- capture ----------

def capture(project: Path) -> dict:
    """抓取当前的 Agent 环境，重写 .harness/ 里自动生成的部分和锁文件。"""
    manifest = load_manifest(project)
    adapter = get_adapter(manifest["agent"]["kind"])
    h = harness_dir(project)
    for d in adapter.generated_dirs:
        shutil.rmtree(h / d, ignore_errors=True)

    ctx = CaptureContext(project=project, home=home_dir(), redactor=Redactor(),
                         options=manifest.get("capture") or {})
    for f in ENV_FILES:
        ctx.ref(f, "env")
    adapter.capture(ctx)

    # 人工维护的上下文也登记进锁文件，这样改了意图或决策会产生新的快照
    for rel in ("context/intent.md", "context/tasks.md"):
        _register_context(ctx, h / rel)
    for p in iter_files(h / "context/decisions"):
        _register_context(ctx, p)

    lock = {
        "lockVersion": 1,
        "hx": __version__,
        "agent": {"kind": adapter.name, "version": ctx.info.get("agent_version")},
        "model": ctx.info.get("model"),
        "code_commit": gitutil.code_head(project, HARNESS_DIR) if gitutil.is_repo(project) else None,
        "entries": sorted(ctx.entries, key=lambda e: e["path"]),
    }
    lock["snapshot"] = snapshot_id(lock)

    manifest["secrets"] = sorted(set(ctx.secrets) | set(manifest.get("extra_secrets") or []))
    write_json(h / MANIFEST, manifest)
    write_json(h / LOCK, lock)
    distill = get_distiller(manifest.get("distiller"))
    (h / "context").mkdir(parents=True, exist_ok=True)
    (h / "context/HANDOFF.md").write_text(distill(h, manifest, lock), encoding="utf-8")
    return {"lock": lock, "manifest": manifest, "redactions": ctx.redactor.summary(),
            "skipped": ctx.skipped}


def _register_context(ctx: CaptureContext, path: Path) -> None:
    if path.is_file():
        ctx.entries.append({
            "path": path.relative_to(ctx.project).as_posix(), "category": "context",
            "scope": "bundle", "sha256": sha256_bytes(path.read_bytes()),
        })


# ---------- snapshot / log ----------

def snapshot(project: Path, message: str, do_capture: bool = True) -> dict:
    if not gitutil.is_repo(project):
        raise HxError("这个目录不是 git 仓库。先运行 git init。")
    result = capture(project) if do_capture else {"lock": load_lock(project)}
    lock = result["lock"]
    code = lock.get("code_commit")
    # 被引用的项目文件如果还没提交，别的机器 clone 下来会对不上
    refs = [e["path"] for e in lock["entries"] if e["scope"] == "project"]
    dirty = []
    if refs:
        out = gitutil.git(project, "status", "--porcelain", "-z", "--", *refs)
        dirty = sorted(item[3:] for item in out.split("\0") if item)
    result = {**result, "uncommitted": dirty}
    gitutil.git(project, "add", "-A", "--", HARNESS_DIR)
    if not gitutil.git(project, "status", "--porcelain", "--", HARNESS_DIR).strip():
        return {**result, "committed": False}
    agent = lock["agent"]
    trailers = [f"Hx-Snapshot: {lock['snapshot']}",
                f"Hx-Agent: {agent['kind']}" + (f"@{agent['version']}" if agent.get("version") else "")]
    if lock.get("model"):
        trailers.append(f"Hx-Model: {lock['model']}")
    if code:
        trailers.append(f"Hx-Code: {code}")
    gitutil.git(project, "commit", "-q", "-m", message + "\n\n" + "\n".join(trailers), "--", HARNESS_DIR)
    return {**result, "committed": True, "commit": gitutil.head(project)}


_LOG_FIELDS = ("commit", "date", "author", "subject", "snapshot", "agent", "model", "code")


def log(project: Path, all_commits: bool = False) -> list:
    """harness 的历史。all_commits=True 时列出所有带 Hx-Snapshot 的提交（含代码提交）。"""
    if not gitutil.is_repo(project) or not gitutil.head(project):
        return []
    def tr(key):
        return f"%(trailers:key={key},valueonly,separator=%x2C)"
    fmt = "%H%x1f%aI%x1f%an%x1f%s%x1f" + "%x1f".join(tr(k) for k in ("Hx-Snapshot", "Hx-Agent", "Hx-Model", "Hx-Code")) + "%x1e"
    args = ["log", f"--format={fmt}"]
    if not all_commits:
        args += ["--", f"{HARNESS_DIR}/{LOCK}"]
    rows = []
    for rec in gitutil.git(project, *args).split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == len(_LOG_FIELDS):
            row = dict(zip(_LOG_FIELDS, (p.strip() for p in parts)))
            if row["snapshot"] or not all_commits:
                rows.append(row)
    return rows


def lock_at(project: Path, ref: str | None) -> dict:
    """取某个版本的锁文件。ref 可以是快照 ID（或前缀）、git 版本，None 表示工作区。"""
    if ref is None:
        return load_lock(project)
    rev = ref
    for row in log(project):
        if row["snapshot"] and row["snapshot"].startswith(ref):
            rev = row["commit"]
            break
    text = gitutil.show_file(project, rev, f"{HARNESS_DIR}/{LOCK}")
    if text is None:
        raise HxError(f"找不到快照或版本 {ref!r}（或者那个版本里没有 harness）。")
    return json.loads(text)


def resolve_commit(project: Path, ref: str) -> str:
    for row in log(project):
        if row["snapshot"] and row["snapshot"].startswith(ref):
            return row["commit"]
    out = gitutil.git(project, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False).strip()
    if not out:
        raise HxError(f"找不到快照或版本 {ref!r}。")
    return out


# ---------- diff ----------

def diff(a: dict, b: dict) -> dict:
    """比较两个锁文件。返回 {meta: [...], categories: {类别: {added, removed, changed}}}。"""
    meta = []
    for label, get in (("Agent", lambda l: (l.get("agent") or {}).get("kind")),
                       ("Agent 版本", lambda l: (l.get("agent") or {}).get("version")),
                       ("模型", lambda l: l.get("model")),
                       ("代码提交", lambda l: l.get("code_commit"))):
        if get(a) != get(b):
            meta.append((label, get(a), get(b)))
    ea = {e["path"]: e for e in a.get("entries", [])}
    eb = {e["path"]: e for e in b.get("entries", [])}
    cats: dict = {}
    for path in sorted(set(ea) | set(eb)):
        x, y = ea.get(path), eb.get(path)
        if x and y and x["sha256"] == y["sha256"]:
            continue
        kind = "added" if not x else "removed" if not y else "changed"
        cat = (y or x)["category"]
        cats.setdefault(cat, {"added": [], "removed": [], "changed": []})[kind].append(path)
    return {"meta": meta, "categories": cats}


# ---------- materialize ----------

def place_file(data: bytes, dest: Path, force: bool) -> Action:
    """把内容写到 dest。已有不同内容时默认不动；force 时先备份再覆盖。"""
    if dest.is_file():
        if dest.read_bytes() == data:
            return Action("same", str(dest))
        if not force:
            return Action("conflict", str(dest), "已有不同内容，未改动（加 --force 覆盖，原文件会备份）")
        shutil.copy2(dest, dest.with_name(dest.name + ".hx-bak"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return Action("written", str(dest))


def _target_path(target: str, project: Path, user_root: Path) -> Path:
    scope, _, rel = target.partition(":")
    base = {"user": user_root, "project": project}.get(scope)
    if base is None or not rel:
        raise HxError(f"锁文件里的还原目标无效：{target!r}")
    dest = (base / rel).resolve()
    if base.resolve() not in dest.parents:
        raise HxError(f"锁文件里的还原目标越界：{target!r}")
    return dest


def materialize(project: Path, force: bool = False, dry_run: bool = False) -> list:
    """把包里的 Agent 配置还原到这台机器上。"""
    manifest, lock = load_manifest(project), load_lock(project)
    adapter = get_adapter(manifest["agent"]["kind"])
    home = home_dir()
    actions = []
    for e in lock.get("entries", []):
        if not e.get("target"):
            continue
        src = project / e["path"]
        dest = _target_path(e["target"], project, adapter.user_root(home))
        if not src.is_file():
            actions.append(Action("skipped", str(dest), f"包里缺少 {e['path']}"))
            continue
        data = src.read_bytes()
        if dry_run:
            status = "same" if dest.is_file() and dest.read_bytes() == data else \
                     "conflict" if dest.is_file() else "written"
            actions.append(Action(status, str(dest), "（预演）"))
        else:
            actions.append(place_file(data, dest, force))
    if not dry_run:
        actions += adapter.materialize(project, home, lock, force)
    return actions


# ---------- doctor ----------

def doctor(project: Path) -> list:
    manifest, lock = load_manifest(project), load_lock(project)
    adapter = get_adapter(manifest["agent"]["kind"])
    home = home_dir()
    checks = [Check("ok", f"harness 快照 {lock.get('snapshot')}（{adapter.name}，模型 {lock.get('model') or '未知'}）")]

    missing, drifted = [], []
    for e in lock.get("entries", []):
        p = project / e["path"]
        if not p.is_file():
            missing.append(e["path"])
        elif sha256_bytes(p.read_bytes()) != e["sha256"]:
            drifted.append(e["path"])
    if missing:
        checks.append(Check("fail", f"{len(missing)} 个文件缺失：{', '.join(missing[:5])}{' …' if len(missing) > 5 else ''}",
                            "仓库不完整，或者这些文件在快照之后被删了。"))
    if drifted:
        checks.append(Check("warn", f"{len(drifted)} 个文件在快照之后改过：{', '.join(drifted[:5])}{' …' if len(drifted) > 5 else ''}",
                            "运行 hx snapshot 记录新的状态。"))
    if not missing and not drifted:
        checks.append(Check("ok", f"{len(lock.get('entries', []))} 个文件与锁文件一致"))

    for name in manifest.get("secrets") or []:
        if os.environ.get(name):
            checks.append(Check("ok", f"环境变量 {name} 已设置"))
        else:
            checks.append(Check("fail", f"环境变量 {name} 未设置", "这个 harness 需要它，请在本机设置。"))

    checks += adapter.doctor(project, home, manifest, lock)

    pending = [a for a in materialize(project, dry_run=True) if a.status != "same"]
    if pending:
        conflicts = sum(1 for a in pending if a.status == "conflict")
        checks.append(Check("warn", f"{len(pending)} 个配置文件还没有还原到本机（其中 {conflicts} 个与本机已有文件不同）",
                            "运行 hx materialize。"))
    else:
        checks.append(Check("ok", "包里的配置都已还原到本机"))
    return checks


# ---------- 打包 ----------

BUNDLE_META = "hx-bundle.json"


def export_bundle(project: Path, out: Path, include_transcripts: bool = False) -> dict:
    """导出成一个自包含的 .hxb 文件（tar.gz）：.harness/ + 锁文件引用的项目文件 + 可选的原始对话。"""
    manifest, lock = load_manifest(project), load_lock(project)
    adapter = get_adapter(manifest["agent"]["kind"])
    files = {p.relative_to(project).as_posix() for p in iter_files(harness_dir(project))}
    files |= {e["path"] for e in lock.get("entries", []) if (project / e["path"]).is_file()}
    redactor = Redactor()
    n_transcripts = 0
    with tarfile.open(out, "w:gz") as tar:
        def add(name: str, data: bytes):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        meta = {"format": 1, "name": manifest.get("name"), "snapshot": lock.get("snapshot"),
                "agent": lock.get("agent"), "transcripts": include_transcripts}
        add(BUNDLE_META, json.dumps(meta, ensure_ascii=False, indent=2).encode())
        for rel in sorted(files):
            add(f"project/{rel}", (project / rel).read_bytes())
        if include_transcripts:
            for p in adapter.transcripts(project, home_dir()):
                text = p.read_text(encoding="utf-8", errors="replace")
                add(f"transcripts/{p.name}", redactor.text(text).encode("utf-8"))
                n_transcripts += 1
    return {"files": len(files), "transcripts": n_transcripts, "redactions": redactor.summary(),
            "snapshot": lock.get("snapshot")}


def import_bundle(bundle: Path, project: Path, force: bool = False,
                  restore_transcripts: bool = False) -> dict:
    """把 .hxb 解到项目目录。已有的不同文件默认不覆盖。"""
    project.mkdir(parents=True, exist_ok=True)
    actions, transcripts = [], {}
    try:
        tar = tarfile.open(bundle, "r:gz")
    except (tarfile.TarError, OSError) as e:
        raise HxError(f"无法打开包文件 {bundle}: {e}")
    with tar:
        meta = None
        for member in tar:
            if not member.isfile():
                continue   # 只接受普通文件，忽略链接和设备
            parts = Path(member.name).parts
            if ".." in parts or member.name.startswith("/"):
                raise HxError(f"包里有不安全的路径：{member.name}")
            data = tar.extractfile(member).read()
            if member.name == BUNDLE_META:
                meta = json.loads(data)
            elif parts[0] == "project" and len(parts) > 1:
                actions.append(place_file(data, project.joinpath(*parts[1:]), force))
            elif parts[0] == "transcripts" and len(parts) == 2:
                transcripts[parts[1]] = data
    if meta is None:
        raise HxError("这不是一个 hx 包（缺少 hx-bundle.json）。")
    restored = 0
    if restore_transcripts and transcripts:
        adapter = get_adapter(load_manifest(project)["agent"]["kind"])
        dest = adapter.transcript_dir(project, home_dir())
        if dest:
            for name, data in transcripts.items():
                restored += place_file(data, dest / name, force).status == "written"
    return {"meta": meta, "actions": actions, "transcripts_in_bundle": len(transcripts),
            "transcripts_restored": restored}


# ---------- git 钩子 ----------

HOOK_MARK = "# hx: add Hx-Snapshot trailer"
HOOK_BODY = f"""{HOOK_MARK}
hx_lock="$(git rev-parse --show-toplevel)/{HARNESS_DIR}/{LOCK}"
if [ -f "$hx_lock" ]; then
  hx_id=$(sed -n 's/.*"snapshot": *"\\([0-9a-f]*\\)".*/\\1/p' "$hx_lock" | head -n 1)
  if [ -n "$hx_id" ]; then
    git interpret-trailers --in-place --if-exists replace --trailer "Hx-Snapshot: $hx_id" "$1"
  fi
fi
"""


def install_hook(project: Path) -> Path:
    """安装 prepare-commit-msg 钩子：之后每个提交都会带上当前的 Hx-Snapshot。"""
    if not gitutil.is_repo(project):
        raise HxError("这个目录不是 git 仓库。")
    hooks = Path(gitutil.git(project, "rev-parse", "--git-path", "hooks").strip())
    hooks = hooks if hooks.is_absolute() else project / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "prepare-commit-msg"
    existing = hook.read_text(encoding="utf-8") if hook.exists() else "#!/bin/sh\n"
    if HOOK_MARK not in existing:
        hook.write_text(existing.rstrip("\n") + "\n\n" + HOOK_BODY, encoding="utf-8")
    hook.chmod(0o755)
    return hook


# ---------- 一键保存 / 载入 ----------

CATEGORY_NAMES = {
    "env": "运行环境", "instructions": "指令", "settings": "设置", "skills": "技能",
    "agents": "子 Agent", "commands": "命令", "rules": "规则", "hooks": "钩子",
    "mcp": "MCP", "plugins": "插件", "memory": "记忆", "sessions": "会话",
    "context": "意图与决策", "other": "其他",
}


def describe_diff(d: dict) -> str:
    """把 diff 结果写成一句人话，用作自动保存的说明。"""
    parts = [f"{label}改为 {new}" for label, _, new in d["meta"] if label in ("模型", "Agent 版本") and new]
    for cat, ch in sorted(d["categories"].items()):
        n = len(ch["added"]) + len(ch["removed"]) + len(ch["changed"])
        parts.append(f"{CATEGORY_NAMES.get(cat, cat)} {n} 项")
    return "，".join(parts)


def git_busy(project: Path) -> bool:
    """仓库是否正在合并、变基等操作中。这时不应该自动提交。"""
    gd = Path(gitutil.git(project, "rev-parse", "--absolute-git-dir").strip())
    return any((gd / n).exists() for n in ("MERGE_HEAD", "rebase-merge", "rebase-apply",
                                           "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG"))


def save(project: Path, message: str | None = None, push: bool = False) -> dict:
    """一键保存：没初始化就初始化，抓取，能提交就提交。"""
    first = not (harness_dir(project) / MANIFEST).exists()
    if first:
        init(project)
    is_repo = gitutil.is_repo(project)
    previous = None
    if is_repo:
        rows = log(project)
        if rows:
            previous = lock_at(project, rows[0]["commit"])
    result = capture(project)
    result.update(first=first, committed=False, pushed=False, reason=None)
    if not is_repo:
        result["reason"] = "no-git"
        return result
    if git_busy(project):
        result["reason"] = "git-busy"
        return result
    if not message:
        summary = describe_diff(diff(previous, result["lock"])) if previous else ""
        message = f"hx 保存：{summary}" if summary else "hx 保存：初始快照"
    result.update(snapshot(project, message, do_capture=False))
    if push and gitutil.git(project, "rev-parse", "--abbrev-ref", "@{u}", check=False).strip():
        gitutil.git(project, "push", "-q")
        result["pushed"] = True
    return result


def fetch_source(source: str, into: Path) -> Path:
    """把 load 的来源变成本地项目目录。来源可以是 .hxb 文件或 git 地址。"""
    src = Path(source)
    if src.is_file():
        import_bundle(src.resolve(), into)
        return into
    if "://" in source or source.startswith("git@") or source.endswith(".git"):
        trimmed = source.rstrip("/")
        trimmed = trimmed[:-4].rstrip("/") if trimmed.endswith(".git") else trimmed
        name = trimmed.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if not name:
            raise HxError(f"无法从 {source!r} 判断项目名。")
        dest = into / name
        if dest.exists():
            raise HxError(f"目录 {dest} 已经存在。请进入它再运行 hx load。")
        gitutil.git(into, "clone", "-q", source, str(dest))
        return dest
    raise HxError(f"看不懂的来源 {source!r}。请给一个 .hxb 文件或 git 仓库地址。")


# ---------- 团队一键接入 ----------

def team(project: Path, enable: bool | None) -> dict:
    """开关团队一键接入。enable 为 None 时只查询。
    改动的文件如果此前没有未提交的修改，就单独提交它，这样 push 之后同事马上能用。"""
    manifest = load_manifest(project)
    adapter = get_adapter(manifest["agent"]["kind"])
    if enable is None:
        return {"enabled": adapter.team_enabled(project), "changed": [], "committed": False}
    is_repo = gitutil.is_repo(project)
    dirty_before = set()
    if is_repo:
        out = gitutil.git(project, "status", "--porcelain", "-z")
        dirty_before = {item[3:] for item in out.split("\0") if item}
    changed = adapter.team_setup(project, enable)
    if changed is None:
        raise HxError(f"{adapter.name} 还不支持团队一键接入。")
    committed = False
    clean = [f for f in changed if f not in dirty_before and not any(d.endswith("/") and f.startswith(d) for d in dirty_before)]
    if is_repo and changed and len(clean) == len(changed) and not git_busy(project):
        gitutil.git(project, "add", "-A", "--", *changed)
        gitutil.git(project, "commit", "-q", "-m",
                    f"hx：{'开启' if enable else '关闭'}团队一键接入", "--", *changed)
        committed = True
    return {"enabled": adapter.team_enabled(project), "changed": changed, "committed": committed}
