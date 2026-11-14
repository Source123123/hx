"""命令行入口。每个子命令对应 core 里的一个函数。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, core, gitutil
from .util import HARNESS_DIR, HxError

ICON = {"ok": "✓", "warn": "!", "fail": "✗", "note": "·",
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
    print(f"已在 {project / HARNESS_DIR} 初始化 harness（Agent：{'、'.join(core.agent_kinds(manifest))}）。")
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
    if result.get("uncommitted"):
        print(f"提醒：快照引用的这些项目文件还有未提交的改动：{', '.join(result['uncommitted'])}")
        print("      请把它们也 git commit，否则别的机器拿到的内容会和快照对不上。")
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
        a_ref, b_ref = rows[0]["commit"], None      # 最近的快照 对比 当前状态
        core.capture(project)                       # 先重新抓取，否则看不到还没抓的改动
    else:
        a_ref, b_ref = args.a, args.b
    a, b = core.lock_at(project, a_ref), core.lock_at(project, b_ref)
    d = core.diff(a, b)
    print(f"{a.get('snapshot')} → {b.get('snapshot')}{'（当前，未提交）' if b_ref is None else ''}")
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
    notes = sum(c.level == "note" for c in checks)
    print(f"\n{fails} 个问题，{warns} 个提醒" + (f"，{notes} 条说明" if notes else "") + "。")
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


def _save_report(r: dict, project: Path) -> None:
    lock = r["lock"]
    if r["first"]:
        print("第一次保存：已创建 .harness/ 目录。")
    if r["committed"]:
        print(f"已保存（快照 {lock['snapshot']}，{len(lock['entries'])} 个文件）。")
    elif r["reason"] == "no-git":
        print(f"已保存到 .harness/（快照 {lock['snapshot']}）。这个目录不是 git 仓库，所以没有历史记录；"
              "运行 git init 后再保存就有了。")
    elif r["reason"] == "git-busy":
        print("已更新 .harness/，但仓库正在合并或变基中，这次没有提交。")
    else:
        print(f"没有新的变化（快照 {lock['snapshot']}）。")
    resolved = r.get("resolved") or {}
    if resolved.get("auto"):
        print(f"已自动解决 .harness/ 里 {len(resolved['auto'])} 个文件的合并冲突。")
    if resolved.get("manual"):
        print("下面这些文件的合并冲突需要你来判断，解决后 git add 它们：")
        for p in resolved["manual"]:
            print(f"  {p}")
    if resolved.get("auto") and not resolved.get("manual") and r["reason"] == "git-busy":
        print("确认其他文件的冲突也已解决后，运行 git commit 完成合并。")
    if r.get("redactions") and r["redactions"] != "未发现需要处理的内容":
        print(f"已自动替换或隐藏：{r['redactions']}")
    if r.get("uncommitted"):
        print(f"提醒：这些文件还没有 git commit，别的机器拿不到最新内容：{', '.join(r['uncommitted'])}")
    if r["pushed"]:
        print("已推送到远端。")
    elif r["committed"]:
        print("要分享给别人：git push（或 hx save --push），对方 clone 后运行 hx load。")
    if r["first"]:
        print("团队协作：运行 hx team on，同事打开项目时会自动被提示安装 hx。")


def cmd_save(args) -> int:
    project = _project(args)
    _save_report(core.save(project, args.message, push=args.push), project)
    return 0


def cmd_load(args) -> int:
    project = _project(args)
    if args.source:
        project = core.fetch_source(args.source, project)
        print(f"项目已就绪：{project}")
    plan = [a for a in core.materialize(project, force=args.force, dry_run=True) if a.status != "same"]
    todo = [a for a in plan if a.status == "written" or (a.status == "conflict" and args.force)]
    if todo and not args.yes:
        print("将把下面这些 Agent 配置放到本机（其中可能包含会自动执行命令的设置，请只载入你信任的项目）：")
        for a in todo:
            print(f"  {a.target}{'  （覆盖，原文件会备份）' if a.status == 'conflict' else ''}")
        if args.dry_run:
            return 0
        if not sys.stdin.isatty():
            raise HxError("需要确认。确认无误后请加 --yes 再运行一次。")
        if input("继续吗？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消。")
            return 1
    elif args.dry_run:
        print("没有需要放到本机的配置。")
        return 0
    actions = core.materialize(project, force=args.force)
    n = {s: sum(a.status == s for a in actions) for s in ("written", "conflict")}
    for a in actions:
        if a.status == "conflict":
            print(f"! {a.target}  本机已有不同的版本，没有改动")
    print(f"已载入：放置了 {n['written']} 项配置" + (f"，{n['conflict']} 项因本机已有不同版本而跳过（hx load --force 可覆盖）" if n["conflict"] else "") + "。")
    checks = core.doctor(project)
    problems = [c for c in checks if c.level == "fail"]
    notes = [c for c in checks if c.level in ("warn", "note") and "快照之后改过" not in c.title]
    if problems or notes:
        print("\n还需要你处理：")
        for c in problems + notes:
            print(f"  {ICON[c.level]} {c.title}" + (f"  → {c.hint}" if c.hint else ""))
    else:
        print("环境检查全部通过。")
    print("\n交接简报：.harness/context/HANDOFF.md。重新打开 Agent 后新配置生效。")
    return 1 if problems else 0


def cmd_auto(args) -> int:
    project = _project(args)
    manifest = core.load_manifest(project)
    if args.state:
        manifest["auto_save"] = args.state == "on"
        from .util import MANIFEST, write_json
        write_json(core.harness_dir(project) / MANIFEST, manifest)
    print(f"自动保存：{'开' if manifest.get('auto_save') else '关'}（由 Agent 的会话结束钩子触发，目前需要安装 Claude Code 插件）")
    return 0


def cmd_team(args) -> int:
    project = _project(args)
    r = core.team(project, None if not args.state else args.state == "on")
    print(f"团队一键接入：{'开' if r['enabled'] else '关'}")
    if r["changed"]:
        if r["committed"]:
            print(f"已修改并提交 {', '.join(r['changed'])}。git push 之后生效。")
        else:
            print(f"已修改 {', '.join(r['changed'])}，请把它 git commit 并 push。")
    if r["enabled"]:
        print("同事 clone 项目、用 Claude Code 打开并信任这个目录后，会被提示安装 hx 插件；装好输入 /hx:load 即可。")
        print("前提：同事的 GitHub 账号能访问 hx 插件所在的仓库。")
    elif not args.state:
        print("开启：hx team on")
    return 0


def _hook_project(args) -> Path:
    import os
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or args.project).resolve()


def cmd_auto_save(args) -> int:
    """给 Agent 的会话结束钩子用。只对已经用 hx 管理、且开着自动保存的项目生效；任何错误都不打断 Agent。"""
    try:
        project = _hook_project(args)
        manifest = core.read_json(core.harness_dir(project) / "harness.json")
        if isinstance(manifest, dict) and manifest.get("auto_save"):
            r = core.save(project)
            if r["committed"]:
                print(f"hx：已自动保存 Agent 环境（快照 {r['lock']['snapshot']}）。")
    except Exception as e:  # noqa: BLE001
        print(f"hx 自动保存失败：{e}", file=sys.stderr)
    return 0


def cmd_auto_load(args) -> int:
    """给 Agent 的会话开始钩子用。只报告，不往本机写任何东西。"""
    try:
        project = _hook_project(args)
        if not (core.harness_dir(project) / "harness.json").is_file() or \
                not (core.harness_dir(project) / "harness.lock.json").is_file():
            return 0
        pending = [a for a in core.materialize(project, dry_run=True) if a.status != "same"]
        print("hx：这个项目带有交接简报 .harness/context/HANDOFF.md，接手工作前请先阅读。")
        if pending:
            print(f"hx：有 {len(pending)} 项 Agent 配置还没有载入本机。请告诉用户可以运行 /hx:load 来载入。")
    except Exception as e:  # noqa: BLE001
        print(f"hx 检查失败：{e}", file=sys.stderr)
    return 0


def cmd_home(args) -> int:
    """不带任何参数运行 hx：说明当前状态和下一步。"""
    project = _project(args)
    if not (core.harness_dir(project) / "harness.json").is_file():
        print("这个项目还没有用 hx 保存过 Agent 环境。\n")
        print("  hx save          保存这个项目的 Agent 环境")
        print("  hx load <来源>   载入别人分享的项目（.hxb 文件或 git 地址）")
        return 0
    rows = core.log(project) if gitutil.is_repo(project) else []
    if rows:
        print(f"最近一次保存：{rows[0]['date'][:16].replace('T', ' ')}　{rows[0]['subject']}（共 {len(rows)} 次）")
    else:
        print("已初始化，还没有保存记录。")
    pending = [a for a in core.materialize(project, dry_run=True) if a.status != "same"] \
        if (core.harness_dir(project) / "harness.lock.json").is_file() else []
    if pending:
        print(f"有 {len(pending)} 项配置还没有载入本机 → hx load")
    print("\n  hx save   保存当前的 Agent 环境\n  hx load   把保存的环境载入本机\n  hx log    查看历史\n  hx -h     全部命令")
    return 0


def cmd_private(args) -> int:
    from . import portable
    home = core.home_dir()
    terms = portable.load_terms(home)
    if args.action == "add":
        if not args.term or not args.alias:
            raise HxError("用法：hx private add 真实的词 代称")
        portable.add_term(home, args.term, args.alias)
        print(f"已登记。之后保存时，{args.term!r} 会写成 [[{args.alias}]]。对照表只存在本机：{portable.terms_file(home)}")
        print("已经保存过的快照不会被改写；重新运行 hx save 才会生效。")
    elif args.action == "remove":
        if not args.term or args.term not in terms:
            raise HxError("没有登记过这个词。用 hx private list 查看。")
        del terms[args.term]
        portable.save_terms(home, terms)
        print("已移除。")
    else:
        if not terms:
            print("还没有登记私有词。用法：hx private add 真实的词 代称")
        for real, alias in sorted(terms.items()):
            print(f"{real}  →  [[{alias}]]")
    return 0


def cmd_audit(args) -> int:
    from . import portable
    project = _project(args)
    core.load_manifest(project)
    findings = portable.audit(project)
    if not findings:
        print("没有发现需要人工确认的内容。这不等于绝对安全：写在句子里的业务信息，规则认不出来。")
        return 0
    by_kind: dict = {}
    for rel, lineno, kind, snippet in findings:
        by_kind.setdefault(kind, []).append((rel, lineno, snippet))
    for kind, items in sorted(by_kind.items()):
        print(f"[{kind}] {len(items)} 处")
        for rel, lineno, snippet in items[: None if args.all else 10]:
            print(f"  {rel}:{lineno}  {snippet}")
        if not args.all and len(items) > 10:
            print(f"  … 另有 {len(items) - 10} 处（hx audit --all 查看全部）")
    print(f"\n共 {len(findings)} 处需要你看一眼。确实敏感的：")
    print("  名称类（客户名、内部域名）→ hx private add 真实的词 代称，然后重新 hx save")
    print("  其余 → 修改或删除对应文件后重新 hx save")
    return 1


def cmd_resource(args) -> int:
    from . import extras
    from .util import MANIFEST, write_json
    project = _project(args)
    manifest = core.load_manifest(project)
    if args.action == "add":
        res = extras.add_resource(manifest, args.name or "", args.path or "", args.sources or [], args.build or "",
                                  args.note or "")
        write_json(core.harness_dir(project) / MANIFEST, manifest)
        print(f"已登记可重建资源 {res['name']}：{res['path']}。hx 不会打包它，载入时会提示用下面的命令重建：")
        print(f"  {res['build']}")
        if extras.is_ignored(project, res["path"]) is False:
            print(f"提醒：{res['path']} 没有被 git 忽略，可能被提交进仓库。建议把它加进 .gitignore。")
    elif args.action == "remove":
        if not extras.remove_resource(manifest, args.name or ""):
            raise HxError("没有这个名字的资源。用 hx resource list 查看。")
        write_json(core.harness_dir(project) / MANIFEST, manifest)
        print("已移除。")
    else:
        items = manifest.get("resources") or []
        if not items:
            print("还没有登记可重建资源。")
            print("用法：hx resource add 名字 --path 产物路径 --sources \"docs/**/*.md\" --build \"重建命令\"")
        status = {"ok": "已就绪", "missing": "还没有生成", "stale": "比源文件旧"}
        for r in items:
            print(f"{r['name']}  {r['path']}  [{status[extras.resource_status(project, r)]}]")
            print(f"    重建：{r.get('build')}")
    return 0


def cmd_claim(args) -> int:
    from . import extras
    project = _project(args)
    core.load_manifest(project)
    if args.action == "add":
        if not args.text:
            raise HxError("用法：hx claim add \"说法\" [--path 路径 | --env 变量名 | --command 命令名]")
        kind = "path" if args.path else "env" if args.env else "command" if args.command else None
        claim = extras.add_claim(project, args.text, kind, args.path or args.env or args.command)
        print(f"已记录（{claim['id']}）。载入时"
              + ("会自动核对。" if kind else "会提醒接手的人核实。"))
    elif args.action == "remove":
        if not extras.remove_claim(project, args.text or ""):
            raise HxError("没有这个编号的声明。用 hx claim list 查看。")
        print("已移除。")
    else:
        claims = extras.load_claims(project)
        if not claims:
            print("还没有记录外部状态声明。")
        marks = {True: "成立", False: "不成立", None: "需人工核实"}
        for c in claims:
            print(f"{c['id']}  [{marks[extras.check_claim(project, core.home_dir(), c)]}]  {c['text']}")
    return 0


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

    s = sub.add_parser("save", help="【常用】一键保存这个项目的 Agent 环境")
    s.add_argument("-m", "--message", help="这次保存的说明（不写会自动生成）")
    s.add_argument("--push", action="store_true", help="保存后推送到远端")
    s.set_defaults(func=cmd_save)

    s = sub.add_parser("load", help="【常用】一键载入：还原配置并检查环境")
    s.add_argument("source", nargs="?", help=".hxb 文件或 git 仓库地址（省略表示当前项目）")
    s.add_argument("-y", "--yes", action="store_true", help="不询问，直接载入")
    s.add_argument("--force", action="store_true", help="覆盖本机已有的不同文件（会先备份）")
    s.add_argument("--dry-run", action="store_true", help="只显示会放置哪些文件")
    s.set_defaults(func=cmd_load)

    s = sub.add_parser("auto", help="查看或开关自动保存")
    s.add_argument("state", nargs="?", choices=("on", "off"))
    s.set_defaults(func=cmd_auto)

    s = sub.add_parser("audit", help="分享前检查：列出包里可能敏感、需要人看一眼的内容")
    s.add_argument("--all", action="store_true", help="列出全部，不省略")
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("private", help="登记私有词（客户名、内部域名等），保存时自动换成代称")
    s.add_argument("action", nargs="?", choices=("add", "list", "remove"), default="list")
    s.add_argument("term", nargs="?", help="真实的词")
    s.add_argument("alias", nargs="?", help="代称")
    s.set_defaults(func=cmd_private)

    s = sub.add_parser("resource", help="登记不随项目迁移、需要在本机重建的数据（向量库、索引等）")
    s.add_argument("action", nargs="?", choices=("add", "list", "remove"), default="list")
    s.add_argument("name", nargs="?")
    s.add_argument("--path", help="产物在项目里的路径")
    s.add_argument("--sources", action="append", help="源文件的通配符，可以写多次，如 docs/**/*.md")
    s.add_argument("--build", help="重建命令")
    s.add_argument("--note", help="补充说明，如所用的向量模型")
    s.set_defaults(func=cmd_resource)

    s = sub.add_parser("claim", help="记录 Agent 的工作所依赖的外部状态，载入时重新核对")
    s.add_argument("action", nargs="?", choices=("add", "list", "remove"), default="list")
    s.add_argument("text", nargs="?", help="说法（add）或编号（remove）")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--path", help="核对方式：这个文件或目录存在")
    g.add_argument("--env", help="核对方式：这个环境变量已设置")
    g.add_argument("--command", help="核对方式：这个命令已安装")
    s.set_defaults(func=cmd_claim)

    s = sub.add_parser("team", help="开关团队一键接入：同事打开项目时自动提示安装 hx")
    s.add_argument("state", nargs="?", choices=("on", "off"))
    s.set_defaults(func=cmd_team)

    for name, fn in (("auto-save", cmd_auto_save), ("auto-load", cmd_auto_load)):
        s = sub.add_parser(name)   # 给钩子用，不在帮助里显示
        s.set_defaults(func=fn)

    s = sub.add_parser("init", help="在项目里初始化 harness")
    s.add_argument("--agent", help="Agent 类型，多个用逗号分隔，如 claude-code,codex（默认自动检测）")
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


def _fix_output_encoding() -> None:
    """Windows 上输出被别的程序接收时（比如 Agent 的钩子），默认编码不是 UTF-8，
    中文和 ✓ 这样的符号会乱码甚至报错。这里统一成 UTF-8，写不出的字符用 ? 代替。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    if sys.version_info < (3, 9):
        sys.stderr.write("hx needs Python 3.9 or newer (found %d.%d). / hx 需要 Python 3.9 或更高版本。\n"
                         % sys.version_info[:2])
        return 2
    _fix_output_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = cmd_home
    try:
        return args.func(args)
    except HxError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
