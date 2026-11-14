"""从真实 Codex 记录得出的回归测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from hx.adapters.codex import digest_rollout
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

