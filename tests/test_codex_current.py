"""从真实 Codex 记录得出的回归测试。"""
import json
import tempfile
import unittest
import os
import io
import argparse
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from hx.adapters.codex import CodexAdapter, digest_rollout, parse_toml, parse_toml_simple
from hx.adapters.base import CaptureContext
from hx.redact import Redactor
from hx.util import HxError, write_text
from test_hx import Env, sh, codex_rollout
from hx import core


class RolloutTest(unittest.TestCase):
    def test_sanitized_real_rollout(self):
        fixture = Path(__file__).parent / "fixtures/codex-real-rollout.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            path = project / "rollout.jsonl"
            write_text(path, fixture.read_text(encoding="utf-8").replace("C:/hx-fixture", project.as_posix()))
            digest = digest_rollout(path, project)
        self.assertEqual(digest["id"], "hx-real-fixture")
        self.assertEqual(digest["git_branch"], "hx-format-check")
        self.assertEqual(digest["agent_version"], "0.167.0-alpha.4.1")
        self.assertEqual(digest["models"], ["gpt-6.1-sol"])
        self.assertEqual(len(digest["prompts"]), 1)
        self.assertTrue(digest["prompts"][0].startswith("In this temporary fixture project"))
        self.assertEqual(digest["files_changed"], ["greeting.txt", "notes.md"])
        self.assertEqual(digest["tool_counts"], {"exec": 3})
        self.assertEqual(digest["last_reply"], "HX fixture done.")
        self.assertLess(digest["started"], digest["ended"])

    def test_user_markup_and_provenance(self):
        rows = []
        for text, provenance in (("<p>请修改这里</p>", "user"), ("injected context", "tool")):
            rows.append({"type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
                "internal_chat_message_metadata_passthrough": {
                    "content_item_metadata": [{"provenance": {"type": provenance}}]}}})
        # 老文件也允许用户输入 HTML；已知系统注入仍被排除。
        for text in ("<h1>旧记录</h1>", "<environment_context>noise</environment_context>"):
            rows.append({"type": "response_item", "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": text}]}})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            write_text(path, "\n".join(json.dumps(d) for d in rows))
            self.assertEqual(digest_rollout(path, Path(tmp))["prompts"],
                             ["<p>请修改这里</p>", "<h1>旧记录</h1>"])


class CodexConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.home = Path(self.tmp.name) / "home"
        self.project.mkdir()
        (self.home / ".codex").mkdir(parents=True)
        self.env = patch.dict(os.environ, {"HX_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.adapter = CodexAdapter()

    def capture(self):
        ctx = CaptureContext(self.project, self.home, Redactor(), agent="codex")
        self.adapter.capture(ctx)
        return ctx

    def test_project_mcp_and_short_secrets(self):
        write_text(self.home / ".codex/config.toml", '''model = "global-model"
[mcp_servers.shared]
command = "old-command"
args = ["kept"]
[mcp_servers.shared.env]
OLD_TOKEN = "s1"
''')
        write_text(self.project / ".codex/config.toml", '''model = "project-model"
[mcp_servers.shared]
command = "project-command"
[mcp_servers.shared.env]
NEW_TOKEN = "s2"
[mcp_servers.api]
url = "https://example.invalid/mcp"
[mcp_servers.api.http_headers]
Authorization = "tiny"
X-Secret = "low"
''')
        ctx = self.capture()
        packed = self.project / ".harness/agent/codex/mcp.json"
        text = packed.read_text(encoding="utf-8")
        for value in ('"s1"', '"s2"', '"tiny"', '"low"'):
            self.assertNotIn(value, text)
        self.assertEqual(ctx.secrets, {"OLD_TOKEN", "NEW_TOKEN", "MCP_API_AUTHORIZATION", "MCP_API_X_SECRET"})
        data = json.loads(text)["mcpServers"]
        self.assertEqual(data["shared"]["command"], "project-command")
        self.assertEqual(data["shared"]["args"], ["kept"])
        self.assertEqual(data["shared"]["x-hx-scope"], "project")
        self.assertEqual(ctx.info["model"], "project-model")
        self.assertIn("shared", self.adapter.export_neutral(self.project).mcp)
        # 项目层保持项目范围，不污染用户配置；冲突不覆盖。
        original = (self.project / ".codex/config.toml").read_bytes()
        actions = self.adapter.materialize(self.project, self.home, {}, False)
        self.assertTrue(any(a.status == "conflict" for a in actions))
        self.assertEqual((self.project / ".codex/config.toml").read_bytes(), original)

    def test_current_skills_override_and_legacy_prompts(self):
        write_text(self.home / ".codex/AGENTS.override.md", "override guidance\n")
        write_text(self.home / ".codex/prompts/old.md", "old custom prompt\n")
        write_text(self.home / ".agents/skills/current/SKILL.md", "current skill\n")
        write_text(self.project / ".agents/skills/repo/SKILL.md", "repo skill\n")
        write_text(self.project / "AGENTS.md", "base\n")
        write_text(self.project / "AGENTS.override.md", "override\n")
        ctx = self.capture()
        self.assertIn("user:AGENTS.override.md", [e.get("target") for e in ctx.entries])
        self.assertIn("user:prompts/old.md", [e.get("target") for e in ctx.entries])
        neutral = self.adapter.export_neutral(self.project)
        self.assertIn("current/SKILL.md", neutral.skills)
        self.assertIn("repo/SKILL.md", neutral.skills)
        self.assertEqual(neutral.instructions, ["AGENTS.override.md"])
        other = Path(self.tmp.name) / "other"
        self.adapter.materialize(self.project, other, {}, False, dry_run=True)
        self.assertFalse(other.exists())
        self.adapter.materialize(self.project, other, {}, False)
        self.assertEqual((other / ".agents/skills/current/SKILL.md").read_text(encoding="utf-8"), "current skill\n")
        write_text(other / ".agents/skills/current/SKILL.md", "local skill\n")
        self.assertTrue(any(a.status == "conflict" for a in self.adapter.materialize(self.project, other, {}, False)))
        # 用户级生成记忆含其他项目：不得顺手打包。
        write_text(self.home / ".codex/memories_v2/global.md", "unrelated private project\n")
        self.assertFalse(any(e["category"] == "memory" for e in self.capture().entries))

    def test_plugin_inventory_capture_and_doctor(self):
        plugins = [{"pluginId": "helper@test-market", "name": "helper", "marketplaceName": "test-market",
                    "version": "1.2.3", "enabled": True, "installed": True,
                    "source": {"source": "local", "path": "./plugins/helper"},
                    "marketplaceSource": {"sourceType": "git", "source": "https://example.invalid/plugins.git"},
                    "extraCredential": "must-not-copy"}]
        with patch.object(self.adapter, "_plugin_inventory", return_value=plugins):
            self.capture()
        packed = (self.project / ".harness/agent/codex/plugins.json").read_text(encoding="utf-8")
        self.assertNotIn("must-not-copy", packed)
        self.assertIn('"version": "1.2.3"', packed)
        with patch.object(self.adapter, "_plugin_inventory", return_value=[]):
            self.assertTrue(any("缺少 Codex 插件" in c.title for c in self.adapter.doctor(self.project, self.home, {}, {})))
        with patch.object(self.adapter, "_plugin_inventory", return_value=None):
            self.assertTrue(any("无法读取" in c.title for c in self.adapter.doctor(self.project, self.home, {}, {})))

    def test_simple_parser_project_overrides(self):
        text = '''[plugins."helper@test-market"]
enabled = true
[mcp_servers."server.with.dot"]
command = "python"
env_http_headers = { Authorization = "TOKEN_NAME" }
'''
        self.assertEqual(parse_toml_simple(text), parse_toml(text))

    def test_team_preserves_other_settings(self):
        path = self.project / ".codex/config.toml"
        original = '# keep this comment\nmodel = "local"\n[mcp_servers.mine]\ncommand = "python"\n'
        write_text(path, original)
        self.assertEqual(self.adapter.team_setup(self.project, True), [".codex/config.toml"])
        self.assertTrue(self.adapter.team_enabled(self.project))
        self.assertTrue(path.read_text(encoding="utf-8").startswith(original))
        self.assertEqual(self.adapter.team_setup(self.project, True), [])
        self.adapter.team_setup(self.project, False)
        self.assertFalse(self.adapter.team_enabled(self.project))
        self.assertEqual(parse_toml(path.read_text(encoding="utf-8"))["mcp_servers"]["mine"], {"command": "python"})
        self.assertEqual(self.adapter.team_setup(self.project, False), [])
        write_text(path, '[marketplaces.hx]\nsource_type = "git"\nsource = "https://other.invalid/market.git"\n')
        with self.assertRaises(HxError):
            self.adapter.team_setup(self.project, True)


class HookInputTest(unittest.TestCase):
    def test_codex_stdin_claude_environment_and_subdirectory(self):
        from hx.cli import _hook_project
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            write_text(project / ".harness/harness.json", "{}")
            child = project / "sub"
            child.mkdir()
            args = argparse.Namespace(project=str(project / "wrong"))
            with patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(child)}))), \
                    patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(project / "other")}):
                self.assertEqual(_hook_project(args), project)
            with patch("sys.stdin", io.StringIO("invalid input")), \
                    patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(child)}):
                self.assertEqual(_hook_project(args), project)


class CodexHookFlowTest(Env, unittest.TestCase):
    def test_declared_hooks_keep_start_read_only_and_save_only_harness(self):
        core.save(self.project)
        old_claude = (self.project / ".harness/agent/claude-code/user/CLAUDE.md").read_bytes()
        self.b.claude.rmdir()  # 原本为空：模拟只有 Codex 的机器
        cx = self.b.home / ".codex"
        cx.mkdir()
        self.b.use()
        repo = Path(__file__).resolve().parents[1]
        plugin = self.root / "plugin with spaces"
        shutil.copytree(repo / "src", plugin / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        shutil.copytree(repo / "bin", plugin / "bin")
        config = json.loads((repo / "hooks/codex.json").read_text(encoding="utf-8"))["hooks"]
        env = dict(os.environ, PLUGIN_ROOT=str(plugin), PYTHONUTF8="1")
        env.pop("CLAUDE_PROJECT_DIR", None)
        def invoke(event):
            handler = config[event][0]["hooks"][0]
            if os.name == "nt":
                shell = shutil.which("pwsh") or shutil.which("powershell")
                args = [shell, "-NoProfile", "-NonInteractive", "-Command", handler["commandWindows"]]
            else:
                args = ["sh", "-c", handler["command"]]
            return subprocess.run(args, cwd=self.root, env=env, input=json.dumps({"cwd": str(self.project)}),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        before = {p.relative_to(self.b.home).as_posix(): p.read_bytes() for p in self.b.home.rglob("*") if p.is_file()}
        result = invoke("SessionStart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HANDOFF.md", result.stdout)
        after = {p.relative_to(self.b.home).as_posix(): p.read_bytes() for p in self.b.home.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        write_text(cx / "AGENTS.md", "new codex guidance\n")
        write_text(cx / "sessions/2026/11/14/rollout-hook.jsonl",
                   codex_rollout(self.project, "hook", "hook verification", "2026-11-14T00:00:00Z"))
        write_text(self.project / "code.txt", "staged application work\n")
        sh(self.project, "git", "add", "code.txt")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.project)
        result = invoke("SessionEnd")
        self.assertEqual(result.returncode, 0, result.stderr)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.project) != head:
                break
            time.sleep(0.1)
        else:
            logs = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (self.b.home / ".hx/logs").glob("*"))
            self.fail("后台未完成提交：" + result.stdout + result.stderr + logs)
        paths = subprocess.check_output(["git", "show", "--pretty=", "--name-only", "HEAD"], cwd=self.project).decode().splitlines()
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith(".harness/") for p in paths), paths)
        staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=self.project).decode()
        self.assertIn("code.txt", staged)
        self.assertEqual((self.project / ".harness/agent/claude-code/user/CLAUDE.md").read_bytes(), old_claude)

