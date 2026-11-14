"""端到端测试：在临时目录里模拟两台机器。运行：python -m unittest discover -s tests"""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from hx import core
from hx.adapters.claude_code import digest_session, project_slug
from hx.adapters.codex import parse_toml, parse_toml_simple
from hx.redact import Redactor
from hx.util import HxError

FAKE_KEY = "sk-ant-" + "a1b2c3d4e5" * 3


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class Machine:
    """一台模拟的机器：有自己的主目录。"""
    def __init__(self, root: Path, name: str):
        self.home = root / f"home-{name}"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)

    def use(self):
        os.environ["HX_HOME"] = str(self.home)


class Env:
    """测试环境：一个项目，机器 A 上有 Claude Code 的配置和会话。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.root = root
        self.a, self.b = Machine(root, "a"), Machine(root, "b")
        self.project = root / "work" / "demo"
        self.project.mkdir(parents=True)
        sh(self.project, "git", "init", "-q", "-b", "main")
        sh(self.project, "git", "config", "user.email", "t@example.com")
        sh(self.project, "git", "config", "user.name", "t")
        (self.project / "CLAUDE.md").write_text("# rules\n")
        (self.project / ".mcp.json").write_text(json.dumps(
            {"mcpServers": {"gh": {"command": "git", "env": {"GH": "${GITHUB_TOKEN}"}}}}))
        sh(self.project, "git", "add", "-A")
        sh(self.project, "git", "commit", "-qm", "init")

        c = self.a.claude
        (c / "CLAUDE.md").write_text(f"be brief. key {FAKE_KEY}\n")
        (c / "skills/deploy").mkdir(parents=True)
        (c / "skills/deploy/SKILL.md").write_text("deploy\n")
        (c / "skills/synced/x").mkdir(parents=True)
        (c / "skills/synced/x/SKILL.md").write_text("builtin\n")
        (c / "settings.json").write_text(json.dumps(
            {"model": "m-1", "env": {"API_TOKEN": "abcdefgh12345", "DEBUG": "1"}}))
        data = c / "projects" / project_slug(self.project)
        (data / "memory").mkdir(parents=True)
        (data / "memory/MEMORY.md").write_text("prefers pnpm\n")
        lines = [
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "gitBranch": "main", "version": "9.9.9",
             "message": {"role": "user", "content": "add a login page"}},
            {"type": "user", "isMeta": True, "message": {"content": "meta noise"}},
            {"type": "assistant", "timestamp": "2026-01-01T00:01:00Z", "message": {"model": "m-1", "content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(self.project / "login.py")}},
                {"type": "text", "text": "Done."}]}},
            "not json",
        ]
        (data / "s1.jsonl").write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines))
        (self.a.home / ".claude.json").write_text(json.dumps({
            "mcpServers": {"db": {"command": "git", "env": {"DB_PASSWORD": "hunter2hunter2"}}}}))
        self.a.use()

    def tearDown(self):
        os.environ.pop("HX_HOME", None)
        self.tmp.cleanup()

    def all_text(self, d: Path) -> str:
        return "\n".join(p.read_text(errors="replace") for p in d.rglob("*") if p.is_file())


class FlowTest(Env, unittest.TestCase):
    def test_capture_redacts_and_references(self):
        core.init(self.project)
        r = core.capture(self.project)
        h = self.project / ".harness"
        text = self.all_text(h)
        for secret in (FAKE_KEY, "hunter2hunter2", "abcdefgh12345"):
            self.assertNotIn(secret, text)
        self.assertEqual(r["manifest"]["secrets"], ["API_TOKEN", "DB_PASSWORD", "GITHUB_TOKEN"])
        paths = {e["path"]: e for e in r["lock"]["entries"]}
        self.assertEqual(paths["CLAUDE.md"]["scope"], "project")          # 引用，不复制
        self.assertFalse((h / "agent/claude-code/project").exists())
        self.assertIn(".harness/agent/claude-code/user/skills/deploy/SKILL.md", paths)
        self.assertFalse(any("synced" in p for p in paths))               # 内置技能不打包
        settings = json.loads((h / "agent/claude-code/user/settings.json").read_text())
        self.assertEqual(settings["env"], {"DEBUG": "1"})                  # 非密钥保留，密钥删掉
        digest = json.loads((h / "context/sessions/s1.json").read_text())
        self.assertEqual(digest["prompts"], ["add a login page"])
        self.assertEqual(digest["files_changed"], ["login.py"])
        self.assertEqual(r["lock"]["agent"]["version"], "9.9.9")
        self.assertEqual(r["lock"]["model"], "m-1")
        self.assertIn("add a login page", (h / "context/HANDOFF.md").read_text())

    def test_snapshot_history_and_diff(self):
        core.init(self.project)
        r1 = core.snapshot(self.project, "first")
        self.assertTrue(r1["committed"])
        self.assertFalse(core.snapshot(self.project, "again")["committed"])   # 无变化不产生提交
        (self.a.claude / "skills/deploy/SKILL.md").write_text("deploy v2\n")
        (self.a.claude / "agents").mkdir()
        (self.a.claude / "agents/r.md").write_text("reviewer\n")
        r2 = core.snapshot(self.project, "second")
        self.assertNotEqual(r1["lock"]["snapshot"], r2["lock"]["snapshot"])
        rows = core.log(self.project)
        self.assertEqual([r["subject"] for r in rows], ["second", "first"])
        self.assertEqual(rows[0]["snapshot"], r2["lock"]["snapshot"])
        d = core.diff(core.lock_at(self.project, r1["lock"]["snapshot"][:6]),
                      core.lock_at(self.project, r2["lock"]["snapshot"]))
        self.assertEqual(d["categories"]["skills"]["changed"],
                         [".harness/agent/claude-code/user/skills/deploy/SKILL.md"])
        self.assertEqual(len(d["categories"]["agents"]["added"]), 1)
        # 人工维护的文件不会被 capture 覆盖
        intent = self.project / ".harness/context/intent.md"
        intent.write_text("my intent\n")
        core.capture(self.project)
        self.assertEqual(intent.read_text(), "my intent\n")

    def test_migrate_by_git_clone(self):
        core.init(self.project)
        core.snapshot(self.project, "first")
        clone = self.root / "work" / "clone"
        sh(self.root, "git", "clone", "-q", str(self.project), str(clone))
        self.b.use()
        actions = core.materialize(clone)
        self.assertFalse([a for a in actions if a.status == "conflict"])
        self.assertEqual((self.b.claude / "skills/deploy/SKILL.md").read_text(), "deploy\n")
        mem = self.b.claude / "projects" / project_slug(clone) / "memory/MEMORY.md"
        self.assertEqual(mem.read_text(), "prefers pnpm\n")
        mcp = json.loads((clone / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(mcp["db"]["env"], {"DB_PASSWORD": "${DB_PASSWORD}"})
        self.assertIn("gh", mcp)                                           # 原有的保留
        # 再跑一次是幂等的
        self.assertTrue(all(a.status == "same" for a in core.materialize(clone)))
        # 冲突：默认不动，force 才覆盖并备份
        (self.b.claude / "CLAUDE.md").write_text("mine\n")
        self.assertEqual([a.status for a in core.materialize(clone)].count("conflict"), 1)
        self.assertEqual((self.b.claude / "CLAUDE.md").read_text(), "mine\n")
        core.materialize(clone, force=True)
        self.assertEqual((self.b.claude / "CLAUDE.md.hx-bak").read_text(), "mine\n")
        levels = [c.level for c in core.doctor(clone)]
        self.assertIn("fail", levels)                                      # 环境变量没设置

    def test_bundle_roundtrip(self):
        core.init(self.project)
        core.snapshot(self.project, "first")
        bundle = self.root / "demo.hxb"
        r = core.export_bundle(self.project, bundle, include_transcripts=True)
        self.assertEqual(r["transcripts"], 1)
        self.b.use()
        dest = self.root / "work" / "imported"
        res = core.import_bundle(bundle, dest, restore_transcripts=True)
        self.assertEqual(res["transcripts_restored"], 1)
        self.assertEqual((dest / "CLAUDE.md").read_text(), "# rules\n")
        self.assertEqual(core.load_lock(dest)["snapshot"], r["snapshot"])
        self.assertTrue((self.b.claude / "projects" / project_slug(dest) / "s1.jsonl").is_file())

    def test_one_step_save_and_load(self):
        r = core.save(self.project)                      # 没初始化过也能直接保存
        self.assertTrue(r["first"] and r["committed"])
        self.assertFalse(core.save(self.project)["committed"])
        (self.a.claude / "skills/deploy/SKILL.md").write_text("v2\n")
        self.assertTrue(core.save(self.project)["committed"])
        self.assertEqual(core.log(self.project)[0]["subject"], "hx 保存：技能 1 项")
        # 载入：从 git 地址一步到位
        self.b.use()
        into = self.root / "elsewhere"
        into.mkdir()
        dest = core.fetch_source(f"file://{self.project}", into)
        self.assertEqual(dest, into / "demo")
        core.materialize(dest)
        self.assertEqual((self.b.claude / "skills/deploy/SKILL.md").read_text(), "v2\n")

    def test_save_without_git_and_during_merge(self):
        plain = self.root / "plain"
        plain.mkdir()
        r = core.save(plain)
        self.assertEqual((r["committed"], r["reason"]), (False, "no-git"))
        self.assertTrue((plain / ".harness/harness.lock.json").is_file())
        core.save(self.project)
        git_dir = self.project / ".git"
        (git_dir / "MERGE_HEAD").write_text("0" * 40 + "\n")
        (self.a.claude / "skills/deploy/SKILL.md").write_text("v3\n")
        r = core.save(self.project)
        self.assertEqual((r["committed"], r["reason"]), (False, "git-busy"))

    def test_hooks_never_touch_unmanaged_projects(self):
        from hx import cli
        plain = self.root / "plain2"
        plain.mkdir()
        os.environ["CLAUDE_PROJECT_DIR"] = str(plain)
        try:
            self.assertEqual(cli.main(["auto-save"]), 0)
            self.assertEqual(cli.main(["auto-load"]), 0)
        finally:
            del os.environ["CLAUDE_PROJECT_DIR"]
        self.assertEqual(list(plain.iterdir()), [])

    def test_team_onboarding(self):
        core.save(self.project)
        settings = self.project / ".claude/settings.json"
        r = core.team(self.project, True)
        self.assertTrue(r["enabled"] and r["committed"])
        data = json.loads(settings.read_text())
        self.assertTrue(data["enabledPlugins"]["hx@hx"])
        self.assertEqual(data["extraKnownMarketplaces"]["hx"]["source"]["source"], "github")
        self.assertEqual(core.team(self.project, True)["changed"], [])       # 重复开启不产生改动
        # 用户自己的设置要保留；文件有未提交的改动时不替用户提交
        data["permissions"] = {"allow": ["Bash(ls)"]}
        settings.write_text(json.dumps(data))
        r = core.team(self.project, False)
        self.assertFalse(r["enabled"] or r["committed"])
        self.assertEqual(json.loads(settings.read_text()), {"permissions": {"allow": ["Bash(ls)"]}})

    def test_import_rejects_unsafe_paths(self):
        bad = self.root / "bad.hxb"
        with tarfile.open(bad, "w:gz") as tar:
            for name in ("hx-bundle.json", "project/../../evil.txt"):
                info = tarfile.TarInfo(name)
                info.size = 2
                tar.addfile(info, io.BytesIO(b"{}"))
        with self.assertRaises(HxError):
            core.import_bundle(bad, self.root / "work" / "x")
        self.assertFalse((self.root / "evil.txt").exists())

    def test_materialize_rejects_escaping_target(self):
        core.init(self.project)
        core.capture(self.project)
        lock_path = self.project / ".harness/harness.lock.json"
        lock = json.loads(lock_path.read_text())
        next(e for e in lock["entries"] if e.get("target"))["target"] = "user:../../evil"
        lock_path.write_text(json.dumps(lock))
        with self.assertRaises(HxError):
            core.materialize(self.project)


CODEX_CONFIG = """model = "gpt-test"

[mcp_servers.gh]
command = "git"
args = ["-y", "gh-mcp"]

[mcp_servers.gh.env]
GITHUB_TOKEN = "abc123secretvalue"

[mcp_servers.db]
command = "git"
env_vars = ["DB_PASSWORD"]

[mcp_servers.api]
url = "https://x.example/mcp"
bearer_token_env_var = "API_TOKEN"

[projects."/some/local/path"]
trust_level = "trusted"
"""


def codex_rollout(project: Path, sid: str, prompt: str, ended: str) -> str:
    """按 codex-cli 0.169.0 真实会话文件的形状构造。助手回复和工具调用两行的形状未经真实文件核对。"""
    lines = [
        {"timestamp": "2026-02-01T00:00:00Z", "type": "session_meta",
         "payload": {"id": sid, "cwd": str(project), "cli_version": "0.169.0", "git": {"branch": "main"}}},
        {"timestamp": "2026-02-01T00:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md instructions for x\n<INSTRUCTIONS>..."},
                {"type": "input_text", "text": "<environment_context>...</environment_context>"}]}},
        {"timestamp": "2026-02-01T00:00:02Z", "type": "turn_context", "payload": {"model": "gpt-test"}},
        {"timestamp": "2026-02-01T00:00:03Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}},
        {"timestamp": "2026-02-01T00:00:04Z", "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Update File: " + str(project / "pay.py") + "\n@@\n*** End Patch"}},
        {"timestamp": ended, "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Patched."}]}},
    ]
    return "\n".join(json.dumps(x) for x in lines) + "\n"


class CodexTest(Env, unittest.TestCase):
    """Codex adapter、多 Agent、跨 Agent 交换。沿用 FlowTest 搭好的项目和机器 A（Claude Code）。"""

    def codex_machine(self, name: str, project: Path, with_data: bool = True) -> Machine:
        m = Machine(self.root, name)
        shutil.rmtree(m.claude)                      # 这台机器只有 Codex
        cx = m.home / ".codex"
        cx.mkdir()
        if with_data:
            (cx / "config.toml").write_text(CODEX_CONFIG)
            (cx / "AGENTS.md").write_text("be terse\n")
            (cx / "skills/ship").mkdir(parents=True)
            (cx / "skills/ship/SKILL.md").write_text("ship it\n")
            (cx / "skills/.system/x").mkdir(parents=True)
            (cx / "skills/.system/x/SKILL.md").write_text("builtin\n")
            d = cx / "sessions/2026/02/01"
            d.mkdir(parents=True)
            (d / "rollout-2026-02-01T00-00-00-c1.jsonl").write_text(
                codex_rollout(project, "c1", "add payments", "2026-02-01T00:05:00Z"))
            (d / "rollout-2026-02-01T00-00-00-other.jsonl").write_text(
                codex_rollout(self.root / "elsewhere", "other", "unrelated", "2026-02-01T00:06:00Z"))
        return m

    def test_codex_only_project(self):
        m = self.codex_machine("cx", self.project)
        m.use()
        r = core.save(self.project)
        self.assertEqual(r["manifest"]["agents"], ["codex"])
        h = self.project / ".harness"
        self.assertNotIn("abc123secretvalue", self.all_text(h))
        self.assertEqual(r["manifest"]["secrets"], ["API_TOKEN", "DB_PASSWORD", "GITHUB_TOKEN"])
        self.assertFalse((h / "agent/codex/user/skills/.system").exists())
        self.assertNotIn("/some/local/path", self.all_text(h / "agent/codex"))   # 本机路径不进包
        digest = json.loads((h / "context/sessions/c1.json").read_text())
        self.assertEqual((digest["agent"], digest["prompts"], digest["files_changed"], digest["models"]),
                         ("codex", ["add payments"], ["pay.py"], ["gpt-test"]))
        self.assertEqual(digest["last_reply"], "Patched.")
        self.assertFalse((h / "context/sessions/other.json").exists())           # 别的项目的会话不收
        self.assertEqual(r["lock"]["agents"]["codex"], {"version": "0.169.0", "model": "gpt-test"})
        # 另一台只有 Codex 的机器
        clone = self.root / "work" / "cx-clone"
        sh(self.root, "git", "clone", "-q", str(self.project), str(clone))
        m2 = self.codex_machine("cx2", clone, with_data=False)
        m2.use()
        core.materialize(clone)
        cfg = parse_toml((m2.home / ".codex/config.toml").read_text())["mcp_servers"]
        self.assertEqual(cfg["gh"], {"command": "git", "args": ["-y", "gh-mcp"], "env_vars": ["GITHUB_TOKEN"]})
        self.assertEqual(cfg["api"], {"url": "https://x.example/mcp", "bearer_token_env_var": "API_TOKEN"})
        self.assertEqual((m2.home / ".codex/skills/ship/SKILL.md").read_text(), "ship it\n")
        self.assertTrue(all(a.status == "same" for a in core.materialize(clone)))   # 幂等

    def test_claude_project_loaded_on_codex_machine(self):
        core.save(self.project)                      # 机器 A：Claude Code
        clone = self.root / "work" / "to-codex"
        sh(self.root, "git", "clone", "-q", str(self.project), str(clone))
        m = self.codex_machine("cx", clone, with_data=False)
        m.use()
        plan = core.materialize(clone, dry_run=True)
        self.assertFalse((clone / "AGENTS.md").exists())                          # 预演不写文件
        self.assertTrue(any(a.status == "written" for a in plan))
        core.materialize(clone)
        self.assertIn("CLAUDE.md", (clone / "AGENTS.md").read_text())             # 指路，不复制内容
        self.assertEqual((m.home / ".codex/skills/deploy/SKILL.md").read_text(), "deploy\n")
        cfg = parse_toml((m.home / ".codex/config.toml").read_text())["mcp_servers"]
        self.assertEqual(cfg["db"], {"command": "git", "env_vars": ["DB_PASSWORD"]})
        self.assertIn("gh", cfg)                                                  # 项目 .mcp.json 里的也转过去
        self.assertFalse((m.home / ".claude").exists())                           # 不给没装的 Agent 写东西

    def test_codex_project_loaded_on_claude_machine(self):
        m = self.codex_machine("cx", self.project)
        (self.project / "CLAUDE.md").unlink()
        (self.project / ".mcp.json").unlink()
        (self.project / "AGENTS.md").write_text("# rules\n")
        sh(self.project, "git", "add", "-A")
        sh(self.project, "git", "commit", "-qm", "codex style")
        m.use()
        core.save(self.project)
        clone = self.root / "work" / "to-claude"
        sh(self.root, "git", "clone", "-q", str(self.project), str(clone))
        self.b.use()                                                              # 机器 B：只有 Claude Code
        core.materialize(clone)
        self.assertEqual((clone / "CLAUDE.md").read_text(), "@AGENTS.md\n@.harness/context/HANDOFF.md\n")
        self.assertEqual((self.b.claude / "skills/ship/SKILL.md").read_text(), "ship it\n")
        mcp = json.loads((clone / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(mcp["api"]["headers"], {"Authorization": "Bearer ${API_TOKEN}"})

    def test_team_with_two_agents_keeps_everyones_work(self):
        core.save(self.project)                      # A 用 Claude Code 保存
        clone = self.root / "work" / "mixed"
        sh(self.root, "git", "clone", "-q", str(self.project), str(clone))
        sh(clone, "git", "config", "user.email", "t@example.com")
        sh(clone, "git", "config", "user.name", "t")
        m = self.codex_machine("cx", clone)          # B 用 Codex 接着做
        m.use()
        r = core.save(clone)
        self.assertEqual(r["manifest"]["agents"], ["claude-code", "codex"])       # 自动登记新的 Agent
        self.assertEqual(r["captured"], ["codex"])
        h = clone / ".harness"
        # A 留下的东西都还在
        self.assertTrue((h / "agent/claude-code/user/skills/deploy/SKILL.md").is_file())
        self.assertTrue((h / "context/memory/MEMORY.md").is_file())
        self.assertEqual(sorted(p.name for p in (h / "context/sessions").iterdir()), ["c1.json", "s1.json"])
        self.assertEqual(set(r["lock"]["agents"]), {"claude-code", "codex"})
        self.assertIn("DB_PASSWORD", r["manifest"]["secrets"])
        handoff = (h / "context/HANDOFF.md").read_text()
        self.assertIn("add a login page", handoff)
        self.assertIn("add payments", handoff)
        self.assertEqual(core.doctor(clone)[1].level, "ok")                        # 文件与锁一致

    def test_simple_toml_parser_matches_stdlib(self):
        want = {"gh": {"command": "git", "args": ["-y", "gh-mcp"], "env": {"GITHUB_TOKEN": "abc123secretvalue"}},
                "db": {"command": "git", "env_vars": ["DB_PASSWORD"]},
                "api": {"url": "https://x.example/mcp", "bearer_token_env_var": "API_TOKEN"}}
        self.assertEqual(parse_toml_simple(CODEX_CONFIG)["mcp_servers"], want)
        self.assertEqual(parse_toml_simple(CODEX_CONFIG)["model"], "gpt-test")
        self.assertEqual(parse_toml_simple('[mcp_servers.a]\nenv = { K = "v", J = "w" }\n')["mcp_servers"]["a"]["env"],
                         {"K": "v", "J": "w"})

    def test_old_sessions_are_pruned(self):
        core.init(self.project)
        d = self.project / ".harness/context/sessions"
        d.mkdir(parents=True)
        for i in range(40):
            (d / f"old{i:02d}.json").write_text(json.dumps({"id": f"old{i:02d}", "ended": f"2025-01-{i % 28 + 1:02d}T00:00:00Z"}))
        core.capture(self.project)
        names = {p.name for p in d.iterdir()}
        self.assertEqual(len(names), 30)
        self.assertIn("s1.json", names)                                           # 最新的保留


class RedactTest(unittest.TestCase):
    def test_rules(self):
        r = Redactor()
        cases = [FAKE_KEY, "ghp_" + "A1" * 20, "AKIA" + "ABCDEFGH12345678",
                 "https://user:pa55word@host/x", 'password = "s3cr3tvalue"', "API_KEY=abcd1234efgh"]
        for c in cases:
            out = r.text(c)
            self.assertIn("[REDACTED", out, c)
        self.assertEqual(r.text('token: "${GITHUB_TOKEN}"'), 'token: "${GITHUB_TOKEN}"')
        self.assertEqual(r.text("normal text, nothing here"), "normal text, nothing here")


if __name__ == "__main__":
    unittest.main()
