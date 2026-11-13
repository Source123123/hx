"""命令行入口。每个子命令对应 core 里的一个函数。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, core, gitutil
from .util import HARNESS_DIR, HxError

ICON = {"ok": "✓", "warn": "!", "fail": "✗",
        "written": "+", "same": "=", "conflict": "!", "skipped": "-"}


def _project(args) -> Path:
    return Path(args.project).resolve()


def _print_capture(result: dict) -> None:
    lock = result["lock"]
    by_cat: dict = {}
    for e in lock["entries"]:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    print(f"快照 ID：{lock['snapshot']}")
    print(f"Agent：{lock['agent']['kind']} {lock['agent'].get('version') or '(版本未知)'}　模型：{lock.get('model') or '未知'}")
    print("内容：" + ("，".join(f"{k} {v}" for k, v in sorted(by_cat.items())) or "空"))
    if "redactions" in result:
        print(f"脱敏：{result['redactions']}")
    secrets = (result.get("manifest") or {}).get("secrets")
    if secrets:
        print(f"需要的环境变量：{', '.join(secrets)}")
    for path, why in result.get("skipped") or []:
        print(f"跳过 {path}：{why}")


def cmd_init(args) -> int:
    project = _project(args)
    manifest = core.init(project, args.agent)
    print(f"已在 {project / HARNESS_DIR} 初始化 harness（Agent：{manifest['agent']['kind']}）。")
    print("下一步：填写 .harness/context/intent.md，然后运行 hx snapshot -m \"初始快照\"。")
    return 0


def cmd_capture(args) -> int:
    _print_capture(core.capture(_project(args)))
    print("已更新 .harness/（未提交）。运行 hx snapshot 来提交。")
    return 0


def cmd_snapshot(args) -> int:
    result = core.snapshot(_project(args), args.message, do_capture=not args.no_capture)
    _print_capture(result)
    if result["committed"]:
        print(f"已提交：{result['commit'][:10]}")
    else:
        print("harness 没有变化，未产生新提交。")
    return 0


def cmd_log(args) -> int:
    rows = core.log(_project(args), all_commits=args.all)
    if not rows:
        print("还没有快照。")
        return 0
    for r in rows:
        print(f"{r['snapshot'] or '-':<12}  {r['date'][:16].replace('T', ' ')}  {r['commit'][:8]}  {r['subject']}")
        if not args.all:
            detail = "  ".join(x for x in (r["agent"], r["model"],
                                           f"代码 {r['code'][:8]}" if r["code"] else "") if x)
            if detail:
                print(f"{'':<12}  {detail}")
    return 0


def cmd_diff(args) -> int:
    project = _project(args)
    if args.a is None:
        rows = core.log(project)
        if not rows:
            raise HxError("还没有快照可以比较。")
        a_ref, b_ref = rows[0]["commit"], None      # 最近的快照 对比 工作区
    else:
        a_ref, b_ref = args.a, args.b
    a, b = core.lock_at(project, a_ref), core.lock_at(project, b_ref)
    d = core.diff(a, b)
    print(f"{a.get('snapshot')} → {b.get('snapshot')}{'（工作区）' if b_ref is None else ''}")
    if not d["meta"] and not d["categories"]:
        print("没有差异。")
        return 0
    for label, old, new in d["meta"]:
        print(f"  {label}：{old or '无'} → {new or '无'}")
    for cat, changes in sorted(d["categories"].items()):
        print(f"[{cat}]")
        for kind, sign in (("added", "+"), ("removed", "-"), ("changed", "~")):
            for path in changes[kind]:
                print(f"  {sign} {path}")
    if args.raw and gitutil.is_repo(project):
        revs = [core.resolve_commit(project, a_ref)] + ([core.resolve_commit(project, b_ref)] if b_ref else [])
        print()
        print(gitutil.git(project, "diff", *revs, "--", HARNESS_DIR), end="")
    return 0


def cmd_doctor(args) -> int:
    checks = core.doctor(_project(args))
    for c in checks:
        print(f"{ICON[c.level]} {c.title}")
        if c.hint and c.level != "ok":
            print(f"    {c.hint}")
    fails = sum(c.level == "fail" for c in checks)
    warns = sum(c.level == "warn" for c in checks)
    print(f"\n{fails} 个问题，{warns} 个提醒。")
    return 1 if fails else 0


def cmd_materialize(args) -> int:
    actions = core.materialize(_project(args), force=args.force, dry_run=args.dry_run)
    for a in actions:
        if a.status == "same" and not args.verbose:
            continue
        print(f"{ICON[a.status]} {a.target}{'  ' + a.note if a.note else ''}")
    count = {s: sum(a.status == s for a in actions) for s in ("written", "same", "conflict", "skipped")}
    print(f"写入 {count['written']}，已一致 {count['same']}，冲突 {count['conflict']}，跳过 {count['skipped']}。")
    return 1 if count["conflict"] else 0


def cmd_restore(args) -> int:
    project = _project(args)
    commit = core.resolve_commit(project, args.ref)
    gitutil.git(project, "checkout", commit, "--", HARNESS_DIR)
    lock = core.load_lock(project)
    print(f".harness/ 已恢复到快照 {lock.get('snapshot')}（提交 {commit[:8]}）。")
    print("运行 hx materialize 把它应用到本机；运行 hx snapshot 把这次回退记成新的快照。")
    return 0


def cmd_export(args) -> int:
    project = _project(args)
    lock = core.load_lock(project)
    out = Path(args.output or f"{project.name}-{lock.get('snapshot')}.hxb").resolve()
    r = core.export_bundle(project, out, include_transcripts=args.include_transcripts)
    print(f"已导出 {out}（快照 {r['snapshot']}，{r['files']} 个文件，{r['transcripts']} 份原始对话）。")
    if args.include_transcripts:
        print(f"原始对话脱敏：{r['redactions']}")
        print("注意：脱敏基于规则，可能有遗漏。分享给团队外的人之前请人工检查。")
    return 0


def cmd_import(args) -> int:
    r = core.import_bundle(Path(args.bundle).resolve(), _project(args), force=args.force,
                           restore_transcripts=args.restore_transcripts)
    conflicts = [a for a in r["actions"] if a.status == "conflict"]
    for a in conflicts:
        print(f"! {a.target}  {a.note}")
    written = sum(a.status == "written" for a in r["actions"])
    print(f"已导入快照 {r['meta'].get('snapshot')}：写入 {written} 个文件，冲突 {len(conflicts)} 个。")
    if r["transcripts_in_bundle"]:
        print(f"包里有 {r['transcripts_in_bundle']} 份原始对话，已还原 {r['transcripts_restored']} 份"
              + ("。" if args.restore_transcripts else "（加 --restore-transcripts 才会还原）。"))
    print("下一步：hx doctor，然后 hx materialize。")
    return 1 if conflicts else 0


def cmd_hook(args) -> int:
    hook = core.install_hook(_project(args))
    print(f"已安装 git 钩子：{hook}")
    print("之后每个提交都会带上 Hx-Snapshot，用 hx log --all 查看。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hx", description="把 Agent 开发环境打包、版本化、迁移。")
    p.add_argument("--version", action="version", version=f"hx {__version__}")
    p.add_argument("-C", "--project", default=".", help="项目目录（默认当前目录）")
    sub = p.add_subparsers(dest="command", metavar="命令")

    s = sub.add_parser("init", help="在项目里初始化 harness")
    s.add_argument("--agent", help="Agent 类型（默认自动检测）")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("capture", help="抓取当前的 Agent 配置和上下文（不提交）")
    s.set_defaults(func=cmd_capture)

    s = sub.add_parser("snapshot", help="抓取并提交一个快照")
    s.add_argument("-m", "--message", required=True, help="这次快照的说明")
    s.add_argument("--no-capture", action="store_true", help="不重新抓取，直接提交 .harness/ 当前内容")
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("log", help="查看快照历史")
    s.add_argument("--all", action="store_true", help="列出所有带快照标记的提交（含代码提交）")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("diff", help="比较两个快照（不带参数：最近的快照对比工作区）")
    s.add_argument("a", nargs="?", help="快照 ID 或 git 版本")
    s.add_argument("b", nargs="?", help="快照 ID 或 git 版本（省略表示工作区）")
    s.add_argument("--raw", action="store_true", help="同时显示文件内容的差异")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("doctor", help="检查本机能不能跑这个 harness")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("materialize", help="把包里的 Agent 配置还原到本机")
    s.add_argument("--force", action="store_true", help="覆盖本机已有的不同文件（会先备份为 .hx-bak）")
    s.add_argument("--dry-run", action="store_true", help="只显示会做什么，不写文件")
    s.add_argument("-v", "--verbose", action="store_true", help="也列出已经一致的文件")
    s.set_defaults(func=cmd_materialize)

    s = sub.add_parser("restore", help="把 .harness/ 恢复到某个历史快照")
    s.add_argument("ref", help="快照 ID 或 git 版本")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("export", help="导出成单个 .hxb 包文件")
    s.add_argument("-o", "--output", help="输出路径")
    s.add_argument("--include-transcripts", action="store_true", help="把原始对话记录（脱敏后）也放进包里")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("import", help="从 .hxb 包文件导入")
    s.add_argument("bundle", help=".hxb 文件")
    s.add_argument("--force", action="store_true", help="覆盖已有的不同文件（会先备份）")
    s.add_argument("--restore-transcripts", action="store_true", help="把包里的原始对话放回 Agent 的数据目录（实验性）")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("hook", help="安装 git 钩子，让代码提交带上快照标记")
    s.set_defaults(func=cmd_hook)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except HxError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
