"""从真实 Codex 记录得出的回归测试。"""
import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from hx.adapters.codex import CodexAdapter, digest_rollout, parse_toml, parse_toml_simple
from hx.adapters.base import CaptureContext
from hx.redact import Redactor
from hx.util import write_text


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

